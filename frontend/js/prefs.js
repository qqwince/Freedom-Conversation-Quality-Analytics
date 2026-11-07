// Личные настройки зрителя (пользователь, тема, язык) в localStorage. Хранилище может быть
// недоступно (приватный режим, встраивание), поэтому все обращения защищены.

const PREFIX = "cqa.";

export function readPref(key, fallback) {
  try {
    const value = localStorage.getItem(PREFIX + key);
    return value == null ? fallback : value;
  } catch {
    return fallback;
  }
}

export function writePref(key, value) {
  try {
    if (value == null) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, value);
  } catch {
    // настройка просто не сохранится между визитами
  }
}
