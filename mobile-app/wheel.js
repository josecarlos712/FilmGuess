// Rueda de tiempo: tres columnas (horas, minutos, segundos) con scroll-snap.

const pad = (n) => String(n).padStart(2, "0");

/** h:mm:ss (las horas sin cero delante). */
export function hms(total) {
  const s = Math.max(0, Math.round(total || 0));
  return `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

function column(container, count, label, onChange) {
  const col = document.createElement("div");
  col.className = "wheel-col";
  const list = document.createElement("div");
  list.className = "wheel-list";
  list.tabIndex = 0;
  list.setAttribute("role", "spinbutton");
  list.setAttribute("aria-label", label);
  list.setAttribute("aria-valuemin", "0");
  list.setAttribute("aria-valuemax", String(count - 1));
  const items = [];
  for (let i = 0; i < count; i += 1) {
    const item = document.createElement("div");
    item.className = "wheel-item num";
    item.textContent = pad(i);
    items.push(item);
    list.appendChild(item);
  }
  const unit = document.createElement("span");
  unit.className = "wheel-unit";
  unit.textContent = label;
  col.append(list, unit);
  container.appendChild(col);

  let value = 0;
  const itemHeight = () => items[0].getBoundingClientRect().height || 48;
  const mark = (v) => {
    items.forEach((it, i) => it.classList.toggle("on", i === v));
    list.setAttribute("aria-valuenow", String(v));
  };
  const set = (v, smooth) => {
    value = Math.min(count - 1, Math.max(0, v));
    list.scrollTo({ top: value * itemHeight(), behavior: smooth ? "smooth" : "auto" });
    mark(value);
  };
  // El encaje lo hace scroll-snap; aquí solo se lee la fila del centro.
  list.addEventListener("scroll", () => {
    const v = Math.min(count - 1, Math.max(0, Math.round(list.scrollTop / itemHeight())));
    if (v !== value) {
      value = v;
      mark(v);
      onChange();
    }
  });
  list.addEventListener("keydown", (event) => {
    const step = { ArrowUp: -1, ArrowDown: 1, PageUp: -10, PageDown: 10 }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    set(value + step, true);
    onChange();
  });
  items.forEach((it, i) =>
    it.addEventListener("click", () => {
      set(i, true);
      onChange();
    }),
  );
  return { get: () => value, set };
}

/**
 * Crea la rueda en `container`.
 * @param {HTMLElement} container
 * @param {{maxHours: number, labels: string[], onChange: (seconds: number) => void}} options
 */
export function createWheel(container, { maxHours, labels, onChange }) {
  container.replaceChildren();
  const changed = () => onChange(api.get());
  const h = column(container, Math.max(1, maxHours + 1), labels[0], changed);
  const m = column(container, 60, labels[1], changed);
  const s = column(container, 60, labels[2], changed);
  const api = {
    get: () => h.get() * 3600 + m.get() * 60 + s.get(),
    set(total) {
      const t = Math.max(0, Math.round(total));
      h.set(Math.floor(t / 3600));
      m.set(Math.floor((t % 3600) / 60));
      s.set(t % 60);
    },
  };
  return api;
}
