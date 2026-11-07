// Вспомогательные функции разметки: экранирование, номера реплик, числа, склонение, страницы.

import assert from "node:assert/strict";
import { test } from "node:test";

import { esc, num, paginate, plural, turn } from "../js/dom.js";

test("esc экранирует HTML", () => {
  assert.equal(esc(`<b onclick="x">'&'</b>`), "&lt;b onclick=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/b&gt;");
  assert.equal(esc(null), "");
});

test("реплики в интерфейсе нумеруются с 1", () => {
  assert.equal(turn(0), 1);
  assert.equal(turn(11), 12);
});

test("plural выбирает форму по-русски", () => {
  const f = (n) => plural(n, "диалог", "диалога", "диалогов");
  assert.deepEqual([1, 2, 5, 11, 21, 22, 25, 111].map(f),
    ["диалог", "диалога", "диалогов", "диалогов", "диалог", "диалога", "диалогов", "диалогов"]);
});

test("paginate не выходит за границы", () => {
  const items = Array.from({ length: 7 }, (_, i) => i);
  const last = paginate(items, 99, 3);
  assert.equal(last.page, 2);
  assert.deepEqual(last.items, [6]);
  assert.equal(paginate([], 0, 3).items.length, 0);
});

test("num округляет и не показывает пустое значение", () => {
  assert.equal(num(15.384, 2), "15.38");
  assert.equal(typeof num(null), "string");
});
