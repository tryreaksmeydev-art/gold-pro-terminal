import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { $, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';

const POPUP_DURATION_MS = 5500;   // លោតបង្ហាញលើអេក្រង់ 5.5 វិនាទី រួចលាក់វិញស្វ័យប្រវត្តិ
const POPUP_COOLDOWN_MS = 35000;  // ការពារកុំឱ្យសារដដែលលោតញឹកញាប់ពេក

const PRO_COACH_TIPS = {
  risk_1m: [
    '⚡ វិន័យ Pro៖ 1m ងាយបោស SL ក្នុង 2–3s — គួរប្រើ 5m ឬ 15m រង់ចាំបិទទៀនបញ្ជាក់។',
    '🛡️ ការពារដើមទុន៖ ជើងល្អលើ 5m/15m តែ ១–២ ជើង ឈ្នះជាងចូលញឹកលើ 1m។',
  ],
  profit_exit: [
    '🎯 ការពារចំណេញឱ្យជាប់! លុយចូលគណនីហើយ ទើបជាប្រាក់ចំណេញពិតប្រាកដ។',
    '💪 ធ្វើបានល្អណាស់! រំកិល SL ស្មើដើម ឬកាត់ចំណេញខ្លះ កុំឱ្យជើងឈ្នះក្លាយជាខាត។',
  ],
  sniper_ready: [
    '🔥 ឱកាសស្តង់ដា! ចូលតាមផែនការ ដាក់ SL ដាច់ខាត ប្រើ Risk 0.5%–1% ហើយរក្សាចិត្តស្ងប់។',
    '✨ ជឿជាក់លើវិន័យ៖ គ្រប់គ្រង Lot ឱ្យសមដើមទុន ការសម្រេចចិត្តកាន់តែច្បាស់។',
  ],
  blocked: [
    '🛡️ មិនចូល Order ពេលមានអន្ទាក់ ក៏ជាការឈ្នះដែរ — រក្សាដើមទុនចាំឱកាសមាស!',
    '🧘‍♂️ ចិត្តត្រជាក់ឈ្នះទីផ្សារ៖ កុំប្រថុយចូលពេល CHoCH ឬលុយធំកំពុងបញ្ច្រាស។',
  ],
  drawdown: [
    '⚠️ កុំបន្ថែម Lot សងសឹក! គោរព Stop Loss ជានិច្ច ដើម្បីរក្សាដើមទុនឈ្នះថ្ងៃក្រោយ។',
  ],
  waiting: [
    '💪 អត់ធ្មត់គឺជាប្រាក់ចំណេញ — រង់ចាំតែជើង Grade A/A+ បិទទៀនបញ្ជាក់។',
    '🎯 ជួញដូរគុណភាព មិនមែនបរិមាណ៖ ស្ងប់ចិត្ត រង់ចាំតម្លៃមកដល់តំបន់ចូលរបស់យើងសិន។',
  ],
  offline: [
    '🚫 ហាមប្រថុយចូល Order ពេលសេវាយឺត ឬដាច់សេវា — ចាំភ្លើង TURBO បៃតងវិញសិន។',
  ],
};

class LiveCommandBar {
  constructor() {
    this.barEl = null;
    this.badgeEl = null;
    this.textEl = null;
    this.coachEl = null;
    this.actBtnEl = null;

    this.popupEl = null;
    this.popBadgeEl = null;
    this.popTextEl = null;
    this.popCoachEl = null;
    this.popBtnEl = null;
    this.popupTimer = null;

    this.currentInterval = '15m';
    this.lastData = null;
    this.lastPrice = null;
    this.auditLot = 0.01;
    this.netState = 'LIVE';
    this.depth = { bidPct: 50, askPct: 50 };
    this.tickHistory = [];
    this.currentAction = null;

    this.coachIdx = 0;
    this.coachTimer = null;
    this.lastRenderSig = '';
    this.lastPopupKey = '';
    this.lastPopupAt = 0;
  }

  init(prefs = {}) {
    this.auditLot = Number(prefs.auditLot) || 0.01;
    this.currentInterval = this._detectActiveInterval();

    this._moveNewsBannerToBottom();
    this._mountBottomDock();
    this._mountTopFlashPopup();
    this._bindEvents();
    this._renderDecision(true, false);

    if (this.coachTimer) clearInterval(this.coachTimer);
    this.coachTimer = setInterval(() => {
      this.coachIdx += 1;
      this._renderDecision(true, false);
    }, 14000);
  }

  _detectActiveInterval() {
    const activeBtn = document.querySelector('button[data-tf].bg-yellow-400, button[data-interval].bg-yellow-400');
    if (activeBtn) return activeBtn.dataset.tf || activeBtn.dataset.interval || '15m';
    return '15m';
  }

  _getBottomContainer() {
    return document.querySelector('main') || document.body;
  }

  /**
   * រុញប្រអប់ព័ត៌មានសេដ្ឋកិច្ច USD (កន្លែងគូសក្រហម) ទៅនៅក្រោមគេបំផុត ដើម្បីឱ្យខាងលើស្អាតទូលាយ
   */
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

  /**
   * ដាក់ប្រអប់ Live Command Bar នៅក្រោមគេបំផុតនៃទំព័រ (មិនឱ្យបាំង Header ឬក្រាហ្វិក)
   */
  _mountBottomDock() {
    $('liveCommandBar')?.remove();
    $('lcbPopover')?.remove();

    const container = this._getBottomContainer();
    if (!container) return;

    const bar = document.createElement('div');
    bar.id = 'liveCommandBar';
    bar.className = 'w-full mt-2 rounded-2xl px-3 py-2 border border-white/12 bg-[#0b1324]/92 shadow-[0_6px_20px_rgba(0,0,0,.3)] transition-colors duration-200';
    bar.innerHTML = `
      <div class="flex items-center gap-2 min-w-0">
        <span id="lcbBadge" class="shrink-0 px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wide bg-sky-500/20 text-sky-200 border border-sky-400/35">AI LIVE</span>
        <div id="lcbText" class="flex-1 min-w-0 text-[10.5px] sm:text-[11px] font-extrabold text-gray-100 leading-snug">កំពុងវិភាគទីផ្សារមាសផ្ទាល់...</div>
        <button id="lcbQuickBtn" type="button" class="hidden shrink-0 px-2.5 py-1 rounded-lg text-[9.5px] font-black bg-yellow-400 text-[#070b13] hover:bg-yellow-300 active:scale-95 transition">ទៅ 5m</button>
      </div>
      <div id="lcbCoach" class="mt-1 text-[9.5px] sm:text-[10px] font-medium text-cyan-200/85 leading-snug">💪 អត់ធ្មត់គឺជាប្រាក់ចំណេញ — រង់ចាំតែជើង Grade A/A+ បិទទៀនបញ្ជាក់។</div>
    `;

    container.appendChild(bar);
    this.barEl = bar;
    this.badgeEl = $('lcbBadge');
    this.textEl = $('lcbText');
    this.coachEl = $('lcbCoach');
    this.actBtnEl = $('lcbQuickBtn');

    this.actBtnEl?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.currentAction?.type === 'switch_tf') {
        this._switchTimeframe(this.currentAction.tf);
      }
    });
  }

  /**
   * ផ្ទាំងសារលោតមកលើមួយភ្លែត (5.5s) តែពេលមានសញ្ញា ឬការព្រមានសំខាន់ប៉ុណ្ណោះ
   */
  _mountTopFlashPopup() {
    $('lcbTopPopup')?.remove();

    const pop = document.createElement('div');
    pop.id = 'lcbTopPopup';
    pop.className = 'fixed left-1/2 -translate-x-1/2 z-[999] w-[min(460px,calc(100vw-20px))] rounded-2xl px-3 py-2 border backdrop-blur-xl shadow-[0_14px_40px_rgba(0,0,0,.65)] transition-all duration-300 opacity-0 -translate-y-4 pointer-events-none';
    pop.style.top = 'calc(10px + env(safe-area-inset-top))';
    pop.innerHTML = `
      <div class="flex items-center gap-2 min-w-0">
        <span id="lcbPopBadge" class="shrink-0 px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wide border">ALERT</span>
        <div id="lcbPopText" class="flex-1 min-w-0 text-[10.5px] sm:text-[11px] font-black text-white leading-snug"></div>
        <button id="lcbPopAction" type="button" class="hidden shrink-0 px-2 py-0.5 rounded-lg text-[9.5px] font-black active:scale-95 transition"></button>
        <button id="lcbPopClose" type="button" class="shrink-0 px-1.5 py-0.5 rounded-lg bg-white/10 hover:bg-white/20 text-gray-200 text-[10px] font-black">✕</button>
      </div>
      <div id="lcbPopCoach" class="mt-0.5 text-[9.5px] font-medium text-gray-200/95 leading-snug"></div>
    `;

    document.body.appendChild(pop);
    this.popupEl = pop;
    this.popBadgeEl = $('lcbPopBadge');
    this.popTextEl = $('lcbPopText');
    this.popCoachEl = $('lcbPopCoach');
    this.popBtnEl = $('lcbPopAction');

    $('lcbPopClose')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._hideTopPopup();
    });

    this.popBtnEl?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.currentAction?.type === 'switch_tf') {
        this._switchTimeframe(this.currentAction.tf);
      }
      this._hideTopPopup();
    });
  }

  _showTopPopup(dec, st) {
    if (!this.popupEl) return;
    const now = Date.now();
    const key = dec.popupKey || `${dec.badge}:${dec.text.slice(0, 32)}`;
    if (key === this.lastPopupKey && (now - this.lastPopupAt) < POPUP_COOLDOWN_MS) return;

    this.lastPopupKey = key;
    this.lastPopupAt = now;

    this.popupEl.className = `fixed left-1/2 -translate-x-1/2 z-[999] w-[min(460px,calc(100vw-20px))] rounded-2xl px-3 py-2 border backdrop-blur-xl shadow-[0_14px_40px_rgba(0,0,0,.65)] transition-all duration-300 opacity-100 translate-y-0 pointer-events-auto ${st.popup}`;
    if (this.popBadgeEl) {
      this.popBadgeEl.className = `shrink-0 px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wide border ${st.badge}`;
      this.popBadgeEl.textContent = dec.badge;
    }
    if (this.popTextEl) this.popTextEl.textContent = dec.text;
    if (this.popCoachEl) {
      this.popCoachEl.className = `mt-0.5 text-[9.5px] font-medium leading-snug ${st.coach}`;
      this.popCoachEl.textContent = dec.coach;
    }
    if (this.popBtnEl) {
      if (dec.action) {
        this.popBtnEl.textContent = dec.action.label;
        this.popBtnEl.className = `shrink-0 px-2 py-0.5 rounded-lg text-[9.5px] font-black active:scale-95 transition ${st.btn}`;
      } else {
        this.popBtnEl.className = 'hidden';
      }
    }

    if (this.popupTimer) clearTimeout(this.popupTimer);
    this.popupTimer = setTimeout(() => this._hideTopPopup(), POPUP_DURATION_MS);
  }

  _hideTopPopup() {
    if (!this.popupEl) return;
    if (this.popupTimer) {
      clearTimeout(this.popupTimer);
      this.popupTimer = null;
    }
    this.popupEl.classList.remove('opacity-100', 'translate-y-0', 'pointer-events-auto');
    this.popupEl.classList.add('opacity-0', '-translate-y-4', 'pointer-events-none');
  }

  _bindEvents() {
    bus.on('interval:changed', ({ interval }) => {
      if (interval) {
        this.currentInterval = interval;
        this.lastPopupKey = ''; // អនុញ្ញាតឱ្យលោតព្រមានថ្មីភ្លាមពេលប្តូរ TF (ឧ. ចុចចូល 1m)
        this._renderDecision(true, true);
      }
    });

    bus.on('market:full', ({ data }) => {
      this.lastData = data;
      if (data?.interval) this.currentInterval = data.interval;
      this._renderDecision(false, true);
    });

    bus.on('price:updated', ({ price }) => {
      if (!Number.isFinite(price) || price <= 0) return;
      this.lastPrice = price;
      const now = Date.now();
      this.tickHistory.push({ ts: now, price });
      while (this.tickHistory.length > 2 && now - this.tickHistory[0].ts > 10000) {
        this.tickHistory.shift();
      }
      this._renderDecision(false, true);
    });

    bus.on('market:depth', (d) => {
      this.depth = d;
    });

    bus.on('network:state', ({ state }) => {
      this.netState = state || 'LIVE';
      this._renderDecision(true, true);
    });

    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
      this._renderDecision(true, false);
    });
  }

  _switchTimeframe(tf) {
    if (!tf) return;
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

  _getBestSafeTf() {
    const items = this.lastData?.tf_scanner?.items || [];
    const safe = items.filter((i) => i.tf === '5m' || i.tf === '15m');
    const sniper = safe.find((i) => i.is_sniper && (i.side === 'BUY' || i.side === 'SELL'));
    if (sniper) return sniper;
    return safe.find((i) => i.side === 'BUY' || i.side === 'SELL') || null;
  }

  _pickCoachTip(category) {
    const list = PRO_COACH_TIPS[category] || PRO_COACH_TIPS.waiting;
    return list[this.coachIdx % list.length];
  }

  _computeDecision() {
    const data = this.lastData;
    const brain = data?.brain;
    const price = this.lastPrice || toNum(data?.price) || 0;
    const tf = String(this.currentInterval || '15m').toLowerCase();

    // 0) ដាច់សេវា / តម្លៃគាំង
    if (this.netState === 'OFFLINE' || this.netState === 'STALE') {
      return {
        tone: 'danger',
        important: true,
        popupKey: 'net_offline',
        badge: 'OFFLINE',
        text: '🚫 ដាច់សេវា/តម្លៃគាំង — ហាមចូល Order លើតម្លៃចាស់!',
        coach: this._pickCoachTip('offline'),
        action: null,
      };
    }

    // 1) អាទិភាពខ្ពស់បំផុត៖ គ្រប់គ្រងចំណេញ/កាត់ខាតផ្ទាល់តាម Tick
    if (brain?.can_trade && brain.plan && price > 0) {
      const side = brain.side;
      const entry = Number(brain.plan.entry);
      const sl = Number(brain.plan.sl);
      const tp1 = Number(brain.plan.tp ?? brain.plan.tp1);
      const tpDist = Math.max(0.5, Math.abs(tp1 - entry));
      const slDist = Math.max(0.5, Math.abs(entry - sl));
      const fav = side === 'BUY' ? (price - entry) : (entry - price);
      const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
      const netPips = (fav * 10.0) - costPips;
      const netUsd = netPips * (this.auditLot * 10.0);
      const progPct = (fav / tpDist) * 100;
      const vel = this.tickHistory.length >= 2 ? (price - this.tickHistory[0].price) : 0;
      const velFav = side === 'BUY' ? vel : -vel;
      const bookAgainst = side === 'BUY' ? this.depth.askPct >= 62 : this.depth.bidPct >= 62;

      if (fav >= tpDist) {
        return {
          tone: 'buy',
          important: true,
          popupKey: `tp1_${side}_${entry.toFixed(1)}`,
          badge: '🎯 កាត់ចំណេញ',
          text: `ដល់ TP1 (+$${netUsd.toFixed(2)}) — កាត់យកចំណេញ 60%–100% ឥឡូវ!`,
          coach: this._pickCoachTip('profit_exit'),
          action: null,
        };
      }
      if (progPct >= 50) {
        return {
          tone: 'buy',
          important: true,
          popupKey: `be_${side}_${entry.toFixed(1)}`,
          badge: '🛡️ រំកិល BE',
          text: `ចំណេញ +$${netUsd.toFixed(2)} (${Math.round(progPct)}% TP1) — រំកិល SL ស្មើដើម $${entry.toFixed(2)} ភ្លាម!`,
          coach: this._pickCoachTip('profit_exit'),
          action: null,
        };
      }
      if (netUsd > 0.35 && (velFav <= -0.35 || bookAgainst)) {
        return {
          tone: 'warn',
          important: true,
          popupKey: `cut_${side}_${entry.toFixed(1)}`,
          badge: '💰 កាត់សិន',
          text: `ចំណេញ +$${netUsd.toFixed(2)} តែកម្លាំងចាប់ផ្តើមបត់ — កាត់យកចំណេញសិនបាន!`,
          coach: this._pickCoachTip('profit_exit'),
          action: null,
        };
      }
      if ((-fav) >= slDist * 0.55) {
        return {
          tone: 'danger',
          important: true,
          popupKey: `slwarn_${side}_${entry.toFixed(1)}`,
          badge: '⚠️ ប្រយ័ត្ន SL',
          text: `ដើរបញ្ច្រាស (-$${Math.abs(netUsd).toFixed(2)}) កៀក SL $${sl.toFixed(2)} — គោរពវិន័យកាត់ខាត!`,
          coach: this._pickCoachTip('drawdown'),
          action: null,
        };
      }
    }

    // 2) ព្រមានមុនគេបើដាក់ខុសលើ 1m
    if (tf === '1m') {
      const bestSafe = this._getBestSafeTf();
      return {
        tone: 'warn',
        important: true,
        popupKey: `warn_1m_${bestSafe?.tf || '5m'}`,
        badge: '⚠️ 1m RISK',
        text: bestSafe
          ? `កុំចូលជើងធំលើ 1m (ងាយបោស SL)! • ${bestSafe.label} មាន ${bestSafe.side} (${bestSafe.grade})`
          : 'កុំប្រើ 1m ចូលជើងធំ (ងាយបោស SL 2–3s) — ប្តូរទៅ 5m ឬ 15m រង់ចាំ Grade A/A+',
        coach: this._pickCoachTip('risk_1m'),
        action: { type: 'switch_tf', tf: bestSafe?.tf || '5m', label: `ទៅ ${bestSafe?.label || '5m'}` },
      };
    }

    if (!brain) {
      return {
        tone: 'neutral',
        important: false,
        badge: 'AI SYNC',
        text: 'កំពុងភ្ជាប់ទិន្នន័យមាស និងខួរក្បាល AI V11.5...',
        coach: this._pickCoachTip('waiting'),
        action: null,
      };
    }

    // 3) ព័ត៌មានក្រហម USD ឬ ហាមចូលអន្ទាក់
    if (data?.news?.danger) {
      const ev = data.news.danger_event || {};
      return {
        tone: 'danger',
        important: true,
        popupKey: `red_news_${ev.title || 'usd'}`,
        badge: '🚨 ព័ត៌មានក្រហម',
        text: `ជិតចេញព័ត៌មាន USD (${ev.title || 'High Impact'}) — ផ្អាកចូល Order ថ្មីជាបន្ទាន់!`,
        coach: this._pickCoachTip('blocked'),
        action: null,
      };
    }

    if (brain.is_danger) {
      const why = (brain.reasons && brain.reasons[0]) || 'ទីផ្សារមានហានិភ័យខ្ពស់ — ផ្អាកចូលសិន';
      return {
        tone: 'danger',
        important: true,
        popupKey: `danger_${why.slice(0, 24)}`,
        badge: '⛔ ហាមចូល',
        text: why,
        coach: this._pickCoachTip('blocked'),
        action: null,
      };
    }

    // 4) សញ្ញាខ្លាំង Sniper Grade A / A+ បិទទៀនបញ្ជាក់
    if (brain.can_trade && brain.plan && (brain.grade === 'A+' || brain.grade === 'A') && brain.close_confirmed) {
      const p = brain.plan;
      const sideKm = brain.side === 'BUY' ? 'ទិញ BUY' : 'លក់ SELL';
      return {
        tone: brain.side === 'BUY' ? 'buy' : 'sell',
        important: true,
        popupKey: `sniper_${tf}_${brain.side}_${Number(p.entry).toFixed(0)}`,
        badge: `🔥 ${brain.side} ${brain.grade}`,
        text: `ចូល${sideKm} (បិទទៀនបញ្ជាក់) @ $${Number(p.entry).toFixed(2)} • SL $${Number(p.sl).toFixed(2)} • TP1 $${Number(p.tp).toFixed(2)}`,
        coach: this._pickCoachTip('sniper_ready'),
        action: null,
      };
    }

    // 5) ប្រសិនបើ 5m/15m ផ្សេងទៀតមានសញ្ញា Sniper ខ្លាំងជាង
    const bestSafe = this._getBestSafeTf();
    if (bestSafe && bestSafe.is_sniper && bestSafe.tf !== tf && !brain.can_trade) {
      return {
        tone: bestSafe.side === 'BUY' ? 'buy' : 'sell',
        important: true,
        popupKey: `other_tf_${bestSafe.tf}_${bestSafe.side}_${bestSafe.grade}`,
        badge: `🔥 ${bestSafe.label} ${bestSafe.grade}`,
        text: `ឱកាសមាសលើ ${bestSafe.label}៖ ${bestSafe.side} (${bestSafe.grade}) — ចុចប្តូរទៅមើលភ្លាម!`,
        coach: this._pickCoachTip('sniper_ready'),
        action: { type: 'switch_tf', tf: bestSafe.tf, label: `ទៅ ${bestSafe.label}` },
      };
    }

    // 6) សញ្ញាធម្មតា (B/B+ ឬ Limit) -> បង្ហាញនៅខាងក្រោម មិនលោតរំខានលើអេក្រង់
    if (brain.can_trade && brain.plan) {
      const p = brain.plan;
      const confNote = brain.close_confirmed ? 'បិទទៀន' : 'ចាំបិទទៀន';
      return {
        tone: brain.side === 'BUY' ? 'buy' : 'sell',
        important: false,
        badge: `${brain.side} ${brain.grade}`,
        text: `${brain.side} (${confNote}) @ $${Number(p.entry).toFixed(2)} • SL $${Number(p.sl).toFixed(2)} • TP1 $${Number(p.tp).toFixed(2)}`,
        coach: this._pickCoachTip('sniper_ready'),
        action: null,
      };
    }

    // 7) សញ្ញាស្ថាប័ន (SMT Divergence / Squeeze ផ្ទុះ) -> លោតប្រាប់មួយភ្លែត
    const smt = data?.smt || brain.smt;
    if (smt?.signal === 'BULL_SMT' || smt?.signal === 'BEAR_SMT') {
      return {
        tone: smt.signal === 'BULL_SMT' ? 'buy' : 'sell',
        important: true,
        popupKey: `smt_${tf}_${smt.signal}`,
        badge: smt.signal === 'BULL_SMT' ? 'SMT ▲' : 'SMT ▼',
        text: smt.detail_km || smt.label_km,
        coach: this._pickCoachTip('waiting'),
        action: null,
      };
    }

    const q = data?.quant || brain.quant;
    if (q?.squeeze_state === 'FIRING') {
      return {
        tone: 'warn',
        important: true,
        popupKey: `squeeze_${tf}`,
        badge: '🔥 SQUEEZE',
        text: 'ទីផ្សារកំពុងផ្ទុះចេញពី Squeeze — ត្រៀមចាំបិទទៀនបញ្ជាក់!',
        coach: this._pickCoachTip('waiting'),
        action: null,
      };
    }

    // 8) ស្ថានភាពធម្មតា (រង់ចាំ) -> បង្ហាញតែនៅខាងក្រោមបំផុត មិនលោតមកលើទេ
    return {
      tone: 'neutral',
      important: false,
      badge: '⏳ រង់ចាំ',
      text: `${brain.hud_hint_km || brain.action_km || 'រង់ចាំសញ្ញា Grade A/A+'}`,
      coach: this._pickCoachTip('waiting'),
      action: null,
    };
  }

  _renderDecision(force = false, allowPopup = false) {
    if (!this.barEl || !this.badgeEl || !this.textEl || !this.coachEl) return;
    const dec = this._computeDecision();
    const sig = `${dec.tone}|${dec.badge}|${dec.text}|${dec.coach}|${dec.action?.label || ''}`;
    if (!force && sig === this.lastRenderSig) return;
    this.lastRenderSig = sig;
    this.currentAction = dec.action;

    const styles = {
      buy: {
        bar: 'border-emerald-400/45 bg-emerald-950/60',
        popup: 'border-emerald-400/70 bg-[#06241a]/95',
        badge: 'bg-emerald-500/25 text-emerald-200 border-emerald-400/50',
        coach: 'text-emerald-200/90',
        btn: 'bg-emerald-400 text-[#070b13] hover:bg-emerald-300',
      },
      sell: {
        bar: 'border-rose-400/45 bg-rose-950/60',
        popup: 'border-rose-400/70 bg-[#2a0a14]/95',
        badge: 'bg-rose-500/25 text-rose-200 border-rose-400/50',
        coach: 'text-rose-200/90',
        btn: 'bg-rose-400 text-white hover:bg-rose-300',
      },
      warn: {
        bar: 'border-amber-400/50 bg-amber-950/55',
        popup: 'border-amber-400/75 bg-[#291b06]/95',
        badge: 'bg-amber-400/25 text-amber-200 border-amber-400/55',
        coach: 'text-amber-200/90',
        btn: 'bg-amber-400 text-[#070b13] hover:bg-amber-300',
      },
      danger: {
        bar: 'border-rose-500/70 bg-rose-950/80',
        popup: 'border-rose-500/85 bg-[#330812]/96',
        badge: 'bg-rose-600/40 text-rose-100 border-rose-400/60 animate-pulse',
        coach: 'text-rose-200/95',
        btn: 'bg-rose-500 text-white hover:bg-rose-400',
      },
      neutral: {
        bar: 'border-white/12 bg-[#0b1324]/92',
        popup: 'border-sky-400/50 bg-[#0b1324]/95',
        badge: 'bg-sky-500/20 text-sky-200 border-sky-400/35',
        coach: 'text-cyan-200/85',
        btn: 'bg-white/15 text-gray-100 hover:bg-white/25',
      },
    };
    const st = styles[dec.tone] || styles.neutral;

    // អាប់ដេតប្រអប់ខាងក្រោមបំផុត
    this.barEl.className = `w-full mt-2 rounded-2xl px-3 py-2 border shadow-[0_6px_20px_rgba(0,0,0,.3)] transition-colors duration-200 ${st.bar}`;
    this.badgeEl.className = `shrink-0 px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wide border ${st.badge}`;
    this.badgeEl.textContent = dec.badge;
    this.textEl.textContent = dec.text;
    this.coachEl.className = `mt-1 text-[9.5px] sm:text-[10px] font-medium leading-snug ${st.coach}`;
    this.coachEl.textContent = dec.coach;

    if (this.actBtnEl) {
      if (dec.action) {
        this.actBtnEl.textContent = dec.action.label;
        this.actBtnEl.className = `shrink-0 px-2.5 py-1 rounded-lg text-[9.5px] font-black active:scale-95 transition ${st.btn}`;
      } else {
        this.actBtnEl.className = 'hidden';
      }
    }

    // លោតសារមកលើមួយភ្លែត (5.5s) តែពេលមានរឿងសំខាន់ប៉ុណ្ណោះ
    if (allowPopup && dec.important) {
      this._showTopPopup(dec, st);
    }
  }
}

export const liveCommandBar = new LiveCommandBar();