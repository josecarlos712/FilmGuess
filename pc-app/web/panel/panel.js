// Panel del PC (/panel, solo localhost): estado, conexión, Netflix y catálogo.
import { TEXTOS } from "./textos.js";

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n ?? 0).toLocaleString("es-ES");

for (const el of document.querySelectorAll("[data-text]")) {
  const value = TEXTOS[el.dataset.text];
  if (typeof value === "string") el.textContent = value;
  if (el.tagName === "INPUT") el.placeholder = value;
}
$("alta-netflix").placeholder = TEXTOS.altaNetflixEj;
$("alta-tmdb").placeholder = TEXTOS.altaTmdbEj;

// --- API ---------------------------------------------------------------------------

class PanelError extends Error {}

async function call(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new PanelError(TEXTOS.sinServidor);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    // sin JSON
  }
  if (!res.ok || (data && data.ok === false)) {
    throw new PanelError((data && data.mensaje) || TEXTOS.errorDesconocido);
  }
  return data;
}

function note(id, text, isError = false) {
  const node = $(id);
  node.textContent = text || "";
  node.classList.toggle("hidden", !text);
  node.classList.toggle("error", isError);
}

async function busy(button, noteId, action) {
  if (button.disabled) return;
  button.disabled = true;
  note(noteId, "");
  try {
    await action();
  } catch (error) {
    note(noteId, error instanceof PanelError ? error.message : TEXTOS.errorDesconocido, true);
  } finally {
    button.disabled = false;
  }
}

// --- Estado ------------------------------------------------------------------------

const STATS = [
  ["total", "total"],
  ["netflix_es", "disponibles"],
  ["alta", "alta"],
  ["media", "media"],
  ["baja", "baja"],
  ["sin_match", "sinMatch"],
  ["pendientes", "pendientes"],
  ["imdb", "conNota"],
];

let counts = { baja: 0, sin_match: 0 };
let wasImporting = false;

function renderStatus(s) {
  $("v-navegador").textContent = s.navegador ? TEXTOS.abierto : TEXTOS.cerrado;
  $("v-navegador").dataset.ok = String(Boolean(s.navegador));
  const session =
    s.sesionNetflix === true ? "sesionOk" : s.sesionNetflix === false ? "sesionNo" : "sesionNs";
  $("v-sesion").textContent = TEXTOS[session];
  $("v-sesion").dataset.ok =
    s.sesionNetflix === null ? "unknown" : String(s.sesionNetflix === true);
  $("v-moviles").textContent = String(s.moviles);
  const tv = s.tele > 0 ? "teleOk" : s.navegador ? "teleNo" : "teleCerrada";
  $("v-tele").textContent = TEXTOS[tv];
  $("v-tele").dataset.ok = tv === "teleCerrada" ? "false" : "true";
  $("v-orden").textContent = s.ultimaOrden ? describeCommand(s.ultimaOrden) : TEXTOS.ninguna;
  $("v-error").textContent = s.ultimoError ? s.ultimoError.mensaje : TEXTOS.ninguno;
  $("v-error").classList.toggle("bad", Boolean(s.ultimoError));
  $("url").textContent = s.conexion.url;

  const stats = $("stats");
  stats.replaceChildren();
  for (const [key, label] of STATS) {
    const item = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = TEXTOS[label];
    const dd = document.createElement("dd");
    dd.className = "num";
    dd.textContent = fmt(s.catalogo[key]);
    item.append(dt, dd);
    stats.appendChild(item);
  }
  const canEdit = s.editarCatalogo !== false;
  $("sin-clave").classList.toggle("hidden", canEdit);
  $("herramientas").classList.toggle("hidden", !canEdit);
  $("alta").classList.toggle("hidden", !canEdit);
  counts = { baja: s.catalogo.baja, sin_match: s.catalogo.sin_match };
  renderSegments();
  renderImport(s.importacion);
}

function describeCommand(order) {
  // El servidor la da como «play · 2026-11-15T10:20:30+00:00».
  const [name, iso] = String(order).split(" · ");
  const label = TEXTOS.ordenes[name] || name;
  const when = iso ? new Date(iso) : null;
  return when && !Number.isNaN(when.getTime())
    ? `${label} · ${when.toLocaleTimeString("es-ES")}`
    : label;
}

