import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';

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
  const daysToAdd = day === 5 ? 2 : (day === 6 ? 1 : 0);
  target.setUTCDate(now.getUTCDate() + daysToAdd);
  target.setUTCHours(22, 0, 0, 0);

  const diffSec = Math.max(0, Math.floor((target.getTime() - now.getTime()) / 1000));
  const hrs = Math.floor(diffSec / 3600);
  const mins = Math.floor((diffSec % 3600) / 60);
  const secs = diffSec % 60;
  return `${String(hrs).padStart(2, '0')}ម៉ោង ${String(mins).padStart(2, '0')}នាទី ${String(secs).padStart(2, '0')}វិនាទី`;
}

function getKillzoneMini() {
  const now = new Date();
  const kh = new Date(now.getTime() + 7 * 3600 * 1000);
  const khH = kh.getUTCHours();
  const khM = kh.getUTCMinutes();
  const hm = khH + khM / 60;
  const tm = `${String(khH).padStart(2, '0')}:${String(khM).padStart(2, '0')}`;

  if (isForexWeekendUTC()) {
    return { label: `បិទចុងសប្តាហ៍ (${tm})`, tone: 'warn', tip: 'បើកវិញច័ន្ទ 05:00 • ម៉ោងមាស 14:00 & 19:20' };
  }
  if (hm >= 19.33 && hm < 23.0) {
    return { label: `🔥 ម៉ោងមាស NY (${tm}) • 90%`, tone: 'golden', tip: 'COMEX + NY Overlap (19:20–23:00) ចំណេញធំ!' };
  }
  if (hm >= 14.0 && hm < 17.5) {
    return { label: `🔥 ម៉ោងមាស LDN (${tm}) • 78%`, tone: 'golden', tip: 'London Open (14:00–17:30) និន្នាការច្បាស់!' };
  }
  if (hm >= 2.5 && hm < 7.0) {
    return { label: `🚫 ម៉ោងងាប់ (${tm}) • 15%`, tone: 'danger', tip: '02:30–07:00 Spread រីក — ហាមចូល!' };
  }
  if (hm >= 7.0 && hm < 14.0) {
    return { label: `⏳ វគ្គអាស៊ី (${tm}) • Scalp`, tone: 'warn', tip: 'លេងតិចៗតែគែម OB • ម៉ោងធំ 14:00' };
  }
  return { label: `⏳ សម្រាក (${tm})`, tone: 'warn', tip: 'ម៉ោងមាសបន្ទាប់៖ 19:20 (NY) ឬ 14:00 (LDN)' };
}

class HudWidget {
  constructor() {
    this.el = null;
    this.visible = true;
    this.collapsed = false;
    this.auditLot = 0.01;
    this.lastData = null;
    this.lastBrain = null;
    this.lastPlan = null;
    this.lastSide = 'WAIT';
    this.lastPrice = null;
    this.lastInterval = '5m';
    this.lastRemText = '--:--';
    this.lastRemSec = 60;
    this.wasWeekend = isForexWeekendUTC();
    this.weekendTimer = null;
  }

  init(prefs = {}) {
    this.el = $('chartHud');
    if (!this.el) return;

    this.visible = prefs.showHud !== false;
    this.collapsed = Boolean(prefs.hudCollapsed);
    this.auditLot = Number(prefs.auditLot) || 0.01;

    this._renderShell();
    this._initSmartDrag();
    this._bindEvents();

    this.setVisible(this.visible, false);
    this.setCollapsed(this.collapsed, false);

    if (isForexWeekendUTC()) {
      this._renderWeekendStandby(this.lastData);
    }

    if (this.weekendTimer) clearInterval(this.weekendTimer);
    this.weekendTimer = setInterval(() => {
      const wknd = isForexWeekendUTC();
      if (wknd) {
        this._updateWeekendCountdownTick();
      } else if (this.wasWeekend && !wknd) {
        this.wasWeekend = false;
        if (this.lastData) this.update(this.lastData);
      }
      this.wasWeekend = wknd;
    }, 1000);
  }

