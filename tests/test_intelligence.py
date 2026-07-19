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


# ---- attach_monto -----------------------------------------------------------

class _FakeClient:
    def __init__(self, partidas):
        self._partidas = partidas

    def fetch_partidas(self, uuid):
        return self._partidas


def _bare_tender(uuid="uuid-1"):
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
        matched_partidas=["25401"],
        intel=[],
        urgency=Urgency(None, None, None, None, "UNKNOWN"),
        signal="",
    )


def test_attach_monto_sums_bands():
    tender = _bare_tender()
    client = _FakeClient([
        {"monto_minimo": 100.0, "monto_maximo": 200.0},
        {"monto_minimo": None, "monto_maximo": 50.0},
    ])
    intelligence.attach_monto([tender], client)
    assert tender.monto_min == 100.0
    assert tender.monto_max == 250.0
    assert tender.line_partidas == 2


def test_attach_monto_all_null_stays_none():
    tender = _bare_tender()
    client = _FakeClient([{"monto_minimo": None, "monto_maximo": None}])
    intelligence.attach_monto([tender], client)
    assert tender.monto_min is None
    assert tender.monto_max is None
    assert tender.line_partidas == 1


# ---- signal -----------------------------------------------------------------

def test_signal_strong_for_open_recurring_valuable():
    intel = BuyerIntel(
        siglas="IMSS", partida="25401", partida_desc="med",
        has_history=True, contract_count=40, distinct_suppliers=12,
        new_entrant_rate=0.35, is_recurring=True, price_median=500_000.0,
    )
    assert intelligence._signal(intel).startswith("STRONG")


def test_signal_moderate_when_median_below_threshold():
    # Open and recurring but low-value: not STRONG.
    intel = BuyerIntel(
        siglas="IMSS", partida="25401", partida_desc="med",
        has_history=True, contract_count=40, distinct_suppliers=12,
        new_entrant_rate=0.35, is_recurring=True, price_median=1_000.0,
    )
    assert intelligence._signal(intel).startswith("MODERATE")


def test_signal_moderate_for_closed_recurring_buyer():
    intel = BuyerIntel(
        siglas="IMSS", partida="25401", partida_desc="med",
        has_history=True, contract_count=40, distinct_suppliers=12,
        new_entrant_rate=0.05, is_recurring=True, price_median=500_000.0,
    )
    assert intelligence._signal(intel).startswith("MODERATE")


def test_signal_weak_when_neither_open_nor_recurring():
    intel = BuyerIntel(
        siglas="IMSS", partida="25401", partida_desc="med",
        has_history=True, contract_count=3, distinct_suppliers=2,
        new_entrant_rate=0.05, is_recurring=False, price_median=500_000.0,
    )
    assert intelligence._signal(intel).startswith("WEAK")


def test_signal_no_history():
    assert "NO HISTORY" in intelligence._signal(None)
