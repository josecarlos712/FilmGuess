// Mando del móvil: conexión, buscador, rueda de tiempo, ronda, marcador y jugadores.
import { ApiError, createApi, forgetToken, initialToken, rememberToken } from "./api.js?v=3";
import { TEXTOS } from "./textos.js?v=3";
import { createWheel, hms } from "./wheel.js?v=3";

const $ = (id) => document.getElementById(id);

const state = {
  api: null,
  stopListening: null,
  view: null,
  status: null,
  players: [],
  search: { q: "", page: 1, all: false, total: 0, seq: 0 },
  chosen: null, // { movie, hidden }
  wheel: null,
  genres: null, // [{ id, nombre, peliculas }]
  packs: null, // [{ id, nombre, conocidas, desconocidas }]
  pack: null, // id del pack elegido para «Al azar»; null = todo el catálogo
  category: null, // { id, nombre } o null
  picked: new Set(), // jugadores marcados como acertantes
  risk: null, // jugador que se arriesga ahora
  peek: null, // título visto en privado en este móvil: { netflixId, titulo, anio }
  adjust: false,
};

for (const el of document.querySelectorAll("[data-text]")) {
  const value = TEXTOS[el.dataset.text];
  if (typeof value === "string") el.textContent = value;
}

// --- Utilidades --------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(target, error) {
  const node = $(target);
  if (!error) {
    node.classList.add("hidden");
    node.textContent = "";
    return;
  }
  node.textContent = error instanceof ApiError ? error.message : TEXTOS.errorDesconocido;
  node.classList.remove("hidden");
}

/** Ejecuta una orden con el botón ocupado y el error debajo. */
async function run(button, errorTarget, action, busyText) {
  if (button.disabled) return undefined;
  const label = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  if (busyText) button.textContent = busyText;
  showError(errorTarget, null);
  try {
    return await action();
  } catch (error) {
    if (error instanceof ApiError && error.code === "token_invalido") {
      disconnect(error.message);
      return undefined;
    }
    showError(errorTarget, error);
    return undefined;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    if (busyText) button.textContent = label;
  }
}

function setLink(stateName) {
  const node = $("estado-conexion");
  node.dataset.state = stateName;
  node.textContent =
    stateName === "ok"
      ? TEXTOS.conectado
      : stateName === "connecting"
        ? TEXTOS.conectando
        : TEXTOS.desconectado;
}

// --- Vistas ------------------------------------------------------------------------

const NAV_FOR = { buscar: "buscar", tiempo: "buscar", ronda: "ronda", jugadores: "jugadores" };

// Orden de las vistas: decide si la nueva entra desde la derecha (avanzar) o la izquierda.
const VIEW_ORDER = ["conexion", "buscar", "tiempo", "ronda", "jugadores"];
const STEP_ORDER = ["buscar", "ronda", "jugadores"];

function show(view) {
  const from = VIEW_ORDER.indexOf(state.view);
  const to = VIEW_ORDER.indexOf(view);
  $("main").dataset.dir = from < 0 || from === to ? "" : to > from ? "fwd" : "back";
  const stepIndex = STEP_ORDER.indexOf(NAV_FOR[view]);
  $("nav").style.setProperty("--idx", String(Math.max(stepIndex, 0)));
  $("nav").classList.toggle("no-step", stepIndex < 0);
  state.view = view;
  for (const section of document.querySelectorAll(".view")) {
    section.classList.toggle("hidden", section.id !== `view-${view}`);
  }
  $("nav").classList.toggle("hidden", view === "conexion");
  for (const step of document.querySelectorAll(".step")) {
    const current = step.dataset.view === NAV_FOR[view];
    step.classList.toggle("on", current);
    if (current) step.setAttribute("aria-current", "page");
    else step.removeAttribute("aria-current");
  }
  window.scrollTo(0, 0);
  if (view === "buscar" && !$("resultados").children.length) runSearch(true);
}

for (const step of document.querySelectorAll(".step")) {
  step.addEventListener("click", () => show(step.dataset.view));
}

