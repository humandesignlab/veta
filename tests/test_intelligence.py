"""Tests for the buyer intelligence join and enrichment (veta.intelligence)."""

from __future__ import annotations

import datetime

import pandas as pd

from veta import intelligence
from veta.intelligence import BuyerIntel, EnrichedTender, Urgency

NOW = datetime.datetime(2026, 1, 10, 12, 0, 0)


def _lookup_row(siglas="IMSS", partida="25401", **overrides):
    row = {
        "siglas": siglas,
        "partida": partida,
        "contract_count": 40,
        "distinct_suppliers": 12,
        "new_entrant_rate": 0.35,
        "price_p10": 100.0,
        "price_median": 500.0,
        "price_p90": 9000.0,
        "years_active": [2023, 2024, 2025],
        "is_recurring": True,
        "typical_month": 9,
        "top_suppliers": "[]",
    }
    row.update(overrides)
    return row


def _listing_record(numero, siglas="IMSS", tipo="LICITACIÓN PÚBLICA", **overrides):
    rec = {
        "numero_procedimiento": numero,
        "uuid_procedimiento": "uuid-" + numero,
        "nombre_procedimiento": "Compra de " + numero,
        "siglas": siglas,
        "tipo_procedimiento": tipo,
        "tipo_contratacion": "ADQUISICIONES",
        "caracter": "NACIONAL",
        "estatus_alterno": "VIGENTE",
        "entidad_federativa_contratacion": "CDMX",
        "unidad_compradora": "UC1",
        "fecha_apertura": "2026-01-20T09:00:00",
    }
    rec.update(overrides)
    return rec


# ---- urgency ----------------------------------------------------------------

def _urgency(deadline_iso=None, aclaraciones_iso=None):
    rec = {}
    if deadline_iso:
        rec["fecha_apertura"] = deadline_iso
    if aclaraciones_iso:
        rec["fecha_aclaraciones"] = aclaraciones_iso
    return intelligence._compute_urgency(rec, NOW)


def test_urgency_green():
    assert _urgency("2026-01-20T09:00:00").level == "GREEN"


def test_urgency_amber():
    assert _urgency("2026-01-15T09:00:00").level == "AMBER"


def test_urgency_red_within_three_days():
    assert _urgency("2026-01-12T09:00:00").level == "RED"


def test_urgency_red_when_past():
    u = _urgency("2026-01-05T09:00:00")
    assert u.level == "RED"
    assert u.days_to_deadline == -5


def test_urgency_unknown_without_deadline():
    assert _urgency(None).level == "UNKNOWN"


def test_urgency_amber_when_aclaraciones_passed_far_deadline():
    # Deadline is 10 days out (would be GREEN) but clarifications already
    # closed: this is a risk signal, so it bumps to AMBER, not RED.
    u = _urgency("2026-01-20T09:00:00", aclaraciones_iso="2026-01-05T09:00:00")
    assert u.aclaraciones_passed is True
    assert u.level == "AMBER"


def test_urgency_red_beats_aclaraciones_amber():
    # A deadline within 3 days stays RED even though aclaraciones also passed.
    u = _urgency("2026-01-12T09:00:00", aclaraciones_iso="2026-01-05T09:00:00")
    assert u.level == "RED"


# ---- build_shortlist --------------------------------------------------------

def test_build_shortlist_matches_partida_and_history():
    lookup = pd.DataFrame([_lookup_row()])
    # partida id 39 maps to clave 25401 in the distributor profile.
    records_by_partida = {39: [_listing_record("LA-1")]}
    shortlist = intelligence.build_shortlist(records_by_partida, lookup, now=NOW)
    assert len(shortlist) == 1
    t = shortlist[0]
    assert t.matched_partidas == ["25401"]
    assert t.primary_intel.has_history is True
    assert t.primary_intel.contract_count == 40


