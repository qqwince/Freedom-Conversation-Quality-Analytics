// Карточка диалога: итог, секции, критерии с доказательствами, диалог (свёрнут), аудио,
// апелляции по критерию, исходная и финальная оценка, журнал вызовов модели.

import { api } from "../api.js";
import { $, $$, esc, formatDate, formatOffset, freshRoot, num, on, plural, total, turn } from "../dom.js";
import { icon } from "../icons.js";
import { CHANNEL, SPEAKER, evidenceOf, pill, reviewNotes, sectionTone, totalTone } from "../labels.js";
import { countUp, replay, staggerStyle } from "../motion.js";
import { can } from "../session.js";
import { toast } from "../ui.js";
import { startEvaluation } from "./conversations.js";

const SUBTYPE = {
  insult: "оскорбление клиента",
  bypass_service: "услуга в обход компании",
  confidential_leak: "раскрытие конфиденциальной информации",
};

export async function renderCard(container, conversationId) {
  const conv = await api.conversation(conversationId);
  const el = freshRoot(container);
  const ctx = { el, conv, mode: "original", openCriterion: null, evaluationId: conv.evaluations[0]?.id ?? null };
  await draw(ctx);

  const route = location.hash;
  const reloadIfStillHere = () => { if (location.hash === route) renderCard(container, conversationId); };
  on(el, "click", "[data-action=back]", () => { location.hash = "#/conversations"; });
  on(el, "click", "[data-action=evaluate]", () => startEvaluation([conv.conversation_id], reloadIfStillHere));
  on(el, "change", "[data-role=history]", (e, sel) => { ctx.evaluationId = sel.value; ctx.mode = "original"; draw(ctx); });
  on(el, "click", "[data-mode]", (e, btn) => { ctx.mode = btn.dataset.mode; draw(ctx); });
  on(el, "click", "[data-crit] > .cqa-list__row", (e, row) => toggleCriterion(ctx, row.parentElement.dataset.crit));
  on(el, "click", "[data-msg]", (e, chip) => { e.stopPropagation(); focusMessages(el, conv, [Number(chip.dataset.msg)], "is-hl"); });
  on(el, "click", "[data-range]", (e, chip) => {
    e.stopPropagation();
    const [from, to] = chip.dataset.range.split("-").map(Number);
    focusMessages(el, conv, Array.from({ length: to - from + 1 }, (_, i) => from + i), "is-range");
  });
  on(el, "click", "[data-action=toggle-dialog]", () => toggleSection(el, "dialog"));
  on(el, "click", "[data-action=toggle-log]", () => toggleLog(ctx));
  on(el, "click", "[data-seek]", (e, btn) => seek(el, Number(btn.dataset.seek)));
  on(el, "click", "[data-action=appeal]", (e, btn) => { e.stopPropagation(); showAppealForm(el, btn.dataset.cid); });
  on(el, "click", "[data-action=appeal-cancel]", (e, btn) => { e.stopPropagation(); hideAppealForm(el, btn.dataset.cid); });
  on(el, "submit", "form[data-appeal]", (e, form) => { e.preventDefault(); submitAppeal(ctx, form); });
}

