"""ComprasMX API client (spec section 2, build step 2).

POST-based REST client for the public ComprasMX endpoints. No auth. Paginates
the expedientes listing (rows=100 per page). Rate limits to 1 req/sec and
retries with backoff on failure.

TODO(phase1): implement the client.
  - BASE_URL = "https://upcp-cnetservicios.buengobierno.gob.mx/whitney/sitiopublico"
  - fetch_catalog(catalogo: str) -> list[dict]
  - fetch_expedientes(filters: dict) -> Iterator[dict]  (paginate all pages)
  - client-side filter for tipo_procedimiento == "LICITACION PUBLICA"
  - discover the tender detail endpoint (spec section 2.3)
"""

BASE_URL = "https://upcp-cnetservicios.buengobierno.gob.mx/whitney/sitiopublico"