  _renderShell() {
    this.el.className = 'absolute top-2.5 left-2.5 z-[25] w-[258px] sm:w-[274px] rounded-2xl bg-[#0a1120]/94 backdrop-blur-xl border border-white/12 border-l-[3px] border-l-amber-400 shadow-[0_14px_40px_rgba(0,0,0,.65)] overflow-hidden select-none transition-colors';
    this.el.innerHTML = `
      <!-- Header -->
      <div id="hudDragBar" class="flex items-center justify-between gap-1.5 px-2.5 py-2 border-b border-white/10 cursor-grab active:cursor-grabbing" style="touch-action:none;">
        <div class="flex items-center gap-1.5 min-w-0">
          <span class="text-gray-500 text-xs leading-none shrink-0">⋮⋮</span>
          <span id="hudDot" class="w-2 h-2 rounded-full bg-amber-400 shrink-0"></span>
          <span id="hudStatusText" class="text-xs font-black text-amber-300 truncate">រង់ចាំ</span>
          <span id="hudGradePill" class="px-1.5 py-0.5 rounded bg-white/10 text-[9px] font-black text-gray-200 tabular-nums shrink-0">--</span>
        </div>
        <div class="flex items-center gap-1 shrink-0 tabular-nums">
          <span id="hudTimer" class="px-1.5 py-0.5 rounded bg-sky-500/20 text-[9px] font-black text-sky-300">--:--</span>
          <button id="hudCollapseBtn" type="button" title="បង្រួម/ពង្រីក" class="w-6 h-6 rounded-lg bg-white/8 hover:bg-white/18 text-gray-200 text-[10px] font-black flex items-center justify-center active:scale-95 transition">⌃</button>
        </div>
      </div>

      <!-- Compact Collapsed Strip -->
      <div id="hudMiniStrip" class="hidden px-2.5 py-1.5 text-[10px] font-bold tabular-nums flex items-center justify-between gap-2 bg-black/25">
        <span id="hudMiniSl" class="text-rose-400">SL --</span>
        <span id="hudMiniTp" class="text-emerald-400">TP --</span>
      </div>

      <!-- Full Body -->
      <div id="hudBody" class="p-2.5 space-y-2 text-[11px]">
        <!-- Weekend Standby Section (បង្ហាញតែថ្ងៃបិទផ្សារ) -->
        <div id="hudWeekendBox" class="hidden space-y-2">
          <div class="rounded-xl bg-amber-500/12 border border-amber-400/40 px-2.5 py-2 text-center space-y-1">
            <div class="text-[11px] font-black text-amber-300">⏸️ ទីផ្សារបិទចុងសប្តាហ៍</div>
            <div id="hudWkndCountdown" class="text-[10px] font-black text-amber-100 tabular-nums">⏳ រង់ចាំផ្សារបើកវិញ...</div>
          </div>
          <div class="space-y-1 text-[10px] leading-snug">
            <div class="flex items-center justify-between text-gray-300 tabular-nums">
              <span>តម្លៃបិទថ្ងៃសុក្រ៖</span>
              <b id="hudWkndPrice" class="text-white font-black">--</b>
            </div>
            <div class="rounded-lg bg-white/[.04] border border-white/10 px-2 py-1.5 text-gray-200 font-semibold">
              ⏰ បើកវិញថ្ងៃច័ន្ទ <b class="text-amber-300">05:00 ព្រឹក</b> — ទិន្នន័យ Entry / SL / TP នឹងបញ្ចូលស្វ័យប្រវត្តិពេលផ្សារបើក។
            </div>
            <div class="rounded-lg bg-emerald-500/10 border border-emerald-400/25 px-2 py-1.5 text-emerald-200 font-semibold">
              🔥 ម៉ោងមាសថ្ងៃច័ន្ទ៖ <b class="text-emerald-300">14:00–17:30</b> & <b class="text-emerald-300">19:20–23:00</b>
            </div>
          </div>
        </div>

        <!-- Live Trading Section (បង្ហាញតែពេលផ្សារបើក) -->
        <div id="hudLiveBox" class="space-y-2">
          <!-- Killzone Timing Line -->
          <div class="flex items-center justify-between gap-1 text-[9.5px] pb-1.5 border-b border-white/10">
            <span id="hudKzBadge" class="font-black text-amber-300 truncate">--</span>
            <span id="hudLivePnl" class="font-black text-sky-300 tabular-nums shrink-0">--</span>
          </div>

          <!-- Click-to-Copy Price Rows -->
          <div id="hudPriceRows" class="space-y-1 tabular-nums">
            <div data-hud-copy="entry" title="ចុច Copy Entry" class="flex items-center justify-between gap-1 cursor-pointer hover:bg-white/[.05] rounded px-1 py-0.5 -mx-1 transition">
              <span class="text-gray-400 text-[10.5px]">Entry</span>
              <div class="flex items-center gap-1.5">
                <span id="hudEntry" class="font-black text-white">--</span>
                <span id="hudEntryTag" class="px-1.5 py-0.5 rounded text-[9px] font-black bg-white/10 text-gray-300">WAIT</span>
              </div>
            </div>
            <div data-hud-copy="sl" title="ចុច Copy Stop Loss" class="flex items-center justify-between gap-1 cursor-pointer hover:bg-white/[.05] rounded px-1 py-0.5 -mx-1 transition">
              <span class="text-gray-400 text-[10.5px]">Stop Loss</span>
              <span id="hudSl" class="font-black text-rose-400">--</span>
            </div>
            <div data-hud-copy="be" title="ចុច Copy BE (រំកិលស្មើដើម)" class="flex items-center justify-between gap-1 cursor-pointer hover:bg-white/[.05] rounded px-1 py-0.5 -mx-1 transition">
              <span class="text-sky-300/90 text-[10.5px]">រំកិល BE</span>
              <span id="hudBe" class="font-black text-sky-300">--</span>
            </div>
            <div data-hud-copy="tp1" title="ចុច Copy TP1 (សុវត្ថិភាព)" class="flex items-center justify-between gap-1 cursor-pointer hover:bg-white/[.05] rounded px-1 py-0.5 -mx-1 transition">
              <span class="text-gray-400 text-[10.5px]">TP 1 (កាត់ 50%)</span>
              <span id="hudTp1" class="font-black text-emerald-400">--</span>
            </div>
            <div data-hud-copy="tp2" title="ចុច Copy TP2" class="flex items-center justify-between gap-1 cursor-pointer hover:bg-white/[.05] rounded px-1 py-0.5 -mx-1 transition">
              <span class="text-gray-400 text-[10.5px]">TP 2 (អតិបរមា)</span>
              <span id="hudTp2" class="font-black text-emerald-300">--</span>
            </div>
          </div>

          <!-- R:R + Safe Lot Line -->
          <div class="flex items-center justify-between gap-1 pt-1.5 border-t border-white/10 text-[10px] tabular-nums">
            <span class="text-gray-400">R : R <b id="hudRr" class="text-sky-300 ml-1">--</b></span>
            <span id="hudSafeLot" class="text-amber-200/95 font-bold">1% Lot: --</span>
          </div>

          <!-- Live SL -> TP1 Progress Bar -->
          <div class="space-y-1">
            <div class="relative w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
              <div id="hudProgFill" class="h-full bg-sky-400 transition-all duration-150" style="width:50%"></div>
            </div>
            <div class="flex items-center justify-between text-[9px] font-bold text-gray-400 tabular-nums">
              <span>SL</span>
              <span id="hudProgLabel" class="text-gray-200">WAIT</span>
              <span>TP1</span>
            </div>
          </div>

          <!-- Full Readable Reason & Exit Shield -->
          <div id="hudReasonBox" class="rounded-xl px-2.5 py-1.5 border border-white/10 bg-white/[.04] text-[10px] font-bold text-gray-100 leading-snug">
            ⏳ កំពុងវិភាគទីផ្សារមាសផ្ទាល់...
          </div>
          <div id="hudShieldBox" class="rounded-xl px-2.5 py-1.5 border border-emerald-400/25 bg-emerald-500/10 text-[9.5px] font-bold text-emerald-200 leading-snug">
            🛡️ ប្រព័ន្ធការពារសកម្ម • ចុចលើលេខដើម្បី Copy ចូល MT5
          </div>
        </div>
      </div>
    `;

    $('hudCollapseBtn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleCollapse();
    });

