// Mando del móvil: conexión, buscador, rueda de tiempo, ronda, marcador y jugadores.
import { ApiError, createApi, forgetToken, initialToken, rememberToken } from "./api.js";
import { TEXTOS } from "./textos.js";
import { createWheel, hms } from "./wheel.js";

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
  category: null, // { id, nombre } o null
  picked: new Set(), // jugadores marcados como acertantes
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

function show(view) {
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
    renderAward();
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
    return img;
  }
  return el("div", `${className} blank`);
}

function renderResults(movies, fresh) {
  const list = $("resultados");
  if (fresh) list.replaceChildren();
  for (const movie of movies) {
    const item = el("li");
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
    const movie = await state.api.random(state.category?.id);
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
    info.append(el("p", "round-title", TEXTOS.cargandoTele), el("div", "progress"));
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
$("btn-dar").addEventListener("click", (e) =>
  run(e.currentTarget, "error-ronda", async () => {
    await state.api.award([...state.picked]);
    state.picked.clear();
  }),
);
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

function renderScore() {
  const list = $("marcador");
  list.replaceChildren();
  for (const p of state.players) {
    const row = el("li", "score-row");
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
    const on = given || state.picked.has(p.id);
    const chip = el("button", "chip", given ? `${p.nombre} ✓` : p.nombre);
    chip.type = "button";
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.disabled = given;
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
