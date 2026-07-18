"""Buyer intelligence enrichment (spec section 3.3, step 3).

For each tender on the filtered shortlist, join with the historical
aggregation by buyer (siglas) + partida and attach the intelligence card:
buyer openness, price band, recurrence, top competitors, typical timing, and
urgency.

TODO(phase1): implement enrichment.
  - enrich(tenders, lookup) -> list of tenders with an intelligence card
  - compute urgency from fecha_apertura and fecha_aclaraciones
  - derive a plain-language SIGNAL summary per tender
"""
