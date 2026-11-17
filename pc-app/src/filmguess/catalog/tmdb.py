"""Cliente asíncrono mínimo de la API de TMDB (v3), con límite de ritmo y reintentos."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
NETFLIX_PROVIDER_IDS = frozenset({8, 1796})
"""Netflix y Netflix con anuncios en los datos de proveedores de TMDB."""


class TmdbError(Exception):
    """Fallo al consultar TMDB (red, límite agotado, respuesta inesperada...)."""


class TmdbAuthError(TmdbError):
    """TMDB rechaza la clave: no tiene sentido seguir intentándolo."""


class TmdbNotFoundError(TmdbError):
    """TMDB no tiene el recurso pedido (404)."""


@dataclass(frozen=True, slots=True)
class TmdbCandidate:
    """Un resultado de ``/search/movie``."""

    tmdb_id: int
    title: str
    original_title: str
    year: int | None
    popularity: float


def poster_url(poster_path: str | None, size: str = "w342") -> str | None:
    """Construye la URL pública de un póster de TMDB.

    Args:
        poster_path: Ruta devuelta por TMDB (``/abc.jpg``).
        size: Tamaño (``w185``, ``w342``, ``w780``...).

    Returns:
        URL completa, o ``None`` si no hay póster.
    """
    return f"{IMAGE_BASE}/{size}{poster_path}" if poster_path else None


def is_on_netflix(details: dict[str, Any], region: str) -> bool | None:
    """Indica si los proveedores de un detalle incluyen Netflix en la región dada.

    Args:
        details: Respuesta de ``/movie/{id}`` con ``append_to_response=watch/providers``.
        region: Código de país (``ES``).

    Returns:
        ``True``/``False``, o ``None`` si la respuesta no trae datos de proveedores.
    """
    providers = details.get("watch/providers")
    if not isinstance(providers, dict):
        return None
    country = providers.get("results", {}).get(region)
    if not isinstance(country, dict):
        return False
    offers = country.get("flatrate", []) + country.get("ads", [])
    return any(o.get("provider_id") in NETFLIX_PROVIDER_IDS for o in offers)


def _year(release_date: str | None) -> int | None:
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


class RateLimiter:
    """Limita el número de peticiones por segundo (intervalo mínimo entre inicios)."""

    def __init__(self, per_second: float) -> None:
        """Crea el limitador.

        Args:
            per_second: Peticiones por segundo; 0 o menos desactiva el límite.
        """
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Espera hasta que se pueda lanzar la siguiente petición."""
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


