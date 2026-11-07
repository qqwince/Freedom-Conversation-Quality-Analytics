// Экран проверяющего: спорная оценка, комментарий, фрагмент диалога, решение с причиной.

import { api } from "../api.js";
import { $, esc, formatDate, freshRoot, num, on, turn } from "../dom.js";
import { icon } from "../icons.js";
import { SPEAKER, allowedAnswers, evidenceOf, pill } from "../labels.js";
import { toast } from "../ui.js";

const state = { tab: "open" };
const CONTEXT = 1;   // сколько соседних реплик показывать вокруг доказательства

export async function renderAppeals(container) {
  const appeals = await api.appeals(state.tab === "open" ? "open" : undefined);
  const list = state.tab === "open" ? appeals : appeals.filter((a) => a.status !== "open");
  const el = freshRoot(container);

  const enriched = await Promise.all(list.map(async (a) => ({
    appeal: a,
    checklist: await api.checklist(a.checklist_id, a.checklist_version),
    conversation: await api.conversation(a.conversation_id),
  })));

  el.innerHTML = `
    <div class="cqa-page-head">
      <div class="grow"><h1>Апелляции</h1>
        <div class="small faint">Проверяющий изучает доказательства и подтверждает или меняет оценку</div></div>
      <div class="cqa-segment">
        <button data-tab="open" class="${state.tab === "open" ? "is-active" : ""}">На рассмотрении</button>
        <button data-tab="decided" class="${state.tab === "decided" ? "is-active" : ""}">Рассмотренные</button>
      </div>
    </div>
    ${enriched.length ? `<div class="stack" style="gap:16px">${enriched.map(appealCard).join("")}</div>` : emptyState()}`;

  on(el, "click", "[data-tab]", (e, btn) => { state.tab = btn.dataset.tab; renderAppeals(container); });
  on(el, "change", "select[name=new_answer]", (e, sel) => {
    $("[data-role=new-answer]", sel.closest("form")).hidden = sel.value === "";
  });
  on(el, "submit", "form[data-decide]", async (e, form) => {
    e.preventDefault();
    if (await decide(form, e.submitter?.value ?? "confirm")) renderAppeals(container);
  });
  on(el, "click", "[data-action=ai]", async (e, btn) => {
    btn.disabled = true;
    btn.innerHTML = `${icon("refresh")} Модель проверяет…`;
    try {
      await api.aiOpinion(btn.dataset.id);
      renderAppeals(container);
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
      btn.innerHTML = `${icon("refresh")} Второе мнение ИИ`;
    }
  });
  on(el, "click", "[data-action=use-ai]", (e, btn) => {
    const item = enriched.find((x) => x.appeal.id === btn.dataset.id);
    const op = item.appeal.ai_opinion;
    const form = el.querySelector(`form[data-decide="${CSS.escape(btn.dataset.id)}"]`);
    const select = form.new_answer;
    const changes = op.answer !== item.appeal.original?.answer;
    select.value = changes ? String(op.answer) : "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    form.reason.value = `По второму мнению ИИ: ${op.explanation}`;
    form.reason.focus();
  });
}

function emptyState() {
  return state.tab === "open"
    ? `<div class="cqa-list cqa-empty"><h3>Спорных оценок нет</h3><p>Апелляции появятся здесь, когда их подадут из карточки диалога.</p></div>`
    : `<div class="cqa-list cqa-empty"><p>Рассмотренных апелляций пока нет.</p></div>`;
}

function appealCard({ appeal, checklist, conversation }) {
  const criterion = checklist.criteria.find((c) => c.id === appeal.criterion_id);
  const result = appeal.original;
  return `
    <article class="cqa-card" style="padding:20px 22px" data-appeal="${esc(appeal.id)}">
      <div class="row" style="align-items:flex-start;margin-bottom:14px">
        <div class="grow">
          <a href="#/c/${encodeURIComponent(appeal.conversation_id)}" class="small muted">${esc(appeal.conversation_id)} ${icon("chevron", "").replace("<svg", '<svg style="transform:rotate(-90deg)"')}</a>
          <h2 style="margin-top:2px">${esc(criterion?.title ?? appeal.criterion_id)}</h2>
        </div>
        <div style="text-align:right">
          ${pill(criterion, result?.answer)}
          <div class="xs faint" style="margin-top:4px">итог оценки ${appeal.evaluation_total == null ? "—" : num(appeal.evaluation_total)}</div>
        </div>
      </div>

      <div class="stack" style="gap:12px">
        <div class="cqa-banner" style="background:var(--cqa-surface-2)">${icon("flag")}
          <div><div class="xs faint">Комментарий · ${esc(formatDate(appeal.created_at))}</div>${esc(appeal.comment)}</div></div>
        <div>
          <div class="xs faint" style="margin-bottom:4px">Обоснование модели</div>
          <p class="muted">${esc(result?.explanation ?? "—")}</p>
        </div>
        ${fragment(conversation, result)}
        ${aiOpinionBlock(appeal, criterion)}
        ${appeal.status === "open" ? decisionForm(appeal, criterion) : decisionSummary(appeal, criterion)}
      </div>
    </article>`;
}

