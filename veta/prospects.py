"""Potential-client (prospect) list builder.

The product is a daily tender-intelligence email. The ideal subscriber is a
company that already competes for federal contracts in the distributor's target
categories: they live and die by tenders, so a daily shortlist has direct value.

The historical contracts cache (veta.history) *is* the prospect universe. Every
supplier that has won a federal contract in a target partida is a business that
bids on government tenders. This module aggregates those suppliers into a ranked,
qualified client list.

Targeting profile (all tunable via build_prospects args):
  - Active: won a contract in the last two source years (still bidding).
  - MIPYME: MICRO / PEQUEÑA / MEDIANA. Big enough to have budget, small enough
    to lack a dedicated bid-intelligence team. GRANDE / NO MIPYME have their own
    teams and are weaker prospects (kept optional via mipyme_only=False).
  - Competitive: participates in licitaciones (the tender flow the tool watches),
    not only adjudicación directa.
  - Engaged: more than a one-off winner (min_contracts).

Note on contacts: ComprasMX does not publish supplier contact people or emails
(confirmed during recon). This list identifies the *company* (RFC + name +
profile). Emails are enriched separately before the daily send.

All supplier names/RFCs are Spanish source data, left as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from veta import filters, history

CURRENCY_MXN = "MXN"

MIPYME_TIERS = ("MICRO", "PEQUEÑA", "MEDIANA")

# clave -> human label, for the categories column.
_PARTIDA_LABEL = {clave: desc for _pid, clave, desc in filters.INCLUDE_PARTIDAS}


@dataclass
class Prospect:
    rfc: str
    proveedor: str
    estratificacion: str
    total_contracts: int
    licitacion_contracts: int
    total_value: float
    distinct_partidas: int
    partidas: list[str] = field(default_factory=list)
    distinct_buyers: int = 0
    top_buyers: list[str] = field(default_factory=list)
    last_year: int = 0
    years_active: int = 0
    score: float = 0.0


def _score(row: pd.Series) -> float:
    """Fit score for "likely to buy a daily tender-intelligence email".

    Weighted toward breadth (a multi-category distributor gets more value from a
    cross-category feed) and recency, with competitive participation and buyer
    reach as supporting signals. Weights are intentionally simple and legible.
    """
    recency = 10.0 if row["last_year"] >= 2025 else 0.0
    return (
        row["distinct_partidas"] * 5.0
        + row["licitacion_contracts"] * 2.0
        + row["distinct_buyers"] * 1.0
        + recency
    )


def build_prospects(
    contracts: pd.DataFrame | None = None,
    *,
    mipyme_only: bool = True,
    min_last_year: int = 2024,
    min_contracts: int = 2,
    require_licitacion: bool = True,
    limit: int | None = None,
) -> list[Prospect]:
    """Build a ranked list of potential clients from the contracts cache.

    Args:
        contracts: optional preloaded contracts frame (defaults to the cache).
        mipyme_only: keep only MICRO/PEQUEÑA/MEDIANA suppliers.
        min_last_year: drop suppliers whose most recent win predates this year.
        min_contracts: drop suppliers with fewer target-category contracts.
        require_licitacion: drop suppliers with no competitive (licitación) wins.
        limit: optional cap on the number of prospects returned.
    """
    if contracts is None:
        contracts = history.load_contracts_cache()

    targets = {clave for _pid, clave, _desc in filters.INCLUDE_PARTIDAS}
    subset = contracts[
        contracts["partida"].isin(targets)
        & (contracts["moneda"] == CURRENCY_MXN)
        & (contracts["importe"] > 0)
    ]
    if subset.empty:
        return []

    grouped = (
        subset.groupby("rfc")
        .agg(
            total_contracts=("importe", "size"),
            licitacion_contracts=("is_licitacion", "sum"),
            total_value=("importe", "sum"),
            distinct_partidas=("partida", "nunique"),
            distinct_buyers=("siglas", "nunique"),
            last_year=("source_year", "max"),
            first_year=("source_year", "min"),
        )
        .reset_index()
    )
    grouped["years_active"] = grouped["last_year"] - grouped["first_year"] + 1

    grouped = grouped[grouped["last_year"] >= min_last_year]
    grouped = grouped[grouped["total_contracts"] >= min_contracts]
    if require_licitacion:
        grouped = grouped[grouped["licitacion_contracts"] > 0]
    if grouped.empty:
        return []

    grouped["score"] = grouped.apply(_score, axis=1)
    grouped = grouped.sort_values(
        ["score", "total_value"], ascending=False
    )

    prospects: list[Prospect] = []
    for row in grouped.itertuples(index=False):
        sup_rows = subset[subset["rfc"] == row.rfc]

        # Most common company size and name (source data varies row to row).
        estr = _mode(sup_rows["estratificacion"])
        if mipyme_only and estr not in MIPYME_TIERS:
            continue
        proveedor = _mode(sup_rows["proveedor"])

        claves = sup_rows["partida"].value_counts().index.tolist()
        partidas = [f"{c} {_PARTIDA_LABEL.get(c, '')}".strip() for c in claves]
        buyers = sup_rows["siglas"].value_counts().head(3).index.tolist()

        prospects.append(
            Prospect(
                rfc=str(row.rfc),
                proveedor=str(proveedor),
                estratificacion=str(estr),
                total_contracts=int(row.total_contracts),
                licitacion_contracts=int(row.licitacion_contracts),
                total_value=float(row.total_value),
                distinct_partidas=int(row.distinct_partidas),
                partidas=partidas,
                distinct_buyers=int(row.distinct_buyers),
                top_buyers=buyers,
                last_year=int(row.last_year),
                years_active=int(row.years_active),
                score=float(row.score),
            )
        )
        if limit is not None and len(prospects) >= limit:
            break

    return prospects


def qualify_prospects(
    prospects: list[Prospect],
    *,
    min_categories: int = 5,
    min_buyers: int = 5,
    min_licitacion_ratio: float = 0.70,
    min_contracts: int = 10,
    max_contracts: int = 500,
    sizes: tuple[str, ...] = ("PEQUEÑA", "MEDIANA"),
    require_latest_year: bool = True,
) -> list[Prospect]:
    """Narrow a prospect list to the outreach-ready "sweet spot".

    Applies the six qualification criteria (all must pass), keeping only the
    companies most likely to buy a paid tender-intelligence subscription:

      - Multi-category (min_categories): gets real value from a cross-category
        feed; single-category suppliers can just check ComprasMX by hand.
      - Actively bidding now (require_latest_year): last win is in the most
        recent year present in the data (computed, never hardcoded).
      - Right size (sizes): has budget but no in-house intelligence team.
      - Broad buyer reach (min_buyers): values buyer-specific intelligence.
      - Competitive (min_licitacion_ratio): wins through licitaciones, not
        mostly adjudicacion directa; the latter does not need the tool.
      - Engaged but not industrial (min/max_contracts): participates enough to
        care, not so much that they likely have internal systems.

    Input is assumed to be score-sorted (build_prospects output); order is
    preserved. Returns [] on empty input.
    """
    if not prospects:
        return []

    latest = max(p.last_year for p in prospects)

    def _passes(p: Prospect) -> bool:
        ratio = (
            p.licitacion_contracts / p.total_contracts
            if p.total_contracts
            else 0.0
        )
        return (
            p.distinct_partidas >= min_categories
            and p.distinct_buyers >= min_buyers
            and ratio >= min_licitacion_ratio
            and min_contracts <= p.total_contracts <= max_contracts
            and p.estratificacion in sizes
            and (not require_latest_year or p.last_year == latest)
        )

    return [p for p in prospects if _passes(p)]


def _mode(series: pd.Series) -> str:
    """Most frequent non-null value in a series, or empty string."""
    clean = series.dropna()
    if clean.empty:
        return ""
    counts = clean.value_counts()
    return str(counts.index[0])
