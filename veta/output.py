"""Output formatters (spec section 3.5, step 5).

Renders the enriched shortlist (from veta.intelligence) as console intelligence
cards and as an XLSX workbook with one row per tender. No em dashes in any
output; monetary values are Mexican pesos.
"""

from __future__ import annotations

import json

import pandas as pd

from veta.intelligence import BuyerIntel, EnrichedTender

MONTHS = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

CARD_WIDTH = 60


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.0f} MXN"


def _month_name(month: int | None) -> str:
    if month is None or month < 1 or month > 12:
        return "n/a"
    return MONTHS[month]


def _deadline_line(tender: EnrichedTender) -> str:
    u = tender.urgency
    if u.deadline is None:
        return "Deadline:  unknown"
    days = u.days_to_deadline
    if days is None:
        remaining = ""
    elif days < 0:
        remaining = f" ({abs(days)} days ago)"
    else:
        remaining = f" ({days} days remaining)"
    return f"Deadline:  {u.deadline.date()}{remaining}"


def _aclaraciones_line(tender: EnrichedTender) -> str:
    u = tender.urgency
    if u.aclaraciones is None:
        return "Clarif.:   none listed"
    state = "PASSED" if u.aclaraciones_passed else "upcoming"
    return f"Clarif.:   {u.aclaraciones.date()} ({state})"


def _monto_line(tender: EnrichedTender) -> str:
    """Estimated value line. Always shown; buyers rarely publish amounts."""
    if tender.monto_min is None and tender.monto_max is None:
        return "Est. value: not published by buyer"
    if tender.monto_min == tender.monto_max:
        return f"Est. value: {_money(tender.monto_min)}"
    return f"Est. value: {_money(tender.monto_min)} to {_money(tender.monto_max)}"


def _intel_block(intel: BuyerIntel) -> list[str]:
    if not intel.has_history:
        return [
            f"BUYER INTELLIGENCE ({intel.siglas} + partida {intel.partida}):",
            "  No federal history for this buyer and category in 2023-2025.",
        ]
    rate = intel.new_entrant_rate
    open_flag = "  [OPEN BUYER]" if intel.is_open_buyer else ""
    lines = [
        f"BUYER INTELLIGENCE ({intel.siglas} + partida {intel.partida}, 2023-2025):",
        f"  Category:            {intel.partida_desc}",
        f"  Contracts awarded:   {intel.contract_count:,}",
        f"  Distinct suppliers:  {intel.distinct_suppliers:,}",
        f"  New entrant rate:    {rate:.0%}{open_flag}" if rate is not None else "  New entrant rate:    n/a",
        f"  Price band (P10-P90): {_money(intel.price_p10)} to {_money(intel.price_p90)}",
        f"  Median value:        {_money(intel.price_median)}",
        f"  Recurrence:          {'recurring ' + str(intel.years_active) if intel.is_recurring else 'irregular ' + str(intel.years_active)}",
        f"  Typical month:       {_month_name(intel.typical_month)}",
    ]
    if intel.top_suppliers:
        lines.append("  Top winners:")
        for s in intel.top_suppliers[:3]:
            lines.append(
                f"    {s['proveedor'][:44]} "
                f"({s['count']} contracts, {_money(s['total'])})"
            )
    return lines


def render_card(tender: EnrichedTender) -> str:
    bar = "=" * CARD_WIDTH
    lines = [
        bar,
        tender.numero_procedimiento,
        tender.nombre_procedimiento[:CARD_WIDTH] or "(no title)",
        bar,
        f"Buyer:     {tender.siglas} ({tender.entidad_federativa})",
        f"Type:      {tender.tipo_contratacion} | {tender.caracter}",
        _deadline_line(tender),
        _aclaraciones_line(tender),
        f"Urgency:   {tender.urgency.level}",
        _monto_line(tender),
        "",
    ]
    primary = tender.primary_intel
    if primary is not None:
        lines.extend(_intel_block(primary))
        if tender.line_partidas:
            matched = tender.line_item_counts.get(primary.partida, 0)
            share = f"  Line items in this category: {matched} of {tender.line_partidas}"
            if matched and matched * 2 < tender.line_partidas:
                share += "  [minority line]"
            lines.append(share)
    lines.append("")
    lines.append(f"SIGNAL: {tender.signal}")
    lines.append(bar)
    return "\n".join(lines)


def render_console(shortlist: list[EnrichedTender]) -> str:
    header = (
        f"VETA SHORTLIST: {len(shortlist)} active licitaciones publicas "
        "with buyer intelligence\n"
    )
    return header + "\n\n".join(render_card(t) for t in shortlist)


def to_dataframe(shortlist: list[EnrichedTender]) -> pd.DataFrame:
    """One row per tender with intelligence columns for XLSX export."""
    rows = []
    for t in shortlist:
        p = t.primary_intel
        rows.append(
            {
                "numero_procedimiento": t.numero_procedimiento,
                "nombre_procedimiento": t.nombre_procedimiento,
                "siglas": t.siglas,
                "entidad_federativa": t.entidad_federativa,
                "unidad_compradora": t.unidad_compradora,
                "tipo_contratacion": t.tipo_contratacion,
                "caracter": t.caracter,
                "estatus": t.estatus_alterno,
                "matched_partidas": ", ".join(t.matched_partidas),
                "deadline": t.urgency.deadline.date() if t.urgency.deadline else None,
                "days_to_deadline": t.urgency.days_to_deadline,
                "aclaraciones_passed": t.urgency.aclaraciones_passed,
                "urgency": t.urgency.level,
                "est_monto_min": t.monto_min,
                "est_monto_max": t.monto_max,
                "signal": t.signal,
                "hist_partida": p.partida if p else None,
                "hist_category": p.partida_desc if p else None,
                "hist_contract_count": p.contract_count if p else None,
                "hist_distinct_suppliers": p.distinct_suppliers if p else None,
                "hist_new_entrant_rate": p.new_entrant_rate if p else None,
                "hist_price_p10": p.price_p10 if p else None,
                "hist_price_median": p.price_median if p else None,
                "hist_price_p90": p.price_p90 if p else None,
                "hist_recurring": p.is_recurring if p else None,
                "hist_typical_month": p.typical_month if p else None,
                "hist_top_suppliers": json.dumps(p.top_suppliers, ensure_ascii=False) if p else None,
                "uuid_procedimiento": t.uuid_procedimiento,
            }
        )
    return pd.DataFrame(rows)


def check_xlsx_writable(path: str) -> None:
    """Raise a clear error if an XLSX cannot be written to path.

    Checks that openpyxl is importable in the active environment and that the
    parent directory exists. Meant to be called before a long fetch so a
    misconfigured run fails fast instead of after minutes of work.
    """
    import os

    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl is required to write XLSX files but is not installed in "
            "the active Python environment. Activate the project venv "
            "(source .venv/bin/activate) or run pip install -r requirements.txt."
        ) from exc
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise RuntimeError(f"output directory does not exist: {parent}")


def write_xlsx(shortlist: list[EnrichedTender], path: str) -> str:
    """Write the shortlist to an XLSX file. Returns the path."""
    check_xlsx_writable(path)
    frame = to_dataframe(shortlist)
    frame.to_excel(path, index=False, engine="openpyxl", sheet_name="shortlist")
    return path
