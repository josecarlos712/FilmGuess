// Tele (/tv): pantalla de espera con QR y marcador, y «Preparando fotograma…».
// Escucha /tv/ws (solo desde el propio PC, sin token) y no envía órdenes.
import { TEXTOS } from "./textos.js";

const $ = (id) => document.getElementById(id);
const MANY_PLAYERS = 8;

for (const el of document.querySelectorAll("[data-text]")) {
  el.textContent = TEXTOS[el.dataset.text];
}

function showScreen(name) {
  // En /tv solo hay espera o carga: «pelicula» y «revelada» se ven ya en Netflix.
  const loading = name !== "espera";
  $("espera").classList.toggle("hidden", loading);
  $("cargando").classList.toggle("hidden", !loading);
}

function renderNotice(error) {
  const aviso = $("aviso");
  aviso.textContent = error ? TEXTOS.aviso + error.mensaje : "";
  aviso.classList.toggle("hidden", !error);
}

function renderPlayers(players) {
  const list = $("jugadores");
  list.replaceChildren();
  list.classList.toggle("many", players.length > MANY_PLAYERS);
  const top = players.length ? players[0].puntos : null;
  players.forEach((p, i) => {
    const row = document.createElement("li");
    row.className = "player";
    if (top !== null && top > 0 && p.puntos === top) row.classList.add("leader");
    const rank = document.createElement("span");
    rank.className = "player-rank num";
    rank.textContent = String(i + 1);
    const name = document.createElement("span");
    name.className = "player-name";
    name.textContent = p.nombre;
    const points = document.createElement("span");
    points.className = "player-points num";
    points.textContent = String(p.puntos);
    points.setAttribute("aria-label", `${p.puntos} ${TEXTOS.puntos(p.puntos)}`);
    row.append(rank, name, points);
    list.appendChild(row);
  });
  $("sin-jugadores").classList.toggle("hidden", players.length > 0);
}

async function loadAddress() {
  try {
    const res = await fetch("/pairing");
    if (!res.ok) return;
    const data = await res.json();
    $("direccion").textContent = `${data.ip}:${data.puerto}`;
  } catch {
    // Sin dirección se sigue viendo el QR.
  }
}

let retry = 500;
let lostTimer = null;

function connect() {
  const ws = new WebSocket(`ws://${location.host}/tv/ws`);
  ws.addEventListener("open", () => {
    retry = 500;
    clearTimeout(lostTimer);
    $("conexion").classList.add("hidden");
  });
  ws.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.tipo === "estado") {
      showScreen(msg.datos.pantalla);
      renderNotice(msg.datos.ultimoError);
    } else if (msg.tipo === "marcador") {
      renderPlayers(msg.datos.jugadores || []);
    } else if (msg.tipo === "error") {
      renderNotice(msg.datos);
    }
  });
  ws.addEventListener("close", () => {
    clearTimeout(lostTimer);
    lostTimer = setTimeout(() => $("conexion").classList.remove("hidden"), 1500);
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 5000);
  });
}

renderPlayers([]);
showScreen("espera");
loadAddress();
connect();
