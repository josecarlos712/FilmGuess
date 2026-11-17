"""Tests de los packs: almacén, elección al azar, editor y rutas del juego."""

from __future__ import annotations

import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from filmguess.catalog.clues import Clues, Genre
from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.random_pick import pick_from_pack
from filmguess.config import RandomConfig
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.packs.app import create_packs_app
from filmguess.packs.store import Pack, PackError, PackStore, build_pack, slugify
from filmguess.server.app import create_app
from tests.conftest import make_match
from tests.test_server import HEADERS, LAN, TOKEN, FakePlayer

LOCAL = ("127.0.0.1", 50000)
EDIT = {"X-Packs": "1"}
CONFIG = RandomConfig(min_year=1991, min_votes=1000, pool_size=10, no_repeat=0)
ACTION = Genre(28, "Acción")


@pytest.fixture
def catalog() -> Iterator[CatalogDB]:
    db = CatalogDB(":memory:")
    db.add_csv_movies([CsvMovie(i, f"Peli {i}") for i in range(1, 7)])
    for i in range(1, 7):
        db.save_match(i, make_match(100 + i, title_es=f"Peli {i}", vote_count=1000 * i))
    db.set_netflix_es(6, False)
    db.save_clues(1, Clues([ACTION], None, None, None, None, None))
    yield db
    db.close()


@pytest.fixture
def store(tmp_path: Path) -> PackStore:
    return PackStore(tmp_path / "packs")


# --- Almacén ----------------------------------------------------------------------


def test_slugify() -> None:
    assert slugify("  Noche de Pelis: ¡Acción! ") == "noche-de-pelis-accion"
    assert slugify("¿¡!?") == ""


def test_create_save_and_read(store: PackStore) -> None:
    pack = store.create("Los Clásicos")
    assert pack.slug == "los-clasicos"
    store.save(build_pack(pack.slug, "Los Clásicos", [1, 2, 2], [2, 3], 0.5))
    saved = store.get("los-clasicos")
    # Sin repetidos, y la que está en las dos listas cuenta como conocida.
    assert saved == Pack("los-clasicos", "Los Clásicos", (1, 2), (3,), 0.5)
    assert [p.slug for p in store.list()] == ["los-clasicos"]


def test_create_rejects_duplicates_and_empty_names(store: PackStore) -> None:
    store.create("Verano")
    with pytest.raises(PackError) as dup:
        store.create("verano")
    assert dup.value.code == "pack_repetido"
    with pytest.raises(PackError) as empty:
        store.create("!!!")
    assert empty.value.code == "pack_invalido"


@pytest.mark.parametrize("slug", ["../config", "a/b", "A", "", "x" * 61, "con espacio"])
def test_unsafe_slugs_are_not_found(store: PackStore, slug: str) -> None:
    with pytest.raises(PackError) as exc:
        store.get(slug)
    assert exc.value.code == "pack_no_encontrado"


def test_build_pack_validation() -> None:
    with pytest.raises(PackError):
        build_pack("ok", "Nombre", [0], [])
    with pytest.raises(PackError):
        build_pack("ok", "Nombre", [True], [])  # type: ignore[list-item]
    with pytest.raises(PackError):
        build_pack("ok", "Nombre", [], [], 1.5)
    with pytest.raises(PackError):
        build_pack("ok", "   ", [], [])


def test_list_skips_damaged_files(store: PackStore) -> None:
    store.create("Bueno")
    (store.directory / "roto.json").write_text("{no es json", encoding="utf-8")
    (store.directory / "lista.json").write_text("[1, 2]", encoding="utf-8")
    assert [p.slug for p in store.list()] == ["bueno"]
    with pytest.raises(PackError) as exc:
        store.get("roto")
    assert exc.value.code == "pack_invalido"


def test_delete(store: PackStore) -> None:
    store.create("Fuera")
    store.delete("fuera")
    assert store.list() == []
    with pytest.raises(PackError):
        store.delete("fuera")


# --- Catálogo ---------------------------------------------------------------------


def test_popular_orders_by_votes_and_skips_unavailable(catalog: CatalogDB) -> None:
    movies, total = catalog.popular(limit=3)
    assert total == 5
    assert [m.netflix_id for m in movies] == [5, 4, 3]
    assert [m.netflix_id for m in catalog.popular(limit=3, offset=3)[0]] == [2, 1]
    assert [m.netflix_id for m in catalog.popular(limit=9, genre_id=28)[0]] == [1]


def test_get_many_keeps_order_and_filters_genre(catalog: CatalogDB) -> None:
    assert [m.netflix_id for m in catalog.get_many([3, 99, 1])] == [3, 1]
    assert [m.netflix_id for m in catalog.get_many([3, 1], genre_id=28)] == [1]
    assert catalog.get_many([]) == []


# --- Elección al azar -------------------------------------------------------------


