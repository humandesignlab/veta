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
  - cache_status_line() -> one-line cache freshness summary (or None)

Aggregation columns per buyer + partida:
  contract_count, distinct_suppliers, new_entrant_rate, price_p10,
  price_median, price_p90, top_suppliers (JSON), years_active, is_recurring,
  typical_month, hhi, openness_shrunk, value_pctile, contestability_score,
  confidence, base_grade.

Gate: lookup loads in under 2 seconds, covers at least 50,000 contracts.
"""

from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

import httpx
import pandas as pd

from veta import filters

# Captured categories (for example pharma 25301) that must never contribute to
# the intelligence of any partida. Sourced from the distributor profile.
EXCLUDE_CLAVES = {clave for _pid, clave, _desc in filters.EXCLUDE_PARTIDAS}

# Confirmed live host (verified 2026-07-18). The spec section 3.2 host
# funcionpublica.gob.mx is dead; use buengobierno.gob.mx instead.
CONTRATOS_URL_TEMPLATE = (
    "https://upcp-compranet.buengobierno.gob.mx/cnetassets/"
    "datos_abiertos_contratos_expedientes/Contratos_CompraNet{year}.csv"
)
CONTRATOS_YEARS = (2023, 2024, 2025, 2026)

CSV_ENCODING = "iso-8859-1"

DATA_DIR = Path("data/contratos")
AGG_DIR = Path("data/aggregated")
CONTRACTS_PARQUET = AGG_DIR / "contracts.parquet"
LOOKUP_PARQUET = AGG_DIR / "buyer_partida.parquet"
CACHE_META = AGG_DIR / "cache_meta.json"

# Only federal (APF) LAASSP contracts are in scope for Veta.
ORDEN_GOBIERNO_FEDERAL = "APF"
LEY_LAASSP = "LAASSP"
CURRENCY_MXN = "MXN"
LICITACION_PUBLICA = "LICITACIÓN PÚBLICA"

# --- Layer 1 signal: market contestability grading (all tunable) ----------- #
# The contestability score blends shrunk openness, low concentration, and
# category-relative value. Openness dominates; value is a modifier.
OPENNESS_WEIGHT = 0.45
CONCENTRATION_WEIGHT = 0.35
VALUE_WEIGHT = 0.20

# Reliability gate: a cell needs this much evidence to earn the top grade.
MIN_CONTRACTS = 8
MIN_SUPPLIERS = 5

# Empirical-Bayes prior: fit a per-partida Beta(alpha, beta) from the spread of
# its cells' raw entrant rates. Below this many cells, or on a degenerate
# variance, fall back to a weak global prior instead.
MIN_CELLS_FOR_PRIOR = 20
GLOBAL_PRIOR = (1.0, 2.0)

# Grade cutoffs as quantiles of the contestability score over gate-passing
# cells, so grades stay meaningfully spread as the data shifts.
STRONG_QUANTILE = 0.80
MODERATE_QUANTILE = 0.50

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
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(path, "wb") as fh:
                        for chunk in response.iter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
            except httpx.HTTPStatusError as exc:
                # The current-year file may not be published yet; skip it rather
                # than fail the whole build.
                if exc.response.status_code == 404:
                    print(f"  skip {year}: not published yet (404)")
                    continue
                raise
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
    """Load and normalize federal LAASSP contracts across the given years.

    Years whose CSV is not on disk (for example a current year not yet
    published) are skipped so a partial set of files still builds.
    """
    frames = []
    for year in years:
        path = data_dir / f"Contratos_CompraNet{year}.csv"
        if not path.exists():
            continue
        frames.append(_read_year(path, year))
    if not frames:
        raise FileNotFoundError(
            f"No Contratos CSVs found in {data_dir}. Run `python run.py --build`."
        )
    df = pd.concat(frames, ignore_index=True)
    return _normalize(df)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to federal LAASSP and clean keys, dates, and amounts.

    About 20% of rows list several partidas in one field (for example
    "25301, 25401"). These bundled contracts are exploded into one row per
    partida so they join correctly against single-clave live tenders. As a
    result a bundled contract is counted once per partida it includes, and its
    amount contributes to the price band of each of those partidas.

    Any contract that bundles a captured category (EXCLUDE_CLAVES, for example
    pharma 25301) is dropped entirely before exploding. Otherwise the pharma
    winner and amount would leak into the non-pharma partida it was bundled
    with (for example showing AstraZeneca and a $6.7B contract under 25401
    medical supplies), which destroys trust in the non-pharma intelligence.
    """
    df = df[
        (df["orden_gobierno"] == ORDEN_GOBIERNO_FEDERAL)
        & (df["ley"] == LEY_LAASSP)
    ].copy()

    for col in ("siglas", "partida", "rfc", "proveedor", "moneda"):
        df[col] = df[col].astype("string").str.strip()
    df["rfc"] = df["rfc"].str.upper()

    # Split bundled partidas, then drop any contract touching an excluded
    # (captured) category so it cannot contaminate a non-excluded partida.
    df["partida"] = df["partida"].str.split(",")

    def _touches_excluded(claves: object) -> bool:
        if not isinstance(claves, list):
            return False
        return any((c or "").strip() in EXCLUDE_CLAVES for c in claves)

    df = df[~df["partida"].apply(_touches_excluded)]

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


