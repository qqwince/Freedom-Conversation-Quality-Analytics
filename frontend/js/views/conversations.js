// Экран «Диалоги»: список с фильтрами, загрузка наборов, запуск пакетной оценки.

import { api } from "../api.js";
import { $, debounce, esc, formatDate, freshRoot, on, plural, total } from "../dom.js";
import { icon } from "../icons.js";
import { CHANNEL, totalTone } from "../labels.js";
import { modal, toast, trackJob } from "../ui.js";

// Фильтры и выбор переживают переход в карточку и обратно.
const state = { search: "", dataset: "", language: "", channel: "", status: "", selected: new Set() };

const STATUS = {
  "": "Все",
  todo: "Не оценены",
  review: "Требуют проверки",
  critical: "С грубой ошибкой",
};

export async function renderConversations(container) {
  const [rows, datasets] = await Promise.all([api.conversations(), api.datasets()]);
  const el = freshRoot(container);
  const route = location.hash;
  const reload = () => { if (location.hash === route) renderConversations(container); };
  el.innerHTML = `
    <div class="cqa-page-head">
      <div class="grow"><h1>Диалоги</h1></div>
      <button class="cqa-btn" data-action="upload">${icon("upload")} Свой транскрипт</button>
      <button class="cqa-btn" data-action="import">${icon("download")} Загрузить набор</button>
    </div>
    <div class="row" style="flex-wrap:wrap;margin-bottom:16px">
      <div style="position:relative;flex:1 1 220px">
        <span class="faint" style="position:absolute;left:12px;top:9px">${icon("search")}</span>
        <input class="cqa-input" style="padding-left:38px" type="search" placeholder="Поиск по ID" data-filter="search" value="${esc(state.search)}">
      </div>
      ${select("dataset", { "": "Все наборы", ...Object.fromEntries(datasets.map((d) => [d.dataset, d.dataset])) })}
      ${select("language", { "": "Все языки", RU: "RU", KZ: "KZ", EN: "EN" })}
      ${select("channel", { "": "Чаты и звонки", "agent chat": "Чаты", "voice call": "Звонки" })}
      ${select("status", STATUS)}
    </div>
    <div class="cqa-banner" data-role="selection" style="margin-bottom:12px" hidden></div>
    <div data-role="list"></div>`;

  const draw = () => drawList(el, rows);
  draw();

  on(el, "input", "[data-filter=search]", debounce((e, input) => { state.search = input.value.trim(); draw(); }, 150));
  on(el, "change", "select[data-filter]", (e, sel) => { state[sel.dataset.filter] = sel.value; draw(); });
  on(el, "change", "[data-role=pick]", (e, box) => {
    box.checked ? state.selected.add(box.value) : state.selected.delete(box.value);
    drawSelection(el, rows);
  });
  on(el, "change", "[data-role=pick-all]", (e, box) => {
    visible(rows).forEach((r) => (box.checked ? state.selected.add(r.conversation_id) : state.selected.delete(r.conversation_id)));
    draw();
  });
  on(el, "click", "[data-role=pick], [data-role=pick-all], label.cqa-check", (e) => e.stopPropagation());
  on(el, "click", "[data-open]", (e, row) => { location.hash = `#/c/${encodeURIComponent(row.dataset.open)}`; });
  on(el, "click", "[data-action=clear]", () => { state.selected.clear(); draw(); });
  on(el, "click", "[data-action=evaluate]", async () => {
    if (await startEvaluation([...state.selected], reload)) reload();
  });
  on(el, "click", "[data-action=import]", () => importDialog(reload));
  on(el, "click", "[data-action=upload]", () => uploadDialog(reload));
}

function select(name, options) {
  return `<select class="cqa-select" style="flex:0 1 170px" data-filter="${name}">
    ${Object.entries(options).map(([v, l]) => `<option value="${esc(v)}" ${state[name] === v ? "selected" : ""}>${esc(l)}</option>`).join("")}
  </select>`;
}

