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
veta/output.py        Console and file output formatters
veta/cli.py           CLI argument parsing
data/contratos/       Raw downloaded CSVs (gitignored)
data/aggregated/      Precomputed lookups (gitignored)
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
