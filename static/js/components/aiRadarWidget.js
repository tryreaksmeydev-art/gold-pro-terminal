import { COMM_PIPS, TF_SECONDS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, escHtml, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';
import { makeResizable } from './draggable.js';

const FALLBACK_TF_SEC = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 };
const ENTRY_WINDOW_SEC = { '1m': 35, '5m': 90, '15m': 180, '1h': 300, '4h': 600, '1d': 900 };

function isForexWeekendUTC() {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun, 5=Fri, 6=Sat
  const hr = now.getUTCHours();
  return day === 6 || (day === 5 && hr >= 22) || (day === 0 && hr < 22);
}

function getTimeUntilMarketOpenKm() {
  const now = new Date();
  const target = new Date(now.getTime());
  const day = now.getUTCDay();

  // ផ្សារ Forex បើកវិញថ្ងៃអាទិត្យ 22:00 UTC (= ថ្ងៃច័ន្ទ 05:00 ព្រឹក ម៉ោងកម្ពុជា)
  let daysToAdd = 0;
  if (day === 5) daysToAdd = 2;
  else if (day === 6) daysToAdd = 1;
  else if (day === 0) daysToAdd = 0;

  target.setUTCDate(now.getUTCDate() + daysToAdd);
  target.setUTCHours(22, 0, 0, 0);

  const diffSec = Math.max(0, Math.floor((target.getTime() - now.getTime()) / 1000));
  const hrs = Math.floor(diffSec / 3600);
  const mins = Math.floor((diffSec % 3600) / 60);
  const secs = diffSec % 60;
  return `${String(hrs).padStart(2, '0')}ម៉ោង ${String(mins).padStart(2, '0')}នាទី ${String(secs).padStart(2, '0')}វិនាទី`;
}

function getCambodiaSessionGuide() {
  const now = new Date();
  const isWeekend = isForexWeekendUTC();

  const kh = new Date(now.getTime() + 7 * 3600 * 1000);
  const khH = kh.getUTCHours();
  const khM = kh.getUTCMinutes();
  const hm = khH + khM / 60;
  const timeStr = `${String(khH).padStart(2, '0')}:${String(khM).padStart(2, '0')}`;

  if (isWeekend) {
    return {
      isWeekend: true,
      isGolden: false,
      isDead: true,
      timeStr,
      bigProfitKm: `ម៉ោង ${timeStr} (បិទចុងសប្តាហ៍) — ម៉ោងចំណេញធំ៖ ច័ន្ទ 14:00–17:30 (London) & 19:20–23:00 (NY Overlap)`,
      scalpKm: 'លេងតិចៗ (Scalp)៖ រង់ចាំថ្ងៃច័ន្ទម៉ោង 07:30–13:00 (វគ្គអាស៊ី) ឬ 17:30–19:15 ចូលតែគែម OB/VWAP យក $2–$4',
    };
  }

  if (hm >= 19.33 && hm < 23.0) {
    return {
      isWeekend: false,
      isGolden: true,
      isDead: false,
      timeStr,
      bigProfitKm: `🔥 ម៉ោងចំណេញធំកំពូល (${timeStr})៖ វគ្គ COMEX + NY Overlap (19:20–23:00) — លុយធំចូលខ្លាំងបំផុត (90%)!`,
      scalpKm: 'លេងតិចៗ (Scalp)៖ អាចចូលតាម Pullback EMA20/VWAP លើ 5m កាត់ចំណេញលឿន 1R ($3–$5) ដាក់ SL ដាច់ខាត',
    };
  }

  if (hm >= 14.0 && hm < 17.5) {
    return {
      isWeekend: false,
      isGolden: true,
      isDead: false,
      timeStr,
      bigProfitKm: `🔥 ម៉ោងចំណេញធំ (${timeStr})៖ វគ្គ London Open (14:00–17:30) — ឱកាសបំបែក Trend ខ្ពស់ (78%)!`,
      scalpKm: 'លេងតិចៗ (Scalp)៖ ចូលពេលតម្លៃ Retest គែម OB/EMA20 យកចំណេញខ្លី $2.5–$4 • ម៉ោងធំបន្ទាប់ 19:20 យប់',
    };
  }

  if (hm >= 17.5 && hm < 19.33) {
    return {
      isWeekend: false,
      isGolden: false,
      isDead: false,
      timeStr,
      bigProfitKm: `⏳ ម៉ោង ${timeStr} (សម្រាកមុន NY) — ម៉ោងចំណេញធំកំពូលជិតដល់ហើយ៖ 19:20–23:00 យប់`,
      scalpKm: 'លេងតិចៗ (Scalp)៖ លេងបានតិចៗតែនៅគែម Discount/Premium យក $2–$3 ហើយបិទមុនម៉ោង 19:15',
    };
  }

  if (hm >= 7.0 && hm < 14.0) {
    return {
      isWeekend: false,
      isGolden: false,
      isDead: false,
      timeStr,
      bigProfitKm: `⏳ ម៉ោង ${timeStr} (វគ្គអាស៊ី) — ម៉ោងចំណេញធំបន្ទាប់៖ 14:00–17:30 (London) & 19:20–23:00 (NY)`,
      scalpKm: 'លេងតិចៗ (Scalp)៖ ល្អសម្រាប់លេងតិចៗក្នុងប្រអប់ 07:30–12:30 ចូលតែគែមលើ-ក្រោម (OB) យក $2–$3 កុំចូលកណ្តាល',
    };
  }

  if (hm >= 23.0 || hm < 2.5) {
    return {
      isWeekend: false,
      isGolden: false,
      isDead: false,
      timeStr,
      bigProfitKm: `⏳ ម៉ោង ${timeStr} (NY ចុងថ្ងៃ) — ម៉ោងចំណេញធំបន្ទាប់៖ ថ្ងៃស្អែក 14:00 (London) & 19:20 (NY)`,
      scalpKm: 'លេងតិចៗ (Scalp)៖ លំហូរលុយថយចុះ យកតែជើង Grade A ហើយកាត់ចំណេញលឿន $2–$3 កុំទុកឆ្លងយប់',
    };
  }

  return {
    isWeekend: false,
    isGolden: false,
    isDead: true,
    timeStr,
    bigProfitKm: `🚫 ម៉ោងងាប់ (${timeStr})៖ 02:30–07:00 Spread រីក — រង់ចាំម៉ោងចំណេញធំ 14:00 (London) ឬ 19:20 (NY)`,
    scalpKm: 'លេងតិចៗ (Scalp)៖ ហាមលេងម៉ោង 02:30–07:00 ព្រឹក (ងាយបោស SL) — ចាំម៉ោង 07:30 ព្រឹកឡើងទៅ',
  };
}

class AiRadarWidget {
  constructor() {
    this.el = null;
    this.scrollBody = null;
    this.sidekickEl = null;
    this.sidekickPinned = false;
    this.sidekickDismissedKey = '';
    this.sidekickCollapsed = false;

    this.visible = false;
    this.auditLot = 0.01;
    this.lastData = null;
    this.lastBrain = null;
    this.lastSetup = null;
    this.lastPrice = null;
    this.lastInterval = '5m';
    this.lastRemSec = 60;
    this.lastRemText = '--:--';
    this.wasWeekend = isForexWeekendUTC();

    this.signalKey = '';
    this.signalFirstSeenAt = 0;
    this.signalWindowEndsAt = 0;

    this.tickHistory = [];
    this.depth = { bidPct: 50, askPct: 50, bidVol: 0, askVol: 0, source: 'WAIT' };
    this.statusTimer = null;
  }