function renderImport(st) {
  const running = st.running;
  $("btn-importar").disabled = running;
  $("btn-importar").textContent = running ? TEXTOS.importando : TEXTOS.importar;
  $("import-progress").classList.toggle("hidden", !running && !st.total);
  const pct = st.total ? Math.round((st.done / st.total) * 100) : running ? 4 : 0;
  $("import-bar").style.width = `${pct}%`;
  $("import-text").textContent = st.total ? TEXTOS.progreso(st.done, st.total) : "";
  if (!running && st.finished_at) {
    const date = new Date(st.finished_at).toLocaleString("es-ES");
    const summary = TEXTOS.resumen(st.summary || {});
    $("import-done").textContent = [TEXTOS.importacionLista(date), summary]
      .filter(Boolean)
      .join(" — ");
    $("import-done").classList.remove("hidden");
    note("msg-importar", st.failed ? st.message : "", st.failed);
  }
  if (wasImporting && !running) loadReview();
  wasImporting = running;
}

let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  let delay = 2000;
  try {
    const s = await call("GET", "/panel/api/estado");
    $("sin-servidor").classList.add("hidden");
    renderStatus(s);
    if (s.importacion.running) delay = 1000;
  } catch {
    $("sin-servidor").classList.remove("hidden");
    delay = 4000;
  }
  pollTimer = setTimeout(poll, delay);
}

// --- Conexión ----------------------------------------------------------------------

$("btn-copiar").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("url").textContent);
    note("msg-conexion", TEXTOS.copiado);
  } catch {
    note("msg-conexion", TEXTOS.errorDesconocido, true);
  }
});

$("btn-token").addEventListener("click", (e) => {
  if (!window.confirm(TEXTOS.confirmarRegenerar)) return;
  busy(e.currentTarget, "msg-conexion", async () => {
    const r = await call("POST", "/panel/api/token");
    $("url").textContent = r.url;
    $("qr").src = `/pairing/qr.svg?v=${Date.now()}`;
    note("msg-conexion", TEXTOS.regenerado(r.desconectados));
  });
});

// --- Netflix -----------------------------------------------------------------------

$("btn-login").addEventListener("click", (e) =>
  busy(e.currentTarget, "msg-netflix", async () => {
    await call("POST", "/panel/api/netflix-login");
    note("msg-netflix", TEXTOS.netflixAbierto);
  }),
);
$("btn-espera").addEventListener("click", (e) =>
  busy(e.currentTarget, "msg-netflix", async () => {
    await call("POST", "/panel/api/espera");
    note("msg-netflix", TEXTOS.esperaLista);
  }),
);

// --- Catálogo ----------------------------------------------------------------------

$("btn-importar").addEventListener("click", (e) =>
  busy(e.currentTarget, "msg-importar", async () => {
    const r = await call("POST", "/panel/api/importar");
    renderImport(r.importacion);
    poll();
  }),
);

const review = { conf: "baja", page: 1, pages: 1 };

function renderSegments() {
  for (const seg of document.querySelectorAll(".seg")) {
    const label = seg.dataset.conf === "baja" ? TEXTOS.filtroBaja : TEXTOS.filtroSinMatch;
    seg.textContent = `${label} · ${fmt(counts[seg.dataset.conf])}`;
    seg.classList.toggle("on", seg.dataset.conf === review.conf);
    seg.setAttribute("aria-pressed", String(seg.dataset.conf === review.conf));
  }
}

for (const seg of document.querySelectorAll(".seg")) {
  seg.addEventListener("click", () => {
    review.conf = seg.dataset.conf;
    review.page = 1;
    renderSegments();
    loadReview();
  });
}
$("btn-anterior").addEventListener("click", () => {
  if (review.page > 1) {
    review.page -= 1;
    loadReview();
  }
});
$("btn-siguiente").addEventListener("click", () => {
  if (review.page < review.pages) {
    review.page += 1;
    loadReview();
  }
});

function cell(...children) {
  const td = document.createElement("td");
  td.append(...children);
  return td;
}

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value ?? "";
  return node;
}

