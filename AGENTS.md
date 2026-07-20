# AGENTS.md - Veta operating guide

Context for any agent working in this repo. The single source of truth is
[docs/veta-phase1-spec.md](docs/veta-phase1-spec.md). Read it fully before
writing code. The build prompt is
[docs/veta-cursor-prompt.md](docs/veta-cursor-prompt.md). These seed files live
in `docs/` and are gitignored (local use only).

## What Veta is

A Python CLI that produces an intelligence-annotated shortlist of Mexican
federal government tenders (ComprasMX) worth bidding on. It joins the live
tender feed with historical Contratos data so a distributor can answer one
question per tender: "is this worth a week of bid preparation, and why?"

Phase 1 is a report/script, not an app. One client, one report.

## Hard rules (do not violate)

- Python 3.11+. Dependencies: `httpx`, `pandas`, `openpyxl` only.
- No web framework, no database, no auth. CLI script only.
- NO EM DASHES in any code, comment, output, or generated text. Use commas,
  parentheses, or periods instead.
- All code and comments in English. Data from the API is in Spanish; leave it
  as-is, do not translate.
- Public data only. No insider or authenticated channels.
- Rate limit API calls to 1 req/sec. Retry with backoff on failure.
- Plan before build. Build incrementally. Honest framing over hype.

## API quick reference (see spec section 2 for full detail)

- Base URL: `https://upcp-cnetservicios.buengobierno.gob.mx/whitney/sitiopublico`
- Endpoints are open (no login) but require signed headers. A bare request now
  returns 401. See "Request signing" below. The listing and catalogs are POST
  with `Content-Type: application/json`; the detail and reqeconomicos endpoints
  are GET. Every request (POST or GET) needs fresh signed headers.
- Listing: `POST /expedientes?rows=100&page={n}`, paginate all pages.
- Detail (GET): `GET /expedientes/{uuid}?id_proceso=procedimiento`, action
  `GET_DETALLE_PROCEDIMIENTO`. Full buyer/date/guarantee fields plus `anexos`.
- Partidas + monto (GET): `GET /expedientes/{uuid}/reqeconomicos?id_proceso=procedimiento&rows=50&page=1&grupo=1`,
  action `GET_REQECONOMICOS`. Only source of a live tender's partida
  (`clave_p_especifica`) and estimated value band (`monto_minimo` /
  `monto_maximo`). Both GET endpoints take `id_proceso=procedimiento` (the SPA
  route literal); passing `0` returns 400.
- Attachment download (GET): `GET {qr_documentosUrl}?id_documento={uuid_documento}&user=sitiopublico`,
  action `DOWNLOAD_FILE`, where qr_documentosUrl is
  `https://upcp-cnetservicios.buengobierno.gob.mx/norah/documentos/recursos/ulck`.
  Returns the raw file (usually application/pdf). The `uuid_documento` comes
  from a detail record's `anexos`.
- Active tenders: send `estatus_alterno: ["VIGENTE"]`.
- Category filter: `id_p_especifica` array (IDs from the `clave` catalog).
- No server-side filter for procedure type. Filter client-side for
  `tipo_procedimiento == "LICITACION PUBLICA"` (or `numero_procedimiento`
  starting with "LA-").
- Use `id_ley: 1` (LAASSP), `id_tipo_contratacion` 1 (ADQUISICIONES) and 3
  (SERVICIOS).

## Request signing (reverse-engineered, not in the original spec)

The spec claimed "no auth required". That is no longer true: the portal added
an anti-bot layer. Every request needs three headers, generated per request
and single-use (a captured token cannot be replayed):

- `grc`  = base64(RSA_PKCS1v1_5(publicKey, base64(payload)))
- `igrc` = client ip (default "127.0.0.1")
- `xgrc` = random 40-char nonce, also embedded inside the payload

payload = comma-joined `[siteKey, ip, dateTime, xgrc, origin, pathname, action]`,
where dateTime is the server clock (from `/adele/interoperabilidad/tp/reloj`)
formatted `yyyyMMddHHmmss` at America/Mexico_City (UTC-6), and action is e.g.
`GET_PROCEDIMIENTOS`. reCAPTCHA v3 loads on the page but is NOT part of the
listing payload. Implemented in `veta/auth.py` using only stdlib crypto (no new
dependency). If the portal rotates the RSA public key or changes the payload,
update `veta/auth.py`.

## Build order (spec section 3)

1. `history.py` - ingest Contratos CSVs (2023-2025), aggregate by buyer +
   partida. Save lookup as parquet/pickle.
2. `api.py` + `smoke_test.py` - ComprasMX client, first filtered pull.
3. `intelligence.py` - join tenders with historical lookup, attach buyer card.
4. `sourcing.py` - reverse lookup: given a partida, list historical suppliers.
5. `output.py` - console intelligence cards + XLSX export.
6. `scanner.py` - adjacent opportunity scanner.
7. `cli.py` / `run.py` - CLI entry point.

## Module map (spec section 4)

