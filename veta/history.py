"""Historical Contratos ingestion and aggregation (spec section 3.1, step 1).

Loads the Contratos CSVs (2023-2025), filters to federal LAASSP contracts,
and builds an aggregated lookup keyed by buyer (siglas) + partida especifica.
The result is saved as parquet for fast loading by the intelligence, sourcing,
and scanner modules.

The source CSVs are latin-1 (iso-8859-1) encoded, comma delimited, with 73
columns and multi-line quoted fields (handled by the pandas C parser). Data
values are in Spanish and left as-is; only code and comments are in English.

Public API:
  - download_contratos(years, overwrite) -> list[Path]
  - load_contratos(years) -> DataFrame (federal LAASSP, normalized columns)
  - build_buyer_partida_lookup(df) -> aggregated DataFrame
  - build(force_download) -> aggregated DataFrame (also writes parquet caches)
  - load_lookup() -> aggregated DataFrame (from parquet cache)

Aggregation columns per buyer + partida:
  contract_count, distinct_suppliers, new_entrant_rate, price_min,
  price_median, price_max, top_suppliers (JSON), years_active, is_recurring,
  typical_month.

Gate: lookup loads in under 2 seconds, covers at least 50,000 contracts.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pandas as pd

# Confirmed live host (verified 2026-07-18). The spec section 3.2 host
# funcionpublica.gob.mx is dead; use buengobierno.gob.mx instead.
CONTRATOS_URL_TEMPLATE = (
    "https://upcp-compranet.buengobierno.gob.mx/cnetassets/"
    "datos_abiertos_contratos_expedientes/Contratos_CompraNet{year}.csv"
)
CONTRATOS_YEARS = (2023, 2024, 2025)

CSV_ENCODING = "iso-8859-1"

DATA_DIR = Path("data/contratos")
AGG_DIR = Path("data/aggregated")
CONTRACTS_PARQUET = AGG_DIR / "contracts.parquet"
LOOKUP_PARQUET = AGG_DIR / "buyer_partida.parquet"

# Only federal (APF) LAASSP contracts are in scope for Veta.
ORDEN_GOBIERNO_FEDERAL = "APF"
LEY_LAASSP = "LAASSP"
CURRENCY_MXN = "MXN"
LICITACION_PUBLICA = "LICITACIÓN PÚBLICA"

# Raw CSV column name -> normalized (snake_case) name. Only the columns Veta
# needs are loaded. Note: the header has two "Moneda" columns; pandas keeps the
# first as "Moneda" (the currency for Importe DRC), which is the one we want.
COLUMN_MAP = {
    "Orden de gobierno": "orden_gobierno",
    "Ley": "ley",
    "Siglas de la Institución": "siglas",
    "Institución": "institucion",
    "Clave de la UC": "uc_clave",
    "Nombre de la UC": "uc_nombre",
    "Partida específica": "partida",
    "Tipo Procedimiento": "tipo_procedimiento",
    "Número de procedimiento": "numero_procedimiento",
    "Tipo de contratación": "tipo_contratacion",
    "Carácter del procedimiento": "caracter",
    "Fecha de publicación": "fecha_publicacion",
    "Fecha de fallo": "fecha_fallo",
    "Importe DRC": "importe",
    "Moneda": "moneda",
    "rfc": "rfc",
    "Proveedor o contratista": "proveedor",
    "Estratificación": "estratificacion",
}


def download_contratos(
    years: tuple[int, ...] = CONTRATOS_YEARS,
    dest_dir: Path = DATA_DIR,
    overwrite: bool = False,
) -> list[Path]:
    """Download the Contratos CSVs. Skips files that already exist."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with httpx.Client(timeout=httpx.Timeout(600.0)) as client:
        for year in years:
            path = dest_dir / f"Contratos_CompraNet{year}.csv"
            if path.exists() and not overwrite:
                paths.append(path)
                continue
            url = CONTRATOS_URL_TEMPLATE.format(year=year)
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(path, "wb") as fh:
                    for chunk in response.iter_bytes(chunk_size=1 << 20):
                        fh.write(chunk)
            paths.append(path)
    return paths


