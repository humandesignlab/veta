"""Historical Contratos ingestion and aggregation (spec section 3.1, step 1).

Loads the Contratos CSVs (2023-2025), parses numero_procedimiento to extract
the buying institution (siglas) and procedure type, then builds aggregated
lookup tables keyed by buyer + partida. Saves the result as parquet for fast
loading.

TODO(phase1): implement ingestion + aggregation.
  - resolve the live CSV host (see AGENTS.md open items)
  - load_contratos(years) -> pandas.DataFrame
  - build_buyer_partida_lookup(df) -> DataFrame with:
      contract_count, distinct_suppliers, new_entrant_rate,
      price_min/median/max, top_suppliers, years_active, is_recurring,
      typical_month
  - save/load parquet under data/aggregated/

Gate: lookup loads in under 2 seconds, covers at least 50,000 contracts.
"""
