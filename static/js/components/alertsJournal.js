import { DEFAULT_CALC_PREFS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, escHtml, toNum } from '../core/utils.js';

class AlertsJournal {
  constructor() {
    const savedAlerts = Store.get(Store.KEYS.ALERTS, []);
    const savedJournal = Store.get(Store.KEYS.JOURNAL, []);
    this.alerts = Array.isArray(savedAlerts) ? savedAlerts : [];
    this.journal = Array.isArray(savedJournal) ? savedJournal : [];
    this.calcPrefs = { ...DEFAULT_CALC_PREFS, ...(Store.get(Store.KEYS.CALC, {}) || {}) };
    this.currentSlDist = 4.0;
    this.lastPrice = null;
  }

  init() {
    if ($('calcBalance')) $('calcBalance').value = this.calcPrefs.balance ?? 1000;
    if ($('calcRisk')) $('calcRisk').value = this.calcPrefs.risk ?? 1;

    this.renderAlerts();
    this.renderJournal();
    this.updateLotCalc();

    bus.on('market:full', ({ data }) => {
      const usePlan = (data.brain?.can_trade && data.brain?.plan)
        ? data.brain.plan
        : (data.brain?.preview_plan || data.setup || null);
      if (usePlan) {
        const slNum = toNum(usePlan.sl);
        const pxNum = toNum(data.price);
        this.currentSlDist = toNum(usePlan.sl_dist)
          || (slNum !== null && pxNum !== null ? Math.abs(pxNum - slNum) : 4.0)
          || 4.0;
        this.updateLotCalc();
      }
    });

    bus.on('price:updated', ({ price }) => {
      this.lastPrice = price;
      this._checkPriceAlerts(price);
    });
  }

  // ---- LOT CALCULATOR ----
  updateLotCalc() {
    const bal = Math.max(0, parseFloat($('calcBalance')?.value) || 0);
    const risk = Math.max(0, Math.min(20, parseFloat($('calcRisk')?.value) || 0));
    Store.set(Store.KEYS.CALC, { balance: bal, risk });

    const riskUsd = bal * (risk / 100);
    const slDist = Math.max(0.5, this.currentSlDist);
    const lot = Math.max(0.01, Math.floor((riskUsd / (slDist * 100)) * 100) / 100);

    if ($('resLot')) $('resLot').textContent = `${lot.toFixed(2)} Lot`;
    if ($('resRiskUsd')) $('resRiskUsd').textContent = `$${riskUsd.toFixed(2)}`;
    if ($('resSlDist')) $('resSlDist').textContent = `$${slDist.toFixed(2)}`;
  }

  // ---- PRICE ALERTS ----
  _checkPriceAlerts(price) {
    if (!Number.isFinite(price) || !this.alerts.length) return;
    let changed = false;
    this.alerts.forEach((a) => {
      if (!a.triggered && ((a.dir === 'above' && price >= a.target) || (a.dir === 'below' && price <= a.target))) {
        a.triggered = true;
        changed = true;
        bus.emit('toast:priceAlert', {
          message: `មាសបានឈានដល់ $${a.target.toFixed(2)} (បច្ចុប្បន្ន $${price.toFixed(2)})`,
        });
      }
    });
    if (changed) {
      Store.set(Store.KEYS.ALERTS, this.alerts);
      this.renderAlerts();
    }
  }

  addAlert(e) {
    e.preventDefault();
    const inp = $('alertPriceInput');
    const val = parseFloat(inp?.value);
    if (!val || val <= 0) return;

    this.alerts.unshift({
      id: Date.now(),
      target: val,
      dir: (this.lastPrice && val < this.lastPrice) ? 'below' : 'above',
      triggered: false,
    });
    this.alerts = this.alerts.slice(0, 8);
    Store.set(Store.KEYS.ALERTS, this.alerts);
    if (inp) inp.value = '';
    this.renderAlerts();
  }

  removeAlert(id) {
    this.alerts = this.alerts.filter((a) => a.id !== id);
    Store.set(Store.KEYS.ALERTS, this.alerts);
    this.renderAlerts();
  }