async function draw(ctx) {
  const { el, conv } = ctx;
  if (!ctx.evaluationId) {
    el.innerHTML = header(conv, null, []) + `
      <div class="cqa-card cqa-empty" style="margin:16px 0">
        <h3>Диалог ещё не оценён</h3>
        <p style="margin-bottom:16px">Модель проверит работу оператора по выбранному чек-листу.</p>
        ${can("evaluate") ? `<button class="cqa-btn cqa-btn--primary" data-action="evaluate">${icon("play")} Оценить</button>` : ""}
      </div>` + dialogSection(conv, true);
    return;
  }
  const evaluation = await api.evaluation(ctx.evaluationId);
  const [checklist, final, checklists] = await Promise.all([
    api.checklist(evaluation.checklist_id, evaluation.checklist_version),
    api.finalResult(ctx.evaluationId),
    api.checklists(),
  ]);
  const latestVersion = checklists.find((c) => c.id === evaluation.checklist_id)?.latest_version;
  Object.assign(ctx, { evaluation, checklist, final });
  const hasDecisions = final.appeals.some((a) => a.status !== "open");
  if (!hasDecisions) ctx.mode = "original";
  const view = ctx.mode === "final" ? finalView(evaluation, final) : originalView(evaluation);
  const wasOpen = $("[data-section=dialog]", el)?.classList.contains("is-open");
  // Критерии появляются по очереди только при первом показе оценки, не при каждом переключении.
  ctx.animateList = ctx.animatedFor !== ctx.evaluationId;
  ctx.animatedFor = ctx.evaluationId;

  el.innerHTML = `
    ${header(conv, evaluation, conv.evaluations, ctx.evaluationId)}
    ${summary(view, final, hasDecisions, ctx.mode)}
    ${criticalBanner(view.critical)}
    ${latestVersion && latestVersion > evaluation.checklist_version ? `
      <div class="cqa-banner" style="margin-top:16px">${icon("history")}
        <div class="grow">Оценено по чек-листу v${evaluation.checklist_version}. Текущая версия — v${latestVersion}: новые и изменённые критерии появятся в этом диалоге после переоценки.</div>
        ${can("evaluate") ? `<button class="cqa-btn cqa-btn--sm" data-action="evaluate">${icon("refresh")} Переоценить</button>` : ""}
      </div>` : ""}
    <div class="cqa-list" style="margin-top:20px">
      ${checklist.criteria
        .slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
        .map((c, i) => criterionHtml(c, view, final, ctx, i))
        .join("")}
    </div>
    ${dialogSection(conv, wasOpen)}
    ${logSection()}`;
  animateTotal(ctx, view.total);
}

/** Итог «набегает» при открытии и плавно перетекает при переключении «Исходная / После апелляции». */
function animateTotal(ctx, value) {
  const hero = $("[data-role=hero]", ctx.el);
  if (!hero || value == null) return;
  const from = ctx.shownTotal ?? 0;
  ctx.shownTotal = value;
  if (from && from !== value) replay(hero, "is-glow");   // итог изменился, а не просто открылся
  countUp(from, value, (v) => {
    const [whole, cents] = v.toFixed(2).split(".");
    hero.innerHTML = `${whole}<small>.${cents}</small>`;
  });
}

// --- Заголовок и итог -------------------------------------------------------------------

function header(conv, evaluation, history, currentId) {
  const meta = [conv.department_title, CHANNEL[conv.channel] ?? conv.channel].filter(Boolean);
  if (conv.operator && conv.operator !== "—") meta.unshift(`оператор ${conv.operator}`);
  if (evaluation) meta.push(`чек-лист v${evaluation.checklist_version}`);
  const historySelect = history.length > 1 ? `
    <select class="cqa-select" style="width:auto" data-role="history" aria-label="История оценок">
      ${history.map((h, i) => `<option value="${esc(h.id)}" ${h.id === currentId ? "selected" : ""}>
        ${i === 0 ? "Последняя" : "Оценка"} · ${esc(formatDate(h.created_at))} · v${h.checklist_version}${h.reevaluated ? " · переоценка" : ""}</option>`).join("")}
    </select>` : "";
  return `
    <div class="cqa-page-head">
      <button class="cqa-icon-btn" data-action="back" aria-label="Назад">${icon("back")}</button>
      <div class="grow">
        <h1>${esc(conv.conversation_id)}</h1>
        <div class="small faint">${meta.map(esc).join(" · ")}</div>
      </div>
      ${historySelect}
      ${evaluation && can("evaluate") ? `<button class="cqa-btn" data-action="evaluate">${icon("refresh")} Переоценить</button>` : ""}
    </div>`;
}

