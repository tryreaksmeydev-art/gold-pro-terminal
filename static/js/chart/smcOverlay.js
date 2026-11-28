function isForexWeekendUTC() {
  const now = new Date();
  const day = now.getUTCDay();
  const hr = now.getUTCHours();
  return day === 6 || (day === 5 && hr >= 22) || (day === 0 && hr < 22);
}

export class SmcOverlay {
  constructor(chartContainer, canvasEl, chart, candleSeries) {
    this.container = chartContainer;
    this.canvas = canvasEl;
    this.ctx = canvasEl ? canvasEl.getContext('2d') : null;
    this.chart = chart;
    this.series = candleSeries;

    this.rafId = null;
    this.showZones = true;
    this.showSignals = true;
    this.smcData = null;
    this.signalsData = [];
    this.quantData = null;
    this.customLayers = new Set();
  }

  setOptions({ showZones, showSignals } = {}) {
    if (typeof showZones === 'boolean') this.showZones = showZones;
    if (typeof showSignals === 'boolean') this.showSignals = showSignals;
    this.scheduleRender();
  }

  setData({ smc, signals, quant } = {}) {
    if (smc !== undefined) this.smcData = smc;
    if (Array.isArray(signals)) this.signalsData = signals;
    if (quant !== undefined) this.quantData = quant;
    this.scheduleRender();
  }

  registerLayer(drawFn) {
    if (typeof drawFn !== 'function') return () => {};
    this.customLayers.add(drawFn);
    return () => this.customLayers.delete(drawFn);
  }

  scheduleRender() {
    if (!this.rafId) {
      this.rafId = requestAnimationFrame(() => this.renderNow());
    }
  }

  _drawRoundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  /**
   * ច្រោះប្រអប់ OB & FVG កុំឱ្យជាន់គ្នារញ៉េរញ៉ៃ៖
   * - ទុកតែ OB ថ្មីបំផុតអតិបរមា 2 + FVG ថ្មីបំផុតអតិបរមា 2 ក្នុង ១ ខាង
   * - បោះចោលប្រអប់ណាដែលជាន់លើប្រអប់ថ្មីជាងលើស 50%
   */
  _filterCleanBlocks(blocks) {
    if (!Array.isArray(blocks) || !blocks.length) return [];

    const normalized = blocks
      .map((b) => ({
        ...b,
        top: Math.max(Number(b.top), Number(b.bottom)),
        bottom: Math.min(Number(b.top), Number(b.bottom)),
        isOB: String(b.label || 'OB').toUpperCase() === 'OB',
        st: Number(b.start_time || 0),
      }))
      .filter((b) => Number.isFinite(b.top) && Number.isFinite(b.bottom) && b.top > 0);

    // ផ្តល់អាទិភាព OB មុន FVG និងថ្មីជាងមុនចាស់ជាង
    normalized.sort((a, b) => (b.isOB - a.isOB) || (b.st - a.st));

    const kept = [];
    let obCount = 0;
    let fvgCount = 0;

    for (const blk of normalized) {
      if (blk.isOB && obCount >= 2) continue;
      if (!blk.isOB && fvgCount >= 2) continue;

      const blkH = Math.max(0.05, blk.top - blk.bottom);
      const overlaps = kept.some((k) => {
        const inter = Math.min(blk.top, k.top) - Math.max(blk.bottom, k.bottom);
        if (inter <= 0) return false;
        const minH = Math.min(blkH, Math.max(0.05, k.top - k.bottom));
        return (inter / minH) >= 0.45;
      });
      if (overlaps) continue;

      kept.push(blk);
      if (blk.isOB) obCount += 1;
      else fvgCount += 1;
    }

    return kept;
  }

  _drawVolumeProfile(ctx, plotW, plotH) {
    if (!this.showZones || !this.quantData?.profile?.length) return;
    const maxBarW = Math.min(38, plotW * 0.09);
    const prof = this.quantData.profile;
    const barH = Math.max(2.5, Math.min(10, (plotH / prof.length) * 0.48));

    for (const item of prof) {
      const y = this.series.priceToCoordinate(item.p);
      if (y === null || y < 8 || y > plotH - 8) continue;
      const bw = Math.max(2, item.pct * maxBarW);
      ctx.fillStyle = item.is_poc ? 'rgba(250, 204, 21, 0.42)' : 'rgba(56, 189, 248, 0.11)';
      ctx.fillRect(plotW - bw - 2, y - barH / 2, bw, barH);
    }
  }

