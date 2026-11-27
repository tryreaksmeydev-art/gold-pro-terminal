import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, escHtml, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';

class AuditScorecard {
  constructor() {
    this.visible = false;
    this.auditLot = 0.01;
    this.lastStats = null;
    this.lastBrain = null;
    this.lastSetup = null;
  }

  init(prefs = {}) {
    this.visible = Boolean(prefs.showAudit);
    this.auditLot = Number(prefs.auditLot) || 0.01;

    this.setLot(this.auditLot, false);
    this.setVisible(this.visible, false, false);

    bus.on('market:full', ({ data }) => {
      this.lastBrain = data.brain || null;
      this.lastSetup = data.setup || null;
      if (data.brain?.signal_stats) {
        this.render(data.brain.signal_stats);
      } else {
        this.updateProjectedPnl();
      }
    });

    bus.on('price:updated', ({ price }) => this.updateLiveTick(price));
  }

  getLot() {
    return this.auditLot;
  }

  pipsToUsd(pips) {
    return Number(pips || 0) * (this.auditLot * 10.0);
  }

  setLot(lot, persist = true) {
    const val = Number(lot) || 0.01;
    this.auditLot = val;
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.auditLot = val;
      Store.savePrefs(prefs);
    }

    document.querySelectorAll('.lot-btn').forEach((b) => {
      b.classList.toggle('active', Number(b.dataset.alot) === val);
    });
    if ($('auditLotLabel')) $('auditLotLabel').textContent = val.toFixed(2);

    bus.emit('audit:lotChanged', { lot: val });

