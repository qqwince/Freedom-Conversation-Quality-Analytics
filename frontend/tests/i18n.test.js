// Перевод интерфейса: словарь, строки с числами, составные подписи и то, что переводить нельзя.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DICT, RULES } from "../js/i18n-dict.js";
import { translateTo } from "../js/i18n.js";

test("каждая строка словаря переведена на оба языка", () => {
  for (const [ru, tr] of Object.entries(DICT)) {
    assert.equal(tr.length, 2, ru);
    assert.ok(tr[0] && tr[1], `пустой перевод: ${ru}`);
    assert.ok(!/[ЁёЪъЫыЭэ]/.test(tr[1]), `в английском переводе кириллица: ${ru}`);
  }
});

test("у каждого правила есть оба шаблона", () => {
  for (const [pattern, kz, en] of RULES) {
    assert.ok(pattern instanceof RegExp && kz && en, String(pattern));
  }
});

test("точное совпадение сохраняет пробелы вокруг", () => {
  assert.equal(translateTo("  Диалоги ", "en"), "  Conversations ");
  assert.equal(translateTo("Диалоги", "kz"), "Диалогтар");
});

test("строки с числами переводятся правилами", () => {
  assert.equal(translateTo("15 диалогов", "en"), "15 conversations");
  assert.equal(translateTo("1 диалог", "en"), "1 conversation");
  assert.equal(translateTo("1–3 из 8", "en"), "1–3 of 8");
  assert.equal(translateTo("Вес 10 → 15.38 после перераспределения N/A", "en"),
    "Weight 10 → 15.38 after N/A redistribution");
  assert.equal(translateTo("Тема: тёмная", "en"), "Theme: dark");
});

test("составная подпись переводится по частям, имя остаётся", () => {
  assert.equal(translateTo("оператор Алина · Линия RU · Чат · чек-лист v6", "en"),
    "operator Алина · RU line · Chat · checklist v6");
  assert.equal(translateTo("· 15 диалогов", "en"), "· 15 conversations");
});

test("русский, текст без кириллицы и неизвестные строки не трогаются", () => {
  assert.equal(translateTo("Диалоги", "ru"), null);
  assert.equal(translateTo("p1-ru-001", "en"), null);
  assert.equal(translateTo("Клиент хочет узнать тариф", "en"), null);
});
