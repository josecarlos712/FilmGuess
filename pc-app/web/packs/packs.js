// Editor de packs: marca películas como conocidas o no conocidas y guarda solo.
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  packs: [], // [{ id, nombre, conocidas, desconocidas, proporcionConocidas }]
  pack: null, // { id, nombre, ratio, marks: Map<netflixId, boolean>, movies: Map<netflixId, movie> }
  view: "explorar",
  page: 1,
  total: 0,
  seq: 0,
  saveTimer: null,
  dirty: false,
  saving: Promise.resolve(),
};

function el(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.textContent = content;
  return node;
}

async function api(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json", "X-Packs": "1" } : { "X-Packs": "1" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(TEXTOS.errorRed);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.mensaje || TEXTOS.errorRed);
  return data;
}

function showError(error) {
  $("error").textContent = error ? error.message : "";
  $("error").classList.toggle("hidden", !error);
}

async function guarded(action) {
  try {
    showError(null);
    return await action();
  } catch (error) {
    showError(error);
    return undefined;
  }
}

// --- Lista de packs ----------------------------------------------------------------

function renderPacks() {
  const nav = $("packs");
  nav.replaceChildren();
  for (const p of state.packs) {
    const tab = el("button", "pack-tab");
    tab.type = "button";
    tab.append(el("span", "", p.nombre), el("span", "num muted", String(p.conocidas + p.desconocidas)));
    const on = state.pack?.id === p.id;
    tab.classList.toggle("on", on);
    tab.setAttribute("aria-pressed", String(on));
    tab.addEventListener("click", () => openPack(p.id));
    nav.appendChild(tab);
  }
  $("editor").classList.toggle("hidden", !state.pack);
  $("bienvenida").classList.toggle("hidden", Boolean(state.pack) || state.packs.length > 0);
}

async function loadPacks() {
  state.packs = (await api("GET", "/api/packs")).packs;
  renderPacks();
}

async function openPack(id) {
  await flushSave();
  const data = await guarded(() => api("GET", `/api/packs/${id}`));
  if (!data) return;
  state.pack = {
    id: data.id,
    nombre: data.nombre,
    ratio: Math.round(data.proporcionConocidas * 10),
    marks: new Map(data.peliculas.map((m) => [m.netflixId, m.conocida])),
    movies: new Map(data.peliculas.map((m) => [m.netflixId, m])),
  };
  try {
    localStorage.setItem("packs.ultimo", id);
  } catch {
    // Sin almacenamiento solo se pierde el recuerdo del último pack abierto.
  }
  $("nombre").value = data.nombre;
  $("mezcla").value = String(state.pack.ratio);
  $("estado").textContent = "";
  renderPacks();
  renderMix();
  renderCounts();
  await showView(state.view);
}

$("form-nuevo").addEventListener("submit", async (event) => {
  event.preventDefault();
  const nombre = $("nuevo-nombre").value.trim();
  if (!nombre) return;
  const data = await guarded(() => api("POST", "/api/packs", { nombre }));
  if (!data) return;
  $("nuevo-nombre").value = "";
  await guarded(loadPacks);
  state.view = "explorar";
  await openPack(data.id);
});

$("btn-borrar").addEventListener("click", async () => {
  const pack = state.pack;
  if (!pack || !window.confirm(TEXTOS.confirmarBorrar(pack.nombre))) return;
  clearTimeout(state.saveTimer);
  state.dirty = false;
  await state.saving;
  const done = await guarded(() => api("DELETE", `/api/packs/${pack.id}`));
  if (!done) return;
  state.pack = null;
  await guarded(loadPacks);
  if (state.packs.length) await openPack(state.packs[0].id);
});

// --- Guardado ----------------------------------------------------------------------

function counts() {
  let known = 0;
  for (const value of state.pack.marks.values()) if (value) known += 1;
  return [known, state.pack.marks.size - known];
}

function scheduleSave() {
  state.dirty = true;
  $("estado").textContent = TEXTOS.guardando;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(flushSave, 500);
}

function flushSave() {
  clearTimeout(state.saveTimer);
  const pack = state.pack;
  if (!pack || !state.dirty) return state.saving;
  state.dirty = false;
  const body = {
    nombre: pack.nombre.trim() || pack.id,
    proporcionConocidas: pack.ratio / 10,
    conocidas: [...pack.marks].filter(([, known]) => known).map(([id]) => id),
    desconocidas: [...pack.marks].filter(([, known]) => !known).map(([id]) => id),
  };
  // Los guardados van en cola: el último enviado es siempre el último estado.
  state.saving = state.saving.then(async () => {
    const saved = await guarded(() => api("PUT", `/api/packs/${pack.id}`, body));
    if (!saved) return;
    const index = state.packs.findIndex((p) => p.id === saved.id);
    if (index >= 0) state.packs[index] = saved;
    if (state.pack === pack && !state.dirty) $("estado").textContent = TEXTOS.guardado;
    renderPacks();
  });
  return state.saving;
}

$("nombre").addEventListener("input", () => {
  state.pack.nombre = $("nombre").value;
  scheduleSave();
});

$("mezcla").addEventListener("input", () => {
  state.pack.ratio = Number($("mezcla").value);
  renderMix();
  scheduleSave();
});

window.addEventListener("beforeunload", () => {
  flushSave();
});

function renderMix() {
  $("mezcla-nota").textContent = TEXTOS.mezcla(state.pack.ratio);
}