def _fit_beta_prior(rates: pd.Series) -> tuple[float, float]:
    """Fit a Beta(alpha, beta) prior to a set of proportions by method of moments.

    Used per partida to shrink each cell's raw entrant rate toward the category
    norm. Falls back to a weak global prior when there are too few cells or the
    variance is degenerate (all rates equal, or mean at 0/1).
    """
    clean = rates.dropna()
    if len(clean) < MIN_CELLS_FOR_PRIOR:
        return GLOBAL_PRIOR
    mean = float(clean.mean())
    var = float(clean.var(ddof=0))
    # Near-zero variance (all rates ~equal) is degenerate; the epsilon guards
    # against floating-point noise producing an absurdly concentrated prior.
    if var <= 1e-9 or mean <= 0 or mean >= 1:
        return GLOBAL_PRIOR
    common = mean * (1 - mean) / var - 1
    if common <= 0:
        return GLOBAL_PRIOR
    alpha = max(mean * common, 1e-3)
    beta = max((1 - mean) * common, 1e-3)
    return alpha, beta


def _assign_grades(lookup: pd.DataFrame) -> pd.DataFrame:
    """Add confidence, base_grade columns from the reliability gate + quantiles.

    A cell needs MIN_CONTRACTS contracts and MIN_SUPPLIERS suppliers to be
    "high" confidence and thus eligible for STRONG. Grade cutoffs are quantiles
    of the contestability score computed over the gate-passing cells only, so
    thin cells cannot drag the thresholds around.
    """
    lookup = lookup.copy()
    reliable = (lookup["contract_count"] >= MIN_CONTRACTS) & (
        lookup["distinct_suppliers"] >= MIN_SUPPLIERS
    )
    lookup["confidence"] = reliable.map({True: "high", False: "low"})

    gated = lookup.loc[reliable, "contestability_score"].dropna()
    if gated.empty:
        strong_cut = moderate_cut = float("inf")
    else:
        strong_cut = float(gated.quantile(STRONG_QUANTILE))
        moderate_cut = float(gated.quantile(MODERATE_QUANTILE))

    def _grade(row: pd.Series) -> str:
        score = row["contestability_score"]
        if score is None or pd.isna(score):
            return "WEAK"
        if row["confidence"] == "high" and score >= strong_cut:
            return "STRONG"
        if score >= moderate_cut:
            return "MODERATE"
        return "WEAK"

    lookup["base_grade"] = lookup.apply(_grade, axis=1)
    lookup.attrs["strong_cut"] = strong_cut
    lookup.attrs["moderate_cut"] = moderate_cut
    return lookup


