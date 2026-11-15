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