function summary(view, final, hasDecisions, mode) {
  const sections = view.sections.map((s, i) => `
    <div class="cqa-tile cqa-stagger" style="${staggerStyle(i + 1)}">
      <div class="cqa-tile__label">${esc(s.title)}</div>
      <div class="cqa-tile__value ${sectionTone(s.points, s.max)}">${num(s.points, 1)}<span> / ${num(s.max, 1)}</span></div>
    </div>`).join("");
  const segment = hasDecisions ? `
    <div class="cqa-segment" role="tablist">
      <button data-mode="original" class="${mode === "original" ? "is-active" : ""}">Исходная</button>
      <button data-mode="final" class="${mode === "final" ? "is-active" : ""}">После апелляции</button>
    </div>` : "";
  const delta = hasDecisions && final.final.total != null && final.original.total != null
    ? final.final.total - final.original.total : null;
  return `
    <div class="row" style="align-items:flex-end;gap:28px;flex-wrap:wrap">
      <div>
        <div class="cqa-hero ${totalTone(view.total)}" data-role="hero">${view.total == null ? "—" : `${Math.trunc(view.total)}<small>.${total(view.total).split(".")[1]}</small>`}</div>
        <div class="small faint" style="margin-top:6px">${view.total == null ? esc(view.error ?? "итог не рассчитан") : "из 100 баллов"}
          ${mode === "final" && delta ? ` · <span class="${delta > 0 ? "cqa-good" : "cqa-bad"}">${delta > 0 ? "+" : ""}${num(delta)} после апелляции</span>` : ""}</div>
      </div>
      <div class="grow stack" style="gap:10px">
        ${segment ? `<div style="text-align:right">${segment}</div>` : ""}
        <div class="cqa-tiles">${sections}</div>
      </div>
    </div>`;
}

function criticalBanner(critical) {
  if (!critical?.flag) return "";
  const kinds = critical.subtypes?.map((s) => SUBTYPE[s] ?? s).join(", ");
  return `<div class="cqa-banner cqa-banner--bad" style="margin-top:16px">${icon("alert")}
    <div><b style="font-weight:600">Грубая ошибка</b>${kinds ? ` — ${esc(kinds)}` : ""}. Отмечается отдельно и не меняет итог.</div></div>`;
}

// --- Данные для исходного и финального вида ---------------------------------------------

function originalView(ev) {
  return {
    total: ev.total, sections: ev.sections, critical: ev.critical, error: ev.scoring_error,
    answer: (r) => r.answer, points: (r) => ({ points: r.points, max: r.effective_weight }),
  };
}

function finalView(ev, final) {
  const byId = Object.fromEntries((final.final.criteria ?? []).map((c) => [c.criterion_id, c]));
  return {
    total: final.final.total, sections: final.final.sections, critical: final.final.critical, error: final.final.scoring_error,
    answer: (r) => final.final.answers[r.criterion_id],
    points: (r) => ({ points: byId[r.criterion_id]?.points, max: byId[r.criterion_id]?.effective_weight }),
  };
}

// --- Критерии ---------------------------------------------------------------------------