// --- Conexión ----------------------------------------------------------------------

$("servidor").textContent = TEXTOS.conexionServidor(location.host);

function disconnect(message) {
  if (state.stopListening) state.stopListening();
  state.stopListening = null;
  state.api = null;
  forgetToken();
  setLink("off");
  showError("error-conexion", message ? new ApiError("token_invalido", message, 401) : null);
  show("conexion");
}

async function connect(token) {
  setLink("connecting");
  const api = createApi(token);
  try {
    const status = await api.status();
    state.api = api;
    rememberToken(token);
    state.status = status;
    state.stopListening = api.listen(onEvent, (ok) => {
      if (ok === "rechazado") disconnect(TEXTOS.conexionMal);
      else setLink(ok ? "ok" : "off");
    });
    renderRound();
    show(status.rondaActual ? "ronda" : "buscar");
    return true;
  } catch (error) {
    setLink("off");
    if (error instanceof ApiError && error.code === "token_invalido") {
      forgetToken();
      showError("error-conexion", new ApiError(error.code, TEXTOS.conexionMal, 401));
    } else {
      showError("error-conexion", error);
    }
    show("conexion");
    return false;
  }
}

$("form-conexion").addEventListener("submit", (event) => {
  event.preventDefault();
  const token = $("token").value.trim();
  if (!token) return;
  const button = event.submitter || $("form-conexion").querySelector("button");
  run(button, "error-conexion", () => connect(token));
});

function onEvent(kind, data) {
  if (kind === "estado") {
    state.status = data;
    renderRound();
  } else if (kind === "marcador") {
    state.players = data.jugadores || [];
    renderScore();
    renderPeople();
    renderRound();
  } else if (kind === "error") {
    showError("error-ronda", new ApiError(data.error, data.mensaje, 0));
  }
}

// --- Buscador ----------------------------------------------------------------------

let searchTimer = null;

$("q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => runSearch(true), 250);
});
$("q").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    clearTimeout(searchTimer);
    runSearch(true);
    event.target.blur();
  }
});
$("todas").addEventListener("change", () => runSearch(true));
$("btn-mas").addEventListener("click", () => runSearch(false));

async function runSearch(fresh) {
  if (!state.api) return;
  const s = state.search;
  if (fresh) {
    s.q = $("q").value.trim();
    s.all = $("todas").checked;
    s.page = 1;
  } else {
    s.page += 1;
  }
  const seq = ++s.seq;
  showError("error-buscar", null);
  try {
    if (!state.genres) loadGenres();
    if (!state.packs) loadPacks();
    const data = await state.api.search(s.q, s.page, s.all, state.category?.id);
    if (seq !== s.seq) return; // llegó una búsqueda más nueva
    s.total = data.total;
    renderResults(data.resultados, fresh);
    const shown = $("resultados").children.length;
    $("btn-mas").classList.toggle("hidden", shown >= data.total);
    $("total").textContent = data.total ? TEXTOS.resultados(data.total) : "";
    // Sin texto la API no devuelve nada: solo se avisa cuando se ha buscado algo.
    $("sin-resultados").classList.toggle("hidden", data.total > 0 || !s.q);
  } catch (error) {
    if (seq !== s.seq) return;
    if (error instanceof ApiError && error.code === "token_invalido") disconnect(error.message);
    else showError("error-buscar", error);
  }
}

function posterNode(movie, className) {
  if (movie.poster) {
    const img = el("img", className);
    img.src = movie.poster;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => img.replaceWith(el("div", `${className} blank`)));
    img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
    if (img.complete) img.classList.add("loaded");
    return img;
  }
  return el("div", `${className} blank`);
}

function renderResults(movies, fresh) {
  const list = $("resultados");
  if (fresh) list.replaceChildren();
  for (const [index, movie] of movies.entries()) {
    const item = el("li");
    item.style.setProperty("--i", String(index)); // escalonado dentro de cada tanda
    const button = el("button", "poster-card");
    button.type = "button";
    const frame = el("span", "poster-frame");
    frame.appendChild(posterNode(movie, "poster"));
    const title = el("span", "poster-title", movie.titulo);
    const year = el("span", "poster-year muted num", movie.anio ? String(movie.anio) : "");
    button.append(frame, title, year);
    button.addEventListener("click", () => choose(movie, false));
    item.appendChild(button);
    list.appendChild(item);
  }
}

