// Дашборд руководителя (KPI качества, соблюдение критериев, грубые ошибки, отделы, рейтинг
// операторов) и «Моя сводка» для оператора. Все цифры считает сервер из выставленных оценок.

import { api } from "../api.js";
import { esc, freshRoot, num, on, pagerHtml, paginate, plural, total } from "../dom.js";
import { icon } from "../icons.js";
import { totalTone } from "../labels.js";
import { countAll, staggerStyle } from "../motion.js";
import { session } from "../session.js";
import { modal, toast } from "../ui.js";

const OPERATORS_PAGE = 10;
const CRITICAL_PAGE = 3;
let operatorsPage = 0;
let criticalPage = 0;

const SUBTYPE = {
  insult: "оскорбление клиента",
  bypass_service: "услуга в обход компании",
  confidential_leak: "раскрытие конфиденциальной информации",
};

export async function renderDashboard(container) {
  const d = await api.dashboard();
  const el = freshRoot(container);
  criticalPage = 0;

  el.innerHTML = `
    <div class="cqa-page-head"><div class="grow"><h1>Дашборд качества</h1></div></div>
    <div class="cqa-tiles" style="margin-bottom:12px">
      ${d.kpi.map(kpiTile).join("")}
      <div class="cqa-tile cqa-stagger" style="${staggerStyle(3)}"><div class="cqa-tile__label">Требуют проверки</div><div class="cqa-tile__value cqa-part"><span data-count="${d.summary.needs_review}">0</span></div></div>
      <div class="cqa-tile cqa-stagger" style="${staggerStyle(4)}"><div class="cqa-tile__label">Открытых апелляций</div><div class="cqa-tile__value"><span data-count="${d.summary.open_appeals}">0</span></div></div>
    </div>

    <div class="cqa-dash-grid">
      <section class="cqa-card cqa-dash-card">
        <div class="row" style="margin-bottom:12px"><h2 class="grow">Соблюдение критериев</h2>
          <span class="xs faint" title="На сколько вырастет средний балл, если критерий выполнять всегда">рост балла</span></div>
        ${d.criteria.filter((c) => c.type !== "flag").map((c, i) => criterionBar(c, i)).join("")}
      </section>
      <section class="cqa-card cqa-dash-card">
        <h2>Распределение итогов</h2>
        <div style="margin-top:14px" data-role="chart">${distribution(d.distribution, d.summary.avg_score, d.score_target)}</div>
      </section>
    </div>

    <section style="margin-top:20px">
      <h2 style="margin-bottom:10px">Грубые ошибки <span class="faint small" style="font-weight:400">· ${d.critical.length}</span></h2>
      <div data-role="critical">${criticalList(d.critical)}</div>
    </section>

    <section style="margin-top:20px">
      <h2 style="margin-bottom:10px">Линии</h2>
      <div class="cqa-list">
        <div class="cqa-list__row cqa-list__head"><span class="grow">Линия</span><span style="width:100px">Средний балл</span>
          <span style="width:90px">Грубые ошибки</span><span class="cqa-hide-sm" style="width:120px">Обращений сегодня</span>
          <span class="cqa-hide-sm" style="width:120px">Время на обращение</span>
          <span class="cqa-hide-sm" style="width:110px">Оценка клиентов</span><span style="width:18px"></span></div>
        ${d.departments.map(departmentRow).join("")}
      </div>
    </section>

    <section style="margin-top:20px">
      <h2 style="margin-bottom:10px">Рейтинг операторов</h2>
      <div data-role="operators">${operatorsTable(d.operators)}</div>
    </section>`;

  bindChartHover(el.querySelector("[data-role=chart]"));
  countAll(el);
  on(el, "click", "[data-feedback]", (e, btn) => { e.stopPropagation(); feedbackDialog(btn.dataset.feedback); });
  on(el, "click", "[data-pager=operators]", (e, btn) => {
    operatorsPage = Number(btn.dataset.page);
    el.querySelector("[data-role=operators]").innerHTML = operatorsTable(d.operators);
  });
  on(el, "click", "[data-pager=critical]", (e, btn) => {
    criticalPage = Number(btn.dataset.page);
    el.querySelector("[data-role=critical]").innerHTML = criticalList(d.critical);
  });
  on(el, "click", "[data-open]", (e, row) => { location.hash = `#/c/${encodeURIComponent(row.dataset.open)}`; });
  on(el, "click", "[data-department]", (e, row) => {
    // линия → список её диалогов
    sessionStorage.setItem("cqa.filter-department", row.dataset.department);
    location.hash = "#/conversations";
  });
}

