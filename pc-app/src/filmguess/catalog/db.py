"""Acceso a la base de datos SQLite del catálogo (con índice de texto FTS5)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from filmguess.catalog.clues import Clues, Genre
from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.models import Confidence, Movie, TmdbMatch
from filmguess.catalog.wikidata import WikidataMovie

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    netflix_id      INTEGER PRIMARY KEY,
    csv_title       TEXT NOT NULL,
    title_es        TEXT,
    original_title  TEXT,
    year            INTEGER,
    runtime_s       INTEGER,
    poster_path     TEXT,
    tmdb_id         INTEGER,
    imdb_id         TEXT,
    imdb_rating     REAL,
    imdb_votes      INTEGER,
    tmdb_popularity REAL,
    tmdb_vote_avg   REAL,
    tmdb_vote_count INTEGER,
    netflix_es      INTEGER,
    match_conf      TEXT,
    manual          INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT,
    wd_tmdb_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_movies_imdb ON movies(imdb_id);
CREATE INDEX IF NOT EXISTS idx_movies_pending ON movies(match_conf);

CREATE VIRTUAL TABLE IF NOT EXISTS movies_fts USING fts5(
    title_es, original_title, csv_title,
    content='movies', content_rowid='netflix_id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS movies_ai AFTER INSERT ON movies BEGIN
    INSERT INTO movies_fts(rowid, title_es, original_title, csv_title)
    VALUES (new.netflix_id, new.title_es, new.original_title, new.csv_title);
END;
CREATE TRIGGER IF NOT EXISTS movies_ad AFTER DELETE ON movies BEGIN
    INSERT INTO movies_fts(movies_fts, rowid, title_es, original_title, csv_title)
    VALUES ('delete', old.netflix_id, old.title_es, old.original_title, old.csv_title);
END;
CREATE TRIGGER IF NOT EXISTS movies_au AFTER UPDATE OF title_es, original_title, csv_title
ON movies BEGIN
    INSERT INTO movies_fts(movies_fts, rowid, title_es, original_title, csv_title)
    VALUES ('delete', old.netflix_id, old.title_es, old.original_title, old.csv_title);
    INSERT INTO movies_fts(rowid, title_es, original_title, csv_title)
    VALUES (new.netflix_id, new.title_es, new.original_title, new.csv_title);
END;

CREATE TABLE IF NOT EXISTS clues (
    netflix_id   INTEGER PRIMARY KEY,
    genres       TEXT NOT NULL DEFAULT '[]',
    actor        TEXT,
    character    TEXT,
    quote        TEXT,
    quote_source TEXT,
    quote_lang   TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    netflix_id INTEGER NOT NULL,
    title      TEXT NOT NULL,
    PRIMARY KEY (netflix_id, title)
);

CREATE TABLE IF NOT EXISTS aliases_fetched (
    netflix_id INTEGER PRIMARY KEY,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS random_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    netflix_id INTEGER NOT NULL,
    picked_at  TEXT NOT NULL
);
"""


class ClueTarget(NamedTuple):
    """Película del catálogo de juego a la que se le buscan pistas."""

    netflix_id: int
    tmdb_id: int
    titles: tuple[str | None, ...]


GENRE_FILTER = (
    "EXISTS (SELECT 1 FROM clues c, json_each(c.genres) g "
    "WHERE c.netflix_id = m.netflix_id AND json_extract(g.value, '$.id') = ?)"
)


class PendingMovie(NamedTuple):
    """Película pendiente de enriquecer con TMDB."""

    netflix_id: int
    csv_title: str
    wd_tmdb_id: int | None = None
    """ID de TMDB según Wikidata; si existe se usa en lugar de buscar por título."""


_MOVIE_COLUMNS = tuple(f.name for f in fields(Movie))
_SELECT_MOVIE = "SELECT " + ", ".join(f"m.{c}" for c in _MOVIE_COLUMNS) + " FROM movies m"