$("btn-azar").addEventListener("click", (event) =>
  run(event.currentTarget, "error-buscar", async () => {
    const movie = await state.api.random(state.category?.id, state.pack);
    choose(movie, true);
  }),
);

// --- Tiempo ------------------------------------------------------------------------

function randomMoment(duration) {
  if (!duration) return 0;
  return Math.round(duration * (0.05 + Math.random() * 0.85));
}

function choose(movie, hidden) {
  state.chosen = { movie, hidden };
  const box = $("elegida");
  box.replaceChildren();
  const duration = movie.duracionSeg;
  if (hidden) {
    box.classList.add("surprise");
    const text = el("div", "chosen-text");
    box.append(el("div", "chosen-poster blank", "?"), text);
    text.append(
      el("p", "chosen-title", TEXTOS.sorpresa),
      el("p", "muted small", TEXTOS.sorpresaAyuda),
      el("p", "muted small num", duration ? TEXTOS.duracion(hms(duration)) : ""),
    );
  } else {
    box.classList.remove("surprise");
    const text = el("div", "chosen-text");
    text.append(
      el("p", "chosen-title", movie.titulo),
      el(
        "p",
        "muted small num",
        [movie.anio, duration ? TEXTOS.duracion(hms(duration)) : null].filter(Boolean).join(" · "),
      ),
    );
    box.append(posterNode(movie, "chosen-poster"), text);
  }
  showError("error-tiempo", duration ? null : new ApiError("", TEXTOS.sinDuracion, 0));
  show("tiempo");
  const maxHours = duration ? Math.floor(duration / 3600) : 3;
  state.wheel = createWheel($("rueda"), {
    maxHours,
    labels: [TEXTOS.horas, TEXTOS.minutos, TEXTOS.segundos],
    onChange: () => showError("error-tiempo", null),
  });
  requestAnimationFrame(() => state.wheel.set(randomMoment(duration)));
}

$("btn-atras").addEventListener("click", () => show("buscar"));
$("btn-momento-azar").addEventListener("click", () => {
  if (state.chosen) state.wheel.set(randomMoment(state.chosen.movie.duracionSeg));
});

$("btn-mostrar").addEventListener("click", (event) => {
  if (!state.chosen) return;
  const { movie } = state.chosen;
  const seconds = state.wheel.get();
  if (movie.duracionSeg && seconds > movie.duracionSeg) {
    showError("error-tiempo", new ApiError("", TEXTOS.fueraDeDuracion(hms(movie.duracionSeg)), 0));
    return;
  }
  run(
    event.currentTarget,
    "error-tiempo",
    async () => {
      await state.api.play(movie.netflixId, seconds, state.category?.id);
      show("ronda");
    },
    TEXTOS.mostrando,
  );
});

// --- Ronda -------------------------------------------------------------------------

