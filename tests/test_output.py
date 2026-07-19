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


def test_monto_line_always_shown_not_published():
    t = _tender(line_partidas=None, monto_min=None, monto_max=None)
    assert output._monto_line(t) == "Est. value: not published by buyer"


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


def test_senal_es_translates_grades():
    assert output._senal_es("STRONG: x") == "FUERTE"
    assert output._senal_es("MODERATE: x") == "MODERADA"
    assert output._senal_es("WEAK: x") == "DEBIL"
    assert output._senal_es("NO HISTORY") == "SIN HISTORIAL"
    assert output._senal_es("UNVERIFIED MATCH: x") == "SIN VERIFICAR"


def test_monto_cell_not_published_and_band():
    assert output._monto_cell(_tender(monto_min=None, monto_max=None)) == "No publicado"
    assert output._monto_cell(_tender(monto_min=100.0, monto_max=200.0)) == "$100 - $200 MXN"


def test_default_report_path_is_dated():
    import datetime

    path = output.default_report_path()
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert path == f"reports/reporte-veta-{today}.xlsx"


def test_write_client_xlsx_has_two_named_sheets(tmp_path):
    from openpyxl import load_workbook

    path = str(tmp_path / "reporte.xlsx")
    t = _tender(line_partidas=50, monto_min=100.0, monto_max=200.0)
    output.write_client_xlsx([t], path)

    wb = load_workbook(path)
    assert wb.sheetnames == ["Resumen", "Detalle"]
    resumen = wb["Resumen"]
    assert resumen["A1"].value == "VETA - Reporte de Inteligencia"
    assert resumen["A6"].value == "Accion"
    # Header row 6, first data row 7 carries the tender.
    assert resumen["B7"].value == "LA-1"
    detalle = wb["Detalle"]
    assert detalle["A1"].value == "Accion"
    assert detalle["B1"].value == "No. Procedimiento"
