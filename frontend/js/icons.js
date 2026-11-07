// Набор иконок (линейные, 24×24). Встроены в код — компоненту не нужны внешние шрифты.

const PATHS = {
  back: '<path d="M15 6l-6 6 6 6"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
  messages: '<path d="M4 6a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-4 3v-3a1 1 0 0 1-1-1z"/><path d="M17 9h1a2 2 0 0 1 2 2v6l-3-2h-5a2 2 0 0 1-2-2"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  upload: '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  download: '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  play: '<path d="M8 5v14l11-7z"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  chat: '<path d="M5 5h14a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-8l-5 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.3-5.7"/><path d="M4 4v4h4"/><path d="M12 8v4l3 2"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  contrast: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".5"/>',
  scale: '<path d="M12 4v16M5 20h14"/><path d="M6 8l-3 6a3 3 0 0 0 6 0zM18 8l-3 6a3 3 0 0 0 6 0z"/><path d="M6 8h12"/>',
};

export function icon(name, extraClass = "") {
  return `<svg class="ic ${extraClass}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name] ?? ""}</svg>`;
}

// Логотип: облачко диалога с галочкой — «проверенный разговор».
export const LOGO = `<svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
  <rect width="30" height="30" rx="9" fill="var(--cqa-green)"/>
  <path d="M8 10.5a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v5.5a3 3 0 0 1-3 3h-4.2L11 22.2V19a3 3 0 0 1-3-3z"
        fill="none" stroke="var(--cqa-text)" stroke-width="1.8" stroke-linejoin="round"/>
  <path d="M12 13.4l2.1 2.1 4-4.2" fill="none" stroke="var(--cqa-green-fg)" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;
