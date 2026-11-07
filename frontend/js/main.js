// Точка входа: шапка (разделы по правам, пользователь, тема), маршрутизация по hash.
// Hash-маршруты работают и как обычная страница, и как HTML web resource в Dynamics 365.

import { api, configureApi, setApiUser } from "./api.js";
import { esc } from "./dom.js";
import { LOGO, icon } from "./icons.js";
import { applyTheme, currentTheme, THEMES } from "./theme.js";
import { can, session, setSession, storedUserId, storeUserId } from "./session.js";
import { markNewSeen, newConversationIds, onNewConversations, refreshSync, startSyncPolling } from "./sync.js";
import { onJobs, setUiHost } from "./ui.js";
import { renderAppeals } from "./views/appeals.js";
import { renderCard } from "./views/card.js";
import { renderChecklist } from "./views/checklist.js";
import { renderConversations } from "./views/conversations.js";
import { renderDashboard, renderMyFeedback } from "./views/dashboard.js";

const NAV = [
  { id: "dashboard", label: "Дашборд", href: "#/dashboard", visible: () => can("dashboard") },
  { id: "me", label: "Моя сводка", href: "#/me", visible: () => session().user?.role === "operator" },
  { id: "conversations", label: "Диалоги", href: "#/conversations", visible: () => can("view_dialogs") },
  { id: "appeals", label: "Апелляции", href: "#/appeals", visible: () => can("appeal") || can("review_appeals") },
  { id: "checklist", label: "Чек-лист", href: "#/checklist", visible: () => true },
];

const ROUTES = [
  { pattern: /^#\/dashboard$/, nav: "dashboard", render: (el) => renderDashboard(el) },
  { pattern: /^#\/me$/, nav: "me", render: (el) => renderMyFeedback(el) },
  { pattern: /^#\/conversations$/, nav: "conversations", render: (el) => renderConversations(el) },
  { pattern: /^#\/c\/(.+)$/, nav: "conversations", render: (el, [id]) => renderCard(el, decodeURIComponent(id)) },
  { pattern: /^#\/appeals$/, nav: "appeals", render: (el) => renderAppeals(el) },
  { pattern: /^#\/checklist$/, nav: "checklist", render: (el) => renderChecklist(el) },
];

async function loadSession() {
  setApiUser(storedUserId());
  try {
    setSession(await api.me());
  } catch {
    storeUserId(null);              // пользователь из старой конфигурации — вернуться к администратору
    setApiUser(null);
    setSession(await api.me());
  }
}

function homeRoute() {
  if (can("dashboard")) return "#/dashboard";
  return session().user?.role === "operator" ? "#/me" : "#/conversations";
}

async function mount(root) {
  configureApi(root.dataset.apiBase);
  setUiHost(root);
  root.classList.add("cqa-app");
  applyTheme(currentTheme(), root);
  await loadSession();

  root.innerHTML = `
    <header class="cqa-header">
      <div class="cqa-header__inner">
        <a class="cqa-logo" data-role="home">${LOGO}<span>CQA</span></a>
        <nav class="cqa-nav" aria-label="Разделы" data-role="nav"></nav>
        <a class="cqa-pill cqa-pill--good" data-role="new" href="#/conversations?new" hidden title="Новые диалоги из API организаторов"></a>
        <div class="cqa-job" data-role="job" hidden></div>
        <select class="cqa-select cqa-user" data-role="user" aria-label="Вы вошли как"></select>
        <button class="cqa-icon-btn" data-role="theme" title="Тема оформления" aria-label="Тема оформления"></button>
      </div>
    </header>
    <main class="cqa-main" data-role="view"></main>`;

  const view = root.querySelector("[data-role=view]");

  const drawHeader = () => {
    const { user, users } = session();
    root.querySelector("[data-role=home]").href = homeRoute();
    root.querySelector("[data-role=nav]").innerHTML = NAV.filter((n) => n.visible()).map((n) =>
      `<a href="${n.href}" data-nav="${n.id}">${esc(n.label)}${n.id === "appeals"
        ? ' <span class="cqa-pill cqa-pill--bad cqa-pill--sm" data-role="appeals-count" hidden></span>' : ""}</a>`).join("");
    // Пользователи сгруппированы по ролям — сразу видно, кем вы смотрите систему.
    const roles = [...new Set(users.map((u) => u.role_title))];
    root.querySelector("[data-role=user]").innerHTML = roles.map((role) => `<optgroup label="${esc(role)}">${users
      .filter((u) => u.role_title === role)
      .map((u) => `<option value="${esc(u.id)}" ${u.id === user.id ? "selected" : ""}>${esc(u.name)}</option>`)
      .join("")}</optgroup>`).join("");
    const theme = THEMES.find((t) => t.id === currentTheme());
    root.querySelector("[data-role=theme]").innerHTML = icon(theme.icon);
    root.querySelector("[data-role=theme]").title = `Тема: ${theme.label}`;
  };

  const route = async () => {
    let hash = location.hash || homeRoute();
    if (hash === "#/conversations?new") {        // переход по пометке «+N новых»
      sessionStorage.setItem("cqa.show-new", "1");
      hash = "#/conversations";
      history.replaceState(null, "", hash);
      refreshSync();
    }
    const match = ROUTES.map((r) => ({ r, m: hash.match(r.pattern) })).find((x) => x.m);
    const allowed = match && NAV.find((n) => n.id === match.r.nav)?.visible();
    if (!match || !allowed) {
      location.hash = homeRoute();
      return;
    }
    root.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("is-active", a.dataset.nav === match.r.nav));
    view.innerHTML = '<div class="cqa-loading">Загрузка…</div>';
    window.scrollTo(0, 0);
    try {
      await match.r.render(view, match.m.slice(1));
    } catch (err) {
      view.innerHTML = `<div class="cqa-empty"><h3>Не удалось загрузить</h3><p>${esc(err.message)}</p></div>`;
    }
    refreshAppealsCount(root);
  };

  root.querySelector("[data-role=user]").addEventListener("change", async (event) => {
    storeUserId(event.target.value);
    await loadSession();
    drawHeader();
    location.hash = homeRoute();
    route();
  });
  root.querySelector("[data-role=theme]").addEventListener("click", () => {
    const index = THEMES.findIndex((t) => t.id === currentTheme());
    applyTheme(THEMES[(index + 1) % THEMES.length].id, root);
    drawHeader();
  });

  window.addEventListener("cqa:new-build", () => {
    const bar = document.createElement("button");
    bar.className = "cqa-update-bar";
    bar.textContent = "Интерфейс обновлён — нажмите, чтобы загрузить новую версию";
    bar.addEventListener("click", () => location.reload());
    root.prepend(bar);
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("cqa:appeals-changed", () => refreshAppealsCount(root));
  onJobs((jobs) => renderJobs(root.querySelector("[data-role=job]"), jobs));
  onNewConversations((ids) => {
    const badge = root.querySelector("[data-role=new]");
    badge.hidden = ids.length === 0;
    badge.textContent = `+${ids.length} ${ids.length === 1 ? "новый" : "новых"}`;
  });
  root.querySelector("[data-role=new]").addEventListener("click", () => {
    sessionStorage.setItem("cqa.new-ids", JSON.stringify(newConversationIds()));
    markNewSeen();
  });
  startSyncPolling();
  drawHeader();
  route();
}

async function refreshAppealsCount(root) {
  const badge = root.querySelector("[data-role=appeals-count]");
  if (!badge) return;
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
