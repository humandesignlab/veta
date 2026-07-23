"""Tests for Layer 2 distributor positioning (veta.positioning)."""

from __future__ import annotations

import pandas as pd

from veta import positioning
from veta.intelligence import BuyerIntel, EnrichedTender, Urgency

CLIENT = "CLI010101AAA"
BASE_RATE = 0.30
REPEAT_RATE = 0.40


def _contracts(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "siglas": "IMSS",
        "partida": "25401",
        "rfc": CLIENT,
        "proveedor": "CLIENTE SA",
        "moneda": "MXN",
        "importe": 100_000.0,
        "source_year": 2025,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _position(contracts, siglas="IMSS", partida="25401", confidence="high"):
    return positioning.compute_position(
        CLIENT, siglas, partida, contracts,
        repeat_win_rate=REPEAT_RATE, openness_shrunk=BASE_RATE,
        current_year=2026, confidence=confidence,
    )


def test_incumbent_scores_highest():
    incumbent = _contracts([{"source_year": 2025} for _ in range(10)])
    pos_inc = _position(incumbent)

    # Category-only: wins in same partida but at other buyers.
    category = _contracts([
        {"siglas": "ISSSTE"}, {"siglas": "SEDENA"}, {"siglas": "PEMEX"},
    ])
    pos_cat = _position(category)

    assert pos_inc.position_grade == "INCUMBENT"
    assert pos_cat.position_grade == "EXPERIENCED"
    assert pos_inc.position_score > pos_cat.position_score


def test_recency_decay():
    recent = _position(_contracts([{"source_year": 2025}]))
    old = _position(_contracts([{"source_year": 2022}]))
    assert recent.incumbency_strength > old.incumbency_strength


def test_category_transfer_is_experienced():
    contracts = _contracts([
        {"siglas": "ISSSTE"}, {"siglas": "SEDENA"}, {"siglas": "PEMEX"},
    ])
    pos = _position(contracts)  # target buyer IMSS has no client history
    assert pos.position_grade == "EXPERIENCED"
    assert pos.n_other_buyers_same_partida == 3


def test_category_below_evidence_threshold_is_outsider():
    # Only 2 contracts in the partida (below MIN_CATEGORY_EVIDENCE) and no
    # relationship with the buyer: one-off wins are noise, not expertise.
    contracts = _contracts([{"siglas": "ISSSTE"}, {"siglas": "SEDENA"}])
    pos = _position(contracts)
    assert pos.position_grade == "OUTSIDER"
    assert pos.category_strength == 0.0


def test_category_below_evidence_threshold_with_relationship_is_adjacent():
    # 2 partida contracts elsewhere (below threshold) but a real relationship
    # with the target buyer in other partidas -> ADJACENT, not EXPERIENCED.
    contracts = _contracts(
        [{"siglas": "ISSSTE"}, {"siglas": "SEDENA"}]
        + [{"siglas": "IMSS", "partida": p} for p in ["21101", "27101"]]
    )
    pos = _position(contracts, partida="25401")
    assert pos.position_grade == "ADJACENT"
    assert pos.category_strength == 0.0


def test_category_at_evidence_threshold_is_experienced():
    # Exactly MIN_CATEGORY_EVIDENCE contracts at a single other buyer qualifies.
    contracts = _contracts([{"siglas": "ISSSTE"} for _ in range(3)])
    pos = _position(contracts)
    assert pos.position_grade == "EXPERIENCED"
    assert pos.category_strength > 0.0


def test_relationship_transfer_is_adjacent():
    # Client sells other partidas to IMSS, but never partida 25401 anywhere.
    contracts = _contracts([
        {"partida": p} for p in ["21101", "21201", "27101", "29101", "35101"]
    ])
    pos = _position(contracts, partida="25401")
    assert pos.position_grade == "ADJACENT"
    assert pos.n_other_partidas_same_buyer == 5


def test_outsider_is_fraction_of_market_rate():
    # A cold outsider gets only the baseline fraction of the market's collective
    # new-entrant rate, not the full rate (which is shared among all entrants).
    contracts = _contracts([{"rfc": "OTHER00000X", "siglas": "PEMEX", "partida": "99999"}])
    pos = _position(contracts)
    assert pos.position_grade == "OUTSIDER"
    midpoint = (pos.p_win_low + pos.p_win_high) / 2
    assert abs(midpoint - BASE_RATE * positioning.NON_INCUMBENT_BASELINE) < 1e-6
    assert midpoint < BASE_RATE  # never inflated to the market rate


def test_non_incumbent_capped_below_certainty():
    # High-openness market + strong category expertise must not produce a
    # near-certain win for a company with zero prior contracts at this buyer.
    contracts = _contracts([
        {"siglas": s} for s in ["ISSSTE", "SEDENA", "PEMEX", "SAT", "IPN", "CFE"]
    ])
    pos = positioning.compute_position(
        CLIENT, "IMSS", "25401", contracts,
        repeat_win_rate=0.9, openness_shrunk=0.9,
        current_year=2026, confidence="high",
    )
    assert pos.position_grade == "EXPERIENCED"
    assert pos.p_win_high <= positioning.NON_INCUMBENT_P_CAP


def test_experienced_beats_outsider_probability():
    outsider = _position(
        _contracts([{"rfc": "OTHER00000X", "siglas": "PEMEX", "partida": "99999"}])
    )
    experienced = _position(_contracts([
        {"siglas": "ISSSTE"}, {"siglas": "SEDENA"}, {"siglas": "PEMEX"},
    ]))
    assert experienced.p_win_high > outsider.p_win_high


def test_probability_cap():
    # A massive incumbent still cannot exceed the cap.
    contracts = _contracts([{"source_year": 2025} for _ in range(500)])
    pos = positioning.compute_position(
        CLIENT, "IMSS", "25401", contracts,
        repeat_win_rate=0.99, openness_shrunk=0.99,
        current_year=2026, confidence="high",
    )
    assert pos.p_win_high <= positioning.P_WIN_CAP


def test_shrinkage_prevents_overconfidence():
    # A single prior win must not produce a high incumbency strength.
    one_win = _position(_contracts([{"source_year": 2025}]))
    assert one_win.n_prior_wins == 1
    assert one_win.incumbency_strength < 0.25  # 1/(1+5) = 0.167


def _tender_with_intel(intel: BuyerIntel | None) -> EnrichedTender:
    return EnrichedTender(
        numero_procedimiento="LA-1", nombre_procedimiento="x", siglas="IMSS",
        tipo_contratacion="ADQUISICIONES", caracter="NACIONAL",
        estatus_alterno="VIGENTE", entidad_federativa="CDMX",
        unidad_compradora="UC1", uuid_procedimiento="u1",
        matched_partidas=["25401"], intel=[intel] if intel else [],
        urgency=Urgency(None, None, None, None, "UNKNOWN"), signal="",
    )


def _market_intel() -> BuyerIntel:
    return BuyerIntel(
        siglas="IMSS", partida="25401", partida_desc="med", has_history=True,
        contract_count=40, distinct_suppliers=12, openness_shrunk=0.35,
        hhi=0.10, base_grade="MODERATE", confidence="high",
    )


def test_enrich_attaches_position_and_updates_signal():
    intel = _market_intel()
    tender = _tender_with_intel(intel)
    contracts = _contracts([{"source_year": 2025} for _ in range(4)])
    positioning.enrich_with_position(
        [tender], CLIENT, contracts, {"25401": REPEAT_RATE, "__global__": 0.28}
    )
    assert intel.position is not None
    assert intel.position.position_grade == "INCUMBENT"
    assert "INCUMBENT" in tender.signal
    assert tender.signal.startswith("MODERATE")  # market grade preserved


def test_no_rfc_skips_positioning():
    # Simulate the CLIENT_RFC-None path: enrich is simply never called, so the
    # signal stays market-only and position is None.
    intel = _market_intel()
    tender = _tender_with_intel(intel)
    from veta import intelligence

    tender.signal = intelligence._signal(intel)
    assert intel.position is None
    assert "INCUMBENT" not in tender.signal
    assert "||" not in tender.signal