  _drawOrderBlocks(ctx, tsScale, plotW, plotH, blocks, isBullish, usedLabelYs) {
    const clean = this._filterCleanBlocks(blocks);
    const rightEdge = plotW - 4;

    for (const b of clean) {
      const yT = this.series.priceToCoordinate(b.top);
      const yB = this.series.priceToCoordinate(b.bottom);
      if (yT === null || yB === null) continue;

      const rX = tsScale.timeToCoordinate(b.start_time);
      const sx = rX === null ? 0 : Math.max(0, Math.min(rightEdge - 36, rX));
      const topY = Math.min(yT, yB);
      const boxH = Math.max(Math.abs(yB - yT), 10);
      if (topY + boxH < 0 || topY > plotH) continue;

      const midY = topY + boxH / 2;
      const boxW = Math.max(12, rightEdge - sx);

      // Gradient ស្រាលស្អាត មិនបាំងទៀន
      const grad = ctx.createLinearGradient(sx, topY, rightEdge, topY);
      if (isBullish) {
        if (b.isOB) {
          grad.addColorStop(0, 'rgba(16, 185, 129, 0.22)');
          grad.addColorStop(1, 'rgba(16, 185, 129, 0.06)');
          ctx.strokeStyle = 'rgba(52, 211, 153, 0.72)';
        } else {
          grad.addColorStop(0, 'rgba(56, 189, 248, 0.14)');
          grad.addColorStop(1, 'rgba(56, 189, 248, 0.03)');
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.35)';
        }
      } else {
        if (b.isOB) {
          grad.addColorStop(0, 'rgba(244, 63, 94, 0.22)');
          grad.addColorStop(1, 'rgba(244, 63, 94, 0.06)');
          ctx.strokeStyle = 'rgba(251, 113, 133, 0.72)';
        } else {
          grad.addColorStop(0, 'rgba(244, 63, 94, 0.12)');
          grad.addColorStop(1, 'rgba(244, 63, 94, 0.03)');
          ctx.strokeStyle = 'rgba(251, 113, 133, 0.35)';
        }
      }

      ctx.fillStyle = grad;
      ctx.fillRect(sx, topY, boxW, boxH);

      // ស៊ុមលើ-ក្រោមស្អាត និងគែមឆ្វេងបញ្ជាក់ដើមប្រអប់
      ctx.lineWidth = b.isOB ? 1.15 : 0.85;
      ctx.beginPath();
      ctx.moveTo(sx, topY);
      ctx.lineTo(rightEdge, topY);
      ctx.moveTo(sx, topY + boxH);
      ctx.lineTo(rightEdge, topY + boxH);
      ctx.stroke();

      if (b.isOB) {
        ctx.fillStyle = isBullish ? '#34d399' : '#fb7185';
        ctx.fillRect(sx, topY, 2.5, boxH);
      }

      // ខ្សែកណ្តាល (50% EQ នៃ OB) ស្តើងស្អាត
      if (b.isOB && boxH >= 16) {
        ctx.strokeStyle = isBullish ? 'rgba(110, 231, 183, 0.32)' : 'rgba(253, 164, 175, 0.32)';
        ctx.lineWidth = 0.8;
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        ctx.moveTo(sx + 3, midY);
        ctx.lineTo(rightEdge, midY);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // ផ្លាកអក្សរ Pill ការពារការជាន់លើគ្នា
      const tooClose = usedLabelYs.some((uy) => Math.abs(uy - midY) < 15);
      if (boxH >= 11 && !tooClose) {
        usedLabelYs.push(midY);
        const refPrice = isBullish ? b.top : b.bottom;
        const labelTxt = b.isOB
          ? `${isBullish ? 'Bull OB' : 'Bear OB'} ${refPrice.toFixed(1)}`
          : 'FVG';
        ctx.font = b.isOB ? '700 9px system-ui, sans-serif' : '700 8px system-ui, sans-serif';
        const tw = ctx.measureText(labelTxt).width;
        const px = sx + 7;
        const ph = 13;

        this._drawRoundRect(ctx, px - 3, midY - ph / 2, tw + 6, ph, 3);
        ctx.fillStyle = 'rgba(7, 11, 20, 0.72)';
        ctx.fill();

        ctx.fillStyle = isBullish
          ? (b.isOB ? '#6ee7b7' : 'rgba(147, 197, 253, 0.9)')
          : (b.isOB ? '#fda4af' : 'rgba(253, 164, 175, 0.88)');
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(labelTxt, px, midY + 0.5);
      }
    }
  }

