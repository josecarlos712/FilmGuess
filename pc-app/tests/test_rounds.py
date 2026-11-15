"""Ronda con pistas, fotogramas, clip, saltar escena, categoría y reparto de puntos."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from filmguess.catalog.clues import Clues, Genre
from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.config import RandomConfig
from filmguess.game import session as session_module
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.server.app import create_app
from tests.conftest import make_match
from tests.test_server import HEADERS, LAN, TOKEN, FakePlayer

ORIGEN = 70131314
CLUES = Clues(
    [Genre(878, "Ciencia ficción"), Genre(28, "Acción")],
    "Leonardo DiCaprio",
    "Cobb",
    "An idea is like a virus.",
    "wikiquote",
    "en",
)


@pytest.fixture
def catalog() -> Iterator[CatalogDB]:
    db = CatalogDB(":memory:")
    db.add_csv_movies([CsvMovie(ORIGEN, "Inception")])
    db.save_match(ORIGEN, make_match(27205, title_es="Origen", imdb_id="tt1375666"))
    db.update_imdb_ratings([("tt1375666", 8.8, 2_500_000)])
    db.save_clues(ORIGEN, CLUES)
    yield db
    db.close()


@pytest.fixture
def player() -> FakePlayer:
    return FakePlayer()


@pytest.fixture
def session(catalog: CatalogDB, player: FakePlayer, tmp_path: Path) -> GameSession:
    return GameSession(
        catalog,
        player,
        Scoreboard(tmp_path / "scoreboard.json"),
        RandomConfig(min_year=1991, min_votes=1000, pool_size=10, no_repeat=0),
    )


@pytest.fixture
def client(session: GameSession) -> TestClient:
    return TestClient(create_app(session, TOKEN, port=8765, ip="192.168.1.10"), client=LAN)


def ronda(client: TestClient) -> dict[str, object]:
    current = client.get("/status", headers=HEADERS).json()["rondaActual"]
    assert isinstance(current, dict)
    return current


def test_pistas_restan_un_punto_hasta_uno(client: TestClient, player: FakePlayer) -> None:
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)
    assert ronda(client)["valor"] == 5
    assert player.hints["valueText"] == "Vale 5 puntos"

    textos = [client.post("/hint", headers=HEADERS).json()["texto"] for _ in range(5)]

    assert textos[0] == "Ciencia ficción · Acción"
    assert ronda(client)["valor"] == 1
    assert len(player.hints["hints"]) == 5  # type: ignore[arg-type]
    assert client.post("/hint", headers=HEADERS).json()["error"] == "sin_pistas"


def test_categoria_vale_tres_y_da_el_genero_gratis(client: TestClient) -> None:
    body = {"netflixId": ORIGEN, "seconds": 600, "categoria": 878}
    assert client.post("/play", json=body, headers=HEADERS).json()["ok"] is True

    current = ronda(client)
    assert current["valor"] == 3
    assert current["categoria"] == {"id": 878, "nombre": "Ciencia ficción"}
    assert current["pistasTotal"] == 3
    assert client.post("/hint", headers=HEADERS).json()["texto"] == "2010 · IMDb 8,8"


def test_categoria_que_no_coincide(client: TestClient) -> None:
    body = {"netflixId": ORIGEN, "seconds": 600, "categoria": 27}

    response = client.post("/play", json=body, headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["error"] == "categoria_no_coincide"


def test_categorias_y_azar_por_genero(client: TestClient) -> None:
    categorias = client.get("/genres", headers=HEADERS).json()["categorias"]
    assert {"id": 878, "nombre": "Ciencia ficción", "peliculas": 1} in categorias

    assert client.get("/random?categoria=878", headers=HEADERS).json()["netflixId"] == ORIGEN
    assert client.get("/random?categoria=27", headers=HEADERS).json()["error"] == "sin_candidatas"
    found = client.get("/search?q=orig&categoria=27", headers=HEADERS).json()
    assert found["total"] == 0


def test_tres_fotogramas_gratis(client: TestClient, player: FakePlayer) -> None:
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)

    assert client.post("/frame", headers=HEADERS).json()["ok"] is True
    assert client.post("/frame", headers=HEADERS).json()["ok"] is True
    assert client.post("/frame", headers=HEADERS).json()["error"] == "sin_fotogramas"
    assert ronda(client)["fotogramas"] == 3
    assert ronda(client)["valor"] == 5
    assert player.hints["frameText"] == "Fotograma 3 de 3"


def test_saltar_escena_tapa_y_no_gasta_fotograma(client: TestClient, player: FakePlayer) -> None:
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)

    assert client.post("/skip", headers=HEADERS).json()["ok"] is True

    assert ("cover", "Saltando escena…") in player.calls
    assert ronda(client)["fotogramas"] == 1
    assert ronda(client)["segundosReales"] == 600.4  # el primer segundo pedido no cambia


async def test_clip_reproduce_y_vuelve_a_pausar(
    session: GameSession, player: FakePlayer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "CLIP_SECONDS", 0)
    await session.play(ORIGEN, 600)

    await session.clip()
    await asyncio.sleep(0.01)

    names = [c[0] for c in player.calls if isinstance(c, tuple)]
    assert names[-2:] == ["resume", "pause"]
    with pytest.raises(session_module.GameError) as exc:
        await session.clip()
    assert exc.value.code == "clip_usado"


def test_quien_se_arriesga_y_falla_pierde_lo_que_vale_la_ronda(client: TestClient) -> None:
    ids = []
    for name in ("Ana", "Luis"):
        jugadores = client.post("/players", json={"nombre": name}, headers=HEADERS).json()
        ids.append(next(j["id"] for j in jugadores["jugadores"] if j["nombre"] == name))
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)

    client.post("/fail", json={"jugadores": ids[:1]}, headers=HEADERS)  # Ana falla con 5
    client.post("/hint", headers=HEADERS)
    marcador = client.post("/fail", json={"jugadores": ids}, headers=HEADERS).json()  # Luis, con 4

    puntos = {j["nombre"]: j["puntos"] for j in marcador["jugadores"]}
    assert puntos == {"Ana": -5, "Luis": -4}  # Ana no vuelve a perder
    assert sorted(ronda(client)["fallos"]) == sorted(ids)  # type: ignore[type-var]

    client.post("/reveal", headers=HEADERS)
    cerrada = client.post("/fail", json={"jugadores": ids}, headers=HEADERS)
    assert cerrada.status_code == 409 and cerrada.json()["error"] == "ronda_cerrada"
    # Quien ha fallado está fuera de la ronda: no recibe puntos aunque se le marque.
    marcador = client.post("/award", json={"jugadores": ids}, headers=HEADERS).json()
    assert {j["nombre"]: j["puntos"] for j in marcador["jugadores"]} == puntos


def test_ver_el_titulo_en_el_movil_no_lo_revela_en_la_tele(client: TestClient) -> None:
    assert client.get("/peek", headers=HEADERS).status_code == 409
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)

    visto = client.get("/peek", headers=HEADERS).json()

    assert visto["titulo"] == "Origen"
    assert ronda(client)["revelada"] is False and "titulo" not in ronda(client)


def test_reparto_de_puntos_tras_revelar(client: TestClient) -> None:
    ids = []
    for name in ("Ana", "Luis"):
        jugadores = client.post("/players", json={"nombre": name}, headers=HEADERS).json()
        ids.append(next(j["id"] for j in jugadores["jugadores"] if j["nombre"] == name))
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)
    client.post("/hint", headers=HEADERS)
    assert client.post("/award", json={"jugadores": ids}, headers=HEADERS).status_code == 409

    client.post("/reveal", headers=HEADERS)
    assert client.post("/hint", headers=HEADERS).json()["error"] == "ronda_cerrada"
    client.post("/award", json={"jugadores": ids[:1]}, headers=HEADERS)
    marcador = client.post("/award", json={"jugadores": ids}, headers=HEADERS).json()

    puntos = {j["nombre"]: j["puntos"] for j in marcador["jugadores"]}
    assert puntos == {"Ana": 4, "Luis": 4}
    assert sorted(ronda(client)["acertantes"]) == sorted(ids)  # type: ignore[type-var]
