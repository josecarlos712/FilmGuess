# 02 · Arquitectura

## Vista general

```
┌──────────── Móvil (navegador) ────────────┐
│  http://<ip-pc>:8765/m/#token=…           │
│  Conexión · Buscador · Rueda hh:mm:ss ·   │
│  Ronda · Marcador                         │
└───────────────┬───────────────────────────┘
                │ HTTP + WebSocket (WiFi local, cabecera X-Token)
┌───────────────▼──────────── PC (pc-app) ──────────────────────────┐
│  FastAPI (puerto 8765)                                             │
│   ├─ API  /status /search /random /play /pause /resume /reveal …   │
│   ├─ /ws  estado en tiempo real                                     │
│   ├─ /m/      web móvil (estáticos de mobile-app/)                  │
│   ├─ /panel   panel de control (solo localhost)                     │
│   └─ /tv      pantalla de espera y marcador (solo localhost)        │
│                                                                     │
│  Catálogo (SQLite + FTS5) ← importador CSV + TMDB + IMDb            │
│  Juego: ronda actual, marcador (data/scoreboard.json)               │
│  Reproductor: Playwright → Microsoft Edge (perfil persistente)    │
└───────────────┬─────────────────────────────────────────────────────┘
                │ una sola pestaña, a pantalla completa
         ┌──────▼──────┐        transmisión (app del usuario)
         │ /tv ⇄ Netflix│ ─────────────────────────────► Tele LG
         └─────────────┘
```

## Módulos de `pc-app/src/filmguess/`

| Módulo | Responsabilidad |
|---|---|
| `main.py` | Punto de entrada: carga la configuración, arranca Edge y el servidor, imprime el QR en consola. |
| `config.py` | Lee `pc-app/config.toml` (puerto, token, clave TMDB, perfil de Netflix, selectores). Valida con pydantic. |
| `server/` | App FastAPI, rutas, dependencia de autenticación, WebSocket y difusión de eventos. Sin lógica de negocio. |
| `player/browser.py` | Ciclo de vida de Playwright: `launch_persistent_context(channel="msedge")` (canal configurable: `msedge` o `chrome`), pestaña única, pantalla completa por CDP. |
| `player/netflix.py` | `play(id, s)`, `pause()`, `resume()`, detección de interrupciones (perfiles, "¿Sigues viendo?", no disponible, error). |
| `player/netflix_api.js` | JavaScript inyectado: acceso encapsulado a la API interna del reproductor con *fallbacks*. |
| `player/overlays.py` | CSS que oculta los controles y el título; capa de "Revelar". |
| `catalog/` | Base de datos, importador (CSV, TMDB, IMDb), búsqueda y selección aleatoria. |
| `catalog/seed.py` | Catálogo incluido: `install_seed` lo instala si no hay catálogo y `export_seed` lo regenera sin historial ni notas de IMDb. |
| `game/` | Estado de la ronda y marcador persistente. |
| `pairing/` | Token, IP de la red local y generación del QR. |

La lógica de negocio (catálogo, juego, reproductor) no depende de FastAPI; las rutas solo la orquestan (requisito de CLAUDE.md).

## Flujo de una ronda

1. El móvil busca «Origen» → `GET /search?q=origen` → resultados con póster, título en español y año.
2. Elige la película y fija `01:23:45` en la rueda (máximo: la duración de la película).
3. Pulsa **Mostrar en la tele** → `POST /play {netflixId, seconds: 5025}`.
4. El PC:
   1. Inyecta el CSS de ocultación **antes** de navegar (`add_init_script`), para que el título no aparezca ni un instante.
   2. Navega a `https://www.netflix.com/watch/{id}?t={seconds}`.
   3. Gestiona interrupciones: selector de perfil (elige el perfil configurado), "¿Sigues viendo?" (pulsa continuar) o "no disponible" (devuelve un error claro).
   4. Espera a que exista `<video>` con `readyState >= 3`.
   5. Comprueba `currentTime` ±2 s; si no coincide, usa `seek(ms)` de la API interna; si no está disponible, recurre a recargar con `?t=`.
   6. Pausa, saca el ratón de la ventana y confirma la pantalla completa.
   7. Responde `{ok: true, actualSeconds}`.
5. **Revelar** → capa con el póster y el título sobre el vídeo pausado.
6. **Nueva ronda** → `POST /idle` → la pestaña vuelve a `/tv` (logo, QR y marcador).

Objetivo: menos de 6 s del paso 3 al 4.7. Edge ya está abierto y con sesión iniciada; solo cuesta la carga del reproductor.

## API

Todas las rutas (salvo `/m/` estática, `/panel` y `/tv`) exigen la cabecera `X-Token`. Los errores se devuelven así: `{ "ok": false, "error": "<código>", "mensaje": "<texto en español>" }`.