function renderRound() {
  const status = state.status;
  if (!status) return;
  const round = status.rondaActual;
  const loading = status.pantalla === "cargando";
  $("ronda-vacia").classList.toggle("hidden", Boolean(round) || loading);
  $("ronda-activa").classList.toggle("hidden", !round && !loading);
  const info = $("ronda-info");
  info.replaceChildren();
  const revealed = Boolean(round && round.revelada);
  if (loading) {
    const projector = el("div", "m-projector");
    projector.setAttribute("aria-hidden", "true");
    projector.append(el("div", "m-reel"), el("div", "m-film"));
    info.append(projector, el("p", "round-title", TEXTOS.cargandoTele));
  } else if (round) {
    const head = el("div", "round-head");
    head.append(el("p", "round-value", TEXTOS.vale(round.valor)));
    if (round.categoria) head.append(el("p", "tag", round.categoria.nombre));
    info.append(head);
    if (revealed) {
      info.append(
        el("p", "round-title", round.titulo),
        el("p", "muted num", round.anio ? String(round.anio) : ""),
      );
    } else {
      info.append(el("p", "round-title", TEXTOS.rondaOculta));
    }
    const moment = hms(round.segundosReales ?? round.segundos);
    info.append(
      el("p", "muted small num", TEXTOS.rondaFotogramas(round.fotogramas, round.fotogramasMax, moment)),
    );
    if (round.pistas && round.pistas.length) {
      const list = el("dl", "hints");
      for (const h of round.pistas) {
        const long = h.tipo === "frase";
        list.append(el("dt", long ? "quote" : "", h.etiqueta), el("dd", long ? "quote" : "", h.texto));
      }
      info.append(list);
    }
  }
  const playing = Boolean(round) && !loading && !revealed;
  const left = round ? round.pistasTotal - round.pistas.length : 0;
  $("btn-pista").disabled = !playing || left <= 0;
  $("btn-pista").textContent =
    round && left > 0
      ? TEXTOS.darPista(round.pistas.length + 1, round.pistasTotal, round.valor > 1)
      : TEXTOS.sinPistas;
  const frames = round ? round.fotogramas : 1;
  const maxFrames = round ? round.fotogramasMax : 3;
  $("btn-fotograma").textContent = TEXTOS.otroFotograma(frames, maxFrames);
  $("btn-fotograma").disabled = !playing || frames >= maxFrames;
  $("btn-clip").disabled = !playing || Boolean(round && round.clipUsado);
  for (const id of ["btn-saltar", "btn-pausar", "btn-reanudar", "btn-revelar"]) {
    $(id).disabled = !playing;
  }
  $("btn-nueva").disabled = loading || !round;
  document.querySelector(".round-actions").classList.toggle("hidden", revealed);
  renderAward();
  renderFail(playing);
}

$("btn-elegir").addEventListener("click", () => show("buscar"));
$("btn-pista").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.hint()),
);
$("btn-fotograma").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.frame(), TEXTOS.mostrando),
);
$("btn-clip").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.clip()),
);
$("btn-saltar").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.skip(), TEXTOS.mostrando),
);
$("btn-dar").addEventListener("click", (e) => {
  const button = e.currentTarget;
  run(button, "error-ronda", async () => {
    const winners = state.picked.size;
    await state.api.award([...state.picked]);
    state.picked.clear();
    if (winners) confetti(button);
  });
});
$("btn-ver").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", async () => {
    state.peek = await state.api.peek();
    renderRound();
  }),
);
$("btn-acierta").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", async () => {
    const winner = state.risk;
    state.risk = null;
    await state.api.reveal();
    await state.api.award([winner]);
    confetti($("btn-acierta"));
  }),
);
$("btn-restar").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", async () => {
    const loser = state.risk;
    state.risk = null;
    await state.api.fail([loser]);
    shake($("fallo"));
  }),
);

// --- Efectos -----------------------------------------------------------------------

/** Desliza las filas desde donde estaban (técnica FLIP): solo transform. */
function flip(list, before) {
  if (reduceMotion.matches || !before.size) return;
  const moved = [];
  for (const row of list.children) {
    const top = before.get(row.dataset.id);
    if (top === undefined) continue;
    const dy = top - row.getBoundingClientRect().top;
    if (!dy) continue;
    row.style.transform = `translateY(${dy}px)`;
    moved.push(row);
  }
  void list.offsetHeight; // fija el punto de partida antes de soltarlas
  for (const row of moved) {
    row.classList.add("moving");
    row.style.transform = "";
  }
}

/** Sacude una tarjeta (respuesta fallida). */
function shake(node) {
  if (reduceMotion.matches || !node) return;
  node.classList.remove("shake");
  void node.offsetWidth;
  node.classList.add("shake");
  node.addEventListener("animationend", () => node.classList.remove("shake"), { once: true });
}

