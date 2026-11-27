export function makeDraggable(el, container, options = {}) {
  if (!el || !container) return { clamp: () => {} };

  const {
    ignoreSelector = 'button, input, select, textarea, a, [data-no-drag]',
    rightMargin = 68,
    bottomMargin = 24,
    padding = 6,
  } = options;

  let dragging = false;
  let startX = 0;
  let startY = 0;
  let origL = padding;
  let origT = padding;

  const clamp = () => {
    const sw = container.clientWidth;
    const sh = container.clientHeight;
    const ew = el.offsetWidth;
    const eh = el.offsetHeight;
    if (!sw || !sh || !ew || !eh) return;

    // បើប្រអប់កំពុងប្រើ right positioning ហើយអេក្រង់តូច -> ប្តូរមក left ឱ្យស្មើស្អាត
    if (el.style.right && el.style.right !== 'auto' && !el.style.left) {
      const computedL = el.offsetLeft;
      if (computedL < padding) {
        el.style.right = 'auto';
        el.style.left = `${padding}px`;
      }
      return;
    }

    const curL = parseFloat(el.style.left);
    const curT = parseFloat(el.style.top);
    const maxL = Math.max(padding, sw - ew - rightMargin);
    const maxT = Math.max(padding, sh - eh - bottomMargin);

    if (Number.isFinite(curL)) {
      el.style.left = `${Math.max(padding, Math.min(curL, maxL))}px`;
    }
    if (Number.isFinite(curT)) {
      el.style.top = `${Math.max(padding, Math.min(curT, maxT))}px`;
    }
  };

  el.addEventListener('pointerdown', (e) => {
    if (ignoreSelector && e.target.closest(ignoreSelector)) return;
    dragging = true;
    el.classList.add('is-dragging');
    try { el.setPointerCapture(e.pointerId); } catch {}
    startX = e.clientX;
    startY = e.clientY;
    origL = el.offsetLeft || padding;
    origT = el.offsetTop || padding;
    el.style.right = 'auto';
    el.style.left = `${origL}px`;
    el.style.top = `${origT}px`;
  });

  el.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const maxL = Math.max(padding, container.clientWidth - el.offsetWidth - rightMargin);
    const maxT = Math.max(padding, container.clientHeight - el.offsetHeight - bottomMargin);
    el.style.left = `${Math.max(padding, Math.min(origL + (e.clientX - startX), maxL))}px`;
    el.style.top = `${Math.max(padding, Math.min(origT + (e.clientY - startY), maxT))}px`;
  });

  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove('is-dragging');
  };

  el.addEventListener('pointerup', endDrag);
  el.addEventListener('pointercancel', endDrag);

  return { clamp };
}

export function makeResizable(el, handleEl, container, options = {}) {
  if (!el || !handleEl || !container) return;

  const { minWidth = 240, maxWidth = 420 } = options;
  let resizing = false;
  let startX = 0;
  let origW = 292;

  handleEl.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    resizing = true;
    startX = e.clientX;
    origW = el.offsetWidth || minWidth;
    try { handleEl.setPointerCapture(e.pointerId); } catch {}
  });

  handleEl.addEventListener('pointermove', (e) => {
    if (!resizing) return;
    const limitW = Math.min(maxWidth, Math.max(minWidth, container.clientWidth - 20));
    const nextW = Math.max(minWidth, Math.min(limitW, origW + (e.clientX - startX)));
    el.style.width = `${nextW}px`;
  });

  const endResize = () => { resizing = false; };
  handleEl.addEventListener('pointerup', endResize);
  handleEl.addEventListener('pointercancel', endResize);
}