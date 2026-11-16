// Adivina la Peli: código que se inyecta en cada página antes de que cargue.
//
// Python lo antepone con `window.__fgConfig = {...}` (selectores y CSS). Solo actúa en
// netflix.com. Todo el acceso a Netflix está aquí, con alternativas cuando la API interna
// del reproductor no responde; si Netflix cambia, se corrige en este fichero o en los
// selectores de config.toml.
(() => {
  if (window.__fg || !/(^|\.)netflix\.com$/.test(location.hostname)) return;
  const cfg = window.__fgConfig || { selectors: {}, css: "" };
  const S = cfg.selectors;

  // --- CSS de ocultación, antes de que se pinte nada -------------------------------
  const addStyle = () => {
    if (document.getElementById("fg-hide")) return true;
    const root = document.head || document.documentElement;
    if (!root) return false;
    const style = document.createElement("style");
    style.id = "fg-hide";
    style.textContent = cfg.css;
    root.appendChild(style);
    return true;
  };
  if (!addStyle()) {
    const obs = new MutationObserver(() => addStyle() && obs.disconnect());
    obs.observe(document, { childList: true, subtree: true });
  }

  // --- Cubierta «Preparando fotograma…» en el reproductor --------------------------
  // Tapa el vídeo desde antes de que se pinte hasta que Python lo deja quieto en su segundo
  // (uncover), para que no se vea Netflix cargando ni otro fotograma.
  const TEXTS = cfg.texts || {};
  const cover = (message) => {
    const current = document.getElementById("fg-cover");
    if (current) {
      if (message) current.querySelector("p").textContent = message;
      return true;
    }
    const root = document.body || document.documentElement;
    if (!root) return false;
    // Mismo marcado que la pantalla de carga de /tv (web/cover.css).
    const div = (cls, ...kids) => {
      const node = document.createElement("div");
      node.className = cls;
      node.append(...kids);
      return node;
    };
    const box = div("fg-cover");
    box.id = "fg-cover";
    box.setAttribute("role", "status");
    const label = document.createElement("p");
    label.className = "fgc-label";
    label.textContent = message || TEXTS.cover || "";
    box.append(
      div("fgc-glow"),
      div("fgc-beam"),
      div(
        "fgc-finder",
        div("fgc-corners"),
        div("fgc-rec"),
        div("fgc-leader", div("fgc-sweep"), div("fgc-dot")),
      ),
      label,
      div("fgc-strip", div("fgc-reel"), div("fgc-gate")),
      div("fgc-vignette"),
      div("fgc-grain"),
    );
    root.appendChild(box);
    return true;
  };
  // Se quita al momento (el id deja de existir) y se desvanece en 260 ms.
  const uncover = () => {
    const old = document.getElementById("fg-cover");
    if (old) {
      old.removeAttribute("id");
      old.classList.add("fgc-out");
      setTimeout(() => old.remove(), 300);
    }
    return true;
  };
  if (/^\/watch\//.test(location.pathname) && !cover()) {
    const obs = new MutationObserver(() => cover() && obs.disconnect());
    obs.observe(document, { childList: true, subtree: true });
  }

  const q = (sel) => (sel ? document.querySelector(sel) : null);
  const qa = (sel) => (sel ? Array.from(document.querySelectorAll(sel)) : []);
  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");
  const norm = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase();
  const visible = (el) => !!el && el.getClientRects().length > 0;

  // --- API interna del reproductor ----------------------------------------------
  const api = () => {
    try {
      const vp = window.netflix.appContext.state.playerApp.getAPI().videoPlayer;
      const ids = vp.getAllPlayerSessionIds() || [];
      const id = ids.find((s) => String(s).includes("watch")) || ids[ids.length - 1];
      return id ? vp.getVideoPlayerBySessionId(id) : null;
    } catch (e) {
      return null;
    }
  };
  const video = () => q("video");

  const unavailableRe = new RegExp(S.unavailableText || "no est[aá] disponible|not available", "i");

  const state = () => {
    const path = location.pathname;
    if (/\/(login|loginhelp|signup)\b/i.test(path)) return { kind: "login", url: location.href };
    // Sin sesión, Netflix lleva a su página pública (p. ej. /es/title/ID) con «Iniciar sesión».
    const loginLink = q(S.loginLink);
    if (loginLink && visible(loginLink) && !/\/watch\//.test(path)) {
      return { kind: "login", url: location.href };
    }
    const gate = q(S.profileGate);
    if (gate && visible(gate)) {
      const names = qa(S.profileName).map(text).filter(Boolean);
      const locked = visible(q(S.profilePin));
      return { kind: locked ? "pin" : "profiles", profiles: names };
    }
    const cont = q(S.continueButton);
    if (cont && visible(cont)) return { kind: "continue" };
    const err = q(S.errorView);
    if (err && visible(err)) {
      const msg = text(err).slice(0, 300);
      // M7111-1331, NSES-500, NW-1-19, TVQ-PM-100, UI-800-3…
      const code = (msg.match(/\b[A-Z]{1,5}(?:-[A-Z]{1,5})?-?\d+(?:-[A-Z0-9]+)*\b/) || [null])[0];
      const unavailable = unavailableRe.test(msg) || unavailableRe.test(norm(msg));
      return { kind: unavailable ? "unavailable" : "error", message: msg, code };
    }
    if (/^\/([a-z]{2}(-[a-z]{2})?\/)?title\//i.test(path)) {
      return { kind: "unavailable", message: "", code: null };
    }
    if (!/^\/watch\//.test(path)) return { kind: "elsewhere", url: location.href };
    const v = video();
    const p = api();
    const t = p ? p.getCurrentTime() / 1000 : v ? v.currentTime : null;
    return {
      kind: v && v.readyState >= 3 ? "ready" : "loading",
      currentTime: t,
      paused: p ? p.isPaused() : v ? v.paused : null,
      hasApi: !!p,
      hasVideo: !!v,
      readyState: v ? v.readyState : null,
      duration: p ? p.getDuration() / 1000 : v ? v.duration : null,
    };
  };

  const chooseProfile = (name) => {
    const wanted = norm(name);
    for (const link of qa(S.profileLink)) {
      if (norm(text(link.querySelector(S.profileName) || link)) === wanted) {
        link.click();
        return true;
      }
    }
    return false;
  };

  const clickContinue = () => {
    const b = q(S.continueButton);
    if (!b) return false;
    b.click();
    return true;
  };

  const seek = (seconds) => {
    const p = api();
    if (!p) return false;
    p.seek(Math.round(seconds * 1000));
    return true;
  };

  const pause = () => {
    const p = api();
    if (p) p.pause();
    else if (video()) video().pause();
    return true;
  };

  const play = () => {
    hideReveal();
    hideScores();
    uncover();
    const p = api();
    if (p) p.play();
    else if (video()) video().play();
    return true;
  };

  const showReveal = (info) => {
    hideReveal();
    hideHints();
    const box = document.createElement("div");
    box.id = "fg-reveal";
    const inner = document.createElement("div");
    inner.className = "fg-reveal-inner";
    if (info.poster) {
      const frame = document.createElement("div");
      frame.className = "fg-poster";
      const img = document.createElement("img");
      img.src = info.poster;
      img.alt = "";
      img.addEventListener("error", () => {
        frame.remove();
        inner.classList.add("fg-no-poster");
      });
      frame.appendChild(img);
      inner.appendChild(frame);
    }
    const caption = document.createElement("div");
    caption.className = "fg-reveal-caption";
    const title = document.createElement("h1");
    // Palabra a palabra, para que el título suba escalonado.
    String(info.title)
      .split(/(\s+)/)
      .forEach((part, i) => {
        if (/^\s+$/.test(part) || !part) {
          title.append(part);
          return;
        }
        const word = document.createElement("span");
        word.className = "fg-word";
        word.style.setProperty("--i", String(i / 2));
        word.textContent = part;
        title.appendChild(word);
      });
    caption.appendChild(title);
    if (info.original) {
      const original = document.createElement("p");
      original.className = "fg-original";
      original.textContent = info.original;
      caption.appendChild(original);
    }
    if (info.year) {
      const year = document.createElement("p");
      year.className = "fg-year";
      year.textContent = String(info.year);
      caption.appendChild(year);
    }
    inner.appendChild(caption);
    box.appendChild(inner);
    (document.fullscreenElement || document.body).appendChild(box);
    return true;
  };

  // --- Pistas de la ronda: en una esquina, sin tapar la escena ----------------------
  // data: { value, category, frame, frames, hints: [{ label, text }] }
  const showHints = (data) => {
    hideHints();
    const box = document.createElement("aside");
    box.id = "fg-hints";
    const head = document.createElement("div");
    head.className = "fg-hints-head";
    const value = document.createElement("p");
    value.className = "fg-value";
    value.textContent = data.valueText || "";
    head.appendChild(value);
    for (const extra of [data.category, data.frameText]) {
      if (!extra) continue;
      const chip = document.createElement("p");
      chip.className = "fg-chip";
      chip.textContent = extra;
      head.appendChild(chip);
    }
    box.appendChild(head);
    if (data.hints && data.hints.length) {
      const list = document.createElement("ol");
      for (const [index, hint] of data.hints.entries()) {
        const item = document.createElement("li");
        item.style.setProperty("--i", String(index));
        const label = document.createElement("span");
        label.className = "fg-hint-label" + (hint.long ? " fg-long-label" : "");
        label.textContent = hint.label;
        const body = document.createElement("span");
        body.className = "fg-hint-text" + (hint.long ? " fg-long" : "");
        body.textContent = hint.text;
        item.append(label, body);
        list.appendChild(item);
      }
      box.appendChild(list);
    }
    (document.fullscreenElement || document.body).appendChild(box);
    return true;
  };

  // --- Avisos de lo que pasa en el móvil: arriba a la derecha, sin tapar la escena -----
  // data: { tone: "good" | "bad" | "info", title, detail, delta }
  const toast = (data) => {
    let stack = document.getElementById("fg-toasts");
    const host = document.fullscreenElement || document.body;
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "fg-toasts";
      stack.className = "fg-layer";
    }
    if (stack.parentNode !== host) host.appendChild(stack);
    const item = document.createElement("div");
    item.className = `fg-toast fg-${data.tone || "info"}`;
    if (data.delta) {
      const badge = document.createElement("span");
      badge.className = "fg-toast-delta";
      badge.textContent = data.delta;
      item.appendChild(badge);
    }
    const text = document.createElement("div");
    text.className = "fg-toast-text";
    const title = document.createElement("p");
    title.className = "fg-toast-title";
    title.textContent = data.title || "";
    text.appendChild(title);
    if (data.detail) {
      const detail = document.createElement("p");
      detail.className = "fg-toast-detail";
      detail.textContent = data.detail;
      text.appendChild(detail);
    }
    item.appendChild(text);
    stack.appendChild(item);
    while (stack.children.length > 3) stack.firstChild.remove();
    setTimeout(() => {
      item.classList.add("fg-toast-out");
      setTimeout(() => item.remove(), 260);
    }, data.ms || 3600);
    return true;
  };

  // --- Marcador de fin de ronda: dentro de «Revelar» (la escena ya está tapada) ------
  // data: { title, players: [{ name, points, delta }] }
  const showScores = (data) => {
    const old = document.getElementById("fg-scores");
    if (old) old.remove();
    const reveal = document.getElementById("fg-reveal");
    const caption = reveal && reveal.querySelector(".fg-reveal-caption");
    let extra = caption && caption.querySelector(".fg-reveal-extra");
    if (caption && !extra) {
      extra = document.createElement("div");
      extra.className = "fg-reveal-extra";
      caption.appendChild(extra);
    }
    // Todas las pistas de la ronda: las que se abrieron y las que quedaron sin abrir.
    // Se pintan una vez; al repartir puntos solo se rehace el marcador.
    if (extra && data.hints && data.hints.length && !extra.querySelector(".fg-all-hints")) {
      const block = document.createElement("section");
      block.className = "fg-all-hints";
      const heading = document.createElement("p");
      heading.className = "fg-scores-title";
      heading.textContent = data.hintsTitle || "";
      const list = document.createElement("dl");
      data.hints.forEach((hint, i) => {
        const label = document.createElement("dt");
        label.style.setProperty("--i", String(i));
        label.textContent = hint.label;
        if (!hint.seen && data.unseenText) {
          const tag = document.createElement("span");
          tag.className = "fg-unseen-tag";
          tag.textContent = data.unseenText;
          label.appendChild(tag);
        }
        const text = document.createElement("dd");
        text.style.setProperty("--i", String(i));
        text.textContent = hint.text;
        if (hint.long) text.classList.add("fg-long");
        if (!hint.seen) {
          label.classList.add("fg-unseen");
          text.classList.add("fg-unseen");
        }
        list.append(label, text);
      });
      block.append(heading, list);
      extra.prepend(block);
    }
    const players = data.players || [];
    if (!players.length) return true;
    const box = document.createElement("section");
    box.id = "fg-scores";
    const heading = document.createElement("p");
    heading.className = "fg-scores-title";
    heading.textContent = data.title || "";
    box.appendChild(heading);
    const list = document.createElement("ol");
    const top = Math.max(1, ...players.map((p) => p.points));
    players.forEach((p, i) => {
      const row = document.createElement("li");
      row.style.setProperty("--i", String(i));
      row.style.setProperty("--w", String(Math.max(0, p.points) / top));
      if (i === 0 && p.points > 0) row.className = "fg-first";
      const rank = document.createElement("span");
      rank.className = "fg-rank";
      rank.textContent = String(i + 1);
      const name = document.createElement("span");
      name.className = "fg-name";
      name.textContent = p.name;
      const pts = document.createElement("span");
      pts.className = "fg-pts";
      pts.textContent = String(p.points);
      const delta = document.createElement("span");
      delta.className = "fg-delta" + (p.delta < 0 ? " fg-neg" : "");
      delta.textContent = p.delta ? (p.delta > 0 ? `+${p.delta}` : String(p.delta)) : "";
      const bar = document.createElement("span");
      bar.className = "fg-bar";
      row.append(rank, name, delta, pts, bar);
      list.appendChild(row);
    });
    box.appendChild(list);
    if (extra) extra.appendChild(box);
    else {
      box.classList.add("fg-floating");
      (document.fullscreenElement || document.body).appendChild(box);
    }
    return true;
  };

  function hideScores() {
    const old = document.getElementById("fg-scores");
    if (old) old.remove();
    return true;
  }

  function hideHints() {
    const old = document.getElementById("fg-hints");
    if (old) old.remove();
    return true;
  }

  function hideReveal() {
    const old = document.getElementById("fg-reveal");
    if (old) old.remove();
    return true;
  }

  window.__fg = {
    state,
    toast,
    showScores,
    hideScores,
    chooseProfile,
    clickContinue,
    seek,
    pause,
    play,
    showReveal,
    hideReveal,
    showHints,
    hideHints,
    cover,
    uncover,
  };
})();