/** Lluvia de confeti bronce desde un elemento (acierto). Sale por arriba y cae. */
function confetti(origin) {
  if (reduceMotion.matches || !origin || !document.body.animate) return;
  const box = origin.getBoundingClientRect();
  const x0 = box.left + box.width / 2;
  const y0 = box.top + box.height / 2;
  const colors = ["#a08672", "#c9b29d", "#f2f2f2", "#7d6553", "#e3cdb6"];
  for (let i = 0; i < 36; i += 1) {
    const bit = el("span", "m-confetti");
    bit.style.background = colors[i % colors.length];
    if (i % 3 === 0) bit.style.borderRadius = "50%";
    document.body.appendChild(bit);
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 0.9;
    const speed = 160 + Math.random() * 220;
    const dx = Math.cos(angle) * speed;
    const dy = Math.sin(angle) * speed;
    const spin = (Math.random() - 0.5) * 900;
    const at = (x, y, r, s) => `translate(${x0 + x}px, ${y0 + y}px) rotate(${r}deg) scale(${s})`;
    bit
      .animate(
        [
          { transform: at(0, 0, 0, 0.6), opacity: 1 },
          { transform: at(dx, dy, spin / 2, 1), opacity: 1, offset: 0.45 },
          { transform: at(dx * 1.25, dy + 260, spin, 0.9), opacity: 0 },
        ],
        {
          duration: 1100 + Math.random() * 500,
          easing: "cubic-bezier(0.23, 1, 0.32, 1)",
          delay: Math.random() * 80,
          fill: "forwards",
        },
      )
      .finished.then(() => bit.remove(), () => bit.remove());
  }
}
$("btn-ajustar").addEventListener("click", () => {
  state.adjust = !state.adjust;
  renderScore();
});
$("btn-revelar").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.reveal()),
);
$("btn-pausar").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.pause()),
);
$("btn-reanudar").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", () => state.api.resume()),
);
$("btn-nueva").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", async () => {
    await state.api.idle();
    show("buscar");
  }),
);

// --- Marcador ----------------------------------------------------------------------

// Puntos de la última vez que se pintó el marcador, para animar quién sube y cuánto.
let shownScore = null; // Map id -> puntos
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function renderScore() {
  const list = $("marcador");
  const before = new Map();
  for (const row of list.children) before.set(row.dataset.id, row.getBoundingClientRect().top);
  list.replaceChildren();
  for (const p of state.players) {
    const row = el("li", "score-row");
    row.dataset.id = String(p.id);
    const old = shownScore && shownScore.get(String(p.id));
    if (old !== undefined && old !== null && old !== p.puntos) {
      const delta = p.puntos - old;
      row.dataset.delta = delta > 0 ? `+${delta}` : String(delta);
      row.classList.add("bump");
      if (delta < 0) row.classList.add("down");
    }
    const name = el("span", "score-name", p.nombre);
    const points = el("span", "score-points num", String(p.puntos));
    const minus = el("button", "round-btn", "−");
    minus.type = "button";
    minus.setAttribute("aria-label", TEXTOS.restar(p.nombre));
    minus.addEventListener("click", () => run(minus, "error-ronda", () => state.api.points(p.id, -1)));
    const plus = el("button", "round-btn plus", "+1");
    plus.type = "button";
    plus.setAttribute("aria-label", TEXTOS.sumar(p.nombre));
    plus.addEventListener("click", () => run(plus, "error-ronda", () => state.api.points(p.id, 1)));
    row.append(name, minus, points, plus);
    list.appendChild(row);
  }
  flip(list, before);
  shownScore = new Map(state.players.map((p) => [String(p.id), p.puntos]));
  list.classList.toggle("adjust", state.adjust);
  $("btn-ajustar").textContent = state.adjust ? TEXTOS.terminarCorregir : TEXTOS.corregir;
  $("btn-ajustar").classList.toggle("hidden", state.players.length === 0);
  $("marcador-vacio").classList.toggle("hidden", state.players.length > 0);
}

// --- Categorías --------------------------------------------------------------------

