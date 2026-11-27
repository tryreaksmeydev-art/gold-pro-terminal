import { MAX_TPSL_SLOTS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, escHtml, toNum } from '../core/utils.js';
import { chartEngine } from '../chart/chartEngine.js';
import { hudWidget } from './hudWidget.js';
import { auditScorecard } from './auditScorecard.js';

class TpSlTracker {
  constructor() {
    const raw = Store.get(Store.KEYS.TPSL, []);
    this.trackers = (Array.isArray(raw) ? raw : []).filter(
      (t) => t && Number.isFinite(Number(t.entry)) && Number.isFinite(Number(t.sl)) && Number.isFinite(Number(t.tp))
    );
    this.selectedIdx = -1;
    this.modalSide = 'BUY';
    this.hitBoxes = [];
    this.lastPrice = null;
  }

  init() {
    chartEngine.registerOverlayLayer((ctx, plotW, plotH, helpers) => {
      this._drawZones(ctx, plotW, plotH, helpers);
    });

    const container = chartEngine.getContainer();
    if (container) {
      container.addEventListener('click', (e) => {
        if (!this.hitBoxes.length) return;
        const rc = container.getBoundingClientRect();
        const cx = e.clientX - rc.left;
        const cy = e.clientY - rc.top;
        for (let i = this.hitBoxes.length - 1; i >= 0; i--) {
          const b = this.hitBoxes[i];
          if (cx >= b.x1 && cx <= b.x2 && cy >= b.y1 && cy <= b.y2) {
            this.openModal(b.idx);
            break;
          }
        }
      });
    }

    bus.on('price:updated', ({ price }) => {
      this.lastPrice = price;
      this._onPriceTick(price);
    });

    this.renderSlotBar();
  }

  _save() {
    Store.set(Store.KEYS.TPSL, this.trackers);
  }

  _calcPnl(tr, px) {
    if (!Number.isFinite(px) || !tr) return { usd: 0, pips: 0 };
    const diff = tr.side === 'BUY' ? (px - tr.entry) : (tr.entry - px);
    return { usd: diff * (tr.lot || 0.01) * 100, pips: Math.round(diff * 10) };
  }

  renderSlotBar() {
    const bar = $('tpSlSlotBar');
    if (!bar) return;
    let html = '';
    for (let i = 0; i < MAX_TPSL_SLOTS; i++) {
      const tr = this.trackers[i];
      if (tr) {
        const r = this._calcPnl(tr, this.lastPrice || tr.entry);
        const bc = tr.status === 'HIT_TP'
          ? 'border-emerald-400 bg-emerald-500/20'
          : tr.status === 'HIT_SL'
            ? 'border-rose-500 bg-rose-500/20'
            : tr.side === 'BUY'
              ? 'border-emerald-500/35 bg-emerald-500/10'
              : 'border-rose-500/35 bg-rose-500/10';
        const lbl = tr.status === 'HIT_TP' ? 'TP!' : tr.status === 'HIT_SL' ? 'SL!' : tr.side;
        html += `<button type="button" data-tpsl-slot="${i}" class="flex items-center gap-1.5 px-2 py-1 rounded-lg border ${bc} text-[10px] font-bold whitespace-nowrap"><span class="text-white">#${i + 1} ${escHtml(lbl)}</span><span id="slot-pnl-${i}" class="${r.usd >= 0 ? 'text-emerald-400' : 'text-rose-400'} tabular-nums font-black">${r.usd >= 0 ? '+' : ''}$${r.usd.toFixed(2)}</span></button>`;
      } else {
        html += `<button type="button" data-tpsl-slot="${i}" class="px-2 py-1 rounded-lg border border-dashed border-white/15 bg-white/5 text-[10px] text-gray-400 font-semibold whitespace-nowrap">+ #${i + 1}</button>`;
      }
    }
    bar.innerHTML = html;

    bar.querySelectorAll('[data-tpsl-slot]').forEach((btn) => {
      btn.addEventListener('click', () => this.openModal(Number(btn.dataset.tpslSlot)));
    });
  }