def _read_year(path: Path, year: int) -> pd.DataFrame:
    """Read one Contratos CSV, keeping only the columns Veta needs."""
    df = pd.read_csv(
        path,
        encoding=CSV_ENCODING,
        usecols=list(COLUMN_MAP),
        low_memory=False,
    )
    df = df.rename(columns=COLUMN_MAP)
    df["source_year"] = year
    return df


def load_contratos(
    years: tuple[int, ...] = CONTRATOS_YEARS,
    data_dir: Path = DATA_DIR,
) -> pd.DataFrame:
    """Load and normalize federal LAASSP contracts across the given years."""
    frames = [
        _read_year(data_dir / f"Contratos_CompraNet{year}.csv", year)
        for year in years
    ]
    df = pd.concat(frames, ignore_index=True)
    return _normalize(df)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to federal LAASSP and clean keys, dates, and amounts.

    About 20% of rows list several partidas in one field (for example
    "25301, 25401"). These bundled contracts are exploded into one row per
    partida so they join correctly against single-clave live tenders. As a
    result a bundled contract is counted once per partida it includes, and its
    amount contributes to the price band of each of those partidas.
    """
    df = df[
        (df["orden_gobierno"] == ORDEN_GOBIERNO_FEDERAL)
        & (df["ley"] == LEY_LAASSP)
    ].copy()

    for col in ("siglas", "partida", "rfc", "proveedor", "moneda"):
        df[col] = df[col].astype("string").str.strip()
    df["rfc"] = df["rfc"].str.upper()

    # Explode multi-partida rows into one row per partida clave.
    df["partida"] = df["partida"].str.split(",")
    df = df.explode("partida")
    df["partida"] = df["partida"].str.strip()

    df["fecha_publicacion"] = pd.to_datetime(
        df["fecha_publicacion"], errors="coerce"
    )
    df["fecha_fallo"] = pd.to_datetime(df["fecha_fallo"], errors="coerce")
    df["importe"] = pd.to_numeric(df["importe"], errors="coerce")
    df["is_licitacion"] = df["tipo_procedimiento"] == LICITACION_PUBLICA

    # Drop rows missing a usable buyer + partida key.
    df = df[df["siglas"].notna() & df["partida"].notna()]
    df = df[(df["siglas"] != "") & (df["partida"] != "") & (df["partida"] != "nan")]
    return df.reset_index(drop=True)


def _top_suppliers_json(group: pd.DataFrame, limit: int = 5) -> str:
    """Serialize the top suppliers of a buyer + partida group as JSON."""
    records = [
        {
            "proveedor": row.proveedor,
            "rfc": row.rfc,
            "count": int(row.count),
            "total": round(float(row.total), 2),
        }
        for row in group.head(limit).itertuples(index=False)
    ]
    return json.dumps(records, ensure_ascii=False)


def build_buyer_partida_lookup(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate contracts into a buyer (siglas) + partida lookup table.

    new_entrant_rate is the share of distinct suppliers at this buyer+partida
    whose first appearance was after the earliest year in the dataset. With a
    three-year window this is a proxy for buyer openness, not a lifetime
    first-contract measure. It is read alongside years_active and is_recurring
    for context.
    """
    key = ["siglas", "partida"]

    base = (
        df.groupby(key)
        .agg(
            contract_count=("rfc", "size"),
            distinct_suppliers=("rfc", "nunique"),
        )
        .reset_index()
    )

    # Price band on MXN contracts with a positive amount only.
    mxn = df[(df["moneda"] == CURRENCY_MXN) & (df["importe"] > 0)]
    price = (
        mxn.groupby(key)["importe"]
        .agg(price_min="min", price_median="median", price_max="max")
        .reset_index()
    )

    # Years active, recurrence, typical publication month.
    years = (
        df.groupby(key)["source_year"]
        .agg(lambda s: sorted(set(int(y) for y in s)))
        .reset_index(name="years_active")
    )
    years["is_recurring"] = years["years_active"].apply(lambda ys: len(ys) >= 2)

    months = df.assign(month=df["fecha_publicacion"].dt.month).dropna(
        subset=["month"]
    )
    typical_month = (
        months.groupby(key)["month"]
        .agg(lambda s: int(s.mode().iloc[0]) if not s.mode().empty else None)
        .reset_index(name="typical_month")
    )

    # New entrant rate.
    earliest_year = int(df["source_year"].min())
    first_year = (
        df.groupby(key + ["rfc"])["source_year"].min().reset_index()
    )
    first_year["is_new"] = first_year["source_year"] > earliest_year
    entrants = (
        first_year.groupby(key)
        .agg(distinct_sup=("rfc", "nunique"), new_sup=("is_new", "sum"))
        .reset_index()
    )
    entrants["new_entrant_rate"] = (
        entrants["new_sup"] / entrants["distinct_sup"]
    ).round(3)
    entrants = entrants[key + ["new_entrant_rate"]]

    # Top suppliers by total MXN value.
    supplier_totals = (
        mxn.groupby(key + ["rfc", "proveedor"])["importe"]
        .agg(count="size", total="sum")
        .reset_index()
        .sort_values(key + ["total"], ascending=[True, True, False])
    )
    top = (
        supplier_totals.groupby(key)[["proveedor", "rfc", "count", "total"]]
        .apply(_top_suppliers_json)
        .reset_index(name="top_suppliers")
    )

    lookup = base
    for part in (price, entrants, years, typical_month, top):
        lookup = lookup.merge(part, on=key, how="left")

    return lookup


