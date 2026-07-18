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
- No auth. All endpoints POST with `Content-Type: application/json`.
- Listing: `POST /expedientes?rows=100&page={n}`, paginate all pages.
- Active tenders: send `estatus_alterno: ["VIGENTE"]`.
- Category filter: `id_p_especifica` array (IDs from the `clave` catalog).
- No server-side filter for procedure type. Filter client-side for
  `tipo_procedimiento == "LICITACION PUBLICA"` (or `numero_procedimiento`
  starting with "LA-").
- Use `id_ley: 1` (LAASSP), `id_tipo_contratacion` 1 (ADQUISICIONES) and 3
  (SERVICIOS).

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
veta/api.py           ComprasMX API client
veta/filters.py       Distributor filter profile (INCLUDE/EXCLUDE partida IDs)
veta/catalogs.py      Confirmed catalog ID constants
veta/history.py       Historical CSV ingestion and aggregation
veta/intelligence.py  Buyer intelligence enrichment
veta/sourcing.py      Supplier sourcing reverse lookup
veta/scanner.py       Adjacent opportunity scanner
veta/output.py        Console and file output formatters
veta/cli.py           CLI argument parsing
data/contratos/       Raw downloaded CSVs (gitignored)
data/aggregated/      Precomputed lookups (gitignored)
catalogs/             Saved catalog JSONs (gitignored)
reports/              Generated shortlist files (gitignored)
```

## Known open items to resolve during build

- Historical CSV host differs between the prompt
  (`upcp-compranet.buengobierno.gob.mx`) and spec section 3.2
  (`upcp-compranet.funcionpublica.gob.mx`). Confirm which is live.
- Tender detail endpoint (spec section 2.3) is not yet captured. Discover it
  to get monto and CUCOP; fall back to keyword matching if unavailable.