| Método | Ruta | Cuerpo / parámetros | Respuesta |
|---|---|---|---|
| GET | `/status` | — | `{navegador, sesionNetflix, pantalla: "espera"\|"pelicula"\|"revelada", rondaActual, ultimaOrden, ultimoError}` |
| GET | `/search` | `q`, `page`, `todas` (incluye las que no constan en Netflix ES) | `{resultados: [{netflixId, titulo, tituloOriginal, anio, duracionSeg, poster}], total}` |
| GET | `/random` | — | Una película del grupo aleatorio (ver catálogo) |
| GET | `/movie/{netflixId}` | — | Ficha completa |
| POST | `/play` | `{netflixId, seconds}` | `{ok, actualSeconds}` |
| POST | `/pause` · `/resume` | — | `{ok}` |
| POST | `/reveal` | — | `{ok, titulo}` |
| POST | `/idle` | — | `{ok}` |
| GET | `/players` | — | marcador `{jugadores: [{id, nombre, puntos}]}`, de más a menos puntos |
| POST | `/players` | `{nombre}` | alta; marcador |
| DELETE | `/players/{id}` | — | baja; marcador |
| POST | `/players/{id}/points` | `{puntos}` (negativo para restar) | marcador |
| POST | `/players/reset` | — | todos a cero; marcador |
| GET | `/pairing` · `/pairing/qr.svg` | — (solo localhost, sin token) | URL de emparejamiento y su QR |
| WS | `/ws?token=` | — | al conectar, `estado` y `marcador`; después, eventos `estado`, `error`, `marcador` como `{tipo, datos}` |
| POST | `/admin/movies` | `{url}` (solo localhost) | alta por enlace (D5) |
| PATCH | `/admin/movies/{id}` | corrección del emparejamiento TMDB (solo localhost) | ficha |

Códigos de error previstos: `token_invalido`, `no_encontrada`, `no_disponible_es`, `perfil_requerido`, `sesion_caducada`, `tiempo_agotado`, `fuera_de_duracion`, `navegador_cerrado`, `netflix_error`. Añadidos en F2: `red_no_permitida`, `solo_local`, `peticion_invalida`, `ruta_no_encontrada`, `sin_ronda`, `sin_candidatas`, `nombre_invalido`, `nombre_repetido`, `demasiados_jugadores`, `jugador_no_encontrado` y `marcador_danado`. El estado HTTP de cada uno está en `server/app.py` (`ERROR_STATUS`).

En los registros de la consola el token se sustituye por `****` (el WebSocket lo recibe en la URL).

## Seguridad

- El servidor escucha en `0.0.0.0:8765`, pero rechaza clientes que no sean de redes privadas (RFC 1918 o enlace local) o de localhost.
- **Token de emparejamiento**: 128 bits aleatorios, guardado en `config.toml`. Va en el QR dentro del *fragment* (`#token=`), que no se envía en peticiones ni queda en registros; el móvil lo guarda en `localStorage` y lo manda como `X-Token`. Se puede regenerar desde el panel.
- `/panel`, `/tv` y `/admin/*` solo aceptan peticiones desde `127.0.0.1`.
- La clave de TMDB solo existe en el PC.
- Comparación del token en tiempo constante (`hmac.compare_digest`).

## Tele (F4)

- `/tv` sirve `pc-app/web/tv/index.html` (solo localhost). Los estáticos (`system.css`, `tv/*`) están en `/static/`.
- `/tv/ws`: WebSocket de la tele, **sin token pero solo desde 127.0.0.1**; recibe los mismos eventos que `/ws`.
- Pantallas de la partida: `espera` → `cargando` → `pelicula` → `revelada`. Al pedir `/play` se emite `cargando` antes de salir de `/tv`; si falla, la partida vuelve a `espera` (se descarta la ronda y el navegador regresa a `/tv`) y la tele muestra el último aviso.
- En Netflix, `netflix_api.js` tapa el vídeo con «Preparando fotograma…» (`#fg-cover`) desde antes de pintarse hasta que el reproductor lo deja pausado en su segundo (`uncover`).
- La capa «Revelar» y la cubierta usan los tokens de `web/system.css` (bloque `tokens:start … tokens:end`, leído por `filmguess.webfiles.design_tokens`): póster `w780`, título, título original si es distinto y año.

## Móvil (F5)

- `/m/` sirve `mobile-app/` (HTML, CSS y JS sin compilación) a la red local; usa `/static/system.css`.
- El token llega en el enlace del QR (`/m/#token=…`): se guarda en el móvil y se quita de la URL. Como la página la sirve el propio PC, la conexión manual solo pide el código, no la IP.
- `PATCH /players/{id}` con `{nombre}` cambia el nombre de un jugador (mismas reglas que el alta).
- «Película al azar» oculta título y póster en el móvil hasta revelar.

## Panel del PC (F6)