function criticalList(items) {
  if (!items.length) return `<div class="cqa-list cqa-empty"><p>Грубых ошибок не найдено.</p></div>`;
  const page = paginate(items, criticalPage, CRITICAL_PAGE);
  criticalPage = page.page;
  return `<div class="cqa-list">${page.items.map(criticalRow).join("")}</div>${pagerHtml(page, "critical")}`;
}

function kpiTile(k, i) {
  const ok = k.value == null || k.target == null ? null
    : k.better === "lower" ? k.value <= k.target : k.value >= k.target;
  const value = k.value == null ? "—"
    : `<span data-count="${k.value}" data-decimals="${k.id === "avg_score" ? 1 : 0}">0</span>`;
  return `<div class="cqa-tile cqa-stagger" style="${staggerStyle(i)}">
    <div class="cqa-tile__label">${esc(k.title)}</div>
    <div class="cqa-tile__value ${ok == null ? "" : ok ? "cqa-good" : "cqa-bad"}">${value}<span> ${k.id === "compliance" ? "из" : "цель"} ${k.target ?? "—"}</span></div>
  </div>`;
}

function criterionBar(c, i) {
  const value = c.compliance ?? 0;
  const tone = value >= 90 ? "var(--cqa-green-fg)" : value >= 70 ? "var(--cqa-amber-fg)" : "var(--cqa-red-fg)";
  return `<div style="margin-bottom:10px">
    <div class="row" style="gap:8px"><span class="grow small">${esc(c.title)}</span>
      <span class="small" style="font-weight:600">${c.compliance == null ? "—" : num(c.compliance, 0) + "%"}</span>
      <span class="xs ${c.gain > 0 ? "cqa-good" : "faint"}" style="width:62px;text-align:right">${c.gain > 0 ? "+" + num(c.gain, 1) : ""}</span></div>
    <div class="cqa-progress" style="margin-top:4px"><div class="cqa-bar-fill" style="width:${value}%;background:${tone};${staggerStyle(i)}"></div></div>
  </div>`;
}

// --- Распределение итогов: плавная кривая по 10 интервалам ---------------------------------

const CHART = { w: 380, h: 250, left: 30, right: 12, top: 34, bottom: 28 };

/** Монотонная кубическая интерполяция (Фриче — Карлсон): кривая не уходит ниже нуля и не «звенит». */
function smoothPath(points) {
  const n = points.length;
  if (n < 2) return "";
  const dx = [], slope = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1].x - points[i].x);
    slope.push((points[i + 1].y - points[i].y) / dx[i]);
  }
  const t = [slope[0]];
  for (let i = 1; i < n - 1; i++) t.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2);
  t.push(slope[n - 2]);
  for (let i = 0; i < n - 1; i++) {
    if (slope[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / slope[i], b = t[i + 1] / slope[i], h = Math.hypot(a, b);
    if (h > 3) { t[i] = (3 * a * slope[i]) / h; t[i + 1] = (3 * b * slope[i]) / h; }
  }
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 0; i < n - 1; i++) {
    const p = points[i], q = points[i + 1], k = dx[i] / 3;
    d += ` C${p.x + k},${p.y + t[i] * k} ${q.x - k},${q.y - t[i + 1] * k} ${q.x},${q.y}`;
  }
  return d;
}

