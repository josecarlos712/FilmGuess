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
| GET/POST | `/players` | alta, baja y puntos de jugadores | marcador |
| WS | `/ws?token=` | — | eventos `estado`, `error`, `marcador` |
| POST | `/admin/movies` | `{url}` (solo localhost) | alta por enlace (D5) |
| PATCH | `/admin/movies/{id}` | corrección del emparejamiento TMDB (solo localhost) | ficha |

Códigos de error previstos: `token_invalido`, `no_encontrada`, `no_disponible_es`, `perfil_requerido`, `sesion_caducada`, `tiempo_agotado`, `fuera_de_duracion`, `navegador_cerrado`, `netflix_error`.

## Seguridad

- El servidor escucha en `0.0.0.0:8765`, pero rechaza clientes que no sean de redes privadas (RFC 1918 o enlace local) o de localhost.
- **Token de emparejamiento**: 128 bits aleatorios, guardado en `config.toml`. Va en el QR dentro del *fragment* (`#token=`), que no se envía en peticiones ni queda en registros; el móvil lo guarda en `localStorage` y lo manda como `X-Token`. Se puede regenerar desde el panel.
- `/panel`, `/tv` y `/admin/*` solo aceptan peticiones desde `127.0.0.1`.
- La clave de TMDB solo existe en el PC.
- Comparación del token en tiempo constante (`hmac.compare_digest`).