  _onPriceTick(price) {
    if (!Number.isFinite(price) || !this.trackers.length) return;
    let changed = false;

    this.trackers.forEach((tr, i) => {
      if (!tr) return;
      const r = this._calcPnl(tr, price);
      const el = $(`slot-pnl-${i}`);
      if (el) {
        el.textContent = `${r.usd >= 0 ? '+' : ''}$${r.usd.toFixed(2)}`;
        el.className = `${r.usd >= 0 ? 'text-emerald-400' : 'text-rose-400'} tabular-nums font-black`;
      }

      if (tr.status === 'ACTIVE') {
        const td = Math.abs(tr.tp - tr.entry);
        const sd = Math.abs(tr.entry - tr.sl);
        const fav = tr.side === 'BUY' ? (price - tr.entry) : (tr.entry - price);

        if (tr.side === 'BUY' ? price >= tr.tp : price <= tr.tp) {
          tr.status = 'HIT_TP';
          changed = true;
          bus.emit('toast:popup', { type: 'win', badge: `TP #${i + 1}`, title: `ជើង #${i + 1} ដល់ TP $${tr.tp.toFixed(2)}!`, body: `ចំណេញ +$${Math.abs(r.usd).toFixed(2)}!`, playSound: true, autoHideMs: 22000 });
        } else if (tr.side === 'BUY' ? price <= tr.sl : price >= tr.sl) {
          tr.status = 'HIT_SL';
          changed = true;
          bus.emit('toast:popup', { type: 'danger', badge: `SL #${i + 1}`, title: `ជើង #${i + 1} ប៉ះ SL $${tr.sl.toFixed(2)}!`, body: `ខាត -$${Math.abs(r.usd).toFixed(2)} — បញ្ឈប់ភ្លាម!`, playSound: true, autoHideMs: 25000 });
        } else if (!tr.beAlerted && td > 0.3 && fav >= td * 0.5) {
          tr.beAlerted = true;
          changed = true;
          bus.emit('toast:popup', { type: 'win', badge: `#${i + 1} BE`, title: `ជើង #${i + 1} ចំណេញ +$${r.usd.toFixed(2)}!`, body: `រំកិល SL ស្មើដើម ($${tr.entry.toFixed(2)}) ឥឡូវនេះ!`, playSound: true, autoHideMs: 16000 });
        } else if (!tr.warnSlAlerted && sd > 0.3 && (-fav) >= sd * 0.55) {
          tr.warnSlAlerted = true;
          changed = true;
          bus.emit('toast:popup', { type: 'warn', badge: `#${i + 1} ជិត SL`, title: `ជើង #${i + 1} ខាត -$${Math.abs(r.usd).toFixed(2)}`, body: `តម្លៃកៀក SL ($${tr.sl.toFixed(2)}) — ត្រៀមកាត់ខាតលឿន!`, playSound: true, autoHideMs: 16000 });
        }
      }
    });

    if (changed) {
      this._save();
      this.renderSlotBar();
    }

    if (this.selectedIdx >= 0 && !$('tpSlModal')?.classList.contains('hidden')) {
      this._updateModalLiveStats(this.trackers[this.selectedIdx]);
    }
  }

  _drawZones(ctx, plotW, plotH, { priceToCoordinate, drawRoundRect }) {
    this.hitBoxes = [];
    if (!this.trackers.length) return;
    const boxW = Math.min(155, Math.max(104, plotW * 0.3));

    this.trackers.forEach((tr, i) => {
      if (!tr) return;
      const yE = priceToCoordinate(tr.entry);
      const yT = priceToCoordinate(tr.tp);
      const yS = priceToCoordinate(tr.sl);
      if (yE === null || yT === null || yS === null) return;

      const xR = plotW - 10 - i * 14;
      const xL = Math.max(8, xR - boxW);

      ctx.fillStyle = 'rgba(16, 185, 129, 0.22)';
      ctx.fillRect(xL, Math.min(yE, yT), xR - xL, Math.max(Math.abs(yT - yE), 4));
      ctx.strokeStyle = 'rgba(52, 211, 153, 0.85)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(xL, yT); ctx.lineTo(xR, yT); ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = 'rgba(244, 63, 94, 0.22)';
      ctx.fillRect(xL, Math.min(yE, yS), xR - xL, Math.max(Math.abs(yS - yE), 4));
      ctx.strokeStyle = 'rgba(251, 113, 133, 0.85)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(xL, yS); ctx.lineTo(xR, yS); ctx.stroke();
      ctx.setLineDash([]);

      ctx.strokeStyle = '#facc15';
      ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.moveTo(xL, yE); ctx.lineTo(xR, yE); ctx.stroke();

      const r = this._calcPnl(tr, this.lastPrice || tr.entry);
      ctx.font = '800 9px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#6ee7b7';
      ctx.fillText(`TP #${i + 1}: $${tr.tp.toFixed(2)}`, xL + 5, yT + (yT < yE ? 9 : -9));
      ctx.fillStyle = '#fda4af';
      ctx.fillText(`SL #${i + 1}: $${tr.sl.toFixed(2)}`, xL + 5, yS + (yS > yE ? -9 : 9));

      drawRoundRect(ctx, xL + 2, yE - 9, Math.min(xR - xL - 4, 134), 18, 4);
      ctx.fillStyle = 'rgba(7, 13, 25, 0.92)';
      ctx.fill();
      ctx.strokeStyle = r.usd >= 0 ? '#10b981' : '#f43f5e';
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = r.usd >= 0 ? '#34d399' : '#fb7185';
      ctx.fillText(`#${i + 1} ${tr.side} ${r.usd >= 0 ? '+' : ''}$${r.usd.toFixed(2)}`, xL + 7, yE + 0.5);

      this.hitBoxes.push({
        idx: i,
        x1: xL,
        y1: Math.min(yE, yT, yS) - 8,
        x2: xR,
        y2: Math.max(yE, yT, yS) + 8,
      });
    });
  }