def build(force_download: bool = False) -> pd.DataFrame:
    """Download if needed, load, aggregate, and cache to parquet."""
    download_contratos(overwrite=force_download)
    AGG_DIR.mkdir(parents=True, exist_ok=True)

    contracts = load_contratos()
    contracts.to_parquet(CONTRACTS_PARQUET, index=False)

    lookup = build_buyer_partida_lookup(contracts)
    lookup.to_parquet(LOOKUP_PARQUET, index=False)
    return lookup


def load_lookup() -> pd.DataFrame:
    """Load the cached buyer + partida lookup from parquet."""
    if not LOOKUP_PARQUET.exists():
        raise FileNotFoundError(
            f"{LOOKUP_PARQUET} not found. Run `python -m veta.history` first."
        )
    return pd.read_parquet(LOOKUP_PARQUET)


def load_contracts_cache() -> pd.DataFrame:
    """Load the cached normalized contracts from parquet."""
    if not CONTRACTS_PARQUET.exists():
        raise FileNotFoundError(
            f"{CONTRACTS_PARQUET} not found. Run `python -m veta.history` first."
        )
    return pd.read_parquet(CONTRACTS_PARQUET)


def main() -> None:
    """Build the historical cache and print a summary."""
    start = time.perf_counter()
    lookup = build()
    build_secs = time.perf_counter() - start

    contracts = load_contracts_cache()
    load_start = time.perf_counter()
    load_lookup()
    load_secs = time.perf_counter() - load_start

    print("Veta historical build complete")
    print(f"  contracts (federal LAASSP): {len(contracts):,}")
    print(f"  buyer+partida combinations: {len(lookup):,}")
    print(f"  distinct buyers:            {contracts['siglas'].nunique():,}")
    print(f"  distinct partidas:          {contracts['partida'].nunique():,}")
    print(f"  build time:                 {build_secs:.1f}s")
    print(f"  lookup load time:           {load_secs:.2f}s")


if __name__ == "__main__":
    main()
