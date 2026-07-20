# Veta

Veta is a Python CLI that produces an intelligence-annotated shortlist of
Mexican federal government tenders (ComprasMX) worth bidding on. It joins the
live tender feed with historical procurement data so a distributor can answer
one question per tender: is this worth a week of bid preparation, and why?

Phase 1 is a report/script, not an app. One client, one report.

## Requirements

- Python 3.11+
- Dependencies: `httpx`, `pandas`, `openpyxl`, `pyarrow`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For development and running the tests, install the dev extras and run pytest:

```bash
pip install -r requirements-dev.txt
pytest
```

## Getting started

Veta answers one question per tender: is this worth a week of bid preparation,
and why? Follow these steps in order.

### 1. Build the historical cache (once)

Before any intelligence works, build the local lookup. This downloads the
Contratos CSVs (one per year, 2023 to the current year) and aggregates them by
buyer and category. Do this once, then re-run it monthly to refresh:

```bash
python run.py --build             # aggregate; reuses CSVs already on disk
python run.py --build --refresh   # re-download fresh CSVs first, then aggregate
```

Use `--build` on its own to re-aggregate from the CSVs you already have (fast,
no download). Use `--build --refresh` to actually pull fresh data from the
portal, the single clean command when the freshness nudge says the cache is
getting old. The current-year file may not be published yet early in the year;
if so it is skipped with a notice and the build proceeds with the years present.

Every other command reads the cached lookup, so it is fast after this step.
Each run also prints a one-line freshness nudge so you know when the cache is
getting stale, for example:

```
Historical cache: built 2026-07-19, latest contract 2025-12-01 (230 days old)
```

### 2. Run your daily shortlist

```bash
python run.py
```

Every run verifies each tender against its real line items and looks up its
published estimated value (one request per tender), so a full pull takes a
couple of minutes; a progress counter prints while it works.

Each tender prints an intelligence card. Read it top to bottom:

- **Urgency (RED / AMBER / GREEN):** RED means the deadline is within 3 days, or
  the clarifications date already passed. Triage on this first.
- **SIGNAL line:** the headline verdict. `STRONG` means an open buyer in a
  recurring category. `NO HISTORY` means the buyer and category were not seen in
  the 2023-2025 data, so you would be bidding blind. `UNVERIFIED MATCH` means the
  category the listing filtered on does not actually appear in the tender.
- **New entrant rate / [OPEN BUYER]:** the key number. At or above 30 percent,
  outsiders actually win here; below that, it tends to be a closed shop.
- **Est. value / Median value:** what the buyer published for this tender (often
  "not published"), and what they have historically paid in the category.
- **Line items in this category:** how much of the tender is actually your
  category. `[minority line]` warns that the tender is mostly something else.
- **Top winners:** who you would be competing against.

### 3. A suggested first session

```bash
python run.py --build                 # grab a coffee, it is downloading
python run.py --limit 5               # read 5 cards, learn the format
python run.py --buyer IMSS --output imss.xlsx   # focus on one buyer, export
python run.py --scan                  # see where your blind spots are
```

## Usage

```bash
python run.py                      # annotated shortlist with intelligence
python run.py --raw                # unfiltered pull (all active tenders)
python run.py --buyer IMSS         # filter by specific buyer
python run.py --output             # write the client report to reports/reporte-veta-{date}.xlsx
python run.py --output report.xlsx # or to a specific path (Resumen + Detalle, Spanish)
python run.py --raw-output raw.xlsx # write the raw single-sheet export (internal/debug)
python run.py --sourcing 51501     # supplier lookup for a partida clave
python run.py --prospects          # ranked list of potential clients -> reports/prospectos-veta-{date}.xlsx
python run.py --prospects list.xlsx --all-sizes  # include GRANDE/NO MIPYME, custom path
python run.py --brief LA-07-...    # full bid brief for one tender (numero or uuid)
python run.py --brief LA-07-... --download reports/anexos  # brief + download attachments
python run.py --scan               # adjacent opportunity scanner
python run.py --calendar           # procurement calendar (typical months)
python run.py --build              # (re)build the historical cache from CSVs on disk
python run.py --build --refresh    # re-download fresh CSVs, then rebuild
python run.py --limit 5            # cap rows shown (combine with any command)
```

Note: live commands rate limit to 1 request per second against the portal (it
signs anti-bot headers under the hood), so they take a little time to run. That
is expected, not a hang.

### The client report (`--output`)

`--output` writes a share-ready, Spanish-language workbook with two sheets:

- **Resumen:** a one-page dashboard. Each tender is placed in an action bucket
  and the sheet is sorted by bucket, then by deadline:
  - **ACTUAR** (red): workable signal closing in <= 3 days.
  - **PREPARAR** (amber): workable signal closing in 4-14 days.
  - **MONITOREAR** (green): workable signal closing in > 14 days.
  - **DESCARTAR** (gray): weak, no-history, or unverified match, at any deadline.
  A colored summary bar at the top counts each bucket. Signal grades are shown
  in Spanish (FUERTE / MODERADA / DEBIL / SIN HISTORIAL / SIN VERIFICAR).
- **Detalle:** every field, one row per tender, for auditing a specific tender.

Use `--raw-output` for the old English single-sheet export (internal/debug).

### The prospect list (`--prospects`)

Who buys a daily tender-intelligence email? Companies that already compete for
federal contracts in the target categories. `--prospects` mines the historical
contracts cache for exactly those firms and ranks them by fit:

- **Active:** won a contract in the last two years (still bidding).
- **MIPYME by default:** MICRO / PEQUEÑA / MEDIANA - big enough to have budget,
  small enough to lack a bid-intelligence team. Use `--all-sizes` to include
  GRANDE / NO MIPYME.
- **Competitive:** has licitación (public-tender) wins, not only direct awards.
- **Engaged:** more than a one-off winner.

The fit score weights category breadth (a multi-category distributor gets more
value from a cross-category feed) and recency, with competitive participation
and buyer reach as supporting signals. Output is a single-sheet XLSX with the
company, RFC, size, win volume, categories, buyers, and score.

Contact note: ComprasMX does not publish supplier emails or contact people, so
the list identifies the **company** (RFC + name + profile). Enrich with contact
data (your provider, RUPC, or manual lookup) before wiring up the daily send.

## Project layout

```
veta/            source package (API client, history, intelligence, output)
data/            downloaded CSVs and precomputed lookups (gitignored)
catalogs/        saved catalog JSONs for reference (gitignored)
reports/         generated shortlist files (gitignored)
docs/            product spec and build prompt (seed files, gitignored)
run.py           main entry point
smoke_test.py    first filtered pull (validation script)
```

## Documentation

The build spec and product context live in `docs/` (local only). See
[AGENTS.md](AGENTS.md) for the operating guide and hard constraints.

## Constraints

- Public data only. No authentication, no insider channels.
- No web framework, no database. CLI script only.
- Code and comments in English. API data is Spanish and left as-is.
