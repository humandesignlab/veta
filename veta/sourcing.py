"""Supplier sourcing reverse lookup (spec section 3.4, step 4).

Given a partida, return the historical suppliers that have won that category,
so an intermediary can find potential sourcing partners.

TODO(phase1): implement reverse lookup.
  - suppliers_for_partida(clave) -> list of
      (supplier_name, rfc, contract_count, total_value, buyers_served)
    sorted by total_value descending
  - source data: the historical Contratos aggregation (see history.py)
"""
