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
  const cover = () => {
    if (document.getElementById("fg-cover")) return true;
    const root = document.body || document.documentElement;
    if (!root) return false;
    const box = document.createElement("div");
    box.id = "fg-cover";
    const label = document.createElement("p");
    label.textContent = TEXTS.cover || "";
    const bar = document.createElement("div");
    bar.className = "fg-bar";
    box.append(label, bar);
    root.appendChild(box);
    return true;
  };
  const uncover = () => {
    const old = document.getElementById("fg-cover");
    if (old) old.remove();
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
      const code = (msg.match(/\b[A-Z]{1,3}-?\d{3,5}(-\d+)?\b/) || [null])[0];
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
    uncover();
    const p = api();
    if (p) p.play();
    else if (video()) video().play();
    return true;
  };

  const showReveal = (info) => {
    hideReveal();
    const box = document.createElement("div");
    box.id = "fg-reveal";
    const inner = document.createElement("div");
    inner.className = "fg-reveal-inner";
    if (info.poster) {
      const img = document.createElement("img");
      img.src = info.poster;
      img.alt = "";
      img.addEventListener("error", () => {
        img.remove();
        inner.classList.add("fg-no-poster");
      });
      inner.appendChild(img);
    }
    const caption = document.createElement("div");
    caption.className = "fg-reveal-caption";
    const title = document.createElement("h1");
    title.textContent = info.title;
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

  function hideReveal() {
    const old = document.getElementById("fg-reveal");
    if (old) old.remove();
    return true;
  }

  window.__fg = {
    state,
    chooseProfile,
    clickContinue,
    seek,
    pause,
    play,
    showReveal,
    hideReveal,
    cover,
    uncover,
  };
})();
