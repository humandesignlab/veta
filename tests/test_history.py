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
    # Non-pharma bundle: all claves survive the explode (25301 would be dropped).
    df = pd.DataFrame([_raw_row(partida="21101, 25401 ,25501")])
    out = history._normalize(df)
    assert sorted(out["partida"].tolist()) == ["21101", "25401", "25501"]


def test_normalize_cleans_keys():
    df = pd.DataFrame([_raw_row(rfc="  abc123 ", siglas="  IMSS ")])
    out = history._normalize(df)
    assert out.iloc[0]["rfc"] == "ABC123"
    assert out.iloc[0]["siglas"] == "IMSS"


def test_normalize_drops_empty_partida():
    df = pd.DataFrame([_raw_row(partida=""), _raw_row(partida="25401")])
    out = history._normalize(df)
    assert out["partida"].tolist() == ["25401"]


def test_normalize_drops_pharma_bundled_contract():
    # A contract bundling pharma (25301) with medical supplies (25401) must be
    # dropped entirely so pharma does not leak into 25401 intelligence.
    df = pd.DataFrame([
        _raw_row(partida="25301, 25401", proveedor="PHARMA GIANT"),
        _raw_row(partida="25401", proveedor="HONEST SUPPLIER"),
    ])
    out = history._normalize(df)
    assert out["proveedor"].tolist() == ["HONEST SUPPLIER"]
    assert "25301" not in out["partida"].tolist()


def test_normalize_drops_pure_pharma_contract():
    df = pd.DataFrame([_raw_row(partida="25301"), _raw_row(partida="25401")])
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
    assert r["price_median"] == 200.0
    # P10/P90 on [100, 200, 300] interpolate to 120 and 280.
    assert r["price_p10"] == 120.0
    assert r["price_p90"] == 280.0


def test_lookup_price_percentiles_trim_outliers():
    # One tiny and one huge outlier should not define the band.
    df = pd.DataFrame(
        [_norm_row(rfc=f"R{i}", importe=100.0) for i in range(8)]
        + [_norm_row(rfc="TINY", importe=1.0), _norm_row(rfc="HUGE", importe=1_000_000.0)]
    )
    r = history.build_buyer_partida_lookup(df).iloc[0]
    assert r["price_median"] == 100.0
    assert r["price_p10"] > 1.0
    assert r["price_p90"] < 1_000_000.0


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
    assert r["price_p90"] == 100.0
    # But all three still count toward contract_count.
    assert r["contract_count"] == 3


# ---- Layer 1: contestability stats ------------------------------------------

def test_fit_beta_prior_falls_back_when_too_few_cells():
    # Fewer than MIN_CELLS_FOR_PRIOR cells -> weak global prior.
    rates = pd.Series([0.1, 0.5, 0.9])
    assert history._fit_beta_prior(rates) == history.GLOBAL_PRIOR


def test_fit_beta_prior_falls_back_on_degenerate_variance():
    rates = pd.Series([0.4] * 50)  # zero variance
    assert history._fit_beta_prior(rates) == history.GLOBAL_PRIOR


def test_fit_beta_prior_reasonable_for_spread():
    import numpy as np

    rng = np.random.default_rng(0)
    rates = pd.Series(rng.beta(2, 5, size=200))
    alpha, beta = history._fit_beta_prior(rates)
    assert alpha > 0 and beta > 0
    # Recovered prior mean should track the sample mean.
    assert abs(alpha / (alpha + beta) - rates.mean()) < 0.05


def test_hhi_equal_shares_is_half():
    df = pd.DataFrame([
        _norm_row(rfc="A", importe=500.0),
        _norm_row(rfc="B", importe=500.0),
    ])
    r = history.build_buyer_partida_lookup(df).iloc[0]
    assert round(r["hhi"], 4) == 0.5


def test_openness_shrinkage_pulls_thin_cell_toward_prior():
    # 1 of 2 suppliers new -> raw 0.5; with GLOBAL_PRIOR (1,2): (1+1)/(2+3)=0.4.
    df = pd.DataFrame([
        _norm_row(rfc="OLD", source_year=2023),
        _norm_row(rfc="NEW", source_year=2024),
    ])
    r = history.build_buyer_partida_lookup(df).iloc[0]
    assert r["new_entrant_rate"] == 0.5
    assert round(r["openness_shrunk"], 4) == 0.4