class TmdbClient:
    """Cliente de TMDB.

    Acepta una clave v3 (se envía como ``api_key``) o un token de lectura v4
    (empieza por ``eyJ``; se envía como ``Authorization: Bearer``).
    """

    def __init__(
        self,
        api_key: str,
        *,
        language: str = "es-ES",
        region: str = "ES",
        per_second: float = 35,
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Crea el cliente.

        Args:
            api_key: Clave v3 o token v4.
            language: Idioma de títulos y fichas.
            region: País para la disponibilidad.
            per_second: Límite de peticiones por segundo.
            max_retries: Reintentos ante 429, 5xx o errores de red.
            transport: Transporte alternativo (para pruebas).

        Raises:
            TmdbError: Si no hay clave.
        """
        if not api_key:
            raise TmdbError("Falta la clave de TMDB en config.toml ([tmdb] api_key).")
        headers = {"Accept": "application/json"}
        params: dict[str, str] = {}
        if api_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            params["api_key"] = api_key
        self.language = language
        self.region = region
        self._max_retries = max_retries
        self._limiter = RateLimiter(per_second)
        self._http = httpx.AsyncClient(
            base_url=API_BASE, headers=headers, params=params, timeout=20, transport=transport
        )

    async def aclose(self) -> None:
        """Cierra las conexiones HTTP."""
        await self._http.aclose()

    async def __aenter__(self) -> TmdbClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def search_movies(self, query: str) -> list[TmdbCandidate]:
        """Busca películas por título.

        Args:
            query: Título a buscar.

        Returns:
            Candidatos de la primera página, en el orden de TMDB.

        Raises:
            TmdbError: Si la petición falla.
        """
        data = await self._get(
            "/search/movie",
            {"query": query, "language": self.language, "include_adult": "false"},
        )
        results = data.get("results", [])
        return [
            TmdbCandidate(
                tmdb_id=int(r["id"]),
                title=str(r.get("title") or ""),
                original_title=str(r.get("original_title") or ""),
                year=_year(r.get("release_date")),
                popularity=float(r.get("popularity") or 0.0),
            )
            for r in results
            if "id" in r
        ]

    async def watch_providers(self) -> list[dict[str, Any]]:
        """Plataformas de cine que TMDB conoce en la región del cliente.

        Returns:
            Elementos de ``/watch/providers/movie`` (``provider_id``, ``provider_name``...).

        Raises:
            TmdbError: Si la petición falla.
        """
        data = await self._get(
            "/watch/providers/movie", {"watch_region": self.region, "language": self.language}
        )
        return [r for r in data.get("results", []) if isinstance(r, dict) and "provider_id" in r]

    async def discover_movies(self, params: dict[str, str]) -> dict[str, Any]:
        """Una página de ``/discover/movie`` en la región e idioma del cliente.

        Args:
            params: Filtros adicionales (``with_watch_providers``, ``page``...).

        Returns:
            JSON con ``results``, ``page``, ``total_pages`` y ``total_results``.

        Raises:
            TmdbError: Si la petición falla.
        """
        query = {
            "watch_region": self.region,
            "language": self.language,
            "include_adult": "false",
            "sort_by": "popularity.desc",
        }
        return await self._get("/discover/movie", query | params)

    async def movie_watch_providers(self, tmdb_id: int) -> dict[str, Any]:
        """Plataformas en las que está una película, en todos los países.

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}/watch/providers`` (``results`` por país).

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(f"/movie/{tmdb_id}/watch/providers", {})

    async def movie_external_ids(self, tmdb_id: int) -> dict[str, Any]:
        """IDs externos de una película (IMDb, Wikidata...).

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}/external_ids``.

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(f"/movie/{tmdb_id}/external_ids", {})

    async def movie_details(self, tmdb_id: int) -> dict[str, Any]:
        """Ficha de una película con IDs externos y proveedores.

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}`` con ``external_ids`` y ``watch/providers``.

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(
            f"/movie/{tmdb_id}",
            {"language": self.language, "append_to_response": "external_ids,watch/providers"},
        )

    async def movie_origin(self, tmdb_id: int) -> dict[str, Any]:
        """Ficha de una película (para su idioma original y países de origen).

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}``.

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(f"/movie/{tmdb_id}", {"language": self.language})

    async def movie_clues(self, tmdb_id: int) -> dict[str, Any]:
        """Ficha de una película con su reparto (para las pistas).

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}`` con ``credits``.

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(
            f"/movie/{tmdb_id}", {"language": self.language, "append_to_response": "credits"}
        )

    async def movie_reviews(self, tmdb_id: int) -> dict[str, Any]:
        """Críticas de usuarios de una película (casi todas en inglés).

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}/reviews``.

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(f"/movie/{tmdb_id}/reviews", {})

    async def movie_aliases(self, tmdb_id: int) -> dict[str, Any]:
        """Títulos alternativos y traducciones de una película.

        Args:
            tmdb_id: ID de TMDB.

        Returns:
            JSON de ``/movie/{id}`` con ``alternative_titles`` y ``translations``.

        Raises:
            TmdbNotFoundError: Si TMDB no tiene esa película.
            TmdbError: Si la petición falla.
        """
        return await self._get(
            f"/movie/{tmdb_id}", {"append_to_response": "alternative_titles,translations"}
        )

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        last_error = "sin respuesta"
        for attempt in range(self._max_retries + 1):
            await self._limiter.wait()
            try:
                response = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                last_error = f"error de red: {exc}"
                await asyncio.sleep(min(2**attempt, 30))
                continue

            if response.status_code == 200:
                return cast(dict[str, Any], response.json())
            if response.status_code == 401:
                raise TmdbAuthError("TMDB rechaza la clave (401). Revisa [tmdb] api_key.")
            if response.status_code == 404:
                raise TmdbNotFoundError(f"TMDB no encuentra {path} (404).")
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else min(2**attempt, 30)
                await asyncio.sleep(wait)
                continue
            raise TmdbError(f"TMDB respondió HTTP {response.status_code} en {path}.")
        raise TmdbError(f"TMDB no responde en {path} tras varios intentos ({last_error}).")
