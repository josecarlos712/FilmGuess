"""Emparejamiento de un título del CSV con el candidato correcto de TMDB."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from filmguess.catalog.models import Confidence, TmdbMatch
from filmguess.catalog.normalize import title_similarity
from filmguess.catalog.tmdb import TmdbCandidate, is_on_netflix

MIN_SIMILARITY = 0.6
"""Por debajo de esta similitud un candidato se descarta."""
DETAIL_SIMILARITY = 0.85
"""Candidatos a partir de esta similitud merecen consultar su ficha (proveedores)."""
MAX_DETAILED = 3
"""Máximo de fichas consultadas por película (controla el coste de la importación)."""
NETFLIX_BONUS = 0.25
POPULARITY_BONUS = 0.05


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """Candidato con su similitud al título del CSV."""

    candidate: TmdbCandidate
    similarity: float


def rank_by_similarity(csv_title: str, candidates: list[TmdbCandidate]) -> list[ScoredCandidate]:
    """Ordena los candidatos por similitud con el título del CSV.

    Usa el máximo entre la similitud con ``title`` y con ``original_title``. A igual
    similitud desempata la popularidad. Descarta los que no llegan a ``MIN_SIMILARITY``.

    Args:
        csv_title: Título tal como viene en el CSV.
        candidates: Resultados de búsqueda de TMDB.

    Returns:
        Candidatos válidos, del mejor al peor.
    """
    scored = [
        ScoredCandidate(
            c,
            max(
                title_similarity(csv_title, c.title), title_similarity(csv_title, c.original_title)
            ),
        )
        for c in candidates
    ]
    valid = [s for s in scored if s.similarity >= MIN_SIMILARITY]
    valid.sort(key=lambda s: (s.similarity, s.candidate.popularity), reverse=True)
    return valid


def final_score(similarity: float, on_netflix: bool | None, popularity_rank: int) -> float:
    """Puntuación final de un candidato con ficha.

    Args:
        similarity: Similitud de título (0-1).
        on_netflix: Si TMDB lo da como disponible en Netflix en la región.
        popularity_rank: Posición por popularidad entre los consultados (0 = el más popular).

    Returns:
        Puntuación; mayor es mejor.
    """
    bonus = NETFLIX_BONUS if on_netflix else 0.0
    if popularity_rank == 0:
        bonus += POPULARITY_BONUS
    return similarity + bonus


def confidence_for(similarity: float, on_netflix: bool | None, exact_count: int) -> Confidence:
    """Nivel de confianza del emparejamiento elegido.

    Args:
        similarity: Similitud del elegido.
        on_netflix: Si está en Netflix en la región.
        exact_count: Cuántos candidatos coincidían exactamente con el título.

    Returns:
        ``alta``, ``media`` o ``baja``.
    """
    if similarity >= 0.95 and (on_netflix or exact_count <= 1):
        return "alta"
    if similarity >= DETAIL_SIMILARITY:
        return "media"
    return "baja"


def build_match(details: dict[str, Any], confidence: Confidence, region: str) -> TmdbMatch:
    """Convierte la ficha de TMDB en los datos que se guardan.

    Args:
        details: JSON de ``/movie/{id}`` con ``external_ids`` y ``watch/providers``.
        confidence: Confianza calculada.
        region: País para la disponibilidad.

    Returns:
        El emparejamiento listo para guardar.
    """
    runtime = details.get("runtime")
    release = str(details.get("release_date") or "")
    external = details.get("external_ids") or {}
    return TmdbMatch(
        tmdb_id=int(details["id"]),
        title_es=details.get("title") or None,
        original_title=details.get("original_title") or None,
        year=int(release[:4]) if release[:4].isdigit() else None,
        runtime_s=int(runtime) * 60 if isinstance(runtime, int) and runtime > 0 else None,
        poster_path=details.get("poster_path") or None,
        imdb_id=external.get("imdb_id") or details.get("imdb_id") or None,
        popularity=_float_or_none(details.get("popularity")),
        vote_avg=_float_or_none(details.get("vote_average")),
        vote_count=_int_or_none(details.get("vote_count")),
        netflix_es=is_on_netflix(details, region),
        confidence=confidence,
    )


DetailsFetcher = Callable[[int], Awaitable[dict[str, Any]]]


async def choose_match(
    csv_title: str,
    candidates: list[TmdbCandidate],
    fetch_details: DetailsFetcher,
    region: str = "ES",
) -> TmdbMatch | None:
    """Elige el mejor candidato de TMDB para un título del CSV.

    Consulta la ficha de hasta ``MAX_DETAILED`` candidatos muy parecidos para usar la
    disponibilidad en Netflix como desempate (remakes, homónimos). Si ninguno es muy
    parecido, consulta solo el mejor.

    Args:
        csv_title: Título del CSV.
        candidates: Resultados de búsqueda.
        fetch_details: Función que devuelve la ficha de un ID de TMDB.
        region: País para la disponibilidad.

    Returns:
        El emparejamiento, o ``None`` si ningún candidato se parece lo suficiente.
    """
    ranked = rank_by_similarity(csv_title, candidates)
    if not ranked:
        return None

    close = [s for s in ranked if s.similarity >= DETAIL_SIMILARITY][:MAX_DETAILED]
    to_detail = close or ranked[:1]
    exact_count = sum(1 for s in ranked if s.similarity >= 0.999)

    by_popularity = sorted(to_detail, key=lambda s: s.candidate.popularity, reverse=True)
    pop_rank = {id(s): i for i, s in enumerate(by_popularity)}

    best: tuple[float, ScoredCandidate, dict[str, Any]] | None = None
    for scored in to_detail:
        details = await fetch_details(scored.candidate.tmdb_id)
        on_netflix = is_on_netflix(details, region)
        score = final_score(scored.similarity, on_netflix, pop_rank[id(scored)])
        if best is None or score > best[0]:
            best = (score, scored, details)

    assert best is not None  # to_detail nunca está vacío
    _, scored, details = best
    confidence = confidence_for(scored.similarity, is_on_netflix(details, region), exact_count)
    return build_match(details, confidence, region)


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