  _drawSmcZones(ctx, tsScale, plotW, plotH) {
    if (!this.showZones || !this.smcData) return;

    const usedLabelYs = [];
    this._drawOrderBlocks(ctx, tsScale, plotW, plotH, this.smcData.demand_blocks || [], true, usedLabelYs);
    this._drawOrderBlocks(ctx, tsScale, plotW, plotH, this.smcData.supply_blocks || [], false, usedLabelYs);

    // ខ្សែ Strong High / Weak Low (ការពារផ្លាកជាន់គ្នានៅខាងស្តាំ)
    const usedRightYs = [];
    for (const lv of this.smcData.levels || []) {
      const y = this.series.priceToCoordinate(lv.price);
      if (y === null || y < 12 || y > plotH - 12) continue;
      if (usedRightYs.some((ry) => Math.abs(ry - y) < 16)) continue;
      usedRightYs.push(y);

      const rX = tsScale.timeToCoordinate(lv.time);
      const sx = rX === null ? 0 : Math.max(0, Math.min(plotW - 70, rX));
      const isHi = lv.kind === 'high';

      ctx.strokeStyle = isHi ? 'rgba(248, 113, 113, 0.55)' : 'rgba(52, 211, 153, 0.55)';
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(sx, y);
      ctx.lineTo(plotW - 4, y);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.font = '700 9px system-ui, sans-serif';
      const txt = `${lv.label} ${Number(lv.price).toFixed(1)}`;
      const tw = ctx.measureText(txt).width;
      const ly = isHi ? y - 9 : y + 9;
      this._drawRoundRect(ctx, plotW - tw - 14, ly - 6.5, tw + 8, 13, 3);
      ctx.fillStyle = 'rgba(7, 11, 20, 0.78)';
      ctx.fill();

      ctx.fillStyle = isHi ? '#fca5a5' : '#6ee7b7';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(txt, plotW - 10, ly);
    }

    // បង្ហាញតែ BOS / CHoCH ៤ ថ្មីចុងក្រោយ ដើម្បីឱ្យក្រាហ្វិកស្អាត
    const recentBreaks = (this.smcData.breaks || []).slice(-4);
    for (const br of recentBreaks) {
      const y = this.series.priceToCoordinate(br.price);
      const x1 = tsScale.timeToCoordinate(br.start_time);
      const x2 = tsScale.timeToCoordinate(br.end_time);
      if (y === null || x2 === null || x2 < 0 || (x1 !== null && x1 > plotW)) continue;
      const sx = x1 === null ? 0 : Math.max(0, x1);
      const ex = Math.min(plotW - 4, x2);
      if (ex - sx < 10) continue;

      const isUp = String(br.type || '').endsWith('_UP');
      ctx.strokeStyle = isUp ? 'rgba(52, 211, 153, 0.72)' : 'rgba(248, 113, 113, 0.72)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(sx, y);
      ctx.lineTo(ex, y);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.font = '800 8.5px system-ui, sans-serif';
      ctx.fillStyle = isUp ? '#6ee7b7' : '#fda4af';
      ctx.textAlign = 'center';
      ctx.textBaseline = isUp ? 'bottom' : 'top';
      ctx.fillText(br.label, (sx + ex) / 2, isUp ? y - 2 : y + 2);
    }
  }

