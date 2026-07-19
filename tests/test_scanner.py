"""Tests for the adjacent opportunity scanner (veta.scanner)."""

from __future__ import annotations

import pandas as pd

from veta import scanner


def _contracts():
    return pd.DataFrame([
        {"partida": "25401", "siglas": "IMSS", "rfc": "R1", "moneda": "MXN", "importe": 1000.0},
        {"partida": "25301", "siglas": "IMSS", "rfc": "R2", "moneda": "MXN", "importe": 500.0},
        {"partida": "60000", "siglas": "IMSS", "rfc": "R3", "moneda": "MXN", "importe": 800.0},
        {"partida": "60000", "siglas": "ISSSTE", "rfc": "R4", "moneda": "MXN", "importe": 200.0},
    ])


def _lookup():
    return pd.DataFrame([
        {"partida": "25401", "new_entrant_rate": 0.3},
        {"partida": "25301", "new_entrant_rate": 0.1},
        {"partida": "60000", "new_entrant_rate": 0.5},
    ])


def _scan():
    return scanner.scan(
        descriptions={"60000": "Adjacent stuff"},
        contracts=_contracts(),
        lookup=_lookup(),
        targeted_claves={"25401"},
        excluded_claves={"25301"},
    )


def test_scan_flags_targeted_and_excluded():
    ranked = _scan().set_index("partida")
    assert bool(ranked.loc["25401", "is_targeted"]) is True
    assert bool(ranked.loc["25301", "is_excluded"]) is True
    assert bool(ranked.loc["60000", "is_targeted"]) is False


def test_scan_sorted_by_total_value_desc():
    ranked = _scan()
    assert ranked.iloc[0]["partida"] == "25401"  # 1000 is the largest


def test_scan_existing_buyer_overlap():
    ranked = _scan().set_index("partida")
    # IMSS buys the targeted partida, so it counts as an existing buyer overlap
    # for 60000; ISSSTE does not.
    assert ranked.loc["60000", "existing_buyer_overlap"] == 1


def test_adjacent_excludes_targeted_and_excluded():
    adjacent = scanner.adjacent_opportunities(
        descriptions={"60000": "Adjacent stuff"},
        contracts=_contracts(),
        lookup=_lookup(),
        targeted_claves={"25401"},
        excluded_claves={"25301"},
    )
    assert adjacent["partida"].tolist() == ["60000"]
    assert adjacent.iloc[0]["descripcion"] == "Adjacent stuff"