```
veta/api.py           ComprasMX API client (pagination, rate limit, retry)
veta/auth.py          Request signing (grc/igrc/xgrc token generation)
veta/filters.py       Distributor filter profile (INCLUDE/EXCLUDE partida IDs)
veta/catalogs.py      Confirmed catalog ID constants
veta/history.py       Historical CSV ingestion and aggregation
veta/intelligence.py  Buyer intelligence enrichment
veta/sourcing.py      Supplier sourcing reverse lookup
veta/brief.py         Single-tender bid brief + attachment download
veta/scanner.py       Adjacent opportunity scanner
veta/output.py        Console cards, client XLSX report, raw XLSX export
veta/cli.py           CLI argument parsing
data/contratos/       Raw downloaded CSVs (gitignored)
data/aggregated/      Precomputed lookups + cache_meta.json (gitignored)
catalogs/             Saved catalog JSONs (gitignored)
reports/              Generated shortlist files (gitignored)
```

## Resolved

- Historical CSV host: use `upcp-compranet.buengobierno.gob.mx` (the prompt's
  host). The spec section 3.2 host `upcp-compranet.funcionpublica.gob.mx` is
  dead (does not resolve). Confirmed URL pattern (verified 2026-07-18, all
  three years return 200 OK):
  `https://upcp-compranet.buengobierno.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet{YEAR}.csv`
  Sizes: 2023 ~188 MB, 2024 ~168 MB, 2025 ~110 MB.

- Tender partida join: the listing response does not carry a tender's partida,
  so tenders are fetched one partida at a time (`api.fetch_by_partida`) and
  tagged with the matching partida. This is how `intelligence.py` joins to the
  historical buyer + partida lookup, no detail endpoint required.

- Data quality (history): two rules keep the intelligence honest.
  (1) Any contract bundling an EXCLUDE clave (pharma 25301) is dropped entirely
  in `_normalize` before the multi-partida explode, so a captured-market winner
  and amount cannot leak into a non-pharma partida.
  (2) The price band uses P10/P90 percentiles (`price_p10` / `price_p90`), not
  min/max, so outliers do not blow the band out to nine orders of magnitude.
  The STRONG signal also requires `price_median >= 200,000` MXN, and a passed
  clarifications window bumps urgency to AMBER rather than RED.

- Tender detail endpoint: earlier 400s were caused by `id_proceso=0`. The SPA
  actually sends `id_proceso=procedimiento` (the route segment). With that,
  `api.fetch_detail(uuid)` and `api.fetch_partidas(uuid)` both return 200.
  Implemented and verified 2026-07-18.

- Partida verification + monto (default): `intelligence.verify_and_enrich`
  calls `api.fetch_partidas(uuid)` once per shortlisted tender to read the real
  line items. The listing filters by partida but returns tenders whose actual
  subject differs (for example kitchen articles surfacing under the medical
  filter), so each tender's intel is filtered to the partidas that truly appear
  in its line items; a tender with matched intel but no matching line item is
  flagged "UNVERIFIED MATCH". The same call fills the estimated amount band
  (`monto_minimo` / `monto_maximo`, summed across line items). This runs on
  every shortlist (about 1 req/sec per tender, roughly two minutes for ~118
  tenders; progress prints to stderr). Most licitaciones publicas leave the
  amount null (card shows "not published by buyer"); where null, the historical
  price band stays the working-capital proxy.

- Client report (`--output`): `output.write_client_xlsx` builds a two-sheet,
  Spanish workbook for the commercial director. Sheet "Resumen" is a scannable
  dashboard (colored summary bar + one row per tender, sorted by action bucket
  then deadline); sheet "Detalle" is the full data with Spanish headers.
  `intelligence.assign_bucket` maps each tender to ACTUAR / PREPARAR /
  MONITOREAR / DESCARTAR: a weak/no-history/unverified signal is DESCARTAR at
  any deadline, otherwise it buckets by days to close (<=3, 4-14, >14).
  `intelligence.signal_grade` parses the grade token; `output.SIGNAL_ES` maps it
  to Spanish (FUERTE / MODERADA / DEBIL / SIN HISTORIAL / SIN VERIFICAR). The old
  English single-sheet export is still available as `write_raw_xlsx`
  (`--raw-output`).

- Cache freshness nudge: `build` writes `data/aggregated/cache_meta.json`
  (`built` date + `latest_contract` date). Every non-build run prints
  `history.cache_status_line()` to stderr, e.g. "Historical cache: built
  2026-07-19, latest contract 2025-12-01 (230 days old)". If the metadata
  sidecar is missing (cache built before this feature) it falls back to the
  parquet mtime and a two-column read of the contracts cache. The historical
  CSVs update ~daily with retroactive edits, but the aggregates are slow-moving,
  so a monthly rebuild is enough. `CONTRATOS_YEARS` includes the current year;
  `--build` re-aggregates from CSVs already on disk (no download), while
  `--build --refresh` (`history.main(force_download=True)`) re-downloads them
  first, the single command for genuinely fresh data. `download_contratos`
  skips a year that 404s (current-year file not published yet) and
  `load_contratos` skips any year whose CSV is absent, so a partial set still
  builds. Add the next year to `CONTRATOS_YEARS` each January.

