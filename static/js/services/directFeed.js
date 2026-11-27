import { bus } from '../core/eventBus.js';
import { toNum } from '../core/utils.js';

class DirectFeedService {
  constructor() {
    this.ws = null;
    this.retryTimer = null;
    this.sourceType = '';
    this.forexOffset = 0;
    this.lastRawPrice = 0;
    this.spread = 0.20;
    this.lastDepth = { bidPct: 50, askPct: 50, bidVol: 0, askVol: 0, source: 'WAIT' };
  }

  setCalibration(offset, spread) {
    if (typeof offset === 'number' && Number.isFinite(offset)) {
      this.forexOffset = offset;
    }
    const sp = toNum(spread);
    if (sp !== null && sp > 0) {
      this.spread = sp;
    }
  }

  recalibrateFromForex(forexPrice, spread) {
    const px = toNum(forexPrice);
    if (px !== null && px > 0 && this.lastRawPrice > 0) {
      const off = px - this.lastRawPrice;
      if (Math.abs(off) < 120) {
        this.forexOffset = off;
      }
    }
    const sp = toNum(spread);
    if (sp !== null && sp > 0) {
      this.spread = sp;
    }
  }

  getSpread() {
    return this.spread;
  }

  getDepth() {
    return this.lastDepth;
  }

  _emitCalibrated(rawPrice) {
    if (!Number.isFinite(rawPrice) || rawPrice <= 0) return;
    this.lastRawPrice = rawPrice;
    const price = Math.round((rawPrice + this.forexOffset) * 100) / 100;
    const half = this.spread / 2;
    bus.emit('market:tick', {
      price,
      bid: Math.round((price - half) * 100) / 100,
      ask: Math.round((price + half) * 100) / 100,
      spread: this.spread,
      is_forex: true,
      fromDirect: true,
    });
  }

  _emitOrderBook(bids, asks, source) {
    if (!Array.isArray(bids) || !Array.isArray(asks)) return;
    let bidVol = 0;
    let askVol = 0;
    for (let i = 0; i < Math.min(5, bids.length); i++) bidVol += parseFloat(bids[i][1]) || 0;
    for (let i = 0; i < Math.min(5, asks.length); i++) askVol += parseFloat(asks[i][1]) || 0;
    const tot = bidVol + askVol;
    if (tot <= 0) return;
    const bidPct = Math.round((bidVol / tot) * 100);
    this.lastDepth = {
      bidPct,
      askPct: 100 - bidPct,
      bidVol: Math.round(bidVol * 100) / 100,
      askVol: Math.round(askVol * 100) / 100,
      source,
    };
    bus.emit('market:depth', this.lastDepth);
  }

  start(sourceType) {
    const target = sourceType === 'XAUT' ? 'XAUT' : 'PAXG';
    if (this.ws && this.sourceType === target) return;
    this.stop();
    this.sourceType = target;
    if (document.hidden || !navigator.onLine) return;

    try {
      if (target === 'PAXG') {
        this.ws = new WebSocket(
          'wss://data-stream.binance.vision/stream?streams=paxgusdt@aggTrade/paxgusdt@bookTicker/paxgusdt@depth5@100ms'
        );
        this.ws.onmessage = (e) => {
          try {
            const parsed = JSON.parse(e.data);
            const stream = parsed?.stream || '';
            const d = parsed?.data;
            if (!d) return;

            if (stream.includes('depth5') && d.bids && d.asks) {
              this._emitOrderBook(d.bids, d.asks, 'L2 100ms');
            } else if (d.e === 'aggTrade') {
              const p = parseFloat(d.p);
              if (p > 0) this._emitCalibrated(p);
            } else if (d.b && d.a) {
              const mid = (parseFloat(d.b) + parseFloat(d.a)) / 2;
              if (mid > 0) this._emitCalibrated(mid);
            }
          } catch {}
        };
      } else {
        this.ws = new WebSocket('wss://ws.okx.com:8443/ws/v5/public');
        this.ws.onopen = () => {
          try {
            this.ws?.send(JSON.stringify({
              op: 'subscribe',
              args: [
                { channel: 'tickers', instId: 'XAUT-USDT' },
                { channel: 'books5', instId: 'XAUT-USDT' },
              ],
            }));
          } catch {}
        };
        this.ws.onmessage = (e) => {
          try {
            const parsed = JSON.parse(e.data);
            const ch = parsed?.arg?.channel;
            const item = parsed?.data?.[0];
            if (!item) return;
            if (ch === 'books5' && item.bids && item.asks) {
              this._emitOrderBook(item.bids, item.asks, 'OKX L2');
            } else if (item.last) {
              const last = parseFloat(item.last);
              if (last > 0) this._emitCalibrated(last);
            }
          } catch {}
        };
      }

      if (this.ws) {
        this.ws.onclose = () => {
          this.ws = null;
          if (!document.hidden && navigator.onLine && this.sourceType) {
            this.retryTimer = setTimeout(() => this.start(this.sourceType), 1000);
          }
        };
        this.ws.onerror = () => this.stop();
      }
    } catch {}
  }

  resume() {
    if (this.sourceType) this.start(this.sourceType);
  }

  stop() {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
  }
}

export const directFeed = new DirectFeedService();