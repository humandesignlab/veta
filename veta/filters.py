"""Distributor filter profile (spec section 2.1 item 5).

The target partida especifica IDs (id_p_especifica) the medical-supplies and
pharmaceuticals distributor wants to include, plus captured categories to
exclude. These IDs feed the id_p_especifica array in the expedientes filter
payload for server-side category filtering.

Refine with the client after the first run. Descriptions are Spanish, as
returned by the API, left as-is.
"""

from veta import catalogs

# INCLUDE: honestly competitive, non-pharma categories bought in volume.
# (id_p_especifica, clave, descripcion)
INCLUDE_PARTIDAS = [
    (1, "21101", "Materiales y utiles de oficina"),
    (2, "21201", "Materiales y utiles de impresion y reproduccion"),
    (4, "21401", "Materiales y utiles consumibles para equipos informaticos"),
    (7, "21601", "Material de limpieza"),
    (39, "25401", "Materiales, accesorios y suministros medicos"),
    (40, "25501", "Materiales, accesorios y suministros de laboratorio"),
    (51, "27101", "Vestuario y uniformes"),
    (52, "27201", "Prendas de proteccion personal"),
    (55, "27501", "Blancos y otros productos textiles"),
    (59, "29101", "Herramientas menores"),
    (62, "29401", "Refacciones y accesorios para equipo de computo"),
    (63, "29501", "Refacciones de equipo e instrumental medico y de laboratorio"),
    (64, "29601", "Refacciones y accesorios menores de equipo de transporte"),
    (82, "31904", "Servicios integrales de infraestructura de computo"),
    (105, "33301", "Servicios de desarrollo de aplicaciones informaticas"),
    (108, "33304", "Servicio de mantenimiento de aplicaciones informaticas"),
    (119, "33801", "Servicios de vigilancia"),
    (133, "35101", "Mantenimiento y conservacion de inmuebles (admin)"),
    (134, "35102", "Mantenimiento y conservacion de inmuebles (servicios publicos)"),
    (136, "35301", "Mantenimiento y conservacion de bienes informaticos"),
    (137, "35401", "Instalacion, reparacion y mantenimiento de equipo medico"),
    (142, "35801", "Servicios de lavanderia, limpieza, higiene"),
    (206, "51501", "Bienes informaticos"),
    (213, "53101", "Equipo medico y de laboratorio"),
    (214, "53201", "Instrumental medico y de laboratorio"),
    (255, "59101", "Software"),
    (368, "59700", "Licencias informaticas e intelectuales"),
]

# EXCLUDE: captured / pharma categories.
EXCLUDE_PARTIDAS = [
    (38, "25301", "MEDICINAS Y PRODUCTOS FARMACEUTICOS"),
]

# Convenience: just the id_p_especifica integers for the API payload.
INCLUDE_PARTIDA_IDS = [pid for pid, _clave, _desc in INCLUDE_PARTIDAS]
EXCLUDE_PARTIDA_IDS = [pid for pid, _clave, _desc in EXCLUDE_PARTIDAS]

# The distributor's own RFC, for Layer 2 (distributor-relative positioning).
# When set, each tender's signal gains a position grade (INCUMBENT/EXPERIENCED/
# ADJACENT/OUTSIDER) and a win-probability band computed from this RFC's own
# contract history. When None, positioning is skipped and only the market
# signal (Layer 1) is shown, keeping client-agnostic commands unchanged.
CLIENT_RFC: str | None = None


# The distributor's default filter profile for the expedientes endpoint.
DEFAULT_PROFILE = {
    "id_ley": catalogs.ID_LEY_LAASSP,
    "id_p_especifica": INCLUDE_PARTIDA_IDS,
    "estatus_alterno": ["VIGENTE"],
    "id_proceso": catalogs.PROCESO_CONTRATACION,
}