def repeat_win_rate_by_partida(df: pd.DataFrame) -> dict[str, float]:
    """Per-partida P(win same buyer+partida in year Y+1 | won in year Y).

    Pooled across buyers and consecutive year pairs. This is the incumbent base
    rate that Layer 2 positioning blends against. With only a 2023-2025 window
    this rests on two year-pairs, so it is thin; callers fall back to the global
    rate (key "__global__") for partidas with little data.
    """
    winners = df[["siglas", "partida", "rfc", "source_year"]].dropna()
    winners = winners.drop_duplicates()
    # Index of (siglas, partida, rfc, year) presence for O(1) "won next year".
    present = set(
        zip(
            winners["siglas"], winners["partida"], winners["rfc"],
            winners["source_year"].astype(int),
        )
    )

    from collections import defaultdict

    hits: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    global_hits = global_total = 0
    for siglas, partida, rfc, year in winners.itertuples(index=False):
        year = int(year)
        total[partida] += 1
        global_total += 1
        if (siglas, partida, rfc, year + 1) in present:
            hits[partida] += 1
            global_hits += 1

    rates: dict[str, float] = {
        p: hits[p] / total[p] for p in total if total[p] > 0
    }
    rates["__global__"] = (global_hits / global_total) if global_total else 0.0
    return rates


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

    # Price band on MXN contracts with a positive amount only. P10 and P90
    # percentiles are used instead of min/max so a single tiny purchase order
    # or a massive consolidated contract does not blow the band out to nine
    # orders of magnitude.
    mxn = df[(df["moneda"] == CURRENCY_MXN) & (df["importe"] > 0)]
    price = (
        mxn.groupby(key)["importe"]
        .agg(
            price_p10=lambda s: s.quantile(0.10),
            price_median="median",
            price_p90=lambda s: s.quantile(0.90),
        )
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

    # New entrant rate. Keep the raw counts (k = new_sup, m = distinct_sup) so
    # the Beta-Binomial shrinkage below can operate on them.
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
    entrants = entrants[key + ["distinct_sup", "new_sup", "new_entrant_rate"]]

    # Top suppliers by total MXN value, and the HHI concentration of award value
    # (sum of squared value shares). Low HHI = fragmented/contestable market.
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
    hhi = (
        supplier_totals.groupby(key)["total"]
        .agg(lambda s: float(((s / s.sum()) ** 2).sum()) if s.sum() > 0 else None)
        .reset_index(name="hhi")
    )

    lookup = base
    for part in (price, entrants, years, typical_month, top, hhi):
        lookup = lookup.merge(part, on=key, how="left")

    # --- Layer 1 contestability score + grade ------------------------------ #
    # Openness with per-partida empirical-Bayes shrinkage: thin cells collapse
    # to their category norm, rich cells keep their own rate.
    lookup["distinct_sup"] = lookup["distinct_sup"].fillna(0)
    lookup["new_sup"] = lookup["new_sup"].fillna(0)
    shrunk = []
    for _partida, grp in lookup.groupby("partida"):
        alpha, beta = _fit_beta_prior(grp["new_entrant_rate"])
        s = (grp["new_sup"] + alpha) / (grp["distinct_sup"] + alpha + beta)
        shrunk.append(s)
    lookup["openness_shrunk"] = pd.concat(shrunk).round(4)

    # Category-relative value: percentile rank of the median contract within its
    # partida (neutral 0.5 when the cell has no priced contracts).
    lookup["value_pctile"] = (
        lookup.groupby("partida")["price_median"].rank(pct=True).fillna(0.5)
    )

    lookup["contestability_score"] = (
        OPENNESS_WEIGHT * lookup["openness_shrunk"]
        + CONCENTRATION_WEIGHT * (1 - lookup["hhi"].fillna(1.0))
        + VALUE_WEIGHT * lookup["value_pctile"]
    ).round(4)

    lookup = _assign_grades(lookup)
    return lookup


def build(force_download: bool = False) -> pd.DataFrame:
    """Download if needed, load, aggregate, and cache to parquet."""
    download_contratos(overwrite=force_download)
    AGG_DIR.mkdir(parents=True, exist_ok=True)

    contracts = load_contratos()
    contracts.to_parquet(CONTRACTS_PARQUET, index=False)

    lookup = build_buyer_partida_lookup(contracts)
    lookup.to_parquet(LOOKUP_PARQUET, index=False)

    repeat_rates = repeat_win_rate_by_partida(contracts)
    _write_cache_meta(contracts, lookup, repeat_rates)
    return lookup


def _latest_contract_date(contracts: pd.DataFrame) -> datetime.date | None:
    """Most recent contract date in the cache (award date, else publication)."""
    dates = pd.concat([contracts["fecha_fallo"], contracts["fecha_publicacion"]])
    latest = dates.max()
    return latest.date() if pd.notna(latest) else None


def _write_cache_meta(
    contracts: pd.DataFrame,
    lookup: pd.DataFrame | None = None,
    repeat_rates: dict[str, float] | None = None,
) -> None:
    """Record cache freshness plus the Layer 1 grading and Layer 2 base rates.

    Beyond the build date and newest contract, this persists the quantile
    cutoffs and grade distribution (so a grade is auditable) and the per-partida
    repeat-win rates the positioning layer blends against.
    """
    latest = _latest_contract_date(contracts)
    meta: dict = {
        "built": datetime.date.today().isoformat(),
        "latest_contract": latest.isoformat() if latest else None,
    }
    if lookup is not None:
        meta["grading"] = {
            "strong_cut": lookup.attrs.get("strong_cut"),
            "moderate_cut": lookup.attrs.get("moderate_cut"),
            "weights": {
                "openness": OPENNESS_WEIGHT,
                "concentration": CONCENTRATION_WEIGHT,
                "value": VALUE_WEIGHT,
            },
            "gate": {"min_contracts": MIN_CONTRACTS, "min_suppliers": MIN_SUPPLIERS},
            "distribution": {
                k: int(v)
                for k, v in lookup["base_grade"].value_counts().items()
            },
        }
    if repeat_rates is not None:
        meta["repeat_win_rate"] = {k: round(v, 4) for k, v in repeat_rates.items()}
    CACHE_META.write_text(json.dumps(meta), encoding="utf-8")


def cache_status_line() -> str | None:
    """One-line freshness summary for the historical cache, or None if absent.

    Example: "Historical cache: built 2026-07-19, latest contract 2026-06-28
    (21 days old)". Falls back to the parquet mtime and a lightweight date-only
    read of the contracts cache when the metadata sidecar predates this feature.
    """
    if not LOOKUP_PARQUET.exists():
        return None

    built: str | None = None
    latest: str | None = None
    if CACHE_META.exists():
        try:
            meta = json.loads(CACHE_META.read_text(encoding="utf-8"))
            built = meta.get("built")
            latest = meta.get("latest_contract")
        except (ValueError, OSError):
            pass

    if built is None:
        mtime = datetime.date.fromtimestamp(LOOKUP_PARQUET.stat().st_mtime)
        built = mtime.isoformat()
    if latest is None and CONTRACTS_PARQUET.exists():
        try:
            cols = pd.read_parquet(
                CONTRACTS_PARQUET, columns=["fecha_fallo", "fecha_publicacion"]
            )
            d = _latest_contract_date(cols)
            latest = d.isoformat() if d else None
        except (OSError, KeyError, ValueError):
            pass

    line = f"Historical cache: built {built}"
    if latest:
        age = (datetime.date.today() - datetime.date.fromisoformat(latest)).days
        line += f", latest contract {latest} ({age} days old)"
    else:
        line += ", latest contract unknown"
    return line


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


def load_repeat_win_rate() -> dict[str, float]:
    """Load per-partida repeat-win rates from cache metadata (may be empty).

    Includes a "__global__" key used as a fallback for partidas with too little
    data. Returns {} when the metadata predates this feature.
    """
    if not CACHE_META.exists():
        return {}
    try:
        meta = json.loads(CACHE_META.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return meta.get("repeat_win_rate", {})


def main(force_download: bool = False) -> None:
    """Build the historical cache and print a summary.

    When force_download is set, the source CSVs are re-fetched even if they are
    already on disk (the `--build --refresh` path).
    """
    start = time.perf_counter()
    lookup = build(force_download=force_download)
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
