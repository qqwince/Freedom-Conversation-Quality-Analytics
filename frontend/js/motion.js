// Сдержанные анимации интерфейса: счётчики цифр и поочерёдное появление элементов.
// Всё отключается, если в системе включено «уменьшение движения» (prefers-reduced-motion).

const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");

export function reducedMotion() {
  return Boolean(media?.matches);
}

const easeOut = (t) => 1 - (1 - t) ** 3;

/**
 * Плавное изменение числа from → to. render(value) рисует промежуточное значение
 * (например, итог «56.88» с мелкими копейками); в конце рисуется точное to.
 */
export function countUp(from, to, render, duration = 700) {
  if (to == null || Number.isNaN(to)) return;
  if (reducedMotion() || from === to) {
    render(to);
    return;
  }
  const start = performance.now();
  let done = false;
  const step = (now) => {
    if (done) return;
    const t = Math.min(1, (now - start) / duration);
    render(t === 1 ? to : from + (to - from) * easeOut(t));
    if (t < 1) requestAnimationFrame(step);
    else done = true;
  };
  requestAnimationFrame(step);
  // В фоновой вкладке браузер не вызывает кадры анимации — число всё равно встанет в конечное значение.
  setTimeout(() => { if (!done) { done = true; render(to); } }, duration + 80);
}

/** Счётчики по разметке: <span data-count="83.4" data-decimals="1"> набегают от нуля. */
export function countAll(root) {
  root.querySelectorAll("span[data-count]").forEach((el) => {
    const to = Number(el.dataset.count);
    const decimals = Number(el.dataset.decimals ?? 0);
    countUp(0, to, (v) => { el.textContent = v.toFixed(decimals); });
  });
}

/** Перезапуск разовой CSS-анимации (подсветка реплики при повторном клике). */
export function replay(node, className) {
  node.classList.remove(className);
  void node.offsetWidth; // принудительный пересчёт, чтобы анимация началась заново
  node.classList.add(className);
}

/** Задержка поочерёдного появления: style="--i: N" используется в CSS. */
export function staggerStyle(index, max = 12) {
  return `--i:${Math.min(index, max)}`;
}