async function loadGenres() {
  state.genres = [];
  try {
    state.genres = (await state.api.genres()).categorias || [];
  } catch {
    state.genres = null; // se reintenta en la siguiente búsqueda
  }
  renderGenres();
}

function renderGenres() {
  const box = $("categorias");
  box.replaceChildren();
  const options = [{ id: null, nombre: TEXTOS.todasCategorias }, ...(state.genres || [])];
  for (const g of options) {
    const chip = el("button", "chip", g.nombre);
    chip.type = "button";
    const on = (state.category?.id ?? null) === g.id;
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.addEventListener("click", () => {
      state.category = g.id ? { id: g.id, nombre: g.nombre } : null;
      renderGenres();
      runSearch(true);
    });
    box.appendChild(chip);
  }
  $("categoria-nota").textContent = state.category
    ? TEXTOS.categoriaElegida(state.category.nombre)
    : TEXTOS.categoriaLibre;
}

// --- Packs -------------------------------------------------------------------------

const PACK_KEY = "filmguess.pack";

async function loadPacks() {
  state.packs = [];
  try {
    state.packs = (await state.api.packs()).packs || [];
  } catch {
    state.packs = null; // se reintenta en la siguiente búsqueda
  }
  let saved = null;
  try {
    saved = localStorage.getItem(PACK_KEY);
  } catch {
    saved = null;
  }
  state.pack = (state.packs || []).some((p) => p.id === saved) ? saved : null;
  renderPacks();
}

function renderPacks() {
  const box = $("packs");
  box.replaceChildren();
  const packs = state.packs || [];
  $("campo-pack").classList.toggle("hidden", packs.length === 0);
  const options = [{ id: null, nombre: TEXTOS.todoElCatalogo }, ...packs];
  for (const p of options) {
    const chip = el("button", "chip", p.nombre);
    chip.type = "button";
    const on = state.pack === p.id;
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.addEventListener("click", () => {
      state.pack = p.id;
      try {
        if (p.id) localStorage.setItem(PACK_KEY, p.id);
        else localStorage.removeItem(PACK_KEY);
      } catch {
        // Sin almacenamiento el pack elegido solo dura hasta recargar.
      }
      renderPacks();
    });
    box.appendChild(chip);
  }
}

// --- Acertantes --------------------------------------------------------------------

function renderAward() {
  const round = state.status && state.status.rondaActual;
  const show = Boolean(round && round.revelada);
  $("acierto").classList.toggle("hidden", !show);
  if (!show) {
    state.picked.clear();
    return;
  }
  $("acierto-titulo").textContent = TEXTOS.quienAcerto(round.valor);
  const list = $("acierto-jugadores");
  list.replaceChildren();
  const done = new Set(round.acertantes || []);
  for (const p of state.players) {
    const given = done.has(p.id);
    const out = (round.fallos || []).includes(p.id); // falló: fuera de la ronda
    const on = given || state.picked.has(p.id);
    const chip = el("button", "chip", given ? `${p.nombre} ✓` : out ? `${p.nombre} ✗` : p.nombre);
    chip.type = "button";
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.disabled = given || out;
    chip.addEventListener("click", () => {
      if (state.picked.has(p.id)) state.picked.delete(p.id);
      else state.picked.add(p.id);
      renderAward();
    });
    list.appendChild(chip);
  }
  $("acierto-vacio").classList.toggle("hidden", state.players.length > 0);
  $("btn-dar").classList.toggle("hidden", state.players.length === 0);
  $("btn-dar").disabled = state.picked.size === 0;
}

// --- Fallos -------------------------------------------------------------------------

