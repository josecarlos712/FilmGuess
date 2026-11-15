"""Pistas: lectura de TMDB, Wikiquote y críticas, título tapado y reglas de la ronda."""

from __future__ import annotations

import random

import pytest

from filmguess.catalog.clues import (
    MASK,
    Clues,
    Genre,
    clean_wikitext,
    extract_quote,
    parse_tmdb_clues,
    redact,
    review_excerpt,
    title_words,
)
from filmguess.catalog.clues_fetch import parse_sparql_pages, parse_wikiquote_pages
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Movie
from filmguess.game.hints import (
    CHARACTER_IN_TITLE,
    build_hints,
    random_moment,
    round_value,
)

WIKITEXT = """{{italic title}}
'''''[[w:Inception|Inception]]''''' is a 2010 film about Cobb.
* An intro bullet that should be ignored because it is before a heading.

== Cobb ==
* Short.
* An idea is like a virus. Resilient. Highly contagious.<ref>Guion</ref>
** Attribution line that is ignored.

== Taglines ==
* Your mind is the scene of the crime, a long tagline that must be skipped.
"""

ORIGEN = Movie(1, "Inception", "Origen", "Inception", 2010, 8880, imdb_rating=8.8)
CLUES = Clues(
    [Genre(878, "Ciencia ficción"), Genre(28, "Acción")],
    "Leonardo DiCaprio",
    "Cobb",
    "An idea is like a virus.",
    "wikiquote",
    "en",
)


def test_parse_tmdb_clues_toma_el_primero_del_reparto() -> None:
    details = {
        "genres": [{"id": 878, "name": "Ciencia ficción"}],
        "tagline": " Tu mente es la escena del crimen ",
        "credits": {
            "cast": [
                {"name": "Joseph Gordon-Levitt", "character": "Arthur", "order": 1},
                {"name": "Leonardo DiCaprio", "character": "Cobb / Dom (voice)", "order": 0},
            ]
        },
    }

    clues = parse_tmdb_clues(details)

    assert clues.genres == [Genre(878, "Ciencia ficción")]
    assert (clues.actor, clues.character) == ("Leonardo DiCaprio", "Cobb")
    assert clues.tagline == "Tu mente es la escena del crimen"


def test_extract_quote_salta_intro_atribuciones_y_esloganes() -> None:
    quote = extract_quote(WIKITEXT, title_words(["Origen", "Inception"]))

    assert quote == "An idea is like a virus. Resilient. Highly contagious."


def test_clean_wikitext_quita_enlaces_negritas_y_acotaciones() -> None:
    text = clean_wikitext("''[se ríe]'' Ve a [[Madrid|la capital]] '''ya''' &ndash; dijo")

    assert text == "Ve a la capital ya " + chr(0x2013) + " dijo"


def test_redact_tapa_el_titulo_sin_acentos_ni_mayusculas() -> None:
    words = title_words(["El viaje de Chihiro", "千と千尋の神隠し"])

    assert redact("Bienvenida a el viaje de chihiro, dijo Chíhiro", words) == (
        f"Bienvenida a {MASK}, dijo {MASK}"
    )


def test_redact_no_tapa_palabras_corrientes_en_minuscula() -> None:
    words = title_words(["Good Will Hunting"])

    assert redact("I will end you, Will.", words) == f"I will end you, {MASK}."


def test_review_excerpt_corta_en_una_frase() -> None:
    reviews = {"results": [{"content": "Great film about dreams. " * 20}]}

    excerpt = review_excerpt(reviews, title_words(["Origen"]))

    assert excerpt is not None and excerpt.endswith(".") and len(excerpt) <= 240


