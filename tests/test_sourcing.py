"""Tests for the supplier reverse lookup (veta.sourcing)."""

from __future__ import annotations

import pandas as pd

from veta import sourcing


def _contracts():
    return pd.DataFrame([
        {"partida": "51501", "rfc": "BIG", "proveedor": "BIG CO", "siglas": "IMSS", "moneda": "MXN", "importe": 900.0},
        {"partida": "51501", "rfc": "BIG", "proveedor": "BIG CO", "siglas": "ISSSTE", "moneda": "MXN", "importe": 100.0},
        {"partida": "51501", "rfc": "SMALL", "proveedor": "SMALL CO", "siglas": "IMSS", "moneda": "MXN", "importe": 50.0},
        {"partida": "51501", "rfc": "USDCO", "proveedor": "USD CO", "siglas": "IMSS", "moneda": "USD", "importe": 5000.0},
        {"partida": "99999", "rfc": "OTHER", "proveedor": "OTHER CO", "siglas": "IMSS", "moneda": "MXN", "importe": 700.0},
    ])


def test_suppliers_sorted_by_total_value():
    suppliers = sourcing.suppliers_for_partida("51501", contracts=_contracts())
    # USD row excluded (non-MXN); BIG ranks above SMALL by total value.
    assert [s.proveedor for s in suppliers] == ["BIG CO", "SMALL CO"]
    assert suppliers[0].total_value == 1000.0
    assert suppliers[0].contract_count == 2
    assert suppliers[0].buyers_served == 2


def test_suppliers_top_buyers():
    suppliers = sourcing.suppliers_for_partida("51501", contracts=_contracts())
    big = suppliers[0]
    assert set(big.top_buyers) == {"IMSS", "ISSSTE"}


def test_suppliers_empty_for_unknown_partida():
    assert sourcing.suppliers_for_partida("00000", contracts=_contracts()) == []


def test_suppliers_limit():
    suppliers = sourcing.suppliers_for_partida("51501", limit=1, contracts=_contracts())
    assert len(suppliers) == 1
