# 01 · Decisiones

Registro de las decisiones tomadas con el usuario antes de empezar. Cada una indica si se aparta de la especificación original (`especificacion.md`).

| # | Tema | Decisión | ¿Cambia la especificación? |
|---|---|---|---|
| D1 | App móvil | **Web servida por el propio PC** (HTML/CSS/JS sin paso de compilación). Se abre escaneando el QR con la cámara del móvil; la URL ya lleva el token. Se puede "Añadir a pantalla de inicio". | Sí: sustituye a Flutter/React Native. No hay APK; no hace falta instalar nada en el móvil. La carpeta `mobile-app/` contiene esta web. |
| D2 | Catálogo | **CSV del usuario** (`"Nombre",netflixId`, ~30.160 películas, sin series) como única fuente de IDs. Se enriquece con **TMDB** (título en español, póster, año, duración, disponibilidad en Netflix ES). Solo se buscan películas que estén en el CSV. | Sí: no se usa uNoGS. TMDB solo enriquece, no aporta títulos nuevos. |
| D3 | Película aleatoria | Se elige entre las **mejor valoradas o más conocidas según IMDb, estrenadas después de 1990** (es decir, de 1991 en adelante) y disponibles en Netflix ES. | Concreta el opcional de la especificación. |
| D4 | Revelar | **Capa a pantalla completa** sobre el vídeo pausado con el título en español, el año y el póster. | Concreta una de las dos opciones de la especificación. |
| D5 | Alta manual de películas | En el panel del PC: pegar un enlace `netflix.com/title/{id}` → se extrae el ID, se lee el nombre de la ficha pública y se empareja con TMDB. Los emparejamientos dudosos se pueden corregir a mano. | Añadido. |
| D6 | Interfaz del PC | **Panel web local** (`http://localhost:8765/panel`) con estado, QR, catálogo y errores, más registro en la consola. | Concreta la especificación. |
| D7 | Marcador | **Sí, sencillo**: jugadores, puntos por ronda, se ve en la pantalla de espera de la tele. | Activa el opcional. |
| D8 | Diseño | **taste-skill** se instala en el proyecto y se adapta: su sistema (tipografía, paleta, movimiento, prohibiciones) se aplica a todas las pantallas web; las reglas propias de webs corporativas (hero, pie de página, navegación fija) se adaptan a interfaz de aplicación. Se usará su verificador `gate.mjs` (el usuario instalará Node 22+). | Añadido. |
| D9 | Idioma | Toda la interfaz en **español** (móvil, tele, panel, mensajes de error de la API). El código y sus identificadores, en inglés; docstrings y documentación, en español. | — |

## Decisiones técnicas por defecto (no consultadas, razonadas)

- **Backend**: Python 3.11+, FastAPI + Uvicorn, Playwright (canal `msedge`; `chrome` como alternativa), `httpx` para TMDB, SQLite con FTS5 para la búsqueda, `qrcode` para el QR.
- **Tiempo real**: WebSocket `/ws` para que móvil y panel reciban estado sin sondeo.
- **Un único navegador** (Edge) controlado, con perfil persistente en `data/browser-profile/`, siempre abierto. La pantalla de espera y Netflix se alternan en la misma pestaña, que es la que se transmite a la tele.
- **Pantalla completa** mediante CDP (`Browser.setWindowBounds` con `windowState: fullscreen`), porque F11 no es accesible desde Playwright.
- **Navegador: Microsoft Edge** (ya instalado). Al ser Chromium, Playwright lo controla con `channel="msedge"` y reproduce Netflix (Widevine y PlayReady). Chrome queda como alternativa configurable. **Firefox no sirve**: Playwright solo maneja su propio Firefox modificado, que no trae el DRM de Netflix.
- **Git**: se inicializa un repositorio en la raíz; `data/` y las claves quedan fuera del control de versiones.