- `/panel` y `/panel/api/*` solo desde 127.0.0.1, sin token. La lógica está en `filmguess.panel.service.PanelService`.
- `GET /panel/api/estado`: navegador, sesión de Netflix, móviles y tele conectados (`EventHub` distingue papeles), última orden y error, recuentos del catálogo, importación en curso y URL del QR.
- `POST /panel/api/token`: genera un código nuevo, lo guarda en `config.toml` y cierra los WebSocket de los móviles (código 1008), que tienen que volver a escanear el QR.
- `POST /panel/api/importar`: lanza `run_import` en un hilo; el progreso se lee en el estado.
- `GET /panel/api/revisar?confianza=baja|sin_match&pagina=N`; `POST /panel/api/peliculas/{id}/tmdb {tmdb}` corrige a mano (confianza alta, `manual=1`); `POST /panel/api/peliculas {netflix, tmdb}` da de alta por enlaces.
- `POST /panel/api/netflix-login` saca Edge de la pantalla completa y abre el inicio de sesión; `POST /panel/api/espera` vuelve a la pantalla de espera.

## Pistas, puntos y fotogramas

### Datos (`arrancar.bat pistas`)

Solo para el catálogo de juego (`netflix_es = 1` con ID de TMDB), en la tabla `clues`:

| Campo | Fuente |
|---|---|
| `genres` (JSON `[{id, name}]`) | TMDB `/movie/{id}?language=es-ES` |
| `actor`, `character` | Primer actor del reparto (`credits.cast`, `order` menor) |
| `quote`, `quote_source`, `quote_lang` | Wikiquote (`es` y si no `en`), enlazada desde Wikidata por el ID de TMDB (P4947); si no hay, extracto de la primera crítica de TMDB (`critica`) , el eslogan (`eslogan`) o, si no hay nada más, la sinopsis en español recortada (`sinopsis`) o el director (`director`) |

IMDb no tiene API de críticas y prohíbe extraerlas de su web, por eso la crítica sale de TMDB. Cualquier aparición del título se tapa con «…» (título completo en cualquier caja; palabras sueltas solo con mayúscula inicial). Si el personaje es el título, la pista dice «Su nombre está en el título».

**Traducciones** (`catalog/translations.py`): los textos en inglés se guardan traducidos en `data/traducciones.json` como `{"<id Netflix>": {"original", "es"}}`. `fetch_clues` usa la traducción solo si el texto descargado es igual al `original` guardado; así una nueva descarga no pierde las traducciones y un texto que cambie vuelve a quedar pendiente. `pistas --exportar-sin-traducir FICHERO` saca los pendientes como `[{"id", "texto"}]` y `pistas --aplicar-traducciones` los pone en el catálogo. La primera tanda (1.224 frases y críticas) se tradujo al español de España conservando el nombre de quien dice la frase y quitando enlaces y coletillas de blogs.

### Reglas (`game/hints.py`)

- Sin categoría: 5 puntos y pistas género → año y nota IMDb → actor → personaje → frase.
- Con categoría (`categoria` en `/play`): el género se sabe gratis, 3 puntos y pistas año y nota → protagonista y personaje → frase.
- Cada pista resta 1 punto, mínimo 1. Las pistas sin datos se omiten.
- Gratis: hasta 3 fotogramas (`/frame`, al azar entre el 5 % y el 90 % de la duración y a más de un 10 % de los ya vistos) y un clip de 10 s (`/clip`).
- `/skip` tapa la tele al instante («Saltando escena…»), corta el clip y cambia a otro momento sin gastar fotograma.
- Tras `/reveal`, `/award` da el valor de la ronda a cada acertante una sola vez.

### API

| Ruta | Qué hace |
|---|---|
| `GET /genres` | Categorías con su número de películas |
| `GET /search?categoria=` · `GET /random?categoria=` | Filtran por género |
| `POST /play` `{netflixId, seconds, categoria?}` | Empieza la ronda (422 `categoria_no_coincide`) |
| `POST /hint` | Siguiente pista (409 `sin_pistas`, `ronda_cerrada`) |
| `POST /frame` | Otro fotograma (409 `sin_fotogramas`) |
| `POST /clip` | Clip de 10 s (409 `clip_usado`) |
| `GET /peek` | Título y año de la ronda para verlos solo en el móvil; no cambia la tele (409 `sin_ronda`) |
| `POST /fail` | `{jugadores}`: resta a quien falla lo que vale la ronda en ese momento y lo deja fuera (ya no recibe puntos en `/award`); 409 `ronda_cerrada` si ya está revelada |
| `POST /skip` | Saltar escena |
| `POST /award` `{jugadores: [id]}` | Reparte los puntos (409 `sin_revelar`) |

`rondaActual` en `estado` añade `valor`, `categoria`, `pistas` (`tipo`, `etiqueta`, `texto`), `pistasTotal`, `fotogramas`, `fotogramasMax`, `clipUsado` y `acertantes`. La tele pinta en la esquina inferior izquierda de Netflix (`#fg-hints`) el valor, la categoría, el fotograma y las pistas reveladas; si la película ya está abierta, los otros fotogramas se buscan sin recargar la página.
