// Точка входа: шапка, маршрутизация по hash (#/conversations, #/c/<id>, #/appeals, #/checklist).
// Hash-маршруты работают и как обычная страница, и как HTML web resource в Dynamics 365.

import { api, configureApi } from "./api.js";
import { esc } from "./dom.js";
import { LOGO } from "./icons.js";
import { onJobs, setUiHost } from "./ui.js";
import { renderAppeals } from "./views/appeals.js";
import { renderCard } from "./views/card.js";
import { renderChecklist } from "./views/checklist.js";
import { renderConversations } from "./views/conversations.js";

const ROUTES = [
  { pattern: /^#\/conversations$/, nav: "conversations", render: (el) => renderConversations(el) },
  { pattern: /^#\/c\/(.+)$/, nav: "conversations", render: (el, [id]) => renderCard(el, decodeURIComponent(id)) },
  { pattern: /^#\/appeals$/, nav: "appeals", render: (el) => renderAppeals(el) },
  { pattern: /^#\/checklist$/, nav: "checklist", render: (el) => renderChecklist(el) },
];

function mount(root) {
  configureApi(root.dataset.apiBase);
  setUiHost(root);
  root.classList.add("cqa-app");
  root.innerHTML = `
    <header class="cqa-header">
      <div class="cqa-header__inner">
        <a class="cqa-logo" href="#/conversations">${LOGO}<span>CQA</span></a>
        <nav class="cqa-nav" aria-label="Разделы">
          <a href="#/conversations" data-nav="conversations">Диалоги</a>
          <a href="#/appeals" data-nav="appeals">Апелляции <span class="cqa-pill cqa-pill--bad cqa-pill--sm" data-role="appeals-count" hidden></span></a>
          <a href="#/checklist" data-nav="checklist">Чек-лист</a>
        </nav>
        <div class="cqa-job" data-role="job" hidden></div>
      </div>
    </header>
    <main class="cqa-main" data-role="view"></main>`;

  const view = root.querySelector("[data-role=view]");
  let disposeView = null;

  const route = async () => {
    const hash = location.hash || "#/conversations";
    const match = ROUTES.map((r) => ({ r, m: hash.match(r.pattern) })).find((x) => x.m);
    if (!match) {
      location.hash = "#/conversations";
      return;
    }
    root.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("is-active", a.dataset.nav === match.r.nav));
    disposeView?.();
    view.innerHTML = '<div class="cqa-loading">Загрузка…</div>';
    window.scrollTo(0, 0);
    try {
      disposeView = (await match.r.render(view, match.m.slice(1))) ?? null;
    } catch (err) {
      view.innerHTML = `<div class="cqa-empty"><h3>Не удалось загрузить</h3><p>${esc(err.message)}</p></div>`;
    }
    refreshAppealsCount(root);
  };

  window.addEventListener("hashchange", route);
  window.addEventListener("cqa:appeals-changed", () => refreshAppealsCount(root));
  onJobs((jobs) => renderJobs(root.querySelector("[data-role=job]"), jobs));
  route();
}

async function refreshAppealsCount(root) {
  const badge = root.querySelector("[data-role=appeals-count]");
  try {
    const open = await api.appeals("open");
    badge.textContent = open.length;
    badge.hidden = open.length === 0;
  } catch {
    badge.hidden = true;
  }
}

function renderJobs(el, jobs) {
  if (!jobs.length) {
    el.hidden = true;
    return;
  }
  const done = jobs.reduce((s, j) => s + j.done + j.failed, 0);
  const totalCount = jobs.reduce((s, j) => s + j.total, 0);
  const finished = jobs.every((j) => j.status === "finished");
  el.hidden = false;
  el.innerHTML = `
    <span>${finished ? "Оценка завершена" : "Оценка"} ${done} / ${totalCount}</span>
    <div class="cqa-progress grow"><div style="width:${totalCount ? (done / totalCount) * 100 : 0}%"></div></div>`;
}

const root = document.querySelector("[data-cqa-app]");
if (root) mount(root);
