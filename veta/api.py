"""ComprasMX API client (spec section 2, build step 2).

POST-based REST client for the public ComprasMX endpoints. The endpoints are
open (no login) but require the signed grc/igrc/xgrc headers produced by
veta.auth, refreshed per request. The client paginates the expedientes
listing, rate limits to 1 request per second, and retries with backoff.

There is no server-side filter for procedure type, so callers filter the
results client-side for tipo_procedimiento == "LICITACION PUBLICA" (helper
is_licitacion_publica / filter_licitaciones).
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Iterator
from typing import Any

import httpx

from veta import auth

BASE_URL = "https://upcp-cnetservicios.buengobierno.gob.mx/whitney/sitiopublico"
CLOCK_URL = (
    "https://upcp-cnetservicios.buengobierno.gob.mx"
    "/adele/interoperabilidad/tp/reloj"
)

DEFAULT_PAGE_SIZE = 100
MIN_REQUEST_INTERVAL = 1.0  # seconds, 1 request per second
MAX_RETRIES = 4
CLOCK_RESYNC_SECONDS = 240

LICITACION_PUBLICA = "LICITACIÓN PÚBLICA"

# The detail and reqeconomicos GET endpoints take id_proceso from the SPA route
# segment, which is the literal string "procedimiento" on the public site (not
# the numeric 0 used by the POST listing filter). Passing 0 here returns 400.
DETAIL_ID_PROCESO = "procedimiento"

# Full expedientes filter payload with the confirmed default values. Callers
# override individual keys (id_p_especifica, estatus_alterno, id_ley, etc.).
DEFAULT_FILTER: dict[str, Any] = {
    "id_ley": None,
    "id_tipo_procedimiento": None,
    "id_tipo_contratacion": None,
    "fecha_apertura_inicio": None,
    "fecha_apertura_fin": None,
    "fecha_publicacion_inicio": None,
    "fecha_publicacion_fin": None,
    "id_tipo_dependencia": [],
    "numero_procedimiento": None,
    "nombre_procedimiento": None,
    "credito_externo": None,
    "exclusivo_mipymes": None,
    "id_forma_participacion": None,
    "id_entidad_federativa": [],
    "id_p_especifica": [],
    "id_caracter_procedimiento": None,
    "id_estatus": 0,
    "id_proceso": 0,
    "codigo_expediente": None,
    "codigo_procedimiento": None,
    "estatus_alterno": [],
    "compra_consolidada": False,
}


def build_filter(**overrides: Any) -> dict[str, Any]:
    """Return a full filter payload with the given overrides applied."""
    payload = dict(DEFAULT_FILTER)
    unknown = set(overrides) - set(DEFAULT_FILTER)
    if unknown:
        raise KeyError(f"unknown filter keys: {sorted(unknown)}")
    payload.update(overrides)
    return payload


def is_licitacion_publica(record: dict[str, Any]) -> bool:
    """True if a tender record is a licitacion publica.

    Uses the tipo_procedimiento label, falling back to the numero_procedimiento
    prefix ("LA-" LAASSP, "LO-" LOPSRM) when the label is missing.
    """
    tipo = (record.get("tipo_procedimiento") or "").strip().upper()
    if tipo == LICITACION_PUBLICA:
        return True
    numero = (record.get("numero_procedimiento") or "").strip().upper()
    return numero.startswith("LA-") or numero.startswith("LO-")


def filter_licitaciones(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if is_licitacion_publica(r)]


class ComprasMXClient:
    """Signed, rate-limited client for the ComprasMX public API."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        page_size: int = DEFAULT_PAGE_SIZE,
        min_interval: float = MIN_REQUEST_INTERVAL,
        timeout: float = 40.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.min_interval = min_interval
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": auth.ORIGIN,
                "Referer": auth.ORIGIN + "/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            },
        )
        self._last_request_at = 0.0
        self._clock_delta = 0.0       # server_epoch - local_epoch
        self._clock_synced_at = 0.0

    def __enter__(self) -> "ComprasMXClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _sync_clock(self, force: bool = False) -> None:
        """Sync to the server clock and remember the offset from local time."""
        if not force and (time.monotonic() - self._clock_synced_at) < CLOCK_RESYNC_SECONDS:
            return
        self._throttle()
        self._last_request_at = time.monotonic()
        response = self._client.get(CLOCK_URL)
        response.raise_for_status()
        server_utc = datetime.datetime.fromisoformat(
            response.json()["fecha_actual"].replace("Z", "+00:00")
        )
        self._clock_delta = server_utc.timestamp() - time.time()
        self._clock_synced_at = time.monotonic()

    def _server_time_cdmx(self) -> datetime.datetime:
        epoch = time.time() + self._clock_delta
        utc = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
        return utc + auth.CDMX_UTC_OFFSET

    def _signed_headers(self, action: str) -> dict[str, str]:
        self._sync_clock()
        return auth.build_headers(self._server_time_cdmx(), action=action)

    def _request(
        self,
        method: str,
        path: str,
        action: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            self._last_request_at = time.monotonic()
            try:
                headers = self._signed_headers(action)
                response = self._client.request(
                    method, url, json=body, headers=headers
                )
                if response.status_code == 200:
                    return response.json()
                # 401 can happen on a stale clock; force a resync and retry.
                if response.status_code == 401:
                    self._sync_clock(force=True)
                    last_error = httpx.HTTPStatusError(
                        "401 Unauthorized",
                        request=response.request,
                        response=response,
                    )
                elif response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"{response.status_code} server error",
                        request=response.request,
                        response=response,
                    )
                else:
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(
            f"request to {url} failed after {MAX_RETRIES} attempts"
        ) from last_error

    def _post(
        self, path: str, body: dict[str, Any], action: str
    ) -> dict[str, Any]:
        return self._request("POST", path, action, body=body)

    def _get(self, path: str, action: str) -> dict[str, Any]:
        return self._request("GET", path, action)

    def fetch_expedientes_page(
        self, filters: dict[str, Any], page: int
    ) -> dict[str, Any]:
        """Fetch a single page. Returns {'registros': [...], 'paginacion': {}}."""
        path = f"expedientes?rows={self.page_size}&page={page}"
        data = self._post(path, filters, auth.ACTION_GET_PROCEDIMIENTOS)
        block = data["data"][0]
        pagination = block["paginacion"][0] if block.get("paginacion") else {}
        return {"registros": block.get("registros", []), "paginacion": pagination}

    def iter_expedientes(
        self, filters: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        """Yield every tender record across all pages for the given filters."""
        first = self.fetch_expedientes_page(filters, page=1)
        yield from first["registros"]
        total_pages = int(first["paginacion"].get("total_paginas", 1) or 1)
        for page in range(2, total_pages + 1):
            page_data = self.fetch_expedientes_page(filters, page=page)
            yield from page_data["registros"]

    def fetch_expedientes(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch all tender records for the given filters."""
        return list(self.iter_expedientes(filters))

    def total_registros(self, filters: dict[str, Any]) -> int:
        """Total matching records reported by the API (first page metadata)."""
        first = self.fetch_expedientes_page(filters, page=1)
        return int(first["paginacion"].get("total_registros", 0) or 0)

    def fetch_catalog(
        self,
        catalogo: str,
        action: str,
        ley_id: int = 1,
        filtro: Any = None,
    ) -> list[Any]:
        """Fetch a lookup catalog (for example catalogo="clave")."""
        body = {"catalogo": catalogo, "ley_id": ley_id, "filtro": filtro}
        data = self._post("catalogos", body, action)
        payload = data.get("data")
        return payload if isinstance(payload, list) else []

    def fetch_by_partida(
        self,
        partida_ids: list[int],
        estatus_alterno: list[str] | None = None,
        id_ley: int | None = None,
        id_proceso: int = 0,
    ) -> dict[int, list[dict[str, Any]]]:
        """Fetch tenders once per partida so each result is tagged by partida.

        The listing response does not carry the partida of a tender, but the
        id_p_especifica filter does. Querying one partida at a time yields the
        partida -> tenders mapping needed to join with the historical lookup.
        Returns {partida_id: [records]}.
        """
        results: dict[int, list[dict[str, Any]]] = {}
        for partida_id in partida_ids:
            payload = build_filter(
                id_ley=id_ley,
                id_p_especifica=[partida_id],
                estatus_alterno=estatus_alterno or [],
                id_proceso=id_proceso,
            )
            results[partida_id] = self.fetch_expedientes(payload)
        return results

    def fetch_detail(self, uuid: str) -> dict[str, Any] | None:
        """Fetch the full detail record for one tender (GET_DETALLE_PROCEDIMIENTO).

        The listing carries only summary fields; this endpoint adds the buyer
        contact, dates, guarantees, payment terms, and the anexos (attachments).
        Returns the single 'registro' dict with an added 'anexos' list, or None
        when the expediente has no detail registro.
        """
        path = f"expedientes/{uuid}?id_proceso={DETAIL_ID_PROCESO}"
        data = self._get(path, auth.ACTION_GET_DETALLE)
        block = data.get("data") or {}
        registros = block.get("registro") or []
        if not registros:
            return None
        record = dict(registros[0])
        record["anexos"] = block.get("anexos", [])
        return record

    def fetch_partidas(
        self, uuid: str, rows: int = 50, grupo: int = 1
    ) -> list[dict[str, Any]]:
        """Fetch a tender's economic requirements, i.e. its partidas.

        This is the only endpoint that exposes a live tender's partida clave
        (clave_p_especifica) and, when the buyer publishes it, its estimated
        amount band (monto_minimo / monto_maximo). Most licitaciones publicas
        leave both amounts null. Returns a flat list of partida line items
        across every requirement group in the response.
        """
        path = (
            f"expedientes/{uuid}/reqeconomicos"
            f"?id_proceso={DETAIL_ID_PROCESO}&rows={rows}&page=1&grupo={grupo}"
        )
        data = self._get(path, auth.ACTION_GET_REQECONOMICOS)
        payload = data.get("data") or []
        if not payload:
            return []
        items: list[dict[str, Any]] = []
        for group in payload[0].get("registros", []):
            items.extend(group.get("data_registros", []))
        return items
