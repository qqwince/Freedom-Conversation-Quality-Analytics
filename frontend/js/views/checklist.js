// Редактор чек-листа: критерии, веса, типы, шкалы, N/A, подпункты проверки.
// Сохранение создаёт новую версию; изменённые критерии можно сразу применить к диалогам.

import { api } from "../api.js";
import { $, esc, freshRoot, num, on, plural } from "../dom.js";
import { icon } from "../icons.js";
import { modal, toast, trackJob } from "../ui.js";

const TYPE = { binary: "Да / Нет", scale: "Шкала", flag: "Флаг грубой ошибки" };

export async function renderChecklist(container, { checklistId, version } = {}) {
  const list = await api.checklists();
  if (!list.length) {
    freshRoot(container).innerHTML = `<div class="cqa-empty"><h3>Чек-листов нет</h3></div>`;
    return;
  }
  const meta = list.find((c) => c.id === checklistId) ?? list[0];
  const loaded = await api.checklist(meta.id, version ?? meta.latest_version);
  const ctx = {
    container, meta,
    base: loaded,
    draft: structuredClone(loaded),
    open: null,
    dirty: false,
    lastSave: null,
  };
  ctx.el = freshRoot(container);
  draw(ctx);
  bind(ctx);
}

function draw(ctx) {
  const { draft, meta } = ctx;
  const weighted = draft.criteria.filter((c) => c.type !== "flag");
  const sum = weighted.reduce((s, c) => s + (Number(c.weight) || 0), 0);
  ctx.el.innerHTML = `
    <div class="cqa-page-head">
      <div class="grow">
        <h1>Чек-лист</h1>
        <div class="small faint">${esc(draft.name)}</div>
      </div>
      <select class="cqa-select" style="width:auto" data-role="version" aria-label="Версия">
        ${meta.versions.slice().reverse().map((v) => `<option value="${v.version}" ${v.version === ctx.base.version ? "selected" : ""}>Версия ${v.version}${v.version === meta.latest_version ? " · текущая" : ""}</option>`).join("")}
      </select>
      <button class="cqa-btn cqa-btn--primary" data-action="save" ${ctx.dirty ? "" : "disabled"}>${icon("check")} Сохранить версию</button>
    </div>
    ${ctx.lastSave ? savedBanner(ctx.lastSave) : ""}
    <div class="cqa-banner" data-role="errors" style="margin-bottom:16px" hidden></div>
    <div class="cqa-tiles" style="margin-bottom:20px">
      <div class="cqa-tile"><div class="cqa-tile__label">Критериев</div><div class="cqa-tile__value">${draft.criteria.length}</div></div>
      <div class="cqa-tile"><div class="cqa-tile__label">Сумма весов</div><div class="cqa-tile__value ${sum === 100 ? "cqa-good" : "cqa-part"}">${num(sum)}<span> / 100</span></div></div>
      <div class="cqa-tile"><div class="cqa-tile__label">Округление итога</div><div class="cqa-tile__value">${draft.scoring?.rounding_digits ?? 2}<span> знака</span></div></div>
    </div>
    ${sum !== 100 ? `<p class="small cqa-part" style="margin:-8px 0 16px">${icon("alert")} Итог всё равно приводится к 100 баллам, но веса удобнее держать в сумме 100.</p>` : ""}
    <div class="cqa-list">
      ${draft.criteria.slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map((c) => criterionRow(ctx, c)).join("")}
    </div>
    <div style="margin-top:12px"><button class="cqa-btn" data-action="add">${icon("plus")} Добавить критерий</button></div>`;
}

function criterionRow(ctx, c) {
  const open = ctx.open === c.id;
  return `
    <div data-crit="${esc(c.id)}" class="${open ? "is-open" : ""}">
      <div class="cqa-list__row ${open ? "is-open" : ""}" data-action="toggle">
        <span class="grow"><span style="font-weight:500">${esc(c.title || "Без названия")}</span>
          <span class="xs faint">· ${esc(c.id)}</span></span>
        <span class="small faint cqa-hide-sm">${esc(TYPE[c.type] ?? c.type)}${c.type === "scale" ? " " + scaleRange(c) : ""}</span>
        ${c.na_allowed ? `<span class="cqa-pill cqa-pill--na cqa-pill--sm">N/A</span>` : ""}
        <span class="cqa-score">${c.type === "flag" ? "<span>без баллов</span>" : `<b>${num(Number(c.weight) || 0)}</b><span> вес</span>`}</span>
        <span class="cqa-chevron">${icon("chevron")}</span>
      </div>
      ${open ? `<div style="padding:4px 20px 20px">${criterionForm(ctx, c)}</div>` : ""}
    </div>`;
}