function visible(rows) {
  const term = state.search.toLowerCase();
  return rows.filter((r) => {
    const ev = r.evaluation;
    if (term && !r.conversation_id.toLowerCase().includes(term)) return false;
    if (state.dataset && r.dataset !== state.dataset) return false;
    if (state.language && r.language !== state.language) return false;
    if (state.channel && r.channel !== state.channel) return false;
    if (state.status === "todo" && ev) return false;
    if (state.status === "review" && !ev?.needs_review) return false;
    if (state.status === "critical" && !ev?.critical) return false;
    return true;
  });
}

function drawList(el, rows) {
  const list = visible(rows);
  const target = $("[data-role=list]", el);
  if (!rows.length) {
    target.innerHTML = `<div class="cqa-list cqa-empty"><h3>Загрузите первый набор</h3>
      <p>Нажмите «Загрузить набор» и укажите group_id из задания, например DEV.</p></div>`;
    drawSelection(el, rows);
    return;
  }
  if (!list.length) {
    target.innerHTML = `<div class="cqa-list cqa-empty"><p>Под фильтры ничего не подходит.</p></div>`;
    drawSelection(el, rows);
    return;
  }
  const allPicked = list.every((r) => state.selected.has(r.conversation_id));
  target.innerHTML = `<div class="cqa-list">
    <div class="cqa-list__row cqa-list__head">
      <label class="cqa-check"><input type="checkbox" data-role="pick-all" ${allPicked ? "checked" : ""} aria-label="Выбрать все"></label>
      <span class="grow">${list.length} ${plural(list.length, "диалог", "диалога", "диалогов")}</span>
      <span class="cqa-hide-sm" style="width:120px">Оценка</span>
      <span style="width:84px;text-align:right">Итог</span>
      <span style="width:18px"></span>
    </div>
    ${list.map(rowHtml).join("")}
  </div>`;
  drawSelection(el, rows);
}

function rowHtml(r) {
  const ev = r.evaluation;
  const flags = [
    ev?.critical ? `<span class="cqa-pill cqa-pill--bad cqa-pill--sm">Грубая ошибка</span>` : "",
    ev?.needs_review ? `<span class="cqa-part" title="Требует проверки">${icon("alert")}</span>` : "",
  ].join("");
  return `<div class="cqa-list__row" data-open="${esc(r.conversation_id)}">
    <label class="cqa-check"><input type="checkbox" data-role="pick" value="${esc(r.conversation_id)}" ${state.selected.has(r.conversation_id) ? "checked" : ""} aria-label="Выбрать ${esc(r.conversation_id)}"></label>
    <span class="faint">${icon(r.channel === "voice call" ? "phone" : "chat")}</span>
    <div class="grow">
      <div style="font-weight:500">${esc(r.conversation_id)}</div>
      <div class="xs faint">${esc(CHANNEL[r.channel] ?? r.channel)} · ${esc(r.language)} · ${r.messages} ${plural(r.messages, "реплика", "реплики", "реплик")}${r.dataset ? " · " + esc(r.dataset) : ""}</div>
    </div>
    <div class="row" style="gap:8px">${flags}</div>
    <span class="cqa-hide-sm xs faint" style="width:120px">${ev ? esc(formatDate(ev.created_at)) + (ev.checklist_version ? ` · v${ev.checklist_version}` : "") : "не оценён"}</span>
    ${finalTotal(ev)}
    <span class="faint" style="transform:rotate(-90deg)">${icon("chevron")}</span>
  </div>`;
}

/** Итог в списке: финальный после апелляции, если оценку меняли, иначе исходный. */
function finalTotal(ev) {
  if (!ev) return `<span style="width:84px"></span>`;
  const appealed = ev.final_total !== undefined;
  const value = appealed ? ev.final_total : ev.total;
  return `<span style="width:84px;text-align:right">
    <span style="font-size:17px;font-weight:600" class="${totalTone(value)}">${total(value)}</span>
    ${appealed ? `<span class="xs faint" style="display:block">было ${total(ev.total)}</span>` : ""}</span>`;
}

