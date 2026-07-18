"""Smoke test: first filtered pull from the ComprasMX API (spec section 3.2).

Validation script, not a unit test. Confirms the signed API client returns
real data:
  1. Call the expedientes endpoint with the distributor filters.
  2. Client-side filter for tipo_procedimiento == "LICITACION PUBLICA".
  3. Print total count and the first 10 results.

Gate: returns real data from the API.
"""

from __future__ import annotations

from veta import api, filters


def main() -> None:
    filter_payload = api.build_filter(
        id_ley=filters.DEFAULT_PROFILE["id_ley"],
        id_p_especifica=filters.INCLUDE_PARTIDA_IDS,
        estatus_alterno=["VIGENTE"],
        id_proceso=0,
    )

    with api.ComprasMXClient() as client:
        total = client.total_registros(filter_payload)
        print(f"API reports {total} matching tenders for the distributor filters")

        records = client.fetch_expedientes(filter_payload)
        licitaciones = api.filter_licitaciones(records)
        print(f"Fetched {len(records)} records, {len(licitaciones)} are licitaciones publicas")

        print("\nFirst 10 licitaciones publicas:")
        for record in licitaciones[:10]:
            print(
                f"  {record.get('numero_procedimiento', '?'):40}  "
                f"{(record.get('siglas') or '?'):10}  "
                f"{(record.get('estatus_alterno') or '?'):10}  "
                f"apertura {record.get('fecha_apertura', '?')}"
            )
            nombre = (record.get("nombre_procedimiento") or "").strip()
            if nombre:
                print(f"      {nombre[:90]}")


if __name__ == "__main__":
    main()
