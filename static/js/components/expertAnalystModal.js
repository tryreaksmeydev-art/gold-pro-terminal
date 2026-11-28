import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { $, escHtml, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';
import { makeResizable } from './draggable.js';

const POLL_MS = 5000;

function isForexWeekendUTC() {
  const now = new Date();
  const day = now.getUTCDay();
  const hr = now.getUTCHours();
  return day === 6 || (day === 5 && hr >= 22) || (day === 0 && hr < 22);
}

function getTimeUntilMarketOpenKm() {
  const now = new Date();
  const target = new Date(now.getTime());
  const day = now.getUTCDay();
  const daysToAdd = day === 5 ? 2 : (day === 6 ? 1 : 0);
  target.setUTCDate(now.getUTCDate() + daysToAdd);
  target.setUTCHours(22, 0, 0, 0);

  const diffSec = Math.max(0, Math.floor((target.getTime() - now.getTime()) / 1000));
  const hrs = Math.floor(diffSec / 3600);
  const mins = Math.floor((diffSec % 3600) / 60);
  const secs = diffSec % 60;
  return `${String(hrs).padStart(2, '0')}ម៉ោង ${String(mins).padStart(2, '0')}នាទី ${String(secs).padStart(2, '0')}វិនាទី`;
}

class ExpertAnalystModal {
  constructor() {
    this.isOpen = false;
    this.isFullscreen = false;
    this.isFetching = false;
    this.isReplaying = false;
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
    this.wasWeekend = isForexWeekendUTC();

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
    win.className = 'hidden absolute top-3 right-3 z-[38] w-[min(400px,calc(100%-16px))] flex flex-col rounded-2xl bg-[#070d1a]/96 backdrop-blur-2xl border border-amber-400/45 shadow-[0_24px_70px_rgba(0,0,0,.85)] overflow-hidden';
    win.style.maxHeight = 'calc(100% - 16px)';

    win.innerHTML = `
      <!-- 1. Sticky Draggable Header -->
      <div id="eaDragHeader" class="shrink-0 flex items-center justify-between gap-2 px-3.5 py-2.5 bg-[#0d162b] border-b border-white/10 cursor-grab active:cursor-grabbing select-none" style="touch-action:none;">
        <div class="flex items-center gap-2 min-w-0">
          <span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse shrink-0"></span>
          <div class="min-w-0">
            <div class="text-xs sm:text-sm font-black text-amber-300 truncate">អ្នកជំនាញវិភាគកម្រិតខ្ពស់ (5m)</div>
            <div class="text-[9px] text-gray-400 truncate">V12 Sniper A++ • 5m Macro/SMT • Paper Trade</div>
          </div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0 tabular-nums">
          <span id="eaCandleTimer" class="px-2 py-0.5 rounded-md bg-sky-500/20 text-[10px] font-black text-sky-300">5m --:--</span>
          <button id="eaBtnRefresh" type="button" title="វិភាគឡើងវិញ" class="w-6 h-6 rounded-lg bg-white/10 hover:bg-white/20 text-gray-200 text-[11px] font-black flex items-center justify-center active:scale-95">↻</button>
          <button id="eaBtnFullscreen" type="button" title="មើលពេញអេក្រង់" class="px-2 h-6 rounded-lg bg-amber-400/20 hover:bg-amber-400/35 text-amber-200 text-[10px] font-black flex items-center gap-1 active:scale-95">
            <span id="eaFsIcon">⛶</span><span id="eaFsTxt" class="hidden sm:inline">ពេញ</span>
          </button>
          <button id="eaBtnClose" type="button" title="បិទ" class="w-6 h-6 rounded-lg bg-rose-500/20 hover:bg-rose-500/35 text-rose-200 text-[10px] font-black flex items-center justify-center active:scale-95">✕</button>
        </div>
      </div>

      <!-- 2. Live Price & Sparkline Strip -->
      <div class="shrink-0 px-3.5 py-2 bg-[#050a14] border-b border-white/10 flex items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="flex items-baseline gap-2 tabular-nums">
            <span id="eaLiveBigPrice" class="text-lg sm:text-xl font-black text-white tracking-tight">$--.--</span>
            <span id="eaLiveChange" class="text-[10px] font-bold text-gray-400">--</span>
          </div>
          <div id="eaLiveBidAsk" class="text-[9.5px] text-gray-400 tabular-nums truncate">Bid -- • Ask -- • Spd --</div>
        </div>
        <svg id="eaMiniSpark" viewBox="0 0 110 30" class="w-[100px] sm:w-[116px] h-[28px] shrink-0 overflow-visible">
          <defs>
            <linearGradient id="eaSparkGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#facc15" stop-opacity="0.32"/>
              <stop offset="100%" stop-color="#facc15" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <path id="eaSparkArea" d="" fill="url(#eaSparkGrad)"></path>
          <path id="eaSparkLine" d="" fill="none" stroke="#facc15" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path>
          <circle id="eaSparkDot" cx="0" cy="0" r="2.3" fill="#facc15" class="hidden"></circle>
        </svg>
      </div>

      <!-- 3. Clean Executive Content Body -->
      <div id="eaBody" class="flex-1 min-h-0 overflow-y-auto px-3.5 sm:px-5 py-3 space-y-3.5 text-[11.5px] sm:text-xs" style="-webkit-overflow-scrolling:touch; overscroll-behavior:contain; touch-action:pan-y;">
        <div id="eaContentWrap" class="space-y-3.5">

          <!-- Weekend Standby Panel -->
          <div id="eaWeekendStandby" class="hidden space-y-2.5 pb-3 border-b border-white/10">
            <div class="rounded-xl bg-amber-500/12 border border-amber-400/40 p-3 text-center space-y-1">
              <div class="text-sm font-black text-amber-300">⏸️ ទីផ្សារបិទចុងសប្តាហ៍ — រង់ចាំផ្សារបើកវិញ</div>
              <div class="text-[11px] text-gray-200">ប្រព័ន្ធបានបង្កកតួលេខ Entry / SL / TP ចាស់ — នឹងបញ្ចូលទិន្នន័យថ្មីស្វ័យប្រវត្តិពេលផ្សារបើក!</div>
              <div id="eaWkndCountdown" class="pt-1 text-xs font-black text-amber-200 tabular-nums">⏳ រង់ចាំផ្សារបើកវិញ (ច័ន្ទ 05:00 ព្រឹក)</div>
            </div>
            <div class="space-y-1.5 text-[11px] leading-snug">
              <div class="text-gray-100">• ⏰ <b>បើកផ្សារវិញ៖</b> ថ្ងៃច័ន្ទ ម៉ោង <b class="text-amber-300">05:00 ព្រឹក</b> (ម៉ោងកម្ពុជា)</div>
              <div class="text-emerald-200">• 🔥 <b>ម៉ោងមាសចំណេញធំថ្ងៃច័ន្ទ៖</b> <b class="text-emerald-300">14:00–17:30</b> (London) & <b class="text-emerald-300">19:20–23:00</b> (NY Overlap)</div>
              <div class="text-rose-200">• ⚠️ <b>ប្រយ័ត្ន Monday Gap៖</b> ម៉ោង <b class="text-rose-300">05:00–06:30 ព្រឹក</b> Spread រីកធំ ងាយលោត Gap — ចាំទៀន 5m/15m ស្ថេរភាពសិន។</div>
            </div>
          </div>

          <!-- Live Trading Sections -->
          <div id="eaLiveSections" class="space-y-3.5">

            <!-- Daily Tilt Lock Banner -->
            <div id="eaTiltBanner" class="hidden rounded-xl bg-rose-950/75 border border-rose-500/70 p-2.5 flex items-center justify-between gap-2">
              <div id="eaTiltReason" class="text-[10.5px] font-black text-rose-200 leading-snug">🔒 សោរការពារគណនីប្រចាំថ្ងៃសកម្ម</div>
              <button id="eaBtnResetTilt" type="button" class="shrink-0 px-2.5 py-1 rounded-lg bg-rose-500 hover:bg-rose-400 text-white text-[9.5px] font-black active:scale-95">🔓 ដោះសោរ</button>
            </div>

            <!-- A. Blunt Truth Verdict + Triple-Lock Sniper A++ -->
            <div class="space-y-2 pb-3 border-b border-white/10">
              <div class="flex items-center justify-between gap-2 tabular-nums">
                <span id="eaGradeBadge" class="text-[10.5px] font-black uppercase tracking-wide text-amber-300">5m AI • --%</span>
                <span id="eaLivePnlBadge" class="text-[11px] font-black text-sky-300">READY</span>
              </div>
              <div id="eaVerdictTitle" class="text-sm sm:text-base font-black text-amber-300 leading-snug">⏳ កំពុងវិភាគទិន្នន័យស្ថាប័ន 5m...</div>
              <div id="eaVerdictSub" class="text-[11px] text-gray-200 leading-relaxed">កំពុងផ្ទៀងផ្ទាត់ Triple-Lock A++, 5m Macro & 7-Point Checklist</div>

              <!-- Triple-Lock Consensus Bar -->
              <div id="eaTripleLockBar" class="grid grid-cols-3 gap-1.5 text-[9.5px] font-black text-center">
                <div id="eaLock1" class="rounded-lg py-1 px-1.5 border border-white/10 bg-white/[.04] text-gray-400">1. AI+6/7</div>
                <div id="eaLock2" class="rounded-lg py-1 px-1.5 border border-white/10 bg-white/[.04] text-gray-400">2. Sweep/Trend</div>
                <div id="eaLock3" class="rounded-lg py-1 px-1.5 border border-white/10 bg-white/[.04] text-gray-400">3. DXY/SMT</div>
              </div>

              <!-- Intraday 5m Macro Line -->
              <div id="eaMacro5mLine" class="text-[10px] font-bold text-cyan-300 tabular-nums leading-snug">🌐 ម៉ាក្រូ 5m៖ --</div>

              <div class="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
                <div id="eaConfBar" class="h-full bg-gradient-to-r from-amber-400 to-emerald-400 transition-all duration-300" style="width:45%"></div>
              </div>
              <div id="eaPrepBanner" class="hidden pt-1 text-[11px] font-black text-amber-300 animate-pulse"></div>
            </div>

            <!-- 1-Click Paper Trade Execution Bar -->
            <div class="flex items-center justify-between gap-2 pb-3 border-b border-white/10">
              <div id="eaPaperStatus" class="text-[10.5px] font-bold text-gray-300 leading-snug">📝 ចូលសាក (Paper)៖ គ្មានជើងកំពុងរត់</div>
              <div class="flex items-center gap-1.5 shrink-0">
                <button id="eaBtnPaperOpen" type="button" class="px-2.5 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-[#070b13] text-[10px] font-black active:scale-95 transition">📝 ចូលសាក</button>
                <button id="eaBtnPaperClose" type="button" class="hidden px-2.5 py-1 rounded-lg bg-rose-500 hover:bg-rose-400 text-white text-[10px] font-black active:scale-95 transition">✖ បិទជើង</button>
              </div>
            </div>

            <!-- B. 7-Point Transparency Checklist -->
            <div class="space-y-1.5 pb-3 border-b border-white/10">
              <div class="flex items-center justify-between gap-2 text-[10.5px]">
                <span class="font-black text-emerald-300">✅ ផ្ទៀងផ្ទាត់តម្លាភាព ៧ កត្តា</span>
                <span id="eaCheckScore" class="font-black text-amber-300 tabular-nums">--/7</span>
              </div>
              <div id="eaChecklistRows" class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-[10.5px] leading-snug tabular-nums"></div>
            </div>

            <!-- C. Cambodia Killzone Timing & Market Truth -->
            <div class="space-y-1 pb-3 border-b border-white/10">
              <div class="flex items-center justify-between gap-2 text-[10.5px]">
                <span class="font-black text-cyan-300">⏰ ម៉ោងជួញដូរកម្ពុជា & ការពិតទីផ្សារ</span>
                <span id="eaKzProb" class="font-black text-amber-300 tabular-nums">--</span>
              </div>
              <div id="eaKzLine" class="font-bold text-white leading-snug">--</div>
              <div id="eaKzTruth" class="text-[11px] text-gray-300 leading-snug">--</div>
              <div id="eaRegimeLine" class="text-[10.5px] text-sky-300 font-semibold leading-snug">--</div>
              <div id="eaTrapList" class="hidden pt-1 space-y-0.5 text-[10.5px] font-bold text-rose-300"></div>
            </div>

            <!-- D. Execution Numbers -->
            <div class="space-y-2 pb-3 border-b border-white/10">
              <div class="flex items-center justify-between gap-2 text-[10.5px]">
                <span class="font-black text-amber-300">🎯 តួលេខចូលផ្សារ (ចុចលើលេខដើម្បី Copy ចូល MT5)</span>
                <span id="eaRrBadge" class="font-black text-emerald-300 tabular-nums">R:R --</span>
              </div>
              <div id="eaCopyGrid" class="flex flex-wrap items-center gap-x-4 gap-y-2 tabular-nums">
                <button type="button" data-ea-copy="entry" class="text-left hover:opacity-80 active:scale-95 transition">
                  <div class="text-[9.5px] text-gray-400 font-bold">ENTRY (ចូល)</div>
                  <div id="eaValEntry" class="text-sm sm:text-base font-black text-amber-300">--</div>
                </button>
                <button type="button" data-ea-copy="sl" class="text-left hover:opacity-80 active:scale-95 transition">
                  <div class="text-[9.5px] text-gray-400 font-bold">STOP LOSS (ឈប់)</div>
                  <div id="eaValSl" class="text-sm sm:text-base font-black text-rose-400">--</div>
                </button>
                <button type="button" data-ea-copy="be" class="text-left hover:opacity-80 active:scale-95 transition">
                  <div class="text-[9.5px] text-gray-400 font-bold">រំកិល BE (ការពារ)</div>
                  <div id="eaValBe" class="text-sm sm:text-base font-black text-sky-300">--</div>
                </button>
                <button type="button" data-ea-copy="tp1" class="text-left hover:opacity-80 active:scale-95 transition">
                  <div class="text-[9.5px] text-gray-400 font-bold">TP1 (កាត់ 50%)</div>
                  <div id="eaValTp1" class="text-sm sm:text-base font-black text-emerald-400">--</div>
                </button>
              </div>
              <div class="text-[10.5px] text-gray-300 tabular-nums leading-snug">
                Trailing Lock: <b id="eaValTrail" class="text-amber-300">--</b> • TP2: <b id="eaValTp2" class="text-emerald-300">--</b> • <span id="eaProjUsd" class="text-cyan-200 font-bold">--</span>
              </div>
              <div id="eaRiskLotLine" class="text-[10.5px] font-bold text-amber-200/95 tabular-nums">--</div>
            </div>

            <!-- E. Clear 5-Step Decision Plan -->
            <div class="space-y-1.5 pb-3 border-b border-white/10 leading-snug">
              <div class="text-[10.5px] font-black text-emerald-300">📌 ផែនការសម្រេចចិត្តច្បាស់ៗ (ចូល • ការពារចំណេញ • ឈប់)</div>
              <div id="eaRowTrend" class="text-gray-200">• --</div>
              <div id="eaRowEntry" class="text-emerald-300 font-bold">• --</div>
              <div id="eaRowAvoid" class="text-rose-300 font-semibold">• --</div>
              <div id="eaRowProfitStop" class="text-amber-200 font-bold">• --</div>
              <div id="eaRowLossStop" class="text-rose-400 font-black">• --</div>
            </div>
          </div>

          <!-- F. 5-Day Auto-Replay Backtest & Self-Learning Brain -->
          <div class="space-y-2 text-[10.5px] leading-snug">
            <div class="flex items-center justify-between gap-2">
              <span class="font-black text-violet-300">🧬 ភស្តុតាងតេស្ត ៥ ថ្ងៃ & ខួរក្បាលរៀនពីកំហុស</span>
              <span id="eaBrainIq" class="font-black text-violet-200 tabular-nums">IQ --</span>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <button id="eaBtnReplay5d" type="button" class="px-3 py-1.5 rounded-xl bg-gradient-to-r from-violet-500 to-cyan-500 hover:brightness-110 text-white text-[10.5px] font-black active:scale-95 transition">
                🧪 រត់តេស្ត ៥ ថ្ងៃចុងក្រោយ (Backtest & រៀន)
              </button>
              <span id="eaReplayStatus" class="text-[10px] text-gray-400"></span>
            </div>

            <div id="eaReplayResult" class="hidden space-y-1 py-1.5 border-y border-white/10 tabular-nums"></div>

            <div id="eaBackendSummary" class="text-gray-300 tabular-nums">--</div>
            <div id="eaReasonsList" class="space-y-0.5 text-cyan-200/90"></div>
            <div id="eaPatternStatus" class="font-bold text-gray-100 pt-1">--</div>
            <div id="eaMemLine" class="text-gray-300 tabular-nums">--</div>
            <div id="eaLessonsList" class="space-y-0.5 text-[10px] text-gray-300"></div>
          </div>

        </div>
      </div>

      <!-- Resize Handle -->
      <div id="eaResizeHandle" title="ទាញពង្រីកទំហំ" class="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize flex items-end justify-end p-0.5 text-amber-300/60 hover:text-amber-300 select-none">
        <svg class="w-2.5 h-2.5" viewBox="0 0 10 10" fill="currentColor"><path d="M9 1v8H1l8-8z"/></svg>
      </div>
    `;

    stage.appendChild(win);
    this.winEl = win;
    this.bodyEl = $('eaBody');

    this._initSmartDrag(win, stage);
    makeResizable(win, $('eaResizeHandle'), stage, { minWidth: 300, maxWidth: 720 });

    $('eaBtnClose')?.addEventListener('click', (e) => { e.stopPropagation(); this.close(); });
    $('eaBtnRefresh')?.addEventListener('click', (e) => { e.stopPropagation(); this._fetchReport(); });
    $('eaBtnFullscreen')?.addEventListener('click', (e) => { e.stopPropagation(); this.toggleFullscreen(); });
    $('eaBtnReplay5d')?.addEventListener('click', (e) => { e.stopPropagation(); this._run5DayReplay(); });
    $('eaBtnPaperOpen')?.addEventListener('click', (e) => { e.stopPropagation(); this._handlePaperAction('open'); });
    $('eaBtnPaperClose')?.addEventListener('click', (e) => { e.stopPropagation(); this._handlePaperAction('close'); });
    $('eaBtnResetTilt')?.addEventListener('click', (e) => { e.stopPropagation(); this._handleTiltReset(); });

    $('eaCopyGrid')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-ea-copy]');
      if (!btn) return;
      e.stopPropagation();
      this._copyField(btn.dataset.eaCopy, btn);
    });
  }

  async _handlePaperAction(action) {
    try {
      const res = await fetch(`/api/paper-trade/${action}`, { method: 'POST' });
      const d = await res.json();
      if (!d.ok && d.error && $('eaPaperStatus')) {
        $('eaPaperStatus').textContent = `⚠️ ${d.error}`;
      }
      this._fetchReport();
    } catch {}
  }

  async _handleTiltReset() {
    try {
      await fetch('/api/tilt-reset', { method: 'POST' });
      this._fetchReport();
    } catch {}
  }

  async _run5DayReplay() {
    if (this.isReplaying) return;
    this.isReplaying = true;

    const btn = $('eaBtnReplay5d');
    const stEl = $('eaReplayStatus');
    const resEl = $('eaReplayResult');
    if (btn) btn.disabled = true;
    if (stEl) stEl.textContent = 'កំពុងគណនាលើទៀន 5m ពិត...';

    try {
      const res = await fetch(`/api/expert-replay?train=1&t=${Date.now()}`, { cache: 'no-store' });
      const d = await res.json();
      if (d?.error) {
        if (stEl) stEl.textContent = d.error;
        return;
      }

      if (stEl) {
        stEl.textContent = d.trained_memory ? '✓ រត់តេស្ត និងបញ្ចូលមេរៀនរួចរាល់!' : '✓ រត់តេស្តរួចរាល់!';
        stEl.className = 'text-[10px] font-bold text-emerald-300';
      }

      if (resEl) {
        const netCol = d.net_pips >= 0 ? 'text-emerald-400' : 'text-rose-400';
        const tradesHtml = (d.recent_trades || []).map((t) => {
          const c = t.is_win ? 'text-emerald-300' : 'text-rose-300';
          return `<div class="${c} text-[10px]">• [${escHtml(t.time_kh)}] ${escHtml(t.side)} $${Number(t.entry).toFixed(1)} → $${Number(t.exit).toFixed(1)} (${t.pips >= 0 ? '+' : ''}${t.pips}p • ${escHtml(t.outcome)})</div>`;
        }).join('');

        resEl.innerHTML = `
          <div class="font-black text-cyan-300">📊 លទ្ធផលពិត៖ ${escHtml(d.source_km || '5 ថ្ងៃ')} (ដក Spread+Comm ${d.cost_pips}p)</div>
          <div class="font-bold text-white">
            សរុប ${d.total_trades} ជើង៖ ឈ្នះ <b class="text-emerald-400">${d.wins}</b> / ខាត <b class="text-rose-400">${d.losses}</b> • Win Rate <b class="text-amber-300">${d.win_rate}%</b> • PF <b class="text-cyan-300">${d.profit_factor}</b>
          </div>
          <div class="font-bold ${netCol}">
            លទ្ធផលសុទ្ធ៖ ${d.net_pips >= 0 ? '+' : ''}${d.net_pips} pips (Lot 0.01: ${d.net_usd_001 >= 0 ? '+' : ''}$${d.net_usd_001}) • Max DD -${d.max_dd_pips}p • ការពារអន្ទាក់ ${d.traps_avoided} ដង
          </div>
          ${tradesHtml}
        `;
        resEl.classList.remove('hidden');
      }

      this._fetchReport();
    } catch {
      if (stEl) stEl.textContent = 'បរាជ័យក្នុងការរត់តេស្ត សូមសាកម្តងទៀត';
    } finally {
      this.isReplaying = false;
      if (btn) btn.disabled = false;
    }
  }

  _initSmartDrag(el, stage) {
    let tracking = false, dragging = false, pointerId = null, fromBody = false, isTouch = false;
    let startX = 0, startY = 0, startLeft = 0, startTop = 0;

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

      tracking = true; dragging = false; pointerId = e.pointerId;
      fromBody = inBody; isTouch = e.pointerType === 'touch' || e.pointerType === 'pen';
      startX = e.clientX; startY = e.clientY;
      if (!fromBody) beginDrag(e);
    });

    el.addEventListener('pointermove', (e) => {
      if (!tracking || e.pointerId !== pointerId || this.isFullscreen) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      if (!dragging) {
        if (Math.hypot(dx, dy) < 6) return;
        if (fromBody && isTouch && Math.abs(dy) >= Math.abs(dx) * 1.1) { tracking = false; return; }
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
      if (dragging) { try { el.releasePointerCapture(e.pointerId); } catch {} }
      tracking = false; dragging = false; pointerId = null;
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
    const wrap = $('eaContentWrap');
    const resizeEl = $('eaResizeHandle');
    const fsIcon = $('eaFsIcon');
    const fsTxt = $('eaFsTxt');

    if (this.isFullscreen) {
      this.savedRect = {
        left: this.winEl.style.left, top: this.winEl.style.top,
        right: this.winEl.style.right, width: this.winEl.style.width,
        maxHeight: this.winEl.style.maxHeight,
      };
      document.body.appendChild(this.winEl);
      this.winEl.className = 'fixed inset-0 z-[160] w-screen h-[100dvh] flex flex-col bg-[#060b16] border-0 rounded-none overflow-hidden';
      this.winEl.style.left = ''; this.winEl.style.top = ''; this.winEl.style.right = '';
      this.winEl.style.width = ''; this.winEl.style.maxHeight = '100dvh';
      if (wrap) wrap.className = 'max-w-3xl mx-auto space-y-4';
      resizeEl?.classList.add('hidden');
      if (fsIcon) fsIcon.textContent = '❐';
      if (fsTxt) fsTxt.textContent = 'បង្រួម';
    } else {
      stage.appendChild(this.winEl);
      this.winEl.className = 'absolute top-3 right-3 z-[38] w-[min(400px,calc(100%-16px))] flex flex-col rounded-2xl bg-[#070d1a]/96 backdrop-blur-2xl border border-amber-400/45 shadow-[0_24px_70px_rgba(0,0,0,.85)] overflow-hidden';
      if (this.savedRect) {
        this.winEl.style.left = this.savedRect.left || '';
        this.winEl.style.top = this.savedRect.top || '';
        this.winEl.style.right = this.savedRect.right || '';
        this.winEl.style.width = this.savedRect.width || '';
        this.winEl.style.maxHeight = this.savedRect.maxHeight || 'calc(100% - 16px)';
      }
      if (wrap) wrap.className = 'space-y-3.5';
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

      if (!isForexWeekendUTC() && (!this.sparkPrices.length || Math.abs(this.sparkPrices[this.sparkPrices.length - 1] - price) >= 0.01)) {
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
      if (this.isOpen && this.lastReport && !isForexWeekendUTC()) this._updateProjectedUsd(this.lastReport);
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
    const w = 110, h = 28, pad = 2;

    const coords = pts.map((v, i) => [
      (i / (pts.length - 1)) * w,
      pad + (1 - (v - min) / span) * (h - pad * 2),
    ]);

    const dLine = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(' ');
    const dArea = `${dLine} L${w},${h} L0,${h} Z`;
    const stroke = pts[pts.length - 1] >= pts[0] ? '#34d399' : '#fb7185';

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
      if (this.isOpen && !document.hidden && !isForexWeekendUTC()) {
        this._fetchReport();
      }
    }, POLL_MS);
  }

  close() {
    if (this.isFullscreen) this.toggleFullscreen(false);
    this.isOpen = false;
    this.winEl?.classList.add('hidden');
    if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
    if (this.countdownTimer) { clearInterval(this.countdownTimer); this.countdownTimer = null; }
  }

  _startCountdown() {
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    const updateTimer = () => {
      const wknd = isForexWeekendUTC();
      const el = $('eaCandleTimer');

      if (wknd) {
        if (el) {
          el.textContent = 'ផ្សារបិទ';
          el.className = 'px-2 py-0.5 rounded-md bg-rose-500/25 text-[10px] font-black text-rose-200';
        }
        if ($('eaWkndCountdown')) {
          $('eaWkndCountdown').textContent = `⏳ នៅសល់ ${getTimeUntilMarketOpenKm()} ទៀតផ្សារបើកវិញ (ច័ន្ទ 05:00 ព្រឹក)`;
        }
        this.wasWeekend = true;
        return;
      }

      if (this.wasWeekend && !wknd) {
        this.wasWeekend = false;
        this._fetchReport();
      }

      const rem = 300 - (Math.floor(Date.now() / 1000) % 300);
      const m = Math.floor(rem / 60);
      const s = rem % 60;
      if (el) {
        el.textContent = `5m ${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        el.className = rem <= 15
          ? 'px-2 py-0.5 rounded-md bg-rose-500/30 text-[10px] font-black text-rose-200 animate-pulse'
          : 'px-2 py-0.5 rounded-md bg-sky-500/20 text-[10px] font-black text-sky-300';
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
    const wknd = isForexWeekendUTC();
    const pm = r.pattern_memory || {};
    const lb = r.learning_brain || {};

    $('eaWeekendStandby')?.classList.toggle('hidden', !wknd);
    $('eaLiveSections')?.classList.toggle('hidden', wknd);

    if (!wknd) {
      const plan = r.plan || {};
      const g = r.guidance || {};
      const kz = r.killzone || {};
      const reg = r.regime || {};
      const rg = r.risk_guide || {};
      const cl = r.checklist || {};
      const tl = r.triple_lock || {};
      const m5 = r.macro_5m || {};
      const tilt = r.tilt_lock || {};

      // Daily Tilt Lock Banner
      const tiltEl = $('eaTiltBanner');
      if (tiltEl) {
        if (tilt.locked) {
          tiltEl.classList.remove('hidden');
          if ($('eaTiltReason')) $('eaTiltReason').textContent = `🔒 ${tilt.reason_km} (នៅសល់ ${tilt.rem_mins} នាទី)`;
        } else {
          tiltEl.classList.add('hidden');
        }
      }

      // Verdict + Triple-Lock Sniper A++
      const title = $('eaVerdictTitle');
      const badge = $('eaGradeBadge');
      const bar = $('eaConfBar');
      if (title && badge) {
        if (r.verdict_code === 'BLOCKED') {
          title.className = 'text-sm sm:text-base font-black text-rose-400 leading-snug';
          badge.className = 'text-[10.5px] font-black uppercase tracking-wide text-rose-400';
        } else if (tl.is_sniper_app) {
          title.className = 'text-sm sm:text-base font-black text-cyan-300 leading-snug';
          badge.className = 'text-[10.5px] font-black uppercase tracking-wide text-cyan-300';
        } else if (r.can_execute && r.bias === 'BUY') {
          title.className = 'text-sm sm:text-base font-black text-emerald-400 leading-snug';
          badge.className = 'text-[10.5px] font-black uppercase tracking-wide text-emerald-400';
        } else if (r.can_execute && r.bias === 'SELL') {
          title.className = 'text-sm sm:text-base font-black text-rose-400 leading-snug';
          badge.className = 'text-[10.5px] font-black uppercase tracking-wide text-rose-400';
        } else {
          title.className = 'text-sm sm:text-base font-black text-amber-300 leading-snug';
          badge.className = 'text-[10.5px] font-black uppercase tracking-wide text-amber-300';
        }
        badge.textContent = `${r.grade || '5m'} • ទំនុកចិត្ត ${r.confidence ?? 0}% • ${tl.label_km || ''}`;
        title.textContent = r.verdict_km || '--';
      }
      if (bar) bar.style.width = `${Math.max(10, Math.min(100, Number(r.confidence || 0)))}%`;
      if ($('eaVerdictSub')) $('eaVerdictSub').textContent = r.sub_km || '--';

      // 3-Lock Indicators
      const setLock = (id, ok, txt) => {
        const el = $(id);
        if (!el) return;
        el.textContent = `${ok ? '✓' : '✕'} ${txt}`;
        el.className = ok
          ? 'rounded-lg py-1 px-1.5 border border-emerald-400/50 bg-emerald-500/18 text-emerald-200'
          : 'rounded-lg py-1 px-1.5 border border-white/10 bg-white/[.04] text-gray-400';
      };
      setLock('eaLock1', tl.lock1_ai, 'AI + 6/7');
      setLock('eaLock2', tl.lock2_structure, 'Sweep/Trend');
      setLock('eaLock3', tl.lock3_macro, 'DXY/SMT 5m');

      if ($('eaMacro5mLine')) {
        $('eaMacro5mLine').textContent = `🌐 ម៉ាក្រូ 5m៖ ${m5.summary_km || '--'}`;
      }

      // 1-Click Paper Trade Bar
      const actTrade = lb.active_trade;
      const pSt = $('eaPaperStatus');
      const btnOpen = $('eaBtnPaperOpen');
      const btnClose = $('eaBtnPaperClose');
      if (actTrade) {
        if (pSt) {
          pSt.textContent = `🟢 ជើងសាក ${actTrade.side} @ $${Number(actTrade.entry).toFixed(2)} • SL $${Number(actTrade.sl).toFixed(2)} ${actTrade.be_locked ? '(BE ✓)' : ''}`;
          pSt.className = 'text-[10.5px] font-black text-emerald-300 leading-snug tabular-nums';
        }
        btnOpen?.classList.add('hidden');
        btnClose?.classList.remove('hidden');
      } else {
        if (pSt) {
          pSt.textContent = `📝 ចូលសាក (Paper)៖ រួចរាល់ • ថ្ងៃនេះ ${tilt.daily_net_pips >= 0 ? '+' : ''}${tilt.daily_net_pips || 0}p`;
          pSt.className = 'text-[10.5px] font-bold text-gray-300 leading-snug tabular-nums';
        }
        btnOpen?.classList.remove('hidden');
        btnClose?.classList.add('hidden');
      }

      // 7-Point Checklist
      const scEl = $('eaCheckScore');
      if (scEl) {
        scEl.textContent = cl.verdict_km || `${cl.passed ?? 0}/7`;
        scEl.className = `font-black tabular-nums ${(cl.passed ?? 0) >= 6 ? 'text-emerald-400' : (cl.passed ?? 0) >= 4 ? 'text-amber-300' : 'text-rose-400'}`;
      }
      const clRows = $('eaChecklistRows');
      if (clRows && Array.isArray(cl.items)) {
        clRows.innerHTML = cl.items.map((it) => {
          const icon = it.ok ? '<span class="text-emerald-400 font-black">✓</span>' : '<span class="text-rose-400 font-black">✕</span>';
          const valCol = it.ok ? 'text-emerald-300' : 'text-rose-300';
          return `<div class="flex items-center justify-between gap-2">
            <span class="text-gray-200 truncate">${icon} ${escHtml(it.label)}</span>
            <span class="${valCol} font-bold shrink-0">${escHtml(it.val)}</span>
          </div>`;
        }).join('');
      }

      if ($('eaKzProb')) {
        $('eaKzProb').textContent = `ឱកាសម៉ោងនេះ ${kz.prob_pct ?? 0}% • ម៉ោង ${kz.time_kh || '--'} (កម្ពុជា)`;
        $('eaKzProb').className = `font-black tabular-nums ${kz.is_golden ? 'text-emerald-400' : kz.is_dead ? 'text-rose-400' : 'text-amber-300'}`;
      }
      if ($('eaKzLine')) $('eaKzLine').textContent = `${kz.name_km || '--'} — ${kz.next_golden_km || ''}`;
      if ($('eaKzTruth')) $('eaKzTruth').textContent = kz.truth_km || '--';
      if ($('eaRegimeLine')) $('eaRegimeLine').textContent = `ស្ថានភាពក្រាហ្វិក៖ ${reg.km || '--'}`;

      const trapEl = $('eaTrapList');
      if (trapEl) {
        const traps = Array.isArray(r.traps) ? r.traps : [];
        if (traps.length) {
          trapEl.innerHTML = traps.map((t) => `<div>⚠️ អន្ទាក់៖ ${escHtml(t)}</div>`).join('');
          trapEl.classList.remove('hidden');
        } else {
          trapEl.classList.add('hidden');
        }
      }

      if ($('eaRrBadge')) $('eaRrBadge').textContent = `R:R ${plan.rr || '--'}`;
      if ($('eaValEntry')) $('eaValEntry').textContent = plan.entry ? `$${Number(plan.entry).toFixed(2)}` : '--';
      if ($('eaValSl')) $('eaValSl').textContent = plan.sl ? `$${Number(plan.sl).toFixed(2)}` : '--';
      if ($('eaValBe')) $('eaValBe').textContent = plan.be_trigger ? `$${Number(plan.be_trigger).toFixed(2)}` : '--';
      if ($('eaValTp1')) $('eaValTp1').textContent = plan.tp1 ? `$${Number(plan.tp1).toFixed(2)}` : '--';
      if ($('eaValTrail')) $('eaValTrail').textContent = plan.trailing_stop ? `$${Number(plan.trailing_stop).toFixed(2)}` : '--';
      if ($('eaValTp2')) $('eaValTp2').textContent = plan.tp2 ? `$${Number(plan.tp2).toFixed(2)}` : '--';
      if ($('eaRiskLotLine')) $('eaRiskLotLine').textContent = rg.rule_km || '--';
      this._updateProjectedUsd(r);

      if ($('eaRowTrend')) $('eaRowTrend').textContent = `• ${g.trend_km || '--'}`;
      if ($('eaRowEntry')) $('eaRowEntry').textContent = `• ${g.entry_km || '--'}`;
      if ($('eaRowAvoid')) $('eaRowAvoid').textContent = `• ${g.avoid_km || '--'}`;
      if ($('eaRowProfitStop')) $('eaRowProfitStop').textContent = `• ${g.profit_stop_km || '--'}`;
      if ($('eaRowLossStop')) $('eaRowLossStop').textContent = `• ${g.loss_stop_km || '--'}`;
    }

    // F. Self-Learning Summary & 5-Day Backtest
    if ($('eaBrainIq')) $('eaBrainIq').textContent = `AI IQ ${lb.iq_level ?? 60}`;
    if ($('eaBackendSummary')) {
      $('eaBackendSummary').textContent = wknd
        ? 'ផ្អាកការស្កេនផ្ទាល់ចុងសប្តាហ៍ — អ្នកអាចចុច «រត់តេស្ត ៥ ថ្ងៃ» ខាងលើដើម្បីហ្វឹកហាត់ខួរក្បាលទុកមុន។'
        : (r.backend_summary_km || '--');
    }

    const rList = $('eaReasonsList');
    if (rList) {
      const reasons = (!wknd && Array.isArray(r.reasons)) ? r.reasons : [];
      rList.innerHTML = reasons.map((txt) => `<div>▸ ${escHtml(txt)}</div>`).join('');
    }

    if ($('eaPatternStatus')) {
      $('eaPatternStatus').textContent = wknd
        ? 'ស្ថានភាពខួរក្បាល៖ រួចរាល់សម្រាប់ផ្សារបើកថ្ងៃច័ន្ទ 05:00 ព្រឹក'
        : `បទពិសោធន៍ទម្រង់នេះ៖ ${pm.status_km || '--'}`;
      $('eaPatternStatus').className = (!wknd && pm.blacklisted)
        ? 'font-black text-rose-400 pt-1'
        : (!wknd && pm.boosted)
          ? 'font-black text-emerald-400 pt-1'
          : 'font-bold text-gray-100 pt-1';
    }

    if ($('eaMemLine')) {
      const wrTxt = lb.win_rate != null ? `${lb.win_rate}%` : 'ថ្មី';
      $('eaMemLine').textContent = `ស្ថិតិរៀនពិត៖ ឈ្នះ ${lb.total_wins ?? 0} / ខាត ${lb.total_losses ?? 0} (${wrTxt}) • ប្លុកកំហុស ${lb.mistakes_blocked ?? 0} ដង • រៀនបាន ${lb.patterns_learned ?? 0} ទម្រង់`;
    }

    const lList = $('eaLessonsList');
    if (lList) {
      const lessons = Array.isArray(lb.recent_lessons) ? lb.recent_lessons : [];
      lList.innerHTML = lessons.length
        ? lessons.map((it) => {
            const col = it.result === 'WIN' ? 'text-emerald-300' : 'text-rose-300';
            return `<div class="${col}">• [${escHtml(it.time_kh)}] ${escHtml(it.lesson_km)}</div>`;
          }).join('')
        : `<div class="text-gray-400">• ចុចប៊ូតុង «រត់តេស្ត ៥ ថ្ងៃ» ខាងលើ ដើម្បីហ្វឹកហាត់ខួរក្បាលទុកជាមុន!</div>`;
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
      if (!isForexWeekendUTC() && this.prevPrice !== null && price !== this.prevPrice) {
        bigEl.className = price > this.prevPrice
          ? 'text-lg sm:text-xl font-black text-emerald-400 tracking-tight transition-colors'
          : 'text-lg sm:text-xl font-black text-rose-400 tracking-tight transition-colors';
      }
    }

    const chEl = $('eaLiveChange');
    if (chEl) {
      if (isForexWeekendUTC()) {
        chEl.textContent = 'Market Closed';
        chEl.className = 'text-[10px] font-bold text-amber-300';
      } else if (Number.isFinite(this.lastChange)) {
        const sign = this.lastChange >= 0 ? '+' : '';
        chEl.textContent = `${sign}${this.lastChange.toFixed(2)} (${sign}${this.lastChangePct.toFixed(2)}%)`;
        chEl.className = `text-[10px] font-bold ${this.lastChange >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
      }
    }

    const baEl = $('eaLiveBidAsk');
    if (baEl) {
      const b = this.lastBid ?? price;
      const a = this.lastAsk ?? (price + (this.lastSpread ?? directFeed.getSpread()));
      const sp = this.lastSpread ?? directFeed.getSpread();
      baEl.textContent = `Bid $${b.toFixed(2)} • Ask $${a.toFixed(2)} • Spd $${sp.toFixed(2)}`;
    }

    if (isForexWeekendUTC()) return;

    const pnlEl = $('eaLivePnlBadge');
    const r = this.lastReport;
    if (!pnlEl || !r?.plan) return;

    const act = r.learning_brain?.active_trade;
    if (act && act.entry) {
      const fav = act.side === 'BUY' ? (price - Number(act.entry)) : (Number(act.entry) - price);
      const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
      const netUsd = ((fav * 10.0) - costPips) * (this.auditLot * 10.0);
      pnlEl.textContent = `Paper ${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)}`;
      pnlEl.className = `text-[11px] font-black ${netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'}`;
      return;
    }

    if (!r.can_execute) {
      const ent = toNum(r.plan.entry);
      pnlEl.textContent = ent !== null ? `ចម្ងាយ $${Math.abs(price - ent).toFixed(2)}` : 'WAIT';
      pnlEl.className = 'text-[11px] font-black text-sky-300';
      return;
    }

    const ent = Number(r.plan.entry);
    const fav = r.bias === 'BUY' ? (price - ent) : (ent - price);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const netUsd = ((fav * 10.0) - costPips) * (this.auditLot * 10.0);
    pnlEl.textContent = `Net ${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)}`;
    pnlEl.className = `text-[11px] font-black ${netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'}`;
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