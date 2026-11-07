// Общие элементы интерфейса: уведомления, модальные окна, отслеживание фоновых задач.

import { api } from "./api.js";
import { esc } from "./dom.js";

let host = document.body;

export function setUiHost(element) {
  host = element;
}

export function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `cqa-toast ${kind === "error" ? "cqa-toast--error" : ""}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3500);
}

/**
 * Модальное окно. body — HTML (уже экранированный), onSubmit(form) → Promise; бросает Error с текстом для показа.
 * Возвращает Promise, который разрешается результатом onSubmit или null при отмене.
 */
export function modal({ title, body, submitLabel = "Готово", onSubmit }) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "cqa-modal-backdrop";
    backdrop.innerHTML = `
      <form class="cqa-modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <h2>${esc(title)}</h2>
        ${body}
        <p class="cqa-error" data-role="error" hidden></p>
        <div class="row" style="justify-content:flex-end">
          <button type="button" class="cqa-btn cqa-btn--ghost" data-role="cancel">Отмена</button>
          <button type="submit" class="cqa-btn cqa-btn--primary">${esc(submitLabel)}</button>
        </div>
      </form>`;
    const form = backdrop.querySelector("form");
    const error = backdrop.querySelector("[data-role=error]");
    const close = (value) => {
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (event) => event.key === "Escape" && close(null);
    document.addEventListener("keydown", onKey);
    backdrop.addEventListener("mousedown", (event) => event.target === backdrop && close(null));
    backdrop.querySelector("[data-role=cancel]").addEventListener("click", () => close(null));
    // ссылки внутри окна (например, на карточку диалога) закрывают окно
    backdrop.addEventListener("click", (event) => { if (event.target.closest("[data-close-modal]")) close(null); });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("[type=submit]");
      button.disabled = true;
      error.hidden = true;
      try {
        close(await onSubmit(form));
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        button.disabled = false;
      }
    });
    host.appendChild(backdrop);
    form.querySelector("input, textarea, select")?.focus();
  });
}

// --- Фоновые задачи оценки -------------------------------------------------------------

const listeners = new Set();
const running = new Map();

/** Подписка на изменения задач: callback(jobs) — список активных и только что завершённых. */
export function onJobs(callback) {
  listeners.add(callback);
  callback([...running.values()]);
  return () => listeners.delete(callback);
}

export function trackJob(job, { onFinish } = {}) {
  running.set(job.id, job);
  emit();
  const tick = async () => {
    try {
      const fresh = await api.job(job.id);
      running.set(job.id, fresh);
      emit();
      if (fresh.status === "finished") {
        setTimeout(() => { running.delete(job.id); emit(); }, 4000);
        const failed = fresh.items.filter((i) => i.status === "failed");
        if (failed.length) toast(`Оценка завершена, ошибок: ${failed.length}. ${failed[0].error}`, "error");
        else toast(`Оценка завершена: ${fresh.done} из ${fresh.total}`);
        onFinish?.(fresh);
        return;
      }
    } catch (err) {
      toast(err.message, "error");
    }
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 800);
}

function emit() {
  const jobs = [...running.values()];
  listeners.forEach((cb) => cb(jobs));
}
