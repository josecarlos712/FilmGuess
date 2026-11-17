"""Comprobación de los títulos que escriben los jugadores en el móvil.

La idea es no ser estrictos con la ortografía, pero sí con la película:

- Da igual la caja, las tildes, los signos y los espacios («spider-man» = «Spiderman»).
- Se perdonan las faltas típicas del español: b/v, h muda, ll/y, c/z/s, qu/k/c, g/j,
  letras dobles y la «e» que se añade delante de «s» («Estar Guars» ≈ «Star Wars»).
- Los números se escriben como se quiera («II», «2», «dos», «parte 2»), pero tienen que
  coincidir: «El padrino» no vale por «El padrino II».
- Cuenta cualquier título conocido de la película: el español, el original, el del CSV
  de Netflix y los alternativos que se guardan en el catálogo (otros países, traducciones
  y la lista manual de ``catalogo/alias.json``).
- Vale el título sin subtítulo («Joker» por «Joker: Folie à deux») solo si ninguna otra
  película del catálogo se llama así o empieza igual.
- Tras todo eso queda un margen de erratas que crece con la longitud del título.
- Un título que es exactamente el de otra película del catálogo es un fallo, aunque se
  parezca («Alien» no vale por «Aliens»).
- Si no acierta pero se queda muy cerca, el resultado es «casi»: no cuenta como fallo.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from filmguess.catalog.normalize import strip_accents

Verdict = Literal["acierto", "casi", "fallo"]

_SEPARATORS = re.compile("\\s*(?::|\\s[-\u2013\u2014]\\s|\\(|\\[)\\s*")
_BRACKETS = re.compile(r"[(\[][^)\]]*[)\]]")
_APOSTROPHES = re.compile("['\u2019`\u00b4]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

_ARTICLES = frozenset({"the", "a", "an", "el", "la", "los", "las", "un", "una", "lo"})
_FILLER = frozenset(
    {
        "parte", "part", "capitulo", "chapter", "episodio", "episode",
        "vol", "volumen", "volume", "pt",
    }
)  # fmt: skip
_NUMBERS = {
    "cero": "0", "zero": "0",
    "uno": "1", "one": "1",
    "dos": "2", "two": "2", "ii": "2",
    "tres": "3", "three": "3", "iii": "3",
    "cuatro": "4", "four": "4", "iv": "4",
    "cinco": "5", "five": "5",
    "seis": "6", "six": "6", "vi": "6",
    "siete": "7", "seven": "7", "vii": "7",
    "ocho": "8", "eight": "8", "viii": "8",
    "nueve": "9", "nine": "9", "ix": "9",
    "diez": "10", "ten": "10",
    "once": "11", "doce": "12", "trece": "13",
    "primera": "1", "primero": "1", "segunda": "2", "segundo": "2",
    "tercera": "3", "tercero": "3",
}  # fmt: skip

# Fonética aproximada del español (en orden): se aplica palabra a palabra.
_PHONETIC: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), repl)
    for pattern, repl in (
        (r"ph", "f"),
        (r"sh", "s"),
        (r"th", "t"),
        (r"ch", "\x01"),  # se protege la «ch» antes de quitar las «h»
        (r"qu", "k"),
        (r"q", "k"),
        (r"g(?=[ei])", "j"),
        (r"gu(?=[ei])", "g"),
        (r"c(?=[ei])", "s"),
        (r"c", "k"),
        (r"z", "s"),
        (r"x", "ks"),
        (r"w", "u"),
        (r"v", "b"),
        (r"ll", "y"),
        (r"y", "i"),
        (r"h", ""),
        (r"\x01", "c"),
        (r"n(?=[bp])", "m"),
        (r"(.)\1+", r"\1"),
        (r"^e(?=s[^aeiou])", ""),  # «estar» → «star», «espiderman» → «spiderman»
    )
)


def _words(text: str) -> list[str]:
    """Palabras normalizadas: sin tildes ni signos, números en cifras y sin relleno."""
    text = _APOSTROPHES.sub("", strip_accents(text).lower())
    text = text.replace("&", " y ")
    words = []
    for word in _NON_ALNUM.sub(" ", text).split():
        word = _NUMBERS.get(word, word)
        if word in _FILLER or word in {"and", "y", "e"}:
            continue
        words.append(word)
    while words and words[0] in _ARTICLES:
        words = words[1:]
    return words


def _phonetic(word: str) -> str:
    if word.isdigit():
        return word
    for pattern, repl in _PHONETIC:
        word = pattern.sub(repl, word)
    return word


def answer_key(text: str) -> str:
    """Clave de comparación de un título: fonética, sin espacios, con los números en cifras.

    Args:
        text: Título o respuesta del jugador.

    Returns:
        La clave; vacía si el texto no tiene letras ni números.
    """
    return "".join(_phonetic(w) for w in _words(text))


def _numbers(text: str) -> tuple[str, ...]:
    return tuple(sorted(w for w in _words(text) if w.isdigit()))


def title_forms(title: str) -> tuple[list[str], list[str]]:
    """Formas aceptables de un título.

    Args:
        title: Un título de la película.

    Returns:
        ``(completas, cortas)``: el título entero y sin lo que va entre paréntesis; y el
        título sin subtítulo (lo que va antes de «:», « - » o un paréntesis).
    """
    full = [title]
    without_brackets = _BRACKETS.sub(" ", title).strip()
    if without_brackets and without_brackets != title:
        full.append(without_brackets)
    short = []
    head = _SEPARATORS.split(title, maxsplit=1)[0].strip()
    if head and head not in full:
        short.append(head)
    return full, short


def tolerance(length: int) -> int:
    """Erratas que se perdonan según la longitud de la clave del título.

    Args:
        length: Longitud de la clave.

    Returns:
        Número de ediciones permitidas.
    """
    if length <= 3:
        return 0
    if length <= 6:
        return 1
    if length <= 11:
        return 2
    return 3


def edit_distance(a: str, b: str, limit: int | None = None) -> int:
    """Distancia de Damerau-Levenshtein (con transposiciones de letras vecinas).

    Args:
        a: Primera cadena.
        b: Segunda cadena.
        limit: Si se da, se deja de calcular al superarla (devuelve ``limit + 1``).

    Returns:
        Número mínimo de inserciones, borrados, cambios o transposiciones.
    """
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                row[j] = min(row[j], prev2[j - 2] + 1)
        if limit is not None and min(row) > limit:
            return limit + 1
        prev2, prev = prev, row
    return prev[-1]


class TitleIndex:
    """Claves de todos los títulos del catálogo, para no aceptar el de otra película."""

    def __init__(self, movies: Iterable[tuple[int, Iterable[str | None]]] = ()) -> None:
        """Crea el índice.

        Args:
            movies: Pares ``(id de Netflix, títulos)`` de todo el catálogo.
        """
        self.full: dict[str, set[int]] = defaultdict(set)
        self.short: dict[str, set[int]] = defaultdict(set)
        for movie_id, titles in movies:
            self.add(movie_id, titles)

    def add(self, movie_id: int, titles: Iterable[str | None]) -> None:
        """Añade los títulos de una película.

        Args:
            movie_id: ID de Netflix.
            titles: Sus títulos (los vacíos se ignoran).
        """
        for title in titles:
            if not title:
                continue
            full, short = title_forms(title)
            for form in full:
                if key := answer_key(form):
                    self.full[key].add(movie_id)
            for form in short:
                if key := answer_key(form):
                    self.short[key].add(movie_id)

    def others(self, key: str, movie_id: int) -> bool:
        """Si la clave es el título completo de otra película.

        Args:
            key: Clave de la respuesta.
            movie_id: Película en juego.

        Returns:
            ``True`` si otra película se llama exactamente así.
        """
        return bool(self.full.get(key, set()) - {movie_id})

    def unique_short(self, key: str, movie_id: int) -> bool:
        """Si la forma corta solo puede ser de esta película.

        Args:
            key: Clave de la forma corta.
            movie_id: Película en juego.

        Returns:
            ``True`` si ninguna otra película se llama o empieza así.
        """
        owners = self.full.get(key, set()) | self.short.get(key, set())
        return not (owners - {movie_id})


@dataclass(frozen=True, slots=True)
class AnswerCheck:
    """Resultado de comprobar una respuesta."""

    verdict: Verdict
    matched: str | None = None
    """Título con el que ha coincidido (si acierta)."""


def check_answer(
    guess: str,
    movie_id: int,
    titles: Iterable[str | None],
    index: TitleIndex | None = None,
) -> AnswerCheck:
    """Comprueba si lo que ha escrito el jugador es la película.

    Args:
        guess: Texto escrito en el móvil.
        movie_id: ID de Netflix de la película en juego.
        titles: Todos los títulos aceptables (español, original, CSV y alternativos).
        index: Títulos del catálogo entero; sin él no se detectan otras películas.

    Returns:
        ``acierto``, ``casi`` (muy cerca: puede volver a intentarlo) o ``fallo``.
    """
    key = answer_key(guess)
    if not key:
        return AnswerCheck("fallo")
    numbers = _numbers(guess)
    full: list[tuple[str, tuple[str, ...], str]] = []
    short: list[tuple[str, tuple[str, ...], str]] = []
    for title in dict.fromkeys(t for t in titles if t):
        f, s = title_forms(title)
        full += [(answer_key(x), _numbers(x), title) for x in f]
        short += [(answer_key(x), _numbers(x), title) for x in s]
    full = [c for c in full if c[0]]
    short = [
        c for c in short if len(c[0]) >= 4 and (index is None or index.unique_short(c[0], movie_id))
    ]
    candidates = full + short

    for k, _, title in candidates:
        if key == k:
            return AnswerCheck("acierto", title)
    if index is not None and index.others(key, movie_id):
        return AnswerCheck("fallo")

    near = False
    for k, title_numbers, title in candidates:
        if title_numbers != numbers:
            continue  # otro número es otra película de la saga
        allowed = tolerance(len(k))
        distance = edit_distance(key, k, allowed + 2)
        if distance <= allowed:
            return AnswerCheck("acierto", title)
        if len(key) >= 4 and (distance <= allowed + 2 or (key in k and 2 * len(key) >= len(k))):
            near = True
    return AnswerCheck("casi" if near else "fallo")