  trackCurrentAiSignal() {
    const p = hudWidget.getActivePlan() || hudWidget.getPreviewPlan();
    const rawSide = hudWidget.getActiveSide();
    const side = (rawSide === 'BUY' || rawSide === 'SELL') ? rawSide : 'BUY';
    if (!p || toNum(p.entry) === null || toNum(p.sl) === null) return;

    let idx = this.trackers.findIndex((t) => !t || t.status !== 'ACTIVE');
    if (idx === -1) idx = this.trackers.length < MAX_TPSL_SLOTS ? this.trackers.length : 0;

    const lot = auditScorecard.getLot();
    this.trackers[idx] = {
      side,
      entry: Number(p.entry),
      sl: Number(p.sl),
      tp: Number(p.tp ?? p.tp1),
      lot,
      status: 'ACTIVE',
      beAlerted: false,
      warnSlAlerted: false,
    };
    this._save();
    this.renderSlotBar();
    chartEngine.scheduleOverlay();

    bus.emit('toast:popup', {
      type: 'info',
      badge: `តាមដាន #${idx + 1}`,
      title: `បានដាក់តាមដាន ${side} (${lot.toFixed(2)} Lot) លើក្រាហ្វិក!`,
      body: 'ប្រព័ន្ធនឹងប្រាប់ភ្លាមៗពេលដល់ BE, ដល់ TP1 ឬពេលត្រូវកាត់ខាតលឿន។',
      playSound: false,
      autoHideMs: 5500,
    });
  }

  setModalSide(side) {
    this.modalSide = side;
    if ($('msBtnBuy')) {
      $('msBtnBuy').className = side === 'BUY'
        ? 'py-2 rounded-xl text-xs font-black border border-emerald-400 bg-emerald-500 text-[#070b13]'
        : 'py-2 rounded-xl text-xs font-bold border border-white/10 bg-white/5 text-gray-300';
    }
    if ($('msBtnSell')) {
      $('msBtnSell').className = side === 'SELL'
        ? 'py-2 rounded-xl text-xs font-black border border-rose-400 bg-rose-500 text-white'
        : 'py-2 rounded-xl text-xs font-bold border border-white/10 bg-white/5 text-gray-300';
    }
  }