def test_build_shortlist_drops_non_licitaciones():
    lookup = pd.DataFrame([_lookup_row()])
    records_by_partida = {
        39: [_listing_record("IA-9", tipo="INVITACIÓN", numero_procedimiento="IA-9")]
    }
    shortlist = intelligence.build_shortlist(records_by_partida, lookup, now=NOW)
    assert shortlist == []


def test_build_shortlist_no_history_flagged():
    lookup = pd.DataFrame([_lookup_row(siglas="OTHER")])
    records_by_partida = {39: [_listing_record("LA-2", siglas="IMSS")]}
    shortlist = intelligence.build_shortlist(records_by_partida, lookup, now=NOW)
    assert shortlist[0].primary_intel.has_history is False
    assert "NO HISTORY" in shortlist[0].signal


def test_build_shortlist_sorts_red_before_green():
    lookup = pd.DataFrame([_lookup_row()])
    records_by_partida = {
        39: [
            _listing_record("LA-GREEN", fecha_apertura="2026-02-20T09:00:00"),
            _listing_record("LA-RED", fecha_apertura="2026-01-11T09:00:00"),
        ]
    }
    shortlist = intelligence.build_shortlist(records_by_partida, lookup, now=NOW)
    assert shortlist[0].numero_procedimiento == "LA-RED"


# ---- verify_and_enrich (partida verification + monto) -----------------------

class _FakeClient:
    def __init__(self, partidas):
        self._partidas = partidas

    def fetch_partidas(self, uuid):
        return self._partidas


def _intel(partida="25401", contract_count=40, base_grade="STRONG", confidence="high"):
    return BuyerIntel(
        siglas="IMSS", partida=partida, partida_desc="desc",
        has_history=True, contract_count=contract_count, distinct_suppliers=12,
        new_entrant_rate=0.35, is_recurring=True, price_median=500_000.0,
        openness_shrunk=0.35, hhi=0.10, value_pctile=0.6,
        contestability_score=0.8, base_grade=base_grade, confidence=confidence,
    )


def _bare_tender(uuid="uuid-1", intel=None, matched=None):
    return EnrichedTender(
        numero_procedimiento="LA-1",
        nombre_procedimiento="x",
        siglas="IMSS",
        tipo_contratacion="ADQUISICIONES",
        caracter="NACIONAL",
        estatus_alterno="VIGENTE",
        entidad_federativa="CDMX",
        unidad_compradora="UC1",
        uuid_procedimiento=uuid,
        matched_partidas=matched if matched is not None else ["25401"],
        intel=intel if intel is not None else [],
        urgency=Urgency(None, None, None, None, "UNKNOWN"),
        signal="",
    )


def test_verify_sums_monto_bands():
    tender = _bare_tender()
    client = _FakeClient([
        {"monto_minimo": 100.0, "monto_maximo": 200.0},
        {"monto_minimo": None, "monto_maximo": 50.0},
    ])
    intelligence.verify_and_enrich([tender], client)
    assert tender.monto_min == 100.0
    assert tender.monto_max == 250.0
    assert tender.line_partidas == 2


def test_verify_monto_all_null_stays_none():
    tender = _bare_tender()
    client = _FakeClient([{"monto_minimo": None, "monto_maximo": None}])
    intelligence.verify_and_enrich([tender], client)
    assert tender.monto_min is None
    assert tender.monto_max is None
    assert tender.line_partidas == 1


def test_verify_keeps_intel_when_partida_present():
    tender = _bare_tender(intel=[_intel("25401")], matched=["25401"])
    client = _FakeClient([{"clave_p_especifica": "25401", "monto_minimo": None, "monto_maximo": None}])
    intelligence.verify_and_enrich([tender], client)
    assert [b.partida for b in tender.intel] == ["25401"]
    assert tender.signal.startswith("STRONG")


def test_verify_flags_unverified_when_partida_absent():
    # Filter matched 25401, but the real line items are kitchen articles (22104).
    tender = _bare_tender(intel=[_intel("25401")], matched=["25401"])
    client = _FakeClient([{"clave_p_especifica": "22104", "monto_minimo": None, "monto_maximo": None}])
    intelligence.verify_and_enrich([tender], client)
    assert tender.intel == []
    assert tender.primary_intel is None
    assert tender.signal.startswith("UNVERIFIED MATCH")


