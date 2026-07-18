"""CLI argument parsing (spec section 3.7, step 7).

Wires the subcommands and flags for run.py.

TODO(phase1): implement argparse and dispatch.
  Flags (spec section 3.7):
    (default)          annotated shortlist with intelligence
    --raw              unfiltered pull (all active tenders)
    --buyer IMSS       filter by specific buyer
    --output FILE      write to file (XLSX)
    --sourcing 51501   supplier lookup for a partida
    --scan             adjacent opportunity scanner
    --calendar         procurement calendar (typical months)
"""


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    # TODO(phase1): parse args and dispatch to the relevant module.
    raise NotImplementedError("Veta CLI not implemented yet")