def test_openness_shrinkage_barely_moves_rich_cell():
    # 30 of 100 suppliers new -> raw 0.30; shrinks only to (30+1)/(100+3)=0.301.
    rows = [_norm_row(rfc=f"OLD{i}", source_year=2023) for i in range(70)]
    rows += [_norm_row(rfc=f"NEW{i}", source_year=2024) for i in range(30)]
    r = history.build_buyer_partida_lookup(pd.DataFrame(rows)).iloc[0]
    assert r["new_entrant_rate"] == 0.3
    assert abs(r["openness_shrunk"] - 0.301) < 0.001


def test_value_pctile_ranks_within_partida():
    df = pd.DataFrame([
        _norm_row(siglas="IMSS", rfc="A", importe=100.0),
        _norm_row(siglas="ISSSTE", rfc="B", importe=300.0),
    ])
    lookup = history.build_buyer_partida_lookup(df).set_index("siglas")
    assert lookup.loc["ISSSTE", "value_pctile"] > lookup.loc["IMSS", "value_pctile"]


def test_assign_grades_gate_and_quantiles():
    # Gate-passing scores [0.1,0.2,0.3,0.4,0.9]: q0.5=0.3, q0.8=0.66.
    rows = []
    for score in [0.1, 0.2, 0.3, 0.4, 0.9]:
        rows.append({"contestability_score": score, "contract_count": 20, "distinct_suppliers": 10})
    # A high-score but low-confidence (thin) cell: must not be STRONG.
    rows.append({"contestability_score": 0.95, "contract_count": 3, "distinct_suppliers": 2})
    graded = history._assign_grades(pd.DataFrame(rows))
    by_score = graded.set_index("contestability_score")["base_grade"].to_dict()
    assert by_score[0.9] == "STRONG"        # high conf, top quintile
    assert by_score[0.3] == "MODERATE"      # >= median cut
    assert by_score[0.2] == "WEAK"          # below median cut
    assert by_score[0.95] == "MODERATE"     # thin cell capped below STRONG
    assert (graded["base_grade"] == "STRONG").sum() == 1


def test_repeat_win_rate_by_partida():
    df = pd.DataFrame([
        _norm_row(siglas="IMSS", partida="25401", rfc="RFC1", source_year=2023),
        _norm_row(siglas="IMSS", partida="25401", rfc="RFC1", source_year=2024),
        _norm_row(siglas="IMSS", partida="25401", rfc="RFC2", source_year=2023),
    ])
    rates = history.repeat_win_rate_by_partida(df)
    # 3 (buyer,partida,rfc,year) rows; only RFC1@2023 has a same-cell win in Y+1.
    assert round(rates["25401"], 3) == round(1 / 3, 3)
    assert "__global__" in rates


def test_latest_contract_date_uses_most_recent_of_either_date():
    df = pd.DataFrame({
        "fecha_fallo": pd.to_datetime(["2025-01-15", None]),
        "fecha_publicacion": pd.to_datetime(["2024-12-01", "2025-06-20"]),
    })
    assert history._latest_contract_date(df) == __import__("datetime").date(2025, 6, 20)


def test_cache_status_line_none_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "LOOKUP_PARQUET", tmp_path / "missing.parquet")
    assert history.cache_status_line() is None


def test_cache_status_line_from_meta(tmp_path, monkeypatch):
    import datetime

    lookup = tmp_path / "buyer_partida.parquet"
    lookup.write_bytes(b"x")
    meta = tmp_path / "cache_meta.json"
    latest = (datetime.date.today() - datetime.timedelta(days=21)).isoformat()
    meta.write_text(json.dumps({"built": "2026-07-19", "latest_contract": latest}))
    monkeypatch.setattr(history, "LOOKUP_PARQUET", lookup)
    monkeypatch.setattr(history, "CACHE_META", meta)

    line = history.cache_status_line()
    assert line == f"Historical cache: built 2026-07-19, latest contract {latest} (21 days old)"
