"""Tests for historical normalization and aggregation (veta.history)."""

from __future__ import annotations

import json

import pandas as pd

from veta import history


def _raw_row(**overrides):
    row = {
        "orden_gobierno": "APF",
        "ley": "LAASSP",
        "siglas": "IMSS",
        "partida": "25401",
        "rfc": "abc123",
        "proveedor": "ACME SA",
        "moneda": "MXN",
        "tipo_procedimiento": "LICITACIÓN PÚBLICA",
        "numero_procedimiento": "LA-1",
        "tipo_contratacion": "ADQUISICIONES",
        "caracter": "NACIONAL",
        "fecha_publicacion": "2024-03-01",
        "fecha_fallo": "2024-04-01",
        "importe": 1000.0,
        "estratificacion": "",
        "institucion": "",
        "uc_clave": "",
        "uc_nombre": "",
        "source_year": 2024,
    }
    row.update(overrides)
    return row


def test_normalize_filters_non_federal_and_non_laassp():
    df = pd.DataFrame([
        _raw_row(),
        _raw_row(orden_gobierno="GE"),        # state government, dropped
        _raw_row(ley="LOPSRM"),               # different law, dropped
    ])
    out = history._normalize(df)
    assert len(out) == 1
    assert out.iloc[0]["siglas"] == "IMSS"


def test_normalize_explodes_multi_partida():
    df = pd.DataFrame([_raw_row(partida="25301, 25401 ,25501")])
    out = history._normalize(df)
    assert sorted(out["partida"].tolist()) == ["25301", "25401", "25501"]


def test_normalize_cleans_keys():
    df = pd.DataFrame([_raw_row(rfc="  abc123 ", siglas="  IMSS ")])
    out = history._normalize(df)
    assert out.iloc[0]["rfc"] == "ABC123"
    assert out.iloc[0]["siglas"] == "IMSS"


def test_normalize_drops_empty_partida():
    df = pd.DataFrame([_raw_row(partida=""), _raw_row(partida="25401")])
    out = history._normalize(df)
    assert out["partida"].tolist() == ["25401"]


def _norm_row(**overrides):
    row = {
        "siglas": "IMSS",
        "partida": "25401",
        "rfc": "RFC1",
        "proveedor": "ACME",
        "moneda": "MXN",
        "importe": 100.0,
        "source_year": 2023,
        "fecha_publicacion": pd.Timestamp("2023-09-01"),
        "tipo_procedimiento": "LICITACIÓN PÚBLICA",
        "is_licitacion": True,
    }
    row.update(overrides)
    return row


def test_lookup_counts_and_price_band():
    df = pd.DataFrame([
        _norm_row(rfc="RFC1", importe=100.0, source_year=2023),
        _norm_row(rfc="RFC2", importe=200.0, source_year=2024),
        _norm_row(rfc="RFC3", importe=300.0, source_year=2025),
    ])
    lookup = history.build_buyer_partida_lookup(df)
    assert len(lookup) == 1
    r = lookup.iloc[0]
    assert r["contract_count"] == 3
    assert r["distinct_suppliers"] == 3
    assert r["price_min"] == 100.0
    assert r["price_median"] == 200.0
    assert r["price_max"] == 300.0


def test_lookup_new_entrant_rate_and_recurrence():
    # RFC1 present in the earliest year (not new), RFC2/RFC3 appear later (new).
    df = pd.DataFrame([
        _norm_row(rfc="RFC1", source_year=2023),
        _norm_row(rfc="RFC2", source_year=2024),
        _norm_row(rfc="RFC3", source_year=2025),
    ])
    lookup = history.build_buyer_partida_lookup(df)
    r = lookup.iloc[0]
    assert r["new_entrant_rate"] == round(2 / 3, 3)
    assert list(r["years_active"]) == [2023, 2024, 2025]
    assert bool(r["is_recurring"]) is True


def test_lookup_top_suppliers_sorted_by_value():
    df = pd.DataFrame([
        _norm_row(rfc="SMALL", proveedor="SMALL CO", importe=10.0),
        _norm_row(rfc="BIG", proveedor="BIG CO", importe=999.0),
    ])
    lookup = history.build_buyer_partida_lookup(df)
    top = json.loads(lookup.iloc[0]["top_suppliers"])
    assert top[0]["proveedor"] == "BIG CO"
    assert top[0]["total"] == 999.0


def test_lookup_price_band_ignores_non_mxn_and_zero():
    df = pd.DataFrame([
        _norm_row(rfc="RFC1", importe=100.0, moneda="MXN"),
        _norm_row(rfc="RFC2", importe=5000.0, moneda="USD"),   # excluded from band
        _norm_row(rfc="RFC3", importe=0.0, moneda="MXN"),      # excluded from band
    ])
    lookup = history.build_buyer_partida_lookup(df)
    r = lookup.iloc[0]
    assert r["price_max"] == 100.0
    # But all three still count toward contract_count.
    assert r["contract_count"] == 3