function distribution(buckets, avg, target) {
  const { w, h, left, right, top, bottom } = CHART;
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const x = (score) => left + (score / 100) * (w - left - right);
  const y = (count) => top + (1 - count / max) * (h - top - bottom);
  const base = y(0);
  const pts = buckets.map((b) => ({ x: x((b.from + b.to) / 2), y: y(b.count), b }));
  const line = smoothPath(pts);
  const area = `${line} L${pts.at(-1).x},${base} L${pts[0].x},${base} Z`;
  const peak = pts.reduce((m, p) => (p.b.count > m.b.count ? p : m), pts[0]);
  // Подписи линий «средний» и «цель»: при близких значениях расходятся влево и вправо.
  const close = avg != null && target != null && Math.abs(x(avg) - x(target)) < 70;
  const vline = (score, label, dashed, side) => score == null ? "" : `
    <line x1="${x(score)}" x2="${x(score)}" y1="${top - 8}" y2="${base}" stroke="var(--cqa-text-3)" stroke-width="1.5" ${dashed ? 'stroke-dasharray="4 4"' : ""}/>
    <text x="${x(score) + (side === "left" ? -4 : side === "right" ? 4 : 0)}" y="${top - 14}"
      text-anchor="${side === "left" ? "end" : side === "right" ? "start" : "middle"}" class="cqa-chart-label">${label}</text>`;
  const avgSide = !close ? "middle" : avg <= target ? "left" : "right";
  const targetSide = !close ? "middle" : avg <= target ? "right" : "left";
  const columnW = (w - left - right) / buckets.length;
  return `<div class="cqa-chart" style="position:relative">
    <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Распределение итоговых баллов по интервалам">
      <line x1="${left}" x2="${w - right}" y1="${y(max)}" y2="${y(max)}" stroke="var(--cqa-line)" stroke-dasharray="2 4"/>
      <line x1="${left}" x2="${w - right}" y1="${y(max / 2)}" y2="${y(max / 2)}" stroke="var(--cqa-line)" stroke-dasharray="2 4"/>
      <line x1="${left}" x2="${w - right}" y1="${base}" y2="${base}" stroke="var(--cqa-line-strong)"/>
      <text x="${left - 8}" y="${y(max) + 4}" text-anchor="end" class="cqa-chart-label">${max}</text>
      <text x="${left - 8}" y="${base + 4}" text-anchor="end" class="cqa-chart-label">0</text>
      ${[0, 20, 40, 60, 80, 100].map((s) => `<text x="${x(s)}" y="${h - 8}" text-anchor="middle" class="cqa-chart-label">${s}</text>`).join("")}
      <path d="${area}" fill="var(--cqa-green)" opacity="0.22" class="cqa-chart-area"/>
      <path d="${line}" fill="none" stroke="var(--cqa-green-fg)" stroke-width="2" stroke-linejoin="round" pathLength="1" class="cqa-draw"/>
      ${vline(target, `цель ${target}`, true, targetSide)}
      ${vline(avg, `средний ${avg == null ? "" : num(avg, 1)}`, false, avgSide)}
      ${pts.map((p, i) => `<circle cx="${p.x}" cy="${p.y}" r="4.5" fill="var(--cqa-green-fg)" stroke="var(--cqa-surface-1)" stroke-width="2" data-dot="${p.b.label}" class="cqa-chart-dot" style="${staggerStyle(i, 10)}"/>`).join("")}
      <text x="${peak.x - 8}" y="${peak.y + 4}" text-anchor="end" class="cqa-chart-value">${peak.b.count}</text>
      ${pts.map((p, i) => `<rect x="${left + i * columnW}" y="${top}" width="${columnW}" height="${base - top}" fill="transparent"
          data-bin="${i}" data-label="${esc(p.b.label)}" data-count="${p.b.count}"/>`).join("")}
    </svg>
    <div class="cqa-chart-tip" data-role="tip" hidden></div>
    <table class="sr-only"><caption>Распределение итогов</caption>
      ${buckets.map((b) => `<tr><th>${esc(b.label)} баллов</th><td>${b.count}</td></tr>`).join("")}</table>
  </div>`;
}

