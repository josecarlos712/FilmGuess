# 05 · Plan de implementación

## Requisitos previos (los hace el usuario)

| Requisito | Para qué | Estado |
|---|---|---|
| Python 3.11+ | App del PC | ✅ instalado |
| Git | Repositorio | ✅ instalado |
| **Microsoft Edge** (o Google Chrome) | DRM de Netflix (Widevine/PlayReady). Firefox no sirve | ✅ instalado |
| **Node.js 22+** | Verificador `gate.mjs` de taste-skill | ❌ pendiente |
| **Clave de API de TMDB** (gratuita: cuenta en themoviedb.org → Ajustes → API) | Enriquecer el catálogo | ❌ pendiente |
| Cuenta de Netflix y nombre del perfil que se usará | Reproducción | primer arranque |
| Permiso para descargar `title.ratings.tsv.gz` de IMDb (~8 MB) | Película aleatoria | se pedirá |

## Fases

Cada fase termina con sus tests en verde (`pytest`), `ruff check .`, `ruff format .` y `mypy src/`.

| Fase | Contenido | Resultado comprobable |
|---|---|---|
| **F0 · Base** | `git init`, `.gitignore` (data/, config.toml), instalar taste-skill, `pc-app/pyproject.toml`, esqueleto de paquetes, `config.example.toml`, `arrancar.bat` | `python src/main.py --help` funciona |
| **F1 · Catálogo** | Lector del CSV, cliente TMDB (con reintentos y límite de ritmo), emparejamiento con confianza, IMDb, SQLite + FTS5, búsqueda, aleatoria | Tests con TMDB simulado; importación real de 200 filas de prueba; buscar «origen» devuelve *Inception* |
| **F2 · Servidor** | FastAPI, autenticación por token, filtro de red local, `/status`, `/search`, `/random`, `/movie`, `/players`, `/ws`, QR, IP de la red | Tests con `TestClient` y un reproductor falso |
| **F3 · Reproductor** | Edge persistente, `play` (CSS previo, interrupciones, *seek* con *fallback*, pausa, pantalla completa), `pause`, `resume`, `idle`, `reveal` | Tests de la lógica con Playwright simulado y una **prueba manual** con Netflix real midiendo el tiempo |
| **F4 · Tele** | `/tv`: espera, cargando, capa de revelar y marcador | Revisión visual a 1080p y 720p |
| **F5 · Móvil** | Conexión, buscador, rueda hh:mm:ss, ronda, jugadores | Revisión a 390 y 320 px en el navegador integrado y `gate.mjs` |
| **F6 · Panel** | Estado, QR, importación, corrección de emparejamientos, alta por enlace | Revisión a 1440 px |
| **F7 · Entrega** | README de instalación paso a paso (incluidos el primer inicio de sesión, la clave de TMDB y el aviso de pantalla negra), prueba de aceptación completa | Criterios de aceptación ↓ (resultado en [06-aceptacion.md](06-aceptacion.md)) |

## Pruebas

- **Unitarias** (`pc-app/tests/`): CSV con comillas y comas, normalización de títulos, puntuación de candidatos, nota ponderada, filtro de la aleatoria y no repetición, búsqueda sin acentos, validación de `seconds` frente a la duración, token y filtro de IP, marcador.
- **Integración**: rutas FastAPI con un `FakePlayer`; importador con respuestas de TMDB grabadas (`unittest.mock` / `respx`).
- **Manual** (la API de Netflix no se puede automatizar en CI): lista de comprobación de F3 y F7.

## Criterios de aceptación (de la especificación, adaptados)

1. Desde el móvil busco «Origen», veo su carátula, elijo 01:23:45 en la rueda y en menos de 6 s el PC muestra la película pausada en ese segundo (±2 s), a pantalla completa y sin título ni barra de progreso visibles.
2. «Revelar» muestra el título y el póster.
3. Tras reiniciar el PC, no hace falta volver a iniciar sesión en Netflix.
4. Si Netflix pide elegir perfil o muestra un aviso, la app lo resuelve o avisa en el móvil con un mensaje claro en español.
5. «Película al azar» solo propone películas posteriores a 1990, conocidas o bien valoradas y disponibles en España.
6. El marcador se ve en la pantalla de espera de la tele y se conserva entre reinicios.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| La API interna del reproductor de Netflix o los selectores cambian | Todo el JavaScript está en `netflix_api.js`, con *fallbacks*, y los selectores se pueden cambiar en `config.toml`. Los errores son claros. |
| Emparejamientos erróneos con TMDB (remakes, títulos genéricos) | Bonus por disponibilidad en Netflix ES, nivel de confianza y corrección manual en el panel. |
| Datos de disponibilidad en ES desactualizados | Interruptor «incluir no confirmadas». Si Netflix responde «no disponible» al reproducir, se marca `netflix_es = 0` automáticamente. |
| Pantalla negra al transmitir (DRM) | Aviso en el README: desactivar la aceleración por hardware (`--disable-gpu`, opción en `config.toml`; en Edge suele bastar para que no use el DRM por hardware de PlayReady), cambiar a Chrome con `channel = "chrome"` o usar HDMI. |
| Automatizar Netflix puede ir contra sus condiciones de uso | Uso personal con la cuenta del usuario y reproducción normal, sin capturas. El usuario asume ese uso. |
| Superar los 6 s | Edge siempre abierto, CSS inyectado de antemano y espera por eventos (no por tiempos fijos). Se mide en la prueba manual. |
