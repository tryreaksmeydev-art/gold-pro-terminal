export const $ = (id) => document.getElementById(id);

export function escHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (m) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]
  ));
}

export function safeUrl(url) {
  const s = String(url || '').trim();
  return /^https?:\/\//i.test(s) ? escHtml(s) : '';
}

export function toNum(val) {
  if (val === null || val === undefined) return null;
  if (typeof val === 'string' && !val.trim()) return null;
  const n = Number(val);
  return Number.isFinite(n) ? n : null;
}

export function formatBold(text) {
  return escHtml(text).replace(/\*\*(.*?)\*\*/g, '<b class="text-yellow-300 font-semibold">$1</b>');
}

/** តម្រៀប និងលុប timestamp ជាន់គ្នា ការពារ Lightweight Charts Crash */
export function sanitizeCandles(arr) {
  if (!Array.isArray(arr)) return [];
  const sorted = arr
    .filter((c) => c && toNum(c.time) !== null && toNum(c.open) !== null && toNum(c.close) !== null)
    .sort((a, b) => Number(a.time) - Number(b.time));
  const out = [];
  let prevTime = -1;
  for (const c of sorted) {
    const t = Number(c.time);
    if (t <= prevTime) continue;
    prevTime = t;
    out.push({
      time: t,
      open: Number(c.open),
      high: Number(c.high),
      low: Number(c.low),
      close: Number(c.close),
    });
  }
  return out;
}

export function sanitizeLineData(arr) {
  if (!Array.isArray(arr)) return [];
  const sorted = arr
    .filter((p) => p && toNum(p.time) !== null && toNum(p.value) !== null)
    .sort((a, b) => Number(a.time) - Number(b.time));
  const out = [];
  let prevTime = -1;
  for (const p of sorted) {
    const t = Number(p.time);
    if (t <= prevTime) continue;
    prevTime = t;
    out.push({ time: t, value: Number(p.value) });
  }
  return out;
}

let sharedAudioCtx = null;
export function playAlertChime(mode = 'win') {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!sharedAudioCtx) sharedAudioCtx = new AudioCtx();
    if (sharedAudioCtx.state === 'suspended') sharedAudioCtx.resume();
    const now = sharedAudioCtx.currentTime;
    const freqs = mode === 'warn' ? [520, 390] : [880, 1174.66];
    freqs.forEach((f, i) => {
      const osc = sharedAudioCtx.createOscillator();
      const gain = sharedAudioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.value = f;
      gain.gain.setValueAtTime(0.16, now + i * 0.16);
      gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.16 + 0.35);
      osc.connect(gain);
      gain.connect(sharedAudioCtx.destination);
      osc.start(now + i * 0.16);
      osc.stop(now + i * 0.16 + 0.35);
    });
  } catch {}
}

export class WakeLockManager {
  constructor(btnId = 'wakeLockBtn', textId = 'wakeLockText') {
    this.btnId = btnId;
    this.textId = textId;
    this.sentinel = null;
    this.enabled = false;
  }

  isSupported() {
    return typeof navigator !== 'undefined' && 'wakeLock' in navigator;
  }

  updateUI(active) {
    const btn = $(this.btnId);
    const txt = $(this.textId);
    if (!btn) return;
    btn.className = active
      ? 'flex items-center gap-1 text-[9px] sm:text-[10px] px-1.5 py-0.5 rounded-lg bg-emerald-500/25 text-emerald-300 border border-emerald-400/50 font-bold active:scale-95 transition shrink-0 whitespace-nowrap'
      : 'flex items-center gap-1 text-[9px] sm:text-[10px] px-1.5 py-0.5 rounded-lg bg-amber-400/15 text-amber-300 border border-amber-400/35 font-bold active:scale-95 transition shrink-0 whitespace-nowrap';
    if (txt) txt.textContent = active ? 'On' : 'Stay On';
  }

  async acquire() {
    if (!this.isSupported()) {
      this.updateUI(false);
      return false;
    }
    try {
      this.sentinel = await navigator.wakeLock.request('screen');
      this.sentinel.addEventListener('release', () => {
        this.sentinel = null;
        if (!this.enabled) this.updateUI(false);
      });
      this.updateUI(true);
      return true;
    } catch {
      this.updateUI(false);
      return false;
    }
  }

  async release() {
    if (this.sentinel) {
      try { await this.sentinel.release(); } catch {}
      this.sentinel = null;
    }
    this.updateUI(false);
  }

  async setEnabled(flag) {
    this.enabled = Boolean(flag);
    if (this.enabled) return this.acquire();
    await this.release();
    return false;
  }
}