function criterionHtml(criterion, view, final, ctx, index = 0) {
  const result = ctx.evaluation.results.find((r) => r.criterion_id === criterion.id);
  if (!result) return "";
  const answer = view.answer(result);
  const { points, max } = view.points(result);
  const appeals = final.appeals.filter((a) => a.criterion_id === criterion.id);
  const changed = ctx.mode === "final" && final.final.changed.includes(criterion.id);
  const score = criterion.type === "flag" ? `<span>без баллов</span>`
    : answer === "na" ? `<span>не учтён</span>`
    : points == null ? `<span>—</span>`
    : `<b>${num(points)}</b><span> / ${num(max)}</span>`;
  const open = ctx.openCriterion === criterion.id;
  const marks = [
    needsReview(result) ? `<span class="cqa-part" title="Требует проверки">${icon("alert")}</span>` : "",
    appeals.some((a) => a.status === "open") ? `<span class="cqa-pill cqa-pill--part cqa-pill--sm">апелляция</span>` : "",
    changed ? `<span class="cqa-pill cqa-pill--na cqa-pill--sm">изменено</span>` : "",
  ].join(" ");
  return `
    <div data-crit="${esc(criterion.id)}" class="${open ? "is-open" : ""} ${ctx.animateList ? "cqa-stagger" : ""}" style="${staggerStyle(index + 3)}">
      <div class="cqa-list__row ${open ? "is-open" : ""}">
        <span class="grow" style="font-weight:500">${esc(criterion.title)} ${marks}</span>
        ${pill(criterion, answer)}
        <span class="cqa-score">${score}</span>
        <span class="cqa-chevron">${icon("chevron")}</span>
      </div>
      <div class="stack cqa-reveal" style="padding:0 20px 18px;gap:14px" ${open ? "" : "hidden"}>
        ${criterionDetails(criterion, result, appeals, ctx)}
      </div>
    </div>`;
}

function criterionDetails(criterion, r, appeals, ctx) {
  const evidence = evidenceOf(r).map((e) =>
    `<button class="cqa-chip" data-msg="${e.msg_idx}" title="${esc(e.proves)}">${icon("messages")} реплика ${turn(e.msg_idx)}</button>`).join("");
  const range = r.expected_at
    ? `<button class="cqa-chip cqa-chip--range" data-range="${r.expected_at.from_msg_idx}-${r.expected_at.to_msg_idx ?? r.expected_at.from_msg_idx}">
         проверенный участок ${turn(r.expected_at.from_msg_idx)}${r.expected_at.to_msg_idx != null && r.expected_at.to_msg_idx !== r.expected_at.from_msg_idx ? "–" + turn(r.expected_at.to_msg_idx) : ""}</button>` : "";
  const weightNote = criterion.type === "flag" ? "Флаг без баллов"
    : r.answer === "na" ? `Вес ${num(criterion.weight)} распределён между остальными критериями`
    : r.effective_weight != null && Math.abs(r.effective_weight - criterion.weight) > 0.001
      ? `Вес ${num(criterion.weight)} → ${num(r.effective_weight)} после перераспределения N/A` : `Вес ${num(criterion.weight)}`;
  const reasoning = r.checks?.length ? `
    <details class="small"><summary class="faint" style="cursor:pointer">Как модель проверяла</summary>
      <div class="stack" style="gap:6px;margin-top:8px" data-i18n-skip>${r.checks.map((c) => `
        <div><span class="faint">${esc(c.q)}</span><br>${esc(c.a)}${c.msg?.length ? ` <span class="faint">· ${c.msg.map((m) => `[${turn(m)}]`).join(" ")}</span>` : ""}</div>`).join("")}
      </div></details>` : "";
  const pending = reviewNotes(r);
  const notes = pending.length ? `<div class="small cqa-part">${icon("alert")} ${pending.map(esc).join("; ")}</div>` : "";
  const openAppeal = appeals.find((a) => a.status === "open");
  const decided = appeals.filter((a) => a.status !== "open");
  return `
    <p class="muted"${r.explanation ? " data-i18n-skip" : ""}>${esc(r.explanation || "Объяснения нет.")}</p>
    ${r.na_reason && r.answer === "na" ? `<p class="small"><span class="faint">Почему N/A:</span> <span data-i18n-skip>${esc(r.na_reason)}</span></p>` : ""}
    ${evidence || range ? `<div class="row" style="flex-wrap:wrap;gap:6px">${evidence}${range}</div>` : ""}
    ${notes}
    ${reasoning}
    ${decided.map((a) => appealDecision(criterion, a)).join("")}
    <div class="row" style="flex-wrap:wrap">
      <span class="grow xs faint">${esc(weightNote)}${r.carried_from ? " · ответ из предыдущей оценки" : ""}</span>
      ${openAppeal
        ? `<span class="small cqa-part">Апелляция на рассмотрении</span>`
        : can("appeal") ? `<button class="cqa-btn cqa-btn--sm" data-action="appeal" data-cid="${esc(criterion.id)}">${icon("flag")} Оспорить</button>` : ""}
    </div>
    <form data-appeal="${esc(criterion.id)}" class="stack" hidden>
      <textarea class="cqa-textarea" name="comment" rows="3" placeholder="Почему оценка неверна: например, оператор предупредил об ожидании в реплике 7"></textarea>
      <p class="cqa-error" data-role="error" hidden></p>
      <div class="row" style="justify-content:flex-end">
        <button type="button" class="cqa-btn cqa-btn--ghost cqa-btn--sm" data-action="appeal-cancel" data-cid="${esc(criterion.id)}">Отмена</button>
        <button type="submit" class="cqa-btn cqa-btn--primary cqa-btn--sm">Отправить проверяющему</button>
      </div>
    </form>`;
}

