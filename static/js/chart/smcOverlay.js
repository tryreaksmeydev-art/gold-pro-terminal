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

  _drawVolumeProfile(ctx, plotW, plotH) {
    if (!this.showZones || !this.quantData?.profile?.length) return;
    const maxBarW = Math.min(44, plotW * 0.11);
    const prof = this.quantData.profile;
    const barH = Math.max(3, Math.min(12, (plotH / prof.length) * 0.55));

    for (const item of prof) {
      const y = this.series.priceToCoordinate(item.p);
      if (y === null || y < 6 || y > plotH - 6) continue;
      const bw = Math.max(2, item.pct * maxBarW);
      ctx.fillStyle = item.is_poc ? 'rgba(250, 204, 21, 0.55)' : 'rgba(56, 189, 248, 0.16)';
      ctx.fillRect(plotW - bw, y - barH / 2, bw, barH);
    }
  }

  _drawOrderBlocks(ctx, tsScale, plotW, blocks, isBullish) {
    for (const b of blocks) {
      const yT = this.series.priceToCoordinate(b.top);
      const yB = this.series.priceToCoordinate(b.bottom);
      if (yT === null || yB === null) continue;

      const rX = tsScale.timeToCoordinate(b.start_time);
      const sx = rX === null ? 0 : Math.max(0, Math.min(plotW - 28, rX));
      const topY = Math.min(yT, yB);
      const boxH = Math.max(Math.abs(yB - yT), 12);
      const midY = topY + boxH / 2;
      const isOB = String(b.label || 'OB').toUpperCase() === 'OB';

      if (isBullish) {
        ctx.fillStyle = isOB ? 'rgba(16, 185, 129, 0.22)' : 'rgba(56, 189, 248, 0.13)';
        ctx.strokeStyle = isOB ? 'rgba(52, 211, 153, 0.88)' : 'rgba(56, 189, 248, 0.45)';
      } else {
        ctx.fillStyle = isOB ? 'rgba(244, 63, 94, 0.22)' : 'rgba(244, 63, 94, 0.11)';
        ctx.strokeStyle = isOB ? 'rgba(251, 113, 133, 0.88)' : 'rgba(251, 113, 133, 0.45)';
      }

      ctx.fillRect(sx, topY, plotW - sx, boxH);
      ctx.lineWidth = isOB ? 1.4 : 1;
      ctx.beginPath();
      ctx.moveTo(sx, topY);
      ctx.lineTo(plotW, topY);
      ctx.moveTo(sx, topY + boxH);
      ctx.lineTo(plotW, topY + boxH);
      ctx.stroke();

      if (isOB && boxH >= 14) {
        ctx.strokeStyle = isBullish ? 'rgba(110, 231, 183, 0.55)' : 'rgba(253, 164, 175, 0.55)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(sx, midY);
        ctx.lineTo(plotW, midY);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      if (boxH >= 12) {
        const refPrice = isBullish ? Number(b.top) : Number(b.bottom);
        const labelTxt = isOB
          ? `${isBullish ? 'Bullish OB' : 'Bearish OB'} • $${refPrice.toFixed(2)}`
          : 'FVG';
        ctx.font = isOB ? '800 9px system-ui, sans-serif' : '700 8px system-ui, sans-serif';
        ctx.fillStyle = isBullish
          ? (isOB ? '#6ee7b7' : 'rgba(147, 197, 253, 0.85)')
          : (isOB ? '#fda4af' : 'rgba(253, 164, 175, 0.85)');
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(labelTxt, sx + 6, midY);
      }
    }
  }

  _drawSmcZones(ctx, tsScale, plotW, plotH) {
    if (!this.showZones || !this.smcData) return;

    this._drawOrderBlocks(ctx, tsScale, plotW, this.smcData.demand_blocks || [], true);
    this._drawOrderBlocks(ctx, tsScale, plotW, this.smcData.supply_blocks || [], false);

    for (const lv of this.smcData.levels || []) {
      const y = this.series.priceToCoordinate(lv.price);
      if (y === null || y < 8 || y > plotH - 8) continue;
      const rX = tsScale.timeToCoordinate(lv.time);
      const sx = rX === null ? 0 : Math.max(0, Math.min(plotW - 60, rX));
      const isHi = lv.kind === 'high';

      ctx.strokeStyle = isHi ? 'rgba(248, 113, 113, 0.78)' : 'rgba(52, 211, 153, 0.78)';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(sx, y);
      ctx.lineTo(plotW - 4, y);
      ctx.stroke();

      ctx.font = '700 9.5px system-ui, sans-serif';
      ctx.fillStyle = isHi ? '#fca5a5' : '#6ee7b7';
      ctx.textAlign = 'right';
      ctx.textBaseline = isHi ? 'bottom' : 'top';
      ctx.fillText(lv.label, plotW - 8, isHi ? y - 3 : y + 3);
    }

    for (const br of this.smcData.breaks || []) {
      const y = this.series.priceToCoordinate(br.price);
      const x1 = tsScale.timeToCoordinate(br.start_time);
      const x2 = tsScale.timeToCoordinate(br.end_time);
      if (y === null || x2 === null || x2 < 0 || (x1 !== null && x1 > plotW)) continue;
      const sx = x1 === null ? 0 : Math.max(0, x1);
      const ex = Math.min(plotW, x2);
      if (ex - sx < 8) continue;

      const isUp = String(br.type || '').endsWith('_UP');
      ctx.strokeStyle = isUp ? 'rgba(52, 211, 153, 0.78)' : 'rgba(248, 113, 113, 0.78)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
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
        const bw = 42, bh = 21, bx = x - bw / 2, by = Math.min(plotH - bh - 4, y + 10);
        ctx.fillStyle = '#16c784';
        ctx.beginPath();
        ctx.moveTo(x, by - 5); ctx.lineTo(x - 5, by + 1); ctx.lineTo(x + 5, by + 1);
        ctx.closePath(); ctx.fill();
        this._drawRoundRect(ctx, bx, by, bw, bh, 5);
        ctx.fillStyle = '#16c784'; ctx.fill();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.font = '800 10.5px system-ui, sans-serif'; ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('BUY', x, by + bh / 2 + 0.5);
        if (sig.sublabel) {
          ctx.font = '700 8.5px system-ui, sans-serif'; ctx.fillStyle = '#6ee7b7';
          ctx.fillText(sig.sublabel, x, by + bh + 8);
        }
      } else if (sig.type === 'SELL') {
        const bw = 44, bh = 21, bx = x - bw / 2, by = Math.max(4, y - bh - 10);
        ctx.fillStyle = '#ef4444';
        ctx.beginPath();
        ctx.moveTo(x, by + bh + 5); ctx.lineTo(x - 5, by + bh - 1); ctx.lineTo(x + 5, by + bh - 1);
        ctx.closePath(); ctx.fill();
        this._drawRoundRect(ctx, bx, by, bw, bh, 5);
        ctx.fillStyle = '#ef4444'; ctx.fill();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.font = '800 10.5px system-ui, sans-serif'; ctx.fillStyle = '#ffffff';
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