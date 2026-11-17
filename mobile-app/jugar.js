// Página del jugador: cada uno escribe el título en su móvil y sabe al momento si acierta.
import { ApiError, createApi, forgetToken, initialToken, rememberToken } from "./api.js?v=4";

const PLAYER_KEY = "fg-jugador";
const $ = (id) => document.getElementById(id);

const state = {
  api: null,
  me: load(PLAYER_KEY),
  players: [],
  status: null,
  result: null, // último resultado de esta ronda: {tipo, titulo, detalle}
  roundKey: null,
};

function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Sin almacenamiento: se vuelve a preguntar el nombre al recargar.
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function show(view) {
  for (const section of document.querySelectorAll(".view")) {
    section.classList.toggle("hidden", section.id !== `view-${view}`);
  }
}

function showError(id, error) {
  const box = $(id);
  if (!error) {
    box.classList.add("hidden");
    return;
  }
  box.textContent = error instanceof ApiError ? error.message : String(error);
  box.classList.remove("hidden");
}

const points = (n) => `${n} ${Math.abs(n) === 1 ? "punto" : "puntos"}`;

function myPlayer() {
  return state.players.find((p) => p.id === state.me) || null;
}

// --- Conexión -------------------------------------------------------------------

function connect(token) {
  state.api = createApi(token);
  state.api.listen(onEvent, (ok) => {
    const label = $("estado-conexion");
    if (ok === "rechazado") {
      forgetToken();
      label.textContent = "Código no válido";
      show("conexion");
      return;
    }
    label.textContent = ok ? "Conectado" : "Sin conexión";
    label.dataset.ok = String(ok === true);
  });
}

function onEvent(kind, data) {
  if (kind === "estado") {
    state.status = data;
    render();
  } else if (kind === "marcador") {
    state.players = data.jugadores || [];
    render();
  }
}

// --- Pintado --------------------------------------------------------------------

function render() {
  if (!state.api) return;
  if (!state.me || !myPlayer()) {
    if (state.players.length || state.status) renderWho();
    return;
  }
  renderGame();
}

function renderWho() {
  show("quien");
  const list = $("quien-jugadores");
  list.replaceChildren();
  for (const p of state.players) {
    const chip = el("button", "chip", p.nombre);
    chip.type = "button";
    chip.addEventListener("click", () => choose(p.id));
    list.appendChild(chip);
  }
}

function choose(id) {
  state.me = id;
  save(PLAYER_KEY, id);
  render();
  $("respuesta").focus();
}

function renderGame() {
  show("juego");
  const me = myPlayer();
  $("t-juego").textContent = me.nombre;
  const round = state.status && state.status.rondaActual;
  const screen = state.status ? state.status.pantalla : "espera";
  const key = round ? `${round.netflixId}:${round.segundos}` : null;
  if (key !== state.roundKey) {
    // Ronda nueva: se olvida el resultado de la anterior.
    state.roundKey = key;
    state.result = null;
    $("respuesta").value = "";
  }

  const playing = round && screen === "pelicula";
  const won = round && (round.acertantes || []).includes(me.id);
  const out = round && (round.fallos || []).includes(me.id);
  $("espera").classList.toggle("hidden", Boolean(round) && screen !== "cargando");
  $("jugando").classList.toggle("hidden", !playing);
  $("form-respuesta").classList.toggle("hidden", !playing || won || out);

  if (round) {
    $("valor").textContent = `Vale ${points(round.valor)}`;
    $("categoria").textContent = round.categoria ? round.categoria.nombre : "";
    $("categoria").classList.toggle("hidden", !round.categoria);
    const hints = $("pistas");
    hints.replaceChildren();
    for (const h of round.pistas || []) {
      const item = el("li");
      item.append(el("span", "p-hint-label", h.etiqueta), el("span", "p-hint-text", h.texto));
      hints.appendChild(item);
    }
  }

  let result = state.result;
  if (round && round.revelada) {
    const title = round.titulo ? `Era «${round.titulo}»${round.anio ? ` (${round.anio})` : ""}` : "";
    result = won
      ? { tipo: "good", titulo: "¡La acertaste!", detalle: title }
      : { tipo: out ? "bad" : "near", titulo: title || "Película revelada", detalle: "Espera a la siguiente ronda." };
  } else if (won && !result) {
    result = { tipo: "good", titulo: "¡La has acertado!", detalle: "Espera a que se revele." };
  } else if (out && !result) {
    result = { tipo: "bad", titulo: "Has fallado esta ronda", detalle: "Espera a la siguiente." };
  }
  const box = $("resultado");
  box.classList.toggle("hidden", !result);
  if (result) {
    box.className = `card p-result ${result.tipo}`;
    $("resultado-titulo").textContent = result.titulo;
    $("resultado-detalle").textContent = result.detalle || "";
  }
  renderScore();
}

function renderScore() {
  const list = $("marcador");
  list.replaceChildren();
  for (const p of state.players) {
    const row = el("li", p.id === state.me ? "me" : "");
    row.append(el("span", "", p.nombre), el("span", "num", String(p.puntos)));
    list.appendChild(row);
  }
}

// --- Acciones -------------------------------------------------------------------

async function submitGuess(event) {
  event.preventDefault();
  const text = $("respuesta").value.trim();
  if (!text) return;
  const button = $("btn-comprobar");
  button.disabled = true;
  showError("error-juego", null);
  try {
    const res = await state.api.guess(state.me, text);
    if (res.resultado === "acierto") {
      state.result = {
        tipo: "good",
        titulo: `¡Acierto! +${points(res.puntos)}`,
        detalle: `Es «${res.titulo}»${res.anio ? ` (${res.anio})` : ""}. Que no te lo vean los demás.`,
      };
    } else if (res.resultado === "casi") {
      state.result = {
        tipo: "near",
        titulo: "¡Casi!",
        detalle: "Vas bien, pero no es exactamente así. Revisa cómo lo has escrito y prueba otra vez.",
      };
    } else {
      state.result = {
        tipo: "bad",
        titulo: `No es esa: ${points(res.puntos)}`,
        detalle: "Quedas fuera de esta ronda.",
      };
      $("respuesta").value = "";
    }
    if (navigator.vibrate) navigator.vibrate(res.resultado === "acierto" ? [60, 40, 60] : 120);
  } catch (error) {
    showError("error-juego", error);
  } finally {
    button.disabled = false;
    render();
  }
}

async function addMe(event) {
  event.preventDefault();
  const name = $("nombre").value.trim();
  if (!name) return;
  showError("error-quien", null);
  try {
    const board = await state.api.addPlayer(name);
    state.players = board.jugadores || state.players;
    const mine = state.players.find((p) => p.nombre === name);
    if (mine) choose(mine.id);
  } catch (error) {
    showError("error-quien", error);
  }
}

function start() {
  $("form-respuesta").addEventListener("submit", submitGuess);
  $("form-nuevo").addEventListener("submit", addMe);
  $("btn-cambiar").addEventListener("click", () => {
    state.me = null;
    save(PLAYER_KEY, null);
    renderWho();
  });
  $("form-conexion").addEventListener("submit", (event) => {
    event.preventDefault();
    const token = $("token").value.trim();
    if (!token) return;
    rememberToken(token);
    connect(token);
  });
  const token = initialToken();
  if (token) connect(token);
  else show("conexion");
}

start();