def test_pick_respects_the_mix(catalog: CatalogDB) -> None:
    rng = random.Random(7)
    only_known = Pack("p", "P", (1, 2), (3, 4), 1.0)
    only_unknown = Pack("p", "P", (1, 2), (3, 4), 0.0)
    for _ in range(20):
        assert pick_from_pack(catalog, only_known, CONFIG, rng).netflix_id in (1, 2)  # type: ignore[union-attr]
        assert pick_from_pack(catalog, only_unknown, CONFIG, rng).netflix_id in (3, 4)  # type: ignore[union-attr]
    mixed = Pack("p", "P", (1, 2), (3, 4), 0.7)
    picks = [pick_from_pack(catalog, mixed, CONFIG, rng).netflix_id for _ in range(400)]  # type: ignore[union-attr]
    known_share = sum(p in (1, 2) for p in picks) / len(picks)
    assert 0.6 < known_share < 0.8


def test_pick_uses_the_other_list_when_one_is_empty(catalog: CatalogDB) -> None:
    pack = Pack("p", "P", (), (3,), 1.0)
    movie = pick_from_pack(catalog, pack, CONFIG, random.Random(1))
    assert movie is not None and movie.netflix_id == 3


def test_pick_does_not_repeat_until_the_pack_runs_out(catalog: CatalogDB) -> None:
    config = RandomConfig(min_year=1991, min_votes=1000, pool_size=10, no_repeat=50)
    pack = Pack("p", "P", (1, 2), (3, 4), 0.5)
    rng = random.Random(3)
    picks = [pick_from_pack(catalog, pack, config, rng).netflix_id for _ in range(8)]  # type: ignore[union-attr]
    assert sorted(picks[:4]) == [1, 2, 3, 4]
    # Con el pack agotado, la más antigua vuelve a estar disponible.
    assert picks[4] == picks[0]


def test_pick_skips_missing_unavailable_and_other_genres(catalog: CatalogDB) -> None:
    rng = random.Random(1)
    assert pick_from_pack(catalog, Pack("p", "P", (6, 99), ()), CONFIG, rng) is None
    assert pick_from_pack(catalog, Pack("p", "P", (), ()), CONFIG, rng) is None
    by_genre = pick_from_pack(catalog, Pack("p", "P", (2,), (1,), 1.0), CONFIG, rng, genre_id=28)
    assert by_genre is not None and by_genre.netflix_id == 1


# --- Editor -----------------------------------------------------------------------


@pytest.fixture
def editor(catalog: CatalogDB, store: PackStore) -> TestClient:
    return TestClient(create_packs_app(catalog, store), client=LOCAL)


def test_editor_full_flow(editor: TestClient, store: PackStore) -> None:
    assert editor.get("/api/packs").json() == {"packs": []}
    created = editor.post("/api/packs", json={"nombre": "Noche de pelis"}, headers=EDIT).json()
    assert created["id"] == "noche-de-pelis" and created["peliculas"] == []

    body = {
        "nombre": "Noche de cine",
        "conocidas": [5, 1],
        "desconocidas": [3],
        "proporcionConocidas": 0.8,
    }
    saved = editor.put("/api/packs/noche-de-pelis", json=body, headers=EDIT)
    assert saved.status_code == 200
    assert saved.json() == {
        "id": "noche-de-pelis",
        "nombre": "Noche de cine",
        "conocidas": 2,
        "desconocidas": 1,
        "proporcionConocidas": 0.8,
    }
    assert store.get("noche-de-pelis").known == (5, 1)

    detail = editor.get("/api/packs/noche-de-pelis").json()
    assert [(m["netflixId"], m["conocida"]) for m in detail["peliculas"]] == [
        (5, True),
        (1, True),
        (3, False),
    ]
    assert detail["peliculas"][0]["titulo"] == "Peli 5"

    assert editor.delete("/api/packs/noche-de-pelis", headers=EDIT).json() == {"ok": True}
    assert editor.get("/api/packs/noche-de-pelis").status_code == 404


def test_editor_errors(editor: TestClient) -> None:
    editor.post("/api/packs", json={"nombre": "Uno"}, headers=EDIT)
    again = editor.post("/api/packs", json={"nombre": "uno"}, headers=EDIT)
    assert again.status_code == 409 and again.json()["error"] == "pack_repetido"
    body = {"nombre": "X", "conocidas": [], "desconocidas": []}
    assert editor.put("/api/packs/no-existe", json=body, headers=EDIT).status_code == 404
    bad = editor.put("/api/packs/uno", json=body | {"conocidas": [-1]}, headers=EDIT)
    assert bad.status_code == 422 and bad.json()["error"] == "pack_invalido"
    assert editor.post("/api/packs", json={}, headers=EDIT).json()["error"] == "peticion_invalida"


def test_editor_writes_need_the_editor_header(editor: TestClient, store: PackStore) -> None:
    response = editor.post("/api/packs", json={"nombre": "Colado"})
    assert response.status_code == 403
    assert store.list() == []