    $('hudPriceRows')?.addEventListener('click', (e) => {
      const row = e.target.closest('[data-hud-copy]');
      if (!row) return;
      e.stopPropagation();
      this._copyField(row.dataset.hudCopy, row);
    });
  }

  _updateWeekendCountdownTick() {
    const remStr = getTimeUntilMarketOpenKm();
    if ($('hudWkndCountdown')) $('hudWkndCountdown').textContent = `⏳ នៅសល់ ${remStr}`;
    const tEl = $('hudTimer');
    if (tEl) {
      tEl.textContent = 'ផ្សារបិទ';
      tEl.className = 'px-1.5 py-0.5 rounded bg-rose-500/20 text-[9px] font-black text-rose-300';
    }
  }

  _renderWeekendStandby(data) {
    const px = this.lastPrice ?? toNum(data?.price);
    const pxTxt = px ? `$${px.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';

    $('hudWeekendBox')?.classList.remove('hidden');
    $('hudLiveBox')?.classList.add('hidden');

    this.el?.classList.remove('border-l-emerald-400', 'border-l-rose-500', 'border-l-rose-400');
    this.el?.classList.add('border-l-amber-400');

    if ($('hudDot')) $('hudDot').className = 'w-2 h-2 rounded-full bg-amber-400 shrink-0';
    if ($('hudStatusText')) {
      $('hudStatusText').textContent = 'ផ្សារបិទ';
      $('hudStatusText').className = 'text-xs font-black text-amber-300 truncate';
    }
    if ($('hudGradePill')) {
      $('hudGradePill').textContent = 'CLOSED';
      $('hudGradePill').className = 'px-1.5 py-0.5 rounded bg-amber-400/20 text-[9px] font-black text-amber-200 shrink-0';
    }
    if ($('hudWkndPrice')) $('hudWkndPrice').textContent = pxTxt;
    if ($('hudMiniSl')) $('hudMiniSl').textContent = `បិទផ្សារ ${pxTxt}`;
    if ($('hudMiniTp')) $('hudMiniTp').textContent = 'ច័ន្ទ 05:00';

    this._updateWeekendCountdownTick();
  }

  _initSmartDrag() {
    const stage = $('chartStage');
    const el = this.el;
    if (!el || !stage) return;

    let dragging = false;
    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let startL = 0;
    let startT = 0;

    el.addEventListener('pointerdown', (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      if (e.target.closest('button, [data-hud-copy]')) return;

      const stageRect = stage.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      startL = elRect.left - stageRect.left;
      startT = elRect.top - stageRect.top;
      startX = e.clientX;
      startY = e.clientY;
      pointerId = e.pointerId;
      dragging = true;

      el.style.right = 'auto';
      el.style.bottom = 'auto';
      el.style.left = `${Math.round(startL)}px`;
      el.style.top = `${Math.round(startT)}px`;
      try { el.setPointerCapture(e.pointerId); } catch {}
    });

    el.addEventListener('pointermove', (e) => {
      if (!dragging || e.pointerId !== pointerId) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      const maxL = Math.max(4, stage.clientWidth - el.offsetWidth - 4);
      const maxT = Math.max(4, stage.clientHeight - el.offsetHeight - 4);
      el.style.left = `${Math.round(Math.max(4, Math.min(maxL, startL + dx)))}px`;
      el.style.top = `${Math.round(Math.max(4, Math.min(maxT, startT + dy)))}px`;
    });

    const endDrag = (e) => {
      if (e.pointerId !== pointerId) return;
      dragging = false;
      pointerId = null;
      try { el.releasePointerCapture(e.pointerId); } catch {}
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
  }

  _bindEvents() {
    bus.on('interval:changed', ({ interval }) => {
      if (interval) this.lastInterval = interval;
    });
    bus.on('market:full', ({ data }) => {
      if (data?.interval) this.lastInterval = data.interval;
      this.update(data);
    });
    bus.on('price:updated', ({ price }) => this._onLiveTick(price));
    bus.on('candle:countdown', ({ text, remSec }) => this._updateTimer(text, remSec));
    bus.on('chart:resized', () => this._clampPosition());
    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
      if (!isForexWeekendUTC() && this.lastPrice) this._onLiveTick(this.lastPrice);
    });
  }

  setVisible(flag, persist = true) {
    this.visible = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.showHud = this.visible;
      Store.savePrefs(prefs);
    }
    const chk = $('chkHud');
    if (chk) chk.checked = this.visible;
    this.el?.classList.toggle('hidden', !this.visible);
    if (this.visible) requestAnimationFrame(() => this._clampPosition());
    bus.emit('hud:toggled', { visible: this.visible });
  }

  setCollapsed(flag, persist = true) {
    this.collapsed = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.hudCollapsed = this.collapsed;
      Store.savePrefs(prefs);
    }
    $('hudBody')?.classList.toggle('hidden', this.collapsed);
    $('hudMiniStrip')?.classList.toggle('hidden', !this.collapsed);
    const btn = $('hudCollapseBtn');
    if (btn) btn.textContent = this.collapsed ? '⌄' : '⌃';
    requestAnimationFrame(() => this._clampPosition());
  }

  toggleCollapse() {
    this.setCollapsed(!this.collapsed, true);
  }

  _updateTimer(text, remSec) {
    this.lastRemText = text;
    this.lastRemSec = remSec;
    if (isForexWeekendUTC()) {
      this._updateWeekendCountdownTick();
      return;
    }
    const el = $('hudTimer');
    if (!el) return;
    el.textContent = text;
    el.className = remSec <= 15
      ? 'px-1.5 py-0.5 rounded bg-rose-500/30 text-[9px] font-black text-rose-200 animate-pulse'
      : 'px-1.5 py-0.5 rounded bg-sky-500/20 text-[9px] font-black text-sky-300';
  }

  _computeSyncedLevels(data, brain) {
    const rawPlan = (brain?.can_trade && brain?.plan) ? brain.plan : (brain?.preview_plan || data?.setup || null);
    if (!rawPlan) return null;

    const entry = toNum(rawPlan.entry);
    const sl = toNum(rawPlan.sl);
    const rawTp1 = toNum(rawPlan.tp ?? rawPlan.tp1);
    if (entry === null || sl === null || rawTp1 === null) return null;

    const side = (brain?.side === 'BUY' || brain?.side === 'SELL')
      ? brain.side
      : (entry > sl ? 'BUY' : 'SELL');

    const risk = Math.max(1.2, Math.abs(entry - sl));
    const smc = data?.smc || {};
    const spread = Math.max(0.1, toNum(data?.spread) ?? directFeed.getSpread());
    const atr = Math.max(0.8, toNum(data?.atr) ?? 2.0);
    const ema20 = toNum(data?.ema20) ?? entry;
    const price = this.lastPrice ?? toNum(data?.price) ?? entry;

    let tp1 = rawTp1;
    const be = side === 'BUY' ? entry + risk * 0.9 : entry - risk * 0.9;
    const tp2 = toNum(rawPlan.tp2) ?? (side === 'BUY' ? entry + risk * 2.6 : entry - risk * 2.6);
    const trail = side === 'BUY'
      ? Math.max(ema20 - atr * 0.45, price - atr * 1.15)
      : Math.min(ema20 + atr * 0.45, price + atr * 1.15);

    if (side === 'BUY') {
      const bearObs = (smc.supply_blocks || []).filter((b) => String(b.label || 'OB').toUpperCase() === 'OB' && toNum(b.bottom) > entry + risk * 0.9);
      const nearest = bearObs.length ? Math.min(...bearObs.map((b) => Number(b.bottom))) : null;
      if (nearest && nearest < tp1) tp1 = nearest - 0.35;
    } else {
      const bullObs = (smc.demand_blocks || []).filter((b) => String(b.label || 'OB').toUpperCase() === 'OB' && toNum(b.top) < entry - risk * 0.9);
      const nearest = bullObs.length ? Math.max(...bullObs.map((b) => Number(b.top))) : null;
      if (nearest && nearest > tp1) tp1 = nearest + 0.35;
    }

    const slPips = Math.round(Math.abs(entry - sl) * 10);
    const tp1Pips = Math.round(Math.abs(tp1 - entry) * 10);
    const tp2Pips = Math.round(Math.abs(tp2 - entry) * 10);
    const rr = (Math.abs(tp1 - entry) / Math.max(0.5, Math.abs(entry - sl))).toFixed(1);

    const effDist = Math.max(Math.abs(entry - sl) + spread, 1.5);
    const lot100 = Math.max(0.01, Math.min(1.0, (100 * 0.01) / (effDist * 100))).toFixed(2);
    const lot500 = Math.max(0.01, Math.min(1.0, (500 * 0.01) / (effDist * 100))).toFixed(2);

    return {
      side,
      entry: Number(entry.toFixed(2)),
      sl: Number(sl.toFixed(2)),
      be: Number(be.toFixed(2)),
      tp1: Number(tp1.toFixed(2)),
      tp2: Number(tp2.toFixed(2)),
      trail: Number(trail.toFixed(2)),
      slPips,
      tp1Pips,
      tp2Pips,
      rr,
      lot100,
      lot500,
      entryState: rawPlan.entry_state || 'IN_ZONE',
    };
  }

  update(data) {
    if (!this.el || !data) return;
    this.lastData = data;
    const brain = data.brain || {};
    this.lastBrain = brain;

    if (isForexWeekendUTC()) {
      this._renderWeekendStandby(data);
      return;
    }

    $('hudWeekendBox')?.classList.add('hidden');
    $('hudLiveBox')?.classList.remove('hidden');

    const lv = this._computeSyncedLevels(data, brain);
    this.lastPlan = lv;
    this.lastSide = brain.can_trade ? brain.side : 'WAIT';

    const dot = $('hudDot');
    const stTxt = $('hudStatusText');
    const grPill = $('hudGradePill');
    const eTag = $('hudEntryTag');

    let borderCls = 'border-l-amber-400';
    if (brain.is_danger || brain.status === 'BLOCKED') {
      borderCls = 'border-l-rose-500';
      if (dot) dot.className = 'w-2 h-2 rounded-full bg-rose-500 animate-pulse shrink-0';
      if (stTxt) { stTxt.textContent = 'ហាមចូល'; stTxt.className = 'text-xs font-black text-rose-400 truncate'; }
      if (grPill) { grPill.textContent = 'RISK'; grPill.className = 'px-1.5 py-0.5 rounded bg-rose-500/25 text-[9px] font-black text-rose-200 shrink-0'; }
      if (eTag) { eTag.textContent = 'ហាមចូល'; eTag.className = 'px-1.5 py-0.5 rounded text-[9px] font-black bg-rose-500/25 text-rose-300'; }
    } else if (brain.can_trade && brain.side === 'BUY') {
      borderCls = 'border-l-emerald-400';
      if (dot) dot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse shrink-0';
      if (stTxt) { stTxt.textContent = 'ទិញ BUY'; stTxt.className = 'text-xs font-black text-emerald-400 truncate'; }
      if (grPill) { grPill.textContent = `${brain.grade} ${brain.confidence_score}%`; grPill.className = 'px-1.5 py-0.5 rounded bg-emerald-500/25 text-[9px] font-black text-emerald-200 shrink-0'; }
      if (eTag) { eTag.textContent = lv?.entryState === 'PULLBACK' ? 'Limit' : 'ចូល BUY'; eTag.className = 'px-1.5 py-0.5 rounded text-[9px] font-black bg-emerald-500/25 text-emerald-300'; }
    } else if (brain.can_trade && brain.side === 'SELL') {
      borderCls = 'border-l-rose-400';
      if (dot) dot.className = 'w-2 h-2 rounded-full bg-rose-400 animate-pulse shrink-0';
      if (stTxt) { stTxt.textContent = 'លក់ SELL'; stTxt.className = 'text-xs font-black text-rose-400 truncate'; }
      if (grPill) { grPill.textContent = `${brain.grade} ${brain.confidence_score}%`; grPill.className = 'px-1.5 py-0.5 rounded bg-rose-500/25 text-[9px] font-black text-rose-200 shrink-0'; }
      if (eTag) { eTag.textContent = lv?.entryState === 'PULLBACK' ? 'Limit' : 'ចូល SELL'; eTag.className = 'px-1.5 py-0.5 rounded text-[9px] font-black bg-rose-500/25 text-rose-300'; }
    } else {
      borderCls = 'border-l-amber-400';
      if (dot) dot.className = 'w-2 h-2 rounded-full bg-amber-400 shrink-0';
      if (stTxt) { stTxt.textContent = 'ត្រៀម'; stTxt.className = 'text-xs font-black text-amber-300 truncate'; }
      if (grPill) { grPill.textContent = `${brain.confidence_score || 45}%`; grPill.className = 'px-1.5 py-0.5 rounded bg-amber-400/20 text-[9px] font-black text-amber-200 shrink-0'; }
      if (eTag) { eTag.textContent = 'រង់ចាំ'; eTag.className = 'px-1.5 py-0.5 rounded text-[9px] font-black bg-white/10 text-gray-300'; }
    }
    this.el.classList.remove('border-l-amber-400', 'border-l-emerald-400', 'border-l-rose-500', 'border-l-rose-400');
    this.el.classList.add(borderCls);

    const kz = getKillzoneMini();
    const kzEl = $('hudKzBadge');
    if (kzEl) {
      kzEl.textContent = kz.label;
      kzEl.className = `font-black truncate ${kz.tone === 'golden' ? 'text-emerald-400' : kz.tone === 'danger' ? 'text-rose-400' : 'text-amber-300'}`;
    }

    if (lv) {
      if ($('hudEntry')) $('hudEntry').textContent = `$${lv.entry.toFixed(2)}`;
      if ($('hudSl')) $('hudSl').textContent = `$${lv.sl.toFixed(2)} (-${lv.slPips})`;
      if ($('hudBe')) $('hudBe').textContent = `$${lv.be.toFixed(2)}`;
      if ($('hudTp1')) $('hudTp1').textContent = `$${lv.tp1.toFixed(2)} (+${lv.tp1Pips})`;
      if ($('hudTp2')) $('hudTp2').textContent = `$${lv.tp2.toFixed(2)} (+${lv.tp2Pips})`;
      if ($('hudRr')) $('hudRr').textContent = `${lv.rr} : 1`;
      if ($('hudSafeLot')) $('hudSafeLot').textContent = `1%: $100→${lv.lot100} | $500→${lv.lot500}`;
      if ($('hudMiniSl')) $('hudMiniSl').textContent = `SL ${lv.sl.toFixed(2)}`;
      if ($('hudMiniTp')) $('hudMiniTp').textContent = `TP ${lv.tp1.toFixed(2)}`;
    }

    const rBox = $('hudReasonBox');
    if (rBox) {
      const reasonTxt = (brain.reasons && brain.reasons[0]) || brain.hud_hint_km || brain.action_km || kz.tip;
      if (brain.is_danger || brain.status === 'BLOCKED') {
        rBox.className = 'rounded-xl px-2.5 py-1.5 border border-rose-500/45 bg-rose-950/55 text-[10px] font-bold text-rose-200 leading-snug';
        rBox.textContent = `⛔ ${reasonTxt}`;
      } else if (brain.can_trade) {
        const cls = brain.side === 'BUY'
          ? 'border-emerald-400/45 bg-emerald-950/45 text-emerald-200'
          : 'border-rose-400/45 bg-rose-950/45 text-rose-200';
        rBox.className = `rounded-xl px-2.5 py-1.5 border text-[10px] font-bold leading-snug ${cls}`;
        rBox.textContent = `⚡ ${brain.action_km || reasonTxt}`;
      } else {
        rBox.className = 'rounded-xl px-2.5 py-1.5 border border-amber-400/35 bg-amber-950/35 text-[10px] font-bold text-amber-100 leading-snug';
        rBox.textContent = `⏳ ${reasonTxt}`;
      }
    }

    const shBox = $('hudShieldBox');
    if (shBox) {
      const sh = brain.exit_shield;
      if (sh?.level === 'DANGER') {
        shBox.className = 'rounded-xl px-2.5 py-1.5 border border-rose-500/50 bg-rose-950/60 text-[9.5px] font-black text-rose-200 leading-snug';
        shBox.textContent = `🚨 ${sh.title_km} — ${sh.action_km}`;
      } else if (lv && brain.can_trade) {
        shBox.className = 'rounded-xl px-2.5 py-1.5 border border-emerald-400/30 bg-emerald-500/10 text-[9.5px] font-bold text-emerald-200 leading-snug';
        shBox.textContent = `🛡️ ដល់ $${lv.be.toFixed(2)} រំកិល BE • ការពារចំណេញ Trail $${lv.trail.toFixed(2)}`;
      } else {
        shBox.className = 'rounded-xl px-2.5 py-1.5 border border-white/10 bg-white/[.03] text-[9.5px] font-semibold text-gray-300 leading-snug';
        shBox.textContent = `🛡️ ${kz.tip}`;
      }
    }

    this._onLiveTick(this.lastPrice ?? toNum(data.price));
  }

  _onLiveTick(price) {
    if (!Number.isFinite(price) || price <= 0) return;
    this.lastPrice = price;

    if (isForexWeekendUTC()) {
      if ($('hudWkndPrice')) $('hudWkndPrice').textContent = `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      return;
    }

    const pnlEl = $('hudLivePnl');
    const fillEl = $('hudProgFill');
    const lblEl = $('hudProgLabel');
    const lv = this.lastPlan;

    if (!lv || !this.lastBrain?.can_trade) {
      if (pnlEl) { pnlEl.textContent = `$${price.toFixed(2)}`; pnlEl.className = 'font-black text-sky-300 tabular-nums shrink-0'; }
      if (fillEl) { fillEl.style.width = '50%'; fillEl.className = 'h-full bg-gray-500/60 transition-all duration-150'; }
      if (lblEl) { lblEl.textContent = this.lastBrain?.is_danger ? 'BLOCKED' : 'WAIT'; lblEl.className = 'text-gray-300'; }
      return;
    }

    const fav = lv.side === 'BUY' ? (price - lv.entry) : (lv.entry - price);
    const tpDist = Math.max(0.5, Math.abs(lv.tp1 - lv.entry));
    const slDist = Math.max(0.5, Math.abs(lv.entry - lv.sl));
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const netUsd = ((fav * 10.0) - costPips) * (this.auditLot * 10.0);

    const pct = fav >= 0
      ? Math.min(100, 50 + (fav / tpDist) * 50)
      : Math.max(0, 50 - ((-fav) / slDist) * 50);

    if (pnlEl) {
      pnlEl.textContent = `${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)}`;
      pnlEl.className = `font-black tabular-nums shrink-0 ${netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'}`;
    }

    if (fillEl) {
      fillEl.style.width = `${pct.toFixed(0)}%`;
      fillEl.className = `h-full transition-all duration-150 ${fav >= 0 ? 'bg-emerald-400' : 'bg-rose-500'}`;
    }

    if (lblEl) {
      if (fav >= tpDist) {
        lblEl.textContent = '🎯 ដល់ TP1 — កាត់ចំណេញ!';
        lblEl.className = 'text-emerald-300 font-black animate-pulse';
      } else if (fav >= tpDist * 0.5) {
        lblEl.textContent = '🛡️ រំកិល SL ស្មើដើម (BE)';
        lblEl.className = 'text-sky-300 font-black';
      } else if ((-fav) >= slDist * 0.55) {
        lblEl.textContent = '⚠️ កៀក SL!';
        lblEl.className = 'text-rose-400 font-black animate-pulse';
      } else {
        lblEl.textContent = `${fav >= 0 ? '+' : ''}$${fav.toFixed(2)}`;
        lblEl.className = fav >= 0 ? 'text-emerald-300' : 'text-rose-300';
      }
    }
  }

  async _copyField(field, rowEl) {
    const lv = this.lastPlan;
    if (!lv) return;
    const raw = lv[field];
    if (raw == null) return;
    const txt = Number(raw).toFixed(2);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(txt);
      const valEl = rowEl.querySelector('span:last-child');
      if (valEl) {
        const old = valEl.textContent;
        valEl.textContent = `✓ Copied ${txt}`;
        setTimeout(() => { valEl.textContent = old; }, 1000);
      }
    } catch {}
  }
}

export const hudWidget = new HudWidget();