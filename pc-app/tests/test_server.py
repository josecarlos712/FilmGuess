"""Tests del servidor FastAPI con un reproductor falso."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.config import RandomConfig
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.player.base import PlayerError, PlayerStatus, RevealInfo, UnavailablePlayer
from filmguess.server.app import create_app
from tests.conftest import make_match

TOKEN = "token-de-prueba"
HEADERS = {"X-Token": TOKEN}
LAN = ("192.168.1.20", 50000)


class FakePlayer:
    """Reproductor que apunta las órdenes y puede fallar a petición."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail: PlayerError | None = None

    async def status(self) -> PlayerStatus:
        return PlayerStatus(browser=True, netflix_session=True)

    async def play(self, netflix_id: int, seconds: int) -> float:
        self._check("play", (netflix_id, seconds))
        return seconds + 0.4

    async def pause(self) -> None:
        self._check("pause", None)

    async def resume(self) -> None:
        self._check("resume", None)

    async def reveal(self, info: RevealInfo) -> None:
        self._check("reveal", info)

    async def idle(self) -> None:
        self._check("idle", None)

    async def open_login(self) -> None:
        self._check("open_login", None)

    def _check(self, name: str, args: object) -> None:
        self.calls.append((name, args))
        if self.fail is not None:
            raise self.fail


@pytest.fixture
def catalog() -> Iterator[CatalogDB]:
    db = CatalogDB(":memory:")
    db.add_csv_movies([CsvMovie(70131314, "Inception"), CsvMovie(2, "Otra")])
    db.save_match(70131314, make_match(27205, title_es="Origen", imdb_id="tt1375666"))
    db.update_imdb_ratings([("tt1375666", 8.8, 2_500_000)])
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


def make_client(session: GameSession, client: tuple[str, int] = LAN) -> TestClient:
    app = create_app(session, TOKEN, port=8765, ip="192.168.1.10")
    return TestClient(app, client=client)


@pytest.fixture
def client(session: GameSession) -> TestClient:
    return make_client(session)


# --- Seguridad --------------------------------------------------------------------


def test_sin_token_devuelve_error_en_espanol(client: TestClient) -> None:
    response = client.get("/status")

    assert response.status_code == 401
    assert response.json()["ok"] is False
    assert response.json()["error"] == "token_invalido"


def test_token_incorrecto(client: TestClient) -> None:
    assert client.get("/status", headers={"X-Token": "otro"}).status_code == 401


def test_rechaza_ip_publica(session: GameSession) -> None:
    public = make_client(session, ("8.8.8.8", 1234))

    response = public.get("/status", headers=HEADERS)

    assert response.status_code == 403
    assert response.json()["error"] == "red_no_permitida"


def test_qr_solo_desde_el_propio_pc(session: GameSession) -> None:
    assert make_client(session).get("/pairing").json()["error"] == "solo_local"
    local = make_client(session, ("127.0.0.1", 1))

    data = local.get("/pairing").json()
    svg = local.get("/pairing/qr.svg")

    assert data["url"] == f"http://192.168.1.10:8765/m/#token={TOKEN}"
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in svg.content


def test_ruta_inexistente(client: TestClient) -> None:
    assert client.get("/nada", headers=HEADERS).json()["error"] == "ruta_no_encontrada"


# --- Catálogo ------------------------------------------------------------------------


def test_status_inicial(client: TestClient) -> None:
    data = client.get("/status", headers=HEADERS).json()

    assert data["navegador"] is True
    assert data["pantalla"] == "espera"
    assert data["rondaActual"] is None


def test_search(client: TestClient) -> None:
    data = client.get("/search", params={"q": "origen"}, headers=HEADERS).json()

    assert data["total"] == 1
    assert data["resultados"][0]["netflixId"] == 70131314
    assert data["resultados"][0]["titulo"] == "Origen"
    assert data["resultados"][0]["poster"].endswith("/p.jpg")


