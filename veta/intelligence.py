"""Buyer intelligence enrichment (spec section 3.3, step 3).

Joins the live filtered tenders with the historical buyer + partida lookup
(from veta.history) and attaches an intelligence card to each tender: buyer
openness, historical price band, recurrence, top competitors, typical timing,
and urgency.

The live listing does not carry each tender's partida, so tenders are fetched
one partida at a time (api.fetch_by_partida) and tagged with the partida that
matched. A tender that spans several target partidas gets one card per matched
partida that has history.

All data values are Spanish and left as-is. No em dashes in any output.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from veta import api, filters, history

# Urgency thresholds in days to the submission deadline (fecha_apertura).
URGENCY_RED_DAYS = 3
URGENCY_AMBER_DAYS = 7

# Openness threshold: a new-entrant rate at or above this reads as an open buyer.
OPEN_BUYER_RATE = 0.30


@dataclass
class BuyerIntel:
    """Historical intelligence for one buyer + partida combination."""

    siglas: str
    partida: str
    partida_desc: str
    has_history: bool
    contract_count: int = 0
    distinct_suppliers: int = 0
    new_entrant_rate: float | None = None
    price_min: float | None = None
    price_median: float | None = None
    price_max: float | None = None
    years_active: list[int] = field(default_factory=list)
    is_recurring: bool = False
    typical_month: int | None = None
    top_suppliers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_open_buyer(self) -> bool:
        return (
            self.new_entrant_rate is not None
            and self.new_entrant_rate >= OPEN_BUYER_RATE
        )


@dataclass
class Urgency:
    deadline: datetime.datetime | None
    days_to_deadline: int | None
    aclaraciones: datetime.datetime | None
    aclaraciones_passed: bool | None
    level: str  # RED, AMBER, GREEN, or UNKNOWN


@dataclass
class EnrichedTender:
    numero_procedimiento: str
    nombre_procedimiento: str
    siglas: str
    tipo_contratacion: str
    caracter: str
    estatus_alterno: str
    entidad_federativa: str
    unidad_compradora: str
    uuid_procedimiento: str
    matched_partidas: list[str]
    intel: list[BuyerIntel]
    urgency: Urgency
    signal: str

    @property
    def primary_intel(self) -> BuyerIntel | None:
        """The matched partida with the most historical data (headline card)."""
        if not self.intel:
            return None
        return max(self.intel, key=lambda b: b.contract_count)


def _partida_maps() -> tuple[dict[int, str], dict[str, str]]:
    """Return (id -> clave) and (clave -> descripcion) from the profile."""
    id_to_clave = {pid: clave for pid, clave, _desc in filters.INCLUDE_PARTIDAS}
    clave_to_desc = {clave: desc for _pid, clave, desc in filters.INCLUDE_PARTIDAS}
    return id_to_clave, clave_to_desc


def _lookup_index(lookup: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    """Index the historical lookup by (siglas, partida) for O(1) joins."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in lookup.to_dict("records"):
        index[(row["siglas"], row["partida"])] = row
    return index


