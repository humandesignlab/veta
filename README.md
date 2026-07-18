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

## Usage

```bash
python run.py                      # annotated shortlist with intelligence
python run.py --raw                # unfiltered pull (all active tenders)
python run.py --buyer IMSS         # filter by specific buyer
python run.py --output report.xlsx # write to file
python run.py --sourcing 51501     # supplier lookup for a partida
python run.py --scan               # adjacent opportunity scanner
python run.py --calendar           # procurement calendar (typical months)
```

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
