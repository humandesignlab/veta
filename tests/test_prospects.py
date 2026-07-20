"""Tests for the potential-client (prospect) list builder."""

from __future__ import annotations

import pandas as pd

from veta import prospects


def _contracts(rows: list[dict]) -> pd.DataFrame:
    """Build a contracts frame with the columns build_prospects reads."""
    defaults = {
        "partida": "25401",
        "moneda": "MXN",
        "importe": 100_000.0,
        "is_licitacion": True,
        "source_year": 2025,
        "siglas": "IMSS",
        "rfc": "AAA010101AAA",
        "proveedor": "PROVEEDOR UNO SA DE CV",
        "estratificacion": "PEQUEÑA",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_ranks_and_aggregates_supplier():
    df = _contracts([
        {"partida": "25401", "importe": 100_000.0},
        {"partida": "27101", "importe": 50_000.0, "siglas": "ISSSTE"},
    ])
    result = prospects.build_prospects(df)
    assert len(result) == 1
    p = result[0]
    assert p.rfc == "AAA010101AAA"
    assert p.total_contracts == 2
    assert p.licitacion_contracts == 2
    assert p.total_value == 150_000.0
    assert p.distinct_partidas == 2
    assert p.distinct_buyers == 2
    assert p.last_year == 2025


def test_mipyme_only_excludes_grande():
    df = _contracts([
        {"rfc": "SME000000AAA", "estratificacion": "MEDIANA"},
        {"rfc": "SME000000AAA", "estratificacion": "MEDIANA"},
        {"rfc": "BIG000000BBB", "estratificacion": "GRANDE"},
        {"rfc": "BIG000000BBB", "estratificacion": "GRANDE"},
    ])
    mipyme = prospects.build_prospects(df, mipyme_only=True)
    assert {p.rfc for p in mipyme} == {"SME000000AAA"}
    everyone = prospects.build_prospects(df, mipyme_only=False)
    assert {p.rfc for p in everyone} == {"SME000000AAA", "BIG000000BBB"}


def test_drops_inactive_and_one_off_and_non_licitacion():
    df = _contracts([
        # stale: last win before min_last_year
        {"rfc": "OLD000000AAA", "source_year": 2023},
        {"rfc": "OLD000000AAA", "source_year": 2023},
        # one-off: only one contract
        {"rfc": "ONE000000BBB"},
        # no competitive participation
        {"rfc": "ADJ000000CCC", "is_licitacion": False},
        {"rfc": "ADJ000000CCC", "is_licitacion": False},
    ])
    result = prospects.build_prospects(df)
    assert result == []


def test_non_target_partida_excluded():
    df = _contracts([
        {"partida": "99999"},
        {"partida": "99999"},
    ])
    assert prospects.build_prospects(df) == []


def test_score_favors_breadth_and_recency():
    df = _contracts([
        # broad + recent
        {"rfc": "BROAD00000A", "partida": "25401"},
        {"rfc": "BROAD00000A", "partida": "27101", "siglas": "ISSSTE"},
        {"rfc": "BROAD00000A", "partida": "21101", "siglas": "SEP"},
        # narrow, older
        {"rfc": "NARROW0000B", "partida": "25401", "source_year": 2024},
        {"rfc": "NARROW0000B", "partida": "25401", "source_year": 2024},
    ])
    result = prospects.build_prospects(df)
    assert result[0].rfc == "BROAD00000A"
    assert result[0].score > result[1].score


def _prospect(rfc: str, **overrides) -> prospects.Prospect:
    """Build a Prospect that passes every qualify criterion by default."""
    defaults = dict(
        rfc=rfc,
        proveedor=f"EMPRESA {rfc}",
        estratificacion="PEQUEÑA",
        total_contracts=50,
        licitacion_contracts=40,  # 0.80 ratio
        total_value=1_000_000.0,
        distinct_partidas=6,
        distinct_buyers=8,
        last_year=2025,
        years_active=3,
        score=100.0,
    )
    defaults.update(overrides)
    return prospects.Prospect(**defaults)


def test_qualify_filters_by_all_criteria():
    good = _prospect("GOOD")
    fails = [
        _prospect("FEWCAT", distinct_partidas=4),
        _prospect("FEWBUY", distinct_buyers=4),
        _prospect("LOWLIC", licitacion_contracts=20),  # 0.40 ratio
        _prospect("TOOFEW", total_contracts=9, licitacion_contracts=9),
        _prospect("TOOMANY", total_contracts=600, licitacion_contracts=600),
        _prospect("MICRO", estratificacion="MICRO"),
        _prospect("STALE", last_year=2024),
    ]
    result = prospects.qualify_prospects([good, *fails])
    assert [p.rfc for p in result] == ["GOOD"]


def test_qualify_require_latest_year_is_dynamic():
    # Max year in the data is 2024, so a 2024 company is "current".
    recent = _prospect("RECENT2024", last_year=2024)
    old = _prospect("OLD2023", last_year=2023)
    result = prospects.qualify_prospects([recent, old])
    assert [p.rfc for p in result] == ["RECENT2024"]

    # Disabling the check keeps the older one too.
    both = prospects.qualify_prospects([recent, old], require_latest_year=False)
    assert {p.rfc for p in both} == {"RECENT2024", "OLD2023"}


def test_qualify_returns_empty_when_none_pass():
    fails = [
        _prospect("A", distinct_partidas=1),
        _prospect("B", estratificacion="GRANDE"),
        _prospect("C", licitacion_contracts=0),
    ]
    assert prospects.qualify_prospects(fails) == []
    assert prospects.qualify_prospects([]) == []
