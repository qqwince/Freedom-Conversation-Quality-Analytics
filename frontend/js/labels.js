// Отображение ответов и баллов: подписи, цвета, допустимые значения по чек-листу.

import { esc, num } from "./dom.js";

export const CHANNEL = { "agent chat": "Чат", "voice call": "Звонок" };
export const SPEAKER = { client: "клиент", operator: "оператор", bot: "бот", system: "система" };

export function maxStep(criterion) {
  return Math.max(...(criterion.steps ?? []).map((s) => s.value));
}

/** Тон ответа: good / bad / part / na. Для флага грубой ошибки «Да» — плохо. */
export function tone(criterion, answer) {
  if (answer == null) return "na";
  if (answer === "na") return "na";
  if (criterion?.type === "flag") return answer === "yes" ? "bad" : "good";
  if (criterion?.type === "scale") {
    const top = maxStep(criterion);
    return answer >= top ? "good" : answer <= 0 ? "bad" : "part";
  }
  return answer === "yes" ? "good" : "bad";
}

export function answerText(criterion, answer) {
  if (answer == null) return "нет ответа";
  if (answer === "na") return "N/A";
  if (criterion?.type === "flag") return answer === "yes" ? "Есть" : "Нет";
  if (criterion?.type === "scale") return `${num(answer)} / ${num(maxStep(criterion))}`;
  return answer === "yes" ? "Да" : "Нет";
}

export function pill(criterion, answer, extra = "") {
  return `<span class="cqa-pill cqa-pill--${tone(criterion, answer)} ${extra}">${esc(answerText(criterion, answer))}</span>`;
}

/** Допустимые ответы для формы решения проверяющего. */
export function allowedAnswers(criterion) {
  const options = criterion.type === "scale"
    ? [...criterion.steps].sort((a, b) => b.value - a.value).map((s) => ({ value: s.value, label: `${num(s.value)} — ${s.text}` }))
    : criterion.type === "flag"
      ? [{ value: "yes", label: "Есть грубая ошибка" }, { value: "no", label: "Нет" }]
      : [{ value: "yes", label: "Да" }, { value: "no", label: "Нет" }];
  if (criterion.na_allowed) options.push({ value: "na", label: "N/A — неприменимо" });
  return options;
}

/** Доказательства критерия; у оценок без них — реплики из подпунктов проверки. */
export function evidenceOf(result) {
  if (result?.evidence?.length) return result.evidence;
  const seen = new Map();
  (result?.checks ?? []).forEach((c) => (c.msg ?? []).forEach((idx) => { if (!seen.has(idx)) seen.set(idx, c.q); }));
  return [...seen].sort((a, b) => a[0] - b[0]).map(([msg_idx, proves]) => ({ msg_idx, proves }));
}

const MISSING_EVIDENCE_NOTE = "нет ни доказательства, ни указанного участка";

/** Замечания проверки без устаревшего «нет доказательств», если доказательства нашлись. */
export function reviewNotes(result) {
  const notes = result?.review_notes ?? [];
  return evidenceOf(result).length ? notes.filter((n) => n !== MISSING_EVIDENCE_NOTE) : notes;
}

export function totalTone(value) {
  if (value == null) return "";
  return value >= 85 ? "cqa-good" : value >= 60 ? "cqa-part" : "cqa-bad";
}

export function sectionTone(points, max) {
  if (points == null || !max) return "faint";
  const share = points / max;
  return share >= 0.999 ? "cqa-good" : share > 0 ? "cqa-part" : "cqa-bad";
}