function bindChartHover(el) {
  el.addEventListener("mouseover", (event) => {
    const bin = event.target.closest?.("[data-bin]");
    const tip = el.querySelector("[data-role=tip]");
    if (!tip) return;
    el.querySelectorAll("[data-dot]").forEach((c) => c.setAttribute("r", "4.5"));
    if (!bin) { tip.hidden = true; return; }
    const count = Number(bin.dataset.count);
    el.querySelector(`[data-dot="${CSS.escape(bin.dataset.label)}"]`)?.setAttribute("r", "6.5");
    tip.textContent = `${bin.dataset.label} баллов: ${count} ${plural(count, "диалог", "диалога", "диалогов")}`;
    const box = bin.getBoundingClientRect(), host = el.querySelector(".cqa-chart").getBoundingClientRect();
    tip.style.left = `${box.left - host.left + box.width / 2}px`;
    tip.hidden = false;
  });
  el.addEventListener("mouseleave", () => { const tip = el.querySelector("[data-role=tip]"); if (tip) tip.hidden = true; });
}

function criticalRow(c) {
  const kinds = c.subtypes.map((s) => SUBTYPE[s] ?? s).join(", ") || "грубое нарушение";
  return `<div class="cqa-list__row" data-open="${esc(c.conversation_id)}" style="align-items:flex-start">
    <span class="cqa-bad" style="margin-top:2px">${icon("alert")}</span>
    <div class="grow">
      <div><b style="font-weight:500">${esc(c.conversation_id)}</b> <span class="small faint">· ${esc(c.operator)} · ${esc(c.department_title)}</span></div>
      <div class="small cqa-bad">${esc(kinds)}</div>
      <div class="small muted" style="margin-top:2px">${esc(c.reason)}</div>
    </div>
    <span class="${totalTone(c.total)}" style="font-weight:600">${total(c.total)}</span>
  </div>`;
}

function departmentRow(dep) {
  const w = dep.workload;
  const minutes = (s) => `${Math.floor(s / 60)} мин ${String(s % 60).padStart(2, "0")} с`;
  return `<div class="cqa-list__row" data-department="${esc(dep.department)}" title="Открыть диалоги линии">
    <span class="grow" style="font-weight:500">${esc(dep.title)} <span class="xs faint">· ${dep.dialogs} ${plural(dep.dialogs, "диалог", "диалога", "диалогов")}</span></span>
    <span style="width:100px;font-weight:600" class="${totalTone(dep.avg_score)}">${dep.avg_score == null ? "—" : num(dep.avg_score, 1)}</span>
    <span style="width:90px" class="${dep.critical ? "cqa-bad" : "faint"}">${dep.critical}</span>
    <span class="cqa-hide-sm small" style="width:120px">${w.contacts_today} из ${w.contacts_plan}</span>
    <span class="cqa-hide-sm small ${w.aht_sec <= w.aht_target_sec ? "cqa-good" : "cqa-part"}" style="width:120px">${minutes(w.aht_sec)}</span>
    <span class="cqa-hide-sm small ${w.csat >= w.csat_target ? "cqa-good" : "cqa-part"}" style="width:110px">${String(w.csat).replace(".", ",")} из 5</span>
    <span class="faint" style="transform:rotate(-90deg)">${icon("chevron")}</span>
  </div>`;
}

function operatorsTable(operators) {
  const page = paginate(operators, operatorsPage, OPERATORS_PAGE);
  operatorsPage = page.page;
  return `<div class="cqa-list">
      <div class="cqa-list__row cqa-list__head"><span style="width:28px">#</span><span class="grow">Оператор</span>
        <span style="width:70px">Диалогов</span><span style="width:90px">Средний балл</span><span style="width:90px"></span></div>
      ${page.items.map((op, i) => operatorRow(op, page.from - 1 + i)).join("")}
    </div>${pagerHtml(page, "operators")}`;
}

function operatorRow(op, i) {
  return `<div class="cqa-list__row" style="cursor:default">
    <span class="faint" style="width:28px">${i + 1}</span>
    <span class="grow"><span style="font-weight:500">${esc(op.operator)}</span> <span class="xs faint">· ${esc(op.department_title)}</span>
      ${op.critical ? ` <span class="cqa-pill cqa-pill--bad cqa-pill--sm">грубых: ${op.critical}</span>` : ""}</span>
    <span style="width:70px" class="faint">${op.dialogs}</span>
    <span style="width:90px;font-weight:600" class="${totalTone(op.avg_score)}">${num(op.avg_score, 1)}</span>
    <span style="width:90px;text-align:right"><button class="cqa-btn cqa-btn--sm" data-feedback="${esc(op.operator)}">Сводка</button></span>
  </div>`;
}