def test_editor_is_local_only(catalog: CatalogDB, store: PackStore) -> None:
    remote = TestClient(create_packs_app(catalog, store), client=LAN)
    assert remote.get("/api/packs").status_code == 403
    assert remote.get("/").status_code == 403


def test_editor_movies(editor: TestClient) -> None:
    page = editor.get("/api/peliculas").json()
    assert page["total"] == 5
    assert [m["netflixId"] for m in page["resultados"]] == [5, 4, 3, 2, 1]
    assert [m["netflixId"] for m in editor.get("/api/peliculas?q=peli 3").json()["resultados"]] == [
        3
    ]
    assert editor.get("/api/peliculas?categoria=28").json()["total"] == 1
    assert editor.get("/api/categorias").json() == {
        "categorias": [{"id": 28, "nombre": "Acción", "peliculas": 1}]
    }


def test_editor_serves_the_page(editor: TestClient) -> None:
    page = editor.get("/")
    assert page.status_code == 200 and "packs.js" in page.text
    assert editor.get("/static/packs/packs.js").status_code == 200


# --- Juego ------------------------------------------------------------------------


@pytest.fixture
def game(catalog: CatalogDB, store: PackStore, tmp_path: Path) -> TestClient:
    session = GameSession(
        catalog, FakePlayer(), Scoreboard(tmp_path / "scoreboard.json"), CONFIG, packs=store
    )
    return TestClient(create_app(session, TOKEN, port=8765, ip="192.168.1.10"), client=LAN)


def test_game_lists_only_packs_with_movies(game: TestClient, store: PackStore) -> None:
    store.create("Vacío")
    store.save(build_pack("lleno", "Lleno", [2], [4]))
    assert game.get("/packs", headers=HEADERS).json() == {
        "packs": [
            {
                "id": "lleno",
                "nombre": "Lleno",
                "conocidas": 1,
                "desconocidas": 1,
                "proporcionConocidas": 0.7,
            }
        ]
    }
    assert game.get("/packs").status_code == 401


def test_game_random_from_pack(game: TestClient, store: PackStore) -> None:
    store.save(build_pack("solo-dos", "Solo dos", [2], []))
    for _ in range(5):
        assert game.get("/random?pack=solo-dos", headers=HEADERS).json()["netflixId"] == 2


def test_game_random_pack_errors(game: TestClient, store: PackStore) -> None:
    missing = game.get("/random?pack=no-existe", headers=HEADERS)
    assert missing.status_code == 404 and missing.json()["error"] == "pack_no_encontrado"
    store.save(build_pack("sin-accion", "Sin acción", [2], []))
    empty = game.get("/random?pack=sin-accion&categoria=28", headers=HEADERS)
    assert empty.status_code == 409 and empty.json()["error"] == "sin_candidatas"
    assert "Sin acción" in empty.json()["mensaje"]


def test_game_serves_the_editor_on_the_pc_only(
    catalog: CatalogDB, store: PackStore, tmp_path: Path
) -> None:
    session = GameSession(
        catalog, FakePlayer(), Scoreboard(tmp_path / "s.json"), CONFIG, packs=store
    )
    app = create_app(session, TOKEN, port=8765, ip="192.168.1.10")
    pc = TestClient(app, client=LOCAL)
    page = pc.get("/editor-packs/")
    assert page.status_code == 200 and "packs.js" in page.text
    assert pc.get("/editor-packs", follow_redirects=False).status_code in (307, 308)
    created = pc.post("/editor-packs/api/packs", json={"nombre": "Desde el PC"}, headers=EDIT)
    assert created.json()["id"] == "desde-el-pc"
    saved = pc.put(
        "/editor-packs/api/packs/desde-el-pc",
        json={"nombre": "Desde el PC", "conocidas": [2], "desconocidas": []},
        headers=EDIT,
    )
    assert saved.status_code == 200
    # El pack recién guardado ya se puede jugar, sin reiniciar nada.
    assert pc.get("/random?pack=desde-el-pc", headers=HEADERS).json()["netflixId"] == 2
    assert pc.get("/editor-packs/api/peliculas").json()["total"] == 5
    # La pantalla de espera enlaza al editor en otra pestaña (esa la maneja el juego).
    tv = pc.get("/tv").text
    assert 'href="/editor-packs/" target="_blank"' in tv
    # Desde el móvil (otra máquina de la red) no se abre ni se cambia nada.
    phone = TestClient(app, client=LAN)
    assert phone.get("/editor-packs/").status_code == 403
    assert phone.get("/editor-packs/api/packs").status_code == 403


def test_game_without_packs(catalog: CatalogDB, tmp_path: Path) -> None:
    session = GameSession(catalog, FakePlayer(), Scoreboard(tmp_path / "s.json"), CONFIG)
    client = TestClient(create_app(session, TOKEN, port=8765, ip="192.168.1.10"), client=LAN)
    assert client.get("/packs", headers=HEADERS).json() == {"packs": []}
    assert client.get("/random?pack=x", headers=HEADERS).status_code == 404
