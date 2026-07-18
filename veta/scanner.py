"""Adjacent opportunity scanner (spec section 3.6, step 6).

Ranks partidas the distributor is NOT currently targeting by attractiveness,
using the historical data: total contract volume, distinct buyers, average
new-entrant rate, competition breadth, and overlap with existing buyers.

TODO(phase1): implement the scanner.
  - scan(lookup, current_partidas) -> ranked list of adjacent partidas
  - drive the "which categories should I add?" conversation
"""