function renderFail(playing) {
  const round = state.status && state.status.rondaActual;
  const show = Boolean(round) && !round.revelada && state.players.length > 0;
  $("fallo").classList.toggle("hidden", !show);
  if (state.peek && (!round || state.peek.netflixId !== round.netflixId)) state.peek = null;
  if (!show) {
    state.risk = null;
    return;
  }
  const done = new Set(round.fallos || []);
  if (state.risk && (done.has(state.risk) || !state.players.some((p) => p.id === state.risk))) {
    state.risk = null;
  }
  $("fallo-titulo").textContent = TEXTOS.arriesgar(round.valor);
  const list = $("fallo-jugadores");
  list.replaceChildren();
  for (const p of state.players) {
    const out = done.has(p.id);
    const on = state.risk === p.id;
    const chip = el("button", "chip", out ? `${p.nombre} ✗` : p.nombre);
    chip.type = "button";
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.disabled = out || !playing;
    chip.addEventListener("click", () => {
      state.risk = on ? null : p.id;
      renderFail(playing);
    });
    list.appendChild(chip);
  }
  const seen = Boolean(state.peek);
  $("btn-ver").classList.toggle("hidden", seen);
  $("btn-ver").disabled = !playing || !state.risk;
  $("fallo-secreto").classList.toggle("hidden", !seen);
  if (seen) {
    $("fallo-pelicula").textContent = state.peek.titulo;
    $("fallo-anio").textContent = state.peek.anio ? String(state.peek.anio) : "";
  }
  $("btn-acierta").disabled = !playing || !state.risk;
  $("btn-restar").disabled = !playing || !state.risk;
}

// --- Jugadores ---------------------------------------------------------------------

function renderPeople() {
  const list = $("lista-jugadores");
  list.replaceChildren();
  for (const p of state.players) {
    const row = el("li", "person");
    const name = el("button", "person-name", p.nombre);
    name.type = "button";
    name.setAttribute("aria-label", TEXTOS.renombrar(p.nombre));
    name.addEventListener("click", () => editName(row, p));
    const remove = el("button", "icon-btn", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", TEXTOS.quitar(p.nombre));
    remove.addEventListener("click", () => {
      if (!window.confirm(TEXTOS.confirmarQuitar(p.nombre))) return;
      run(remove, "error-jugadores", () => state.api.removePlayer(p.id));
    });
    row.append(name, remove);
    list.appendChild(row);
  }
}

function editName(row, player) {
  const form = el("form", "person-edit");
  form.noValidate = true;
  const input = el("input");
  input.type = "text";
  input.maxLength = 30;
  input.value = player.nombre;
  input.setAttribute("aria-label", TEXTOS.renombrar(player.nombre));
  const save = el("button", "btn", TEXTOS.guardar);
  save.type = "submit";
  const cancel = el("button", "btn secondary", TEXTOS.cancelar);
  cancel.type = "button";
  cancel.addEventListener("click", renderPeople);
  form.append(input, save, cancel);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(save, "error-jugadores", () => state.api.renamePlayer(player.id, input.value));
  });
  row.replaceChildren(form);
  input.focus();
  input.select();
}

$("form-jugador").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("nombre");
  const name = input.value.trim();
  if (!name) return;
  const button = event.submitter || $("form-jugador").querySelector("button");
  run(button, "error-jugadores", async () => {
    await state.api.addPlayer(name);
    input.value = "";
  });
});

$("btn-cero").addEventListener("click", (event) => {
  if (!window.confirm(TEXTOS.confirmarCero)) return;
  run(event.currentTarget, "error-jugadores", () => state.api.resetPoints());
});

// --- Arranque ----------------------------------------------------------------------

const token = initialToken();
renderScore();
if (token) {
  connect(token);
} else {
  setLink("off");
  show("conexion");
}

// --- Invitación para jugar cada uno desde su móvil ---------------------------------

$("btn-invitar").addEventListener("click", async () => {
  const box = $("invitacion");
  const url = $("invitacion-url");
  if (!box.classList.contains("hidden")) {
    box.classList.add("hidden");
    url.classList.add("hidden");
    $("btn-invitar").textContent = "Mostrar código de invitación";
    return;
  }
  try {
    const [svg, info] = await Promise.all([state.api.inviteQr(), state.api.invite()]);
    box.innerHTML = svg;
    url.textContent = info.url.split("#")[0];
    box.classList.remove("hidden");
    url.classList.remove("hidden");
    $("btn-invitar").textContent = "Ocultar código";
  } catch (error) {
    showError("error-jugadores", error);
  }
});