def test_verify_filters_to_only_real_partidas():
    tender = _bare_tender(
        intel=[_intel("25401", contract_count=40), _intel("25501", contract_count=10)],
        matched=["25401", "25501"],
    )
    client = _FakeClient([{"clave_p_especifica": "25501"}])
    intelligence.verify_and_enrich([tender], client)
    assert [b.partida for b in tender.intel] == ["25501"]
    assert tender.matched_partidas == ["25501"]


def test_primary_intel_favors_partida_with_more_line_items():
    # 25401 has far more national history, but the tender is dominated by 21101
    # line items, so 21101 should be the headline category.
    tender = _bare_tender(
        intel=[_intel("25401", contract_count=30000), _intel("21101", contract_count=2000)],
        matched=["25401", "21101"],
    )
    client = _FakeClient([
        {"clave_p_especifica": "21101"},
        {"clave_p_especifica": "21101"},
        {"clave_p_especifica": "21101"},
        {"clave_p_especifica": "25401"},
    ])
    intelligence.verify_and_enrich([tender], client)
    assert tender.line_item_counts == {"25401": 1, "21101": 3}
    assert tender.primary_intel.partida == "21101"


# ---- signal (Layer 1 formatter over the precomputed grade) ------------------

def _graded_intel(base_grade="STRONG", confidence="high", contract_count=40):
    return BuyerIntel(
        siglas="IMSS", partida="25401", partida_desc="med",
        has_history=True, contract_count=contract_count, distinct_suppliers=12,
        new_entrant_rate=0.35, is_recurring=True, price_median=500_000.0,
        openness_shrunk=0.42, hhi=0.08, value_pctile=0.7,
        contestability_score=0.85, base_grade=base_grade, confidence=confidence,
    )


def test_signal_formats_precomputed_grade_with_stats():
    intel = _graded_intel(base_grade="STRONG", confidence="high")
    signal = intelligence._signal(intel)
    assert signal.startswith("STRONG")
    assert "openness 42%" in signal
    assert "HHI 0.08" in signal
    assert "low confidence" not in signal


def test_signal_weak_grade_renders_weak():
    assert intelligence._signal(_graded_intel(base_grade="WEAK")).startswith("WEAK")


def test_signal_low_confidence_note():
    intel = _graded_intel(base_grade="MODERATE", confidence="low", contract_count=4)
    signal = intelligence._signal(intel)
    assert signal.startswith("MODERATE")
    assert "low confidence, n=4" in signal


def test_signal_no_history():
    assert "NO HISTORY" in intelligence._signal(None)


# ---- signal_grade / assign_bucket -------------------------------------------


def _tender_for_bucket(signal, days):
    return EnrichedTender(
        numero_procedimiento="LA-1", nombre_procedimiento="x", siglas="IMSS",
        tipo_contratacion="ADQUISICIONES", caracter="NACIONAL",
        estatus_alterno="VIGENTE", entidad_federativa="CDMX",
        unidad_compradora="UC", uuid_procedimiento="u",
        matched_partidas=["25401"], intel=[],
        urgency=Urgency(None, days, None, None, "RED"), signal=signal,
    )


def test_signal_grade_parses_token():
    assert intelligence.signal_grade("STRONG: open buyer") == "STRONG"
    assert intelligence.signal_grade("UNVERIFIED MATCH: ...") == "UNVERIFIED MATCH"
    assert intelligence.signal_grade("NO HISTORY") == "NO HISTORY"


def test_bucket_actuar_when_urgent_and_workable():
    assert intelligence.assign_bucket(_tender_for_bucket("STRONG: x", 2)) == "ACTUAR"


