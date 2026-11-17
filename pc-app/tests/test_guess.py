# ruff: noqa: F811, F401
"""Cada jugador escribe el título en su móvil: POST /guess, invitación y títulos alternativos."""

from __future__ import annotations

from fastapi.testclient import TestClient

from filmguess.catalog.db import CatalogDB
from filmguess.game.answers import check_answer
from tests.test_rounds import ORIGEN, catalog, client, player, ronda, session
from tests.test_server import HEADERS, TOKEN


def jugadores(client: TestClient, *names: str) -> list[str]:
    ids = []
    for name in names:
        data = client.post("/players", json={"nombre": name}, headers=HEADERS).json()
        ids.append(next(j["id"] for j in data["jugadores"] if j["nombre"] == name))
    return ids


def guess(client: TestClient, player_id: str, text: str):  # type: ignore[no-untyped-def]
    return client.post("/guess", json={"jugador": player_id, "texto": text}, headers=HEADERS)


def puntos(client: TestClient) -> dict[str, int]:
    data = client.get("/players", headers=HEADERS).json()["jugadores"]
    return {j["id"]: j["puntos"] for j in data}


def jugar(client: TestClient) -> None:
    client.post("/play", json={"netflixId": ORIGEN, "seconds": 600}, headers=HEADERS)


def test_sin_ronda(client: TestClient) -> None:
    (a,) = jugadores(client, "A")

    data = guess(client, a, "origen").json()

    assert data["ok"] is False
    assert data["error"] == "sin_ronda"


def test_acierto_y_no_repite(client: TestClient) -> None:
    a, _ = jugadores(client, "A", "B")
    jugar(client)

    data = guess(client, a, "orijen").json()

    assert data["ok"] is True
    assert data["resultado"] == "acierto"
    assert data["puntos"] == 5
    assert data["titulo"] == "Origen"
    assert puntos(client)[a] == 5
    assert a in ronda(client)["acertantes"]  # type: ignore[operator]
    again = guess(client, a, "origen")
    assert again.status_code == 409
    assert again.json()["error"] == "ya_acertado"


def test_fallo_resta_y_no_repite(client: TestClient) -> None:
    _, b = jugadores(client, "A", "B")
    jugar(client)

    data = guess(client, b, "Titanic").json()

    assert data["resultado"] == "fallo"
    assert data["puntos"] == -5
    assert puntos(client)[b] == -5
    assert b in ronda(client)["fallos"]  # type: ignore[operator]
    again = guess(client, b, "origen")
    assert again.status_code == 409
    assert again.json()["error"] == "fuera_de_ronda"


def test_casi_no_cambia_el_marcador(client: TestClient) -> None:
    (a,) = jugadores(client, "A")
    jugar(client)
    assert check_answer("origenes", ORIGEN, ["Origen", "Inception"]).verdict == "casi"

    data = guess(client, a, "origenes").json()

    assert data["resultado"] == "casi"
    assert puntos(client)[a] == 0
    assert guess(client, a, "origen").json()["resultado"] == "acierto"


def test_titulo_alternativo(client: TestClient, catalog: CatalogDB) -> None:
    catalog.save_aliases(ORIGEN, ["Inception: El origen"])
    (a,) = jugadores(client, "A")
    jugar(client)

    assert guess(client, a, "inception el origen").json()["resultado"] == "acierto"


def test_ronda_cerrada(client: TestClient) -> None:
    (a,) = jugadores(client, "A")
    jugar(client)
    client.post("/reveal", headers=HEADERS)

    response = guess(client, a, "origen")

    assert response.status_code == 409
    assert response.json()["error"] == "ronda_cerrada"


def test_jugador_desconocido(client: TestClient) -> None:
    jugar(client)

    assert guess(client, "nadie", "origen").json()["error"] == "jugador_no_encontrado"


def test_invitacion(client: TestClient) -> None:
    url = client.get("/invite", headers=HEADERS).json()["url"]
    assert "/m/jugar.html#token=" in url

    svg = client.get("/invite.svg", headers=HEADERS)
    assert svg.headers["content-type"].startswith("image/svg+xml")

    assert client.get("/invite").status_code == 401
    assert client.get("/invite.svg").status_code == 401
    assert TOKEN in url


def test_db_alias(catalog: CatalogDB) -> None:
    assert ORIGEN in {t.netflix_id for t in catalog.alias_targets()}

    catalog.save_aliases(ORIGEN, ["Uno", "Dos"])
    catalog.save_aliases(ORIGEN, ["Tres"])

    assert catalog.aliases(ORIGEN) == ["Tres"]
    assert ORIGEN not in {t.netflix_id for t in catalog.alias_targets()}
    assert ORIGEN in {t.netflix_id for t in catalog.alias_targets(refresh=True)}
    assert "Tres" in dict(catalog.all_titles())[ORIGEN]