def test_movie_y_no_encontrada(client: TestClient) -> None:
    assert client.get("/movie/70131314", headers=HEADERS).json()["notaImdb"] == 8.8
    response = client.get("/movie/999", headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"] == "no_encontrada"


def test_random(client: TestClient) -> None:
    assert client.get("/random", headers=HEADERS).json()["netflixId"] == 70131314


def test_random_sin_candidatas(catalog: CatalogDB, player: FakePlayer) -> None:
    empty = GameSession(CatalogDB(":memory:"), player, Scoreboard(None), RandomConfig())

    response = make_client(empty).get("/random", headers=HEADERS)

    assert response.status_code == 409
    assert response.json()["error"] == "sin_candidatas"


# --- Ronda ------------------------------------------------------------------------------


def test_ronda_completa(client: TestClient, player: FakePlayer) -> None:
    played = client.post("/play", json={"netflixId": 70131314, "seconds": 5025}, headers=HEADERS)
    assert played.json() == {"ok": True, "actualSeconds": 5025.4}
    status = client.get("/status", headers=HEADERS).json()
    assert status["pantalla"] == "pelicula"
    assert "titulo" not in status["rondaActual"]  # no se desvela antes de tiempo

    assert client.post("/pause", headers=HEADERS).json() == {"ok": True}
    assert client.post("/resume", headers=HEADERS).json() == {"ok": True}
    revealed = client.post("/reveal", headers=HEADERS).json()
    assert revealed["titulo"] == "Origen"
    assert client.get("/status", headers=HEADERS).json()["rondaActual"]["titulo"] == "Origen"

    assert client.post("/idle", headers=HEADERS).json() == {"ok": True}
    assert client.get("/status", headers=HEADERS).json()["pantalla"] == "espera"
    assert [c[0] for c in player.calls] == ["play", "pause", "resume", "reveal", "idle"]
    info = player.calls[3][1]
    assert isinstance(info, RevealInfo)
    assert info.poster_url is not None and "/w780/" in info.poster_url


def test_play_fuera_de_duracion(client: TestClient, player: FakePlayer) -> None:
    response = client.post("/play", json={"netflixId": 70131314, "seconds": 9000}, headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["error"] == "fuera_de_duracion"
    assert "02:28:00" in response.json()["mensaje"]
    assert player.calls == []


def test_play_cuerpo_invalido(client: TestClient) -> None:
    response = client.post("/play", json={"netflixId": "x"}, headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["error"] == "peticion_invalida"


def test_sin_ronda(client: TestClient) -> None:
    assert client.post("/reveal", headers=HEADERS).json()["error"] == "sin_ronda"
    assert client.post("/pause", headers=HEADERS).status_code == 409


def test_no_disponible_marca_la_pelicula(
    client: TestClient, player: FakePlayer, catalog: CatalogDB
) -> None:
    player.fail = PlayerError("no_disponible_es", "Netflix dice que no está disponible.")

    response = client.post("/play", json={"netflixId": 70131314, "seconds": 10}, headers=HEADERS)

    assert response.status_code == 409
    assert response.json()["mensaje"] == "Netflix dice que no está disponible."
    movie = catalog.get(70131314)
    assert movie is not None and movie.netflix_es is False
    status = client.get("/status", headers=HEADERS).json()
    assert status["ultimoError"]["error"] == "no_disponible_es"
    assert status["pantalla"] == "espera"


def test_sin_navegador(catalog: CatalogDB) -> None:
    session = GameSession(catalog, UnavailablePlayer(), Scoreboard(None), RandomConfig())
    client = make_client(session)

    response = client.post("/play", json={"netflixId": 70131314, "seconds": 1}, headers=HEADERS)

    assert response.status_code == 503
    assert response.json()["error"] == "navegador_cerrado"
    assert client.get("/status", headers=HEADERS).json()["navegador"] is False


# --- Marcador ---------------------------------------------------------------------------


def test_marcador(client: TestClient, tmp_path: Path) -> None:
    ana = client.post("/players", json={"nombre": " Ana "}, headers=HEADERS)
    assert ana.status_code == 201
    client.post("/players", json={"nombre": "Luis"}, headers=HEADERS)
    repeated = client.post("/players", json={"nombre": "ana"}, headers=HEADERS)
    assert repeated.status_code == 409

    players = client.get("/players", headers=HEADERS).json()["jugadores"]
    luis = next(p for p in players if p["nombre"] == "Luis")
    data = client.post(f"/players/{luis['id']}/points", json={"puntos": 3}, headers=HEADERS)
    assert data.json()["jugadores"][0] == {"id": luis["id"], "nombre": "Luis", "puntos": 3}

    assert (tmp_path / "scoreboard.json").exists()
    reloaded = Scoreboard(tmp_path / "scoreboard.json")
    assert {p.name: p.points for p in reloaded.players} == {"Ana": 0, "Luis": 3}

    client.post("/players/reset", headers=HEADERS)
    deleted = client.delete(f"/players/{luis['id']}", headers=HEADERS).json()
    assert deleted["jugadores"] == [{"id": players[0]["id"], "nombre": "Ana", "puntos": 0}]
    assert client.delete("/players/nadie", headers=HEADERS).status_code == 404


# --- WebSocket ---------------------------------------------------------------------------


def test_websocket_recibe_estado_y_eventos(client: TestClient) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        assert ws.receive_json()["tipo"] == "estado"
        assert ws.receive_json() == {"tipo": "marcador", "datos": {"jugadores": []}}

        client.post("/play", json={"netflixId": 70131314, "seconds": 60}, headers=HEADERS)
        assert ws.receive_json()["datos"]["pantalla"] == "cargando"
        event = ws.receive_json()
        assert event["tipo"] == "estado"
        assert event["datos"]["pantalla"] == "pelicula"

        client.post("/players", json={"nombre": "Ana"}, headers=HEADERS)
        assert ws.receive_json()["datos"]["jugadores"][0]["nombre"] == "Ana"


def test_websocket_error_del_reproductor(client: TestClient, player: FakePlayer) -> None:
    player.fail = PlayerError("perfil_requerido", "Elige el perfil de Netflix en el PC.")
    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.receive_json()
        ws.receive_json()
        client.post("/play", json={"netflixId": 70131314, "seconds": 60}, headers=HEADERS)
        assert ws.receive_json()["datos"]["pantalla"] == "cargando"
        event = ws.receive_json()
        back = ws.receive_json()

    assert back["tipo"] == "estado"
    assert back["datos"]["pantalla"] == "espera"
    assert back["datos"]["ultimoError"]["error"] == "perfil_requerido"
    assert event == {
        "tipo": "error",
        "datos": {"error": "perfil_requerido", "mensaje": "Elige el perfil de Netflix en el PC."},
    }


def test_websocket_sin_token(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws?token=malo") as ws:
        ws.receive_json()


# --- Tele (/tv) -------------------------------------------------------------------


LOCAL = ("127.0.0.1", 50000)


def test_tv_solo_desde_el_propio_pc(session: GameSession) -> None:
    local = make_client(session, LOCAL).get("/tv")
    assert local.status_code == 200
    assert local.headers["content-type"].startswith("text/html")
    assert "/static/tv/tv.js" in local.text

    remote = make_client(session).get("/tv")
    assert remote.status_code == 403
    assert remote.json()["error"] == "solo_local"


def test_estaticos_de_la_web(client: TestClient) -> None:
    css = client.get("/static/system.css")
    assert css.status_code == 200
    assert "tokens:start" in css.text
    assert client.get("/static/tv/textos.js").status_code == 200
    assert client.get("/static/no-existe.css").status_code == 404


def test_websocket_de_la_tele_sin_token(session: GameSession) -> None:
    with make_client(session, LOCAL).websocket_connect("/tv/ws") as ws:
        assert ws.receive_json()["datos"]["pantalla"] == "espera"
        assert ws.receive_json()["tipo"] == "marcador"


def test_websocket_de_la_tele_rechaza_la_red_local(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/tv/ws") as ws:
        ws.receive_json()


def test_play_fallido_vuelve_a_la_espera(client: TestClient, player: FakePlayer) -> None:
    client.post("/play", json={"netflixId": 70131314, "seconds": 60}, headers=HEADERS)
    player.fail = PlayerError("no_disponible_es", "No está disponible aquí.")

    assert (
        client.post(
            "/play", json={"netflixId": 70131314, "seconds": 60}, headers=HEADERS
        ).status_code
        == 409
    )

    status = client.get("/status", headers=HEADERS).json()
    assert status["pantalla"] == "espera"
    assert status["rondaActual"] is None
    assert player.calls[-1] == ("idle", None)


def test_revelar_usa_poster_grande(client: TestClient, player: FakePlayer) -> None:
    client.post("/play", json={"netflixId": 70131314, "seconds": 60}, headers=HEADERS)
    client.post("/reveal", headers=HEADERS)

    info = player.calls[-1][1]
    assert isinstance(info, RevealInfo)
    assert info.title == "Origen"
    assert info.original_title == "Inception"
    assert info.poster_url is not None and "/w780/" in info.poster_url


# --- Móvil (/m/) ------------------------------------------------------------------


def test_movil_se_sirve_en_la_red_local(client: TestClient) -> None:
    redirect = client.get("/m", follow_redirects=False)
    assert redirect.status_code in (307, 308)
    assert redirect.headers["location"].endswith("/m/")

    page = client.get("/m/")
    assert page.status_code == 200
    assert '<script type="module" src="app.js">' in page.text
    for name in ("app.js", "api.js", "wheel.js", "textos.js", "app.css"):
        assert client.get(f"/m/{name}").status_code == 200, name


def test_renombrar_jugador(client: TestClient) -> None:
    ana = client.post("/players", json={"nombre": "Ana"}, headers=HEADERS).json()["jugadores"][0]
    client.post("/players", json={"nombre": "Luis"}, headers=HEADERS)

    ok = client.patch(f"/players/{ana['id']}", json={"nombre": "  Ana  María "}, headers=HEADERS)
    assert ok.status_code == 200
    assert {p["nombre"] for p in ok.json()["jugadores"]} == {"Ana María", "Luis"}

    repeated = client.patch(f"/players/{ana['id']}", json={"nombre": "luis"}, headers=HEADERS)
    assert repeated.status_code == 409
    assert repeated.json()["error"] == "nombre_repetido"

    same = client.patch(f"/players/{ana['id']}", json={"nombre": "ANA MARÍA"}, headers=HEADERS)
    assert same.status_code == 200

    missing = client.patch("/players/nadie", json={"nombre": "X"}, headers=HEADERS)
    assert missing.status_code == 404