class CatalogDB:
    """Catálogo persistente de películas.

    Todas las escrituras se confirman de inmediato, de modo que una importación
    interrumpida se puede reanudar sin perder lo ya procesado.
    """

    def __init__(self, path: Path | str) -> None:
        """Abre (o crea) la base de datos.

        Args:
            path: Ruta del fichero SQLite, o ``":memory:"`` para pruebas.
        """
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        """Cierra la conexión."""
        self._conn.close()

    def _migrate(self) -> None:
        columns = {str(r[1]) for r in self._conn.execute("PRAGMA table_info(movies)")}
        if "wd_tmdb_id" not in columns:
            with self._conn:
                self._conn.execute("ALTER TABLE movies ADD COLUMN wd_tmdb_id INTEGER")
        for name in ("original_language", "origin_country", "origin_fetched_at"):
            if name not in columns:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE movies ADD COLUMN {name} TEXT")

    # --- Alta desde el CSV -------------------------------------------------

    def add_csv_movies(self, movies: Iterable[CsvMovie]) -> int:
        """Inserta las películas del CSV que aún no existan.

        Args:
            movies: Filas del CSV.

        Returns:
            Número de películas nuevas.
        """
        before = self.count()
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO movies(netflix_id, csv_title) VALUES (?, ?)",
                ((m.netflix_id, m.title) for m in movies),
            )
        return self.count() - before

    def add_wikidata_movies(self, movies: Iterable[WikidataMovie]) -> tuple[int, int]:
        """Enlaza las películas con el ID de TMDB que da Wikidata y añade las que falten.

        Solo se usan los títulos con ID de TMDB. Si una película ya emparejada (no a mano)
        apunta a otro ID de TMDB, vuelve a quedar pendiente para corregirla por ID.
        Un ID que no cambia no se vuelve a procesar.

        Args:
            movies: Títulos leídos de Wikidata.

        Returns:
            ``(nuevas, reabiertas)``: películas añadidas y emparejamientos que se rehacen.
        """
        wanted = {m.netflix_id: m.tmdb_id for m in movies if m.tmdb_id is not None}
        current = {
            int(r[0]): r[1] for r in self._conn.execute("SELECT netflix_id, wd_tmdb_id FROM movies")
        }
        new = [(nid, tid) for nid, tid in wanted.items() if nid not in current]
        changed = [
            (tid, nid) for nid, tid in wanted.items() if nid in current and current[nid] != tid
        ]
        reopened = 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO movies(netflix_id, csv_title, wd_tmdb_id) VALUES (?, '', ?)", new
            )
            self._conn.executemany("UPDATE movies SET wd_tmdb_id=? WHERE netflix_id=?", changed)
            for _, nid in changed:
                cur = self._conn.execute(
                    "UPDATE movies SET match_conf=NULL WHERE netflix_id=? AND manual=0 "
                    "AND match_conf IS NOT NULL AND (tmdb_id IS NULL OR tmdb_id != wd_tmdb_id)",
                    (nid,),
                )
                reopened += cur.rowcount
        return len(new), reopened

    # --- Enriquecimiento -----------------------------------------------------

    def pending(self, limit: int | None = None) -> list[PendingMovie]:
        """Películas que aún no se han emparejado con TMDB.

        Primero las que tienen ID de TMDB por Wikidata (no gastan búsquedas) y, dentro de
        cada grupo, por ID de Netflix.

        Args:
            limit: Máximo de filas; ``None`` para todas.

        Returns:
            Las películas pendientes.
        """
        sql = (
            "SELECT netflix_id, csv_title, wd_tmdb_id FROM movies "
            "WHERE match_conf IS NULL AND manual = 0 "
            "ORDER BY wd_tmdb_id IS NULL, netflix_id"
        )
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [
            PendingMovie(int(r[0]), str(r[1]), None if r[2] is None else int(r[2]))
            for r in self._conn.execute(sql, params)
        ]

    def save_match(self, netflix_id: int, match: TmdbMatch, *, manual: bool = False) -> None:
        """Guarda el emparejamiento con TMDB.

        Si la película se corrigió a mano, solo otra corrección manual la sobrescribe.

        Args:
            netflix_id: ID de Netflix.
            match: Datos de TMDB.
            manual: ``True`` si viene de una corrección hecha por el usuario.
        """
        with self._conn:
            self._conn.execute(
                """
                UPDATE movies SET tmdb_id=?, title_es=?, original_title=?, year=?, runtime_s=?,
                    poster_path=?, imdb_id=?, tmdb_popularity=?, tmdb_vote_avg=?,
                    tmdb_vote_count=?, netflix_es=?, match_conf=?, manual=?, updated_at=?
                WHERE netflix_id=? AND (manual = 0 OR ?)
                """,
                (
                    match.tmdb_id,
                    match.title_es,
                    match.original_title,
                    match.year,
                    match.runtime_s,
                    match.poster_path,
                    match.imdb_id,
                    match.popularity,
                    match.vote_avg,
                    match.vote_count,
                    _bool_to_db(match.netflix_es),
                    match.confidence,
                    int(manual),
                    _now(),
                    netflix_id,
                    int(manual),
                ),
            )

    def mark_no_match(self, netflix_id: int) -> None:
        """Marca una película sin candidato en TMDB (no se volverá a procesar).

        Args:
            netflix_id: ID de Netflix.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE movies SET match_conf='sin_match', updated_at=? "
                "WHERE netflix_id=? AND manual=0",
                (_now(), netflix_id),
            )

    def set_netflix_es(self, netflix_id: int, available: bool) -> None:
        """Actualiza la disponibilidad en Netflix España (p. ej., tras un error al reproducir).

        Args:
            netflix_id: ID de Netflix.
            available: Si está disponible.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE movies SET netflix_es=?, updated_at=? WHERE netflix_id=?",
                (int(available), _now(), netflix_id),
            )

    def update_imdb_ratings(self, ratings: Iterable[tuple[str, float, int]]) -> int:
        """Actualiza nota y votos de IMDb de las películas con ese ``imdb_id``.

        Args:
            ratings: Tuplas ``(imdb_id, nota, votos)``.

        Returns:
            Número de películas actualizadas.
        """
        with self._conn:
            cur = self._conn.executemany(
                "UPDATE movies SET imdb_rating=?, imdb_votes=? WHERE imdb_id=?",
                ((rating, votes, imdb_id) for imdb_id, rating, votes in ratings),
            )
        return cur.rowcount

    def imdb_ids(self) -> set[str]:
        """IMDb IDs presentes en el catálogo.

        Returns:
            Conjunto de IDs (``tt...``).
        """
        rows = self._conn.execute("SELECT imdb_id FROM movies WHERE imdb_id IS NOT NULL")
        return {str(r[0]) for r in rows}

    # --- Consultas -------------------------------------------------------------

    def get(self, netflix_id: int) -> Movie | None:
        """Obtiene una película por su ID de Netflix.

        Args:
            netflix_id: ID de Netflix.

        Returns:
            La película o ``None`` si no existe.
        """
        row = self._conn.execute(f"{_SELECT_MOVIE} WHERE m.netflix_id=?", (netflix_id,)).fetchone()
        return _row_to_movie(row) if row else None

    def count(self) -> int:
        """Número total de películas.

        Returns:
            Recuento.
        """
        return int(self._conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0])

    def stats(self) -> dict[str, int]:
        """Resumen del estado del catálogo para el panel.

        Returns:
            Recuentos: ``total``, ``pendientes``, uno por confianza y ``netflix_es``.
        """
        row = self._conn.execute(
            """
            SELECT COUNT(*),
                   SUM(match_conf IS NULL),
                   SUM(match_conf = 'alta'), SUM(match_conf = 'media'),
                   SUM(match_conf = 'baja'), SUM(match_conf = 'sin_match'),
                   SUM(netflix_es = 1), SUM(imdb_votes IS NOT NULL)
            FROM movies
            """
        ).fetchone()
        keys = ("total", "pendientes", "alta", "media", "baja", "sin_match", "netflix_es", "imdb")
        return {k: int(v or 0) for k, v in zip(keys, row, strict=True)}

    def to_review(
        self, confidence: Confidence, *, limit: int, offset: int = 0
    ) -> tuple[list[Movie], int]:
        """Emparejamientos dudosos que no se han corregido a mano, para revisarlos.

        Args:
            confidence: ``baja`` o ``sin_match``.
            limit: Tamaño de página.
            offset: Desplazamiento.

        Returns:
            La página (primero las disponibles en España y las más votadas) y el total.
        """
        where = "WHERE m.match_conf = ? AND m.manual = 0"
        total = int(
            self._conn.execute(f"SELECT COUNT(*) FROM movies m {where}", (confidence,)).fetchone()[
                0
            ]
        )
        rows = self._conn.execute(
            f"{_SELECT_MOVIE} {where} "
            "ORDER BY COALESCE(m.netflix_es, 0) DESC, COALESCE(m.imdb_votes, 0) DESC, "
            "m.netflix_id LIMIT ? OFFSET ?",
            (confidence, limit, offset),
        ).fetchall()
        return [_row_to_movie(r) for r in rows], total

    def search_fts(
        self,
        fts_query: str,
        *,
        only_netflix_es: bool,
        limit: int,
        offset: int,
        genre_id: int | None = None,
    ) -> tuple[list[Movie], int]:
        """Búsqueda de texto completo.

        Args:
            fts_query: Expresión FTS5 ya construida y escapada.
            only_netflix_es: Si se limitan los resultados a ``netflix_es = 1``.
            limit: Tamaño de página.
            offset: Desplazamiento.
            genre_id: Solo películas de este género de TMDB.

        Returns:
            La página de resultados y el total de coincidencias.
        """
        where = "movies_fts MATCH ?"
        params: list[Any] = [fts_query]
        if only_netflix_es:
            where += " AND m.netflix_es = 1"
        if genre_id is not None:
            where += f" AND {GENRE_FILTER}"
            params.append(genre_id)
        base = f"FROM movies_fts JOIN movies m ON m.netflix_id = movies_fts.rowid WHERE {where}"
        total = int(self._conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0])
        cols = ", ".join(f"m.{c}" for c in _MOVIE_COLUMNS)
        rows = self._conn.execute(
            f"SELECT {cols} {base} "
            "ORDER BY bm25(movies_fts, 10.0, 5.0, 2.0), COALESCE(m.imdb_votes, 0) DESC "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_row_to_movie(r) for r in rows], total

    def random_candidates(
        self,
        *,
        min_year: int,
        genre_id: int | None = None,
        exclude_languages: Sequence[str] = (),
        exclude_countries: Sequence[str] = (),
    ) -> list[Movie]:
        """Películas aptas para el modo aleatorio (sin ordenar ni recortar).

        Args:
            min_year: Año mínimo de estreno.
            genre_id: Solo películas de este género de TMDB.
            exclude_languages: Idiomas originales excluidos (los de origen desconocido se
                conservan).
            exclude_countries: Países de origen excluidos (basta con que coincida uno).

        Returns:
            Películas disponibles en ES, con confianza alta o media y año >= ``min_year``.
        """
        sql = (
            f"{_SELECT_MOVIE} WHERE m.netflix_es = 1 AND m.match_conf IN ('alta', 'media') "
            "AND m.year >= ?"
        )
        params: list[Any] = [min_year]
        if genre_id is not None:
            sql += f" AND {GENRE_FILTER}"
            params.append(genre_id)
        langs = [x.strip().lower() for x in exclude_languages if x.strip()]
        if langs:
            sql += (
                " AND (m.original_language IS NULL "
                "OR lower(m.original_language) NOT IN (SELECT value FROM json_each(?)))"
            )
            params.append(json.dumps(langs))
        countries = [x.strip().upper() for x in exclude_countries if x.strip()]
        if countries:
            sql += (
                " AND (m.origin_country IS NULL OR NOT EXISTS (SELECT 1 FROM json_each(?) c "
                "WHERE instr(',' || upper(m.origin_country) || ',', ',' || c.value || ',') > 0))"
            )
            params.append(json.dumps(countries))
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_movie(r) for r in rows]

    def popular(
        self, *, limit: int, offset: int = 0, genre_id: int | None = None
    ) -> tuple[list[Movie], int]:
        """Películas jugables, de la más votada (la más conocida) a la menos.

        Args:
            limit: Tamaño de página.
            offset: Desplazamiento.
            genre_id: Solo películas de este género de TMDB.

        Returns:
            La página (disponibles en ES y con confianza alta o media) y el total.
        """
        where = "WHERE m.netflix_es = 1 AND m.match_conf IN ('alta', 'media')"
        params: list[Any] = []
        if genre_id is not None:
            where += f" AND {GENRE_FILTER}"
            params.append(genre_id)
        total = int(
            self._conn.execute(f"SELECT COUNT(*) FROM movies m {where}", params).fetchone()[0]
        )
        rows = self._conn.execute(
            f"{_SELECT_MOVIE} {where} "
            "ORDER BY COALESCE(m.imdb_votes, 0) DESC, COALESCE(m.tmdb_vote_count, 0) DESC, "
            "m.netflix_id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_row_to_movie(r) for r in rows], total

    def get_many(self, netflix_ids: Sequence[int], *, genre_id: int | None = None) -> list[Movie]:
        """Obtiene varias películas por su ID de Netflix.

        Args:
            netflix_ids: IDs pedidos.
            genre_id: Solo las de este género de TMDB.

        Returns:
            Las que existen, en el orden pedido.
        """
        sql = f"{_SELECT_MOVIE} WHERE m.netflix_id IN (SELECT value FROM json_each(?))"
        params: list[Any] = [json.dumps(list(netflix_ids))]
        if genre_id is not None:
            sql += f" AND {GENRE_FILTER}"
            params.append(genre_id)
        found = {m.netflix_id: m for m in map(_row_to_movie, self._conn.execute(sql, params))}
        return [found[i] for i in netflix_ids if i in found]

    # --- Pistas ------------------------------------------------------------

    def clue_targets(self, *, refresh: bool = False) -> list[ClueTarget]:
        """Películas del catálogo de juego (disponibles en ES y con TMDB) sin pistas aún.

        Args:
            refresh: Incluye también las que ya tienen pistas.

        Returns:
            Las películas, de la más votada a la menos.
        """
        where = "m.netflix_es = 1 AND m.tmdb_id IS NOT NULL"
        if not refresh:
            where += " AND NOT EXISTS (SELECT 1 FROM clues c WHERE c.netflix_id = m.netflix_id)"
        rows = self._conn.execute(
            "SELECT m.netflix_id, m.tmdb_id, m.title_es, m.original_title, m.csv_title "
            f"FROM movies m WHERE {where} ORDER BY COALESCE(m.imdb_votes, 0) DESC"
        ).fetchall()
        return [ClueTarget(int(r[0]), int(r[1]), (r[2], r[3], r[4])) for r in rows]

    def origin_targets(self, *, refresh: bool = False) -> list[ClueTarget]:
        """Películas del catálogo de juego a las que falta el origen (idioma y país).

        Args:
            refresh: Incluye también las que ya tienen el origen.

        Returns:
            Las películas, de la más votada a la menos.
        """
        where = "m.netflix_es = 1 AND m.tmdb_id IS NOT NULL"
        if not refresh:
            where += " AND m.origin_fetched_at IS NULL"
        rows = self._conn.execute(
            "SELECT m.netflix_id, m.tmdb_id, m.title_es, m.original_title, m.csv_title "
            f"FROM movies m WHERE {where} ORDER BY COALESCE(m.imdb_votes, 0) DESC"
        ).fetchall()
        return [ClueTarget(int(r[0]), int(r[1]), (r[2], r[3], r[4])) for r in rows]

    def save_origin(self, netflix_id: int, language: str | None, countries: list[str]) -> None:
        """Guarda el idioma original y los países de origen de una película.

        Args:
            netflix_id: ID de Netflix.
            language: Código ISO 639-1 del idioma original, si se conoce.
            countries: Códigos ISO 3166-1 de los países de origen.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE movies SET original_language = ?, origin_country = ?, "
                "origin_fetched_at = datetime('now') WHERE netflix_id = ?",
                (language or None, ",".join(countries) or None, netflix_id),
            )

    def save_clues(self, netflix_id: int, clues: Clues) -> None:
        """Guarda (o sustituye) las pistas de una película.

        Args:
            netflix_id: ID de Netflix.
            clues: Pistas.
        """
        genres = json.dumps(
            [{"id": g.id, "name": g.name} for g in clues.genres], ensure_ascii=False
        )
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO clues(netflix_id, genres, actor, character, quote, "
                "quote_source, quote_lang, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    netflix_id,
                    genres,
                    clues.actor,
                    clues.character,
                    clues.quote,
                    clues.quote_source,
                    clues.quote_lang,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def clues(self, netflix_id: int) -> Clues | None:
        """Pistas de una película.

        Args:
            netflix_id: ID de Netflix.

        Returns:
            Las pistas, o ``None`` si aún no se han descargado.
        """
        row = self._conn.execute(
            "SELECT genres, actor, character, quote, quote_source, quote_lang "
            "FROM clues WHERE netflix_id = ?",
            (netflix_id,),
        ).fetchone()
        if row is None:
            return None
        genres = [Genre(int(g["id"]), str(g["name"])) for g in json.loads(row[0] or "[]")]
        return Clues(genres, row[1], row[2], row[3], row[4], row[5])

    def untranslated_quotes(self) -> list[tuple[int, str]]:
        """Frases y críticas guardadas que no están en español.

        Returns:
            Pares (ID de Netflix, texto).
        """
        rows = self._conn.execute(
            "SELECT netflix_id, quote FROM clues "
            "WHERE quote IS NOT NULL AND COALESCE(quote_lang, '') <> 'es' ORDER BY netflix_id"
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def set_quote(self, netflix_id: int, quote: str, lang: str) -> None:
        """Sustituye el texto de la última pista (por ejemplo, por su traducción).

        Args:
            netflix_id: ID de Netflix.
            quote: Texto nuevo.
            lang: Idioma del texto.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE clues SET quote = ?, quote_lang = ? WHERE netflix_id = ?",
                (quote, lang, netflix_id),
            )

    def genre_counts(self) -> list[tuple[Genre, int]]:
        """Géneros del catálogo de juego con cuántas películas disponibles tienen.

        Returns:
            Pares (género, películas), de más a menos películas.
        """
        rows = self._conn.execute(
            "SELECT json_extract(g.value, '$.id'), json_extract(g.value, '$.name'), COUNT(*) "
            "FROM clues c JOIN movies m ON m.netflix_id = c.netflix_id, json_each(c.genres) g "
            "WHERE m.netflix_es = 1 GROUP BY 1 ORDER BY 3 DESC, 2"
        ).fetchall()
        return [(Genre(int(r[0]), str(r[1])), int(r[2])) for r in rows]

    def clue_stats(self) -> dict[str, int]:
        """Cuántas películas del catálogo de juego tienen cada tipo de pista.

        Returns:
            Recuentos ``con_pistas``, ``frase``, ``critica``, ``eslogan``, ``sinopsis``,
            ``director`` y ``sin_texto``.
        """
        row = self._conn.execute(
            "SELECT COUNT(*), SUM(quote_source = 'wikiquote'), SUM(quote_source = 'critica'), "
            "SUM(quote_source = 'eslogan'), SUM(quote_source = 'sinopsis'), "
            "SUM(quote_source = 'director'), "
            "SUM(quote IS NULL) FROM clues c "
            "JOIN movies m ON m.netflix_id = c.netflix_id WHERE m.netflix_es = 1"
        ).fetchone()
        keys = ("con_pistas", "frase", "critica", "eslogan", "sinopsis", "director", "sin_texto")
        return {k: int(v or 0) for k, v in zip(keys, row, strict=True)}

    def recent_random(self, n: int) -> list[int]:
        """IDs elegidos en las últimas ``n`` rondas aleatorias.

        Args:
            n: Número de rondas.

        Returns:
            IDs de Netflix, del más reciente al más antiguo.
        """
        if n <= 0:
            return []
        rows = self._conn.execute(
            "SELECT netflix_id FROM random_history ORDER BY id DESC LIMIT ?", (n,)
        )
        return [int(r[0]) for r in rows]

    def record_random(self, netflix_id: int) -> None:
        """Apunta una elección aleatoria en el historial.

        Args:
            netflix_id: ID elegido.
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO random_history(netflix_id, picked_at) VALUES (?, ?)",
                (netflix_id, _now()),
            )

    # --- Títulos alternativos ----------------------------------------------

    def alias_targets(self, *, refresh: bool = False) -> list[ClueTarget]:
        """Películas del catálogo de juego sin títulos alternativos descargados.

        Args:
            refresh: Incluye también las que ya los tienen.

        Returns:
            Las películas, de la más votada a la menos.
        """
        targets = self.clue_targets(refresh=True)
        if refresh:
            return targets
        done = {int(r[0]) for r in self._conn.execute("SELECT netflix_id FROM aliases_fetched")}
        return [t for t in targets if t.netflix_id not in done]

    def save_aliases(self, netflix_id: int, titles: Iterable[str]) -> None:
        """Guarda (sustituye) los títulos alternativos de una película.

        Args:
            netflix_id: ID de Netflix.
            titles: Títulos alternativos.
        """
        with self._conn:
            self._conn.execute("DELETE FROM aliases WHERE netflix_id = ?", (netflix_id,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO aliases (netflix_id, title) VALUES (?, ?)",
                [(netflix_id, t) for t in titles],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO aliases_fetched (netflix_id, fetched_at) VALUES (?, ?)",
                (netflix_id, _now()),
            )

    def aliases(self, netflix_id: int) -> list[str]:
        """Títulos alternativos de una película.

        Args:
            netflix_id: ID de Netflix.

        Returns:
            Los títulos guardados (puede estar vacío).
        """
        rows = self._conn.execute(
            "SELECT title FROM aliases WHERE netflix_id = ? ORDER BY title", (netflix_id,)
        ).fetchall()
        return [str(r[0]) for r in rows]

    def all_titles(self) -> list[tuple[int, list[str]]]:
        """Todos los títulos de todas las películas (para saber si una respuesta es otra).

        Returns:
            Pares ``(id de Netflix, títulos)`` con el español, el original, el del CSV y
            los alternativos.
        """
        titles: dict[int, list[str]] = {}
        for row in self._conn.execute(
            "SELECT netflix_id, title_es, original_title, csv_title FROM movies"
        ):
            titles[int(row[0])] = [str(t) for t in row[1:] if t]
        for row in self._conn.execute("SELECT netflix_id, title FROM aliases"):
            titles.setdefault(int(row[0]), []).append(str(row[1]))
        return list(titles.items())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bool_to_db(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _row_to_movie(row: Sequence[Any]) -> Movie:
    data = dict(zip(_MOVIE_COLUMNS, row, strict=True))
    data["netflix_es"] = None if data["netflix_es"] is None else bool(data["netflix_es"])
    data["manual"] = bool(data["manual"])
    data["match_conf"] = cast(Confidence | None, data["match_conf"])
    return Movie(**data)