/** Второе мнение модели: подсказка проверяющему. Решение и причину всё равно вводит человек. */
function aiOpinionBlock(appeal, criterion) {
  const op = appeal.ai_opinion;
  if (!op) {
    return appeal.status === "open"
      ? `<div><button class="cqa-btn cqa-btn--sm" data-action="ai" data-id="${esc(appeal.id)}">${icon("refresh")} Второе мнение ИИ</button>
          <span class="xs faint" style="margin-left:8px">модель заново проверит критерий с учётом комментария · ≈ 30 с</span></div>`
      : "";
  }
  const turns = (op.evidence ?? []).map((e) => turn(e.msg_idx));
  const verdict = op.supports_appeal
    ? `<span class="cqa-part">поддерживает апелляцию</span>`
    : `<span class="cqa-good">оставляет исходную оценку</span>`;
  return `
    <div class="cqa-banner" style="flex-direction:column;gap:8px;border-color:var(--cqa-line-strong)">
      <div class="row" style="gap:10px;flex-wrap:wrap">
        <span class="faint">${icon("refresh")}</span>
        <b style="font-weight:600">Второе мнение ИИ</b>
        ${pill(criterion, op.answer, "cqa-pill--sm")}
        <span class="small">${verdict}</span>
        <span class="grow"></span>
        <span class="xs faint">${esc(op.model)} · ${esc(formatDate(op.created_at))}</span>
      </div>
      <p class="muted small">${esc(op.explanation)}${turns.length ? ` <span class="faint">· реплики ${turns.join(", ")}</span>` : ""}</p>
      ${appeal.status === "open" ? `<div class="row" style="gap:8px">
        <button class="cqa-btn cqa-btn--sm" data-action="use-ai" data-id="${esc(appeal.id)}">Подставить в решение</button>
        <span class="xs faint">Это подсказка: решение принимает проверяющий.</span></div>` : ""}
    </div>`;
}

/** Фрагмент диалога: реплики-доказательства и проверенный участок с соседями. */
function fragment(conversation, result) {
  if (!result) return "";
  const focus = new Set(evidenceOf(result).map((e) => e.msg_idx));
  const range = result.expected_at;
  if (range) for (let i = range.from_msg_idx; i <= (range.to_msg_idx ?? range.from_msg_idx); i++) focus.add(i);
  if (!focus.size) return "";
  const show = new Set();
  focus.forEach((i) => { for (let d = -CONTEXT; d <= CONTEXT; d++) show.add(i + d); });
  const messages = conversation.transcript.filter((m) => show.has(m.idx));
  let previous = null;
  const html = messages.map((m) => {
    const gap = previous != null && m.idx - previous > 1 ? `<div class="xs faint" style="text-align:center">···</div>` : "";
    previous = m.idx;
    const cls = focus.has(m.idx) ? (range && m.idx >= range.from_msg_idx && m.idx <= (range.to_msg_idx ?? range.from_msg_idx) ? "is-range" : "is-hl") : "";
    return `${gap}<div class="cqa-msg cqa-msg--${esc(m.speaker)} ${cls}">
      <div class="cqa-msg__meta"><span>${turn(m.idx)} · ${esc(SPEAKER[m.speaker] ?? m.speaker)}</span></div>${esc(m.text)}</div>`;
  }).join("");
  return `<div><div class="xs faint" style="margin-bottom:6px">Фрагмент диалога</div><div class="stack" style="gap:6px">${html}</div></div>`;
}

function decisionForm(appeal, criterion) {
  const options = allowedAnswers(criterion).filter((o) => o.value !== appeal.original?.answer);
  return `
    <form data-decide="${esc(appeal.id)}" class="stack" style="gap:10px;border-top:1px solid var(--cqa-line);padding-top:14px">
      <label class="cqa-field"><span>Причина решения</span>
        <textarea class="cqa-textarea" name="reason" rows="2" required placeholder="Например: предупреждение об ожидании есть в реплике 7"></textarea></label>
      <div class="row" style="flex-wrap:wrap">
        <label class="cqa-field" style="flex:1 1 240px"><span>Новая оценка (если меняете)</span>
          <select class="cqa-select" name="new_answer">
            <option value="">— оставить как есть —</option>
            ${options.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}
          </select></label>
        <div class="row" style="align-self:flex-end">
          <button class="cqa-btn" type="submit" name="action" value="confirm">${icon("check")} Подтвердить оценку</button>
          <button class="cqa-btn cqa-btn--primary" type="submit" name="action" value="change" data-role="new-answer" hidden>Изменить оценку</button>
        </div>
      </div>
      <p class="cqa-error" data-role="error" hidden></p>
    </form>`;
}

function decisionSummary(appeal, criterion) {
  const changed = appeal.status === "changed";
  return `<div class="cqa-banner ${changed ? "" : ""}" style="border-color:var(--cqa-line-strong)">${icon(changed ? "scale" : "check")}
    <div><div class="xs faint">Решение · ${esc(formatDate(appeal.decided_at))}</div>
      ${changed ? `Оценка изменена на ${pill(criterion, appeal.decision.new_answer, "cqa-pill--sm")}` : "Оценка подтверждена"} — ${esc(appeal.decision.reason)}</div></div>`;
}

async function decide(form, action) {
  const error = $("[data-role=error]", form);
  const reason = form.reason.value.trim();
  const raw = form.new_answer.value;
  error.hidden = true;
  if (!reason) {
    error.textContent = "Укажите причину решения";
    error.hidden = false;
    return false;
  }
  const newAnswer = raw === "" ? null : Number.isNaN(Number(raw)) ? raw : Number(raw);
  try {
    const result = await api.decide(form.dataset.decide, { action, reason, new_answer: action === "change" ? newAnswer : null });
    window.dispatchEvent(new CustomEvent("cqa:appeals-changed"));
    const final = await api.finalResult(result.evaluation_id);
    const verdict = action === "change" ? "Оценка изменена" : "Оценка подтверждена";
    toast(`${verdict}. Итог: ${final.original.total == null ? "—" : num(final.original.total)} → ${final.final.total == null ? "—" : num(final.final.total)}`);
    return true;
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    return false;
  }
}
