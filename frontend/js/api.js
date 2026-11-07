// Единственная точка обращения к серверу. Адрес API берётся из data-api-base корневого
// элемента — при встраивании в Dynamics 365 его достаточно поменять в разметке.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let base = "";

export function configureApi(apiBase) {
  base = (apiBase || "").replace(/\/$/, "");
}

async function request(method, path, body) {
  const response = await fetch(base + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
  audioUrl: (id) => `${base}/api/conversations/${encodeURIComponent(id)}/audio`,

  checklists: () => request("GET", "/api/checklists"),
  async checklist(id, version) {
    if (version == null) return request("GET", `/api/checklists/${encodeURIComponent(id)}`);
    const key = `${id}@${version}`;
    if (!checklistCache.has(key)) {
      checklistCache.set(key, request("GET", `/api/checklists/${encodeURIComponent(id)}${q({ version })}`));
    }
    return checklistCache.get(key);
  },
  saveChecklist: (checklist) => request("PUT", `/api/checklists/${encodeURIComponent(checklist.id)}`, checklist),

  evaluate: (body) => request("POST", "/api/evaluations", body),
  job: (id) => request("GET", `/api/jobs/${id}`),
  evaluation: (id, includeLog = false) => request("GET", `/api/evaluations/${id}${q({ include_log: includeLog || undefined })}`),
  finalResult: (id) => request("GET", `/api/evaluations/${id}/final`),

  exportUrl: (dataset) => `${base}/api/export${q({ dataset })}`,

  createAppeal: (evaluationId, criterionId, comment) =>
    request("POST", `/api/evaluations/${evaluationId}/appeals`, { criterion_id: criterionId, comment }),
  appeals: (status) => request("GET", "/api/appeals" + q({ status })),
  aiOpinion: (appealId) => request("POST", `/api/appeals/${appealId}/ai-opinion`),
  decide: (appealId, body) => request("POST", `/api/appeals/${appealId}/decision`, body),
};