function movieLabel(m) {
  const wrap = document.createElement("div");
  wrap.className = "match";
  if (m.poster) {
    const img = document.createElement("img");
    img.src = m.poster;
    img.alt = "";
    img.loading = "lazy";
    img.addEventListener("error", () => img.remove());
    wrap.appendChild(img);
  }
  const body = document.createElement("div");
  if (m.tmdbId) {
    body.append(
      text("p", "match-title", m.titulo),
      text("p", "muted small", [m.tituloOriginal !== m.titulo ? m.tituloOriginal : null, m.anio].filter(Boolean).join(" · ")),
    );
    const link = document.createElement("a");
    link.href = `https://www.themoviedb.org/movie/${m.tmdbId}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.className = "small";
    link.textContent = `TMDB ${m.tmdbId}`;
    body.appendChild(link);
  } else {
    body.appendChild(text("p", "muted", TEXTOS.sinEmparejar));
  }
  wrap.appendChild(body);
  return wrap;
}

function reviewRow(m) {
  const tr = document.createElement("tr");
  const csv = document.createElement("div");
  csv.append(text("p", "csv-title", m.tituloCsv));
  const nf = document.createElement("a");
  nf.href = `https://www.netflix.com/title/${m.netflixId}`;
  nf.target = "_blank";
  nf.rel = "noopener";
  nf.className = "small num";
  nf.textContent = `Netflix ${m.netflixId}`;
  csv.appendChild(nf);

  const form = document.createElement("form");
  form.className = "fix";
  form.noValidate = true;
  const input = document.createElement("input");
  input.type = "url";
  input.placeholder = TEXTOS.enlaceTmdb;
  input.setAttribute("aria-label", `${TEXTOS.colCorregir}: ${m.tituloCsv}`);
  const button = text("button", "btn secondary", TEXTOS.corregir);
  button.type = "submit";
  const msg = text("p", "note small hidden");
  form.append(input, button, msg);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!input.value.trim() || button.disabled) return;
    button.disabled = true;
    msg.classList.add("hidden");
    try {
      const r = await call("POST", `/panel/api/peliculas/${m.netflixId}/tmdb`, {
        tmdb: input.value.trim(),
      });
      tr.children[1].replaceChildren(movieLabel(r.pelicula));
      form.replaceChildren(text("p", "ok-text", TEXTOS.corregida));
    } catch (error) {
      msg.textContent = error.message;
      msg.classList.remove("hidden");
      msg.classList.add("error");
      button.disabled = false;
    }
  });

  tr.append(cell(csv), cell(movieLabel(m)), cell(form));
  return tr;
}

async function loadReview() {
  try {
    const r = await call(
      "GET",
      `/panel/api/revisar?confianza=${review.conf}&pagina=${review.page}`,
    );
    review.pages = Math.max(1, Math.ceil(r.total / r.porPagina));
    const body = $("revisar");
    body.replaceChildren(...r.resultados.map(reviewRow));
    $("nada").classList.toggle("hidden", r.total > 0);
    document.querySelector(".review").classList.toggle("hidden", r.total === 0);
    $("pagina").textContent = TEXTOS.pagina(review.page, review.pages);
    $("btn-anterior").disabled = review.page <= 1;
    $("btn-siguiente").disabled = review.page >= review.pages;
  } catch (error) {
    $("nada").textContent = error.message;
    $("nada").classList.remove("hidden");
  }
}

// --- Alta por enlace ---------------------------------------------------------------

$("form-alta").addEventListener("submit", (event) => {
  event.preventDefault();
  const netflix = $("alta-netflix").value.trim();
  const tmdb = $("alta-tmdb").value.trim();
  if (!netflix || !tmdb) return;
  const button = event.submitter || $("form-alta").querySelector("button");
  busy(button, "msg-alta", async () => {
    const r = await call("POST", "/panel/api/peliculas", { netflix, tmdb });
    const title = r.pelicula.titulo;
    note("msg-alta", r.nueva ? TEXTOS.anadida(title) : TEXTOS.actualizada(title));
    $("alta-netflix").value = "";
    $("alta-tmdb").value = "";
  });
});

poll();
loadReview();
