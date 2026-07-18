"""Smoke test: first filtered pull from the ComprasMX API (spec section 3.2).

Validation script, not a unit test. Confirms the API client returns real data.

TODO(phase1):
  1. Call the expedientes endpoint with the distributor filters.
  2. Client-side filter for tipo_procedimiento == "LICITACION PUBLICA".
  3. Print total count and the first 10 results.

Gate: returns real data from the API.
"""


def main() -> None:
    # TODO(phase1): implement the smoke test.
    raise NotImplementedError("smoke_test not implemented yet")


if __name__ == "__main__":
    main()