def test_bucket_actuar_includes_passed_deadline():
    assert intelligence.assign_bucket(_tender_for_bucket("MODERATE: x", -5)) == "ACTUAR"


def test_bucket_preparar_when_mid_window_and_workable():
    assert intelligence.assign_bucket(_tender_for_bucket("STRONG: x", 10)) == "PREPARAR"


def test_bucket_monitorear_when_far_out():
    assert intelligence.assign_bucket(_tender_for_bucket("MODERATE: x", 20)) == "MONITOREAR"


def test_bucket_monitorear_when_no_deadline():
    assert intelligence.assign_bucket(_tender_for_bucket("STRONG: x", None)) == "MONITOREAR"


def test_bucket_descartar_for_weak_regardless_of_deadline():
    assert intelligence.assign_bucket(_tender_for_bucket("WEAK: x", 1)) == "DESCARTAR"


def test_bucket_descartar_for_no_history():
    assert intelligence.assign_bucket(_tender_for_bucket("NO HISTORY", 20)) == "DESCARTAR"


def test_bucket_descartar_for_unverified():
    assert intelligence.assign_bucket(_tender_for_bucket("UNVERIFIED MATCH: x", 2)) == "DESCARTAR"


# --------------------------------------------------------------------------- #
# Strategic buckets (Layer 2 active).
# --------------------------------------------------------------------------- #


def _position(grade):
    from veta.positioning import ClientPosition

    return ClientPosition(
        position_grade=grade,
        position_score=0.5,
        p_win_low=0.2,
        p_win_high=0.3,
        n_prior_wins=5 if grade == "INCUMBENT" else 0,
        last_win_year=2025,
        share_of_buyer=0.1,
        n_other_buyers_same_partida=2,
        n_other_partidas_same_buyer=3,
        incumbency_strength=0.5,
        category_strength=0.4,
        relationship_strength=0.3,
    )


def _positioned_tender(pos_grade, base_grade, days=10):
    intel = _intel(base_grade=base_grade)
    intel.position = _position(pos_grade)
    t = _bare_tender(intel=[intel])
    t.urgency = Urgency(NOW + datetime.timedelta(days=days), days, None, None, "GREEN")
    t.signal = intelligence._signal(intel)
    return t


def test_strategic_bucket_experienced_strong_is_oportunidad():
    t = _positioned_tender("EXPERIENCED", "STRONG")
    assert intelligence.assign_strategic_bucket(t) == "OPORTUNIDAD"


def test_strategic_bucket_adjacent_moderate_is_oportunidad():
    t = _positioned_tender("ADJACENT", "MODERATE")
    assert intelligence.assign_strategic_bucket(t) == "OPORTUNIDAD"


def test_strategic_bucket_incumbent_is_territorio():
    t = _positioned_tender("INCUMBENT", "STRONG")
    assert intelligence.assign_strategic_bucket(t) == "TERRITORIO"


def test_strategic_bucket_outsider_strong_is_explorar():
    t = _positioned_tender("OUTSIDER", "STRONG")
    assert intelligence.assign_strategic_bucket(t) == "EXPLORAR"


def test_strategic_bucket_outsider_weak_is_no_prioritario():
    t = _positioned_tender("OUTSIDER", "WEAK")
    assert intelligence.assign_strategic_bucket(t) == "NO PRIORITARIO"


def test_strategic_bucket_experienced_weak_is_no_prioritario():
    # Relevant capability but a weak market does not justify preparation cost.
    t = _positioned_tender("EXPERIENCED", "WEAK")
    assert intelligence.assign_strategic_bucket(t) == "NO PRIORITARIO"


def test_strategic_bucket_no_position_is_no_prioritario():
    # No matched history/position in a client run: no basis to win, so the
    # strategic view parks it in NO PRIORITARIO (never an urgency bucket).
    t = _tender_for_bucket("STRONG: x", 2)
    assert t.primary_intel is None or t.primary_intel.position is None
    assert intelligence.assign_strategic_bucket(t) == "NO PRIORITARIO"
