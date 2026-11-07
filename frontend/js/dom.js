// Мелкие помощники для разметки. Любой текст из данных проходит через esc():
// реплики клиентов — пользовательский ввод, вставлять его как HTML нельзя.

export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function $$(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

/**
 * Свежий корень экрана внутри контейнера. Обработчики вешаются на него, поэтому при
 * повторной отрисовке или переходе старые обработчики уходят вместе со старым элементом.
 */
export function freshRoot(container) {
  const root = document.createElement("div");
  container.replaceChildren(root);
  return root;
}

/** Делегирование событий: on(root, "click", "[data-action=x]", (event, target) => …). */
export function on(root, type, selector, handler) {
  root.addEventListener(type, (event) => {
    const target = event.target.closest(selector);
    if (target && root.contains(target)) handler(event, target);
  });
}

/** Число без лишних нулей: 12.5 → «12.5», 18.75 → «18.75», 10 → «10». */
export function num(value, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value.toFixed(digits)).toString();
}

/** Итог: всегда два знака, как в правилах чек-листа. */
export function total(value) {
  return value == null ? "—" : value.toFixed(2);
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Время реплики: MM:SS для чатов, ЧЧ:ММ:СС от начала — для звонков. */
export function formatOffset(seconds) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}

export function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function debounce(fn, ms = 200) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