function needsReview(result) {
  return result.answer == null || (result.agreement ?? 1) < 1 || reviewNotes(result).length > 0;
}

function appealDecision(criterion, appeal) {
  const changed = appeal.status === "changed";
  return `<div class="cqa-banner small" style="flex-direction:column;gap:4px">
    <div><span class="faint">Апелляция:</span> ${esc(appeal.comment)}</div>
    <div><span class="faint">Решение проверяющего:</span> ${changed ? `оценка изменена на ${pill(criterion, appeal.decision.new_answer, "cqa-pill--sm")}` : "оценка подтверждена"} — ${esc(appeal.decision.reason)}</div>
  </div>`;
}

function toggleCriterion(ctx, cid) {
  const opening = ctx.openCriterion !== cid;
  ctx.openCriterion = opening ? cid : null;
  $$("[data-crit]", ctx.el).forEach((node) => {
    const isTarget = opening && node.dataset.crit === cid;
    node.classList.toggle("is-open", isTarget);
    node.firstElementChild.classList.toggle("is-open", isTarget);
    node.lastElementChild.hidden = !isTarget;
  });
}

// --- Апелляция ---------------------------------------------------------------------------

function showAppealForm(el, cid) {
  const form = $(`form[data-appeal="${CSS.escape(cid)}"]`, el);
  form.hidden = false;
  form.comment.focus();
}

function hideAppealForm(el, cid) {
  $(`form[data-appeal="${CSS.escape(cid)}"]`, el).hidden = true;
}

