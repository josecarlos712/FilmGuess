// Textos del editor de packs (español).
const TEXTOS = {
  marca: "Packs · Adivina la Peli",
  volver: "← Volver al panel",
  subtitulo: "Elige qué películas pueden salir al azar.",
  nuevoCampo: "Nombre del pack nuevo",
  nuevoPlaceholder: "Nombre del pack nuevo",
  nuevoBoton: "Crear pack",
  nombreCampo: "Nombre",
  mezclaCampo: "Mezcla al azar",
  mezcla: (n) =>
    n === 10
      ? "Solo salen películas que conocéis."
      : n === 0
        ? "Solo salen películas que no conocéis."
        : `De cada 10 rondas, ${n} con películas que conocéis y ${10 - n} con las que no.`,
  borrar: "Borrar pack",
  confirmarBorrar: (nombre) => `¿Borrar el pack «${nombre}»? No se puede deshacer.`,
  guardando: "Guardando…",
  guardado: "Guardado",
  vistaExplorar: "Catálogo",
  vistaPack: (n) => `En el pack (${n})`,
  buscarCampo: "Buscar película",
  buscarPlaceholder: "Buscar por título",
  categoriaCampo: "Categoría",
  todasCategorias: "Todas las categorías",
  ayudaExplorar: (total) =>
    `${total.toLocaleString("es-ES")} películas, de la más famosa a la menos. Marca las que quieras en el pack.`,
  ayudaPack: (c, d) => `${c} que conocéis · ${d} que no conocéis`,
  conozco: "La conozco",
  noConozco: "No la conozco",
  cargarMas: "Cargar más",
  vacioExplorar: "No hay películas con ese filtro.",
  vacioPack: "El pack está vacío. Ve a «Catálogo» y marca películas.",
  bienvenidaTitulo: "Crea tu primer pack",
  bienvenidaTexto:
    "Ponle un nombre arriba. Después marca las películas que conocéis y algunas que no, para que no sea tan fácil. En el móvil podrás elegir el pack antes de pulsar «Al azar».",
  errorRed: "No se pudo conectar con el editor. ¿Sigue abierta la ventana de arrancar.bat?",
};