- Tender statuses (estatus_alterno): the authoritative full set comes from the
  API status catalog, `client.fetch_catalog("estatus", action="GET_CAT_ESTATUS")`
  (verified 2026-07-20, 13 values). The API groups them by a `tab` field:
  tab 0 open/biddable (`VIGENTE`, `EN ACLARACIONES`, `EN ATENCIÓN DE PREGUNTAS`,
  `EN REPREGUNTAS`), tab 1 in progress/bids closed (`EN APERTURA`,
  `PENDIENTE DE APERTURA`, `EN EVALUACIÓN`, `EN DECISIÓN DE FALLO`, `SUSPENDIDO`),
  tab 2 concluded (`ADJUDICADO`, `ADJUDICADO PARCIAL`, `CANCELADO`, `DESIERTO`).
  These are mirrored in `catalogs.ESTATUS_OPEN / ESTATUS_IN_PROGRESS /
  ESTATUS_CONCLUDED`. IMPORTANT: `estatus_alterno` is a server-side string
  filter, so the accents are load-bearing; an unaccented value (for example
  "EN ATENCION DE PREGUNTAS") matches zero records. The public listing only
  surfaces tab-0 procedures (concluded ones drop off and live only in the CSVs),
  so `ESTATUS_OPEN` is the correct universe for the live shortlist. NOTE: the
  shortlist currently still queries `["VIGENTE"]` only (see intelligence.py
  `enrich_live` and filters.DEFAULT_PROFILE); widening it to `ESTATUS_OPEN` so
  clarification-phase tenders are not dropped is the agreed next change.

## Fallback: if the Whitney API breaks

The live tender feed depends on the ComprasMX Whitney API at
upcp-cnetservicios.buengobierno.gob.mx. This API already hardened once
(added RSA request signing after the spec was written). If it hardens
again or goes offline, here are the fallback paths in order of preference.

The intelligence layer (intelligence.py, history.py, sourcing.py, scanner.py)
does not depend on the API. It takes dicts and DataFrames. Only api.py and
auth.py touch the live endpoint. Any fallback only needs to produce the same
list[dict] that api.fetch_expedientes returns.

### Fallback 1: OCDS API (datos.gob.mx)

Endpoint: https://api.datos.gob.mx/v2/contratacionesabiertas
Format: JSON, Open Contracting Data Standard (EDCA)
Auth: none (public datos.gob.mx infrastructure, separate team)
Coverage: federal procurement 2017 to present
Docs: https://www.transparenciapresupuestaria.gob.mx/work/models/PTP/programas/OpenDataDay/Resultados/Guia%20_uso_API_contrataciones%20_abiertas.pdf

Identified during Phase 0 recon but not used because Whitney was cleaner.
Supports pagination (pageSize, page), entity name filtering, and returns
tender status, dates, and classification. Test first: confirm it still
returns current 2026 data and that the CUCOP/partida classification field
is populated.

To implement: write a new data source module (e.g. veta/ocds.py) that
queries this API and normalizes the response into the same record format
intelligence.py expects. Swap api.py calls in cli.py.

### Fallback 2: Expedientes bulk CSV

URL pattern:
https://upcp-compranet.buengobierno.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Expedientes_PICompraNet{YEAR}.csv

The 2025 file exists and contains active-system data with partida codes
and ComprasMX detail URLs. A 2026 file may appear during the year. These
CSVs include a status field and publication/opening dates, so filtering
for open tenders is possible.

Trade-off: not real-time. The file updates periodically (nominally daily
per the metadata, but in practice weekly or irregular). The shortlist
would be days stale instead of live. The intelligence layer still works
identically; only freshness degrades.

To implement: extend history.py to also ingest Expedientes CSVs, filter
for status = VIGENTE and fecha_apertura > today, and feed the results
into intelligence.build_shortlist.

### Fallback 3: Browser automation

Use Playwright or Claude-in-Chrome to navigate
comprasmx.buengobierno.gob.mx/sitiopublico/#/, apply filters, and scrape
the rendered table. The portal always works for human users; automation
just needs to look human enough.

Trade-off: fragile (breaks on any DOM change), slow (page rendering +
rate limits), and harder to maintain. Last resort only.

### What to do when the API returns 401

1. Check if the RSA public key rotated: open the SPA source in DevTools,
   search for the key in the app config or environment, compare with
   PUBLIC_KEY_PEM in auth.py. If different, update and retry.
2. Check if the payload format changed: compare the SPA's request headers
   in DevTools Network tab against what auth.py generates. Look for new
   fields, changed action names, or a reCAPTCHA token requirement.
3. Check if the clock endpoint moved: the signing requires server time
   from /adele/interoperabilidad/tp/reloj. If that 404s, the clock sync
   breaks and every signature is invalid.
4. If none of the above: try Fallback 1 (OCDS API) while debugging.