/** Диалоги оператора со ссылкой в карточку: грубые ошибки и низкие итоги сверху. */
function conversationsList(items) {
  if (!items.length) return "";
  return `<div><div class="xs faint" style="margin-bottom:6px">Оценённые диалоги — откройте, чтобы увидеть обоснование по каждому критерию</div>
    <div class="cqa-list">${items.map((c) => `
      <a class="cqa-list__row" href="#/c/${encodeURIComponent(c.conversation_id)}" data-close-modal>
        <span class="grow" style="font-weight:500">${esc(c.conversation_id)}
          ${c.critical ? ' <span class="cqa-pill cqa-pill--bad cqa-pill--sm">грубая ошибка</span>' : ""}</span>
        <span style="font-weight:600" class="${totalTone(c.total)}">${total(c.total)}</span>
        <span class="faint" style="transform:rotate(-90deg)">${icon("chevron")}</span>
      </a>`).join("")}</div></div>`;
}

/** Текст сводки и цифры — общий блок для руководителя (модально) и для самого оператора. */
export function feedbackHtml(f) {
  return `
    <div class="row" style="gap:16px;flex-wrap:wrap">
      <div class="cqa-tile grow"><div class="cqa-tile__label">Средний балл</div><div class="cqa-tile__value ${totalTone(f.avg_score)}">${num(f.avg_score, 1)}</div></div>
      <div class="cqa-tile grow"><div class="cqa-tile__label">Можно достичь</div><div class="cqa-tile__value cqa-good">${f.potential_score == null ? "—" : num(f.potential_score, 1)}</div></div>
      <div class="cqa-tile grow"><div class="cqa-tile__label">Место в рейтинге</div><div class="cqa-tile__value">${f.place ?? "—"}<span> из ${f.of}</span></div></div>
    </div>
    <div class="cqa-banner" style="white-space:pre-line;line-height:1.6">${esc(f.message)}</div>
    ${conversationsList(f.conversations ?? [])}
    <p class="xs faint" title="Сводка формируется раз в день и сохраняется; рост балла посчитан из выставленных оценок">Сводка на ${esc(f.date.split("-").reverse().join("."))} · обновляется раз в день</p>`;
}

async function feedbackDialog(name) {
  modal({
    title: `Сводка для оператора: ${name}`,
    submitLabel: "Закрыть",
    body: `<div data-role="feedback" class="stack"><div class="cqa-loading">Готовим сводку…</div></div>
      <div><button type="button" class="cqa-btn cqa-btn--sm cqa-btn--ghost" data-role="refresh-feedback">${icon("refresh")} Пересчитать сегодняшнюю сводку</button></div>`,
    onSubmit: async () => true,
  });
  const load = async (refresh) => {
    const target = document.querySelector("[data-role=feedback]");
    if (!target) return;
    target.innerHTML = '<div class="cqa-loading">Готовим сводку…</div>';
    try {
      target.innerHTML = feedbackHtml(await api.operatorFeedback(name, refresh));
    } catch (err) {
      target.innerHTML = "";
      toast(err.message, "error");
    }
  };
  document.querySelector("[data-role=refresh-feedback]")?.addEventListener("click", () => load(true));
  load(false);
}

// --- Чат-наставник ----------------------------------------------------------------------

const QUICK_QUESTIONS = [
  "Почему мне снизили баллы?",
  "Как правильно завершать диалог?",
  "Приведите пример хорошего приветствия",
];

function chatKey(operator) {
  return `cqa.coach.${operator}`;
}

function loadChat(operator) {
  try {
    return JSON.parse(sessionStorage.getItem(chatKey(operator)) || "[]");
  } catch {
    return [];
  }
}

