import { TF_SECONDS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { $, toNum, sanitizeCandles, sanitizeLineData } from '../core/utils.js';
import { SmcOverlay } from './smcOverlay.js';

class ChartEngine {
  constructor() {
    this.chart = null;
    this.candleSeries = null;
    this.ema20Series = null;
    this.ema50Series = null;
    this.vwapSeries = null;
    this.overlay = null;

    this.container = null;
    this.currentInterval = '15m';
    this.lastPrice = null;
    this.lastCandle = null;
    this.prevCandleClose = null;
    this.candleCount = 0;
    this.isFirstLoad = true;
    this.isFullscreen = false;

    this.tickRaf = null;
    this.pendingTick = null;
    this.lastOverlayTickTs = 0;
    this.countdownTimer = null;

    this.bidLine = null;
    this.askLine = null;
    this.priceLines = { pdh: null, pdl: null, ah: null, al: null, entry: null, sl: null, tp1: null, poc: null };
    this.lastInstData = null;
    this.activePlan = null;
    this.activeSide = 'WAIT';
    this.showInst = false;
    this.showSlTp = true;
  }

  init({ interval = '15m', prefs = {} } = {}) {
    this.currentInterval = interval;
    this.showInst = Boolean(prefs.showInst);
    this.showSlTp = prefs.showSlTp !== false;

    this.container = $('chart');
    if (!this.container || typeof window.LightweightCharts === 'undefined') {
      console.error('[ChartEngine] LightweightCharts or #chart not found');
      return;
    }

    const LWC = window.LightweightCharts;
    this.chart = LWC.createChart(this.container, {
      layout: { background: { color: 'transparent' }, textColor: '#cbd5e1', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(255,255,255,.035)' }, horzLines: { color: 'rgba(255,255,255,.035)' } },
      crosshair: { mode: LWC.CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'rgba(255,255,255,.1)', autoScale: true, scaleMargins: { top: 0.14, bottom: 0.16 } },
      timeScale: { borderColor: 'rgba(255,255,255,.1)', timeVisible: true, secondsVisible: false, rightOffset: 7, barSpacing: 9, minBarSpacing: 3, fixLeftEdge: true },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    });

    // បង្កើតខ្សែ EMA & VWAP មុន ដើម្បីឱ្យទៀនស្ថិតនៅស្រទាប់លើបង្អស់ច្បាស់ស្អាតដូច TradingView Pro
    this.ema50Series = this.chart.addLineSeries({
      color: 'rgba(245, 158, 11, 0.85)',
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 3,
    });

    this.ema20Series = this.chart.addLineSeries({
      color: 'rgba(56, 189, 248, 0.90)',
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 3,
    });

    this.vwapSeries = this.chart.addLineSeries({
      color: 'rgba(192, 132, 252, 0.85)',
      lineWidth: 1.8,
      lineStyle: LWC.LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'VWAP',
    });

    // ទៀនក្រាហ្វិកទំនើប (Neon Emerald / Crimson Rose) នៅស្រទាប់លើបង្អស់
    this.candleSeries = this.chart.addCandlestickSeries({
      upColor: '#00d992',
      downColor: '#ff4769',
      borderVisible: true,
      borderUpColor: '#2bffb8',
      borderDownColor: '#ff6b86',
      wickUpColor: '#2bffb8',
      wickDownColor: '#ff6b86',
      lastValueVisible: false,
      priceLineVisible: false,
    });

    this.overlay = new SmcOverlay(this.container, $('signalCanvas'), this.chart, this.candleSeries);
    this.overlay.setOptions({
      showZones: prefs.showZones !== false,
      showSignals: prefs.showSignals !== false,
    });

    this._bindChartEvents();
    this._bindBusEvents();
    this._startCountdownTimer();
  }

  _updateLiveBidAskLines(bid, ask, fallbackPrice) {
    const LWC = window.LightweightCharts;
    if (!this.candleSeries || !LWC) return;
    const bPx = toNum(bid) ?? fallbackPrice;
    const aPx = toNum(ask) ?? (bPx ? bPx + 0.25 : null);

    try {
      if (aPx && aPx > 0) {
        if (!this.askLine) {
          this.askLine = this.candleSeries.createPriceLine({
            price: aPx, color: 'rgba(244, 63, 94, 0.85)', lineWidth: 1,
            lineStyle: LWC.LineStyle.Solid, axisLabelVisible: true, title: '',
          });
        } else {
          this.askLine.applyOptions({ price: aPx });
        }
      }
      if (bPx && bPx > 0) {
        if (!this.bidLine) {
          this.bidLine = this.candleSeries.createPriceLine({
            price: bPx, color: 'rgba(16, 185, 129, 0.85)', lineWidth: 1,
            lineStyle: LWC.LineStyle.Dotted, axisLabelVisible: false, title: '',
          });
        } else {
          this.bidLine.applyOptions({ price: bPx });
        }
      }
    } catch {}
  }

  _bindChartEvents() {
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      if (this.lastPrice) this._updateAxisPill(this.lastPrice, 0);
      this.overlay.scheduleRender();
    });
    this.chart.timeScale().subscribeVisibleTimeRangeChange(() => this.overlay.scheduleRender());

    ['pointermove', 'wheel', 'touchmove', 'touchend', 'mouseup'].forEach((ev) => {
      this.container.addEventListener(ev, () => {
        this.overlay.scheduleRender();
        if (this.lastPrice) this._updateAxisPill(this.lastPrice, 0);
      }, { passive: true });
    });

    new ResizeObserver((entries) => {
      for (const e of entries) {
        if (e.contentRect.width > 0 && e.contentRect.height > 0) {
          this.chart.applyOptions({ width: e.contentRect.width, height: e.contentRect.height });
          requestAnimationFrame(() => {
            if (this.lastPrice) this._updateAxisPill(this.lastPrice, 0);
            this.overlay.renderNow();
            bus.emit('chart:resized');
          });
        }
      }
    }).observe(this.container);

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isFullscreen) this.toggleFullscreen(false);
    });

    window.addEventListener('orientationchange', () => {
      setTimeout(() => {
        if (!this.container || !this.chart) return;
        this.chart.applyOptions({ width: this.container.clientWidth, height: this.container.clientHeight });
        if (this.lastPrice) this._updateAxisPill(this.lastPrice, 0);
        this.overlay.scheduleRender();
        bus.emit('chart:resized');
      }, 220);
    });
  }

  _bindBusEvents() {
    bus.on('interval:changed', ({ interval }) => {
      this.currentInterval = interval;
      this.isFirstLoad = true;
      this.lastCandle = null;
      this.prevCandleClose = null;
    });

    bus.on('market:full', ({ data }) => this._applyFullData(data));
    bus.on('market:tick', (msg) => this._queueTick(msg));
  }

  _applyFullData(data) {
    const cleanCandles = sanitizeCandles(data.candles);
    if (!cleanCandles.length) return;

    this.candleCount = cleanCandles.length;
    this.lastCandle = { ...cleanCandles[this.candleCount - 1] };
    this.prevCandleClose = this.candleCount > 1 ? cleanCandles[this.candleCount - 2].close : this.lastCandle.open;

    if (data.inst_levels) this.lastInstData = data.inst_levels;

    this.overlay.setData({
      smc: data.smc || null,
      signals: Array.isArray(data.chart_signals) ? data.chart_signals : [],
      quant: data.quant || null,
    });

    try {
      this.candleSeries.setData(cleanCandles);
      const ema20 = sanitizeLineData(data.ema20_line);
      const ema50 = sanitizeLineData(data.ema50_line);
      const vwap = sanitizeLineData(data.vwap_line);
      if (ema20.length) this.ema20Series.setData(ema20);
      if (ema50.length) this.ema50Series.setData(ema50);
      if (vwap.length && this.vwapSeries) this.vwapSeries.setData(vwap);

      const pocVal = data.quant?.poc ?? null;
      this._setLine('poc', pocVal, 'rgba(250, 204, 21, 0.72)', 'POC', window.LightweightCharts.LineStyle.Dotted);

      this.drawInstLines();
      this.drawSlTpLines();
    } catch (err) {
      console.error('[ChartEngine:setData]', err);
    }

    const px = toNum(data.price);
    if (px !== null && px > 0) {
      this._commitPrice(px, toNum(data.change), toNum(data.change_pct), data.bid, data.ask, data.spread);
    }

    if (this.isFirstLoad) {
      this.resetView();
      this.isFirstLoad = false;
    } else {
      this.overlay.scheduleRender();
    }
  }

  _queueTick(msg) {
    this.pendingTick = msg;
    if (!this.tickRaf) {
      this.tickRaf = requestAnimationFrame(() => this._flushTick());
    }
  }

  _flushTick() {
    this.tickRaf = null;
    const msg = this.pendingTick;
    if (!msg) return;
    this.pendingTick = null;

    const lp = typeof msg === 'number' ? msg : toNum(msg.price);
    if (lp === null || lp <= 0) return;

    if (this.lastCandle?.time) {
      const tfSec = TF_SECONDS[this.currentInterval] || 60;
      const bt = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
      if (bt > this.lastCandle.time) {
        this.prevCandleClose = this.lastCandle.close;
        this.lastCandle = { time: bt, open: lp, high: lp, low: lp, close: lp };
        this.candleCount++;
      } else {
        this.lastCandle = {
          ...this.lastCandle,
          close: lp,
          high: Math.max(this.lastCandle.high, lp),
          low: Math.min(this.lastCandle.low, lp),
        };
      }
      try {
        this.candleSeries.update(this.lastCandle);
      } catch {}

      const now = performance.now();
      if (now - this.lastOverlayTickTs > 180) {
        this.lastOverlayTickTs = now;
        this.overlay.scheduleRender();
      }
    }

    const ch = this.prevCandleClose ? lp - this.prevCandleClose : 0;
    const chPct = this.prevCandleClose ? (ch / this.prevCandleClose) * 100 : 0;
    this._commitPrice(lp, ch, chPct, msg.bid, msg.ask, msg.spread);
  }

  _commitPrice(price, change, changePct, bid, ask, spread) {
    const direction = this.lastPrice !== null ? (price > this.lastPrice ? 1 : price < this.lastPrice ? -1 : 0) : 0;
    this.lastPrice = price;
    this._updateLiveBidAskLines(bid, ask, price);
    this._updateAxisPill(toNum(bid) ?? price, direction);

    bus.emit('price:updated', { price, change, changePct, direction, bid, ask, spread });
  }

  _updateAxisPill(price, dir = 0) {
    const pill = $('axisLivePill');
    const pillPx = $('pillPrice');
    if (!pill || !pillPx || !Number.isFinite(price) || price <= 0) return;

    pillPx.textContent = price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (dir > 0) pill.style.backgroundColor = '#00a87e';
    else if (dir < 0) pill.style.backgroundColor = '#e11d48';

    const y = this.candleSeries.priceToCoordinate(price);
    const sh = this.container.clientHeight;
    if (y === null || y < 14 || y > sh - 28) {
      pill.classList.add('hidden');
    } else {
      pill.classList.remove('hidden');
      pill.style.transform = `translate3d(0, ${y}px, 0) translateY(-50%)`;
    }
  }

  _startCountdownTimer() {
    if (this.countdownTimer) clearInterval(this.countdownTimer);
    const tick = () => {
      const tfSec = TF_SECONDS[this.currentInterval] || 60;
      const rem = tfSec - (Math.floor(Date.now() / 1000) % tfSec);
      const h = Math.floor(rem / 3600);
      const m = Math.floor((rem % 3600) / 60);
      const s = rem % 60;
      const text = h > 0
        ? `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
        : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

      const el = $('pillTimer');
      if (el && el.textContent !== 'OFFLINE') el.textContent = text;
      bus.emit('candle:countdown', { remSec: rem, tfSec, text, interval: this.currentInterval });
    };
    tick();
    this.countdownTimer = setInterval(tick, 1000);
  }

  _setLine(key, price, color, title, style = window.LightweightCharts.LineStyle.Dashed) {
    try {
      if (this.priceLines[key]) {
        this.candleSeries.removePriceLine(this.priceLines[key]);
        this.priceLines[key] = null;
      }
      const v = toNum(price);
      if (v !== null && v > 0) {
        this.priceLines[key] = this.candleSeries.createPriceLine({
          price: v, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title,
        });
      }
    } catch {}
  }

  setShowInst(flag) {
    this.showInst = Boolean(flag);
    this.drawInstLines();
  }

  drawInstLines() {
    if (!this.showInst || !this.lastInstData) {
      ['pdh', 'pdl', 'ah', 'al'].forEach((k) => this._setLine(k, null));
      return;
    }
    this._setLine('pdh', this.lastInstData.pdh, 'rgba(192,132,252,.55)', 'PDH');
    this._setLine('pdl', this.lastInstData.pdl, 'rgba(192,132,252,.55)', 'PDL');
    this._setLine('ah', this.lastInstData.asian_high, 'rgba(34,211,238,.5)', 'ASIAN H');
    this._setLine('al', this.lastInstData.asian_low, 'rgba(34,211,238,.5)', 'ASIAN L');
  }

  setSlTpPlan(plan, side, showSlTp = this.showSlTp) {
    this.activePlan = plan;
    this.activeSide = side || 'WAIT';
    this.showSlTp = Boolean(showSlTp);
    this.drawSlTpLines();
  }

  drawSlTpLines() {
    const solid = window.LightweightCharts.LineStyle.Solid;
    if (!this.showSlTp || !this.activePlan || this.activeSide === 'WAIT') {
      ['entry', 'sl', 'tp1'].forEach((k) => this._setLine(k, null));
      return;
    }
    this._setLine('entry', this.activePlan.entry, 'rgba(250,204,21,.75)', 'ENTRY', solid);
    this._setLine('sl', this.activePlan.sl, 'rgba(244,63,94,.85)', 'SL', solid);
    this._setLine('tp1', this.activePlan.tp ?? this.activePlan.tp1, 'rgba(16,185,129,.85)', 'TP1', solid);
  }

  setOverlayOptions(opts) { this.overlay?.setOptions(opts); }
  registerOverlayLayer(drawFn) { return this.overlay ? this.overlay.registerLayer(drawFn) : () => {}; }
  scheduleOverlay() { this.overlay?.scheduleRender(); }

  resetView() {
    try {
      this.chart.priceScale('right').applyOptions({ autoScale: true });
      if (this.candleCount > 10) {
        this.chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, this.candleCount - (this.isFullscreen ? 65 : 46)),
          to: this.candleCount + 6,
        });
      } else {
        this.chart.timeScale().fitContent();
      }
      requestAnimationFrame(() => {
        if (this.lastPrice) this._updateAxisPill(this.lastPrice, 0);
        this.overlay.renderNow();
      });
    } catch {}
  }

  toggleFullscreen(force = null) {
    this.isFullscreen = force !== null ? Boolean(force) : !this.isFullscreen;
    const card = $('chartCard');
    if (card) card.classList.toggle('is-fullscreen', this.isFullscreen);
    document.body.style.overflowY = this.isFullscreen ? 'hidden' : '';

    ['fsIconEnter', 'fsIconExit', 'fsLiveInfo', 'fsTfBar'].forEach((id) => {
      const el = $(id);
      if (!el) return;
      const show = id === 'fsIconEnter' ? !this.isFullscreen : this.isFullscreen;
      el.classList.toggle('hidden', !show);
      el.classList.toggle('flex', show);
    });

    setTimeout(() => {
      if (!this.container || !this.chart) return;
      this.chart.applyOptions({ width: this.container.clientWidth, height: this.container.clientHeight });
      this.resetView();
      bus.emit('chart:resized');
    }, 60);
  }

  getLastPrice() { return this.lastPrice; }
  getContainer() { return this.container; }
}

export const chartEngine = new ChartEngine();