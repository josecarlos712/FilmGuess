// Cliente de la API del PC: cabecera X-Token, errores en español y WebSocket de estado.
import { TEXTOS } from "./textos.js";

const STORAGE_KEY = "fg-token";

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

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
    // Sin almacenamiento (modo privado): la conexión dura lo que la pestaña.
  }
}

/** Token del QR (#token=…) o el último que funcionó. El del enlace se quita de la URL. */
export function initialToken() {
  const hash = new URLSearchParams(location.hash.slice(1));
  const fromLink = hash.get("token");
  if (fromLink) {
    save(STORAGE_KEY, fromLink);
    history.replaceState(null, "", location.pathname + location.search);
    return fromLink;
  }
  return load(STORAGE_KEY);
}

export function forgetToken() {
  save(STORAGE_KEY, null);
}

export function rememberToken(token) {
  save(STORAGE_KEY, token);
}

export function createApi(token) {
  async function request(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: {
          "X-Token": token,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("sin_red", TEXTOS.errorRed, 0);
    }
    let data = null;
    try {
      data = await res.json();
    } catch {
      // Respuesta sin JSON: se trata abajo como error genérico.
    }
    if (!res.ok || (data && data.ok === false)) {
      const code = (data && data.error) || "error";
      const message = (data && data.mensaje) || TEXTOS.errorDesconocido;
      throw new ApiError(code, message, res.status);
    }
    return data;
  }

  return {
    status: () => request("GET", "/status"),
    search: (q, page, all, categoria) => {
      const params = new URLSearchParams({ q, page: String(page), todas: String(all) });
      if (categoria) params.set("categoria", String(categoria));
      return request("GET", `/search?${params}`);
    },
    random: (categoria) =>
      request("GET", categoria ? `/random?categoria=${categoria}` : "/random"),
    genres: () => request("GET", "/genres"),
    hint: () => request("POST", "/hint"),
    frame: () => request("POST", "/frame"),
    skip: () => request("POST", "/skip"),
    clip: () => request("POST", "/clip"),
    award: (jugadores) => request("POST", "/award", { jugadores }),
    fail: (jugadores) => request("POST", "/fail", { jugadores }),
    play: (netflixId, seconds, categoria) =>
      request("POST", "/play", { netflixId, seconds, categoria: categoria || null }),
    pause: () => request("POST", "/pause"),
    resume: () => request("POST", "/resume"),
    reveal: () => request("POST", "/reveal"),
    idle: () => request("POST", "/idle"),
    players: () => request("GET", "/players"),
    addPlayer: (nombre) => request("POST", "/players", { nombre }),
    renamePlayer: (id, nombre) =>
      request("PATCH", `/players/${encodeURIComponent(id)}`, { nombre }),
    removePlayer: (id) => request("DELETE", `/players/${encodeURIComponent(id)}`),
    points: (id, puntos) =>
      request("POST", `/players/${encodeURIComponent(id)}/points`, { puntos }),
    resetPoints: () => request("POST", "/players/reset"),

    /** Abre /ws y se reconecta solo. Devuelve una función para cerrarlo. */
    listen(onEvent, onConnection) {
      let ws = null;
      let retry = 500;
      let closed = false;
      const open = () => {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
        ws.addEventListener("open", () => {
          retry = 500;
          onConnection(true);
        });
        ws.addEventListener("message", (event) => {
          try {
            const msg = JSON.parse(event.data);
            onEvent(msg.tipo, msg.datos);
          } catch {
            // Mensaje que no es JSON: se ignora.
          }
        });
        ws.addEventListener("close", (event) => {
          if (event.code === 1008) {
            // El PC rechaza el token (o la red): reintentar no sirve.
            closed = true;
            onConnection("rechazado");
            return;
          }
          onConnection(false);
          if (closed) return;
          setTimeout(open, retry);
          retry = Math.min(retry * 2, 5000);
        });
      };
      open();
      return () => {
        closed = true;
        if (ws) ws.close();
      };
    },
  };
}
