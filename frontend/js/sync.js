// Пометка «+N новых» в шапке: новые диалоги, которые автообновление загрузило из API
// с момента, когда пользователь последний раз их смотрел.

import { api } from "./api.js";
import { readPref, writePref } from "./prefs.js";

const POLL_MS = 30_000;
let newIds = [];
let listeners = new Set();
let timer = null;

export function newConversationIds() {
  return newIds;
}

export function onNewConversations(callback) {
  listeners.add(callback);
  callback(newIds);
  return () => listeners.delete(callback);
}

/** Пользователь открыл список новых — дальше считаем только то, что придёт после этого момента. */
export function markNewSeen() {
  writePref("sync-seen", new Date().toISOString().slice(0, 19));
}

function since() {
  let seen = readPref("sync-seen", null);
  if (!seen) {
    seen = new Date().toISOString().slice(0, 19);   // первый визит: старые проверки не считаются новыми
    writePref("sync-seen", seen);
  }
  return seen;
}

async function poll() {
  try {
    const status = await api.syncStatus(since());
    newIds = status.new_since ?? [];
  } catch {
    newIds = [];
  }
  listeners.forEach((cb) => cb(newIds));
}

export function startSyncPolling() {
  clearInterval(timer);
  poll();
  timer = setInterval(poll, POLL_MS);
}

export function refreshSync() {
  return poll();
}

export function resetSyncListeners() {
  listeners = new Set();
}
