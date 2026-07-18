"""Supplier sourcing reverse lookup (spec section 3.4, step 4).

Given a partida clave, return the historical suppliers that have won that
category from the federal government, so an intermediary can find potential
sourcing partners for a tender in a category they do not currently stock.

Reads the normalized contracts cache produced by veta.history (federal LAASSP,
2023-2025). All supplier names and RFCs are Spanish source data, left as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from veta import history

CURRENCY_MXN = "MXN"


@dataclass
class Supplier:
    proveedor: str
    rfc: str
    contract_count: int
    total_value: float
    buyers_served: int
    top_buyers: list[str] = field(default_factory=list)


def suppliers_for_partida(
    clave: str,
    limit: int | None = None,
    contracts: pd.DataFrame | None = None,
) -> list[Supplier]:
    """Return suppliers for a partida clave, sorted by total MXN value desc.

    Args:
        clave: the 5-digit partida especifica code (for example "51501").
        limit: optional cap on the number of suppliers returned.
        contracts: optional preloaded contracts frame (defaults to the cache).
    """
    if contracts is None:
        contracts = history.load_contracts_cache()

    subset = contracts[
        (contracts["partida"] == str(clave))
        & (contracts["moneda"] == CURRENCY_MXN)
        & (contracts["importe"] > 0)
    ]
    if subset.empty:
        return []

    grouped = (
        subset.groupby(["rfc", "proveedor"])
        .agg(
            contract_count=("importe", "size"),
            total_value=("importe", "sum"),
            buyers_served=("siglas", "nunique"),
        )
        .reset_index()
        .sort_values("total_value", ascending=False)
    )

    if limit is not None:
        grouped = grouped.head(limit)

    # Top buyers per supplier (by contract count) for context.
    suppliers: list[Supplier] = []
    for row in grouped.itertuples(index=False):
        buyers = (
            subset[subset["rfc"] == row.rfc]["siglas"]
            .value_counts()
            .head(3)
            .index.tolist()
        )
        suppliers.append(
            Supplier(
                proveedor=row.proveedor,
                rfc=row.rfc,
                contract_count=int(row.contract_count),
                total_value=float(row.total_value),
                buyers_served=int(row.buyers_served),
                top_buyers=buyers,
            )
        )
    return suppliers
