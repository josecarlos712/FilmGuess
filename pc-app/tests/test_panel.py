"""Tests del panel del PC: servicio y rutas ``/panel``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.importer import ImportProgress
from filmguess.catalog.service import ImportSummary
from filmguess.catalog.tmdb import TmdbError, TmdbNotFoundError
from filmguess.config import RandomConfig
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.panel.service import PanelError, PanelService, parse_netflix_id, parse_tmdb_id
from filmguess.server.app import create_app
from tests.conftest import make_match
from tests.test_server import TOKEN, FakePlayer

LOCAL = ("127.0.0.1", 50000)
LAN = ("192.168.1.20", 50000)

DETAILS: dict[str, Any] = {
    "id": 27205,
    "title": "Origen",
    "original_title": "Inception",
    "release_date": "2010-07-15",
    "runtime": 148,
    "poster_path": "/p.jpg",
    "external_ids": {"imdb_id": "tt1375666"},
    "popularity": 50.0,
    "vote_average": 8.4,
    "vote_count": 30000,
}


class FakeTmdb:
    region = "ES"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[int] = []

    async def movie_details(self, tmdb_id: int) -> dict[str, Any]:
        self.calls.append(tmdb_id)
        if self.error:
            raise self.error
        return DETAILS | {"id": tmdb_id}


def factory(client: FakeTmdb) -> Callable[[], Any]:
    @asynccontextmanager
    async def open_client() -> AsyncIterator[FakeTmdb]:
        yield client

    return open_client


@pytest.fixture
def catalog() -> CatalogDB:
    db = CatalogDB(":memory:")
    db.add_csv_movies([CsvMovie(1, "MOVIE"), CsvMovie(2, "Origen"), CsvMovie(3, "Rara")])
    db.save_match(1, make_match(5, title_es="Bee Movie", confidence="baja"))
    db.save_match(2, make_match(27205, confidence="alta"))
    db.mark_no_match(3)
    return db


# --- Enlaces ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "https://www.netflix.com/title/81435227",
        "https://www.netflix.com/es/title/81435227?trackId=255824129",
        "netflix.com/watch/81435227?t=10",
        " 81435227 ",
    ],
)
def test_parse_netflix_id(text: str) -> None:
    assert parse_netflix_id(text) == 81435227


@pytest.mark.parametrize(
    "text",
    [
        "https://www.themoviedb.org/movie/27205-inception",
        "https://www.themoviedb.org/movie/27205-inception?language=es-ES",
        "themoviedb.org/movie/27205",
        "27205",
    ],
)
def test_parse_tmdb_id(text: str) -> None:
    assert parse_tmdb_id(text) == 27205


@pytest.mark.parametrize("text", ["", "hola", "https://www.netflix.com/browse"])
def test_enlace_de_netflix_invalido(text: str) -> None:
    with pytest.raises(PanelError) as exc:
        parse_netflix_id(text)
    assert exc.value.code == "enlace_invalido"


def test_enlace_de_tmdb_invalido() -> None:
    with pytest.raises(PanelError):
        parse_tmdb_id("https://www.themoviedb.org/tv/1399-juego-de-tronos")


# --- Servicio ---------------------------------------------------------------------


def test_regenerar_token_lo_guarda() -> None:
    saved: list[str] = []
    panel = PanelService(CatalogDB(":memory:"), token="viejo", save_token=saved.append)

    new = panel.regenerate_token()

    assert new != "viejo" and len(new) >= 20
    assert panel.token == new
    assert saved == [new]


def test_regenerar_token_si_no_se_puede_guardar() -> None:
    def fail(_: str) -> None:
        raise OSError("disco lleno")

    panel = PanelService(CatalogDB(":memory:"), token="viejo", save_token=fail)
    with pytest.raises(PanelError) as exc:
        panel.regenerate_token()
    assert exc.value.code == "config_error"
    assert panel.token == "viejo"


def test_lista_para_revisar(catalog: CatalogDB) -> None:
    panel = PanelService(catalog, token="t")

    low, total = panel.to_review("baja", 1, 20)
    assert total == 1 and low[0].netflix_id == 1
    unmatched, total = panel.to_review("sin_match", 1, 20)
    assert total == 1 and unmatched[0].netflix_id == 3


async def test_corregir_emparejamiento(catalog: CatalogDB) -> None:
    tmdb = FakeTmdb()
    panel = PanelService(catalog, token="t", tmdb=factory(tmdb))

    movie = await panel.correct(1, "https://www.themoviedb.org/movie/27205-inception")

    assert tmdb.calls == [27205]
    assert movie.title_es == "Origen" and movie.manual and movie.match_conf == "alta"
    assert panel.to_review("baja", 1, 20)[1] == 0


@pytest.mark.parametrize(
    ("error", "code"),
    [(TmdbNotFoundError("404"), "tmdb_no_encontrada"), (TmdbError("caído"), "tmdb_error")],
)
async def test_corregir_con_tmdb_fallando(catalog: CatalogDB, error: Exception, code: str) -> None:
    panel = PanelService(catalog, token="t", tmdb=factory(FakeTmdb(error)))
    with pytest.raises(PanelError) as exc:
        await panel.correct(1, "27205")
    assert exc.value.code == code


async def test_corregir_sin_clave_ni_pelicula(catalog: CatalogDB) -> None:
    with pytest.raises(PanelError) as exc:
        await PanelService(catalog, token="t").correct(1, "27205")
    assert exc.value.code == "sin_clave_tmdb"
    with pytest.raises(PanelError) as exc:
        await PanelService(catalog, token="t", tmdb=factory(FakeTmdb())).correct(99, "1")
    assert exc.value.code == "no_encontrada"


async def test_alta_por_enlace(catalog: CatalogDB) -> None:
    panel = PanelService(catalog, token="t", tmdb=factory(FakeTmdb()))

    movie, added = await panel.add_by_link(
        "https://www.netflix.com/title/81435227", "themoviedb.org/movie/607259"
    )
    assert added is True
    assert movie.netflix_id == 81435227 and movie.tmdb_id == 607259
    assert movie.netflix_es is True and movie.manual

    again, added = await panel.add_by_link("81435227", "27205")
    assert added is False and again.tmdb_id == 27205


async def test_importacion_en_segundo_plano(catalog: CatalogDB) -> None:
    def runner(progress: Callable[[ImportProgress], None]) -> ImportSummary:
        progress(ImportProgress(total=10, done=4, matched=3))
        progress(ImportProgress(total=10, done=10, matched=9, no_match=1))
        return ImportSummary(csv_added=2, imdb_updated=7)

    panel = PanelService(catalog, token="t", run_import=runner)
    state = panel.start_import()
    assert state.running
    with pytest.raises(PanelError) as exc:
        panel.start_import()
    assert exc.value.code == "importando"

    await panel.wait_import()
    final = panel.import_state
    assert not final.running and not final.failed
    assert (final.done, final.total, final.matched) == (10, 10, 9)
    assert final.summary["csv_added"] == 2 and final.finished_at


async def test_importacion_que_falla(catalog: CatalogDB) -> None:
    def runner(progress: Callable[[ImportProgress], None]) -> ImportSummary:
        raise FileNotFoundError("movies.csv")

    panel = PanelService(catalog, token="t", run_import=runner)
    panel.start_import()
    await panel.wait_import()

    assert panel.import_state.failed and "movies.csv" in (panel.import_state.message or "")
    assert not panel.import_state.running


async def test_sin_importador() -> None:
    with pytest.raises(PanelError) as exc:
        PanelService(CatalogDB(":memory:"), token="t").start_import()
    assert exc.value.code == "sin_importador"


# --- Rutas ------------------------------------------------------------------------


@pytest.fixture
def app_parts(catalog: CatalogDB, tmp_path: Path) -> tuple[FastAPI, PanelService, FakePlayer]:
    player = FakePlayer()
    session = GameSession(
        catalog, player, Scoreboard(tmp_path / "s.json"), RandomConfig(min_votes=0)
    )
    saved: list[str] = []
    panel = PanelService(catalog, token=TOKEN, save_token=saved.append, tmdb=factory(FakeTmdb()))
    app = create_app(session, TOKEN, port=8765, ip="192.168.1.10", panel=panel)
    return app, panel, player


Parts = tuple[FastAPI, PanelService, FakePlayer]


def test_panel_solo_desde_el_propio_pc(app_parts: Parts) -> None:
    app = app_parts[0]
    remote = TestClient(app, client=LAN)
    for method, path in [
        ("GET", "/panel"),
        ("GET", "/panel/api/estado"),
        ("POST", "/panel/api/token"),
        ("POST", "/panel/api/importar"),
        ("GET", "/panel/api/revisar"),
        ("POST", "/panel/api/netflix-login"),
    ]:
        response = remote.request(method, path, headers={"X-Token": TOKEN})
        assert response.status_code == 403, path
        assert response.json()["error"] == "solo_local"

    local = TestClient(app, client=LOCAL)
    page = local.get("/panel")
    assert page.status_code == 200 and "/static/panel/panel.js" in page.text


def test_estado_del_panel(app_parts: Parts) -> None:
    client = TestClient(app_parts[0], client=LOCAL)

    data = client.get("/panel/api/estado").json()

    assert data["navegador"] is True
    assert data["moviles"] == 0 and data["tele"] == 0
    assert data["catalogo"]["baja"] == 1 and data["catalogo"]["sin_match"] == 1
    assert data["importacion"]["running"] is False
    assert data["conexion"]["url"].endswith(f"#token={TOKEN}")


def test_regenerar_token_desconecta_moviles(app_parts: Parts) -> None:
    app, panel, _ = app_parts
    local = TestClient(app, client=LOCAL)
    phone = TestClient(app, client=LAN)

    with phone.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.receive_json()
        ws.receive_json()
        assert local.get("/panel/api/estado").json()["moviles"] == 1
        result = local.post("/panel/api/token").json()
        assert result["desconectados"] == 1
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    assert panel.token != TOKEN
    assert result["url"].endswith(f"#token={panel.token}")
    assert phone.get("/status", headers={"X-Token": TOKEN}).status_code == 401
    assert phone.get("/status", headers={"X-Token": panel.token}).status_code == 200


def test_revisar_y_corregir_por_rutas(app_parts: Parts) -> None:
    client = TestClient(app_parts[0], client=LOCAL)

    page = client.get("/panel/api/revisar?confianza=baja").json()
    assert page["total"] == 1
    assert page["resultados"][0]["tituloCsv"] == "MOVIE"

    fixed = client.post("/panel/api/peliculas/1/tmdb", json={"tmdb": "27205"})
    assert fixed.status_code == 200
    assert fixed.json()["pelicula"]["titulo"] == "Origen"
    assert client.get("/panel/api/revisar?confianza=baja").json()["total"] == 0

    bad = client.post("/panel/api/peliculas/1/tmdb", json={"tmdb": "no es un enlace"})
    assert bad.status_code == 422 and bad.json()["error"] == "enlace_invalido"


def test_alta_por_rutas(app_parts: Parts) -> None:
    client = TestClient(app_parts[0], client=LOCAL)

    added = client.post(
        "/panel/api/peliculas",
        json={"netflix": "https://www.netflix.com/title/81435227", "tmdb": "607259"},
    ).json()

    assert added["nueva"] is True
    assert added["pelicula"]["netflixId"] == 81435227


def test_login_y_espera_por_rutas(app_parts: Parts) -> None:
    app, _, player = app_parts
    client = TestClient(app, client=LOCAL)

    assert client.post("/panel/api/netflix-login").json() == {"ok": True}
    assert client.post("/panel/api/espera").json() == {"ok": True}
    assert [c[0] for c in player.calls] == ["open_login", "idle"]


def test_has_tmdb_depends_on_key() -> None:
    assert PanelService(CatalogDB(":memory:"), token="t").has_tmdb is False
    with_key = PanelService(CatalogDB(":memory:"), token="t", tmdb=factory(FakeTmdb()))
    assert with_key.has_tmdb is True
