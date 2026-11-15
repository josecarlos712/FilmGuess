# 04 · Diseño e interfaz

## taste-skill: cómo se aplica

[taste-skill](https://github.com/Leonxlnx/taste-skill) (`design-taste-frontend`, MIT) se instala en `.claude/skills/design-taste-frontend` del proyecto. Está pensada para **webs corporativas**, así que se aplica así:

| Regla de la skill | Aplicación en este proyecto |
|---|---|
| Un único sistema de diseño (tipografía de Google Fonts, paleta repartida por tipo de página, `--radius`) | Un solo `system.css` compartido por móvil, tele y panel. Tipo de página: **gaming** (juego de salón). La paleta y las fuentes salen del reparto de la skill durante la implementación. |
| Nunca violeta ni lavanda | Se respeta. |
| Prohibido: *fade-in* al hacer scroll, *parallax* en texto, *scroll-jacking*, *tilt*, carruseles, pestañas temporizadas, parpadeos | Se respeta en todo. |
| Sin pie de foto bajo las imágenes | Los pósters de la cuadrícula muestran título y año como información de la tarjeta, no como pie de foto decorativo. |
| Movimiento: entrada única, *hover* sutil (subida de 2 px, zoom del póster de 1,02-1,03), botones con efecto de pulsación, `prefers-reduced-motion` respetado | Se aplica tal cual. En el móvil, el estado de pulsación sustituye al *hover*. |
| Prohibido inventar cifras, reseñas o logos | La tele y el móvil solo muestran datos reales (catálogo y marcador). El logo es tipográfico. |
| Comprobación a 1440/1024/768/390 px | El móvil se valida a 390 y 320 px; la tele, a 1920×1080 y 1280×720; el panel, a 1440 y 1024 px. Se usa `gate.mjs` donde encaje. |
| *Hero*, navegación fija y pie de página | **Se adaptan**: en el móvil, barra superior con el estado de la conexión y navegación por pasos; la tele no lleva navegación; el panel lleva una barra lateral. |

## Pantallas

### Móvil (`/m/`)
1. **Conexión**: si llega con `#token`, se conecta sola. Si no, pide la IP y el token, con el mensaje «Escanea el código QR de la tele con la cámara». Recuerda la última conexión.
2. **Buscador**: campo grande con el foco puesto y cuadrícula de 2 columnas (3 en apaisado) con póster, título y año. Botón **«Película al azar»** (el título queda oculto hasta revelarlo, para que quien elige tampoco lo sepa). Interruptor «Incluir no confirmadas en España».
3. **Tiempo**: rueda de **tres columnas, horas, minutos y segundos**, con `scroll-snap`, al estilo de un temporizador. El máximo es la duración de la película. Atajos: «Al azar» (entre el 5 % y el 90 % de la duración). Botón principal **«Mostrar en la tele»**, con su estado de carga y el error en español si falla.
4. **Ronda**: botones **Revelar**, **Reanudar**, **Pausar** y **Nueva ronda**. Marcador: jugadores con botones +1 y −1.
5. **Jugadores**: añadir, renombrar y quitar jugadores, y poner el marcador a cero.

### Tele (`/tv`, la pestaña que se transmite)
- **Espera**: logo tipográfico grande, QR de conexión y marcador. Fondo oscuro, legible a 3 metros (texto de al menos 32 px a 1080p).
- **Cargando**: pantalla neutra «Preparando fotograma…». No llega a verse Netflix mientras carga, porque el CSS de ocultación se inyecta antes.
- **Revelar**: capa sobre el vídeo pausado con el póster `w780`, el título en español, el original si es distinto, y el año.

### Panel del PC (`/panel`, solo localhost)
- Estado: navegador, sesión de Netflix, móviles conectados, última orden y último error.
- QR y botón «Regenerar token».
- Catálogo: progreso de la importación, botón «Importar o actualizar», lista de emparejamientos de confianza baja para corregir, y alta por enlace.
- Botón «Abrir Netflix para iniciar sesión», que la primera vez saca Edge de la pantalla completa.

## Textos

Todo en español de España, en un tono directo y sin tecnicismos. Por ejemplo, «Netflix pide elegir perfil. Configúralo en el panel del PC.» y no «Error 409: profile_required». Los textos están centralizados en un único módulo por interfaz, para que sea fácil revisarlos.
