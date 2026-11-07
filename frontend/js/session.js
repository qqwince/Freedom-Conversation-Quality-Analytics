// Текущий демо-пользователь и его права. Вход без паролей (так требует ТЗ для демонстрации):
// пользователь выбирается в шапке, сервер применяет права роли и видимость по отделам.

import { readPref, writePref } from "./prefs.js";

let current = { user: null, users: [], departments: [], demo: true, note: "" };

export function setSession(data) {
  current = data;
}

export function session() {
  return current;
}

export function can(permission) {
  return Boolean(current.user?.permissions?.includes(permission));
}

export function storedUserId() {
  return readPref("user", null);
}

export function storeUserId(id) {
  writePref("user", id);
}
