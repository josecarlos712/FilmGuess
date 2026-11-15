"""Cuerpos de petición y conversión de los objetos de negocio al JSON de la API.

Las claves del JSON van en español (``netflixId``, ``titulo``...), como en docs/02.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from filmguess.catalog.clues import Genre
from filmguess.catalog.models import Movie
from filmguess.catalog.search import SearchPage
from filmguess.catalog.tmdb import poster_url
from filmguess.game.hints import MAX_FRAMES
from filmguess.game.scoreboard import Player
from filmguess.game.session import SessionState


class PlayRequest(BaseModel):
    """Cuerpo de ``POST /play``."""

    netflixId: int = Field(gt=0)
    seconds: int = Field(ge=0)
    categoria: int | None = Field(default=None, gt=0)
    """Género elegido: la ronda vale como máximo 3 puntos."""


class AwardRequest(BaseModel):
    """Cuerpo de ``POST /award`` (quienes han acertado) y ``POST /fail`` (quienes fallan)."""

    jugadores: list[str] = Field(min_length=1, max_length=20)


class NewPlayerRequest(BaseModel):
    """Cuerpo de ``POST /players``."""

    nombre: str = Field(min_length=1, max_length=60)


class PointsRequest(BaseModel):
    """Cuerpo de ``POST /players/{id}/points``."""

    puntos: int = Field(ge=-1000, le=1000)


def movie_summary(movie: Movie) -> dict[str, Any]:
    """Resultado de búsqueda.

    Args:
        movie: Película.

    Returns:
        Datos para la lista.
    """
    return {
        "netflixId": movie.netflix_id,
        "titulo": movie.display_title,
        "tituloOriginal": movie.original_title,
        "anio": movie.year,
        "duracionSeg": movie.runtime_s,
        "poster": poster_url(movie.poster_path),
    }


def movie_detail(movie: Movie) -> dict[str, Any]:
    """Ficha completa.

    Args:
        movie: Película.

    Returns:
        Todos los datos que se muestran.
    """
    return movie_summary(movie) | {
        "posterGrande": poster_url(movie.poster_path, "w500"),
        "notaImdb": movie.imdb_rating,
        "votosImdb": movie.imdb_votes,
        "disponibleEs": movie.netflix_es,
        "confianza": movie.match_conf,
    }


def review_item(movie: Movie) -> dict[str, Any]:
    """Película del panel (emparejamiento para revisar o recién corregido).

    Args:
        movie: Película.

    Returns:
        Datos del CSV y de TMDB para compararlos.
    """
    return movie_summary(movie) | {
        "tituloCsv": movie.csv_title,
        "tmdbId": movie.tmdb_id,
        "confianza": movie.match_conf,
        "manual": movie.manual,
        "disponibleEs": movie.netflix_es,
        "votosImdb": movie.imdb_votes,
    }


class CorrectRequest(BaseModel):
    """Cuerpo de ``POST /panel/api/peliculas/{id}/tmdb``."""

    tmdb: str = Field(min_length=1, max_length=300)


class AddMovieRequest(BaseModel):
    """Cuerpo de ``POST /panel/api/peliculas``."""

    netflix: str = Field(min_length=1, max_length=300)
    tmdb: str = Field(min_length=1, max_length=300)


def search_page(page: SearchPage) -> dict[str, Any]:
    """Página de resultados.

    Args:
        page: Resultados.

    Returns:
        ``{resultados, total, pagina, porPagina}``.
    """
    return {
        "resultados": [movie_summary(m) for m in page.results],
        "total": page.total,
        "pagina": page.page,
        "porPagina": page.page_size,
    }


def genres(items: list[tuple[Genre, int]]) -> dict[str, Any]:
    """Categorías de ``GET /genres``.

    Args:
        items: Pares (género, películas).

    Returns:
        ``{categorias: [{id, nombre, peliculas}]}``.
    """
    return {"categorias": [{"id": g.id, "nombre": g.name, "peliculas": n} for g, n in items]}


def scoreboard(players: list[Player]) -> dict[str, Any]:
    """Marcador, de más a menos puntos.

    Args:
        players: Jugadores.

    Returns:
        ``{jugadores: [{id, nombre, puntos}]}``.
    """
    ordered = sorted(players, key=lambda p: -p.points)
    return {"jugadores": [{"id": p.id, "nombre": p.name, "puntos": p.points} for p in ordered]}


def status(state: SessionState) -> dict[str, Any]:
    """Estado de ``GET /status`` y del evento ``estado``.

    Args:
        state: Foto de la partida.

    Returns:
        ``{navegador, sesionNetflix, pantalla, rondaActual, ultimaOrden, ultimoError}``.
    """
    current = None
    if state.round is not None:
        r = state.round
        current = {
            "netflixId": r.movie.netflix_id,
            "segundos": r.seconds,
            "segundosReales": r.actual_seconds,
            "revelada": state.screen == "revelada",
            "valor": r.value,
            "categoria": {"id": r.category.id, "nombre": r.category.name} if r.category else None,
            "pistas": [
                {"tipo": h.kind, "etiqueta": h.label, "texto": h.text} for h in r.shown_hints
            ],
            "pistasTotal": len(r.hints),
            "fotogramas": len(r.frames),
            "fotogramasMax": MAX_FRAMES,
            "clipUsado": r.clip_used,
            "acertantes": sorted(r.awarded),
            "fallos": sorted(r.failed),
        }
        if state.screen == "revelada":
            current |= movie_summary(state.round.movie)
    return {
        "navegador": state.player.browser,
        "sesionNetflix": state.player.netflix_session,
        "pantalla": state.screen,
        "rondaActual": current,
        "ultimaOrden": state.last_command,
        "ultimoError": state.last_error,
    }
