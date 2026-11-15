"""Tests del cliente de TMDB con respuestas simuladas."""

from __future__ import annotations

import httpx
import pytest
import respx

from filmguess.catalog.tmdb import (
    API_BASE,
    TmdbAuthError,
    TmdbClient,
    TmdbError,
    is_on_netflix,
    poster_url,
)

SEARCH = f"{API_BASE}/search/movie"


def client(key: str = "clave_v3") -> TmdbClient:
    return TmdbClient(key, per_second=0)


@respx.mock
async def test_busqueda_con_clave_v3() -> None:
    route = respx.get(SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 27205,
                        "title": "Origen",
                        "original_title": "Inception",
                        "release_date": "2010-07-15",
                        "popularity": 80.5,
                    },
                    {"id": 1, "title": "Sin fecha", "original_title": "", "release_date": ""},
                ]
            },
        )
    )

    async with client() as c:
        results = await c.search_movies("Inception")

    request = route.calls.last.request
    assert request.url.params["api_key"] == "clave_v3"
    assert request.url.params["language"] == "es-ES"
    assert results[0].tmdb_id == 27205
    assert results[0].year == 2010
    assert results[1].year is None


@respx.mock
async def test_token_v4_va_en_cabecera() -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))

    async with client("eyJtoken") as c:
        await c.search_movies("x")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer eyJtoken"
    assert "api_key" not in request.url.params


@respx.mock
async def test_reintenta_tras_429() -> None:
    respx.get(SEARCH).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"results": []}),
        ]
    )

    async with client() as c:
        assert await c.search_movies("x") == []


@respx.mock
async def test_401_es_error_de_clave() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(401))

    async with client() as c:
        with pytest.raises(TmdbAuthError):
            await c.search_movies("x")


@respx.mock
async def test_agota_reintentos() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(503, headers={"Retry-After": "0"}))

    async with TmdbClient("k", per_second=0, max_retries=2) as c:
        with pytest.raises(TmdbError, match="varios intentos"):
            await c.search_movies("x")


def test_sin_clave() -> None:
    with pytest.raises(TmdbError, match="Falta la clave"):
        TmdbClient("")


def test_is_on_netflix() -> None:
    netflix = {"watch/providers": {"results": {"ES": {"flatrate": [{"provider_id": 8}]}}}}
    ads = {"watch/providers": {"results": {"ES": {"ads": [{"provider_id": 1796}]}}}}
    other = {"watch/providers": {"results": {"ES": {"flatrate": [{"provider_id": 337}]}}}}
    other_country = {"watch/providers": {"results": {"US": {"flatrate": [{"provider_id": 8}]}}}}

    assert is_on_netflix(netflix, "ES") is True
    assert is_on_netflix(ads, "ES") is True
    assert is_on_netflix(other, "ES") is False
    assert is_on_netflix(other_country, "ES") is False
    assert is_on_netflix({}, "ES") is None


def test_poster_url() -> None:
    assert poster_url("/a.jpg") == "https://image.tmdb.org/t/p/w342/a.jpg"
    assert poster_url(None) is None