def test_parse_sparql_pages() -> None:
    data = {
        "results": {
            "bindings": [
                {
                    "tmdb": {"value": "27205"},
                    "page": {"value": "https://en.wikiquote.org/wiki/Inception_(film)"},
                },
                {
                    "tmdb": {"value": "13"},
                    "page": {"value": "https://es.wikiquote.org/wiki/Forrest_Gump"},
                },
            ]
        }
    }

    assert parse_sparql_pages(data) == {
        27205: {"en": "Inception (film)"},
        13: {"es": "Forrest Gump"},
    }


def test_parse_wikiquote_pages_sigue_redirecciones() -> None:
    data = {
        "query": {
            "normalized": [{"from": "inception (film)", "to": "Inception (film)"}],
            "redirects": [{"from": "Inception (film)", "to": "Inception"}],
            "pages": [{"title": "Inception", "revisions": [{"slots": {"main": {"content": "x"}}}]}],
        }
    }

    pages = parse_wikiquote_pages(data)

    assert pages["inception (film)"] == "x" and pages["Inception (film)"] == "x"


def test_db_guarda_pistas_y_filtra_por_genero() -> None:
    db = CatalogDB(":memory:")
    try:
        db._conn.execute(
            "INSERT INTO movies(netflix_id, csv_title, netflix_es, match_conf, year, tmdb_id) "
            "VALUES (1, 'Inception', 1, 'alta', 2010, 27205), (2, 'Otra', 1, 'alta', 2001, 9)"
        )
        db.save_clues(1, CLUES)
        db.save_clues(2, Clues([Genre(18, "Drama")]))

        assert db.clues(1) == CLUES
        assert [m.netflix_id for m in db.random_candidates(min_year=1991, genre_id=878)] == [1]
        assert [g.name for g, _ in db.genre_counts()] == ["Acción", "Ciencia ficción", "Drama"]
        assert db.clue_targets() == []
        assert len(db.clue_targets(refresh=True)) == 2
    finally:
        db.close()


# --- Reglas de la ronda -------------------------------------------------------------


def test_cinco_pistas_sin_categoria() -> None:
    hints = build_hints(ORIGEN, CLUES, category=False)

    assert [h.kind for h in hints] == ["genero", "anio_nota", "actor", "personaje", "frase"]
    assert hints[0].text == "Ciencia ficción · Acción"
    assert hints[1].text == "2010 · IMDb 8,8"
    assert hints[4].label == "Una frase de la película (en inglés)"


def test_con_categoria_actor_y_personaje_van_juntos() -> None:
    hints = build_hints(ORIGEN, CLUES, category=True)

    assert [h.kind for h in hints] == ["anio_nota", "reparto", "frase"]
    assert hints[1].text == "Leonardo DiCaprio, como Cobb"


def test_personaje_que_es_el_titulo() -> None:
    clues = Clues(actor="Tom Hanks", character=MASK)

    hints = build_hints(ORIGEN, clues, category=False)

    assert hints[-1].text == CHARACTER_IN_TITLE


def test_sin_pistas_descargadas_queda_el_anio() -> None:
    assert [h.kind for h in build_hints(ORIGEN, None, category=False)] == ["anio_nota"]


@pytest.mark.parametrize(
    ("revealed", "category", "value"),
    [(0, False, 5), (1, False, 4), (4, False, 1), (5, False, 1), (0, True, 3), (2, True, 1)],
)
def test_valor_de_la_ronda(revealed: int, category: bool, value: int) -> None:
    assert round_value(revealed, category=category) == value


def test_momento_al_azar_lejos_de_los_vistos_y_de_los_creditos() -> None:
    rng = random.Random(4)
    for _ in range(50):
        moment = random_moment(6000, [3000], rng)
        assert 300 <= moment <= 5400
        assert abs(moment - 3000) >= 600


def test_extract_quote_conserva_quien_habla() -> None:
    wikitext = "== Dialogue ==\n* Colonel Bagley: What is it about your own people that you hate?\n"

    quote = extract_quote(wikitext, title_words(["El último samurái"]))

    assert quote == "Colonel Bagley: What is it about your own people that you hate?"
