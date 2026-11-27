import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { $, toNum, playAlertChime } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';

class ToastManager {
  constructor() {
    this.oppHideTimer = null;
    this.aiToastAutoHide = null;
    this.lastOppKey = '';
    this.lastNotifiedKey = '';
    this.liveSignalMonitor = null;
    this.lastBrain = null;
    this.lastSetup = null;
    this.auditLot = 0.01;
  }

  init({ auditLot = 0.01 } = {}) {
    this.auditLot = Number(auditLot) || 0.01;

    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
    });

    bus.on('market:full', ({ data, fromCache, interval }) => {
      this.lastBrain = data.brain || null;
      this.lastSetup = data.setup || null;
      if (!fromCache && this.lastBrain) {
        this._evaluateBrainAlerts(this.lastBrain, interval);
      }
    });

    bus.on('price:updated', ({ price }) => {
      this._checkLiveExitGuardian(price);
    });

    bus.on('toast:popup', (opts) => this.showPopup(opts));
    bus.on('toast:priceAlert', ({ message }) => this.showPriceAlert(message));
    bus.on('signal:openLatest', () => this.openLatestSignal());
  }

  _netWinUsd(entry, tp) {
    const spread = Math.max(0, directFeed.getSpread());
    const costPips = (spread * 10.0) + COMM_PIPS;
    const netPips = Math.max(0, Math.round((Math.abs(tp - entry) * 10) - costPips));
    return netPips * (this.auditLot * 10.0);
  }

  showOppPill(text) {
    const pill = $('liveOppPill');
    const txtEl = $('liveOppText');
    if (!pill || !txtEl) return;
    txtEl.textContent = text;
    pill.classList.remove('hidden');
    clearTimeout(this.oppHideTimer);
    this.oppHideTimer = setTimeout(() => this.dismissOppPill(), 18000);
  }

  dismissOppPill() {
    clearTimeout(this.oppHideTimer);
    $('liveOppPill')?.classList.add('hidden');
  }

  showPriceAlert(message) {
    const box = $('alertToast');
    const msgEl = $('alertToastMsg');
    if (!box || !msgEl) return;
    msgEl.textContent = message;
    box.classList.remove('hidden');
    playAlertChime('win');
  }

  dismissPriceAlert() {
    $('alertToast')?.classList.add('hidden');
  }

  dismissPopup() {
    clearTimeout(this.aiToastAutoHide);
    $('aiSignalToast')?.classList.add('hidden');
  }

  showPopup({
    type = 'info',
    badge = 'AI',
    title = '',
    body = '',
    plan = null,
    showTrackBtn = false,
    playSound = true,
    autoHideMs = 16000,
  } = {}) {
    const box = $('aiSignalToast');
    const badgeEl = $('sigToastBadge');
    if (!box || !badgeEl) return;

    const borderMap = {
      buy: 'border-l-emerald-400',
      sell: 'border-l-rose-500',
      win: 'border-l-emerald-400',
      warn: 'border-l-amber-400',
      danger: 'border-l-rose-500',
      info: 'border-l-sky-400',
    };
    const badgeMap = {
      buy: 'bg-emerald-500 text-[#070b13]',
      sell: 'bg-rose-500 text-white',
      win: 'bg-emerald-400 text-[#070b13]',
      warn: 'bg-amber-400 text-[#070b13]',
      danger: 'bg-rose-600 text-white',
      info: 'bg-sky-500/25 text-sky-300 border border-sky-400/35',
    };

    box.className = `fixed left-3 right-3 sm:left-auto sm:right-4 sm:w-[360px] z-[96] rounded-2xl bg-[#0b1324]/95 backdrop-blur-xl border border-white/15 border-l-4 ${borderMap[type] || borderMap.info} shadow-[0_18px_48px_rgba(0,0,0,.85)] p-3.5 space-y-2.5`;
    badgeEl.className = `shrink-0 text-[10px] px-2 py-0.5 rounded-lg font-black ${badgeMap[type] || badgeMap.info}`;
    badgeEl.textContent = badge;

    if ($('sigToastTitle')) $('sigToastTitle').textContent = title;
    if ($('sigToastBody')) $('sigToastBody').textContent = body;

    const gridEl = $('sigToastGrid');
    if (gridEl) {
      if (plan && toNum(plan.entry) !== null && toNum(plan.sl) !== null) {
        const tp1 = toNum(plan.tp ?? plan.tp1);
        const tp2 = toNum(plan.tp2);
        if ($('stEntry')) $('stEntry').textContent = `$${Number(plan.entry).toFixed(2)}`;
        if ($('stSl')) $('stSl').textContent = `$${Number(plan.sl).toFixed(2)}`;
        if ($('stTp1')) $('stTp1').textContent = tp1 !== null ? `$${tp1.toFixed(2)}` : '--';
        if ($('stTp2')) $('stTp2').textContent = tp2 !== null ? `$${tp2.toFixed(2)}` : '--';
        gridEl.classList.remove('hidden');
      } else {
        gridEl.classList.add('hidden');
      }
    }

    $('sigToastTrackBtn')?.classList.toggle('hidden', !showTrackBtn);
    box.classList.remove('hidden');

    if (playSound) playAlertChime(type === 'danger' || type === 'warn' ? 'warn' : 'win');
    clearTimeout(this.aiToastAutoHide);
    if (autoHideMs > 0) {
      this.aiToastAutoHide = setTimeout(() => box.classList.add('hidden'), autoHideMs);
    }
  }

  openLatestSignal() {
    const b = this.lastBrain;
    if (!b) return;
    const canTrade = Boolean(b.can_trade && (b.side === 'BUY' || b.side === 'SELL'));
    const isDanger = Boolean(b.status === 'BLOCKED' || b.is_danger);
    const p = canTrade ? b.plan : (b.preview_plan || this.lastSetup);
    const detail = b.toast_detail_km || (Array.isArray(b.reasons) ? b.reasons.join(' • ') : b.action_km);

    if (canTrade) {
      this.showPopup({
        type: b.side === 'BUY' ? 'buy' : 'sell',
        badge: `${b.high_conf_alert ? 'SNIPER ' : ''}${b.side} ${b.confidence_score || ''}% (${b.grade || ''})`,
        title: b.sniper_text_km || b.action_km || `សញ្ញា ${b.side}`,
        body: `${detail}។ ដាក់ SL ដាច់ខាត — ចំណេញ 50% រំកិល SL ស្មើដើម ហើយដល់ TP1 កាត់ 50%។`,
        plan: p,
        showTrackBtn: true,
        playSound: false,
        autoHideMs: 20000,
      });
    } else if (isDanger) {
      this.showPopup({
        type: 'danger',
        badge: 'ហាមចូល',
        title: b.action_km || 'ហានិភ័យខ្ពស់',
        body: detail || 'សូមផ្អាកចូល Order សិន។',
        playSound: false,
        autoHideMs: 14000,
      });
    } else {
      this.showPopup({
        type: 'info',
        badge: 'ត្រៀមចូល',
        title: b.action_km || 'កំពុងរង់ចាំចំណុចបញ្ជាក់',
        body: detail || 'រង់ចាំទៀនបែកផុតតំបន់សំខាន់សិន។',
        plan: p,
        playSound: false,
        autoHideMs: 14000,
      });
    }
  }

  _evaluateBrainAlerts(brain, interval) {
    const canTrade = Boolean(brain.can_trade && (brain.side === 'BUY' || brain.side === 'SELL'));
    const isDanger = Boolean(brain.status === 'BLOCKED' || brain.is_danger);
    const side = canTrade ? brain.side : 'WAIT';
    const plan = canTrade ? brain.plan : null;

    if (canTrade && plan && toNum(plan.entry) !== null) {
      const oKey = `${interval}_${side}_${Math.round(Number(plan.entry))}`;
      if (oKey !== this.lastOppKey) {
        this.lastOppKey = oKey;
        const tpVal = toNum(plan.tp ?? plan.tp1);
        const netWin = tpVal !== null ? this._netWinUsd(Number(plan.entry), tpVal) : 0;
        this.showOppPill(
          `ឱកាស ${side} (${brain.grade || 'V10'}) @ $${Number(plan.entry).toFixed(2)} • Net TP1 +$${netWin.toFixed(2)}`
        );
      }
    }

    if (brain.high_conf_alert && canTrade && plan && toNum(plan.entry) !== null && toNum(plan.sl) !== null) {
      if (this.liveSignalMonitor && !this.liveSignalMonitor.closed && this.liveSignalMonitor.side !== side) {
        this.liveSignalMonitor.closed = true;
      }
      const sigKey = `${interval}_${side}_${Math.round(Number(plan.entry))}`;
      if (sigKey !== this.lastNotifiedKey) {
        this.lastNotifiedKey = sigKey;
        this.liveSignalMonitor = {
          side,
          entry: Number(plan.entry),
          sl: Number(plan.sl),
          tp1: Number(plan.tp ?? plan.tp1),
          tp2: toNum(plan.tp2),
          beNotified: false,
          tp1Notified: false,
          tp2Notified: false,
          warnSlNotified: false,
          closed: false,
        };
        this.showPopup({
          type: side === 'BUY' ? 'buy' : 'sell',
          badge: `SNIPER ${side} ${brain.confidence_score}% (${brain.grade})`,
          title: brain.sniper_text_km || `ឱកាសខ្ពស់ ${side}`,
          body: `ចូលនៅ $${Number(plan.entry).toFixed(2)} • SL $${Number(plan.sl).toFixed(2)} • TP1 $${Number(plan.tp ?? plan.tp1).toFixed(2)}`,
          plan,
          showTrackBtn: true,
          playSound: true,
          autoHideMs: 20000,
        });
      }
    } else if (this.liveSignalMonitor && !this.liveSignalMonitor.closed && isDanger) {
      this.liveSignalMonitor.closed = true;
      this.showPopup({
        type: 'danger',
        badge: 'ប្រកាសអាសន្ន',
        title: `គួរបញ្ឈប់ / ការពារជើង ${this.liveSignalMonitor.side} ភ្លាម!`,
        body: brain.toast_detail_km || 'មានហានិភ័យទីផ្សារខ្ពស់។',
        playSound: true,
        autoHideMs: 20000,
      });
    } else if (!canTrade && (!this.liveSignalMonitor || this.liveSignalMonitor.closed)) {
      this.dismissPopup();
    }
  }

  _checkLiveExitGuardian(price) {
    const m = this.liveSignalMonitor;
    if (!Number.isFinite(price) || price <= 0 || !m || m.closed) return;

    const tpDist = Math.abs(m.tp1 - m.entry);
    const slDist = Math.abs(m.entry - m.sl);
    if (tpDist <= 0.2 || slDist <= 0.2) return;

    const fav = m.side === 'BUY' ? (price - m.entry) : (m.entry - price);
    const adv = -fav;

    if (m.tp2 && !m.tp2Notified && ((m.side === 'BUY' && price >= m.tp2) || (m.side === 'SELL' && price <= m.tp2))) {
      m.tp2Notified = m.closed = true;
      this.showPopup({ type: 'win', badge: 'ដល់ TP2', title: `ជើង ${m.side} ឈ្នះដល់ TP2 ($${m.tp2.toFixed(2)})!`, body: 'បិទយកប្រាក់ចំណេញទាំងអស់ភ្លាម!', playSound: true, autoHideMs: 22000 });
    } else if (!m.tp1Notified && fav >= tpDist) {
      m.tp1Notified = true;
      this.showPopup({ type: 'win', badge: 'ដល់ TP1', title: `ជើង ${m.side} ដល់ TP1 ($${m.tp1.toFixed(2)})`, body: `កាត់យកចំណេញ 50%–70% ភ្លាម ហើយរំកិល SL ស្មើដើម ($${m.entry.toFixed(2)})!`, playSound: true, autoHideMs: 22000 });
    } else if (!m.beNotified && fav >= tpDist * 0.5) {
      m.beNotified = true;
      this.showPopup({ type: 'win', badge: 'រំកិល BE', title: `ជើង ${m.side} ចំណេញ +$${fav.toFixed(2)}!`, body: `រំកិល SL ស្មើដើម ($${m.entry.toFixed(2)}) ឥឡូវនេះ!`, playSound: true, autoHideMs: 18000 });
    } else if (!m.closed && adv >= slDist) {
      m.closed = true;
      this.showPopup({ type: 'danger', badge: 'HIT SL', title: `ជើង ${m.side} ប៉ះ SL ($${m.sl.toFixed(2)})!`, body: 'បញ្ឈប់ជើងនេះភ្លាម កុំកាន់ខាតបន្ត!', playSound: true, autoHideMs: 25000 });
    } else if (!m.warnSlNotified && adv >= slDist * 0.55) {
      m.warnSlNotified = true;
      this.showPopup({ type: 'warn', badge: 'ត្រៀមកាត់ខាត', title: `ជើង ${m.side} ដើរបញ្ច្រាស (-$${adv.toFixed(2)})`, body: `តម្លៃកៀក SL ($${m.sl.toFixed(2)}) — ត្រៀមកាត់ខាតលឿន!`, playSound: true, autoHideMs: 18000 });
    }
  }
}

export const toastManager = new ToastManager();