import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { $, escHtml, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';
import { makeResizable } from './draggable.js';

const POLL_MS = 5000;

class ExpertAnalystModal {
  constructor() {
    this.isOpen = false;
    this.isFullscreen = false;
    this.isFetching = false;
    this.lastReport = null;
    this.lastPrice = null;
    this.prevPrice = null;
    this.lastBid = null;
    this.lastAsk = null;
    this.lastSpread = null;
    this.lastChange = 0;
    this.lastChangePct = 0;
    this.sparkPrices = [];
    this.auditLot = 0.01;

    this.pollTimer = null;
    this.countdownTimer = null;
    this.winEl = null;
    this.bodyEl = null;
    this.savedRect = null;
  }

  init(prefs = {}) {
    this.auditLot = Number(prefs.auditLot) || 0.01;
    this._injectTriggerButton();
    this._injectFloatingWindow();
    this._bindEvents();
  }

  _injectTriggerButton() {
    if ($('btnExpertAnalyst')) return;

    const btn = document.createElement('button');
    btn.id = 'btnExpertAnalyst';
    btn.type = 'button';
    btn.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl bg-gradient-to-r from-amber-400 via-yellow-300 to-amber-500 text-[#070b13] text-[10px] sm:text-[11px] font-black shadow-[0_4px_18px_rgba(250,204,21,.4)] hover:brightness-110 active:scale-95 transition shrink-0';
    btn.innerHTML = `<span>🧠</span><span>អ្នកជំនាញវិភាគកម្រិតខ្ពស់ (5m)</span>`;

    const radarChk = $('chkScanner');
    const host = radarChk?.closest('label')?.parentElement || $('btnFullscreen')?.parentElement;
    if (host) {
      host.appendChild(btn);
    } else {
      btn.classList.add('fixed', 'bottom-4', 'right-4', 'z-[90]');
      document.body.appendChild(btn);
    }

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.isOpen) this.close();
      else this.open();
    });
  }

  _injectFloatingWindow() {
    $('expertAnalystBackdrop')?.remove();
    $('expertAnalystWindow')?.remove();

    const stage = $('chartStage') || document.body;
    const win = document.createElement('div');
    win.id = 'expertAnalystWindow';
    win.className = 'hidden absolute top-3 right-3 z-[38] w-[min(370px,calc(100%-16px))] flex flex-col rounded-2xl bg-[#070d1b]/95 backdrop-blur-2xl border border-amber-400/45 shadow-[0_24px_70px_rgba(0,0,0,.82),0_0_28px_rgba(250,204,21,.12)] overflow-hidden transition-shadow';
    win.style.maxHeight = 'calc(100% - 16px)';

    win.innerHTML = `
      <!-- Sticky Draggable Header -->
      <div id="eaDragHeader" class="shrink-0 flex items-center justify-between gap-2 px-3 py-2 bg-gradient-to-r from-[#131f3b] via-[#0c152b] to-[#131f3b] border-b border-amber-400/20 cursor-grab active:cursor-grabbing select-none" style="touch-action:none;">
        <div class="flex items-center gap-2 min-w-0">
          <div class="w-6 h-6 rounded-lg bg-gradient-to-br from-amber-300 to-amber-500 text-[#070b13] flex items-center justify-center text-xs font-black shadow-[0_0_12px_rgba(250,204,21,.45)] shrink-0">🧠</div>
          <div class="min-w-0">
            <div class="text-[11px] sm:text-xs font-black bg-gradient-to-r from-amber-200 via-yellow-300 to-amber-400 bg-clip-text text-transparent truncate">អ្នកជំនាញវិភាគកម្រិតខ្ពស់</div>
            <div class="text-[8.5px] text-cyan-300/80 font-bold tracking-wide truncate">5m Institutional • Self-Learning AI</div>
          </div>
        </div>
        <div class="flex items-center gap-1 shrink-0 tabular-nums">
          <span id="eaCandleTimer" class="px-1.5 py-0.5 rounded-md bg-sky-500/20 border border-sky-400/40 text-[9.5px] font-black text-sky-300">5m --:--</span>
          <button id="eaBtnRefresh" type="button" title="វិភាគឡើងវិញ" class="w-6 h-6 rounded-lg bg-white/8 hover:bg-white/18 text-gray-200 text-[11px] font-black flex items-center justify-center active:scale-95 transition">↻</button>
          <button id="eaBtnFullscreen" type="button" title="មើលពេញអេក្រង់" class="px-1.5 h-6 rounded-lg bg-amber-400/15 hover:bg-amber-400/30 border border-amber-400/40 text-amber-200 text-[9.5px] font-black flex items-center gap-1 active:scale-95 transition">
            <span id="eaFsIcon">⛶</span><span id="eaFsTxt" class="hidden sm:inline">ពេញ</span>
          </button>
          <button id="eaBtnClose" type="button" title="បិទ" class="w-6 h-6 rounded-lg bg-rose-500/20 hover:bg-rose-500/35 border border-rose-400/40 text-rose-200 text-[10px] font-black flex items-center justify-center active:scale-95 transition">✕</button>
        </div>
      </div>

      <!-- Live Price & Mini Chart Telemetry Strip -->
      <div class="shrink-0 px-3 py-2 bg-[#050a14]/90 border-b border-white/10 flex items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="flex items-baseline gap-1.5 tabular-nums">
            <span id="eaLiveBigPrice" class="text-base sm:text-lg font-black text-white tracking-tight">$--.--</span>
            <span id="eaLiveChange" class="text-[9.5px] font-bold text-gray-400">--</span>
          </div>
          <div id="eaLiveBidAsk" class="text-[8.5px] text-gray-400 tabular-nums truncate">Bid -- • Ask -- • Spd --</div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <svg id="eaMiniSpark" viewBox="0 0 110 30" class="w-[96px] sm:w-[110px] h-[28px] overflow-visible">
            <defs>
              <linearGradient id="eaSparkGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#facc15" stop-opacity="0.35"/>
                <stop offset="100%" stop-color="#facc15" stop-opacity="0"/>
              </linearGradient>
            </defs>
            <path id="eaSparkArea" d="" fill="url(#eaSparkGrad)"></path>
            <path id="eaSparkLine" d="" fill="none" stroke="#facc15" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path>
            <circle id="eaSparkDot" cx="0" cy="0" r="2.3" fill="#facc15" class="hidden"></circle>
          </svg>
        </div>
      </div>

      <!-- Scrollable Content Body -->
      <div id="eaBody" class="flex-1 min-h-0 overflow-y-auto p-2.5 sm:p-3 space-y-2.5 text-[10.5px]" style="-webkit-overflow-scrolling:touch; overscroll-behavior:contain; touch-action:pan-y;">
        <div id="eaGridWrap" class="space-y-2.5">
          <!-- Column 1 (Verdict + Plan + 5-Rule Guidance) -->
          <div class="space-y-2.5">
            <div id="eaVerdictCard" class="rounded-xl border border-amber-400/35 bg-gradient-to-b from-white/[.04] to-black/40 p-2.5 space-y-1.5 text-center">
              <div class="flex items-center justify-between text-[9px] tabular-nums">
                <span id="eaGradeBadge" class="px-2 py-0.5 rounded-md bg-amber-400/20 text-amber-300 font-black border border-amber-400/35">5m AI</span>
                <span id="eaLivePnlBadge" class="font-black text-sky-300">READY</span>
              </div>
              <div id="eaVerdictTitle" class="text-xs sm:text-sm font-black text-amber-300 leading-snug">⏳ កំពុងវិភាគទិន្នន័យស្ថាប័ន 5m...</div>
              <div id="eaVerdictSub" class="text-[10px] text-gray-200 leading-snug">កំពុងផ្ទៀងផ្ទាត់ Premium/Discount, OB, VWAP, SMT និងខួរក្បាលរៀនពីកំហុស</div>
              <div class="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
                <div id="eaConfBar" class="h-full bg-gradient-to-r from-amber-400 to-emerald-400 transition-all duration-300" style="width:50%"></div>
              </div>
              <div id="eaPrepBanner" class="hidden rounded-lg bg-amber-500/20 border border-amber-400/50 px-2 py-1 text-[9.5px] font-black text-amber-200"></div>
            </div>

            <!-- One-Click Copy Execution Plan (MT5) -->
            <div class="rounded-xl bg-[#050b18] border border-white/10 p-2.5 space-y-2">
              <div class="flex items-center justify-between text-[9.5px]">
                <span class="font-black text-cyan-300">ផែនការចូល & ការពារ (ចុច Copy ចូល MT5)</span>
                <span id="eaRrBadge" class="font-black text-amber-300 tabular-nums">R:R --</span>
              </div>
              <div id="eaCopyGrid" class="grid grid-cols-2 gap-1.5 tabular-nums">
                <button type="button" data-ea-copy="entry" class="rounded-lg bg-amber-400/12 hover:bg-amber-400/22 border border-amber-400/35 p-1.5 text-left active:scale-95 transition">
                  <div class="text-[8px] text-amber-200/80 font-bold">📋 ENTRY (ចូល)</div>
                  <div id="eaValEntry" class="text-xs font-black text-amber-300">--</div>
                </button>
                <button type="button" data-ea-copy="sl" class="rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-400/40 p-1.5 text-left active:scale-95 transition">
                  <div class="text-[8px] text-rose-200/80 font-bold">📋 STOP LOSS (ឈប់)</div>
                  <div id="eaValSl" class="text-xs font-black text-rose-300">--</div>
                </button>
                <button type="button" data-ea-copy="be" class="rounded-lg bg-sky-500/15 hover:bg-sky-500/25 border border-sky-400/40 p-1.5 text-left active:scale-95 transition">
                  <div class="text-[8px] text-sky-200/80 font-bold">📋 រំកិល BE (ការពារ)</div>
                  <div id="eaValBe" class="text-xs font-black text-sky-300">--</div>
                </button>
                <button type="button" data-ea-copy="tp1" class="rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-400/40 p-1.5 text-left active:scale-95 transition">
                  <div class="text-[8px] text-emerald-200/80 font-bold">📋 TP1 (កាត់ចំណេញ)</div>
                  <div id="eaValTp1" class="text-xs font-black text-emerald-300">--</div>
                </button>
              </div>
              <div class="flex items-center justify-between text-[9px] text-gray-300 pt-1 border-t border-white/10 tabular-nums">
                <span>Trail៖ <b id="eaValTrail" class="text-amber-300">--</b></span>
                <span>TP2៖ <b id="eaValTp2" class="text-emerald-300">--</b></span>
                <span id="eaProjUsd" class="text-cyan-200 font-bold">--</span>
              </div>
            </div>

            <!-- 5-Rule Clear Guidance -->
            <div class="space-y-1.5">
              <div class="flex items-start gap-2 rounded-xl bg-white/[.035] border border-white/10 p-2">
                <span class="w-5 h-5 rounded-lg bg-sky-500/20 border border-sky-400/40 text-sky-300 flex items-center justify-center font-black shrink-0">↗</span>
                <div id="eaRowTrend" class="text-gray-100 font-semibold leading-snug">--</div>
              </div>
              <div class="flex items-start gap-2 rounded-xl bg-emerald-500/10 border border-emerald-400/35 p-2">
                <span class="w-5 h-5 rounded-lg bg-emerald-500/25 border border-emerald-400/50 text-emerald-300 flex items-center justify-center font-black shrink-0">◎</span>
                <div id="eaRowEntry" class="text-emerald-100 font-bold leading-snug">--</div>
              </div>
              <div class="flex items-start gap-2 rounded-xl bg-rose-500/10 border border-rose-400/35 p-2">
                <span class="w-5 h-5 rounded-lg bg-rose-500/25 border border-rose-400/50 text-rose-300 flex items-center justify-center font-black shrink-0">⊘</span>
                <div id="eaRowAvoid" class="text-rose-100 font-semibold leading-snug">--</div>
              </div>
              <div class="flex items-start gap-2 rounded-xl bg-amber-500/12 border border-amber-400/45 p-2">
                <span class="w-5 h-5 rounded-lg bg-amber-500/25 border border-amber-400/55 text-amber-200 flex items-center justify-center font-black shrink-0">🛡️</span>
                <div id="eaRowProfitStop" class="text-amber-100 font-bold leading-snug">--</div>
              </div>
              <div class="flex items-start gap-2 rounded-xl bg-rose-950/60 border border-rose-500/50 p-2">
                <span class="w-5 h-5 rounded-lg bg-rose-500/30 border border-rose-400/60 text-rose-200 flex items-center justify-center font-black shrink-0">!</span>
                <div id="eaRowLossStop" class="text-rose-100 font-bold leading-snug">--</div>
              </div>
            </div>
          </div>

          <!-- Column 2 (Institutional Matrix + Self-Learning Brain) -->
          <div class="space-y-2.5">
            <div class="rounded-xl bg-[#050b18] border border-cyan-400/25 p-2.5 space-y-2">
              <div class="flex items-center justify-between text-[9.5px]">
                <span class="font-black text-cyan-300">ទិន្នន័យស្ថាប័នកម្រិត Pro (8 កត្តា)</span>
                <span id="eaSweepBadge" class="text-[8.5px] font-bold text-gray-300 truncate max-w-[55%]">--</span>
              </div>
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-1.5 text-center text-[8.5px] tabular-nums">
                <div class="rounded-lg bg-black/50 p-1.5"><div class="text-gray-400">តំបន់ P/D</div><div id="eaMatPd" class="font-black text-amber-300">--</div></div>
                <div class="rounded-lg bg-black/50 p-1.5"><div class="text-gray-400">VWAP / POC</div><div id="eaMatVwap" class="font-black text-purple-300">--</div></div>
                <div class="rounded-lg bg-black/50 p-1.5"><div class="text-gray-400">CVD លុយធំ</div><div id="eaMatCvd" class="font-black text-sky-300">--</div></div>
                <div class="rounded-lg bg-black/50 p-1.5"><div class="text-gray-400">SMT / MTF</div><div id="eaMatSmt" class="font-black text-emerald-300">--</div></div>
              </div>
              <div id="eaReasonsList" class="space-y-1 text-[9.5px] text-gray-200"></div>
            </div>

            <!-- Self-Learning Brain -->
            <div class="rounded-xl bg-gradient-to-br from-[#0f1a33] to-[#070e1f] border border-violet-400/35 p-2.5 space-y-2">
              <div class="flex items-center justify-between gap-2">
                <span class="font-black text-violet-300 text-[10px]">🧬 ខួរក្បាលរៀនពីកំហុស & កែលម្អខ្លួនឯង</span>
                <span id="eaBrainIq" class="px-2 py-0.5 rounded-md bg-violet-500/25 border border-violet-400/45 text-[9px] font-black text-violet-200 tabular-nums">IQ --</span>
              </div>
              <div id="eaPatternStatus" class="rounded-lg bg-white/[.04] border border-white/10 px-2 py-1 text-[9.5px] font-bold text-gray-100">--</div>
              <div class="grid grid-cols-4 gap-1 text-center text-[8.5px] tabular-nums">
                <div class="rounded bg-black/45 p-1"><div class="text-gray-400">ឈ្នះ/ខាត</div><div id="eaMemWl" class="font-black text-emerald-300">0 / 0</div></div>
                <div class="rounded bg-black/45 p-1"><div class="text-gray-400">អត្រាឈ្នះ</div><div id="eaMemWr" class="font-black text-amber-300">--</div></div>
                <div class="rounded bg-black/45 p-1"><div class="text-gray-400">ប្លុកកំហុស</div><div id="eaMemBlocked" class="font-black text-rose-300">0</div></div>
                <div class="rounded bg-black/45 p-1"><div class="text-gray-400">ទម្រង់រៀន</div><div id="eaMemPats" class="font-black text-cyan-300">0</div></div>
              </div>
              <div id="eaLessonsList" class="space-y-1 text-[9px]"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Resize Handle (Bottom-Right) -->
      <div id="eaResizeHandle" title="ទាញពង្រីកទំហំ" class="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize flex items-end justify-end p-0.5 text-amber-300/60 hover:text-amber-300 select-none">
        <svg class="w-2.5 h-2.5" viewBox="0 0 10 10" fill="currentColor"><path d="M9 1v8H1l8-8z"/></svg>
      </div>
    `;

    stage.appendChild(win);
    this.winEl = win;
    this.bodyEl = $('eaBody');

    this._initSmartDrag(win, stage);
    makeResizable(win, $('eaResizeHandle'), stage, { minWidth: 280, maxWidth: 680 });

    $('eaBtnClose')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.close();
    });
    $('eaBtnRefresh')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._fetchReport();
    });
    $('eaBtnFullscreen')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleFullscreen();
    });
    $('eaCopyGrid')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-ea-copy]');
      if (!btn) return;
      e.stopPropagation();
      this._copyField(btn.dataset.eaCopy, btn);
    });
  }

  _initSmartDrag(el, stage) {
    let tracking = false;
    let dragging = false;
    let pointerId = null;
    let fromBody = false;
    let isTouch = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    const beginDrag = (e) => {
      if (this.isFullscreen) return;
      const stageRect = stage.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      startLeft = elRect.left - stageRect.left;
      startTop = elRect.top - stageRect.top;
      el.style.right = 'auto';
      el.style.bottom = 'auto';
      el.style.left = `${Math.round(startLeft)}px`;
      el.style.top = `${Math.round(startTop)}px`;
      dragging = true;
      try { el.setPointerCapture(e.pointerId); } catch {}
    };

    el.addEventListener('pointerdown', (e) => {
      if (this.isFullscreen) return;
      if (e.button !== undefined && e.button !== 0) return;
      if (e.target.closest('button, input, a, #eaResizeHandle')) return;

      const inBody = Boolean(e.target.closest('#eaBody'));
      if (inBody && e.pointerType === 'mouse' && this.bodyEl) {
        const sbRect = this.bodyEl.getBoundingClientRect();
        if (e.clientX >= sbRect.right - 12) return;
      }

      tracking = true;
      dragging = false;
      pointerId = e.pointerId;
      fromBody = inBody;
      isTouch = e.pointerType === 'touch' || e.pointerType === 'pen';
      startX = e.clientX;
      startY = e.clientY;

      if (!fromBody) beginDrag(e);
    });

    el.addEventListener('pointermove', (e) => {
      if (!tracking || e.pointerId !== pointerId || this.isFullscreen) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      if (!dragging) {
        if (Math.hypot(dx, dy) < 6) return;
        if (fromBody && isTouch && Math.abs(dy) >= Math.abs(dx) * 1.1) {
          tracking = false;
          return;
        }
        beginDrag(e);
      }

      if (dragging) {
        const maxL = Math.max(4, stage.clientWidth - el.offsetWidth - 4);
        const maxT = Math.max(4, stage.clientHeight - el.offsetHeight - 4);
        el.style.left = `${Math.round(Math.max(4, Math.min(maxL, startLeft + dx)))}px`;
        el.style.top = `${Math.round(Math.max(4, Math.min(maxT, startTop + dy)))}px`;
      }
    });

    const endDrag = (e) => {
      if (e.pointerId !== pointerId) return;
      if (dragging) {
        try { el.releasePointerCapture(e.pointerId); } catch {}
      }
      tracking = false;
      dragging = false;
      pointerId = null;
    };

    el.addEventListener('pointerup', endDrag);
    el.addEventListener('pointercancel', endDrag);
  }

  _clampPosition() {
    if (!this.winEl || !this.isOpen || this.isFullscreen) return;
    const stage = $('chartStage');
    if (!stage) return;
    const stageRect = stage.getBoundingClientRect();
    const elRect = this.winEl.getBoundingClientRect();
    if (!elRect.width || !stageRect.width) return;

    this.winEl.style.maxHeight = `${Math.max(220, stage.clientHeight - 16)}px`;

    const curL = elRect.left - stageRect.left;
    const curT = elRect.top - stageRect.top;
    const maxL = Math.max(4, stage.clientWidth - this.winEl.offsetWidth - 4);
    const maxT = Math.max(4, stage.clientHeight - this.winEl.offsetHeight - 4);

    if (curL < 4 || curL > maxL || curT < 4 || curT > maxT) {
      this.winEl.style.right = 'auto';
      this.winEl.style.bottom = 'auto';
      this.winEl.style.left = `${Math.round(Math.max(4, Math.min(maxL, curL)))}px`;
      this.winEl.style.top = `${Math.round(Math.max(4, Math.min(maxT, curT)))}px`;
    }
  }

  toggleFullscreen(force = null) {
    if (!this.winEl) return;
    const next = force !== null ? Boolean(force) : !this.isFullscreen;
    if (next === this.isFullscreen) return;
    this.isFullscreen = next;

    const stage = $('chartStage') || document.body;
    const grid = $('eaGridWrap');
    const resizeEl = $('eaResizeHandle');
    const fsIcon = $('eaFsIcon');
    const fsTxt = $('eaFsTxt');

    if (this.isFullscreen) {
      this.savedRect = {
        left: this.winEl.style.left,
        top: this.winEl.style.top,
        right: this.winEl.style.right,
        width: this.winEl.style.width,
        maxHeight: this.winEl.style.maxHeight,
      };
      document.body.appendChild(this.winEl);
      this.winEl.className = 'fixed inset-0 z-[160] w-screen h-[100dvh] flex flex-col bg-[#060b16] border-0 rounded-none overflow-hidden';
      this.winEl.style.left = '';
      this.winEl.style.top = '';
      this.winEl.style.right = '';
      this.winEl.style.width = '';
      this.winEl.style.maxHeight = '100dvh';
      if (grid) grid.className = 'max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-3';
      resizeEl?.classList.add('hidden');
      if (fsIcon) fsIcon.textContent = '❐';
      if (fsTxt) fsTxt.textContent = 'បង្រួម';
    } else {
      stage.appendChild(this.winEl);
      this.winEl.className = 'absolute top-3 right-3 z-[38] w-[min(370px,calc(100%-16px))] flex flex-col rounded-2xl bg-[#070d1b]/95 backdrop-blur-2xl border border-amber-400/45 shadow-[0_24px_70px_rgba(0,0,0,.82),0_0_28px_rgba(250,204,21,.12)] overflow-hidden transition-shadow';
      if (this.savedRect) {
        this.winEl.style.left = this.savedRect.left || '';
        this.winEl.style.top = this.savedRect.top || '';
        this.winEl.style.right = this.savedRect.right || '';
        this.winEl.style.width = this.savedRect.width || '';
        this.winEl.style.maxHeight = this.savedRect.maxHeight || 'calc(100% - 16px)';
      }
      if (grid) grid.className = 'space-y-2.5';
      resizeEl?.classList.remove('hidden');
      if (fsIcon) fsIcon.textContent = '⛶';
      if (fsTxt) fsTxt.textContent = 'ពេញ';
      requestAnimationFrame(() => this._clampPosition());
    }
  }

  _bindEvents() {
    bus.on('market:full', ({ data }) => {
      if (Array.isArray(data?.candles) && data.candles.length) {
        this.sparkPrices = data.candles.slice(-28).map((c) => Number(c.close)).filter((v) => Number.isFinite(v) && v > 0);
        if (this.isOpen) this._drawMiniSparkline();
      }
    });

    bus.on('price:updated', ({ price, change, changePct, bid, ask, spread }) => {
      if (!Number.isFinite(price) || price <= 0) return;
      this.prevPrice = this.lastPrice;
      this.lastPrice = price;
      if (toNum(bid) !== null) this.lastBid = Number(bid);
      if (toNum(ask) !== null) this.lastAsk = Number(ask);
      if (toNum(spread) !== null) this.lastSpread = Number(spread);
      if (toNum(change) !== null) this.lastChange = Number(change);
      if (toNum(changePct) !== null) this.lastChangePct = Number(changePct);

      if (!this.sparkPrices.length || Math.abs(this.sparkPrices[this.sparkPrices.length - 1] - price) >= 0.01) {
        this.sparkPrices.push(price);
        if (this.sparkPrices.length > 34) this.sparkPrices.shift();
      }

      if (this.isOpen) {
        this._updateLiveTickStatus(price);
        this._drawMiniSparkline();
      }
    });

    bus.on('chart:resized', () => this._clampPosition());

    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
      if (this.isOpen && this.lastReport) this._updateProjectedUsd(this.lastReport);
    });

    window.addEventListener('resize', () => this._clampPosition(), { passive: true });
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen) {
        if (this.isFullscreen) this.toggleFullscreen(false);
        else this.close();
      }
    });
  }

  _drawMiniSparkline() {
    const lineEl = $('eaSparkLine');
    const areaEl = $('eaSparkArea');
    const dotEl = $('eaSparkDot');
    if (!lineEl || !areaEl || this.sparkPrices.length < 2) return;

    const pts = this.sparkPrices;
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const span = Math.max(max - min, 0.05);
    const w = 110;
    const h = 28;
    const pad = 2;

    const coords = pts.map((v, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return [x, y];
    });

    const dLine = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(' ');
    const dArea = `${dLine} L${w},${h} L0,${h} Z`;
    const isUp = pts[pts.length - 1] >= pts[0];
    const stroke = isUp ? '#34d399' : '#fb7185';

    lineEl.setAttribute('d', dLine);
    lineEl.setAttribute('stroke', stroke);
    areaEl.setAttribute('d', dArea);
    if (dotEl) {
      const last = coords[coords.length - 1];
      dotEl.setAttribute('cx', last[0].toFixed(1));
      dotEl.setAttribute('cy', last[1].toFixed(1));
      dotEl.setAttribute('fill', stroke);
      dotEl.classList.remove('hidden');
    }
  }

  _switchChartTo5m() {
    const target =
      document.querySelector('button[data-tf="5m"]') ||
      document.querySelector('button[data-interval="5m"]') ||
      Array.from(document.querySelectorAll('button')).find(
        (b) => !b.closest('#expertAnalystWindow') && b.textContent.trim().toLowerCase() === '5m'
      );
    if (target) target.click();
    else bus.emit('interval:changed', { interval: '5m' });
  }

  open() {
    if (!this.winEl) return;
    this.isOpen = true;
    this.winEl.classList.remove('hidden');

    this._switchChartTo5m();
    this._startCountdown();
    this._fetchReport();
    if (this.lastPrice) this._updateLiveTickStatus(this.lastPrice);
    this._drawMiniSparkline();
    requestAnimationFrame(() => this._clampPosition());

    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => {
      if (this.isOpen && !document.hidden) this._fetchReport();
    }, POLL_MS);
  }

  close() {
    if (this.isFullscreen) this.toggleFullscreen(false);
    this.isOpen = false;
    this.winEl?.classList.add('hidden');
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
  }

  _startCountdown() {
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    const updateTimer = () => {
      const rem = 300 - (Math.floor(Date.now() / 1000) % 300);
      const m = Math.floor(rem / 60);
      const s = rem % 60;
      const el = $('eaCandleTimer');
      if (el) {
        el.textContent = `5m ${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        el.className = rem <= 15
          ? 'px-1.5 py-0.5 rounded-md bg-rose-500/30 border border-rose-400/60 text-[9.5px] font-black text-rose-200 animate-pulse'
          : 'px-1.5 py-0.5 rounded-md bg-sky-500/20 border border-sky-400/40 text-[9.5px] font-black text-sky-300';
      }

      const prep = $('eaPrepBanner');
      if (prep && this.lastReport) {
        if (this.lastReport.can_execute && rem <= 30) {
          prep.textContent = `⏱️ ត្រៀមខ្លួន! នៅសល់ ${rem} វិនាទីទៀតបិទទៀន 5m បញ្ជាក់ដើម្បីចូល ${this.lastReport.bias}`;
          prep.classList.remove('hidden');
        } else {
          prep.classList.add('hidden');
        }
      }
    };
    updateTimer();
    this.countdownTimer = setInterval(updateTimer, 1000);
  }

  async _fetchReport() {
    if (this.isFetching) return;
    this.isFetching = true;
    const refBtn = $('eaBtnRefresh');
    if (refBtn) refBtn.classList.add('animate-spin');

    try {
      const res = await fetch(`/api/expert-analyst?t=${Date.now()}`, { cache: 'no-store' });
      const data = await res.json();
      if (data && !data.error) {
        this.lastReport = data;
        this._renderReport(data);
      } else if (data?.error && $('eaVerdictSub')) {
        $('eaVerdictSub').textContent = data.error;
      }
    } catch {
      if ($('eaVerdictSub')) $('eaVerdictSub').textContent = 'កំពុងភ្ជាប់ម៉ាស៊ីនវិភាគ 5m ឡើងវិញ...';
    } finally {
      this.isFetching = false;
      if (refBtn) refBtn.classList.remove('animate-spin');
    }
  }

  _renderReport(r) {
    const plan = r.plan || {};
    const g = r.guidance || {};
    const inst = r.institutional || {};
    const pm = r.pattern_memory || {};
    const lb = r.learning_brain || {};

    const card = $('eaVerdictCard');
    const title = $('eaVerdictTitle');
    const badge = $('eaGradeBadge');
    const bar = $('eaConfBar');

    if (card && title && badge) {
      if (r.verdict_code === 'BLOCKED') {
        card.className = 'rounded-xl border-2 border-rose-500/70 bg-rose-950/55 p-2.5 space-y-1.5 text-center';
        title.className = 'text-xs sm:text-sm font-black text-rose-300 leading-snug';
        badge.className = 'px-2 py-0.5 rounded-md bg-rose-500/30 text-rose-100 font-black border border-rose-400/50';
      } else if (r.can_execute && r.bias === 'BUY') {
        card.className = 'rounded-xl border-2 border-emerald-400/70 bg-emerald-950/50 p-2.5 space-y-1.5 text-center';
        title.className = 'text-xs sm:text-sm font-black text-emerald-300 leading-snug';
        badge.className = 'px-2 py-0.5 rounded-md bg-emerald-500/30 text-emerald-100 font-black border border-emerald-400/50';
      } else if (r.can_execute && r.bias === 'SELL') {
        card.className = 'rounded-xl border-2 border-rose-400/70 bg-rose-950/50 p-2.5 space-y-1.5 text-center';
        title.className = 'text-xs sm:text-sm font-black text-rose-300 leading-snug';
        badge.className = 'px-2 py-0.5 rounded-md bg-rose-500/30 text-rose-100 font-black border border-rose-400/50';
      } else {
        card.className = 'rounded-xl border border-amber-400/35 bg-gradient-to-b from-white/[.04] to-black/40 p-2.5 space-y-1.5 text-center';
        title.className = 'text-xs sm:text-sm font-black text-amber-300 leading-snug';
        badge.className = 'px-2 py-0.5 rounded-md bg-amber-400/20 text-amber-300 font-black border border-amber-400/35';
      }
      badge.textContent = `${r.grade || '5m'} • ${r.confidence ?? 0}%`;
      title.textContent = r.verdict_km || '--';
    }
    if (bar) bar.style.width = `${Math.max(10, Math.min(100, Number(r.confidence || 0)))}%`;
    if ($('eaVerdictSub')) $('eaVerdictSub').textContent = r.sub_km || '--';

    if ($('eaRrBadge')) $('eaRrBadge').textContent = `R:R ${plan.rr || '--'}`;
    if ($('eaValEntry')) $('eaValEntry').textContent = plan.entry ? `$${Number(plan.entry).toFixed(2)}` : '--';
    if ($('eaValSl')) $('eaValSl').textContent = plan.sl ? `$${Number(plan.sl).toFixed(2)}` : '--';
    if ($('eaValBe')) $('eaValBe').textContent = plan.be_trigger ? `$${Number(plan.be_trigger).toFixed(2)}` : '--';
    if ($('eaValTp1')) $('eaValTp1').textContent = plan.tp1 ? `$${Number(plan.tp1).toFixed(2)}` : '--';
    if ($('eaValTrail')) $('eaValTrail').textContent = plan.trailing_stop ? `$${Number(plan.trailing_stop).toFixed(2)}` : '--';
    if ($('eaValTp2')) $('eaValTp2').textContent = plan.tp2 ? `$${Number(plan.tp2).toFixed(2)}` : '--';
    this._updateProjectedUsd(r);

    if ($('eaRowTrend')) $('eaRowTrend').textContent = g.trend_km || '--';
    if ($('eaRowEntry')) $('eaRowEntry').textContent = g.entry_km || '--';
    if ($('eaRowAvoid')) $('eaRowAvoid').textContent = g.avoid_km || '--';
    if ($('eaRowProfitStop')) $('eaRowProfitStop').textContent = g.profit_stop_km || '--';
    if ($('eaRowLossStop')) $('eaRowLossStop').textContent = g.loss_stop_km || '--';

    if ($('eaSweepBadge')) $('eaSweepBadge').textContent = inst.sweep_km || '--';
    if ($('eaMatPd')) {
      $('eaMatPd').textContent = `${inst.pd_zone || '--'} (${inst.pd_pct ?? 50}%)`;
      $('eaMatPd').className = `font-black ${inst.pd_zone === 'DISCOUNT' ? 'text-emerald-400' : inst.pd_zone === 'PREMIUM' ? 'text-rose-400' : 'text-amber-300'}`;
    }
    if ($('eaMatVwap')) $('eaMatVwap').textContent = `$${Number(inst.vwap || 0).toFixed(0)} / $${Number(inst.poc || 0).toFixed(0)}`;
    if ($('eaMatCvd')) $('eaMatCvd').textContent = inst.cvd_div && inst.cvd_div !== 'NONE' ? inst.cvd_div : `${inst.cvd_pct ?? 50}%`;
    if ($('eaMatSmt')) $('eaMatSmt').textContent = `${inst.smt && inst.smt !== 'NONE' ? inst.smt : 'OK'} • ${inst.mtf_aligned || 'MIX'}`;

    const rList = $('eaReasonsList');
    if (rList) {
      const reasons = Array.isArray(r.reasons) ? r.reasons : [];
      rList.innerHTML = reasons.map((txt) => `<div class="flex items-center gap-1.5"><span class="text-cyan-400">▸</span><span>${escHtml(txt)}</span></div>`).join('');
    }

    if ($('eaBrainIq')) $('eaBrainIq').textContent = `IQ ${lb.iq_level ?? 60}`;
    if ($('eaPatternStatus')) {
      $('eaPatternStatus').textContent = `បទពិសោធន៍ទម្រង់នេះ៖ ${pm.status_km || 'កំពុងសិក្សា'}`;
      $('eaPatternStatus').className = pm.blacklisted
        ? 'rounded-lg bg-rose-500/20 border border-rose-400/50 px-2 py-1 text-[9.5px] font-black text-rose-200'
        : pm.boosted
          ? 'rounded-lg bg-emerald-500/20 border border-emerald-400/50 px-2 py-1 text-[9.5px] font-black text-emerald-200'
          : 'rounded-lg bg-white/[.04] border border-white/10 px-2 py-1 text-[9.5px] font-bold text-gray-100';
    }
    if ($('eaMemWl')) $('eaMemWl').textContent = `${lb.total_wins ?? 0} / ${lb.total_losses ?? 0}`;
    if ($('eaMemWr')) $('eaMemWr').textContent = lb.win_rate != null ? `${lb.win_rate}%` : 'ថ្មី';
    if ($('eaMemBlocked')) $('eaMemBlocked').textContent = `${lb.mistakes_blocked ?? 0}`;
    if ($('eaMemPats')) $('eaMemPats').textContent = `${lb.patterns_learned ?? 0}`;

    const lList = $('eaLessonsList');
    if (lList) {
      const lessons = Array.isArray(lb.recent_lessons) ? lb.recent_lessons : [];
      if (!lessons.length) {
        lList.innerHTML = `<div class="text-gray-400 text-center py-1">ខួរក្បាលកំពុងតាមដានជើង 5m ផ្ទាល់ ដើម្បីរៀន និងកត់ត្រាមេរៀនស្វ័យប្រវត្តិ...</div>`;
      } else {
        lList.innerHTML = lessons.map((it) => {
          const isW = it.result === 'WIN';
          const cls = isW ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200' : 'border-rose-400/35 bg-rose-500/10 text-rose-200';
          return `<div class="rounded-lg border px-2 py-1 flex items-center justify-between gap-2 ${cls}">
            <span class="truncate font-semibold">${escHtml(it.lesson_km)}</span>
            <span class="shrink-0 text-[8.5px] opacity-80 tabular-nums">${escHtml(it.time_kh)}</span>
          </div>`;
        }).join('');
      }
    }

    this._updateLiveTickStatus(this.lastPrice || toNum(r.price) || 0);
  }

  _updateProjectedUsd(r) {
    const el = $('eaProjUsd');
    const p = r?.plan;
    if (!el || !p) return;
    const ent = toNum(p.entry);
    const sl = toNum(p.sl);
    const tp1 = toNum(p.tp1);
    if (ent === null || sl === null || tp1 === null) return;

    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const winPips = Math.max(0, (Math.abs(tp1 - ent) * 10.0) - costPips);
    const lossPips = (Math.abs(ent - sl) * 10.0) + costPips;
    const mult = this.auditLot * 10.0;
    el.textContent = `Lot ${this.auditLot.toFixed(2)}: +$${(winPips * mult).toFixed(2)} / -$${(lossPips * mult).toFixed(2)}`;
  }

  _updateLiveTickStatus(price) {
    if (!price || price <= 0) return;

    const bigEl = $('eaLiveBigPrice');
    if (bigEl) {
      bigEl.textContent = `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      if (this.prevPrice !== null && price !== this.prevPrice) {
        bigEl.className = price > this.prevPrice
          ? 'text-base sm:text-lg font-black text-emerald-400 tracking-tight transition-colors'
          : 'text-base sm:text-lg font-black text-rose-400 tracking-tight transition-colors';
      }
    }

    const chEl = $('eaLiveChange');
    if (chEl && Number.isFinite(this.lastChange)) {
      const sign = this.lastChange >= 0 ? '+' : '';
      chEl.textContent = `${sign}${this.lastChange.toFixed(2)} (${sign}${this.lastChangePct.toFixed(2)}%)`;
      chEl.className = `text-[9.5px] font-bold ${this.lastChange >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    }

    const baEl = $('eaLiveBidAsk');
    if (baEl) {
      const b = this.lastBid ?? price;
      const a = this.lastAsk ?? (price + (this.lastSpread ?? directFeed.getSpread()));
      const sp = this.lastSpread ?? directFeed.getSpread();
      baEl.textContent = `Bid $${b.toFixed(2)} • Ask $${a.toFixed(2)} • Spd $${sp.toFixed(2)}`;
    }

    const pnlEl = $('eaLivePnlBadge');
    const r = this.lastReport;
    if (!pnlEl || !r?.plan) return;

    if (!r.can_execute) {
      const ent = toNum(r.plan.entry);
      pnlEl.textContent = ent !== null ? `ចម្ងាយ $${Math.abs(price - ent).toFixed(2)}` : 'WAIT';
      pnlEl.className = 'font-black text-sky-300';
      return;
    }

    const ent = Number(r.plan.entry);
    const fav = r.bias === 'BUY' ? (price - ent) : (ent - price);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const netUsd = ((fav * 10.0) - costPips) * (this.auditLot * 10.0);
    pnlEl.textContent = `Net ${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)}`;
    pnlEl.className = `font-black ${netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'}`;
  }

  async _copyField(field, btnEl) {
    const p = this.lastReport?.plan;
    if (!p) return;
    const raw = field === 'entry' ? p.entry : field === 'sl' ? p.sl : field === 'be' ? p.be_trigger : p.tp1;
    const val = toNum(raw);
    if (val === null) return;
    const txt = val.toFixed(2);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(txt);
      const valEl = btnEl.querySelector('div:last-child');
      if (valEl) {
        const old = valEl.textContent;
        valEl.textContent = `✓ ${txt}`;
        setTimeout(() => { valEl.textContent = old; }, 1100);
      }
    } catch {}
  }
}

export const expertAnalystModal = new ExpertAnalystModal();