function drawSelection(el, rows) {
  const bar = $("[data-role=selection]", el);
  const n = state.selected.size;
  bar.hidden = n === 0;
  if (!n) return;
  const evaluated = rows.filter((r) => state.selected.has(r.conversation_id) && r.evaluation).length;
  bar.innerHTML = `
    <div class="grow row" style="gap:8px"><b style="font-weight:500">Выбрано ${n}</b>
      <span class="faint small">${evaluated ? `уже оценено ${evaluated} — будет новая оценка` : ""}</span></div>
    <button class="cqa-btn cqa-btn--ghost cqa-btn--sm" data-action="clear">Сбросить</button>
    <button class="cqa-btn cqa-btn--primary cqa-btn--sm" data-action="evaluate">${icon("play")} Оценить</button>`;
}

/** Диалог запуска оценки: выбор чек-листа и числа прогонов. Используется и из карточки. */
export async function startEvaluation(conversationIds, onFinish, { criteriaIds } = {}) {
  const checklists = await api.checklists();
  const job = await modal({
    title: criteriaIds ? "Переоценить критерии" : `Оценить ${conversationIds.length} ${plural(conversationIds.length, "диалог", "диалога", "диалогов")}`,
    submitLabel: "Запустить",
    body: `
      <label class="cqa-field"><span>Чек-лист</span>
        <select class="cqa-select" name="checklist">
          ${checklists.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} · v${c.latest_version}</option>`).join("")}
        </select></label>
      <label class="cqa-field"><span>Прогонов модели на диалог</span>
        <select class="cqa-select" name="votes">
          <option value="1">1 — быстро</option>
          <option value="3">3 — голосование, точнее</option>
        </select></label>
      ${criteriaIds ? `<p class="small muted">Модель оценит только: ${criteriaIds.map(esc).join(", ")}. Остальные ответы возьмутся из последней оценки.</p>` : ""}`,
    onSubmit: (form) => api.evaluate({
      conversation_ids: conversationIds,
      checklist_id: form.checklist.value,
      votes: Number(form.votes.value),
      criteria_ids: criteriaIds ?? null,
    }),
  });
  if (job) {
    state.selected.clear();
    toast("Оценка запущена");
    trackJob(job, { onFinish });
  }
  return job;
}

async function importDialog(onDone) {
  const result = await modal({
    title: "Загрузить набор",
    submitLabel: "Загрузить",
    body: `<label class="cqa-field"><span>group_id</span>
      <input class="cqa-input" name="group" placeholder="DEV" required autocomplete="off"></label>
      <p class="small muted">Диалоги придут из API организаторов. Уже загруженные обновятся.</p>`,
    onSubmit: (form) => api.importGroup(form.group.value.trim()),
  });
  if (result) {
    toast(`Загружено ${result.imported} ${plural(result.imported, "диалог", "диалога", "диалогов")} · ${result.dataset}`);
    state.dataset = result.dataset;
    onDone();
  }
}

const EXAMPLE = JSON.stringify({
  conversation_id: "my-001", channel: "agent chat", language: "RU",
  transcript: [
    { timestamp: "00:00", speaker: "client", text: "Где посмотреть тариф?" },
    { timestamp: "00:40", speaker: "operator", text: "Здравствуйте, я Алина. Откройте «Кабинет»…" },
  ],
}, null, 2);

async function uploadDialog(onDone) {
  const result = await modal({
    title: "Свой транскрипт",
    submitLabel: "Загрузить",
    body: `<label class="cqa-field"><span>Файл JSON</span><input class="cqa-input" type="file" accept=".json,application/json" name="file" style="padding-top:7px"></label>
      <label class="cqa-field"><span>или вставьте JSON — запись диалога, список записей или ответ API</span>
      <textarea class="cqa-textarea mono" name="json" rows="9" placeholder="${esc(EXAMPLE)}"></textarea></label>`,
    async onSubmit(form) {
      const text = form.file.files[0] ? await form.file.files[0].text() : form.json.value.trim();
      if (!text) throw new Error("Выберите файл или вставьте JSON");
      let payload;
      try {
        payload = JSON.parse(text);
      } catch {
        throw new Error("Это не похоже на JSON. Проверьте кавычки и запятые.");
      }
      return api.upload(payload, "MANUAL");
    },
  });
  if (result) {
    toast(`Загружено: ${result.conversation_ids?.join(", ") ?? result.imported}`);
    if (result.conversation_ids?.length === 1) location.hash = `#/c/${encodeURIComponent(result.conversation_ids[0])}`;
    else onDone();
  }
}
