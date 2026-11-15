# 03 · Catálogo

## Origen

`movies.csv` del usuario: 30.160 filas, sin cabecera, con codificación ASCII y saltos CRLF. Formato `"Nombre",netflixId`; el nombre va entre comillas porque puede llevar comas (`"1,000 Times Good Night",80001898`). Solo contiene películas. Los nombres están en inglés o en versión original, y el CSV no indica año, póster, duración ni disponibilidad por país. Se copia en `data/movies.csv` (no se versiona).

## Proceso de importación

Se ejecuta con `arrancar.bat importar` (o con el botón del panel). Opciones: `--limite N` (enriquecer solo N películas), `--sin-tmdb` (solo cargar el CSV), `--sin-imdb` y `--descargar-imdb` (descarga antes el fichero de valoraciones). Se puede **reanudar**: guarda el progreso fila a fila y vuelve a empezar donde se quedó.

1. **Cargar el CSV** con el módulo `csv` (respeta las comillas). Descarta filas con un ID no numérico o repetido.
2. **Buscar en TMDB**: `GET /search/movie?query=<nombre>&language=es-ES&include_adult=false`.
3. **Elegir el candidato**: puntúa a los 5 primeros por:
   - similitud del nombre normalizado (minúsculas, sin acentos ni signos) con `original_title` y `title`;
   - un **bonus** si TMDB indica que está en **Netflix España** (`watch/providers`, región `ES`, proveedor 8), que es la mejor forma de distinguir remakes y homónimos;
   - la popularidad, solo para desempatar.
4. **Detalle del elegido**: `GET /movie/{id}?language=es-ES&append_to_response=external_ids,watch/providers` → título en español, título original, año, duración, póster, `imdb_id` y si está en Netflix ES.
5. Guarda una **confianza** (`alta` / `media` / `baja`). Las de confianza baja se marcan para revisarlas en el panel.
6. **Valoraciones de IMDb**: descarga el fichero público `title.ratings.tsv.gz` de los *IMDb Non-Commercial Datasets* (uso personal) y lo cruza por `imdb_id` para obtener la nota media y el número de votos. Se pedirá permiso antes de la primera descarga.

**Coste estimado**: unas 30.000 búsquedas y otros tantos detalles, más ~2 consultas de proveedores por fila dudosa. A ~35 peticiones/s (por debajo del límite de TMDB) son entre 30 y 60 minutos la primera vez. Después solo se procesan las películas nuevas.

## Esquema (SQLite, `data/catalogo.sqlite`)

```sql
movies(
  netflix_id      INTEGER PRIMARY KEY,
  csv_title       TEXT NOT NULL,
  title_es        TEXT, original_title TEXT, year INTEGER,
  runtime_s       INTEGER, poster_path TEXT,
  tmdb_id         INTEGER, imdb_id TEXT,
  imdb_rating     REAL, imdb_votes INTEGER, tmdb_popularity REAL,
  netflix_es      INTEGER,          -- 1 sí, 0 no, NULL desconocido
  match_conf      TEXT,             -- alta | media | baja | sin_match
  manual          INTEGER DEFAULT 0,-- corregido a mano: el importador no lo pisa
  updated_at      TEXT
);
movies_fts USING fts5(title_es, original_title, csv_title,
                      tokenize='unicode61 remove_diacritics 2');
```

## Búsqueda (`GET /search`)

- Busca a la vez en el título en español, el original y el del CSV, sin distinguir acentos: «origen», «Inception» y «Orígen» encuentran la misma película.
- Prefijos por palabra (`orig*`) para resultados en vivo con un *debounce* de 300 ms.
- Orden: relevancia FTS (bm25) y, a igualdad, número de votos en IMDb.
- Por defecto **solo películas con `netflix_es = 1`**. El interruptor «Mostrar también las no confirmadas en España» incluye las de valor `0` o desconocido (los datos de proveedores de TMDB, que vienen de JustWatch, pueden ir retrasados).
- Pósters servidos desde `image.tmdb.org` (`w342` en la cuadrícula, `w780` en Revelar).

## Película aleatoria (`GET /random`)

Grupo de candidatas:
- `year >= 1991` (estrenadas después de 1990);
- `netflix_es = 1` y `match_conf` alta o media;
- con `imdb_votes >= 25.000` (las conocidas), ordenadas por la **nota ponderada** de IMDb `WR = v/(v+m)·R + m/(v+m)·C` (con `m = 25.000` y `C` la nota media del catálogo), que equilibra lo bien valorada que está con lo conocida que es;
- se toman las **500 primeras** y de ahí se elige una al azar, sin repetir las de las últimas 50 rondas.

Los umbrales (`votos_minimos`, `tamano_grupo`, `anio_minimo`) se pueden cambiar en `config.toml`. Si no hay datos de IMDb, se usa `vote_count` y `vote_average` de TMDB.

## Alta y corrección manual (panel)

- **Añadir por enlace**: se pega `https://www.netflix.com/title/80100172`; se extrae el ID, se lee el nombre de la ficha pública de Netflix y se pasa por los pasos 2 a 6.
- **Corregir**: en las fichas de confianza baja, el panel muestra los candidatos de TMDB con su póster para elegir el correcto o buscar otro. Se marca `manual = 1`.
