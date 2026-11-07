// Темы оформления: тёмная (по умолчанию), светлая и высококонтрастная. Цвета — в css/tokens.css.

import { readPref, writePref } from "./prefs.js";

export const THEMES = [
  { id: "dark", label: "тёмная", icon: "moon" },
  { id: "light", label: "светлая", icon: "sun" },
  { id: "contrast", label: "высокий контраст", icon: "contrast" },
];

export function currentTheme() {
  const saved = readPref("theme", null);
  return THEMES.some((t) => t.id === saved) ? saved : "dark";
}

export function applyTheme(id, root) {
  root.dataset.theme = id;
  const bg = getComputedStyle(root).getPropertyValue("--cqa-bg").trim();
  document.documentElement.style.background = bg;
  document.body.style.background = bg;
  document.documentElement.style.colorScheme = id === "light" ? "light" : "dark";
  writePref("theme", id);
}