function scaleRange(c) {
  const values = (c.steps ?? []).map((s) => s.value);
  return values.length ? `${num(Math.min(...values))}–${num(Math.max(...values))}` : "";
}

function criterionForm(ctx, c) {
  const sections = ctx.draft.sections ?? [];
  const steps = (c.steps ?? []).slice().sort((a, b) => b.value - a.value);
  return `
    <div class="stack" data-form="${esc(c.id)}">
      <div class="row" style="flex-wrap:wrap;align-items:flex-start">
        <label class="cqa-field" style="flex:2 1 260px"><span>Название</span>
          <input class="cqa-input" data-field="title" value="${esc(c.title)}"></label>
        <label class="cqa-field" style="flex:1 1 180px"><span>Секция</span>
          <select class="cqa-select" data-field="section">${sections.map((s) => `<option value="${esc(s.id)}" ${s.id === c.section ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select></label>
      </div>
      <div class="row" style="flex-wrap:wrap;align-items:flex-start">
        <label class="cqa-field" style="flex:1 1 180px"><span>Тип ответа</span>
          <select class="cqa-select" data-field="type">${Object.entries(TYPE).map(([v, l]) => `<option value="${v}" ${v === c.type ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="cqa-field" style="flex:0 1 120px"><span>Вес</span>
          <input class="cqa-input" type="number" min="0" step="1" data-field="weight" value="${esc(c.weight)}" ${c.type === "flag" ? "disabled" : ""}></label>
        <label class="cqa-check" style="flex:1 1 180px;margin-top:30px"><input type="checkbox" data-field="na_allowed" ${c.na_allowed ? "checked" : ""}> Разрешить N/A</label>
      </div>
      <label class="cqa-field"><span>Формулировка критерия</span>
        <textarea class="cqa-textarea" rows="2" data-field="description">${esc(c.description)}</textarea></label>
      ${c.na_allowed ? `<label class="cqa-field"><span>Когда ставить N/A</span>
        <textarea class="cqa-textarea" rows="2" data-field="na_rule">${esc(c.na_rule ?? "")}</textarea></label>` : ""}
      ${c.type === "scale" ? `
        <div class="cqa-field"><span>Шкала — верхняя ступень даёт полный вес</span>
          <div class="stack" style="gap:6px">
            ${steps.map((s, i) => `<div class="row" data-step="${i}">
              <input class="cqa-input" style="width:90px" type="number" min="0" step="1" data-step-field="value" value="${esc(s.value)}" aria-label="Значение">
              <input class="cqa-input grow" data-step-field="text" value="${esc(s.text)}" placeholder="Когда ставить это значение" aria-label="Описание ступени">
              <button class="cqa-icon-btn" data-action="remove-step" data-index="${i}" aria-label="Удалить ступень">${icon("x")}</button>
            </div>`).join("")}
            <div><button class="cqa-btn cqa-btn--sm" data-action="add-step">${icon("plus")} Ступень</button></div>
          </div></div>` : ""}
      <details ${c.checks?.length ? "" : "open"}>
        <summary class="small muted" style="cursor:pointer">Подпункты проверки для модели · ${(c.checks ?? []).length}</summary>
        <div class="stack" style="margin-top:10px">
          <label class="cqa-field"><span>По одному вопросу в строке — модель ответит на каждый перед итоговым ответом</span>
            <textarea class="cqa-textarea" rows="4" data-field="checks">${esc((c.checks ?? []).join("\n"))}</textarea></label>
          <label class="cqa-field"><span>Правило решения</span>
            <textarea class="cqa-textarea" rows="2" data-field="decision">${esc(c.decision ?? "")}</textarea></label>
        </div>
      </details>
      <div class="row"><span class="grow"></span>
        <button class="cqa-btn cqa-btn--ghost cqa-btn--danger cqa-btn--sm" data-action="remove">${icon("trash")} Удалить критерий</button></div>
    </div>`;
}

function savedBanner(save) {
  const parts = [];
  if (save.added.length) parts.push(`добавлены ${save.added.join(", ")}`);
  if (save.changed.length) parts.push(`изменены ${save.changed.join(", ")}${save.needsModel.length < save.changed.length ? " (вес и название — без вызова модели)" : ""}`);
  if (save.removed.length) parts.push(`удалены ${save.removed.join(", ")}`);
  return `<div class="cqa-banner" style="margin-bottom:16px;border-color:var(--cqa-green)">
    <span class="cqa-good">${icon("check")}</span>
    <div class="grow">Сохранена версия ${save.version}${parts.length ? ": " + esc(parts.join("; ")) : ""}.</div>
    <button class="cqa-btn cqa-btn--primary cqa-btn--sm" data-action="apply">${icon("play")} Применить к диалогам</button></div>`;
}

// --- События ---------------------------------------------------------------------------

function bind(ctx) {
  const { el } = ctx;
  const criterion = (node) => ctx.draft.criteria.find((c) => c.id === node.closest("[data-crit]").dataset.crit);
  const touch = (redraw = false) => {
    ctx.dirty = true;
    if (redraw) draw(ctx);
    else $("[data-action=save]", el).disabled = false;
  };

  on(el, "click", "[data-action=toggle]", (e, row) => {
    const id = row.parentElement.dataset.crit;
    ctx.open = ctx.open === id ? null : id;
    draw(ctx);
  });
  on(el, "change", "[data-role=version]", (e, sel) => {
    if (ctx.dirty && !confirm("Несохранённые изменения пропадут. Открыть другую версию?")) {
      sel.value = ctx.base.version;
      return;
    }
    renderChecklist(ctx.container, { checklistId: ctx.meta.id, version: Number(sel.value) });
  });
  on(el, "input", "[data-field]", (e, input) => {
    const c = criterion(input);
    const field = input.dataset.field;
    if (field === "checks") c.checks = input.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (field === "weight") c.weight = input.value === "" ? 0 : Number(input.value);
    else if (field !== "na_allowed" && field !== "type") c[field] = input.value;
    touch();
  });
  on(el, "change", "[data-field=type], [data-field=na_allowed], [data-field=weight], [data-field=title]", (e, input) => {
    const c = criterion(input);
    if (input.dataset.field === "type") changeType(c, input.value);
    if (input.dataset.field === "na_allowed") c.na_allowed = input.checked;
    touch(true);
  });
  on(el, "input", "[data-step-field]", (e, input) => {
    const c = criterion(input);
    const steps = c.steps.slice().sort((a, b) => b.value - a.value);
    const step = steps[Number(input.closest("[data-step]").dataset.step)];
    step[input.dataset.stepField] = input.dataset.stepField === "value" ? Number(input.value) : input.value;
    touch();
  });
  on(el, "click", "[data-action=add-step]", (e, btn) => {
    const c = criterion(btn);
    const min = Math.min(...c.steps.map((s) => s.value), 1);
    c.steps.push({ value: Math.max(0, min - 1), text: "" });
    touch(true);
  });
  on(el, "click", "[data-action=remove-step]", (e, btn) => {
    const c = criterion(btn);
    const sorted = c.steps.slice().sort((a, b) => b.value - a.value);
    c.steps = sorted.filter((_, i) => i !== Number(btn.dataset.index));
    touch(true);
  });
  on(el, "click", "[data-action=remove]", (e, btn) => {
    const c = criterion(btn);
    if (!confirm(`Удалить критерий «${c.title}»?`)) return;
    ctx.draft.criteria = ctx.draft.criteria.filter((x) => x !== c);
    ctx.open = null;
    touch(true);
  });
  on(el, "click", "[data-action=add]", () => {
    const c = newCriterion(ctx.draft);
    ctx.draft.criteria.push(c);
    ctx.open = c.id;
    touch(true);
    el.querySelector(`[data-crit="${CSS.escape(c.id)}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  on(el, "click", "[data-action=save]", () => save(ctx));
  on(el, "click", "[data-action=apply]", () => applyDialog(ctx));
}

function changeType(c, type) {
  c.type = type;
  if (type === "scale" && !(c.steps?.length >= 2)) {
    c.steps = [{ value: 10, text: "Полностью выполнено" }, { value: 5, text: "Частично" }, { value: 0, text: "Не выполнено" }];
  }
  if (type !== "scale") delete c.steps;
  if (type === "flag") {
    c.weight = 0;
    c.na_allowed = false;
    c.is_critical_flag = true;
  } else {
    c.is_critical_flag = false;
  }
}

function newCriterion(draft) {
  const used = new Set(draft.criteria.map((c) => c.id));
  let n = draft.criteria.length + 1;
  while (used.has(`C${n}`)) n++;
  const section = (draft.sections ?? []).find((s) => !draft.criteria.some((c) => c.section === s.id && c.type === "flag"))
    ?? draft.sections?.[0];
  return {
    id: `C${n}`, section: section?.id, order: Math.max(0, ...draft.criteria.map((c) => c.order ?? 0)) + 1,
    title: "Новый критерий", type: "binary", weight: 10, na_allowed: false, na_rule: null,
    description: "", checks: [], decision: "", validators: [], examples: [], is_critical_flag: false,
  };
}

// --- Сохранение и применение -----------------------------------------------------------

// Поля, от которых зависит ответ модели. Вес, название, секция и порядок влияют только на расчёт итога.
const MODEL_FIELDS = ["type", "description", "na_allowed", "na_rule", "steps", "subtypes", "checks", "decision",
  "examples", "validators", "wait_threshold_seconds"];

function diff(before, after) {
  const prev = new Map(before.criteria.map((c) => [c.id, c]));
  const next = new Map(after.criteria.map((c) => [c.id, c]));
  const same = (a, b, fields) => fields.every((f) => JSON.stringify(a[f] ?? null) === JSON.stringify(b[f] ?? null));
  const common = [...next.keys()].filter((id) => prev.has(id));
  const changed = common.filter((id) => JSON.stringify(prev.get(id)) !== JSON.stringify(next.get(id)));
  return {
    added: [...next.keys()].filter((id) => !prev.has(id)),
    changed,
    needsModel: changed.filter((id) => !same(prev.get(id), next.get(id), MODEL_FIELDS)),
    removed: [...prev.keys()].filter((id) => !next.has(id)),
  };
}

async function save(ctx) {
  const errors = $("[data-role=errors]", ctx.el);
  const { saved_at: _, version: __, ...payload } = ctx.draft;
  try {
    const saved = await api.saveChecklist(payload);
    const changes = diff(ctx.base, saved);
    toast(`Сохранена версия ${saved.version}`);
    const meta = (await api.checklists()).find((c) => c.id === saved.id);
    Object.assign(ctx, { meta, base: saved, draft: structuredClone(saved), dirty: false,
      lastSave: { version: saved.version, ...changes } });
    draw(ctx);
  } catch (err) {
    errors.innerHTML = `<span class="cqa-bad">${icon("alert")}</span><div>${esc(err.message).replaceAll("; ", "<br>")}</div>`;
    errors.hidden = false;
    errors.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function applyDialog(ctx) {
  const save = ctx.lastSave;
  const toEvaluate = [...save.added, ...save.needsModel];
  const datasets = await api.datasets();
  const all = await api.conversations();
  const evaluatedIn = (dataset) => all.filter((r) => r.dataset === dataset && r.evaluation).length;
  const job = await modal({
    title: `Применить версию ${save.version}`,
    submitLabel: "Запустить оценку",
    body: `
      <label class="cqa-field"><span>Диалоги</span>
        <select class="cqa-select" name="dataset">
          ${datasets.map((d) => `<option value="${esc(d.dataset)}">${esc(d.dataset)} · ${d.n} ${plural(d.n, "диалог", "диалога", "диалогов")}, оценено ${evaluatedIn(d.dataset)}</option>`).join("")}
        </select></label>
      <label class="cqa-field"><span>Что оценить</span>
        <select class="cqa-select" name="mode">
          <option value="changed">${toEvaluate.length ? `Только изменённые критерии: ${esc(toEvaluate.join(", "))}` : "Только пересчитать итог (без модели)"}</option>
          <option value="full">Все критерии заново</option>
        </select></label>
      <p class="small muted">Остальные ответы возьмутся из последней оценки каждого диалога, итог пересчитается по новой версии. Диалоги без оценки будут оценены полностью.</p>`,
    async onSubmit(form) {
      const rows = await api.conversations({ dataset: form.dataset.value });
      if (!rows.length) throw new Error("В наборе нет диалогов");
      return api.evaluate({
        conversation_ids: rows.map((r) => r.conversation_id),
        checklist_id: ctx.base.id,
        checklist_version: save.version,
        criteria_ids: form.mode.value === "changed" ? toEvaluate : null,
      });
    },
  });
  if (job) {
    toast(`Оценка запущена: ${job.total} ${plural(job.total, "диалог", "диалога", "диалогов")}`);
    trackJob(job);
  }
}