function saveChat(operator, messages) {
  try {
    sessionStorage.setItem(chatKey(operator), JSON.stringify(messages.slice(-30)));
  } catch {
    // переписка просто не сохранится до перезагрузки
  }
}

/** Панель переписки с наставником. История хранится в браузере на время сессии — без лишних вызовов. */
function coachPanel(host, operator) {
  let messages = loadChat(operator);
  host.innerHTML = `
    <section class="cqa-card cqa-dash-card stack" style="gap:12px">
      <h2>Вопрос наставнику</h2>
      <div class="stack" style="gap:8px" data-role="chat"></div>
      <div class="row" style="flex-wrap:wrap;gap:6px" data-role="quick">${QUICK_QUESTIONS.map((q) =>
        `<button type="button" class="cqa-chip" data-quick="${esc(q)}">${esc(q)}</button>`).join("")}</div>
      <form class="row" data-role="ask" style="align-items:flex-end">
        <textarea class="cqa-textarea grow" name="question" rows="2" maxlength="600" placeholder="Например: что я сделал не так в завершении диалога?"></textarea>
        <button class="cqa-btn cqa-btn--primary" type="submit">Спросить</button>
      </form>
      <p class="xs faint" data-role="left"></p>
    </section>`;
  const chat = host.querySelector("[data-role=chat]");
  const form = host.querySelector("[data-role=ask]");
  const draw = () => {
    chat.innerHTML = messages.map((m) => `<div class="cqa-msg ${m.role === "user" ? "cqa-msg--operator" : "cqa-msg--client"}" style="white-space:pre-line">
      <div class="cqa-msg__meta"><span>${m.role === "user" ? "Вы" : "Наставник"}</span></div>${esc(m.text)}</div>`).join("");
    host.querySelector("[data-role=quick]").hidden = messages.length > 0;
  };
  const send = async (question) => {
    if (!question.trim()) return;
    const history = messages.slice();
    messages = [...messages, { role: "user", text: question.trim() }];
    draw();
    chat.insertAdjacentHTML("beforeend", '<div class="small faint" data-role="typing">Наставник думает…</div>');
    form.querySelector("button").disabled = true;
    try {
      const res = await api.coach({ question, history, operator });
      messages = [...messages, { role: "assistant", text: res.answer }];
      host.querySelector("[data-role=left]").textContent = `Осталось вопросов на сегодня: ${res.remaining_today}`;
    } catch (err) {
      messages = messages.slice(0, -1);
      toast(err.message, "error");
    } finally {
      form.querySelector("button").disabled = false;
      saveChat(operator, messages);
      draw();
    }
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = form.question.value;
    form.question.value = "";
    send(question);
  });
  form.question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  host.addEventListener("click", (event) => {
    const quick = event.target.closest("[data-quick]");
    if (quick) send(quick.dataset.quick);
  });
  draw();
}

export async function renderMyFeedback(container) {
  const { user } = session();
  const el = freshRoot(container);
  el.innerHTML = `<div class="cqa-page-head"><div class="grow"><h1>Моя сводка</h1>
      <div class="small faint">${esc(user.name)}</div></div></div><div data-role="feedback" class="stack"><div class="cqa-loading">Готовим сводку…</div></div>
    <div data-role="coach" style="margin-top:20px"></div>`;
  coachPanel(el.querySelector("[data-role=coach]"), user.operator);
  try {
    const f = await api.operatorFeedback(user.operator);
    el.querySelector("[data-role=feedback]").innerHTML = feedbackHtml(f) + (f.weak.length ? `
      <div class="cqa-list">${f.weak.map((w) => `<div class="cqa-list__row" style="cursor:default">
        <span class="grow">${esc(w.title)}</span><span class="small faint">выполняется на ${num(w.compliance, 0)}%</span>
        <span class="cqa-good small" style="width:80px;text-align:right">+${num(w.gain, 1)}</span></div>`).join("")}</div>` : "");
  } catch (err) {
    el.querySelector("[data-role=feedback]").innerHTML = `<div class="cqa-empty"><p>${esc(err.message)}</p></div>`;
  }
}
