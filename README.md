# Adivina la Peli

Juego de salón: desde el móvil se elige una película del catálogo de Netflix España y un instante exacto (hh:mm:ss). El PC abre esa película en Netflix justo en ese segundo, la pausa y oculta cualquier pista (título, barra de progreso). Los jugadores adivinan. La pantalla del PC se transmite a la tele con una app externa (fuera de este proyecto).

> La app **no descarga, graba ni captura** vídeo ni fotogramas. Solo controla la reproducción normal de Netflix con la cuenta del usuario.

## Estado

Fase de **planificación**. La instalación paso a paso se completará al terminar la implementación (fase F7 del plan).

## Documentación

| Documento | Contenido |
|---|---|
| [docs/01-decisiones.md](docs/01-decisiones.md) | Decisiones tomadas y en qué se aparta de la especificación original |
| [docs/02-arquitectura.md](docs/02-arquitectura.md) | Componentes, flujo de una ronda, API y seguridad |
| [docs/03-catalogo.md](docs/03-catalogo.md) | Cómo se construye el catálogo: CSV + TMDB + valoraciones IMDb |
| [docs/04-diseno.md](docs/04-diseno.md) | Pantallas e interfaz siguiendo taste-skill |
| [docs/05-plan.md](docs/05-plan.md) | Fases de implementación, pruebas, requisitos previos y riesgos |

## Estructura prevista

```
FilmGuess/
├── pc-app/        Servidor FastAPI + control de Microsoft Edge con Playwright + catálogo
├── mobile-app/    Web móvil (HTML/CSS/JS sin compilación) que sirve el propio PC
├── data/          Catálogo SQLite, CSV de origen, marcador (no se versiona)
└── docs/          Esta documentación
```