  _drawSignals(ctx, tsScale, plotW, plotH) {
    if (!this.showSignals || !Array.isArray(this.signalsData)) return;

    for (const sig of this.signalsData) {
      const x = tsScale.timeToCoordinate(sig.time);
      const y = this.series.priceToCoordinate(sig.price);
      if (x === null || y === null || x < -30 || x > plotW + 30) continue;

      if (sig.type === 'BUY') {
        const bw = 42, bh = 20, bx = x - bw / 2, by = Math.min(plotH - bh - 4, y + 10);
        ctx.fillStyle = '#16c784';
        ctx.beginPath();
        ctx.moveTo(x, by - 5); ctx.lineTo(x - 5, by + 1); ctx.lineTo(x + 5, by + 1);
        ctx.closePath(); ctx.fill();
        this._drawRoundRect(ctx, bx, by, bw, bh, 5);
        ctx.fillStyle = '#16c784'; ctx.fill();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.font = '800 10px system-ui, sans-serif'; ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('BUY', x, by + bh / 2 + 0.5);
        if (sig.sublabel) {
          ctx.font = '700 8.5px system-ui, sans-serif'; ctx.fillStyle = '#6ee7b7';
          ctx.fillText(sig.sublabel, x, by + bh + 8);
        }
      } else if (sig.type === 'SELL') {
        const bw = 44, bh = 20, bx = x - bw / 2, by = Math.max(4, y - bh - 10);
        ctx.fillStyle = '#ef4444';
        ctx.beginPath();
        ctx.moveTo(x, by + bh + 5); ctx.lineTo(x - 5, by + bh - 1); ctx.lineTo(x + 5, by + bh - 1);
        ctx.closePath(); ctx.fill();
        this._drawRoundRect(ctx, bx, by, bw, bh, 5);
        ctx.fillStyle = '#ef4444'; ctx.fill();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.font = '800 10px system-ui, sans-serif'; ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('SELL', x, by + bh / 2 + 0.5);
        if (sig.sublabel) {
          ctx.font = '700 8.5px system-ui, sans-serif'; ctx.fillStyle = '#fda4af';
          ctx.fillText(sig.sublabel, x, by - 7);
        }
      } else if (sig.type === 'TP1') {
        const bw = 30, bh = 16, bx = x - bw / 2, by = Math.max(4, y - bh - 8);
        this._drawRoundRect(ctx, bx, by, bw, bh, 4);
        ctx.fillStyle = 'rgba(34, 197, 94, 0.92)'; ctx.fill();
        ctx.font = '800 9px system-ui, sans-serif'; ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('TP1', x, by + bh / 2 + 0.5);
      } else if (sig.type === 'SL') {
        ctx.strokeStyle = '#ff4d6d'; ctx.lineWidth = 2;
        const cy = y - 10;
        ctx.beginPath();
        ctx.moveTo(x - 4, cy - 4); ctx.lineTo(x + 4, cy + 4);
        ctx.moveTo(x + 4, cy - 4); ctx.lineTo(x - 4, cy + 4);
        ctx.stroke();
        ctx.font = '800 8.5px system-ui, sans-serif'; ctx.fillStyle = '#fda4af';
        ctx.textAlign = 'center';
        ctx.fillText('SL', x, cy - 9);
      }
    }
  }

  _drawWeekendClosedBadge(ctx, plotW) {
    if (!isForexWeekendUTC() || plotW < 180) return;
    const txt = 'MARKET CLOSED • បិទចុងសប្តាហ៍';
    ctx.font = '800 9.5px system-ui, sans-serif';
    const tw = ctx.measureText(txt).width;
    const bw = tw + 16;
    const bh = 20;
    const bx = plotW - bw - 10;
    const by = 8;

    this._drawRoundRect(ctx, bx, by, bw, bh, 6);
    ctx.fillStyle = 'rgba(159, 18, 57, 0.78)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(251, 113, 133, 0.65)';
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = '#ffe4e6';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(txt, bx + bw / 2, by + bh / 2 + 0.5);
  }

  renderNow() {
    this.rafId = null;
    if (!this.ctx || !this.container) return;

    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const targetW = Math.round(w * dpr);
    const targetH = Math.round(h * dpr);
    if (this.canvas.width !== targetW || this.canvas.height !== targetH) {
      this.canvas.width = targetW;
      this.canvas.height = targetH;
      this.canvas.style.width = `${w}px`;
      this.canvas.style.height = `${h}px`;
    }

    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const priceScale = this.chart.priceScale('right');
    const axisW = (typeof priceScale.width === 'function' ? priceScale.width() : 72) || 72;
    const plotW = Math.max(0, w - axisW);
    const plotH = Math.max(0, h - 26);
    const tsScale = this.chart.timeScale();

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, plotW, plotH);
    ctx.clip();

    this._drawVolumeProfile(ctx, plotW, plotH);
    this._drawSmcZones(ctx, tsScale, plotW, plotH);
    this._drawSignals(ctx, tsScale, plotW, plotH);
    this._drawWeekendClosedBadge(ctx, plotW);

    for (const layerFn of this.customLayers) {
      try {
        layerFn(ctx, plotW, plotH, {
          priceToCoordinate: (p) => this.series.priceToCoordinate(p),
          drawRoundRect: (c, x, y, rw, rh, r) => this._drawRoundRect(c, x, y, rw, rh, r),
        });
      } catch (err) {
        console.error('[SmcOverlay:layer]', err);
      }
    }

    ctx.restore();
  }
}