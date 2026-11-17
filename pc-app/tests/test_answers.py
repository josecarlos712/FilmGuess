"""Comprobación de los títulos escritos en el móvil."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filmguess.catalog.aliases import (
    AliasError,
    is_latin,
    load_manual_aliases,
    parse_tmdb_aliases,
)
from filmguess.game.answers import (
    TitleIndex,
    answer_key,
    check_answer,
    edit_distance,
    title_forms,
)

INTERSTELLAR = (1, ["Interstellar", "Interstellar", "Interstellar"])
PADRINO = (2, ["El padrino", "The Godfather"])
PADRINO_2 = (3, ["El padrino II", "The Godfather Part II"])
SPIDERMAN = (4, ["Spider-Man", "Spider-Man"])
SPIDERMAN_NWH = (5, ["Spider-Man: No Way Home", "Spider-Man: No Way Home"])
ALIEN = (6, ["Alien, el octavo pasajero", "Alien"])
ALIENS = (7, ["Aliens: El regreso", "Aliens"])
CLUB = (8, ["El club de la lucha", "Fight Club"])
JOKER_2 = (9, ["Joker: Folie à Deux", "Joker: Folie à Deux"])
JOKER = (10, ["Joker", "Joker"])
BLADE = (11, ["Blade Runner 2049", "Blade Runner 2049"])
ORIGEN = (12, ["Origen", "Inception"])
SENOR = (13, ["El señor de los anillos: la comunidad del anillo"])
ALL = [
    INTERSTELLAR, PADRINO, PADRINO_2, SPIDERMAN, SPIDERMAN_NWH, ALIEN, ALIENS,
    CLUB, JOKER_2, JOKER, BLADE, ORIGEN, SENOR,
]  # fmt: skip


@pytest.fixture(scope="module")
def index() -> TitleIndex:
    return TitleIndex(ALL)


def verdict(movie: tuple[int, list[str]], guess: str, index: TitleIndex) -> str:
    return check_answer(guess, movie[0], movie[1], index).verdict


@pytest.mark.parametrize(
    ("movie", "guess"),
    [
        (INTERSTELLAR, "interstellar"),
        (INTERSTELLAR, "INTERSTELLAR"),
        (INTERSTELLAR, "Interestelar"),
        (INTERSTELLAR, "intersteller"),
        (INTERSTELLAR, "Insterstelar"),
        (SPIDERMAN, "spiderman"),
        (SPIDERMAN, "spider man"),
        (SPIDERMAN, "Espiderman"),
        (SPIDERMAN_NWH, "spiderman no way home"),
        (PADRINO, "padrino"),
        (PADRINO, "el pdrino"),
        (PADRINO_2, "el padrino 2"),
        (PADRINO_2, "El Padrino parte II"),
        (PADRINO_2, "el padrino dos"),
        (PADRINO_2, "the godfather part 2"),
        (CLUB, "el club de lucha"),
        (CLUB, "fight club"),
        (CLUB, "el clu de la lucha"),
        (JOKER, "jocker"),
        (JOKER_2, "joker folie a deux"),
        (ORIGEN, "orijen"),
        (ORIGEN, "inception"),
        (ORIGEN, "inseption"),
        (BLADE, "blade runner 2049"),
        (SENOR, "el senor de los anillos la comunidad del anillo"),
        (SENOR, "el señor de los anillos"),
        (SENOR, "El Señor De Los Anillos: La Comunidad Del Anillo"),
    ],
)
def test_accepts_variants(movie: tuple[int, list[str]], guess: str, index: TitleIndex) -> None:
    assert verdict(movie, guess, index) == "acierto"


@pytest.mark.parametrize(
    ("movie", "guess"),
    [
        (PADRINO, "el padrino 2"),  # es otra película de la saga
        (PADRINO_2, "el padrino"),
        (ALIENS, "alien"),  # existe «Alien»
        (JOKER_2, "joker"),  # existe «Joker»: sin subtítulo no basta
        (SPIDERMAN_NWH, "spiderman"),
        (BLADE, "blade runner 2048"),
        (ORIGEN, "interstellar"),
        (ORIGEN, "titanic"),
        (ORIGEN, "   "),
    ],
)
def test_rejects_other_movies(movie: tuple[int, list[str]], guess: str, index: TitleIndex) -> None:
    assert verdict(movie, guess, index) == "fallo"


@pytest.mark.parametrize(
    ("movie", "guess"),
    [
        (CLUB, "club lucha"),
        (INTERSTELLAR, "interestelaria"),
    ],
)
def test_near_misses(movie: tuple[int, list[str]], guess: str, index: TitleIndex) -> None:
    assert verdict(movie, guess, index) == "casi"


def test_matched_title_is_reported(index: TitleIndex) -> None:
    check = check_answer("fight club", CLUB[0], CLUB[1], index)
    assert check.matched == "Fight Club"


def test_without_index_still_checks_spelling() -> None:
    assert check_answer("orijen", 12, ["Origen"]).verdict == "acierto"


def test_answer_key_spanish_spelling() -> None:
    assert answer_key("Vaya hierba") == answer_key("baya ierva")
    assert answer_key("Cazadores") == answer_key("kasadores")
    assert answer_key("Gigante") == answer_key("jigante")
    assert answer_key("Spider-Man") == answer_key("spider man")
    assert answer_key("Ocean's Eleven") == answer_key("oceans eleven")


def test_title_forms() -> None:
    full, short = title_forms("Joker: Folie à Deux")
    assert full == ["Joker: Folie à Deux"]
    assert short == ["Joker"]
    full, short = title_forms("Mi vecino Totoro (Tonari no Totoro)")
    assert "Mi vecino Totoro" in full


def test_edit_distance() -> None:
    assert edit_distance("abc", "abc") == 0
    assert edit_distance("abcd", "abdc") == 1  # transposición
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("a", "abcdef", limit=2) == 3


def test_parse_tmdb_aliases() -> None:
    data = {
        "alternative_titles": {
            "titles": [
                {"iso_3166_1": "MX", "title": "Interestelar"},
                {"iso_3166_1": "JP", "title": "インターステラー"},
                {"iso_3166_1": "FR", "title": "Interstellaire"},
            ]
        },
        "translations": {
            "translations": [
                {"iso_639_1": "es", "data": {"title": "Interestelar"}},
                {"iso_639_1": "en", "data": {"title": ""}},
                {"iso_639_1": "ru", "data": {"title": "Интерстеллар"}},
            ]
        },
    }
    assert parse_tmdb_aliases(data) == ["Interestelar"]


def test_is_latin() -> None:
    assert is_latin("Señor de los anillos")
    assert not is_latin("Интерстеллар")


def test_manual_aliases(tmp_path: Path) -> None:
    path = tmp_path / "alias.json"
    path.write_text(json.dumps({"_nota": "x", "70305903": ["Interestelar", " "]}), "utf-8")
    assert load_manual_aliases(path) == {70305903: ["Interestelar"]}
    assert load_manual_aliases(tmp_path / "no.json") == {}
    path.write_text(json.dumps({"abc": ["x"]}), "utf-8")
    with pytest.raises(AliasError):
        load_manual_aliases(path)
