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
import sys
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from veta import api, filters, history

# Urgency thresholds in days to the submission deadline (fecha_apertura).
URGENCY_RED_DAYS = 3
URGENCY_AMBER_DAYS = 7

# Openness threshold: a new-entrant rate at or above this reads as an open buyer.
OPEN_BUYER_RATE = 0.30

# A STRONG signal additionally requires a median contract worth pursuing, so
# open+recurring categories of trivial value do not all read as STRONG.
STRONG_MEDIAN_THRESHOLD = 200_000


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
    price_p10: float | None = None
    price_median: float | None = None
    price_p90: float | None = None
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
    # Published estimated amount band from the reqeconomicos endpoint. Both are
    # None unless the buyer published amounts (rare for licitaciones publicas).
    monto_min: float | None = None
    monto_max: float | None = None
    line_partidas: int | None = None
    # How many of the tender's real line items fall in each matched partida,
    # filled by verify_and_enrich. Lets the card show whether a match is the
    # tender's main subject or a minority line (e.g. 2 medical items of 50).
    line_item_counts: dict[str, int] = field(default_factory=dict)

    @property
    def primary_intel(self) -> BuyerIntel | None:
        """The headline partida: most real line items, then most history.

        Ranking by actual line-item count first keeps a tender's dominant
        subject at the top, so a kitchen tender that also carries a couple of
        medical items does not lead with the medical category just because
        medical has more national history.
        """
        if not self.intel:
            return None
        return max(
            self.intel,
            key=lambda b: (self.line_item_counts.get(b.partida, 0), b.contract_count),
        )


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
        price_p10=_as_float(row.get("price_p10")),
        price_median=_as_float(row.get("price_median")),
        price_p90=_as_float(row.get("price_p90")),
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

    # A passed clarifications window is a risk signal, not a disqualifier: it
    # bumps GREEN down to AMBER rather than forcing RED.
    if days is None:
        level = "UNKNOWN"
    elif days <= URGENCY_RED_DAYS:      # includes already-passed deadlines
        level = "RED"
    elif days <= URGENCY_AMBER_DAYS or acl_passed:
        level = "AMBER"
    else:
        level = "GREEN"
    return Urgency(deadline, days, aclaraciones, acl_passed, level)


def _signal(intel: BuyerIntel | None) -> str:
    """Plain-language signal derived from the primary buyer intelligence.

    STRONG   open buyer AND recurring AND median contract >= threshold
    MODERATE open buyer OR recurring (but not all STRONG criteria)
    WEAK     neither open nor recurring
    """
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

    median_ok = (
        intel.price_median is not None
        and intel.price_median >= STRONG_MEDIAN_THRESHOLD
    )
    if intel.is_open_buyer and intel.is_recurring and median_ok:
        grade = "STRONG"
    elif intel.is_open_buyer or intel.is_recurring:
        grade = "MODERATE"
    else:
        grade = "WEAK"
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


def verify_and_enrich(
    shortlist: list[EnrichedTender],
    client: api.ComprasMXClient,
    progress: bool = False,
) -> None:
    """Verify each tender's partidas against its real line items and add monto.

    The listing filters by partida, but the API returns tenders whose primary
    subject is different (for example "articulos de cocina" surfacing under the
    medical-supplies filter). This calls fetch_partidas(uuid) once per tender to
    read the actual line items (clave_p_especifica) and:

    - keeps only the BuyerIntel whose partida truly appears in the line items,
      recomputing primary_intel and the signal from the verified set;
    - flags the signal "UNVERIFIED MATCH" when a tender that had matched
      intelligence turns out not to contain any of those partidas;
    - fills the estimated amount band (monto_minimo / monto_maximo).

    One signed request per tender (about 1 req/sec), so roughly two minutes for
    a full 118-tender shortlist. Mutates the tenders in place.
    """
    total = len(shortlist)
    for i, tender in enumerate(shortlist, 1):
        if progress:
            print(f"Enriching {i}/{total}...", end="\r", file=sys.stderr, flush=True)
        if not tender.uuid_procedimiento:
            continue
        items = client.fetch_partidas(tender.uuid_procedimiento)

        # Estimated amount band (summed across the tender's line items).
        tender.line_partidas = len(items) or None
        mins = [_as_float(it.get("monto_minimo")) for it in items]
        maxs = [_as_float(it.get("monto_maximo")) for it in items]
        mins = [m for m in mins if m is not None]
        maxs = [m for m in maxs if m is not None]
        tender.monto_min = sum(mins) if mins else None
        tender.monto_max = sum(maxs) if maxs else None

        # Verify matched partidas against the real line-item claves, counting
        # how many line items each clave has so the card can show its weight.
        clave_counts: dict[str, int] = {}
        for it in items:
            clave = it.get("clave_p_especifica")
            if clave:
                clave = str(clave).strip()
                clave_counts[clave] = clave_counts.get(clave, 0) + 1
        real_claves = set(clave_counts)

        original = tender.intel
        verified = [b for b in original if b.partida in real_claves]
        if verified:
            tender.intel = verified
            tender.matched_partidas = [b.partida for b in verified]
            tender.line_item_counts = {
                b.partida: clave_counts[b.partida] for b in verified
            }
            primary = tender.primary_intel
            tender.signal = _signal(primary)
        elif original:
            # The tender had matched intelligence, but none of those partidas
            # appear in its actual line items: the match is not trustworthy.
            tender.intel = []
            tender.signal = (
                "UNVERIFIED MATCH: tender line items do not include the "
                "filtered partida"
            )
        # If there was no matched intelligence to begin with, leave as-is.
    if progress and total:
        print(f"Enriched {total}/{total}.        ", file=sys.stderr, flush=True)


def enrich_live(
    partida_ids: list[int] | None = None,
    id_ley: int | None = 1,
    statuses: list[str] | None = None,
    client: api.ComprasMXClient | None = None,
    progress: bool = False,
) -> list[EnrichedTender]:
    """Fetch live tenders per partida and enrich them with buyer intelligence.

    Every shortlisted tender is verified against its real line items (dropping
    false partida matches) and annotated with its estimated amount band. This
    adds one request per tender, so a full run takes a couple of minutes; set
    progress=True to print a counter while it works.
    """
    partida_ids = partida_ids or filters.INCLUDE_PARTIDA_IDS
    statuses = statuses or ["VIGENTE"]
    lookup = history.load_lookup()

    owns_client = client is None
    client = client or api.ComprasMXClient()
    try:
        records_by_partida = client.fetch_by_partida(
            partida_ids, estatus_alterno=statuses, id_ley=id_ley
        )
        shortlist = build_shortlist(records_by_partida, lookup)
        verify_and_enrich(shortlist, client, progress=progress)
    finally:
        if owns_client:
            client.close()
    return shortlist
