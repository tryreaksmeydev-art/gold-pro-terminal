import { bus } from '../core/eventBus.js';
import { toNum } from '../core/utils.js';

function isForexWeekendUTC() {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun, 5=Fri, 6=Sat
  const hr = now.getUTCHours();
  return day === 6 || (day === 5 && hr >= 22) || (day === 0 && hr < 22);
}

class DirectFeedService {
  constructor() {
    this.ws = null;
    this.retryTimer = null;
    this.weekendWatchTimer = null;
    this.sourceType = '';
    this.forexOffset = 0;
    this.lastRawPrice = 0;
    this.lastEmittedBid = 0;
    this.spread = 0.25;
    this.lastDepth = { bidPct: 50, askPct: 50, bidVol: 0, askVol: 0, source: 'WAIT' };
  }

  setCalibration(offset, spread) {
    if (typeof offset === 'number' && Number.isFinite(offset)) {
      this.forexOffset = offset;
    }
    const sp = toNum(spread);
    if (sp !== null && sp > 0 && sp < 10) {
      this.spread = sp;
    }
  }

  recalibrateFromForex(forexBidPrice, spread) {
    const px = toNum(forexBidPrice);
    if (px !== null && px > 1000 && this.lastRawPrice > 1000) {
      const off = px - this.lastRawPrice;
      if (Math.abs(off) < 120) {
        this.forexOffset = off;
      }
    }
    const sp = toNum(spread);
    if (sp !== null && sp > 0 && sp < 10) {
      this.spread = sp;
    }
  }

  getSpread() {
    return this.spread;
  }

  getDepth() {
    return this.lastDepth;
  }

  _emitCalibrated(rawBidPrice) {
    // ថ្ងៃបិទផ្សារ Forex៖ ហាមបាញ់ Tick Crypto ចូលក្រាហ្វិកដាច់ខាត
    if (isForexWeekendUTC()) return;
    if (!Number.isFinite(rawBidPrice) || rawBidPrice <= 1000) return;

    this.lastRawPrice = rawBidPrice;
    const bid = Math.round((rawBidPrice + this.forexOffset) * 100) / 100;

    // ការពារ Bad Tick លោតខុសធម្មតា > $25 ក្នុង ១ វិនាទី
    if (this.lastEmittedBid > 1000 && Math.abs(bid - this.lastEmittedBid) > 25) {
      return;
    }
    this.lastEmittedBid = bid;

    const ask = Math.round((bid + this.spread) * 100) / 100;

    // ប្រើ price = bid ដូច MT5 ដើម្បីឱ្យ Header និងក្រាហ្វិកដូចគ្នា ១០០%
    bus.emit('market:tick', {
      price: bid,
      bid,
      ask,
      spread: this.spread,
      is_forex: true,
      fromDirect: true,
    });
  }

  _emitOrderBook(bids, asks, source) {
    if (isForexWeekendUTC()) return;
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

  _startWeekendWatcher() {
    if (this.weekendWatchTimer) return;
    this.weekendWatchTimer = setInterval(() => {
      if (!isForexWeekendUTC() && this.sourceType) {
        clearInterval(this.weekendWatchTimer);
        this.weekendWatchTimer = null;
        this.start(this.sourceType);
      }
    }, 60000);
  }

  start(sourceType) {
    const target = sourceType === 'XAUT' ? 'XAUT' : 'PAXG';
    this.sourceType = target;

    // បង្កក Feed ១០០% ពេលទីផ្សារ Forex បិទចុងសប្តាហ៍
    if (isForexWeekendUTC()) {
      this.stop(false);
      this.lastDepth = { bidPct: 50, askPct: 50, bidVol: 0, askVol: 0, source: 'CLOSED' };
      bus.emit('market:depth', this.lastDepth);
      this._startWeekendWatcher();
      return;
    }

    if (this.ws && this.sourceType === target) return;
    this.stop(false);
    if (document.hidden || !navigator.onLine) return;

    try {
      if (target === 'PAXG') {
        this.ws = new WebSocket(
          'wss://data-stream.binance.vision/stream?streams=paxgusdt@aggTrade/paxgusdt@bookTicker/paxgusdt@depth5@100ms'
        );
        this.ws.onmessage = (e) => {
          if (isForexWeekendUTC()) {
            this.stop(false);
            return;
          }
          try {
            const parsed = JSON.parse(e.data);
            const stream = parsed?.stream || '';
            const d = parsed?.data;
            if (!d) return;

            if (stream.includes('depth5') && d.bids && d.asks) {
              this._emitOrderBook(d.bids, d.asks, 'L2 100ms');
            } else if (d.b && d.a) {
              const bidPx = parseFloat(d.b);
              if (bidPx > 1000) this._emitCalibrated(bidPx);
            } else if (d.e === 'aggTrade') {
              const p = parseFloat(d.p);
              if (p > 1000) this._emitCalibrated(p);
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
          if (isForexWeekendUTC()) {
            this.stop(false);
            return;
          }
          try {
            const parsed = JSON.parse(e.data);
            const ch = parsed?.arg?.channel;
            const item = parsed?.data?.[0];
            if (!item) return;

            if (ch === 'books5' && item.bids && item.asks) {
              this._emitOrderBook(item.bids, item.asks, 'OKX L2');
            } else if (item.bidPx || item.last) {
              const bidPx = parseFloat(item.bidPx || item.last);
              if (bidPx > 1000) this._emitCalibrated(bidPx);
            }
          } catch {}
        };
      }

      if (this.ws) {
        this.ws.onclose = () => {
          this.ws = null;
          if (!isForexWeekendUTC() && !document.hidden && navigator.onLine && this.sourceType) {
            this.retryTimer = setTimeout(() => this.start(this.sourceType), 1200);
          }
        };
        this.ws.onerror = () => this.stop(false);
      }
    } catch {}
  }

  resume() {
    if (this.sourceType) this.start(this.sourceType);
  }

  stop(clearWatcher = true) {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (clearWatcher && this.weekendWatchTimer) {
      clearInterval(this.weekendWatchTimer);
      this.weekendWatchTimer = null;
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