  _updateModalLiveStats(tr) {
    if (!tr || !this.lastPrice) return;
    const r = this._calcPnl(tr, this.lastPrice);
    if ($('msStatus')) $('msStatus').textContent = tr.status === 'HIT_TP' ? 'ដល់ TP!' : tr.status === 'HIT_SL' ? 'ប៉ះ SL!' : 'កំពុងតាមដាន...';
    if ($('msPnl')) {
      $('msPnl').textContent = `${r.usd >= 0 ? '+' : ''}$${r.usd.toFixed(2)} (${r.pips >= 0 ? '+' : ''}${r.pips}p)`;
      $('msPnl').className = `text-sm font-black tabular-nums ${r.usd >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    }
    if ($('msToTp')) $('msToTp').textContent = `${Math.round((tr.side === 'BUY' ? tr.tp - this.lastPrice : this.lastPrice - tr.tp) * 10)}p`;
    if ($('msToSl')) $('msToSl').textContent = `${Math.round((tr.side === 'BUY' ? this.lastPrice - tr.sl : tr.sl - this.lastPrice) * 10)}p`;
  }

  openModal(idx = -1) {
    if (idx === -1) {
      idx = this.trackers.findIndex((t) => !t);
      if (idx === -1) idx = this.trackers.length < MAX_TPSL_SLOTS ? this.trackers.length : 0;
    }
    this.selectedIdx = idx;
    const tr = this.trackers[idx];

    if ($('tpSlModalTitle')) $('tpSlModalTitle').textContent = tr ? `តាមដាន / កែ TP & SL (#${idx + 1})` : `បន្ថែម TP & SL (#${idx + 1})`;
    $('msDeleteBtn')?.classList.toggle('hidden', !tr);
    $('tpSlLiveStats')?.classList.toggle('hidden', !tr);

    if (tr) {
      this.setModalSide(tr.side);
      if ($('msEntry')) $('msEntry').value = tr.entry.toFixed(2);
      if ($('msSl')) $('msSl').value = tr.sl.toFixed(2);
      if ($('msTp')) $('msTp').value = tr.tp.toFixed(2);
      if ($('msLot')) $('msLot').value = (tr.lot || 0.01).toFixed(2);
      this._updateModalLiveStats(tr);
    } else {
      this.setModalSide(hudWidget.getActiveSide() === 'SELL' ? 'SELL' : 'BUY');
      if ($('msEntry')) $('msEntry').value = (this.lastPrice || 3650).toFixed(2);
      if ($('msLot')) $('msLot').value = auditScorecard.getLot().toFixed(2);
      this.quickSlTp(4, 8);
    }

    $('tpSlModal')?.classList.remove('hidden');
  }

  closeModal() {
    $('tpSlModal')?.classList.add('hidden');
  }

  quickSlTp(slDist, tpDist) {
    const e = parseFloat($('msEntry')?.value) || this.lastPrice || 3650;
    if ($('msSl')) $('msSl').value = (this.modalSide === 'BUY' ? e - slDist : e + slDist).toFixed(2);
    if ($('msTp')) $('msTp').value = (this.modalSide === 'BUY' ? e + tpDist : e - tpDist).toFixed(2);
  }

  autoFillFromAI() {
    const p = hudWidget.getActivePlan() || hudWidget.getPreviewPlan();
    if (!p) return;
    const e = toNum(p.entry ?? this.lastPrice);
    const s = toNum(p.sl);
    const t = toNum(p.tp ?? p.tp1);
    if (e === null || s === null || t === null) return;

    const side = hudWidget.getActiveSide();
    this.setModalSide(side === 'SELL' ? 'SELL' : (side === 'BUY' ? 'BUY' : (t >= e ? 'BUY' : 'SELL')));
    if ($('msEntry')) $('msEntry').value = e.toFixed(2);
    if ($('msSl')) $('msSl').value = s.toFixed(2);
    if ($('msTp')) $('msTp').value = t.toFixed(2);
  }

  save() {
    const e = parseFloat($('msEntry')?.value);
    const s = parseFloat($('msSl')?.value);
    const t = parseFloat($('msTp')?.value);
    const l = Math.max(0.01, parseFloat($('msLot')?.value) || 0.01);
    if (!e || !s || !t) return;

    if (this.modalSide === 'BUY' && (s >= e || t <= e)) {
      alert('សម្រាប់ BUY៖ SL ត្រូវទាបជាង Entry ហើយ TP ត្រូវខ្ពស់ជាង Entry!');
      return;
    }
    if (this.modalSide === 'SELL' && (s <= e || t >= e)) {
      alert('សម្រាប់ SELL៖ SL ត្រូវខ្ពស់ជាង Entry ហើយ TP ត្រូវទាបជាង Entry!');
      return;
    }

    this.trackers[this.selectedIdx] = {
      side: this.modalSide, entry: e, sl: s, tp: t, lot: l,
      status: 'ACTIVE', beAlerted: false, warnSlAlerted: false,
    };
    this._save();
    this.renderSlotBar();
    chartEngine.scheduleOverlay();
    this.closeModal();
  }

  removeSelected() {
    if (this.selectedIdx >= 0) {
      this.trackers.splice(this.selectedIdx, 1);
      this._save();
      this.renderSlotBar();
      chartEngine.scheduleOverlay();
    }
    this.closeModal();
  }
}

export const tpslTracker = new TpSlTracker();