import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { $, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';

const POPUP_DURATION_MS = 6000;
const SAME_EVENT_LOCK_MS = 180000; // ចាក់សោ ៣ នាទី មិនឱ្យសារប្រភេទដដែលលោតរំខានវិញ

function getKhTimeNow() {
  const kh = new Date(Date.now() + 7 * 3600 * 1000);
  const h = String(kh.getUTCHours()).padStart(2, '0');
  const m = String(kh.getUTCMinutes()).padStart(2, '0');
  const s = String(kh.getUTCSeconds()).padStart(2, '0');
  const hm = kh.getUTCHours() + kh.getUTCMinutes() / 60;
  const dayUtc = new Date().getUTCDay();
  const hrUtc = new Date().getUTCHours();
  const isWeekend = dayUtc === 6 || (dayUtc === 5 && hrUtc >= 22) || (dayUtc === 0 && hrUtc < 22);
  return { h, m, s, hm, str: `${h}:${m}:${s}`, short: `${h}:${m}`, isWeekend };
}

class LiveCommandBar {
  constructor() {
    this.barEl = null;
    this.badgeEl = null;
    this.timeEl = null;
    this.textEl = null;
    this.flowEl = null;
    this.actBtnEl = null;

    this.popupEl = null;
    this.popAccentEl = null;
    this.popBadgeEl = null;
    this.popTitleEl = null;
    this.popFlowEl = null;
    this.popBtnEl = null;
    this.popProgressEl = null;
    this.popupTimer = null;
    this.popupPaused = false;

    this.currentInterval = '5m';
    this.lastData = null;
    this.lastPrice = null;
    this.auditLot = 0.01;
    this.netState = 'LIVE';
    this.depth = { bidPct: 50, askPct: 50 };
    this.tickHistory = [];

    this.currentAction = null;
    this.lastRenderSig = '';
    this.sentEvents = new Map(); // eventKey -> timestamp
    this.lastMacroKey = '';
    this.phraseSeed = 0;
  }

  init(prefs = {}) {
    this.auditLot = Number(prefs.auditLot) || 0.01;
    this.currentInterval = this._detectActiveInterval();

    this._moveNewsBannerToBottom();
    this._mountBottomDock();
    this._mountTopFlashPopup();
    this._bindEvents();
    this._render(true, false);
  }

  _detectActiveInterval() {
    const activeBtn = document.querySelector('button[data-tf].bg-yellow-400, button[data-interval].bg-yellow-400');
    return activeBtn ? (activeBtn.dataset.tf || activeBtn.dataset.interval || '5m') : '5m';
  }

  _getBottomContainer() {
    return document.querySelector('main') || document.body;
  }

  _moveNewsBannerToBottom() {
    const container = this._getBottomContainer();
    if (!container) return;

    const newsBanner =
      $('newsBanner') ||
      $('newsBar') ||
      $('usdNewsBanner') ||
      $('newsTitle')?.closest('.glass, section, div.rounded-2xl, div.rounded-xl') ||
      $('newsBadge')?.closest('.glass, section, div.rounded-2xl, div.rounded-xl');

    if (newsBanner && newsBanner !== container && !newsBanner.contains($('chartCard'))) {
      newsBanner.classList.add('mt-2');
      container.appendChild(newsBanner);
    }
  }

  _mountBottomDock() {
    $('liveCommandBar')?.remove();
    $('lcbPopover')?.remove();

    const container = this._getBottomContainer();
    if (!container) return;

    const bar = document.createElement('div');
    bar.id = 'liveCommandBar';
    bar.className = 'w-full mt-2 rounded-2xl px-3.5 py-2.5 border border-white/12 bg-[#091121]/94 shadow-[0_10px_28px_rgba(0,0,0,.38)] transition-colors duration-200';
    bar.innerHTML = `
      <div class="flex items-center gap-2 min-w-0">
        <span id="lcbBadge" class="shrink-0 px-2 py-0.5 rounded-md text-[9.5px] font-black uppercase tracking-wide bg-sky-500/20 text-sky-200 border border-sky-400/35">AI FLOW</span>
        <div id="lcbText" class="flex-1 min-w-0 text-[11px] sm:text-xs font-extrabold text-white leading-snug">កំពុងវិភាគលំហូរមាស និងទីផ្សារពិភពលោក...</div>
        <span id="lcbTime" class="hidden sm:inline shrink-0 text-[9.5px] font-bold text-gray-400 tabular-nums">--:--</span>
        <button id="lcbQuickBtn" type="button" class="hidden shrink-0 px-2.5 py-1 rounded-lg text-[9.5px] font-black active:scale-95 transition"></button>
      </div>
      <div id="lcbFlow" class="mt-1.5 pt-1.5 border-t border-white/10 text-[10px] sm:text-[10.5px] font-medium text-cyan-200/90 leading-relaxed">
        🌐 លំហូរមាសពិភពលោក៖ កំពុងទាញទិន្នន័យ DXY, US10Y, ប្រាក់ XAG និងលំហូរលុយស្ថាប័ន...
      </div>
    `;

    container.appendChild(bar);
    this.barEl = bar;
    this.badgeEl = $('lcbBadge');
    this.timeEl = $('lcbTime');
    this.textEl = $('lcbText');
    this.flowEl = $('lcbFlow');
    this.actBtnEl = $('lcbQuickBtn');

    this.actBtnEl?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._executeQuickAction(this.actBtnEl);
    });
  }

  _mountTopFlashPopup() {
    $('lcbTopPopup')?.remove();

    const pop = document.createElement('div');
    pop.id = 'lcbTopPopup';
    pop.className = 'fixed left-1/2 -translate-x-1/2 z-[999] w-[min(480px,calc(100vw-20px))] rounded-2xl overflow-hidden border backdrop-blur-2xl shadow-[0_20px_55px_rgba(0,0,0,.78)] transition-all duration-300 opacity-0 -translate-y-5 pointer-events-none';
    pop.style.top = 'calc(12px + env(safe-area-inset-top))';
    pop.innerHTML = `
      <div class="relative px-3.5 py-2.5 pl-4">
        <div id="lcbPopAccent" class="absolute left-0 top-0 bottom-0 w-1 bg-amber-400"></div>
        <div class="flex items-start gap-2 min-w-0">
          <div class="flex-1 min-w-0 space-y-1">
            <div class="flex items-center gap-2 min-w-0">
              <span id="lcbPopBadge" class="shrink-0 px-2 py-0.5 rounded-md text-[9.5px] font-black uppercase tracking-wide border">ALERT</span>
              <span id="lcbPopTime" class="text-[9.5px] font-bold text-gray-300/80 tabular-nums"></span>
            </div>
            <div id="lcbPopTitle" class="text-[11.5px] sm:text-xs font-black text-white leading-snug"></div>
            <div id="lcbPopFlow" class="text-[10px] sm:text-[10.5px] font-medium text-gray-200/95 leading-snug"></div>
          </div>
          <div class="flex flex-col items-end gap-1.5 shrink-0">
            <button id="lcbPopClose" type="button" class="w-6 h-6 rounded-lg bg-white/10 hover:bg-white/20 text-gray-200 text-[10px] font-black flex items-center justify-center">✕</button>
            <button id="lcbPopAction" type="button" class="hidden px-2.5 py-1 rounded-lg text-[9.5px] font-black active:scale-95 transition"></button>
          </div>
        </div>
      </div>
      <div class="w-full h-[2.5px] bg-white/10">
        <div id="lcbPopProgress" class="h-full bg-amber-400" style="width:100%"></div>
      </div>
    `;

    document.body.appendChild(pop);
    this.popupEl = pop;
    this.popAccentEl = $('lcbPopAccent');
    this.popBadgeEl = $('lcbPopBadge');
    this.popTitleEl = $('lcbPopTitle');
    this.popFlowEl = $('lcbPopFlow');
    this.popBtnEl = $('lcbPopAction');
    this.popProgressEl = $('lcbPopProgress');

    $('lcbPopClose')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._hideTopPopup();
    });

    this.popBtnEl?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._executeQuickAction(this.popBtnEl);
    });

    // ដាក់ Mouse លើផ្ទាំងសារ -> ឈប់បិទបណ្តោះអាសន្នដើម្បីអានស្រួល
    pop.addEventListener('mouseenter', () => {
      this.popupPaused = true;
      if (this.popupTimer) clearTimeout(this.popupTimer);
    });
    pop.addEventListener('mouseleave', () => {
      this.popupPaused = false;
      this.popupTimer = setTimeout(() => this._hideTopPopup(), 2500);
    });
  }

  async _executeQuickAction(btnEl) {
    const act = this.currentAction;
    if (!act) return;

    if (act.type === 'switch_tf' && act.tf) {
      this._switchTimeframe(act.tf);
      this._hideTopPopup();
      return;
    }

    if (act.type === 'copy_value' && act.value) {
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(act.value);
        if (btnEl) {
          const old = btnEl.textContent;
          btnEl.textContent = `✓ ${act.value}`;
          setTimeout(() => { btnEl.textContent = old; }, 1100);
        }
      } catch {}
    }
  }

  _switchTimeframe(tf) {
    const target =
      document.querySelector(`button[data-tf="${tf}"]`) ||
      document.querySelector(`button[data-interval="${tf}"]`) ||
      Array.from(document.querySelectorAll('button')).find(
        (b) =>
          !b.closest('#liveCommandBar') &&
          !b.closest('#lcbTopPopup') &&
          !b.closest('#nsTfScannerBar') &&
          b.textContent.trim().toLowerCase() === tf.toLowerCase()
      );
    if (target) target.click();
    else bus.emit('interval:changed', { interval: tf });
  }

  _canFirePopup(eventKey) {
    if (!eventKey) return false;
    const now = Date.now();
    const last = this.sentEvents.get(eventKey) || 0;
    if (now - last < SAME_EVENT_LOCK_MS) return false;

    this.sentEvents.set(eventKey, now);
    // សម្អាត Key ចាស់ៗ
    if (this.sentEvents.size > 40) {
      for (const [k, ts] of this.sentEvents.entries()) {
        if (now - ts > SAME_EVENT_LOCK_MS * 2) this.sentEvents.delete(k);
      }
    }
    return true;
  }

  _showTopPopup(dec, st) {
    if (!this.popupEl || !this._canFirePopup(dec.eventKey)) return;

    const kh = getKhTimeNow();
    this.popupEl.className = `fixed left-1/2 -translate-x-1/2 z-[999] w-[min(480px,calc(100vw-20px))] rounded-2xl overflow-hidden border backdrop-blur-2xl shadow-[0_20px_55px_rgba(0,0,0,.78)] transition-all duration-300 opacity-100 translate-y-0 pointer-events-auto ${st.popup}`;

    if (this.popAccentEl) this.popAccentEl.className = `absolute left-0 top-0 bottom-0 w-1 ${st.accent}`;
    if (this.popBadgeEl) {
      this.popBadgeEl.className = `shrink-0 px-2 py-0.5 rounded-md text-[9.5px] font-black uppercase tracking-wide border ${st.badge}`;
      this.popBadgeEl.textContent = dec.badge;
    }
    if ($('lcbPopTime')) $('lcbPopTime').textContent = `ម៉ោង ${kh.str}`;
    if (this.popTitleEl) this.popTitleEl.textContent = dec.title;
    if (this.popFlowEl) this.popFlowEl.textContent = dec.flowNote;

    if (this.popBtnEl) {
      if (dec.action) {
        this.popBtnEl.textContent = dec.action.label;
        this.popBtnEl.className = `px-2.5 py-1 rounded-lg text-[9.5px] font-black active:scale-95 transition ${st.btn}`;
      } else {
        this.popBtnEl.className = 'hidden';
      }
    }

    // របារវិនាទីបិទស្វ័យប្រវត្តិ (6s)
    if (this.popProgressEl) {
      this.popProgressEl.className = `h-full ${st.accent}`;
      this.popProgressEl.style.transition = 'none';
      this.popProgressEl.style.width = '100%';
      requestAnimationFrame(() => {
        if (!this.popProgressEl) return;
        this.popProgressEl.style.transition = `width ${POPUP_DURATION_MS}ms linear`;
        this.popProgressEl.style.width = '0%';
      });
    }

    if (this.popupTimer) clearTimeout(this.popupTimer);
    if (!this.popupPaused) {
      this.popupTimer = setTimeout(() => this._hideTopPopup(), POPUP_DURATION_MS);
    }
  }

  _hideTopPopup() {
    if (!this.popupEl) return;
    if (this.popupTimer) {
      clearTimeout(this.popupTimer);
      this.popupTimer = null;
    }
    this.popupEl.classList.remove('opacity-100', 'translate-y-0', 'pointer-events-auto');
    this.popupEl.classList.add('opacity-0', '-translate-y-5', 'pointer-events-none');
  }

  _bindEvents() {
    bus.on('interval:changed', ({ interval }) => {
      if (interval) {
        this.currentInterval = interval;
        this._render(true, false);
      }
    });

    bus.on('market:full', ({ data }) => {
      this.lastData = data;
      if (data?.interval) this.currentInterval = data.interval;
      this.phraseSeed += 1;
      this._render(false, true);
    });

    bus.on('price:updated', ({ price }) => {
      if (!Number.isFinite(price) || price <= 0) return;
      this.lastPrice = price;
      const now = Date.now();
      this.tickHistory.push({ ts: now, price });
      while (this.tickHistory.length > 2 && now - this.tickHistory[0].ts > 10000) {
        this.tickHistory.shift();
      }
      this._render(false, true);
    });

    bus.on('market:depth', (d) => {
      this.depth = d;
    });

    bus.on('network:state', ({ state }) => {
      this.netState = state || 'LIVE';
      this._render(true, true);
    });

    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
      this._render(true, false);
    });
  }

  /**
   * ខួរក្បាលវិភាគលំហូរមាសជាមួយទីផ្សារពិភពលោក (Dynamic Human-Like Briefing)
   * មិនប្រើអក្សរដដែលៗ — សំយោគផ្ទាល់ពី DXY, US10Y, ព័ត៌មានសង្គ្រាម, SMT ប្រាក់, VWAP, CVD & Order Book
   */
  _synthesizeGlobalGoldFlow(data, price) {
    const kh = getKhTimeNow();
    const brain = data?.brain || {};
    const macro = brain.macro || {};
    const gNews = brain.global_news || {};
    const war = brain.war_news || {};
    const quant = data?.quant || brain.quant || {};
    const smt = data?.smt || brain.smt || {};

    const parts = [];

    // 1. ស្ថានភាពមាសជាមួយម៉ាក្រូពិភពលោក (DXY / US10Y / War)
    const dxy = toNum(macro.dxy);
    const us10y = toNum(macro.us10y);
    const warLvl = String(war.level || 'LOW').toUpperCase();
    const newsBias = String(gNews.bias || 'NEUTRAL').toUpperCase();

    if (warLvl === 'HIGH' || warLvl === 'CRITICAL') {
      parts.push(`ភូមិសាស្ត្រនយោបាយតានតឹងខ្លាំង (War ${warLvl}) ជំរុញលុយពិភពលោករត់រកមាសជាជម្រកសុវត្ថិភាព`);
    } else if (newsBias === 'BULLISH') {
      parts.push('ព័ត៌មានសេដ្ឋកិច្ចពិភពលោកកំពុងគាំទ្រតម្លៃមាសឡើង');
    } else if (newsBias === 'BEARISH') {
      parts.push('ព័ត៌មានពិភពលោកកំពុងដាក់សម្ពាធលក់លើមាស');
    }

    if (dxy !== null || us10y !== null) {
      const dxyTxt = dxy !== null ? `DXY ${dxy.toFixed(2)}` : '';
      const y10Txt = us10y !== null ? `US10Y ${us10y.toFixed(2)}%` : '';
      const macroJoin = [dxyTxt, y10Txt].filter(Boolean).join(' • ');
      const yieldNote = (us10y !== null && us10y >= 4.5)
        ? 'ការប្រាក់អាមេរិកនៅខ្ពស់ (ប្រយ័ត្នសម្ពាធលក់)'
        : 'ការប្រាក់អាមេរិកនៅកម្រិតធម្មតា';
      parts.push(`${macroJoin} (${yieldNote})`);
    }

    // 2. លំហូរលុយស្ថាប័នផ្ទាល់ (VWAP + CVD + Order Book + SMT)
    const vwap = toNum(quant.vwap);
    const cvdPct = Math.round(toNum(quant.cvd_pct) ?? 50);
    const cvdDiv = String(quant.cvd_divergence || 'NONE');
    const bidPct = Math.round(this.depth?.bidPct ?? 50);

    if (smt.signal === 'BULL_SMT') {
      parts.push('⚡ ប្រាក់ (XAG) រឹងមិនចុះតាមមាស (SMT ▲) — សញ្ញាស្ថាប័នលួចប្រមូលទិញ');
    } else if (smt.signal === 'BEAR_SMT') {
      parts.push('⚡ ប្រាក់ (XAG) ខ្សោយមិនឡើងតាមមាស (SMT ▼) — សញ្ញាស្ថាប័នលួចបញ្ចេញលក់');
    }

    if (cvdDiv === 'BULL_DIV') {
      parts.push(`លុយធំលួចប្រមូលទិញបញ្ច្រាសតម្លៃ (CVD DIV▲ • Book ទិញ ${bidPct}%)`);
    } else if (cvdDiv === 'BEAR_DIV') {
      parts.push(`លុយធំលួចលក់ចេញនៅកំពូល (CVD DIV▼ • Book លក់ ${100 - bidPct}%)`);
    } else if (vwap !== null && price > 0) {
      const diff = price - vwap;
      const sideFlow = cvdPct >= 56
        ? `លំហូរទិញឈ្នះ (CVD ${cvdPct}% • Book ${bidPct}%)`
        : cvdPct <= 44
          ? `លំហូរលក់សង្កត់ (CVD ${cvdPct}% • Book លក់ ${100 - bidPct}%)`
          : `កម្លាំងទិញ-លក់ស្មើគ្នា (CVD ${cvdPct}%)`;
      parts.push(`មាសនៅ${diff >= 0 ? 'លើ' : 'ក្រោម'} VWAP ${diff >= 0 ? '+' : ''}$${diff.toFixed(1)} • ${sideFlow}`);
    }

    // 3. វគ្គទីផ្សារបច្ចុប្បន្ន
    if (kh.isWeekend) {
      parts.push('ផ្សារបិទចុងសប្តាហ៍ — ត្រៀមតាមដានលំហូរថ្មីថ្ងៃច័ន្ទម៉ោង 14:00 (London) & 19:20 (NY)');
    } else if (kh.hm >= 19.33 && kh.hm < 23.0) {
      parts.push('កំពុងស្ថិតក្នុងម៉ោងមាស COMEX+NY — លំហូរលុយពិភពលោកខ្លាំងបំផុត');
    } else if (kh.hm >= 14.0 && kh.hm < 17.5) {
      parts.push('កំពុងស្ថិតក្នុងម៉ោងមាស London Open — ធនាគារអឺរ៉ុបកំពុងកំណត់ទិសដៅ');
    }

    const macroKey = `${smt.signal || 'NONE'}|${cvdDiv}|${quant.squeeze_state || 'NORMAL'}|${warLvl}`;
    const text = parts.length
      ? `🌐 លំហូរមាសពិភពលោក៖ ${parts.join(' • ')}`
      : '🌐 លំហូរមាសពិភពលោក៖ កំពុងតាមដានចលនាដុល្លារ ការប្រាក់អាមេរិក និងលំហូរលុយស្ថាប័នផ្ទាល់...';

    return { text, macroKey };
  }

  _computeDecision() {
    const data = this.lastData;
    const brain = data?.brain;
    const price = this.lastPrice || toNum(data?.price) || 0;
    const tf = String(this.currentInterval || '5m').toLowerCase();
    const kh = getKhTimeNow();
    const flow = this._synthesizeGlobalGoldFlow(data, price);

    // 0) ដាច់សេវា (សំខាន់បំផុត — លោតភ្លាម)
    if (this.netState === 'OFFLINE' || this.netState === 'STALE') {
      return {
        tone: 'danger',
        shouldPopup: true,
        eventKey: 'net_offline',
        badge: '🚫 ដាច់សេវា',
        title: 'តម្លៃកំពុងគាំងដោយសារអ៊ីនធឺណិត/Server — ហាមចូល Order លើតម្លៃចាស់!',
        flowNote: flow.text,
        action: null,
      };
    }

    // 1) គ្រប់គ្រងចំណេញ & ការពារដើមទុនផ្ទាល់ពេលមានជើងសកម្ម (លោតភ្លាមៗ)
    if (brain?.can_trade && brain.plan && price > 0 && !kh.isWeekend) {
      const side = brain.side;
      const entry = Number(brain.plan.entry);
      const sl = Number(brain.plan.sl);
      const tp1 = Number(brain.plan.tp ?? brain.plan.tp1);
      const tpDist = Math.max(0.5, Math.abs(tp1 - entry));
      const slDist = Math.max(0.5, Math.abs(entry - sl));
      const fav = side === 'BUY' ? (price - entry) : (entry - price);
      const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
      const netUsd = ((fav * 10.0) - costPips) * (this.auditLot * 10.0);
      const progPct = (fav / tpDist) * 100;
      const vel = this.tickHistory.length >= 2 ? (price - this.tickHistory[0].price) : 0;
      const velFav = side === 'BUY' ? vel : -vel;
      const bookAgainst = side === 'BUY' ? this.depth.askPct >= 62 : this.depth.bidPct >= 62;

      if (fav >= tpDist) {
        return {
          tone: 'buy',
          shouldPopup: true,
          eventKey: `tp1_${side}_${entry.toFixed(1)}`,
          badge: '🎯 ដល់ TP1',
          title: `ដល់គោលដៅ $${tp1.toFixed(2)} (+$${netUsd.toFixed(2)}) — បិទយកចំណេញ 60%–100% ឥឡូវ!`,
          flowNote: flow.text,
          action: null,
        };
      }

      if (progPct >= 50) {
        return {
          tone: 'buy',
          shouldPopup: true,
          eventKey: `be_${side}_${entry.toFixed(1)}`,
          badge: '🛡️ រំកិល BE',
          title: `ចំណេញ +$${netUsd.toFixed(2)} (${Math.round(progPct)}% TP1) — រំកិល SL ស្មើដើម $${entry.toFixed(2)} ការពារដើមទុន!`,
          flowNote: flow.text,
          action: { type: 'copy_value', value: entry.toFixed(2), label: `Copy BE ${entry.toFixed(1)}` },
        };
      }

      if (netUsd > 0.45 && (velFav <= -0.40 || bookAgainst)) {
        return {
          tone: 'warn',
          shouldPopup: true,
          eventKey: `cut_${side}_${entry.toFixed(1)}`,
          badge: '💰 ការពារចំណេញ',
          title: `កំពុងចំណេញ +$${netUsd.toFixed(2)} តែលំហូរលុយចាប់ផ្តើមបត់ — បិទយកចំណេញមុនកុំឱ្យធ្លាក់វិញ!`,
          flowNote: flow.text,
          action: null,
        };
      }

      if ((-fav) >= slDist * 0.60) {
        return {
          tone: 'danger',
          shouldPopup: true,
          eventKey: `slwarn_${side}_${entry.toFixed(1)}`,
          badge: '⚠️ កៀក SL',
          title: `តម្លៃដើរបញ្ច្រាស (-$${Math.abs(netUsd).toFixed(2)}) កៀក SL $${sl.toFixed(2)} — គោរពវិន័យកាត់ខាត!`,
          flowNote: flow.text,
          action: null,
        };
      }
    }

    if (!brain) {
      return {
        tone: 'neutral',
        shouldPopup: false,
        eventKey: null,
        badge: 'AI SYNC',
        title: 'កំពុងភ្ជាប់ខួរក្បាល AI និងលំហូរមាសពិភពលោក...',
        flowNote: flow.text,
        action: null,
      };
    }

    // 2) ព័ត៌មានក្រហម USD ជិតចេញ (លោតភ្លាម)
    if (data?.news?.danger) {
      const ev = data.news.danger_event || {};
      return {
        tone: 'danger',
        shouldPopup: true,
        eventKey: `rednews_${ev.title || 'usd'}`,
        badge: '🚨 ព័ត៌មានក្រហម',
        title: `ជិតចេញព័ត៌មាន USD (${ev.title || 'High Impact'}) — ផ្អាកចូល Order ថ្មីជាបន្ទាន់!`,
        flowNote: flow.text,
        action: null,
      };
    }

    // 3) ត្រៀមចូលផ្សារចំណេញភ្លាមៗ៖ សញ្ញាខ្លាំង Grade A / A+ លើ 5m/15m+ (លោតភ្លាម!)
    if (brain.can_trade && brain.plan && !kh.isWeekend) {
      const p = brain.plan;
      const ent = Number(p.entry);
      const sl = Number(p.sl);
      const tp1 = Number(p.tp ?? p.tp1);
      const sideKm = brain.side === 'BUY' ? 'ទិញ BUY' : 'លក់ SELL';
      const isTopGrade = brain.grade === 'A+' || brain.grade === 'A';
      const distToEntry = price > 0 ? Math.abs(price - ent) : 99;

      if (isTopGrade && (brain.close_confirmed || distToEntry <= 1.5)) {
        const readyTag = brain.close_confirmed ? 'បិទទៀនបញ្ជាក់ — ចូលផ្សារបាន!' : `តម្លៃកៀកចំណុចចូល ($${distToEntry.toFixed(2)}) — ត្រៀមចូល!`;
        return {
          tone: brain.side === 'BUY' ? 'buy' : 'sell',
          shouldPopup: true,
          eventKey: `ready_${tf}_${brain.side}_${ent.toFixed(0)}`,
          badge: `🔥 ${brain.side} ${brain.grade}`,
          title: `ត្រៀម${sideKm} (${readyTag}) @ $${ent.toFixed(2)} • SL $${sl.toFixed(2)} • TP1 $${tp1.toFixed(2)}`,
          flowNote: flow.text,
          action: { type: 'copy_value', value: ent.toFixed(2), label: `Copy ${ent.toFixed(1)}` },
        };
      }
    }

    // 4) ប្រសិនបើ 5m ឬ 15m មានឱកាសចំណេញ Grade A/A+ ខណៈអ្នកនៅ TF ផ្សេង (លោតភ្លាម)
    if (!kh.isWeekend) {
      const items = data?.tf_scanner?.items || [];
      const bestSafe = items.find((i) => (i.tf === '5m' || i.tf === '15m') && i.is_sniper && i.tf !== tf);
      if (bestSafe) {
        return {
          tone: bestSafe.side === 'BUY' ? 'buy' : 'sell',
          shouldPopup: true,
          eventKey: `sniper_other_${bestSafe.tf}_${bestSafe.side}_${bestSafe.grade}`,
          badge: `🔥 ${bestSafe.label} ${bestSafe.grade}`,
          title: `ឱកាសចូលផ្សារលើ ${bestSafe.label}៖ ${bestSafe.side} (${bestSafe.grade}) — ចុចប្តូរទៅមើលភ្លាម!`,
          flowNote: flow.text,
          action: { type: 'switch_tf', tf: bestSafe.tf, label: `ទៅ ${bestSafe.label}` },
        };
      }
    }

    // 5) លំហូរមាសពិភពលោកមានការប្រែប្រួលធំថ្មី (SMT Divergence ឬ Squeeze ផ្ទុះ) -> លោត ១ ដងពេលទើបកើតឡើង
    const smt = data?.smt || brain.smt || {};
    const quant = data?.quant || brain.quant || {};
    if (!kh.isWeekend && (smt.signal === 'BULL_SMT' || smt.signal === 'BEAR_SMT')) {
      return {
        tone: smt.signal === 'BULL_SMT' ? 'buy' : 'sell',
        shouldPopup: true,
        eventKey: `smt_${tf}_${smt.signal}`,
        badge: smt.signal === 'BULL_SMT' ? '⚡ SMT ▲' : '⚡ SMT ▼',
        title: smt.detail_km || smt.label_km,
        flowNote: flow.text,
        action: null,
      };
    }

    if (!kh.isWeekend && quant.squeeze_state === 'FIRING') {
      return {
        tone: 'warn',
        shouldPopup: true,
        eventKey: `squeeze_${tf}`,
        badge: '🔥 SQUEEZE ផ្ទុះ',
        title: 'មាសកំពុងផ្ទុះចេញពីតំបន់សន្សំកម្លាំង — ត្រៀមចាំបិទទៀន 5m បញ្ជាក់ទិសដៅ!',
        flowNote: flow.text,
        action: null,
      };
    }

    // 6) ស្ថានភាពធម្មតា (WAIT / BLOCKED / ចុងសប្តាហ៍) -> អាប់ដេតតែប្រអប់ខាងក្រោម មិនលោតរំខានលើអេក្រង់ទេ!
    if (kh.isWeekend) {
      return {
        tone: 'neutral',
        shouldPopup: false,
        eventKey: null,
        badge: '⏸️ បិទចុងសប្តាហ៍',
        title: `ទីផ្សារបិទចុងសប្តាហ៍ ($${price.toFixed(2)}) — រង់ចាំថ្ងៃច័ន្ទម៉ោង 14:00 (London) & 19:20 (NY)`,
        flowNote: flow.text,
        action: null,
      };
    }

    if (brain.is_danger) {
      const why = (brain.reasons && brain.reasons[0]) || 'ទីផ្សារមានអន្ទាក់ — ផ្អាកចូលសិន';
      return {
        tone: 'danger',
        shouldPopup: false,
        eventKey: null,
        badge: '⛔ ការពារអន្ទាក់',
        title: why,
        flowNote: flow.text,
        action: null,
      };
    }

    if (brain.can_trade && brain.plan) {
      const p = brain.plan;
      return {
        tone: brain.side === 'BUY' ? 'buy' : 'sell',
        shouldPopup: false,
        eventKey: null,
        badge: `${brain.side} ${brain.grade}`,
        title: `${brain.side} @ $${Number(p.entry).toFixed(2)} • SL $${Number(p.sl).toFixed(2)} • TP1 $${Number(p.tp).toFixed(2)} (ចាំបិទទៀនបញ្ជាក់)`,
        flowNote: flow.text,
        action: { type: 'copy_value', value: Number(p.entry).toFixed(2), label: `Copy ${Number(p.entry).toFixed(1)}` },
      };
    }

    return {
      tone: 'neutral',
      shouldPopup: false,
      eventKey: null,
      badge: '👁️ កំពុងតាមដាន',
      title: `${brain.hud_hint_km || 'រង់ចាំតម្លៃមកដល់ចំណុចចូលស្អាត (Grade A/A+)'} • ផ្សារ $${price.toFixed(2)}`,
      flowNote: flow.text,
      action: null,
    };
  }

  _render(force = false, allowPopup = false) {
    if (!this.barEl || !this.badgeEl || !this.textEl || !this.flowEl) return;
    const dec = this._computeDecision();
    const sig = `${dec.tone}|${dec.badge}|${dec.title}|${dec.flowNote}|${dec.action?.label || ''}`;
    if (!force && sig === this.lastRenderSig) return;
    this.lastRenderSig = sig;
    this.currentAction = dec.action;

    const styles = {
      buy: {
        bar: 'border-emerald-400/45 bg-emerald-950/45',
        popup: 'border-emerald-400/70 bg-[#051f17]/96',
        accent: 'bg-emerald-400',
        badge: 'bg-emerald-500/25 text-emerald-200 border-emerald-400/50',
        btn: 'bg-emerald-400 text-[#070b13] hover:bg-emerald-300',
      },
      sell: {
        bar: 'border-rose-400/45 bg-rose-950/45',
        popup: 'border-rose-400/70 bg-[#260912]/96',
        accent: 'bg-rose-400',
        badge: 'bg-rose-500/25 text-rose-200 border-rose-400/50',
        btn: 'bg-rose-400 text-white hover:bg-rose-300',
      },
      warn: {
        bar: 'border-amber-400/50 bg-amber-950/45',
        popup: 'border-amber-400/75 bg-[#241805]/96',
        accent: 'bg-amber-400',
        badge: 'bg-amber-400/25 text-amber-200 border-amber-400/55',
        btn: 'bg-amber-400 text-[#070b13] hover:bg-amber-300',
      },
      danger: {
        bar: 'border-rose-500/65 bg-rose-950/65',
        popup: 'border-rose-500/85 bg-[#2e0711]/97',
        accent: 'bg-rose-500',
        badge: 'bg-rose-600/40 text-rose-100 border-rose-400/60',
        btn: 'bg-rose-500 text-white hover:bg-rose-400',
      },
      neutral: {
        bar: 'border-white/12 bg-[#091121]/94',
        popup: 'border-sky-400/50 bg-[#0a1326]/96',
        accent: 'bg-sky-400',
        badge: 'bg-sky-500/20 text-sky-200 border-sky-400/35',
        btn: 'bg-white/15 text-gray-100 hover:bg-white/25',
      },
    };
    const st = styles[dec.tone] || styles.neutral;
    const kh = getKhTimeNow();

    this.barEl.className = `w-full mt-2 rounded-2xl px-3.5 py-2.5 border shadow-[0_10px_28px_rgba(0,0,0,.38)] transition-colors duration-200 ${st.bar}`;
    this.badgeEl.className = `shrink-0 px-2 py-0.5 rounded-md text-[9.5px] font-black uppercase tracking-wide border ${st.badge}`;
    this.badgeEl.textContent = dec.badge;
    this.textEl.textContent = dec.title;
    this.flowEl.textContent = dec.flowNote;
    if (this.timeEl) this.timeEl.textContent = `អាប់ដេត ${kh.short}`;

    if (this.actBtnEl) {
      if (dec.action) {
        this.actBtnEl.textContent = dec.action.label;
        this.actBtnEl.className = `shrink-0 px-2.5 py-1 rounded-lg text-[9.5px] font-black active:scale-95 transition ${st.btn}`;
      } else {
        this.actBtnEl.className = 'hidden';
      }
    }

    // លោតសារមកលើតែពេលមានព្រឹត្តិការណ៍សំខាន់ថ្មីពិតប្រាកដប៉ុណ្ណោះ
    if (allowPopup && dec.shouldPopup) {
      this._showTopPopup(dec, st);
    }
  }
}

export const liveCommandBar = new LiveCommandBar();