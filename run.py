"""Veta CLI entry point.

Usage:
    python run.py                      annotated shortlist with intelligence
    python run.py --raw                unfiltered pull (all active tenders)
    python run.py --buyer IMSS         filter by specific buyer
    python run.py --output report.xlsx write to file
    python run.py --sourcing 51501     supplier lookup for a partida
    python run.py --scan               adjacent opportunity scanner
    python run.py --calendar           procurement calendar (typical months)
"""

import sys

from veta.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
