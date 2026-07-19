"""Single-tender bid brief (detail endpoint feature).

Given one procedure number (or uuid), pulls the full detail and economic
requirements and renders a bid-preparation brief: buyer, timeline, procedure,
the bid economics that drive the working-capital and guarantee barriers, the
partidas in scope with any published amount band, and the attachments. It can
also download the attachments (convocatoria, anexo tecnico, etc.) to disk.

No em dashes in output. API data is Spanish and left as-is.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any

from veta import api

# Content types the download endpoint returns, mapped to file extensions.
_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

_UUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass
class TenderBrief:
    detail: dict[str, Any]
    partidas: list[dict[str, Any]]
    anexos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def numero(self) -> str:
        return self.detail.get("numero_procedimiento", "")

    @property
    def uuid(self) -> str:
        return self.detail.get("uuid_procedimiento", "")


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.0f} MXN"
    except (TypeError, ValueError):
        return "not published"


def _yn(value: Any) -> str:
    return "yes" if value in (1, "1", True) else "no"


def _dt(value: Any) -> str:
    if not value:
        return "not listed"
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _pct(value: Any) -> str | None:
    try:
        return f"{float(value):g}%"
    except (TypeError, ValueError):
        return None


def build_brief(client: api.ComprasMXClient, identifier: str) -> TenderBrief | None:
    """Resolve identifier (numero or uuid) and build the brief. None if missing."""
    identifier = identifier.strip()
    if _UUID_RE.match(identifier):
        uuid = identifier
    else:
        record = client.fetch_by_numero(identifier)
        if record is None:
            return None
        uuid = record.get("uuid_procedimiento", "")
    detail = client.fetch_detail(uuid)
    if detail is None:
        return None
    partidas = client.fetch_partidas(uuid)
    return TenderBrief(detail=detail, partidas=partidas, anexos=detail.get("anexos", []))


def _partida_block(partidas: list[dict[str, Any]]) -> list[str]:
    if not partidas:
        return ["  (no economic requirements published)"]
    lines: list[str] = []
    for p in partidas:
        clave = p.get("clave_p_especifica", "?")
        cucop = p.get("clave_cucop", "")
        desc = (p.get("descripcion_detallada") or p.get("descripcion_cucop") or "").strip()
        cant = p.get("cantidad_solicitada")
        unidad = p.get("unidad_medida", "")
        lines.append(f"  partida {clave} ({cucop}): {desc[:70]}")
        lines.append(f"    cantidad: {cant} {unidad}")
        mn, mx = p.get("monto_minimo"), p.get("monto_maximo")
        if mn is not None or mx is not None:
            lines.append(f"    monto band: {_money(mn)} to {_money(mx)}")
    return lines


def render_brief(brief: TenderBrief) -> str:
    d = brief.detail
    bar = "=" * 72

    def row(label: str, value: Any) -> str:
        return f"  {label:26} {value}"

    lines = [
        bar,
        brief.numero,
        (d.get("nombre_procedimiento") or "").strip(),
        bar,
        "BUYER",
        row("Dependencia:", d.get("nombre_dependencia", "")),
        row("Unidad compradora:", d.get("unidad_compradora", "")),
        row("Responsable:", d.get("responsable", "")),
        row("Correo:", d.get("email_uc", "")),
        row("Ramo:", d.get("ramo", "")),
        row("Entidad:", d.get("entidad_federativa_contratacion", "")),
        "",
        "TIMELINE",
        row("Publicacion:", _dt(d.get("fecha_publicacion"))),
        row("Junta aclaraciones:", _dt(d.get("fecha_junta_aclaracion"))),
        row("Limite aclaraciones:", _dt(d.get("fecha_limite_aclaracion"))),
        row("Visita instalaciones:", _dt(d.get("fecha_visita"))),
        row("Apertura proposiciones:", _dt(d.get("fecha_apertura"))),
        row("Acto de fallo:", _dt(d.get("fecha_acto_fallo"))),
        row("Inicio estimado:", _dt(d.get("fecha_estimada_contrato"))),
        "",
        "PROCEDURE",
        row("Tipo:", d.get("tipo_procedimiento", "")),
        row("Contratacion:", d.get("tipo_contratacion", "")),
        row("Caracter:", d.get("caracter", "")),
        row("Ley:", d.get("ley", "")),
        row("Participacion:", d.get("forma_participacion", "")),
        row("Criterio evaluacion:", d.get("nombre_criterio", "")),
    ]
    if d.get("total_punto_tecnico") is not None:
        lines.append(
            row("Puntos (tec/eco):", f"{d.get('total_punto_tecnico')}/{d.get('total_punto_economico')}")
        )

    lines += ["", "BID ECONOMICS (bid-prep barriers)"]
    anticipo = _yn(d.get("anticipo"))
    ant_pct = _pct(d.get("porcentaje_anticipo"))
    lines.append(row("Anticipo:", anticipo + (f" ({ant_pct})" if anticipo == "yes" and ant_pct else "")))
    gar = _yn(d.get("garantia_cumplimiento"))
    gar_pct = _pct(d.get("porcentaje_monto_proveedor"))
    lines.append(row("Garantia cumplimiento:", gar + (f" ({gar_pct})" if gar == "yes" and gar_pct else "")))
    lines.append(row("Garantia bien/servicio:", _yn(d.get("garantia_bien_servicio"))))
    lines.append(row("Otros seguros:", _yn(d.get("otros_seguros"))))
    if d.get("descripcion_otros_seguros"):
        lines.append(row("  seguro:", d.get("descripcion_otros_seguros")))
    lines.append(row("Contrato abierto:", _yn(d.get("contrato_abierto"))))
    lines.append(row("Plurianual:", _yn(d.get("plurianual"))))
    if d.get("forma_pago"):
        lines += ["", "FORMA DE PAGO", "  " + (d.get("forma_pago") or "").strip()]

    lines += ["", "PARTIDAS IN SCOPE"]
    lines += _partida_block(brief.partidas)

    desc = (d.get("descripcion") or "").strip()
    if desc:
        lines += ["", "DESCRIPTION", "  " + desc]

    lines += ["", f"ATTACHMENTS ({len(brief.anexos)})"]
    if brief.anexos:
        for a in brief.anexos:
            num = a.get("numero", "?")
            tipo = a.get("tipodoc_descripcion", "")
            adesc = a.get("descripcion", "")
            lines.append(f"  [{num}] {adesc} ({tipo})")
    else:
        lines.append("  (none)")
    lines.append(bar)
    return "\n".join(lines)


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", " ", name)
    name = re.sub(r"[\s_]+", "_", name.strip("_ "))
    return name[:120] or "documento"


def download_anexos(
    client: api.ComprasMXClient, brief: TenderBrief, dest_dir: str
) -> list[str]:
    """Download every attachment to dest_dir. Returns the written file paths."""
    import os

    os.makedirs(dest_dir, exist_ok=True)
    written: list[str] = []
    for a in brief.anexos:
        uuid_doc = a.get("uuid_documento")
        if not uuid_doc:
            continue
        content, ctype = client.download_document(str(uuid_doc).split(",")[0].strip())
        ext = _EXTENSIONS.get(ctype.split(";")[0].strip(), ".bin")
        base = f"{a.get('numero', 0):02d}_{a.get('descripcion', 'documento')}"
        path = os.path.join(dest_dir, _safe_filename(base) + ext)
        with open(path, "wb") as fh:
            fh.write(content)
        written.append(path)
    return written
