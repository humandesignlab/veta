"""Confirmed ComprasMX catalog IDs (spec section 2.1).

These are the hardcoded numeric IDs discovered from live network inspection.
They back the filter dropdowns in the ComprasMX SPA. Descriptions are in
Spanish (as returned by the API) and left as-is on purpose.
"""

# 1. leyes (governing law) -> filter field: id_ley
ID_LEY_LAASSP = 1  # Adquisiciones, Arrendamientos y Servicios
ID_LEY_LOPSRM = 2  # Obras Publicas y Servicios Relacionados
ID_LEY_LAPP = 21  # Asociaciones Publico Privadas
ID_LEY_CREDEX = 3  # Credito Externo
ID_LEY_CEEP = 4  # Contrataciones Entre Entes Publicos

# 2. tipocontratacion (what is being bought) -> filter: id_tipo_contratacion
TIPO_CONTRATACION_ADQUISICIONES = 1
TIPO_CONTRATACION_ARRENDAMIENTOS = 2
TIPO_CONTRATACION_SERVICIOS = 3

# 3. medioparticipacion (submission method) -> filter: id_forma_participacion
FORMA_PARTICIPACION_ELECTRONICA = 1
FORMA_PARTICIPACION_MIXTA = 2
FORMA_PARTICIPACION_PRESENCIAL = 3

# 4. entidadfederativa (states) -> filter: id_entidad_federativa
# Key IDs only; full catalog fetched at runtime when needed.
ENTIDAD_CDMX = 7
ENTIDAD_CHIHUAHUA = 6
ENTIDAD_JALISCO = 14
ENTIDAD_NUEVO_LEON = 19

# 6. caracter (national/international) -> filter: id_caracter_procedimiento
CARACTER_NACIONAL = 1
CARACTER_INTERNACIONAL_TRATADOS = 2
CARACTER_INTERNACIONAL_ABIERTO = 3

# 7. estatus (tender status) -> filter: estatus_alterno (string values)
# Authoritative full set from the API status catalog (GET_CAT_ESTATUS,
# catalogo="estatus"), verified 2026-07-20. The API groups them by "tab":
# tab 0 = open (still accepting bids), tab 1 = in progress (bids closed,
# under evaluation), tab 2 = concluded. These strings are used verbatim in the
# server-side estatus_alterno filter, so the accents are load-bearing: passing
# an unaccented value (for example "EN ATENCION DE PREGUNTAS") matches nothing.
# tab 0: open, still accepting bids. This is the shortlist universe.
ESTATUS_OPEN = [
    "VIGENTE",
    "EN ACLARACIONES",
    "EN ATENCIÓN DE PREGUNTAS",
    "EN REPREGUNTAS",
]
# tab 1: bids closed, procedure under way (no longer biddable).
ESTATUS_IN_PROGRESS = [
    "EN APERTURA",
    "PENDIENTE DE APERTURA",
    "EN EVALUACIÓN",
    "EN DECISIÓN DE FALLO",
    "SUSPENDIDO",
]
# tab 2: concluded.
ESTATUS_CONCLUDED = [
    "ADJUDICADO",
    "ADJUDICADO PARCIAL",
    "CANCELADO",
    "DESIERTO",
]

# id_proceso: 0 = Procedimiento de Contratacion (biddable), 1 = Proyecto de
# Convocatoria (early signal).
PROCESO_CONTRATACION = 0
PROCESO_CONVOCATORIA = 1
