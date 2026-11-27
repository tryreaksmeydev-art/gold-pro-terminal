import { TF_SECONDS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { toNum } from '../core/utils.js';
import { apiService } from './apiService.js';
import { directFeed } from './directFeed.js';

class SocketService {
  constructor() {
    const savedTf = Store.get(Store.KEYS.TF, '15m');
    this.interval = TF_SECONDS[savedTf] ? savedTf : '15m';
    this.ws = null;
    this.reconnectTimer = null;
    this.reconnectDelay = 2000;
    this.wsFailCount = 0;
    this.httpPollTimer = null;
    this.tfDebounce = null;
    this.lastMsgTs = Date.now();
    this.watchdogTimer = null;
    this._bound = false;
  }

  getInterval() {
    return this.interval;
  }

  init() {
    if (!this._bound) {
      this._bindLifecycle();
      this._startWatchdog();
      this._bound = true;
    }
    this.connect();
  }

  switchInterval(tf) {
    if (!TF_SECONDS[tf] || tf === this.interval) return;
    clearTimeout(this.tfDebounce);
    this.tfDebounce = setTimeout(() => {
      this.interval = tf;
      Store.set(Store.KEYS.TF, tf);
      bus.emit('interval:changed', { interval: tf });
      this.connect();
    }, 100);
  }

  connect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._closeWs();

    const cached = Store.get(Store.KEYS.CACHE_PREFIX + this.interval);
    if (cached && Array.isArray(cached.candles) && cached.candles.length) {
      this._handleFullData(cached, true);
    } else {
      bus.emit('market:loading', { loading: true });
    }

    if (!navigator.onLine) {
      bus.emit('connection:status', { status: 'OFFLINE' });
      return;
    }
    if (document.hidden) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.lastMsgTs = Date.now();
    this.ws = new WebSocket(`${proto}//${location.host}/ws/gold/${this.interval}`);

    this.ws.onopen = () => {
      this.wsFailCount = 0;
      this.reconnectDelay = 2000;
      this.lastMsgTs = Date.now();
      this._stopHttpFallback();
      bus.emit('connection:status', { status: 'TURBO' });
    };

    this.ws.onmessage = (event) => {
      this.lastMsgTs = Date.now();
      try {
        const msg = JSON.parse(event.data);
        if (!msg || msg.type === 'ping') return;
        if (msg.type === 'full') {
          this._handleFullData(msg, false);
        } else if (msg.type === 'tick') {
          const tickPx = toNum(msg.price);
          if (tickPx !== null && tickPx > 0) {
            if (msg.is_forex) {
              directFeed.recalibrateFromForex(tickPx, msg.spread);
            }
            bus.emit('market:tick', msg);
          }
        }
      } catch {}
    };

    this.ws.onerror = () => {
      try { this.ws?.close(); } catch {}
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.wsFailCount++;
      bus.emit('connection:status', { status: 'SYNC' });
      if (this.wsFailCount >= 2) this._startHttpFallback();
      if (!document.hidden && navigator.onLine) {
        this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 12000);
      }
    };
  }

  _handleFullData(data, fromCache) {
    if (!data || !Array.isArray(data.candles) || !data.candles.length) return;
    if (data.interval && data.interval !== this.interval) return;

    directFeed.setCalibration(data.basis_offset, data.spread);
    if (!fromCache) {
      Store.set(Store.KEYS.CACHE_PREFIX + this.interval, data);
      directFeed.start(data.raw_feed === 'XAUT' ? 'XAUT' : 'PAXG');
    }

    bus.emit('market:loading', { loading: false });
    bus.emit('market:full', { data, fromCache, interval: this.interval });
  }

  async _fetchRestOnce() {
    if (document.hidden || !navigator.onLine) return;
    try {
      const data = await apiService.fetchGold(this.interval);
      this._handleFullData(data, false);
      bus.emit('connection:status', { status: 'SYNC' });
    } catch {}
  }

  _startHttpFallback() {
    if (!this.httpPollTimer) {
      this._fetchRestOnce();
      this.httpPollTimer = setInterval(() => this._fetchRestOnce(), 8000);
    }
  }

  _stopHttpFallback() {
    if (this.httpPollTimer) {
      clearInterval(this.httpPollTimer);
      this.httpPollTimer = null;
    }
  }

  _closeWs() {
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
  }

  _startWatchdog() {
    if (this.watchdogTimer) clearInterval(this.watchdogTimer);
    this.watchdogTimer = setInterval(() => {
      if (
        !document.hidden &&
        navigator.onLine &&
        this.ws &&
        this.ws.readyState === WebSocket.OPEN &&
        Date.now() - this.lastMsgTs > 35000
      ) {
        this.connect();
      }
    }, 10000);
  }

  _bindLifecycle() {
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
        this._stopHttpFallback();
        directFeed.stop();
      } else {
        this.connect();
        directFeed.resume();
      }
    });

    window.addEventListener('online', () => {
      bus.emit('connection:status', { status: 'SYNC' });
      this.connect();
      directFeed.resume();
    });

    window.addEventListener('offline', () => {
      directFeed.stop();
      this._stopHttpFallback();
      this._closeWs();
      bus.emit('connection:status', { status: 'OFFLINE' });
    });
  }
}

export const socketService = new SocketService();