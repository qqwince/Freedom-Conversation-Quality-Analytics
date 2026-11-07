// Единственная точка обращения к серверу. Адрес API берётся из data-api-base корневого
// элемента — при встраивании в Dynamics 365 его достаточно поменять в разметке.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let base = "";
let userId = null;

export function configureApi(apiBase) {
  base = (apiBase || "").replace(/\/$/, "");
}

/** Демо-пользователь, от имени которого идут запросы (заголовок X-CQA-User). */
export function setApiUser(id) {
  userId = id;
}

// Версия интерфейса, с которой загрузилась страница. Если сервер сообщает другую — интерфейс
// обновили, пока вкладка была открыта: показываем плашку «обновить страницу».
const loadedBuild = document.querySelector('meta[name="cqa-build"]')?.content ?? null;
let buildNotified = false;

function checkBuild(serverBuild) {
  if (!loadedBuild || !serverBuild || serverBuild === loadedBuild || buildNotified) return;
  buildNotified = true;
  window.dispatchEvent(new CustomEvent("cqa:new-build"));
}

async function request(method, path, body) {
  const headers = body === undefined ? {} : { "Content-Type": "application/json" };
  if (userId) headers["X-CQA-User"] = userId;
  const response = await fetch(base + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  checkBuild(response.headers.get("X-CQA-Build"));
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ")
      : `Ошибка сервера (${response.status})`;
    throw new ApiError(message, response.status);
  }
  return data;
}

const q = (params) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? "?" + new URLSearchParams(entries).toString() : "";
};

const checklistCache = new Map();

export const api = {
  health: () => request("GET", "/api/health"),

  datasets: () => request("GET", "/api/datasets"),
  importGroup: (groupId) => request("POST", "/api/datasets/import", { group_id: groupId }),
  upload: (payload, dataset) => request("POST", "/api/conversations/upload", { payload, dataset }),

  conversations: (filters = {}) => request("GET", "/api/conversations" + q(filters)),
  conversation: (id) => request("GET", `/api/conversations/${encodeURIComponent(id)}`),
  // Для аудиоплеера и скачивания заголовок не передать — пользователь идёт параметром.
  audioUrl: (id) => `${base}/api/conversations/${encodeURIComponent(id)}/audio${q({ as_user: userId })}`,

  checklists: () => request("GET", "/api/checklists"),
  async checklist(id, version) {
    if (version == null) return request("GET", `/api/checklists/${encodeURIComponent(id)}`);
    const key = `${id}@${version}`;
    if (!checklistCache.has(key)) {
      checklistCache.set(key, request("GET", `/api/checklists/${encodeURIComponent(id)}${q({ version })}`));
    }
    return checklistCache.get(key);
  },
  draftCriterion: (checklistId, body) =>
    request("POST", `/api/checklists/${encodeURIComponent(checklistId)}/draft-criterion`, body),
  saveChecklist: (checklist) => request("PUT", `/api/checklists/${encodeURIComponent(checklist.id)}`, checklist),

  evaluate: (body) => request("POST", "/api/evaluations", body),
  job: (id) => request("GET", `/api/jobs/${id}`),
  evaluation: (id, includeLog = false) => request("GET", `/api/evaluations/${id}${q({ include_log: includeLog || undefined })}`),
  finalResult: (id) => request("GET", `/api/evaluations/${id}/final`),

  exportUrl: (dataset) => `${base}/api/export${q({ dataset, as_user: userId })}`,
  me: () => request("GET", "/api/me"),
  syncStatus: (since) => request("GET", "/api/sync" + q({ since })),
  syncUpdate: (body) => request("PUT", "/api/sync", body),
  syncRun: () => request("POST", "/api/sync/run"),
  dashboard: () => request("GET", "/api/dashboard"),
  coach: (body) => request("POST", "/api/coach", body),
  operatorFeedback: (name, refresh = false) =>
    request("GET", `/api/operators/${encodeURIComponent(name)}/feedback${q({ refresh: refresh || undefined })}`),

  createAppeal: (evaluationId, criterionId, comment) =>
    request("POST", `/api/evaluations/${evaluationId}/appeals`, { criterion_id: criterionId, comment }),
  appeals: (status) => request("GET", "/api/appeals" + q({ status })),
  aiOpinion: (appealId) => request("POST", `/api/appeals/${appealId}/ai-opinion`),
  decide: (appealId, body) => request("POST", `/api/appeals/${appealId}/decision`, body),
};