  init(prefs = {}) {
    this.el = $('neuralScannerBox');
    this.visible = Boolean(prefs.showScanner);
    this.auditLot = Number(prefs.auditLot) || 0.01;

    this._injectCleanProUI();
    this._setupCleanScrollLayout();
    this._injectEntrySidekick();

    const stage = $('chartStage');
    if (this.el && stage) {
      this._initSmartDrag(this.el, stage);
      makeResizable(this.el, $('nsResizeHandle'), stage, { minWidth: 270, maxWidth: 460 });
    }

    this.setVisible(this.visible, false);

    bus.on('interval:changed', ({ interval }) => {
      if (interval) {
        this.lastInterval = interval;
        this.signalKey = '';
        this.signalFirstSeenAt = 0;
        this.signalWindowEndsAt = 0;
        this.sidekickDismissedKey = '';
        this._updateRealAiBadge();
      }
    });

    bus.on('market:full', ({ data }) => {
      if (data?.interval) this.lastInterval = data.interval;
      this.update(data);
    });

    bus.on('price:updated', ({ price }) => this._onLiveTick(price));
    bus.on('market:depth', (d) => this._onOrderBookDepth(d));
    bus.on('candle:countdown', ({ text, remSec }) => this._updateCandleTimer(text, remSec));
    bus.on('chart:resized', () => {
      this._fitMaxHeight();
      this._clampPosition();
      this._positionSidekick();
    });
    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
      if (!isForexWeekendUTC()) {
        this._updateProjectedPnl();
        if (this.lastPrice) {
          this._updateLiveExitTiming(this.lastPrice);
          this._renderEntryGuardAndLiveRR(this.lastPrice);
        }
      }
    });

    window.addEventListener('resize', () => {
      this._fitMaxHeight();
      this._clampPosition();
      this._positionSidekick();
    }, { passive: true });

    window.addEventListener('orientationchange', () => {
      setTimeout(() => {
        this._fitMaxHeight();
        this._clampPosition();
        this._positionSidekick();
      }, 220);
    }, { passive: true });

    // រៀងរាល់ 1 វិនាទី៖ អាប់ដេតរាប់ថយក្រោយផ្សារបើក (ថ្ងៃបិទ) ឬវិនាទីសញ្ញា BUY/SELL (ថ្ងៃបើក)
    this.statusTimer = setInterval(() => {
      const wknd = isForexWeekendUTC();
      if (wknd) {
        this._updateWeekendCountdownTick();
      } else if (this.wasWeekend && !wknd) {
        // ផ្សារទើបបើកវិញថ្ងៃច័ន្ទ 05:00 ព្រឹក -> បើកទិន្នន័យជួញដូរឡើងវិញស្វ័យប្រវត្តិ!
        this.wasWeekend = false;
        if (this.lastData) this.update(this.lastData);
      } else {
        this._updateRealAiBadge();
      }
      this.wasWeekend = wknd;
    }, 1000);
  }

  _updateWeekendCountdownTick() {
    const remStr = getTimeUntilMarketOpenKm();
    const prep = $('nsPrepCountdown');
    if (prep) {
      prep.textContent = `⏳ នៅសល់ ${remStr} ទៀតផ្សារបើកវិញ (ច័ន្ទ 05:00 ព្រឹក)`;
      prep.className = 'rounded-lg bg-amber-500/15 border border-amber-400/40 px-2.5 py-1.5 text-[10px] font-black text-amber-200 text-center tabular-nums leading-snug';
    }
    const cEl = $('nsCandleCountdown');
    if (cEl) {
      cEl.textContent = 'ផ្សារបិទ';
      cEl.className = 'px-1.5 py-0.5 rounded bg-rose-500/20 border border-rose-400/40 text-[9px] font-black text-rose-300';
    }
    const sEl = $('nsScanCountdown');
    if (sEl) {
      sEl.textContent = 'AI រង់ចាំ';
      sEl.className = 'px-1.5 py-0.5 rounded bg-white/10 border border-white/20 text-[9px] font-black text-gray-300';
    }
    if ($('skTimer')) {
      $('skTimer').textContent = 'CLOSED';
      $('skTimer').className = 'px-1.5 py-0.5 rounded bg-rose-500/25 text-[9px] font-black text-rose-200';
    }
  }

  _renderWeekendStandby(data) {
    const px = this.lastPrice ?? toNum(data?.price);
    const pxTxt = px ? `$${px.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';

    // 1) Verdict Card -> ស្ងៀមស្អាត រង់ចាំផ្សារបើក
    const vCard = $('nsVerdictCard');
    const actEl = $('nsAction');
    if (vCard && actEl) {
      vCard.className = 'rounded-xl bg-amber-500/10 border border-amber-400/40 p-2.5 text-center space-y-1';
      actEl.className = 'text-[11.5px] font-black text-amber-300';
      actEl.textContent = '⏸️ ទីផ្សារបិទចុងសប្តាហ៍ — រង់ចាំផ្សារបើកវិញ';
    }
    if ($('nsExit')) {
      $('nsExit').textContent = 'ប្រព័ន្ធបានបង្កកទិន្នន័យចាស់ — នឹងបញ្ចូលសញ្ញាថ្មីភ្លាមពេលផ្សារបើក!';
    }
    if ($('nsProjPnl')) {
      $('nsProjPnl').textContent = `តម្លៃបិទថ្ងៃសុក្រ៖ ${pxTxt} • ផ្អាកគណនា Entry / SL / TP`;
    }
    $('nsCircuitBanner')?.classList.add('hidden');

    // 2) លាក់ប្រអប់ចូលផ្សារ, Order Book និងប៊ូតុង Copy E/SL/BE/TP ចាស់ៗចេញទាំងអស់
    $('nsEntryGuardBox')?.classList.add('hidden');
    $('nsTimingAdvisor')?.classList.add('hidden');
    $('nsCopyBar')?.classList.add('hidden');

    // 3) រាប់ថយក្រោយផ្សារបើកវិញ
    this._updateWeekendCountdownTick();

    // 4) ប្តូរអក្សរណែនាំខាងក្រោម (កន្លែងគូសក្រហម) ទៅជា «ផែនការរង់ចាំផ្សារបើកថ្ងៃច័ន្ទ» គ្មានសញ្ញា BUY/SELL ចាស់ៗ
    const listEl = $('nsGuideList');
    if (listEl) {
      const standbyRows = [
        {
          sym: '⏳',
          bg: 'bg-amber-500/20 text-amber-300 border-amber-400/45',
          hl: 'ថ្ងៃច័ន្ទ ម៉ោង 05:00 ព្រឹក (ម៉ោងកម្ពុជា)',
          hlCol: 'text-amber-300',
          prefix: 'ផ្សារមាស XAU/USD បើកវិញនៅ',
          suffix: '— ទិន្នន័យសញ្ញាទិញ-លក់ (Entry/SL/TP) នឹងដាក់ចូលស្វ័យប្រវត្តិពេលផ្សារបើក!',
        },
        {
          sym: '🔥',
          bg: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/45',
          hl: '14:00–17:30 (London) & 19:20–23:00 (NY Overlap)',
          hlCol: 'text-emerald-300',
          prefix: 'ម៉ោងមាសចំណេញធំថ្ងៃច័ន្ទ៖',
          suffix: '— លុយស្ថាប័នចូលខ្លាំងបំផុត ឱកាសដល់ TP ខ្ពស់!',
        },
        {
          sym: '⚠️',
          bg: 'bg-rose-500/20 text-rose-300 border-rose-400/45',
          hl: '05:00–06:30 ព្រឹកថ្ងៃច័ន្ទ (Monday Gap)',
          hlCol: 'text-rose-300',
          prefix: 'ប្រយ័ត្នម៉ោងបើកដំបូង៖',
          suffix: 'Spread រីកធំ និងងាយលោត Gap — រង់ចាំទៀន 5m/15m ស្ថេរភាពសិនមុនចូល Order។',
        },
        {
          sym: '📌',
          bg: 'bg-sky-500/20 text-sky-300 border-sky-400/45',
          hl: pxTxt,
          hlCol: 'text-sky-300',
          prefix: 'តម្លៃបិទថ្ងៃសុក្រ (Friday Close)៖',
          suffix: '— អ្នកអាចចុច «🧠 អ្នកជំនាញវិភាគកម្រិតខ្ពស់» ដើម្បីរត់តេស្ត Backtest ៥ ថ្ងៃទុកមុនបាន។',
        },
      ];

      listEl.innerHTML = standbyRows.map((r) => `
        <div class="flex items-start gap-2 rounded-xl bg-white/[.035] border border-white/10 px-2.5 py-2 text-[10.5px] leading-snug">
          <span class="w-5 h-5 mt-0.5 rounded-lg border ${r.bg} flex items-center justify-center font-black text-[10px] shrink-0">${r.sym}</span>
          <div class="text-gray-100 font-semibold min-w-0">${escHtml(r.prefix)} <b class="${r.hlCol} font-black tabular-nums">${escHtml(r.hl)}</b> ${escHtml(r.suffix)}</div>
        </div>
      `).join('');
    }

    if ($('nsGuideFooter')) {
      $('nsGuideFooter').textContent = 'ទីផ្សារបិទចុងសប្តាហ៍ • រង់ចាំទិន្នន័យផ្សារពិតថ្ងៃច័ន្ទ ម៉ោង 05:00 ព្រឹក';
    }

    // 5) Footer Telemetry -> បង្ហាញ CLOSED ស្អាត (គ្មានសញ្ញា BUY/SELL ចាស់លើ 5 TF)
    if ($('nsDepthTxt')) {
      $('nsDepthTxt').textContent = 'CLOSED';
      $('nsDepthTxt').className = 'font-black text-rose-300';
    }
    if ($('nsVelocity')) {
      $('nsVelocity').textContent = '$0.00';
      $('nsVelocity').className = 'font-bold text-gray-400';
    }
    if ($('nsLivePnlBadge')) {
      $('nsLivePnlBadge').textContent = 'បិទផ្សារ';
      $('nsLivePnlBadge').className = 'font-black text-amber-300';
    }
    if ($('nsQuantTextLine')) {
      $('nsQuantTextLine').textContent = 'ផ្អាកការស្កេន VWAP • POC • CVD • SMT បណ្តោះអាសន្នរហូតដល់ផ្សារបើក';
    }
    if ($('nsScannerBest')) {
      $('nsScannerBest').textContent = 'រង់ចាំផ្សារបើក';
      $('nsScannerBest').className = 'font-bold text-amber-300';
    }

    const bar = $('nsTfScannerBar');
    if (bar) {
      const tfs = [
        { tf: '1m', label: '1m' }, { tf: '5m', label: '5m' }, { tf: '15m', label: '15m' },
        { tf: '1h', label: '1H' }, { tf: '4h', label: '4H' },
      ];
      bar.innerHTML = tfs.map((it) => {
        const isActive = it.tf === String(this.lastInterval).toLowerCase();
        const ring = isActive ? 'ring-1 ring-amber-300' : '';
        return `<button type="button" data-scan-tf="${it.tf}" class="rounded-lg border px-1 py-0.5 text-center leading-tight bg-white/[.04] border-white/10 text-gray-400 ${ring}">
          <div class="text-[8px] font-black">${it.label}</div>
          <div class="text-[7.5px] font-bold">បិទ</div>
        </button>`;
      }).join('');
    }

    // 6) Sidekick -> មិនលោតរំខាន បើអ្នកចុច ⚡ ផ្ទាល់ បង្ហាញស្ថានភាពរង់ចាំផ្សារបើកគ្មានលេខក្លែងក្លាយ
    this._updateSidekickUI({
      important: false,
      stateKey: 'weekend_closed',
      tone: 'wait',
      badge: '⏸️ ផ្សារបិទចុងសប្តាហ៍',
      headline: 'រង់ចាំផ្សារបើកវិញ (ថ្ងៃច័ន្ទ 05:00 ព្រឹក)',
      whenLine: `នៅសល់ ${getTimeUntilMarketOpenKm()}`,
      entry: null, sl: null, tp1: null,
      timingStatus: `តម្លៃបិទថ្ងៃសុក្រ៖ ${pxTxt} • ទិន្នន័យសញ្ញានឹងដាក់ចូលស្វ័យប្រវត្តិពេលផ្សារបើក`,
      ctaLabel: null,
      ctaValue: null,
    });
  }

  _injectEntrySidekick() {
    $('nsEntrySidekick')?.remove();
    const stage = $('chartStage');
    if (!stage) return;

    const sk = document.createElement('div');
    sk.id = 'nsEntrySidekick';
    sk.className = 'hidden absolute z-[30] rounded-2xl bg-[#081020]/96 backdrop-blur-2xl border-2 border-amber-400/65 shadow-[0_18px_48px_rgba(0,0,0,.82)] overflow-hidden transition-all duration-150 select-none';
    sk.innerHTML = `
      <div class="flex items-center justify-between gap-1.5 px-2.5 py-1.5 bg-white/[.04] border-b border-white/10">
        <div class="flex items-center gap-1.5 min-w-0">
          <span id="skDot" class="w-2 h-2 rounded-full bg-amber-400 animate-pulse shrink-0"></span>
          <span id="skBadge" class="text-[10px] font-black text-amber-300 truncate">⚡ ចំណុចចូលសំខាន់</span>
        </div>
        <div class="flex items-center gap-1 shrink-0 tabular-nums">
          <span id="skTimer" class="px-1.5 py-0.5 rounded bg-sky-500/20 text-[9px] font-black text-sky-300">--:--</span>
          <button id="skCollapseBtn" type="button" title="បង្រួម/ពង្រីក" class="w-5 h-5 rounded-md bg-white/10 hover:bg-white/20 text-gray-200 text-[9px] font-black flex items-center justify-center">⌃</button>
          <button id="skCloseBtn" type="button" title="បិទ" class="w-5 h-5 rounded-md bg-rose-500/20 hover:bg-rose-500/35 text-rose-200 text-[9px] font-black flex items-center justify-center">✕</button>
        </div>
      </div>

      <div id="skBody" class="p-2.5 space-y-2 text-[10.5px]">
        <div class="space-y-0.5">
          <div id="skHeadline" class="text-xs font-black text-white leading-snug">--</div>
          <div id="skWhen" class="text-[10px] font-bold text-cyan-300 leading-snug">--</div>
        </div>

        <div id="skNumsGrid" class="grid grid-cols-3 gap-1 tabular-nums">
          <button type="button" data-sk-copy="entry" class="rounded-lg bg-amber-400/12 hover:bg-amber-400/22 border border-amber-400/35 px-1.5 py-1 text-left active:scale-95 transition">
            <div class="text-[8px] text-amber-200/80 font-bold">ចូលត្រឹមណា</div>
            <div id="skValEntry" class="text-[11px] font-black text-amber-300">--</div>
          </button>
          <button type="button" data-sk-copy="sl" class="rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-400/40 px-1.5 py-1 text-left active:scale-95 transition">
            <div class="text-[8px] text-rose-200/80 font-bold">ឈប់ (SL)</div>
            <div id="skValSl" class="text-[11px] font-black text-rose-300">--</div>
          </button>
          <button type="button" data-sk-copy="tp1" class="rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-400/40 px-1.5 py-1 text-left active:scale-95 transition">
            <div class="text-[8px] text-emerald-200/80 font-bold">ចំណេញ TP1</div>
            <div id="skValTp1" class="text-[11px] font-black text-emerald-300">--</div>
          </button>
        </div>

        <div id="skTimingStatus" class="rounded-lg bg-white/[.04] border border-white/10 px-2 py-1 text-[10px] font-bold text-gray-100 leading-snug tabular-nums">--</div>

        <button id="skPrimaryCopy" type="button" data-copy-raw="" class="hidden w-full py-1.5 px-2 rounded-xl bg-amber-400 hover:bg-amber-300 text-[#070b13] text-[10.5px] font-black active:scale-95 transition tabular-nums">
          📋 Copy តម្លៃចូល
        </button>
      </div>
    `;

    stage.appendChild(sk);
    this.sidekickEl = sk;

    $('skCloseBtn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.sidekickPinned = false;
      this.sidekickDismissedKey = sk.dataset.stateKey || 'manual';
      sk.classList.add('hidden');
      this._syncSidekickBtnStyle();
    });

    $('skCollapseBtn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.sidekickCollapsed = !this.sidekickCollapsed;
      $('skBody')?.classList.toggle('hidden', this.sidekickCollapsed);
      const cBtn = $('skCollapseBtn');
      if (cBtn) cBtn.textContent = this.sidekickCollapsed ? '⌄' : '⌃';
      this._positionSidekick();
    });

    $('skNumsGrid')?.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-sk-copy]');
      if (!btn) return;
      e.stopPropagation();
      const raw = btn.dataset.copyVal;
      if (raw) {
        const valEl = btn.querySelector('div:last-child');
        this._copyTextRaw(raw, valEl);
      }
    });

    $('skPrimaryCopy')?.addEventListener('click', (e) => {
      e.stopPropagation();
      const btn = e.currentTarget;
      const raw = btn.dataset.copyRaw;
      if (raw) this._copyTextRaw(raw, btn);
    });
  }

  _syncSidekickBtnStyle() {
    const btn = $('btnToggleSidekick');
    if (!btn) return;
    const shown = this.sidekickEl && !this.sidekickEl.classList.contains('hidden');
    btn.className = shown
      ? 'px-1.5 py-0.5 rounded bg-amber-400 text-[#070b13] border border-amber-300 text-[9px] font-black active:scale-95 transition'
      : 'px-1.5 py-0.5 rounded bg-white/8 hover:bg-white/15 border border-white/15 text-[9px] font-black text-amber-300 active:scale-95 transition';
  }

  _positionSidekick() {
    const sk = this.sidekickEl;
    const stage = $('chartStage');
    if (!sk || !stage || sk.classList.contains('hidden')) return;

    const stageW = stage.clientWidth;
    const stageH = stage.clientHeight;
    if (!stageW || !stageH) return;

    if (stageW < 600) {
      const w = Math.min(stageW - 12, 360);
      sk.style.width = `${w}px`;
      sk.style.left = `${Math.max(6, Math.round((stageW - w) / 2))}px`;
      sk.style.top = '6px';
      sk.style.right = 'auto';
      sk.style.bottom = 'auto';
      return;
    }

    const skW = 246;
    sk.style.width = `${skW}px`;
    sk.style.right = 'auto';
    sk.style.bottom = 'auto';

    if (this.el && this.visible) {
      const stageRect = stage.getBoundingClientRect();
      const rRect = this.el.getBoundingClientRect();
      const rLeft = rRect.left - stageRect.left;
      const rRight = rRect.right - stageRect.left;
      const rTop = rRect.top - stageRect.top;

      let left = rRight + 8;
      if (left + skW > stageW - 6) left = rLeft - skW - 8;
      if (left < 6) left = Math.max(6, stageW - skW - 8);

      const skH = sk.offsetHeight || 190;
      const top = Math.max(6, Math.min(Math.max(6, stageH - skH - 6), rTop));
      sk.style.left = `${Math.round(left)}px`;
      sk.style.top = `${Math.round(top)}px`;
    } else {
      sk.style.left = `${Math.max(6, stageW - skW - 10)}px`;
      sk.style.top = '10px';
    }
  }

  _updateSidekickUI(ctx) {
    const sk = this.sidekickEl;
    if (!sk) return;

    if (!this.visible && !this.sidekickPinned) {
      sk.classList.add('hidden');
      this._syncSidekickBtnStyle();
      return;
    }

    const shouldAutoShow = Boolean(ctx.important && ctx.stateKey !== this.sidekickDismissedKey);
    if (!this.sidekickPinned && !shouldAutoShow) {
      sk.classList.add('hidden');
      this._syncSidekickBtnStyle();
      return;
    }

    sk.dataset.stateKey = ctx.stateKey;
    sk.classList.remove('hidden');

    const tones = {
      entry: { border: 'border-emerald-400/80', dot: 'bg-emerald-400', badge: 'text-emerald-300', cta: 'bg-emerald-400 hover:bg-emerald-300 text-[#070b13]' },
      armed: { border: 'border-amber-400/80', dot: 'bg-amber-400', badge: 'text-amber-300', cta: 'bg-amber-400 hover:bg-amber-300 text-[#070b13]' },
      late: { border: 'border-rose-500/85', dot: 'bg-rose-500', badge: 'text-rose-300', cta: 'bg-amber-400 hover:bg-amber-300 text-[#070b13]' },
      profit: { border: 'border-cyan-400/80', dot: 'bg-cyan-400', badge: 'text-cyan-300', cta: 'bg-cyan-400 hover:bg-cyan-300 text-[#070b13]' },
      blocked: { border: 'border-rose-500/70', dot: 'bg-rose-500', badge: 'text-rose-300', cta: 'bg-rose-500 text-white' },
      wait: { border: 'border-sky-400/50', dot: 'bg-sky-400', badge: 'text-sky-300', cta: 'bg-sky-400 text-[#070b13]' },
    };
    const t = tones[ctx.tone] || tones.wait;

    sk.className = `absolute z-[30] rounded-2xl bg-[#081020]/96 backdrop-blur-2xl border-2 ${t.border} shadow-[0_18px_48px_rgba(0,0,0,.82)] overflow-hidden transition-colors duration-150 select-none`;
    if ($('skDot')) $('skDot').className = `w-2 h-2 rounded-full ${t.dot} animate-pulse shrink-0`;
    if ($('skBadge')) {
      $('skBadge').className = `text-[10px] font-black ${t.badge} truncate`;
      $('skBadge').textContent = ctx.badge;
    }
    if ($('skTimer')) $('skTimer').textContent = isForexWeekendUTC() ? 'CLOSED' : this.lastRemText;
    if ($('skHeadline')) $('skHeadline').textContent = ctx.headline;
    if ($('skWhen')) $('skWhen').textContent = ctx.whenLine;
    if ($('skTimingStatus')) $('skTimingStatus').textContent = ctx.timingStatus;

    const numsGrid = $('skNumsGrid');
    const hasNums = ctx.entry != null && ctx.sl != null && ctx.tp1 != null;
    if (numsGrid) numsGrid.classList.toggle('hidden', !hasNums);

    const setNum = (id, val) => {
      const el = $(id);
      if (!el) return;
      el.textContent = val != null ? `$${Number(val).toFixed(1)}` : '--';
      const btn = el.closest('button[data-sk-copy]');
      if (btn) btn.dataset.copyVal = val != null ? Number(val).toFixed(2) : '';
    };
    setNum('skValEntry', ctx.entry);
    setNum('skValSl', ctx.sl);
    setNum('skValTp1', ctx.tp1);

    const cta = $('skPrimaryCopy');
    if (cta) {
      if (ctx.ctaLabel && ctx.ctaValue) {
        cta.textContent = ctx.ctaLabel;
        cta.dataset.copyRaw = ctx.ctaValue;
        cta.className = `w-full py-1.5 px-2 rounded-xl text-[10.5px] font-black active:scale-95 transition tabular-nums ${t.cta}`;
      } else {
        cta.className = 'hidden';
      }
    }

    this._syncSidekickBtnStyle();
    requestAnimationFrame(() => this._positionSidekick());
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
      if (e.button !== undefined && e.button !== 0) return;
      if (e.target.closest('button, input, a, #nsResizeHandle')) return;

      const inBody = Boolean(e.target.closest('#nsScrollBody'));
      if (inBody && e.pointerType === 'mouse' && this.scrollBody) {
        const sbRect = this.scrollBody.getBoundingClientRect();
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
      if (!tracking || e.pointerId !== pointerId) return;
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
        this._positionSidekick();
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
    const stage = $('chartStage');
    if (!this.el || !stage || !this.visible) return;
    const stageRect = stage.getBoundingClientRect();
    const elRect = this.el.getBoundingClientRect();
    if (!elRect.width || !stageRect.width) return;

    const curL = elRect.left - stageRect.left;
    const curT = elRect.top - stageRect.top;
    const maxL = Math.max(4, stage.clientWidth - this.el.offsetWidth - 4);
    const maxT = Math.max(4, stage.clientHeight - this.el.offsetHeight - 4);

    if (curL < 4 || curL > maxL || curT < 4 || curT > maxT) {
      this.el.style.right = 'auto';
      this.el.style.bottom = 'auto';
      this.el.style.left = `${Math.round(Math.max(4, Math.min(maxL, curL)))}px`;
      this.el.style.top = `${Math.round(Math.max(4, Math.min(maxT, curT)))}px`;
    }
    this._positionSidekick();
  }

  _setupCleanScrollLayout() {
    if (!this.el || $('nsScrollBody')) return;

    const children = Array.from(this.el.children);
    if (children.length < 2) return;

    const closeBtn = this.el.querySelector('#btnCloseRadar');
    const headerEl = children.find((c) => closeBtn && c.contains(closeBtn)) || children[0];
    const resizeEl = this.el.querySelector('#nsResizeHandle');

    const vCard = this.el.querySelector('#nsVerdictCard');
    const topAction = this.el.querySelector('#nsTopActionStrip');
    const guideList = this.el.querySelector('#nsGuideList');
    const guideFooter = this.el.querySelector('#nsGuideFooter');
    const footBlock = this.el.querySelector('#nsCleanFooterTelemetry');

    this.el.classList.add('ns-radar-shell');
    headerEl.classList.add('ns-radar-header');

    const body = document.createElement('div');
    body.id = 'nsScrollBody';
    body.className = 'ns-scroll-body space-y-2 p-2.5';

    if (resizeEl) this.el.insertBefore(body, resizeEl);
    else this.el.appendChild(body);

    if (vCard) { vCard.classList.remove('hidden'); body.appendChild(vCard); }
    if (topAction) { topAction.classList.remove('hidden'); body.appendChild(topAction); }
    if (guideList) { guideList.classList.remove('hidden'); body.appendChild(guideList); }
    if (guideFooter) { guideFooter.classList.remove('hidden'); body.appendChild(guideFooter); }
    if (footBlock) { footBlock.classList.remove('hidden'); body.appendChild(footBlock); }

    for (const ch of Array.from(this.el.children)) {
      if (ch === headerEl || ch === body || ch === resizeEl) continue;
      if (!ch.textContent.trim() && ch.offsetHeight <= 10) continue;
      ch.classList.add('hidden');
    }

    this.scrollBody = body;
    this._fitMaxHeight();
  }

  _fitMaxHeight() {
    if (!this.el) return;
    const stage = $('chartStage');
    const stageH = stage ? stage.clientHeight : window.innerHeight - 80;
    this.el.style.maxHeight = `${Math.max(240, stageH - 16)}px`;
  }

  _injectCleanProUI() {
    if (!this.el || $('nsTopActionStrip')) return;

    const closeBtn = $('btnCloseRadar');
    if (closeBtn && closeBtn.parentElement && !$('nsTimerStrip')) {
      const strip = document.createElement('div');
      strip.id = 'nsTimerStrip';
      strip.className = 'flex items-center gap-1 ml-auto mr-1 tabular-nums shrink-0';
      strip.innerHTML = `
        <span id="nsCandleCountdown" title="ពេលនៅសល់មុនទៀនបិទ" class="px-1.5 py-0.5 rounded bg-sky-500/20 border border-sky-400/35 text-[9px] font-black text-sky-300">ទៀន --:--</span>
        <span id="nsScanCountdown" title="វិនាទីចូលសុវត្ថិភាព (រាប់តែពេលមានសញ្ញា BUY/SELL ថ្មី)" class="px-1.5 py-0.5 rounded bg-white/10 border border-white/20 text-[9px] font-black text-gray-300">AI តាមដាន</span>
        <button id="btnToggleSidekick" type="button" title="បើក/បិទកូនប្រអប់ចូលផ្សារជាប់ខាង" class="px-1.5 py-0.5 rounded bg-white/8 hover:bg-white/15 border border-white/15 text-[9px] font-black text-amber-300 active:scale-95 transition">⚡</button>
      `;
      closeBtn.parentElement.insertBefore(strip, closeBtn);

      $('btnToggleSidekick')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const isShown = this.sidekickEl && !this.sidekickEl.classList.contains('hidden');
        if (isShown) {
          this.sidekickPinned = false;
          this.sidekickDismissedKey = this.sidekickEl.dataset.stateKey || 'manual';
          this.sidekickEl.classList.add('hidden');
          this._syncSidekickBtnStyle();
        } else {
          this.sidekickPinned = true;
          this.sidekickDismissedKey = '';
          this.sidekickCollapsed = false;
          $('skBody')?.classList.remove('hidden');
          if (isForexWeekendUTC()) {
            this._renderWeekendStandby(this.lastData);
          } else {
            const px = this.lastPrice ?? toNum(this.lastData?.price);
            if (px) this._renderEntryGuardAndLiveRR(px);
          }
        }
      });
    }

    const vCard = $('nsVerdictCard');
    if (vCard && vCard.parentElement) {
      const topStrip = document.createElement('div');
      topStrip.id = 'nsTopActionStrip';
      topStrip.className = 'space-y-1.5';
      topStrip.innerHTML = `
        <div id="nsCircuitBanner" class="hidden rounded-lg bg-amber-500/20 border border-amber-400/50 px-2 py-1 text-[9.5px] font-black text-amber-200 text-center leading-snug"></div>

        <div id="nsEntryGuardBox" class="rounded-xl bg-[#071020] border border-cyan-400/35 px-2.5 py-2 space-y-1.5">
          <div class="flex items-center justify-between gap-1.5">
            <span id="nsGuardTitle" class="text-[10.5px] font-black text-cyan-300 leading-snug">🛡️ ប្រព័ន្ធពិនិត្យតំបន់ចូលសុវត្ថិភាព</span>
            <button id="nsGuardCopyBtn" type="button" data-copy-raw="" class="hidden shrink-0 px-2 py-0.5 rounded-md bg-amber-400 text-[#070b13] text-[9px] font-black active:scale-95 transition">Copy Limit</button>
          </div>
          <div id="nsGuardDesc" class="text-[10px] font-semibold text-gray-200 leading-snug">កំពុងវាស់ចម្ងាយតម្លៃផ្ទាល់ធៀបនឹងចំណុចចូលដើម...</div>
          <div id="nsLiveRrLine" class="pt-1 border-t border-white/10 text-[9.5px] font-bold text-sky-300 tabular-nums leading-snug">
            ចូលឥឡូវ៖ --
          </div>
        </div>

        <div id="nsPrepCountdown" class="rounded-lg bg-sky-500/12 border border-sky-400/30 px-2.5 py-1 text-[10px] font-bold text-sky-200 text-center tabular-nums leading-snug">
          ⏱️ ត្រៀមខ្លួន៖ នៅសល់ --:-- ទៀតបិទទៀនបញ្ជាក់
        </div>

        <div id="nsTimingAdvisor" class="rounded-lg bg-white/[.04] border border-white/10 px-2.5 py-1.5 text-[10px] font-bold text-cyan-200 text-center leading-snug">
          ⚡ កំពុងវាស់ល្បឿន និងចំណុចកាត់ចំណេញផ្ទាល់...
        </div>

        <div id="nsCopyBar" class="grid grid-cols-4 gap-1 tabular-nums">
          <button type="button" data-copy-field="entry" class="ns-copy-btn rounded-lg bg-yellow-400/12 hover:bg-yellow-400/22 border border-yellow-400/35 px-1 py-1 text-[8.5px] font-black text-yellow-200 active:scale-95 transition">E --</button>
          <button type="button" data-copy-field="sl" class="ns-copy-btn rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-400/35 px-1 py-1 text-[8.5px] font-black text-rose-200 active:scale-95 transition">SL --</button>
          <button type="button" data-copy-field="be" class="ns-copy-btn rounded-lg bg-sky-500/15 hover:bg-sky-500/25 border border-sky-400/35 px-1 py-1 text-[8.5px] font-black text-sky-200 active:scale-95 transition">BE --</button>
          <button type="button" data-copy-field="tp" class="ns-copy-btn rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-400/35 px-1 py-1 text-[8.5px] font-black text-emerald-200 active:scale-95 transition">TP1 --</button>
        </div>
      `;
      vCard.insertAdjacentElement('afterend', topStrip);

      const foot = document.createElement('div');
      foot.id = 'nsCleanFooterTelemetry';
      foot.className = 'pt-1.5 border-t border-white/10 space-y-1.5 text-[9.5px]';
      foot.innerHTML = `
        <div class="flex items-center justify-between gap-1 text-[9px] tabular-nums">
          <span class="text-gray-400">Book: <b id="nsDepthTxt" class="text-gray-200">50/50</b></span>
          <span class="text-gray-400">10s: <b id="nsVelocity" class="text-white">$0.00</b></span>
          <span id="nsLivePnlBadge" class="font-black text-sky-300">LIVE --</span>
        </div>
        <div id="nsQuantTextLine" class="text-[9px] text-gray-300 tabular-nums leading-snug text-center">
          VWAP -- • POC -- • CVD -- • SMT --
        </div>
        <div class="space-y-1">
          <div class="flex items-center justify-between text-[8.5px] text-gray-400">
            <span class="font-bold text-yellow-300">ប្តូរ TF លឿន</span>
            <span id="nsScannerBest" class="font-black text-gray-300">--</span>
          </div>
          <div id="nsTfScannerBar" class="grid grid-cols-5 gap-1"></div>
        </div>
      `;
      vCard.parentElement.appendChild(foot);

      topStrip.querySelectorAll('.ns-copy-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          this._handleCopyField(btn.dataset.copyField, btn);
        });
      });

      topStrip.querySelector('#nsGuardCopyBtn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const btn = e.currentTarget;
        const val = btn.dataset.copyRaw;
        if (val) this._copyTextRaw(val, btn);
      });

      foot.querySelector('#nsTfScannerBar')?.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-scan-tf]');
        if (!btn) return;
        e.stopPropagation();
        this._switchTimeframe(btn.dataset.scanTf);
      });
    }
  }

  async _copyTextRaw(text, targetEl) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      if (targetEl) {
        const oldTxt = targetEl.textContent;
        targetEl.textContent = `✓ ${text}`;
        setTimeout(() => { targetEl.textContent = oldTxt; }, 1100);
      }
    } catch {}
  }

  _getActivePlan() {
    const b = this.lastBrain;
    return (b?.can_trade && b?.plan) ? b.plan : (b?.preview_plan || this.lastSetup);
  }

  _computeExitTargets(plan, side) {
    if (!plan) return null;
    const entry = toNum(plan.entry);
    const sl = toNum(plan.sl);
    const tp1 = toNum(plan.tp ?? plan.tp1);
    if (entry === null || sl === null || tp1 === null) return null;

    const risk = Math.max(1.2, Math.abs(entry - sl));
    const atr = Math.max(0.8, toNum(this.lastData?.atr) ?? 2.0);
    const ema20 = toNum(this.lastData?.ema20) ?? entry;
    const price = this.lastPrice ?? toNum(this.lastData?.price) ?? entry;
    const smc = this.lastData?.smc || {};
    const tp2 = toNum(plan.tp2) ?? (side === 'BUY' ? entry + risk * 2.6 : entry - risk * 2.6);

    if (side === 'BUY') {
      const be = entry + risk * 0.9;
      const bearObs = (smc.supply_blocks || []).filter((b) => String(b.label || 'OB').toUpperCase() === 'OB' && toNum(b.bottom) > entry + risk * 0.9);
      const nearestBearBot = bearObs.length ? Math.min(...bearObs.map((b) => Number(b.bottom))) : null;
      const safeTp = (nearestBearBot && nearestBearBot < tp1) ? (nearestBearBot - 0.35) : tp1;
      const trailLock = Math.max(ema20 - atr * 0.45, price - atr * 1.15);
      return {
        side: 'BUY',
        be: Number(be.toFixed(2)),
        safeTp: Number(safeTp.toFixed(2)),
        maxReach: Number(tp2.toFixed(2)),
        trailLock: Number(trailLock.toFixed(2)),
      };
    }

    const be = entry - risk * 0.9;
    const bullObs = (smc.demand_blocks || []).filter((b) => String(b.label || 'OB').toUpperCase() === 'OB' && toNum(b.top) < entry - risk * 0.9);
    const nearestBullTop = bullObs.length ? Math.max(...bullObs.map((b) => Number(b.top))) : null;
    const safeTp = (nearestBullTop && nearestBullTop > tp1) ? (nearestBullTop + 0.35) : tp1;
    const trailLock = Math.min(ema20 + atr * 0.45, price + atr * 1.15);
    return {
      side: 'SELL',
      be: Number(be.toFixed(2)),
      safeTp: Number(safeTp.toFixed(2)),
      maxReach: Number(tp2.toFixed(2)),
      trailLock: Number(trailLock.toFixed(2)),
    };
  }

  _inferSide() {
    const b = this.lastBrain;
    if (b?.side === 'BUY' || b?.side === 'SELL') return b.side;
    const cand = b?.score_breakdown?.candidate_side;
    if (cand === 'BUY' || cand === 'SELL') return cand;
    const p = this._getActivePlan();
    if (p && toNum(p.entry) !== null && toNum(p.sl) !== null) {
      return Number(p.entry) > Number(p.sl) ? 'BUY' : 'SELL';
    }
    return 'BUY';
  }

  _trackRealSignalChange() {
    const b = this.lastBrain;
    const p = b?.plan;
    const validTrade = Boolean(
      !isForexWeekendUTC() &&
      b?.can_trade &&
      !b?.is_danger &&
      b?.status !== 'BLOCKED' &&
      (b.side === 'BUY' || b.side === 'SELL') &&
      p &&
      toNum(p.entry) !== null &&
      toNum(p.sl) !== null
    );

    if (validTrade) {
      const key = `${this.lastInterval}|${b.side}|${Number(p.entry).toFixed(1)}|${Number(p.sl).toFixed(1)}`;
      if (key !== this.signalKey) {
        this.signalKey = key;
        this.signalFirstSeenAt = Date.now();
        const winSec = ENTRY_WINDOW_SEC[this.lastInterval] || 90;
        this.signalWindowEndsAt = this.signalFirstSeenAt + winSec * 1000;
      }
    } else {
      this.signalKey = '';
      this.signalFirstSeenAt = 0;
      this.signalWindowEndsAt = 0;
    }

    this._updateRealAiBadge();
  }

  _getSignalAgeSec() {
    if (!this.signalFirstSeenAt) return 0;
    return Math.max(0, Math.floor((Date.now() - this.signalFirstSeenAt) / 1000));
  }

  _isCandleExhausted() {
    const candles = this.lastData?.candles;
    const atr = Math.max(0.8, toNum(this.lastData?.atr) ?? 2.0);
    if (!Array.isArray(candles) || !candles.length) return false;
    const last = candles[candles.length - 1];
    const hi = toNum(last?.high);
    const lo = toNum(last?.low);
    if (hi === null || lo === null) return false;
    return (hi - lo) >= atr * 1.85;
  }

  _updateRealAiBadge() {
    const el = $('nsScanCountdown');
    if (!el) return;

    if (isForexWeekendUTC()) {
      el.textContent = 'AI រង់ចាំ';
      el.className = 'px-1.5 py-0.5 rounded bg-white/10 border border-white/20 text-[9px] font-black text-gray-300';
      return;
    }

    const b = this.lastBrain;
    if (!b) {
      el.textContent = 'AI ភ្ជាប់';
      el.className = 'px-1.5 py-0.5 rounded bg-white/10 border border-white/20 text-[9px] font-black text-gray-300';
      return;
    }

    if (b.is_danger || b.status === 'BLOCKED') {
      el.textContent = 'AI បិទ';
      el.className = 'px-1.5 py-0.5 rounded bg-rose-500/20 border border-rose-400/40 text-[9px] font-black text-rose-300';
      return;
    }

    const price = this.lastPrice ?? toNum(this.lastData?.price) ?? 0;
    const plan = this._getActivePlan();
    const side = this._inferSide();
    const exits = this._computeExitTargets(plan, side);
    const atr = Math.max(0.8, toNum(this.lastData?.atr) ?? 2.0);
    const entryWindow = Math.max(0.75, Math.min(1.5, atr * 0.35));

    if (!b.can_trade || !this.signalWindowEndsAt || !plan || !exits) {
      const ent = toNum(plan?.entry);
      const dist = (ent !== null && price > 0) ? Math.abs(price - ent) : 99;
      if (dist <= Math.max(3.5, atr * 1.65)) {
        el.textContent = 'AI ត្រៀម';
        el.className = 'px-1.5 py-0.5 rounded bg-amber-500/20 border border-amber-400/45 text-[9px] font-black text-amber-300';
      } else {
        el.textContent = 'AI តាមដាន';
        el.className = 'px-1.5 py-0.5 rounded bg-white/10 border border-white/20 text-[9px] font-black text-gray-300';
      }
      return;
    }

    const entry = Number(plan.entry);
    const sl = Number(plan.sl);
    const tp1 = exits.safeTp;
    const favMove = side === 'BUY' ? (price - entry) : (entry - price);
    const tpDist = Math.max(0.5, Math.abs(tp1 - entry));
    const progPct = (favMove / tpDist) * 100;
    const liveRew = side === 'BUY' ? (tp1 - price) : (price - tp1);
    const liveRisk = side === 'BUY' ? (price - sl) : (sl - price);
    const liveRr = (liveRew > 0 && liveRisk > 0) ? (liveRew / liveRisk) : 0;
    const distAbs = Math.abs(price - entry);

    if (favMove > entryWindow && (progPct >= 30 || liveRr < 1.20)) {
      el.textContent = `${side} ហួស`;
      el.className = 'px-1.5 py-0.5 rounded bg-rose-500/25 border border-rose-400/55 text-[9px] font-black text-rose-200';
      return;
    }

    if (plan.entry_state === 'PULLBACK' && distAbs > entryWindow) {
      el.textContent = `${side} Limit`;
      el.className = 'px-1.5 py-0.5 rounded bg-amber-500/20 border border-amber-400/50 text-[9px] font-black text-amber-200';
      return;
    }

    const remSec = Math.max(0, Math.ceil((this.signalWindowEndsAt - Date.now()) / 1000));
    if (remSec <= 0) {
      el.textContent = `${side} ចាស់`;
      el.className = 'px-1.5 py-0.5 rounded bg-amber-500/20 border border-amber-400/45 text-[9px] font-black text-amber-300';
      return;
    }

    const fmt = remSec >= 60
      ? `${Math.floor(remSec / 60)}:${String(remSec % 60).padStart(2, '0')}`
      : `${remSec}s`;

    el.textContent = `${side} ${fmt}`;
    if (side === 'BUY') {
      el.className = remSec <= 15
        ? 'px-1.5 py-0.5 rounded bg-emerald-500/30 border border-emerald-400/70 text-[9px] font-black text-emerald-100 animate-pulse'
        : 'px-1.5 py-0.5 rounded bg-emerald-500/25 border border-emerald-400/55 text-[9px] font-black text-emerald-200';
    } else {
      el.className = remSec <= 15
        ? 'px-1.5 py-0.5 rounded bg-rose-500/30 border border-rose-400/70 text-[9px] font-black text-rose-100 animate-pulse'
        : 'px-1.5 py-0.5 rounded bg-rose-500/25 border border-rose-400/55 text-[9px] font-black text-rose-200';
    }
  }

  _renderEntryGuardAndLiveRR(price) {
    if (isForexWeekendUTC()) return;

    const box = $('nsEntryGuardBox');
    const titleEl = $('nsGuardTitle');
    const descEl = $('nsGuardDesc');
    const rrEl = $('nsLiveRrLine');
    const copyBtn = $('nsGuardCopyBtn');
    if (!box || !titleEl || !descEl || !rrEl || !copyBtn) return;

    const plan = this._getActivePlan();
    const side = this._inferSide();
    const exits = this._computeExitTargets(plan, side);
    const b = this.lastBrain;

    if (!plan || !exits || !Number.isFinite(price) || price <= 0) {
      copyBtn.classList.add('hidden');
      return;
    }

    const entry = Number(plan.entry);
    const sl = Number(plan.sl);
    const tp1 = exits.safeTp;
    const atr = Math.max(0.8, toNum(this.lastData?.atr) ?? 2.0);
    const ema20 = toNum(this.lastData?.ema20) ?? entry;

    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const mult = this.auditLot * 10.0;
    const liveRewardUsdDist = side === 'BUY' ? (tp1 - price) : (price - tp1);
    const liveRiskUsdDist = side === 'BUY' ? (price - sl) : (sl - price);
    const liveWinNet = Math.max(0, ((liveRewardUsdDist * 10.0) - costPips) * mult);
    const liveLossNet = Math.max(0.1, ((Math.max(0.1, liveRiskUsdDist) * 10.0) + costPips) * mult);
    const liveRr = (liveRewardUsdDist > 0 && liveRiskUsdDist > 0) ? (liveRewardUsdDist / liveRiskUsdDist) : 0;

    let rrSummaryTxt = '';
    if (liveRewardUsdDist <= 0) {
      rrSummaryTxt = `ចូលឥឡូវ ($${price.toFixed(2)})៖ ហួស TP1 ហើយ — ហាមចូល!`;
      rrEl.className = 'pt-1 border-t border-white/10 text-[9.5px] font-black text-rose-400 tabular-nums leading-snug';
    } else if (liveRiskUsdDist <= 0) {
      rrSummaryTxt = `ចូលឥឡូវ ($${price.toFixed(2)})៖ បែក SL ហើយ — អស់សុពលភាព!`;
      rrEl.className = 'pt-1 border-t border-white/10 text-[9.5px] font-black text-rose-400 tabular-nums leading-snug';
    } else {
      const rrBad = liveRr < 1.20;
      rrSummaryTxt = `ចូលឥឡូវ ($${price.toFixed(2)})៖ TP +$${liveWinNet.toFixed(2)} | SL -$${liveLossNet.toFixed(2)} • R:R ${liveRr.toFixed(2)}:1 ${rrBad ? '(ខាតប្រៀប!)' : '(ល្អ ✓)'}`;
      rrEl.className = `pt-1 border-t border-white/10 text-[9.5px] font-black tabular-nums leading-snug ${rrBad ? 'text-rose-400' : 'text-emerald-300'}`;
    }
    rrEl.textContent = rrSummaryTxt;

    if (b?.is_danger || b?.status === 'BLOCKED') {
      const why = (b.reasons && b.reasons[0]) || 'ទីផ្សារមានហានិភ័យខ្ពស់ — ផ្អាកចូល Order សិន!';
      box.className = 'rounded-xl bg-rose-950/45 border border-rose-500/50 px-2.5 py-2 space-y-1.5';
      titleEl.className = 'text-[10.5px] font-black text-rose-300 leading-snug';
      titleEl.textContent = '⛔ ប្រព័ន្ធចាក់សោការពារដើមទុន';
      descEl.textContent = why;
      copyBtn.classList.add('hidden');

      this._updateSidekickUI({
        important: false,
        stateKey: 'blocked',
        tone: 'blocked',
        badge: '⛔ ហាមចូលពេលនេះ',
        headline: why,
        whenLine: `ចូលពេលណា៖ រង់ចាំផុតអន្ទាក់ (${this.lastInterval})`,
        entry, sl, tp1,
        timingStatus: rrSummaryTxt,
        ctaLabel: null,
        ctaValue: null,
      });
      return;
    }

    const entryWindow = Math.max(0.75, Math.min(1.5, atr * 0.35));
    const favMove = side === 'BUY' ? (price - entry) : (entry - price);
    const tpDist = Math.max(0.5, Math.abs(tp1 - entry));
    const progPct = (favMove / tpDist) * 100;
    const distAbs = Math.abs(price - entry);

    const tfSec = (TF_SECONDS && TF_SECONDS[this.lastInterval]) || FALLBACK_TF_SEC[this.lastInterval] || 300;
    const ageSec = this._getSignalAgeSec();
    const isStale = Boolean(b?.can_trade && ageSec >= Math.max(90, Math.round(tfSec * 1.6)));
    const isExhausted = Boolean(b?.can_trade && this._isCandleExhausted());

    if (b?.can_trade && progPct >= 50) {
      const hitTp = progPct >= 100;
      this._updateSidekickUI({
        important: true,
        stateKey: `${hitTp ? 'tp1' : 'be'}_${side}_${entry.toFixed(1)}`,
        tone: 'profit',
        badge: hitTp ? '🎯 ដល់ TP1 កាត់ចំណេញ!' : '🛡️ រំកិល SL ស្មើដើម!',
        headline: hitTp
          ? `ដល់គោលដៅ $${tp1.toFixed(2)} — បិទយកចំណេញ 60%–100% ឥឡូវ!`
          : `រត់បាន ${Math.round(progPct)}% TP1 — រំកិល SL មក $${entry.toFixed(2)} ភ្លាម!`,
        whenLine: `ការពារចំណេញ៖ បើបកក្រោយបិទហួស $${exits.trailLock.toFixed(2)} បិទចេញភ្លាម`,
        entry, sl, tp1,
        timingStatus: `តម្លៃផ្ទាល់ $${price.toFixed(2)} • រំកិល BE: $${exits.be.toFixed(2)}`,
        ctaLabel: hitTp ? null : `📋 Copy BE $${entry.toFixed(2)}`,
        ctaValue: hitTp ? null : entry.toFixed(2),
      });
    }

    if (favMove > entryWindow && (progPct >= 30 || liveRr < 1.20)) {
      const midPull = (entry + price) / 2.0;
      const emaInBand = (ema20 >= Math.min(entry, price) && ema20 <= Math.max(entry, price));
      const pull2 = Number((emaInBand ? (midPull + ema20) / 2.0 : midPull).toFixed(2));
      const rr2 = Math.abs(tp1 - pull2) / Math.max(0.5, Math.abs(pull2 - sl));
      const hasPull2 = rr2 >= 1.45 && progPct < 82;

      box.className = 'rounded-xl bg-rose-950/65 border-2 border-rose-500/70 px-2.5 py-2 space-y-1.5';
      titleEl.className = 'text-[10.5px] font-black text-rose-300 leading-snug';
      titleEl.textContent = `🚫 ហួសចំណុចចូលហើយ (${Math.round(progPct)}% TP1) — ហាមដេញ!`;

      if (hasPull2) {
        descEl.textContent = `🎯 ជើងទី ២ (Pullback Limit)៖ រង់ចាំថយមក $${pull2.toFixed(2)} (R:R ${rr2.toFixed(1)}:1) • បើមិនថយមកទេ បោះបង់ជើងនេះ!`;
        copyBtn.textContent = `Copy ជើងទី២ ${pull2.toFixed(1)}`;
        copyBtn.dataset.copyRaw = pull2.toFixed(2);
        copyBtn.className = 'shrink-0 px-2 py-0.5 rounded-md bg-amber-400 text-[#070b13] text-[9px] font-black active:scale-95 transition';
      } else {
        descEl.textContent = 'បោះបង់ជើងនេះចុះ! តម្លៃរត់ឆ្ងាយពេកហើយ — រក្សាដើមទុនរង់ចាំសញ្ញាជុំថ្មី ល្អជាងដេញចូលនាំខាត។';
        copyBtn.classList.add('hidden');
      }

      if (!(b?.can_trade && progPct >= 50)) {
        this._updateSidekickUI({
          important: Boolean(b?.can_trade),
          stateKey: `late_${side}_${entry.toFixed(1)}`,
          tone: 'late',
          badge: '🚫 ចូលយឺតហើយ — ហាមដេញ!',
          headline: `ហួសចំណុចចូលដើម (${Math.round(progPct)}% TP1) — កុំដេញពាក់កណ្តាលផ្លូវ!`,
          whenLine: hasPull2
            ? `ចូលជើងទី ២៖ ដាក់ ${side} Limit @ $${pull2.toFixed(2)} (R:R ${rr2.toFixed(1)}:1)`
            : 'បោះបង់ជើងនេះ! រង់ចាំជុំថ្មីល្អជាងចូលខាត',
          entry: hasPull2 ? pull2 : entry, sl, tp1,
          timingStatus: rrSummaryTxt,
          ctaLabel: hasPull2 ? `📋 Copy ជើងទី ២ ($${pull2.toFixed(2)})` : null,
          ctaValue: hasPull2 ? pull2.toFixed(2) : null,
        });
      }
      return;
    }

    if (isStale || isExhausted) {
      box.className = 'rounded-xl bg-amber-950/55 border border-amber-400/60 px-2.5 py-2 space-y-1.5';
      titleEl.className = 'text-[10.5px] font-black text-amber-300 leading-snug';
      titleEl.textContent = isExhausted
        ? '⚠️ ទៀនផ្ទុះវែងអស់កម្លាំង (>1.8x ATR) — ហាមដេញ!'
        : '⏳ សញ្ញាចាស់ហើយ — ប្រយ័ត្នការបត់ក្បាល!';
      descEl.textContent = `រង់ចាំទៀន ${this.lastInterval} ថយ Pullback មក $${entry.toFixed(2)} ឬរង់ចាំជុំថ្មី កុំប្រថុយចូលយឺត។`;
      copyBtn.classList.add('hidden');

      this._updateSidekickUI({
        important: true,
        stateKey: `stale_${side}_${entry.toFixed(1)}`,
        tone: 'late',
        badge: isExhausted ? '⚠️ ទៀនអស់កម្លាំង' : '⏳ សញ្ញាចាស់ហើយ',
        headline: 'ប្រយ័ត្នការបត់ក្បាល! កុំដេញចូលយឺត',
        whenLine: `ចាំថយមក $${entry.toFixed(2)} ឬរង់ចាំជុំថ្មី`,
        entry, sl, tp1,
        timingStatus: rrSummaryTxt,
        ctaLabel: null,
        ctaValue: null,
      });
      return;
    }

    if (distAbs <= entryWindow && liveRr >= 1.20) {
      box.className = 'rounded-xl bg-emerald-950/55 border-2 border-emerald-400/65 px-2.5 py-2 space-y-1.5';
      titleEl.className = 'text-[10.5px] font-black text-emerald-300 leading-snug';
      titleEl.textContent = `✅ ក្នុងតំបន់ចូលសុវត្ថិភាព (±$${entryWindow.toFixed(2)})`;
      descEl.textContent = b?.can_trade
        ? `ចំណុចចូលល្អបំផុត (${side} @ $${entry.toFixed(2)}) — ចាំបិទទៀនបញ្ជាក់ ដាក់ SL $${sl.toFixed(2)} ដាច់ខាត!`
        : `តម្លៃមកដល់តំបន់ត្រៀម $${entry.toFixed(2)} ហើយ — រង់ចាំទៀន ${this.lastInterval} បិទបញ្ជាក់ទិស ${side}។`;
      copyBtn.textContent = `Copy E ${entry.toFixed(1)}`;
      copyBtn.dataset.copyRaw = entry.toFixed(2);
      copyBtn.className = 'shrink-0 px-2 py-0.5 rounded-md bg-emerald-400 text-[#070b13] text-[9px] font-black active:scale-95 transition';

      this._updateSidekickUI({
        important: true,
        stateKey: `entry_${side}_${entry.toFixed(1)}`,
        tone: 'entry',
        badge: `✅ ដល់ពេលចូល ${side}!`,
        headline: b?.can_trade
          ? `ចូល ${side === 'BUY' ? 'ទិញ (BUY)' : 'លក់ (SELL)'} @ $${entry.toFixed(2)} (${b.grade || 'A'})`
          : `តម្លៃប៉ះតំបន់ ${side} ($${entry.toFixed(2)}) ហើយ!`,
        whenLine: b?.close_confirmed
          ? 'ចូលពេលណា៖ បិទទៀនបញ្ជាក់ហើយ — ចូលបានឥឡូវ!'
          : `ចូលពេលណា៖ រង់ចាំ ${this.lastRemText} បិទទៀន ${this.lastInterval} បញ្ជាក់`,
        entry, sl, tp1,
        timingStatus: rrSummaryTxt,
        ctaLabel: `📋 Copy Entry $${entry.toFixed(2)}`,
        ctaValue: entry.toFixed(2),
      });
      return;
    }

    const armedRange = Math.max(3.5, atr * 1.65);
    if (favMove < -entryWindow && distAbs <= armedRange) {
      box.className = 'rounded-xl bg-amber-950/50 border border-amber-400/60 px-2.5 py-2 space-y-1.5';
      titleEl.className = 'text-[10.5px] font-black text-amber-300 leading-snug';
      titleEl.textContent = `⚡ ARMED (ត្រៀមដៃ) — សល់ $${distAbs.toFixed(2)} ដល់ចំណុចចូល!`;
      descEl.textContent = `ដាក់ ${side} Limit ទុកមុននៅ $${entry.toFixed(2)} (SL $${sl.toFixed(2)}) ដើម្បីចាប់ជើងចំកំពូល/បាត មិនបាច់ដេញតាមទៀន!`;
      copyBtn.textContent = `Copy Limit ${entry.toFixed(1)}`;
      copyBtn.dataset.copyRaw = entry.toFixed(2);
      copyBtn.className = 'shrink-0 px-2 py-0.5 rounded-md bg-amber-400 text-[#070b13] text-[9px] font-black active:scale-95 transition';

      this._updateSidekickUI({
        important: true,
        stateKey: `armed_${side}_${entry.toFixed(1)}`,
        tone: 'armed',
        badge: `⚡ ត្រៀមដៃ (${side} Limit)`,
        headline: `សល់តែ $${distAbs.toFixed(2)} ទៀតដល់ចំណុចចូលល្អ!`,
        whenLine: `ចូលពេលណា៖ ដាក់ ${side} Limit ទុកមុនឥឡូវ កុំឱ្យចូលយឺត`,
        entry, sl, tp1,
        timingStatus: rrSummaryTxt,
        ctaLabel: `📋 Copy Limit $${entry.toFixed(2)}`,
        ctaValue: entry.toFixed(2),
      });
      return;
    }

    box.className = 'rounded-xl bg-[#071020] border border-white/15 px-2.5 py-2 space-y-1.5';
    titleEl.className = 'text-[10.5px] font-black text-sky-300 leading-snug';
    titleEl.textContent = `👁️ រង់ចាំតម្លៃមកដល់តំបន់ចូល ($${entry.toFixed(2)})`;
    descEl.textContent = `នៅឆ្ងាយ $${distAbs.toFixed(2)} — ហាមចូលពាក់កណ្តាលផ្លូវ អត់ធ្មត់ចាំតម្លៃមកប៉ះចំណុចល្អសិន។`;
    copyBtn.classList.add('hidden');

    this._updateSidekickUI({
      important: false,
      stateKey: `wait_${side}_${entry.toFixed(1)}`,
      tone: 'wait',
      badge: `👁️ រង់ចាំ (${side})`,
      headline: `នៅឆ្ងាយ $${distAbs.toFixed(2)} ពីចំណុចចូល $${entry.toFixed(2)}`,
      whenLine: 'ចូលពេលណា៖ ហាមចូលពាក់កណ្តាលផ្លូវ ចាំតម្លៃមកប៉ះសិន',
      entry, sl, tp1,
      timingStatus: rrSummaryTxt,
      ctaLabel: `📋 Copy Limit $${entry.toFixed(2)}`,
      ctaValue: entry.toFixed(2),
    });
  }

  async _handleCopyField(field, btnEl) {
    const plan = this._getActivePlan();
    if (!plan) return;
    const side = this._inferSide();
    const exits = this._computeExitTargets(plan, side);

    const raw =
      field === 'entry' ? plan.entry :
      field === 'sl' ? plan.sl :
      field === 'be' ? exits?.be :
      (exits?.safeTp ?? plan.tp ?? plan.tp1);

    const val = toNum(raw);
    if (val === null) return;
    await this._copyTextRaw(val.toFixed(2), btnEl);
  }

  _updateCopyButtons() {
    const plan = this._getActivePlan();
    const bar = $('nsCopyBar');
    if (!bar) return;
    const side = this._inferSide();
    const exits = this._computeExitTargets(plan, side);

    const eBtn = bar.querySelector('[data-copy-field="entry"]');
    const sBtn = bar.querySelector('[data-copy-field="sl"]');
    const bBtn = bar.querySelector('[data-copy-field="be"]');
    const tBtn = bar.querySelector('[data-copy-field="tp"]');

    const ent = toNum(plan?.entry);
    const sl = toNum(plan?.sl);
    const be = exits?.be ?? null;
    const tp = exits?.safeTp ?? toNum(plan?.tp ?? plan?.tp1);

    if (eBtn) eBtn.textContent = ent !== null ? `E ${ent.toFixed(1)}` : 'E --';
    if (sBtn) sBtn.textContent = sl !== null ? `SL ${sl.toFixed(1)}` : 'SL --';
    if (bBtn) bBtn.textContent = be !== null ? `BE ${be.toFixed(1)}` : 'BE --';
    if (tBtn) tBtn.textContent = tp !== null ? `TP ${tp.toFixed(1)}` : 'TP1 --';
  }

  _switchTimeframe(tf) {
    if (!tf) return;
    const target =
      document.querySelector(`button[data-tf="${tf}"]`) ||
      document.querySelector(`button[data-interval="${tf}"]`) ||
      Array.from(document.querySelectorAll('button')).find(
        (b) => !b.closest('#nsTfScannerBar') && b.textContent.trim().toLowerCase() === tf.toLowerCase()
      );
    if (target) target.click();
    else bus.emit('interval:changed', { interval: tf });
  }

  _renderTfScanner(scanner) {
    const bar = $('nsTfScannerBar');
    const bestEl = $('nsScannerBest');
    if (!bar) return;

    const items = scanner?.items || [];
    if (!items.length) return;

    bar.innerHTML = items.map((it) => {
      const isActive = String(it.tf).toLowerCase() === String(this.lastInterval).toLowerCase();
      const ring = isActive ? 'ring-1 ring-yellow-300' : '';
      let cls = 'bg-white/[.05] border-white/10 text-gray-300';
      let tag = 'WAIT';

      if (it.side === 'BUY') {
        cls = it.is_sniper
          ? 'bg-emerald-500/25 border-emerald-400/60 text-emerald-200'
          : 'bg-emerald-500/15 border-emerald-400/35 text-emerald-300';
        tag = `B•${it.grade}`;
      } else if (it.side === 'SELL') {
        cls = it.is_sniper
          ? 'bg-rose-500/25 border-rose-400/60 text-rose-200'
          : 'bg-rose-500/15 border-rose-400/35 text-rose-300';
        tag = `S•${it.grade}`;
      }

      return `<button type="button" data-scan-tf="${escHtml(it.tf)}" class="rounded-lg border px-1 py-0.5 text-center leading-tight active:scale-95 transition ${cls} ${ring}">
        <div class="text-[8px] font-black">${escHtml(it.label)}</div>
        <div class="text-[7.5px] font-bold opacity-95">${escHtml(tag)}</div>
      </button>`;
    }).join('');

    if (bestEl) {
      const best = scanner.best;
      if (best) {
        bestEl.textContent = `ល្អបំផុត៖ ${best.label} ${best.side} (${best.grade})`;
        bestEl.className = `font-black ${best.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`;
      } else {
        bestEl.textContent = 'គ្មានសញ្ញាខ្លាំង';
        bestEl.className = 'font-bold text-gray-400';
      }
    }
  }

  _updateCandleTimer(text, remSec) {
    this.lastRemSec = remSec;
    this.lastRemText = text;

    if (isForexWeekendUTC()) {
      this._updateWeekendCountdownTick();
      return;
    }

    if ($('skTimer')) {
      $('skTimer').textContent = text;
      $('skTimer').className = remSec <= 15
        ? 'px-1.5 py-0.5 rounded bg-rose-500/30 text-[9px] font-black text-rose-200 animate-pulse'
        : 'px-1.5 py-0.5 rounded bg-sky-500/20 text-[9px] font-black text-sky-300';
    }

    const el = $('nsCandleCountdown');
    if (el) {
      el.textContent = `ទៀន ${text}`;
      el.className = remSec <= 15
        ? 'px-1.5 py-0.5 rounded bg-rose-500/25 border border-rose-400/50 text-[9px] font-black text-rose-300 animate-pulse'
        : 'px-1.5 py-0.5 rounded bg-sky-500/20 border border-sky-400/35 text-[9px] font-black text-sky-300';
    }

    const prep = $('nsPrepCountdown');
    if (prep) {
      const b = this.lastBrain;
      const canTrade = Boolean(b?.can_trade && (b.side === 'BUY' || b.side === 'SELL'));
      if (canTrade && remSec <= 30) {
        prep.textContent = `🔥 ត្រៀមចូល ${b.side}! នៅសល់ ${remSec} វិនាទីទៀតបិទទៀន ${this.lastInterval} បញ្ជាក់`;
        prep.className = 'rounded-lg bg-amber-500/25 border border-amber-400/60 px-2.5 py-1 text-[10px] font-black text-amber-200 text-center tabular-nums leading-snug animate-pulse';
      } else if (canTrade) {
        prep.textContent = `⏱️ ត្រៀម ${b.side} (${b.grade}) • នៅសល់ ${text} បិទទៀន ${this.lastInterval}`;
        prep.className = 'rounded-lg bg-emerald-500/15 border border-emerald-400/40 px-2.5 py-1 text-[10px] font-bold text-emerald-200 text-center tabular-nums leading-snug';
      } else {
        prep.textContent = `⏱️ រាប់វិនាទីទៀន ${this.lastInterval}៖ នៅសល់ ${text} (ចាំបិទទៀនបញ្ជាក់មុនចូល)`;
        prep.className = 'rounded-lg bg-sky-500/12 border border-sky-400/30 px-2.5 py-1 text-[10px] font-bold text-sky-200 text-center tabular-nums leading-snug';
      }
    }
  }

  _onOrderBookDepth(d) {
    this.depth = d;
    if (!this.visible || isForexWeekendUTC()) return;
    const txt = $('nsDepthTxt');
    if (txt) {
      txt.textContent = `B ${d.bidPct}% / A ${d.askPct}%`;
      txt.className = `font-black tabular-nums ${d.bidPct >= 60 ? 'text-emerald-400' : d.askPct >= 60 ? 'text-rose-400' : 'text-gray-200'}`;
    }
  }

  _onLiveTick(price) {
    if (!Number.isFinite(price) || price <= 0) return;
    this.lastPrice = price;

    if (isForexWeekendUTC()) return;

    const now = Date.now();
    this.tickHistory.push({ ts: now, price });
    while (this.tickHistory.length > 2 && now - this.tickHistory[0].ts > 10000) {
      this.tickHistory.shift();
    }

    if (!this.visible && !this.sidekickPinned) return;
    this._updateLiveExitTiming(price);
    this._renderEntryGuardAndLiveRR(price);
    this._updateRealAiBadge();
  }

  _getVelocity10s(price) {
    if (this.tickHistory.length < 2) return 0;
    return price - this.tickHistory[0].price;
  }

  _updateLiveExitTiming(price) {
    const vel = this._getVelocity10s(price);
    const velEl = $('nsVelocity');
    if (velEl) {
      velEl.textContent = `${vel >= 0 ? '+' : ''}$${vel.toFixed(2)}`;
      velEl.className = vel >= 0.35 ? 'text-emerald-400 font-black' : vel <= -0.35 ? 'text-rose-400 font-black' : 'text-gray-200 font-bold';
    }

    const pnlEl = $('nsLivePnlBadge');
    const advEl = $('nsTimingAdvisor');
    if (!pnlEl || !advEl) return;

    const b = this.lastBrain;
    const canTrade = Boolean(b?.can_trade && (b.side === 'BUY' || b.side === 'SELL') && b.plan);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const lotMult = this.auditLot * 10.0;

    if (!canTrade) {
      const plan = b?.preview_plan || this.lastSetup;
      const ent = toNum(plan?.entry);
      pnlEl.textContent = `$${price.toFixed(2)}`;
      pnlEl.className = 'font-black text-sky-300';

      if (ent !== null) {
        const dist = Math.abs(price - ent);
        const dir = price < ent ? 'ឡើងដល់' : 'ចុះដល់';
        advEl.textContent = `⏳ រង់ចាំតម្លៃ${dir} $${ent.toFixed(2)} (សល់ $${dist.toFixed(2)}) • Order Book ${this.depth.bidPct >= 55 ? 'ទិញឈ្នះ' : this.depth.askPct >= 55 ? 'លក់ឈ្នះ' : 'ស្មើគ្នា'}`;
        advEl.className = 'rounded-lg bg-white/[.04] border border-white/10 px-2.5 py-1.5 text-[10px] font-bold text-gray-200 text-center leading-snug';
      }
      return;
    }

    const side = b.side;
    const entry = Number(b.plan.entry);
    const sl = Number(b.plan.sl);
    const exits = this._computeExitTargets(b.plan, side);
    const tp1 = exits?.safeTp ?? Number(b.plan.tp ?? b.plan.tp1);
    const tpDist = Math.max(0.5, Math.abs(tp1 - entry));
    const slDist = Math.max(0.5, Math.abs(entry - sl));

    const fav = side === 'BUY' ? (price - entry) : (entry - price);
    const netPips = (fav * 10.0) - costPips;
    const netUsd = netPips * lotMult;
    const progPct = (fav / tpDist) * 100;
    const velFav = side === 'BUY' ? vel : -vel;
    const bookAgainst = side === 'BUY' ? this.depth.askPct >= 62 : this.depth.bidPct >= 62;

    pnlEl.textContent = `Net ${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)}`;
    pnlEl.className = `font-black ${netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'}`;

    if (fav >= tpDist) {
      advEl.textContent = `🎯 ដល់គោលដៅ $${tp1.toFixed(2)} (+$${netUsd.toFixed(2)}) — បិទយកចំណេញ 60%–100% ឥឡូវកុំឱ្យបត់វិញ!`;
      advEl.className = 'rounded-lg bg-emerald-500/25 border border-emerald-400/60 px-2.5 py-1.5 text-[10px] font-black text-emerald-200 text-center leading-snug animate-pulse';
    } else if (progPct >= 50) {
      advEl.textContent = `🛡️ ចំណេញ +$${netUsd.toFixed(2)} (${Math.round(progPct)}% TP1) — រំកិល SL ស្មើដើម $${entry.toFixed(2)} ភ្លាម!`;
      advEl.className = 'rounded-lg bg-emerald-500/20 border border-emerald-400/45 px-2.5 py-1.5 text-[10px] font-black text-emerald-300 text-center leading-snug';
    } else if (netUsd > 0.35 && (velFav <= -0.35 || bookAgainst)) {
      advEl.textContent = `💰 ចំណេញ +$${netUsd.toFixed(2)} តែកម្លាំងចាប់ផ្តើមបត់ — បិទយកចំណេញមុនកុំឱ្យខាតចំណេញ!`;
      advEl.className = 'rounded-lg bg-amber-500/20 border border-amber-400/50 px-2.5 py-1.5 text-[10px] font-black text-amber-200 text-center leading-snug';
    } else if ((-fav) >= slDist * 0.55) {
      advEl.textContent = `⚠️ ដើរបញ្ច្រាស (-$${Math.abs(netUsd).toFixed(2)}) កៀក SL $${sl.toFixed(2)} — គោរពវិន័យកាត់ខាត!`;
      advEl.className = 'rounded-lg bg-rose-500/25 border border-rose-500/55 px-2.5 py-1.5 text-[10px] font-black text-rose-200 text-center leading-snug animate-pulse';
    } else {
      const toTp = Math.max(0, tpDist - fav);
      advEl.textContent = `🚀 កំពុងរត់ ${side} • សល់ $${toTp.toFixed(2)} ដល់ TP1 $${tp1.toFixed(2)} (Trail Lock $${exits?.trailLock?.toFixed(2) ?? sl.toFixed(2)})`;
      advEl.className = 'rounded-lg bg-sky-500/15 border border-sky-400/30 px-2.5 py-1.5 text-[10px] font-bold text-sky-200 text-center leading-snug';
    }
  }

  setVisible(flag, persist = true) {
    this.visible = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.showScanner = this.visible;
      Store.savePrefs(prefs);
    }

    const chk = $('chkScanner');
    if (chk) chk.checked = this.visible;
    this.el?.classList.toggle('hidden', !this.visible);

    if (!this.visible && !this.sidekickPinned) {
      this.sidekickEl?.classList.add('hidden');
      this._syncSidekickBtnStyle();
    }

    if (this.visible) {
      this._fitMaxHeight();
      if (isForexWeekendUTC()) {
        this._renderWeekendStandby(this.lastData);
      } else {
        this._onOrderBookDepth(directFeed.getDepth());
        if (this.lastPrice) {
          this._updateLiveExitTiming(this.lastPrice);
          this._renderEntryGuardAndLiveRR(this.lastPrice);
        }
        this._updateRealAiBadge();
      }
      requestAnimationFrame(() => {
        this._clampPosition();
        this._positionSidekick();
      });
    }

    bus.emit('radar:toggled', { visible: this.visible });
  }

  update(data) {
    const brain = data?.brain;
    if (!brain) return;
    this.lastData = data;
    this.lastBrain = brain;
    this.lastSetup = data.setup || null;

    // បើទីផ្សារបិទចុងសប្តាហ៍ -> បង្កកទិន្នន័យចាស់ទាំងអស់ និងបង្ហាញផ្ទាំងរង់ចាំផ្សារបើក
    if (isForexWeekendUTC()) {
      this._renderWeekendStandby(data);
      return;
    }

    // ផ្សារបើកវិញ -> បើកបង្ហាញប្រអប់ជួញដូរទាំងអស់ឡើងវិញដោយស្វ័យប្រវត្តិ
    $('nsEntryGuardBox')?.classList.remove('hidden');
    $('nsTimingAdvisor')?.classList.remove('hidden');
    $('nsCopyBar')?.classList.remove('hidden');

    this._trackRealSignalChange();

    const sh = brain.exit_shield;

    if ($('nsAction')) {
      $('nsAction').textContent = brain.sniper_text_km || brain.action_km || 'កំពុងវិភាគ...';
    }
    if ($('nsExit')) {
      $('nsExit').textContent = sh ? `${sh.title_km} — ${sh.action_km}` : 'ប្រព័ន្ធការពារសកម្ម';
    }

    const cbEl = $('nsCircuitBanner');
    const cb = brain.circuit_breaker;
    if (cbEl) {
      if (cb?.active && cb.reason_km) {
        cbEl.textContent = cb.reason_km;
        cbEl.classList.remove('hidden');
      } else {
        cbEl.classList.add('hidden');
      }
    }

    const vCard = $('nsVerdictCard');
    const actEl = $('nsAction');
    if (vCard && actEl) {
      if (brain.can_trade && brain.side === 'BUY') {
        vCard.className = 'rounded-xl bg-emerald-500/15 border border-emerald-400/50 p-2 text-center space-y-1';
        actEl.className = 'text-[11.5px] font-black text-emerald-300';
      } else if (brain.can_trade && brain.side === 'SELL') {
        vCard.className = 'rounded-xl bg-rose-500/15 border border-rose-500/50 p-2 text-center space-y-1';
        actEl.className = 'text-[11.5px] font-black text-rose-300';
      } else if (sh?.level === 'DANGER' || brain.is_danger) {
        vCard.className = 'rounded-xl bg-rose-600/20 border border-rose-500/60 p-2 text-center space-y-1';
        actEl.className = 'text-[11.5px] font-black text-rose-300';
      } else {
        vCard.className = 'rounded-xl bg-black/55 border border-yellow-400/35 p-2 text-center space-y-1';
        actEl.className = 'text-[11.5px] font-black text-yellow-300';
      }
    }

    const q = data.quant || brain.quant || {};
    const smt = data.smt || brain.smt || {};
    const qLine = $('nsQuantTextLine');
    if (qLine) {
      const vwapTxt = q.vwap ? `$${Number(q.vwap).toFixed(1)}` : '--';
      const pocTxt = q.poc ? `$${Number(q.poc).toFixed(1)}` : '--';
      const cvdTxt = q.cvd_divergence && q.cvd_divergence !== 'NONE' ? q.cvd_divergence : `${q.cvd_pct ?? 50}%`;
      const smtTxt = smt.signal === 'BULL_SMT' ? 'SMT▲' : smt.signal === 'BEAR_SMT' ? 'SMT▼' : (smt.silver_px ? `Ag$${Number(smt.silver_px).toFixed(1)}` : 'ស្មើ');
      const sqTxt = q.squeeze_state === 'FIRING' ? '🔥ផ្ទុះ' : (q.squeeze_state === 'COILING' ? '⚡សន្សំ' : 'ធម្មតា');
      qLine.textContent = `VWAP ${vwapTxt} • POC ${pocTxt} • CVD ${cvdTxt} • ${sqTxt} • ${smtTxt}`;
    }

    this._renderEnhancedGuide(brain.beginner_guide);
    if (data.tf_scanner) this._renderTfScanner(data.tf_scanner);

    this._updateCopyButtons();
    this._updateProjectedPnl();
    this._updateCandleTimer(this.lastRemText, this.lastRemSec);

    const px = this.lastPrice ?? toNum(data.price);
    if (px) {
      this._updateLiveExitTiming(px);
      this._renderEntryGuardAndLiveRR(px);
    }
    this._updateRealAiBadge();
  }

  _renderEnhancedGuide(bg) {
    const listEl = $('nsGuideList');
    if (!listEl) return;

    const sess = getCambodiaSessionGuide();
    const plan = this._getActivePlan();
    const side = this._inferSide();
    const exits = this._computeExitTargets(plan, side);

    const cfg = {
      golden: { sym: '⏰', bg: 'bg-amber-500/20 text-amber-300 border-amber-400/45', hl: 'text-amber-300' },
      scalp: { sym: '⚡', bg: 'bg-cyan-500/20 text-cyan-300 border-cyan-400/40', hl: 'text-cyan-300' },
      profit: { sym: '🎯', bg: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/45', hl: 'text-emerald-300' },
      range: { sym: '↔', bg: 'bg-violet-500/20 text-violet-300 border-violet-400/35', hl: 'text-violet-300' },
      up: { sym: '↗', bg: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/35', hl: 'text-emerald-300' },
      down: { sym: '↘', bg: 'bg-rose-500/20 text-rose-300 border-rose-400/35', hl: 'text-rose-300' },
      target_buy: { sym: '◎', bg: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/35', hl: 'text-emerald-300' },
      target_sell: { sym: '◎', bg: 'bg-rose-500/20 text-rose-300 border-rose-400/35', hl: 'text-rose-300' },
      avoid: { sym: '⊘', bg: 'bg-rose-500/20 text-rose-300 border-rose-400/35', hl: 'text-rose-300' },
      stop: { sym: '!', bg: 'bg-amber-500/20 text-amber-300 border-amber-400/35', hl: 'text-amber-300' },
    };

    const rows = [];

    rows.push({ icon: 'golden', prefix: '', hl: sess.bigProfitKm, suffix: '' });
    rows.push({ icon: 'scalp', prefix: '', hl: '', suffix: sess.scalpKm });

    if (exits) {
      if (exits.side === 'BUY') {
        rows.push({
          icon: 'profit',
          prefix: 'បើទិញ (BUY) អាចឡើងដល់',
          hl: `$${exits.safeTp.toFixed(2)} (អតិបរមា $${exits.maxReach.toFixed(2)})`,
          suffix: `— ដល់ $${exits.be.toFixed(2)} រំកិល SL ស្មើដើម • បើថយបិទក្រោម $${exits.trailLock.toFixed(2)} បិទយកចំណេញភ្លាមកុំឱ្យធ្លាក់វិញ!`,
        });
      } else {
        rows.push({
          icon: 'profit',
          prefix: 'បើលក់ (SELL) អាចចុះដល់',
          hl: `$${exits.safeTp.toFixed(2)} (អតិបរមា $${exits.maxReach.toFixed(2)})`,
          suffix: `— ដល់ $${exits.be.toFixed(2)} រំកិល SL ស្មើដើម • បើងើបបិទលើ $${exits.trailLock.toFixed(2)} បិទយកចំណេញភ្លាមកុំឱ្យឡើងវិញ!`,
        });
      }
    }

    if (Array.isArray(bg?.rows)) {
      for (const r of bg.rows) rows.push(r);
    }

    listEl.innerHTML = rows.map((r) => {
      const c = cfg[r.icon] || cfg.range;
      return `<div class="flex items-start gap-2 rounded-xl bg-white/[.035] border border-white/10 px-2.5 py-1.5 text-[10.5px] leading-snug"><span class="w-5 h-5 mt-0.5 rounded-lg border ${c.bg} flex items-center justify-center font-black text-[10px] shrink-0">${c.sym}</span><div class="text-gray-100 font-semibold min-w-0">${escHtml(r.prefix || '')} ${r.hl ? `<b class="${c.hl} font-black tabular-nums">${escHtml(r.hl)}</b>` : ''} ${escHtml(r.suffix || '')}</div></div>`;
    }).join('');

    if ($('nsGuideFooter')) {
      $('nsGuideFooter').textContent = bg?.footer_km || 'ទៀនបិទ = ទៀនចប់ពេញលេញ មិនមែនគ្រាន់តែប៉ះ • គណនាផ្ទាល់ពីទីផ្សារពិត';
    }
  }

  _updateProjectedPnl() {
    const el = $('nsProjPnl');
    if (!el) return;
    const plan = this._getActivePlan();
    if (!plan || toNum(plan.entry) === null || toNum(plan.sl) === null) {
      el.textContent = '--';
      return;
    }

    const side = this._inferSide();
    const exits = this._computeExitTargets(plan, side);
    const entry = Number(plan.entry);
    const sl = Number(plan.sl);
    const tp = exits?.safeTp ?? Number(plan.tp ?? plan.tp1);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const winPips = Math.max(0, Math.round((Math.abs(tp - entry) * 10) - costPips));
    const lossPips = Math.round((Math.abs(entry - sl) * 10) + costPips);
    const mult = this.auditLot * 10.0;

    el.textContent = `Lot ${this.auditLot.toFixed(2)} (Net) ៖ TP1 +$${(winPips * mult).toFixed(2)} | SL -$${(lossPips * mult).toFixed(2)}`;
  }
}

export const aiRadarWidget = new AiRadarWidget();