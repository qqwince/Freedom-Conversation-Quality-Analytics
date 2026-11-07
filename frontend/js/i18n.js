// Языки интерфейса: русский (исходный), казахский, английский.
//
// Экраны пишутся по-русски; после отрисовки подписи интерфейса подменяются по словарю
// (текстовые узлы и атрибуты placeholder / title / aria-label). Содержимое диалогов, тексты
// модели и ввод пользователя не переводятся: такие блоки помечены .cqa-msg, .cqa-pre или
// data-i18n-skip — иначе клиентское «Да» в переписке превратилось бы в «Иә».

import { readPref, writePref } from "./prefs.js";
import { DICT, RULES } from "./i18n-dict.js";

export const LANGS = [
  { id: "ru", label: "RU", html: "ru" },
  { id: "kz", label: "KZ", html: "kk" },
  { id: "en", label: "EN", html: "en" },
];

const SKIP = ".cqa-msg, .cqa-pre, [data-i18n-skip], textarea, script, style";
const ATTRS = ["placeholder", "title", "aria-label"];
const INDEX = { kz: 0, en: 1 };

let lang = LANGS.some((l) => l.id === readPref("lang", "ru")) ? readPref("lang", "ru") : "ru";

export function currentLang() {
  return lang;
}

export function setLang(id) {
  writePref("lang", id);
  location.reload();   // исходные русские подписи восстанавливаются заново отрисованной страницей
}

/** Перевод одной строки интерфейса (без перевода — исходная строка). */
export function t(text) {
  if (lang === "ru" || !text) return text;
  return translate(text) ?? text;
}

function exact(text) {
  const entry = DICT[text];
  return entry ? entry[INDEX[lang]] : null;
}

function byRule(text) {
  for (const [pattern, kz, en] of RULES) {
    const m = text.match(pattern);
    if (m) {
      const tpl = lang === "kz" ? kz : en;
      return typeof tpl === "function" ? tpl(...m.slice(1)) : tpl.replace(/\$(\d)/g, (_, i) => m[Number(i)]);
    }
  }
  return null;
}

/** Точное совпадение, перевод по частям «a · b · c» или правило с числами. */
function translate(text) {
  const trimmed = text.trim();
  if (!trimmed || !/[А-Яа-яЁё]/.test(trimmed)) return null;
  const whole = exact(trimmed);
  if (whole != null) return text.replace(trimmed, whole);
  // «оператор Алина · Линия RU · Чат»: части переводятся по отдельности, разделители остаются
  if (/(^|\s)·\s/.test(trimmed)) {
    const parts = trimmed.split(/(^·\s|\s·\s)/).map((p) => (p.trim() ? exact(p) ?? byRule(p) ?? p : p));
    return text.replace(trimmed, parts.join(""));
  }
  const ruled = byRule(trimmed);
  if (ruled != null) return text.replace(trimmed, ruled);
  return null;
}

function translateTree(root) {
  if (lang === "ru" || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);
  for (const node of nodes) {
    if (node.parentElement?.closest(SKIP)) continue;
    const out = translate(node.nodeValue);
    if (out != null && out !== node.nodeValue) node.nodeValue = out;
  }
  const elements = root.nodeType === 1 ? [root, ...root.querySelectorAll("*")] : [];
  for (const el of elements) {
    if (el.closest(SKIP) && !["INPUT", "TEXTAREA"].includes(el.tagName)) continue;
    for (const attr of ATTRS) {
      const value = el.getAttribute(attr);
      if (value) {
        const out = translate(value);
        if (out != null && out !== value) el.setAttribute(attr, out);
      }
    }
    if (el.tagName === "OPTGROUP") {
      const out = translate(el.label);
      if (out) el.label = out;
    }
  }
}

/** Перевод уже отрисованного и всего, что появится дальше (экраны, окна, уведомления). */
export function startTranslating(root) {
  document.documentElement.lang = LANGS.find((l) => l.id === lang).html;
  if (lang === "ru") return;
  translateTree(root);
  let pending = false;
  const queue = new Set();
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === "characterData") queue.add(m.target.parentElement);
      m.addedNodes.forEach((n) => queue.add(n.nodeType === 3 ? n.parentElement : n));
    }
    if (pending) return;
    pending = true;
    queueMicrotask(() => {
      pending = false;
      const items = [...queue];
      queue.clear();
      items.forEach((n) => n && n.isConnected && translateTree(n));
    });
  }).observe(root, { childList: true, subtree: true, characterData: true });
}