function renderCounts() {
  const [known, unknown] = counts();
  document.querySelector('[data-view="pack"]').textContent = TEXTOS.vistaPack(known + unknown);
  if (state.view === "pack") $("ayuda").textContent = TEXTOS.ayudaPack(known, unknown);
}

// --- Cuadrícula --------------------------------------------------------------------

function mark(movie, known) {
  const marks = state.pack.marks;
  if (marks.get(movie.netflixId) === known) {
    marks.delete(movie.netflixId);
  } else {
    marks.set(movie.netflixId, known);
    state.pack.movies.set(movie.netflixId, movie);
  }
  renderCounts();
  scheduleSave();
}

function card(movie) {
  const item = el("li", "movie");
  const frame = el("div", "poster-frame");
  if (movie.poster) {
    const img = el("img");
    img.src = movie.poster;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => img.remove());
    frame.appendChild(img);
  }
  const title = el("p", "movie-title", movie.titulo);
  title.title = movie.titulo;
  const year = el("p", "muted small num", movie.anio ? String(movie.anio) : " ");
  const actions = el("div", "marks");
  const yes = el("button", "mark yes", TEXTOS.conozco);
  const no = el("button", "mark no", TEXTOS.noConozco);
  const paint = () => {
    const value = state.pack.marks.get(movie.netflixId);
    item.dataset.mark = value === undefined ? "" : value ? "si" : "no";
    yes.setAttribute("aria-pressed", String(value === true));
    no.setAttribute("aria-pressed", String(value === false));
  };
  for (const [button, known] of [
    [yes, true],
    [no, false],
  ]) {
    button.type = "button";
    button.addEventListener("click", () => {
      mark(movie, known);
      paint();
    });
  }
  actions.append(yes, no);
  item.append(frame, title, year, actions);
  paint();
  return item;
}

function renderGrid(movies, fresh) {
  const grid = $("rejilla");
  if (fresh) grid.replaceChildren();
  grid.append(...movies.map(card));
}

async function loadCatalog(fresh) {
  state.page = fresh ? 1 : state.page + 1;
  const seq = ++state.seq;
  const params = new URLSearchParams({ q: $("q").value.trim(), pagina: String(state.page) });
  if ($("categoria").value) params.set("categoria", $("categoria").value);
  const data = await guarded(() => api("GET", `/api/peliculas?${params}`));
  if (!data || seq !== state.seq || state.view !== "explorar") return;
  state.total = data.total;
  renderGrid(data.resultados, fresh);
  $("ayuda").textContent = TEXTOS.ayudaExplorar(data.total);
  $("vacio").textContent = TEXTOS.vacioExplorar;
  $("vacio").classList.toggle("hidden", data.total > 0);
  $("btn-mas").classList.toggle("hidden", $("rejilla").children.length >= data.total);
}

function showPackMovies() {
  const pack = state.pack;
  const movies = [...pack.marks.keys()].map((id) => pack.movies.get(id)).filter(Boolean);
  // Primero las conocidas; dentro de cada grupo, por título.
  movies.sort(
    (a, b) =>
      Number(pack.marks.get(b.netflixId)) - Number(pack.marks.get(a.netflixId)) ||
      a.titulo.localeCompare(b.titulo, "es"),
  );
  renderGrid(movies, true);
  $("vacio").textContent = TEXTOS.vacioPack;
  $("vacio").classList.toggle("hidden", movies.length > 0);
  $("btn-mas").classList.add("hidden");
  renderCounts();
}

async function showView(view) {
  state.view = view;
  for (const seg of document.querySelectorAll(".seg")) {
    const on = seg.dataset.view === view;
    seg.classList.toggle("on", on);
    seg.setAttribute("aria-pressed", String(on));
  }
  $("filtros").classList.toggle("hidden", view !== "explorar");
  if (view === "pack") showPackMovies();
  else await loadCatalog(true);
}

for (const seg of document.querySelectorAll(".seg")) {
  seg.addEventListener("click", () => showView(seg.dataset.view));
}

let searchTimer = null;
$("q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadCatalog(true), 300);
});
$("categoria").addEventListener("change", () => loadCatalog(true));
$("btn-mas").addEventListener("click", () => loadCatalog(false));

// --- Arranque ----------------------------------------------------------------------

async function loadGenres() {
  const select = $("categoria");
  const all = el("option", "", TEXTOS.todasCategorias);
  all.value = "";
  select.replaceChildren(all);
  const data = await guarded(() => api("GET", "/api/categorias"));
  for (const g of data?.categorias || []) {
    const option = el("option", "", g.nombre);
    option.value = String(g.id);
    select.appendChild(option);
  }
}

async function start() {
  for (const node of document.querySelectorAll("[data-text]")) {
    node.textContent = TEXTOS[node.dataset.text];
  }
  $("nuevo-nombre").placeholder = TEXTOS.nuevoPlaceholder;
  $("q").placeholder = TEXTOS.buscarPlaceholder;
  document.querySelector('[data-view="explorar"]').textContent = TEXTOS.vistaExplorar;
  document.querySelector('[data-view="pack"]').textContent = TEXTOS.vistaPack(0);
  await Promise.all([guarded(loadPacks), loadGenres()]);
  if (!state.packs.length) return;
  let last = null;
  try {
    last = localStorage.getItem("packs.ultimo");
  } catch {
    last = null;
  }
  const first = state.packs.find((p) => p.id === last) || state.packs[0];
  await openPack(first.id);
}

start();