def _parse_dt(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _build_intel(
    siglas: str,
    clave: str,
    clave_to_desc: dict[str, str],
    row: dict[str, Any] | None,
) -> BuyerIntel:
    desc = clave_to_desc.get(clave, "")
    if row is None:
        return BuyerIntel(siglas=siglas, partida=clave, partida_desc=desc, has_history=False)

    top = row.get("top_suppliers")
    top_list = json.loads(top) if isinstance(top, str) and top else []
    years = row.get("years_active")
    years_list = [int(y) for y in years] if years is not None else []
    tm = row.get("typical_month")

    return BuyerIntel(
        siglas=siglas,
        partida=clave,
        partida_desc=desc,
        has_history=True,
        contract_count=int(row.get("contract_count", 0) or 0),
        distinct_suppliers=int(row.get("distinct_suppliers", 0) or 0),
        new_entrant_rate=_as_float(row.get("new_entrant_rate")),
        price_min=_as_float(row.get("price_min")),
        price_median=_as_float(row.get("price_median")),
        price_max=_as_float(row.get("price_max")),
        years_active=years_list,
        is_recurring=bool(row.get("is_recurring", False)),
        typical_month=int(tm) if tm is not None and not pd.isna(tm) else None,
        top_suppliers=top_list,
    )


def _as_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_urgency(record: dict[str, Any], now: datetime.datetime) -> Urgency:
    deadline = _parse_dt(record.get("fecha_apertura"))
    aclaraciones = _parse_dt(record.get("fecha_aclaraciones"))
    days = (deadline.date() - now.date()).days if deadline else None
    acl_passed = aclaraciones < now if aclaraciones else None

    if days is None:
        level = "UNKNOWN"
    elif days < 0:
        level = "RED"
    elif days <= URGENCY_RED_DAYS or acl_passed:
        level = "RED"
    elif days <= URGENCY_AMBER_DAYS:
        level = "AMBER"
    else:
        level = "GREEN"
    return Urgency(deadline, days, aclaraciones, acl_passed, level)


def _signal(intel: BuyerIntel | None) -> str:
    """Plain-language signal derived from the primary buyer intelligence."""
    if intel is None or not intel.has_history:
        return "NO HISTORY: buyer or category not seen in 2023-2025 federal data"
    parts: list[str] = []
    if intel.is_open_buyer:
        parts.append(f"open buyer ({intel.new_entrant_rate:.0%} new entrants)")
    else:
        rate = intel.new_entrant_rate
        parts.append(f"lower openness ({rate:.0%} new entrants)" if rate is not None else "openness unknown")
    parts.append("recurring" if intel.is_recurring else "irregular")
    parts.append(f"{intel.distinct_suppliers} historical suppliers")

    strong = intel.is_open_buyer and intel.is_recurring
    grade = "STRONG" if strong else "MODERATE"
    return f"{grade}: " + ", ".join(parts)


def build_shortlist(
    records_by_partida: dict[int, list[dict[str, Any]]],
    lookup: pd.DataFrame,
    now: datetime.datetime | None = None,
) -> list[EnrichedTender]:
    """Join fetched tenders with the historical lookup and build cards.

    Pure function (no network). Only licitaciones publicas are kept.
    """
    now = now or datetime.datetime.now()
    id_to_clave, clave_to_desc = _partida_maps()
    index = _lookup_index(lookup)

    # Collect matched partidas per tender (keyed by numero_procedimiento).
    tenders: dict[str, dict[str, Any]] = {}
    matched: dict[str, list[str]] = {}
    for partida_id, records in records_by_partida.items():
        clave = id_to_clave.get(partida_id)
        if clave is None:
            continue
        for record in api.filter_licitaciones(records):
            key = record.get("numero_procedimiento") or record.get("uuid_procedimiento")
            if not key:
                continue
            tenders.setdefault(key, record)
            claves = matched.setdefault(key, [])
            if clave not in claves:
                claves.append(clave)

    shortlist: list[EnrichedTender] = []
    for key, record in tenders.items():
        siglas = (record.get("siglas") or "").strip()
        claves = matched[key]
        intel = [
            _build_intel(siglas, clave, clave_to_desc, index.get((siglas, clave)))
            for clave in claves
        ]
        # Headline card is the matched partida with the most history.
        primary = max(intel, key=lambda b: b.contract_count) if intel else None
        urgency = _compute_urgency(record, now)
        shortlist.append(
            EnrichedTender(
                numero_procedimiento=record.get("numero_procedimiento", ""),
                nombre_procedimiento=(record.get("nombre_procedimiento") or "").strip(),
                siglas=siglas,
                tipo_contratacion=record.get("tipo_contratacion", ""),
                caracter=record.get("caracter", ""),
                estatus_alterno=record.get("estatus_alterno", ""),
                entidad_federativa=record.get("entidad_federativa_contratacion", ""),
                unidad_compradora=record.get("unidad_compradora", ""),
                uuid_procedimiento=record.get("uuid_procedimiento", ""),
                matched_partidas=claves,
                intel=intel,
                urgency=urgency,
                signal=_signal(primary),
            )
        )

    # Sort: actionable first (urgency), then buyer openness, then history depth.
    urgency_rank = {"RED": 0, "AMBER": 1, "GREEN": 2, "UNKNOWN": 3}
    shortlist.sort(
        key=lambda t: (
            urgency_rank.get(t.urgency.level, 3),
            0 if (t.primary_intel and t.primary_intel.is_open_buyer) else 1,
            -(t.primary_intel.contract_count if t.primary_intel else 0),
        )
    )
    return shortlist


def enrich_live(
    partida_ids: list[int] | None = None,
    id_ley: int | None = 1,
    statuses: list[str] | None = None,
    client: api.ComprasMXClient | None = None,
) -> list[EnrichedTender]:
    """Fetch live tenders per partida and enrich them with buyer intelligence."""
    partida_ids = partida_ids or filters.INCLUDE_PARTIDA_IDS
    statuses = statuses or ["VIGENTE"]
    lookup = history.load_lookup()

    owns_client = client is None
    client = client or api.ComprasMXClient()
    try:
        records_by_partida = client.fetch_by_partida(
            partida_ids, estatus_alterno=statuses, id_ley=id_ley
        )
    finally:
        if owns_client:
            client.close()
    return build_shortlist(records_by_partida, lookup)