    if (this.lastStats) this.render(this.lastStats);
    this.updateProjectedPnl();
  }

  setVisible(flag, scroll = true, persist = true) {
    this.visible = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.showAudit = this.visible;
      Store.savePrefs(prefs);
    }

    if ($('chkAudit')) $('chkAudit').checked = this.visible;
    const sec = $('aiAuditSection');
    sec?.classList.toggle('hidden', !this.visible);

    if (this.visible) {
      if (this.lastStats) this.render(this.lastStats);
      this.updateProjectedPnl();
      if (scroll && sec) {
        setTimeout(() => sec.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
      }
    }
  }

  updateProjectedPnl() {
    const titleEl = $('auditProjTitle');
    const winEl = $('auditProjWin');
    const lossEl = $('auditProjLoss');
    if (!titleEl) return;

    const b = this.lastBrain;
    const plan = (b?.can_trade && b?.plan) ? b.plan : (b?.preview_plan || this.lastSetup);
    const side = (b?.can_trade && (b.side === 'BUY' || b.side === 'SELL'))
      ? b.side
      : (plan && toNum(plan.tp ?? plan.tp1) > toNum(plan.entry) ? 'BUY' : 'SELL');

    if (!plan || toNum(plan.entry) === null || toNum(plan.sl) === null) {
      titleEl.textContent = 'កំពុងរង់ចាំផែនការ Entry/SL/TP...';
      if (winEl) winEl.textContent = '--';
      if (lossEl) lossEl.textContent = '--';
      return;
    }

    const entry = Number(plan.entry);
    const sl = Number(plan.sl);
    const tp = Number(plan.tp ?? plan.tp1);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;

    const winPips = Math.max(0, Math.round((Math.abs(tp - entry) * 10) - costPips));
    const lossPips = Math.round((Math.abs(entry - sl) * 10) + costPips);

    titleEl.textContent = `${b?.can_trade ? 'LIVE' : 'WAIT'} ${side} @ $${entry.toFixed(2)} (SL $${sl.toFixed(2)} • TP1 $${tp.toFixed(2)})`;
    if (winEl) winEl.textContent = `+$${this.pipsToUsd(winPips).toFixed(2)} (+${winPips}p)`;
    if (lossEl) lossEl.textContent = `-$${this.pipsToUsd(lossPips).toFixed(2)} (-${lossPips}p)`;
  }

  render(st) {
    if (!st) return;
    this.lastStats = st;

    const wrEl = $('auditWinRate');
    if (wrEl) {
      wrEl.textContent = st.win_rate != null ? `${st.win_rate}%` : '--%';
      wrEl.className = `text-base font-black tabular-nums mt-0.5 ${
        st.win_rate == null ? 'text-yellow-300' : st.win_rate >= 55 ? 'text-emerald-400' : 'text-rose-400'
      }`;
    }
    if ($('auditWL')) $('auditWL').textContent = `${st.wins || 0}W / ${st.losses || 0}L (${st.total || 0})`;

    const netPips = Number(st.net_pips || 0);
    const netUsd = this.pipsToUsd(netPips);
    const netEl = $('auditNetUsd');
    if (netEl) {
      netEl.textContent = `${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)}`;
      netEl.className = `text-base font-black tabular-nums mt-0.5 ${
        netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'
      }`;
    }
    if ($('auditNetPips')) $('auditNetPips').textContent = `${netPips >= 0 ? '+' : ''}${netPips.toFixed(1)} pips`;

    const actList = Array.isArray(st.active_list) ? st.active_list : [];
    const filledCnt = actList.filter((a) => a.filled !== false && a.status !== 'PENDING').length;
    const waitCnt = actList.length - filledCnt;

    const flPips = Number(st.floating_pips || 0);
    const flUsd = this.pipsToUsd(flPips);
    const flEl = $('auditFloatUsd');
    if (flEl) {
      flEl.textContent = `${flUsd >= 0 ? '+' : ''}$${flUsd.toFixed(2)}`;
      flEl.className = `text-base font-black tabular-nums mt-0.5 ${
        flUsd > 0 ? 'text-emerald-400' : flUsd < 0 ? 'text-rose-400' : 'text-sky-300'
      }`;
    }
    if ($('auditFloatPips')) {
      $('auditFloatPips').textContent = `${filledCnt} រត់ផ្ទាល់ • ${waitCnt} រង់ចាំ Limit`;
    }
    if ($('auditPf')) $('auditPf').textContent = st.profit_factor != null ? `${st.profit_factor}` : '--';

    const actBox = $('auditActiveList');
    if (actBox) {
      actBox.innerHTML = actList.length
        ? actList.map((a, idx) => {
          const isFilled = a.filled !== false && a.status !== 'PENDING';
          const usd = isFilled ? this.pipsToUsd(a.live_pips) : 0;
          const sCls = a.side === 'BUY'
            ? 'bg-emerald-500/20 text-emerald-300 border-emerald-400/35'
            : 'bg-rose-500/20 text-rose-300 border-rose-400/35';
          const statusTag = !isFilled ? 'រង់ចាំប៉ះតម្លៃ' : (a.entry_state === 'PULLBACK' ? 'Limit Filled' : 'Live');
          const rightHtml = isFilled
            ? `<div id="audit-act-usd-${idx}" class="text-xs font-black ${usd >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${usd >= 0 ? '+' : ''}$${usd.toFixed(2)}</div><div id="audit-act-pips-${idx}" class="text-[9.5px] text-gray-400">${Number(a.live_pips || 0) >= 0 ? '+' : ''}${Number(a.live_pips || 0).toFixed(1)} pips</div>`
            : `<div id="audit-act-usd-${idx}" class="text-[10.5px] font-black text-amber-300">រង់ចាំ Limit</div><div id="audit-act-pips-${idx}" class="text-[9px] text-gray-400">មិនទាន់ចូល Order</div>`;

          return `<div class="rounded-xl bg-black/35 border ${isFilled ? 'border-white/10' : 'border-amber-400/25'} px-3 py-2 flex flex-wrap items-center justify-between gap-2"><div class="space-y-0.5 min-w-0"><div class="flex items-center gap-1.5 flex-wrap"><span class="px-1.5 py-0.5 rounded font-black text-[9.5px] border ${sCls}">${a.is_sniper ? '[SNIPER] ' : ''}${escHtml(a.side)} ${escHtml(a.grade || '')} (${statusTag})</span><span class="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-yellow-300 font-bold">${escHtml(a.tf)}</span><span class="text-[10px] text-gray-400 tabular-nums">ចូល $${Number(a.entry).toFixed(2)} (${escHtml(a.open_kh || '')})</span></div><div class="text-[10px] text-gray-400 tabular-nums">SL <span class="text-rose-400">$${Number(a.sl).toFixed(2)}</span> • TP1 <span class="text-emerald-400">$${Number(a.tp).toFixed(2)}</span></div></div><div class="text-right tabular-nums shrink-0">${rightHtml}</div></div>`;
        }).join('')
        : '<div class="text-[11px] text-gray-500 bg-black/25 rounded-xl px-3 py-2 border border-white/5">កំពុងរង់ចាំសញ្ញា BUY/SELL ពី AI...</div>';
    }

    const recList = Array.isArray(st.recent) ? st.recent : [];
    const recBox = $('auditRecentList');
    if (recBox) {
      recBox.innerHTML = recList.length
        ? recList.map((r) => {
          const isWin = r.result === 'WIN';
          const usd = this.pipsToUsd(r.pips);
          return `<div class="rounded-xl bg-black/30 border border-white/5 px-3 py-2 flex items-center justify-between gap-2"><div class="space-y-0.5 min-w-0"><div class="flex items-center gap-1.5 flex-wrap"><span class="px-1.5 py-0.5 rounded font-black text-[9px] ${isWin ? 'bg-emerald-500 text-[#070b13]' : 'bg-rose-600 text-white'}">${isWin ? 'WIN TP1' : 'LOSS SL'}</span><span class="text-[10.5px] font-bold ${r.side === 'BUY' ? 'text-emerald-300' : 'text-rose-300'}">${escHtml(r.side)} ${escHtml(r.grade || '')}</span><span class="text-[9.5px] text-gray-400">${escHtml(r.tf)} • ${escHtml(r.close_kh || '')}</span></div><div class="text-[10px] text-gray-400 tabular-nums">$${Number(r.entry).toFixed(2)} → $${Number(r.exit || r.tp).toFixed(2)} (${r.dur_min || 1} នាទី)</div></div><div class="text-right tabular-nums shrink-0"><div class="text-xs font-black ${usd >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${usd >= 0 ? '+' : ''}$${usd.toFixed(2)}</div><div class="text-[9.5px] text-gray-400">${Number(r.pips || 0) >= 0 ? '+' : ''}${Number(r.pips || 0).toFixed(1)} pips</div></div></div>`;
        }).join('')
        : '<div class="text-[11px] text-gray-500 bg-black/25 rounded-xl px-3 py-2 border border-white/5">មិនទាន់មានប្រវត្តិជើងបិទរួចនៅឡើយទេ។</div>';
    }

    this.updateProjectedPnl();
  }

  updateLiveTick(price) {
    if (!this.visible || !this.lastStats?.active_list?.length || !Number.isFinite(price)) return;
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    let totalUsd = 0;

    this.lastStats.active_list.forEach((a, idx) => {
      if (a.filled === false || a.status === 'PENDING') return;
      const pips = ((a.side === 'BUY' ? (price - a.entry) : (a.entry - price)) * 10.0) - costPips;
      const usd = this.pipsToUsd(pips);
      totalUsd += usd;

      const uEl = $(`audit-act-usd-${idx}`);
      const pEl = $(`audit-act-pips-${idx}`);
      if (uEl) {
        uEl.textContent = `${usd >= 0 ? '+' : ''}$${usd.toFixed(2)}`;
        uEl.className = `text-xs font-black ${usd >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
      }
      if (pEl) pEl.textContent = `${pips >= 0 ? '+' : ''}${pips.toFixed(1)} pips`;
    });

    const flEl = $('auditFloatUsd');
    if (flEl) {
      flEl.textContent = `${totalUsd >= 0 ? '+' : ''}$${totalUsd.toFixed(2)}`;
      flEl.className = `text-base font-black tabular-nums mt-0.5 ${
        totalUsd > 0 ? 'text-emerald-400' : totalUsd < 0 ? 'text-rose-400' : 'text-sky-300'
      }`;
    }
  }
}

export const auditScorecard = new AuditScorecard();