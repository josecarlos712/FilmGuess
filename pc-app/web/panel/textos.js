// Textos del panel del PC, todos en un sitio para revisarlos de una vez.
export const TEXTOS = {
  marca: "Adivina la peli",
  subtitulo: "Panel del PC",
  navEstado: "Estado",
  navConexion: "Conexión",
  navNetflix: "Netflix",
  navCatalogo: "Catálogo",
  navAlta: "Añadir película",

  estadoTitulo: "Estado",
  navegador: "Navegador de la tele",
  abierto: "Abierto",
  cerrado: "Cerrado",
  sesion: "Sesión de Netflix",
  sesionOk: "Iniciada",
  sesionNo: "Caducada",
  sesionNs: "Sin comprobar",
  moviles: "Móviles conectados",
  tele: "Pantalla de la tele",
  teleOk: "En espera",
  teleNo: "En Netflix",
  teleCerrada: "Sin abrir",
  ultimaOrden: "Última orden",
  ultimoError: "Último error",
  ninguna: "Ninguna",
  ordenes: {
    play: "Mostrar película",
    pause: "Pausar",
    resume: "Reanudar",
    reveal: "Revelar",
    idle: "Nueva ronda",
    login: "Abrir Netflix",
  },
  ninguno: "Ninguno",
  sinServidor: "No hay respuesta del servidor. ¿Sigue abierto en la consola?",

  conexionTitulo: "Conexión del móvil",
  conexionAyuda: "Escanea el QR con el móvil (en la misma wifi que el PC) o abre el enlace.",
  copiar: "Copiar enlace",
  copiado: "Enlace copiado",
  regenerar: "Cambiar código",
  regenerarAyuda: "Los móviles conectados se desconectan y tendrán que escanear el QR nuevo.",
  confirmarRegenerar: "¿Cambiar el código? Los móviles tendrán que volver a escanear el QR.",
  regenerado: (n) =>
    n === 1 ? "Código cambiado. Se ha desconectado 1 móvil." : `Código cambiado. Se han desconectado ${n} móviles.`,

  netflixTitulo: "Sesión de Netflix",
  netflixAyuda:
    "Si Netflix ha cerrado la sesión, ábrelo aquí: la ventana de la tele sale de la pantalla completa para que puedas escribir. Cuando termines, vuelve a la pantalla de espera.",
  abrirNetflix: "Abrir Netflix para iniciar sesión",
  volverEspera: "Volver a la pantalla de espera",
  netflixAbierto: "Netflix está abierto en la ventana de la tele.",
  esperaLista: "La tele vuelve a mostrar la pantalla de espera.",

  catalogoTitulo: "Catálogo",
  total: "Películas",
  disponibles: "En Netflix España",
  alta: "Confianza alta",
  media: "Confianza media",
  baja: "Confianza baja",
  sinMatch: "Sin emparejar",
  pendientes: "Pendientes",
  conNota: "Con nota de IMDb",
  sinClave:
    "Este catálogo viene incluido con la app: no hace falta importar nada para jugar. Para ampliarlo o corregirlo, añade una clave de TMDB en config.toml ([tmdb] api_key) y reinicia.",
  importar: "Importar o actualizar",
  importando: "Importando…",
  importarAyuda:
    "Lee el CSV y Wikidata, empareja las nuevas con TMDB y actualiza las notas de IMDb. Puede tardar varios minutos; el juego sigue funcionando mientras tanto.",
  progreso: (done, total) =>
    `${done.toLocaleString("es-ES")} de ${total.toLocaleString("es-ES")} películas emparejadas con TMDB`,
  importacionLista: (fecha) => `Última importación terminada: ${fecha}`,
  resumen: (r) =>
    [
      r.csv_added != null ? `${r.csv_added} nuevas del CSV` : null,
      r.wikidata_added != null ? `${r.wikidata_added} enlazadas con Wikidata` : null,
      r.imdb_updated != null ? `${r.imdb_updated} notas de IMDb` : null,
    ]
      .filter(Boolean)
      .join(" · "),

  revisarTitulo: "Emparejamientos para revisar",
  revisarAyuda:
    "Comprueba que el título del CSV y el de TMDB son la misma película. Si no, pega el enlace correcto de themoviedb.org.",
  filtroBaja: "Confianza baja",
  filtroSinMatch: "Sin emparejar",
  colCsv: "En el CSV",
  colTmdb: "Emparejada con",
  colCorregir: "Enlace correcto de TMDB",
  sinEmparejar: "Sin emparejar",
  corregir: "Corregir",
  corregida: "Corregida",
  enlaceTmdb: "https://www.themoviedb.org/movie/…",
  nadaQueRevisar: "No hay nada que revisar aquí.",
  anterior: "Anterior",
  siguiente: "Siguiente",
  pagina: (p, n) => `Página ${p} de ${n}`,
  abrirNetflixFicha: "Ver en Netflix",

  altaTitulo: "Añadir película por enlace",
  altaAyuda:
    "Para una película que está en Netflix pero no aparece en el buscador: pega su enlace de Netflix y su ficha de TMDB.",
  altaNetflix: "Enlace de Netflix",
  altaNetflixEj: "https://www.netflix.com/title/81435227",
  altaTmdb: "Enlace de TMDB",
  altaTmdbEj: "https://www.themoviedb.org/movie/607259",
  anadir: "Añadir al catálogo",
  anadida: (t) => `«${t}» está en el catálogo.`,
  actualizada: (t) => `«${t}» ya estaba: se ha corregido su ficha.`,

  errorDesconocido: "Algo ha fallado. Prueba otra vez.",
};
