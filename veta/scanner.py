"""Adjacent opportunity scanner (spec section 3.6, step 6).

Ranks partidas the distributor is NOT currently targeting by attractiveness,
using the historical federal LAASSP data (2023-2025). For each partida it
computes total contract volume, distinct buyers, distinct suppliers, average
new-entrant rate across buyers, and how many of the distributor's existing
buyers also buy this partida. This drives the "which categories should I add?"
conversation.

Partida descriptions come from the live clave catalog (cached under catalogs/).
The scan itself runs offline against the cached historical data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from veta import api, filters, history

CLAVE_CATALOG_PATH = Path("catalogs/clave.json")
CURRENCY_MXN = "MXN"


def load_clave_descriptions(
    client: api.ComprasMXClient | None = None,
    refresh: bool = False,
) -> dict[str, str]:
    """Return {clave: descripcion}, fetching and caching the clave catalog."""
    if CLAVE_CATALOG_PATH.exists() and not refresh:
        entries = json.loads(CLAVE_CATALOG_PATH.read_text(encoding="utf-8"))
    else:
        owns_client = client is None
        client = client or api.ComprasMXClient()
        try:
            entries = client.fetch_catalog("clave", action="GET_CAT_CLAVES", ley_id=1)
        finally:
            if owns_client:
                client.close()
        CLAVE_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLAVE_CATALOG_PATH.write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8"
        )
    return {str(e["clave"]): e.get("descripcion", "") for e in entries}


def scan(
    descriptions: dict[str, str] | None = None,
    contracts: pd.DataFrame | None = None,
    lookup: pd.DataFrame | None = None,
    targeted_claves: set[str] | None = None,
    excluded_claves: set[str] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Rank partidas by attractiveness, flagging targeted vs adjacent ones.

    Returns a DataFrame sorted by total contract value descending. Pure and
    offline when contracts, lookup, and descriptions are supplied.
    """
    if contracts is None:
        contracts = history.load_contracts_cache()
    if lookup is None:
        lookup = history.load_lookup()
    if targeted_claves is None:
        targeted_claves = {clave for _pid, clave, _desc in filters.INCLUDE_PARTIDAS}
    if excluded_claves is None:
        excluded_claves = {clave for _pid, clave, _desc in filters.EXCLUDE_PARTIDAS}
    descriptions = descriptions or {}

    mxn = contracts[(contracts["moneda"] == CURRENCY_MXN) & (contracts["importe"] > 0)]

    # The distributor's existing buyers: those active in targeted partidas.
    existing_buyers = set(
        contracts[contracts["partida"].isin(targeted_claves)]["siglas"].unique()
    )

    per_partida = (
        mxn.groupby("partida")
        .agg(
            total_value=("importe", "sum"),
            contract_count=("importe", "size"),
            distinct_buyers=("siglas", "nunique"),
            distinct_suppliers=("rfc", "nunique"),
        )
        .reset_index()
    )

    # Average new-entrant rate across buyers for each partida.
    avg_ne = (
        lookup.groupby("partida")["new_entrant_rate"].mean().reset_index(
            name="avg_new_entrant_rate"
        )
    )
    per_partida = per_partida.merge(avg_ne, on="partida", how="left")

    # Overlap with the distributor's existing buyers.
    overlap = (
        mxn[mxn["siglas"].isin(existing_buyers)]
        .groupby("partida")["siglas"]
        .nunique()
        .reset_index(name="existing_buyer_overlap")
    )
    per_partida = per_partida.merge(overlap, on="partida", how="left")
    per_partida["existing_buyer_overlap"] = (
        per_partida["existing_buyer_overlap"].fillna(0).astype(int)
    )

    per_partida["is_targeted"] = per_partida["partida"].isin(targeted_claves)
    per_partida["is_excluded"] = per_partida["partida"].isin(excluded_claves)
    per_partida["descripcion"] = per_partida["partida"].map(descriptions).fillna("")
    per_partida["avg_new_entrant_rate"] = per_partida["avg_new_entrant_rate"].round(3)

    per_partida = per_partida.sort_values("total_value", ascending=False)
    if limit is not None:
        per_partida = per_partida.head(limit)
    return per_partida.reset_index(drop=True)


def adjacent_opportunities(
    descriptions: dict[str, str] | None = None,
    limit: int | None = 20,
    **scan_kwargs,
) -> pd.DataFrame:
    """Scan and return only the partidas the distributor is NOT targeting.

    Excludes captured categories (EXCLUDE_PARTIDAS, for example pharma), which
    the distributor deliberately avoids and are not opportunities.
    """
    ranked = scan(descriptions=descriptions, **scan_kwargs)
    adjacent = ranked[~ranked["is_targeted"] & ~ranked["is_excluded"]]
    if limit is not None:
        adjacent = adjacent.head(limit)
    return adjacent.reset_index(drop=True)