  renderAlerts() {
    const box = $('alertList');
    if (!box) return;
    if (!this.alerts.length) {
      box.innerHTML = '<div class="text-[11px] text-gray-500">មិនទាន់មានការដាស់តឿនតម្លៃទេ។</div>';
      return;
    }
    box.innerHTML = this.alerts.map((a) => `<div class="flex items-center justify-between bg-black/30 border border-white/5 rounded-xl px-3 py-2"><div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full ${a.triggered ? 'bg-emerald-400' : 'bg-amber-400'}"></span><span class="font-bold tabular-nums ${a.triggered ? 'line-through text-gray-500' : 'text-white'}">$${Number(a.target).toFixed(2)}</span><span class="text-[10px] text-gray-400">${a.dir === 'above' ? 'ឡើងដល់' : 'ចុះដល់'}</span></div><button type="button" data-del-alert="${a.id}" class="text-gray-400 hover:text-rose-400 text-[11px] font-semibold">លុប</button></div>`).join('');

    box.querySelectorAll('[data-del-alert]').forEach((btn) => {
      btn.addEventListener('click', () => this.removeAlert(Number(btn.dataset.delAlert)));
    });
  }

  // ---- TRADE JOURNAL ----
  addJournalEntry(e) {
    e.preventDefault();
    const pnl = parseFloat($('jPnl')?.value);
    if (Number.isNaN(pnl)) return;

    const now = new Date();
    const timeStr = `${String(now.getDate()).padStart(2, '0')}/${String(now.getMonth() + 1).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    this.journal.unshift({
      id: Date.now(),
      side: $('jSide')?.value || 'BUY',
      pnl,
      time: timeStr,
    });
    this.journal = this.journal.slice(0, 50);
    Store.set(Store.KEYS.JOURNAL, this.journal);
    if ($('jPnl')) $('jPnl').value = '';
    this.renderJournal();
  }

  removeJournal(id) {
    this.journal = this.journal.filter((j) => j.id !== id);
    Store.set(Store.KEYS.JOURNAL, this.journal);
    this.renderJournal();
  }

  renderJournal() {
    const box = $('journalList');
    const tot = this.journal.length;
    const wins = this.journal.filter((j) => j.pnl > 0).length;
    const net = this.journal.reduce((s, j) => s + Number(j.pnl || 0), 0);

    if ($('jWinRate')) $('jWinRate').textContent = `Win: ${tot ? Math.round((wins / tot) * 100) : 0}% (${tot})`;
    if ($('jNetPnl')) {
      $('jNetPnl').textContent = `${net >= 0 ? '+' : ''}$${net.toFixed(2)}`;
      $('jNetPnl').className = net >= 0 ? 'text-emerald-400' : 'text-rose-400';
    }
    if (!box) return;

    if (!tot) {
      box.innerHTML = '<div class="text-[11px] text-gray-500">មិនទាន់មានកំណត់ត្រាទេ។</div>';
      return;
    }
    box.innerHTML = this.journal.map((j) => `<div class="flex items-center justify-between bg-black/30 border border-white/5 rounded-xl px-3 py-2"><div class="flex items-center gap-2"><span class="text-[10px] px-1.5 py-0.5 rounded font-bold ${j.side === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'}">${escHtml(j.side)}</span><span class="text-[10px] text-gray-400">${escHtml(j.time)}</span></div><div class="flex items-center gap-2.5"><span class="font-bold tabular-nums ${j.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${j.pnl >= 0 ? '+' : ''}$${Number(j.pnl).toFixed(2)}</span><button type="button" data-del-journal="${j.id}" class="text-gray-500 hover:text-rose-400 text-[10px]">លុប</button></div></div>`).join('');

    box.querySelectorAll('[data-del-journal]').forEach((btn) => {
      btn.addEventListener('click', () => this.removeJournal(Number(btn.dataset.delJournal)));
    });
  }
}

export const alertsJournal = new AlertsJournal();