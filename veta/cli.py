"""CLI argument parsing and dispatch (spec section 3.7, step 7).

Commands:
    python run.py                      annotated shortlist with intelligence
    python run.py --brief LA-...       full bid brief for one tender
    python run.py --brief LA-... --download reports/anexos   brief + attachments
    python run.py --raw                unfiltered pull (all active tenders)
    python run.py --buyer IMSS         filter the shortlist by buyer siglas
    python run.py --output report.xlsx also write the shortlist to XLSX
    python run.py --sourcing 51501     supplier lookup for a partida
    python run.py --scan               adjacent opportunity scanner
    python run.py --calendar           procurement calendar (typical months)
    python run.py --build              (re)build the historical cache (step 1)
"""

from __future__ import annotations

import argparse

MONTHS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veta",
        description="Intelligence-annotated shortlist of Mexican federal tenders.",
    )
    parser.add_argument("--build", action="store_true", help="(re)build the historical cache")
    parser.add_argument("--raw", action="store_true", help="unfiltered pull of active tenders")
    parser.add_argument("--buyer", metavar="SIGLAS", help="filter shortlist by buyer siglas")
    parser.add_argument(
        "--output", metavar="FILE", nargs="?", const="",
        help="write the client report (Resumen + Detalle); omit FILE for reports/reporte-veta-{date}.xlsx",
    )
    parser.add_argument("--raw-output", metavar="FILE", dest="raw_output", help="write the raw single-sheet export (internal/debug)")
    parser.add_argument("--sourcing", metavar="CLAVE", help="supplier lookup for a partida clave")
    parser.add_argument("--brief", metavar="NUMERO", help="full bid brief for one tender (numero or uuid)")
    parser.add_argument("--download", metavar="DIR", help="with --brief, download attachments to DIR")
    parser.add_argument("--scan", action="store_true", help="adjacent opportunity scanner")
    parser.add_argument("--calendar", action="store_true", help="procurement calendar (typical months)")
    parser.add_argument("--limit", type=int, default=None, help="cap the number of rows shown")
    return parser


def _cmd_build() -> None:
    from veta import history

    history.main()


def _cmd_sourcing(clave: str, limit: int | None) -> None:
    from veta import sourcing

    suppliers = sourcing.suppliers_for_partida(clave, limit=limit or 20)
    if not suppliers:
        print(f"No historical suppliers found for partida {clave}.")
        return
    print(f"Historical federal suppliers for partida {clave} (2023-2025):\n")
    for s in suppliers:
        print(
            f"  {s.proveedor[:44]:44} {s.rfc:14} "
            f"{s.contract_count:>4} contracts  ${s.total_value:>16,.0f}  "
            f"{s.buyers_served} buyers"
        )
        if s.top_buyers:
            print(f"       top buyers: {', '.join(s.top_buyers)}")


def _cmd_brief(identifier: str, download: str | None) -> None:
    from veta import api, brief as brief_mod

    with api.ComprasMXClient() as client:
        brief = brief_mod.build_brief(client, identifier)
        if brief is None:
            print(f"No tender found for '{identifier}'.")
            return
        print(brief_mod.render_brief(brief))
        if download:
            paths = brief_mod.download_anexos(client, brief, download)
            print(f"\nDownloaded {len(paths)} attachment(s) to {download}:")
            for p in paths:
                print(f"  {p}")


def _cmd_scan(limit: int | None) -> None:
    from veta import scanner

    descriptions = scanner.load_clave_descriptions()
    adjacent = scanner.adjacent_opportunities(descriptions=descriptions, limit=limit or 20)
    print("Adjacent opportunities (partidas you are NOT targeting), by volume:\n")
    for r in adjacent.itertuples():
        print(
            f"  {r.partida}  {r.descripcion[:46]:46} "
            f"${r.total_value/1e6:>9,.0f}M MXN  "
            f"buyers={r.distinct_buyers:>3}  suppliers={r.distinct_suppliers:>5}  "
            f"new_entrant={r.avg_new_entrant_rate:.0%}  overlap={r.existing_buyer_overlap}"
        )


def _cmd_calendar() -> None:
    from veta import filters, history

    lookup = history.load_lookup()
    targeted = {clave for _pid, clave, _desc in filters.INCLUDE_PARTIDAS}
    subset = lookup[lookup["partida"].isin(targeted) & lookup["typical_month"].notna()]
    subset = subset.sort_values(["typical_month", "contract_count"], ascending=[True, False])
    print("Procurement calendar: typical publication month by buyer + partida\n")
    current_month = None
    for r in subset.itertuples():
        month = int(r.typical_month)
        if month != current_month:
            current_month = month
            print(f"\n{MONTHS[month]}:")
        print(f"  {r.siglas:16} partida {r.partida}  ({r.contract_count} historical contracts)")


def _cmd_raw(buyer: str | None, output: str | None) -> None:
    from veta import api

    filter_payload = api.build_filter(estatus_alterno=["VIGENTE"], id_proceso=0)
    with api.ComprasMXClient() as client:
        records = client.fetch_expedientes(filter_payload)
    if buyer:
        records = [r for r in records if (r.get("siglas") or "").upper() == buyer.upper()]
    print(f"{len(records)} active tenders" + (f" for {buyer}" if buyer else "") + ":\n")
    for r in records:
        print(
            f"  {r.get('numero_procedimiento', '?'):40} {(r.get('siglas') or '?'):12} "
            f"{(r.get('tipo_procedimiento') or '?')[:22]:22} apertura {r.get('fecha_apertura', '?')}"
        )
    if output:
        import pandas as pd

        pd.DataFrame(records).to_excel(output, index=False, engine="openpyxl")
        print(f"\nWrote {len(records)} rows to {output}")


def _cmd_shortlist(
    buyer: str | None,
    output: str | None,
    limit: int | None,
    raw_output: str | None = None,
) -> None:
    from veta import intelligence, output as out

    # `--output` with no filename falls back to a dated default in reports/.
    if output == "":
        output = out.default_report_path()

    # Fail fast before the multi-minute fetch if any XLSX target is unwritable.
    if output:
        out.check_xlsx_writable(output)
    if raw_output:
        out.check_xlsx_writable(raw_output)

    # Every tender is verified against its real line items and annotated with
    # its estimated value during enrichment (progress prints to stderr).
    shortlist = intelligence.enrich_live(progress=True)
    if buyer:
        shortlist = [t for t in shortlist if t.siglas.upper() == buyer.upper()]
    if limit is not None:
        shortlist = shortlist[:limit]
    print(out.render_console(shortlist))
    if output:
        out.write_client_xlsx(shortlist, output)
        print(f"\nWrote client report ({len(shortlist)} tenders) to {output}")
    if raw_output:
        out.write_raw_xlsx(shortlist, raw_output)
        print(f"Wrote raw export ({len(shortlist)} tenders) to {raw_output}")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    if args.build:
        _cmd_build()
    elif args.sourcing:
        _cmd_sourcing(args.sourcing, args.limit)
    elif args.brief:
        _cmd_brief(args.brief, args.download)
    elif args.scan:
        _cmd_scan(args.limit)
    elif args.calendar:
        _cmd_calendar()
    elif args.raw:
        _cmd_raw(args.buyer, args.output)
    else:
        _cmd_shortlist(args.buyer, args.output, args.limit, args.raw_output)
    return 0
