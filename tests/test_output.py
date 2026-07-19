"""Tests for the console/XLSX formatters (veta.output)."""

from __future__ import annotations

import datetime

from veta import output
from veta.intelligence import BuyerIntel, EnrichedTender, Urgency


def _tender(**overrides):
    base = dict(
        numero_procedimiento="LA-1",
        nombre_procedimiento="Compra de prueba",
        siglas="IMSS",
        tipo_contratacion="ADQUISICIONES",
        caracter="NACIONAL",
        estatus_alterno="VIGENTE",
        entidad_federativa="CDMX",
        unidad_compradora="UC1",
        uuid_procedimiento="uuid-1",
        matched_partidas=["25401"],
        intel=[BuyerIntel("IMSS", "25401", "med", True, contract_count=5, new_entrant_rate=0.4)],
        urgency=Urgency(datetime.datetime(2026, 1, 20, 9, 0), 10, None, None, "GREEN"),
        signal="STRONG: open buyer",
    )
    base.update(overrides)
    return EnrichedTender(**base)


def test_money():
    assert output._money(None) == "n/a"
    assert output._money(1234567) == "$1,234,567 MXN"


def test_month_name():
    assert output._month_name(9) == "Sep"
    assert output._month_name(None) == "n/a"
    assert output._month_name(13) == "n/a"


def test_monto_line_not_queried_returns_none():
    t = _tender(line_partidas=None)
    assert output._monto_line(t) is None


def test_monto_line_not_published():
    t = _tender(line_partidas=1, monto_min=None, monto_max=None)
    assert output._monto_line(t) == "Est. value: not published by buyer"


def test_monto_line_band():
    t = _tender(line_partidas=1, monto_min=100.0, monto_max=200.0)
    assert output._monto_line(t) == "Est. value: $100 MXN to $200 MXN"


def test_render_card_contains_key_fields():
    card = output.render_card(_tender())
    assert "LA-1" in card
    assert "SIGNAL:" in card
    assert "Urgency:   GREEN" in card


def test_to_dataframe_has_monto_columns():
    frame = output.to_dataframe([_tender(line_partidas=1, monto_min=100.0, monto_max=200.0)])
    assert "est_monto_min" in frame.columns
    assert "est_monto_max" in frame.columns
    assert frame.iloc[0]["est_monto_min"] == 100.0
    assert frame.iloc[0]["numero_procedimiento"] == "LA-1"