async function submitAppeal(ctx, form) {
  const error = $("[data-role=error]", form);
  const comment = form.comment.value.trim();
  if (!comment) {
    error.textContent = "Напишите, с чем вы не согласны";
    error.hidden = false;
    return;
  }
  try {
    await api.createAppeal(ctx.evaluationId, form.dataset.appeal, comment);
    toast("Апелляция отправлена проверяющему");
    window.dispatchEvent(new CustomEvent("cqa:appeals-changed"));
    ctx.openCriterion = form.dataset.appeal;
    await draw(ctx);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

// --- Диалог и аудио ----------------------------------------------------------------------

const RECORDING_PAUSED = /приостановлен|paused|тоқтатыл/i;
const RECORDING_RESUMED = /возобновлен|resumed|жалғастырыл|қайта басталды/i;

/**
 * Время реплики внутри аудиофайла. Пока запись стоит на паузе («Запись и транскрибирование
 * приостановлены» … «возобновлены»), время в транскрипте идёт, а в файле — нет, поэтому
 * длительность всех предыдущих пауз вычитается. Реплика внутри паузы ведёт к моменту паузы.
 */
export function audioTimeline(transcript) {
  const result = new Map();
  let paused = 0;
  let pauseStart = null;
  for (const m of transcript) {
    if (m.offset_sec == null) continue;
    if (m.speaker === "system" && RECORDING_PAUSED.test(m.text) && pauseStart == null) pauseStart = m.offset_sec;
    else if (m.speaker === "system" && RECORDING_RESUMED.test(m.text) && pauseStart != null) {
      paused += m.offset_sec - pauseStart;
      pauseStart = null;
    }
    result.set(m.idx, (pauseStart ?? m.offset_sec) - paused);
  }
  return result;
}

/** Повтор распознавания: та же реплика того же участника с тем же временем, что и предыдущая. */
function isAsrDuplicate(m, prev) {
  return prev && prev.speaker === m.speaker && prev.timestamp === m.timestamp && prev.text.trim() === m.text.trim();
}

function dialogSection(conv, open) {
  const isCall = conv.channel === "voice call";
  const audioAt = isCall && conv.has_audio ? audioTimeline(conv.transcript) : new Map();
  const messages = conv.transcript.map((m, i) => {
    if (isAsrDuplicate(m, conv.transcript[i - 1])) {
      return `<div class="cqa-msg cqa-msg--system" id="msg-${m.idx}">${turn(m.idx)} · повтор реплики ${turn(m.idx - 1)} (ошибка распознавания речи)</div>`;
    }
    const time = isCall ? formatOffset(m.offset_sec) : m.timestamp;
    const at = audioAt.get(m.idx);
    return `<div class="cqa-msg cqa-msg--${esc(m.speaker)}" id="msg-${m.idx}">
      <div class="cqa-msg__meta"><span>${turn(m.idx)} · ${esc(SPEAKER[m.speaker] ?? m.speaker)}</span>
        ${at != null ? `<button class="cqa-msg__seek" data-seek="${at}" title="Слушать с этого места">${icon("play")} ${esc(time)}</button>` : `<span>${esc(time)}</span>`}</div>
      ${esc(m.text)}</div>`;
  }).join("");
  return `
    <div class="cqa-list ${open ? "is-open" : ""}" data-section="dialog" style="margin-top:16px">
      <div class="cqa-list__row ${open ? "is-open" : ""}" data-action="toggle-dialog">
        <span class="faint">${icon(isCall ? "phone" : "messages")}</span>
        <span class="grow" style="font-weight:500">${isCall ? "Звонок" : "Диалог"}</span>
        <span class="small faint">${conv.transcript.length} ${plural(conv.transcript.length, "реплика", "реплики", "реплик")}</span>
        <span class="cqa-chevron">${icon("chevron")}</span>
      </div>
      <div data-role="dialog-body" class="stack cqa-reveal" style="padding:4px 20px 20px;gap:6px" ${open ? "" : "hidden"}>
        ${isCall && conv.has_audio ? `<audio controls preload="none" data-role="audio" style="width:100%;margin-bottom:8px" src="${esc(api.audioUrl(conv.conversation_id))}"></audio>
          <p class="xs faint" style="margin:-4px 0 8px">Нажмите на время реплики, чтобы прослушать с этого места.</p>` : ""}
        ${messages}
      </div>
    </div>`;
}

function toggleSection(el, name, force) {
  const section = $(`[data-section=${name}]`, el);
  const open = force ?? !section.classList.contains("is-open");
  section.classList.toggle("is-open", open);
  section.firstElementChild.classList.toggle("is-open", open);
  section.lastElementChild.hidden = !open;
  return section;
}

function focusMessages(el, conv, indexes, cls) {
  toggleSection(el, "dialog", true);
  $$(".cqa-msg", el).forEach((m) => m.classList.remove("is-hl", "is-range", "is-pulse"));
  const nodes = indexes.map((i) => el.querySelector(`#msg-${i}`)).filter(Boolean);
  nodes.forEach((n) => { n.classList.add(cls); replay(n, "is-pulse"); });
  nodes[0]?.scrollIntoView({ behavior: "smooth", block: "center" });
  if (conv.channel === "voice call" && conv.has_audio) {
    const at = audioTimeline(conv.transcript).get(indexes[0]);
    if (at != null) seek(el, at, false);
  }
}

function seek(el, seconds, play = true) {
  const audio = $("[data-role=audio]", el);
  if (!audio) return;
  const apply = () => {
    audio.currentTime = Math.max(0, seconds);
    if (play) audio.play().catch(() => toast("Не удалось воспроизвести запись", "error"));
  };
  if (audio.readyState >= 1) apply();
  else {
    audio.preload = "metadata";
    audio.addEventListener("loadedmetadata", apply, { once: true });
    audio.load();
  }
}

// --- Журнал оценки ----------------------------------------------------------------------

function logSection() {
  return `
    <div class="cqa-list" data-section="log" style="margin-top:12px">
      <div class="cqa-list__row" data-action="toggle-log">
        <span class="faint">${icon("code")}</span>
        <span class="grow" style="font-weight:500">Журнал оценки</span>
        <span class="small faint">промпт и ответы модели</span>
        <span class="cqa-chevron">${icon("chevron")}</span>
      </div>
      <div data-role="log-body" class="stack cqa-reveal" style="padding:4px 20px 20px" hidden></div>
    </div>`;
}

async function toggleLog(ctx) {
  const section = toggleSection(ctx.el, "log");
  const body = $("[data-role=log-body]", section);
  if (!section.classList.contains("is-open") || body.dataset.loaded) return;
  body.innerHTML = '<div class="cqa-loading">Загрузка…</div>';
  const ev = await api.evaluation(ctx.evaluationId, true);
  const u = ev.usage ?? {};
  body.dataset.loaded = "1";
  body.innerHTML = `
    <div class="cqa-tiles">
      <div class="cqa-tile"><div class="cqa-tile__label">Модель</div><div class="small">${esc(ev.model)}</div></div>
      <div class="cqa-tile"><div class="cqa-tile__label">Вызовов</div><div class="small">${u.calls ?? "—"}</div></div>
      <div class="cqa-tile"><div class="cqa-tile__label">Токены вход / выход</div><div class="small">${u.input_tokens ?? "—"} / ${u.output_tokens ?? "—"}</div></div>
      <div class="cqa-tile"><div class="cqa-tile__label">Время модели</div><div class="small">${u.latency_sec != null ? num(u.latency_sec, 1) + " с" : "—"}</div></div>
    </div>
    <p class="xs faint">Оценка ${esc(ev.id)} · промпт ${esc(ev.prompt_version)} · хэш транскрипта ${esc((ev.transcript_sha256 ?? "").slice(0, 12))}…</p>
    ${ev.log ? `
      <details><summary class="small muted" style="cursor:pointer">Системный промпт</summary><pre class="cqa-pre">${esc(ev.log.system_prompt)}</pre></details>
      <details><summary class="small muted" style="cursor:pointer">Запрос: чек-лист, факты и транскрипт</summary><pre class="cqa-pre">${esc(ev.log.user_prompt)}</pre></details>
      ${ev.log.calls.map((c, i) => `
        <details><summary class="small muted" style="cursor:pointer">Ответ модели ${i + 1}${c.attempt ? " · повтор" : ""} · ${esc(c.model)} · ${num(c.latency_sec, 1)} с${c.issues.length ? ` · <span class="cqa-part">замечаний: ${c.issues.length}</span>` : ""}</summary>
          ${c.issues.length ? `<p class="small cqa-part">${c.issues.map(esc).join("<br>")}</p>` : ""}
          <pre class="cqa-pre">${esc(prettyJson(c.response))}</pre></details>`).join("")}` : `<p class="small faint">Журнал не сохранён.</p>`}`;
}

function prettyJson(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
