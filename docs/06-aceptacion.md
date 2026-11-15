# Prueba de aceptación

Estado de los seis criterios de [05-plan.md](05-plan.md#criterios-de-aceptación-de-la-especificación-adaptados) al cerrar la F7 (15-11-2026).

| # | Criterio | Cómo se ha comprobado | Resultado |
|---|---|---|---|
| 1 | Buscar, elegir 01:23:45 y ver la película pausada en ese segundo, a pantalla completa y sin pistas, en menos de 6 s (±2 s) | Tests de la lógica del reproductor con Playwright simulado (`test_netflix_player.py`, `test_netflix_js.py`) y la prueba manual de la F3 con Netflix real | En la F3: **6,5 s** y desviación de **±1 s**. La precisión cumple; el tiempo queda medio segundo por encima en el primer intento en frío (el navegador ya abierto y la película en caché lo rebajan). Hay que repetirlo con la lista de abajo |
| 2 | «Revelar» enseña el título y el póster | `test_server.py` (ruta y estado `revelada`) y `test_netflix_js.py` (capa de revelado, título original, póster que no carga) | Automático: pasa. Falta verlo en la tele real |
| 3 | Tras reiniciar el PC no hay que volver a iniciar sesión | Perfil persistente `data/browser-profile` (F3) | Pendiente de la prueba manual |
| 4 | Selector de perfil o avisos: los resuelve o avisa en el móvil en español | `test_netflix_player.py` (perfil por nombre, sesión cerrada, no disponible) y `test_server.py` (errores `{ok:false, error, mensaje}`) | Automático: pasa. Falta con Netflix real |
| 5 | «Película al azar» solo propone películas posteriores a 1990, conocidas o bien valoradas y disponibles en España | `test_importer_imdb_random.py` y una comprobación sobre el catálogo real (52 838 títulos): **300 tiradas**, 0 de 1990 o antes, 0 no disponibles en España, todas con 25 000 votos o más y emparejamiento de confianza alta | **Pasa** |
| 6 | El marcador se ve en la espera de la tele y se conserva entre reinicios | `test_pairing_scoreboard.py` (guardado en disco y recarga) y la pantalla de espera revisada en el navegador (F4) | **Pasa** |

Las 300 tiradas de la comprobación del criterio 5 se borraron después del historial `random_history`, para que no cuenten en la regla de no repetir.

## Lista para la prueba manual con Netflix real

*Origen* no está en Netflix España; usa cualquier película que sí lo esté y dure más de 1 h 24 min.

1. Reinicia el PC y ejecuta `arrancar.bat`. **Criterio 3:** la tele llega a la pantalla de espera sin pedir inicio de sesión.
2. Conecta el móvil con el QR. Busca la película, comprueba que sale su carátula y elígela.
3. Pon 01:23:45 en la rueda, pulsa «Mostrar en la tele» y cronometra hasta ver el fotograma. **Criterio 1:** menos de 6 s, pausado, a pantalla completa, sin título ni barra, y el reloj de Netflix (pulsa Reanudar y Pausar) entre 01:23:43 y 01:23:47.
4. Pulsa «Revelar». **Criterio 2:** título, título original si es distinto, año y póster.
5. Suma puntos a un jugador y pulsa «Nueva ronda». **Criterio 6:** el marcador sale en la espera; cierra la app, vuelve a abrirla y sigue igual.
6. **Criterio 4:** en el panel, «Abrir Netflix para iniciar sesión», cierra la sesión de Netflix y vuelve a la espera; al lanzar una ronda el móvil debe decir que Netflix ha cerrado la sesión. Inicia sesión de nuevo. Si la cuenta tiene varios perfiles, quita `profile_name` y comprueba el aviso de elegir perfil.
7. Si la tele muestra el vídeo en negro al transmitir, sigue la tabla «Si algo falla» del README.
