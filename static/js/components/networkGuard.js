import { bus } from '../core/eventBus.js';
import { $, playAlertChime } from '../core/utils.js';
import { socketService } from '../services/socketService.js';
import { directFeed } from '../services/directFeed.js';

const SLOW_TICK_MS = 7000;        // ឈប់លោតលើស 7s -> សេវាយឺត (SLOW)
const STALE_TICK_MS = 15000;      // ឈប់លោតលើស 15s -> តម្លៃគាំង (STALE)
const HIGH_PING_MS = 1500;        // Ping លើស 1.5s -> សេវាយឺត (SLOW)
const HEARTBEAT_MS = 5000;        // ពិនិត្យ /health រៀងរាល់ 5s
const AUTO_RECONNECT_MS = 6000;   // ព្យាយាមភ្ជាប់ឡើងវិញស្វ័យប្រវត្តិរៀងរាល់ 6s ពេលដាច់/គាំង
const CHECK_UI_MS = 1000;         // អាប់ដេតវិនាទីគាំងរៀងរាល់ 1s

function isForexWeekendUTC() {
  const now = new Date();
  const day = now.getUTCDay();   // 0=Sun, 5=Fri, 6=Sat
  const hr = now.getUTCHours();
  return day === 6 || (day === 5 && hr >= 22) || (day === 0 && hr < 22);
}

class NetworkGuard {
  constructor() {
    this.state = 'CONNECTING'; // LIVE | SLOW | STALE | OFFLINE
    this.lastLiveTs = Date.now();
    this.graceUntilTs = Date.now() + 8000;
    this.lastPingMs = null;
    this.serverReachable = true;
    this.pingFailCount = 0;
    this.isPinging = false;
    this.alertedDrop = false;
    this.lastReconnectTryTs = 0;

    this.uiTimer = null;
    this.pingTimer = null;
    this.shieldEl = null;
    this.shieldTitleEl = null;
    this.shieldMsgEl = null;
    this.shieldAgeEl = null;
  }

  init() {
    this._injectChartShield();
    this._bindEvents();
    this._startLoops();
    this._pingServer();
  }

  _injectChartShield() {
    const stage = $('chartStage');
    if (!stage || $('netFreezeShield')) return;

    const div = document.createElement('div');
    div.id = 'netFreezeShield';
    div.className = 'hidden absolute inset-0 z-[28] bg-[#070b13]/78 backdrop-blur-[3px] flex flex-col items-center justify-center p-4 text-center';
    div.innerHTML = `
      <div class="max-w-xs w-full rounded-2xl bg-[#0e1526]/95 border-2 border-rose-500/70 shadow-[0_18px_50px_rgba(244,63,94,.35)] p-4 space-y-2.5">
        <div class="mx-auto w-10 h-10 rounded-full bg-rose-500/20 border border-rose-500/50 flex items-center justify-center text-rose-400">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="2" x2="22" y1="2" y2="22"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M2 8.82a15 15 0 0 1 4.17-2.65"/><path d="M10.66 5c4.01-.36 8.14.9 11.34 3.76"/><path d="M16.85 11.25a10 10 0 0 1 2.22 1.68"/><path d="M5 13a10 10 0 0 1 5.24-2.76"/><line x1="12" x2="12.01" y1="20" y2="20"/></svg>
        </div>
        <div id="netShieldTitle" class="text-xs sm:text-sm font-black text-rose-400 uppercase tracking-wide">ដាច់សេវា • តម្លៃកំពុងគាំង!</div>
        <div id="netShieldMsg" class="text-[11px] text-gray-200 leading-relaxed">
          ហាមចូល Order លើតម្លៃចាស់នេះ — អ៊ីនធឺណិត ឬ Server កំពុងដាច់ការភ្ជាប់។
        </div>
        <div id="netShieldAge" class="text-[10.5px] font-bold text-amber-300 tabular-nums">គាំងអស់ -- វិនាទី</div>
        <button id="netReconnectBtn" type="button" class="w-full py-2 px-3 rounded-xl bg-rose-500 hover:bg-rose-400 text-white text-xs font-black active:scale-95 transition">
          ភ្ជាប់ឡើងវិញឥឡូវនេះ
        </button>
      </div>
    `;
    stage.appendChild(div);

    this.shieldEl = div;
    this.shieldTitleEl = $('netShieldTitle');
    this.shieldMsgEl = $('netShieldMsg');
    this.shieldAgeEl = $('netShieldAge');

    $('netReconnectBtn')?.addEventListener('click', () => this.forceReconnect());
  }

  _bindEvents() {
    bus.on('market:tick', () => this._markLiveActivity());
    bus.on('market:full', ({ fromCache }) => {
      if (!fromCache) this._markLiveActivity();
    });

    bus.on('connection:status', ({ status }) => {
      if (status === 'OFFLINE') {
        this.pingFailCount = Math.max(this.pingFailCount, 2);
        this.serverReachable = false;
        this._evaluateState();
      } else if (status === 'ONLINE' || status === 'LIVE') {
        this.pingFailCount = 0;
        this.serverReachable = true;
        this._evaluateState();
      }
    });

    window.addEventListener('offline', () => {
      this.serverReachable = false;
      this.pingFailCount = 2;
      this.lastPingMs = null;
      this._evaluateState();
    });

    window.addEventListener('online', () => {
      this.graceUntilTs = Date.now() + 5000;
      this.forceReconnect(false);
    });

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        this.graceUntilTs = Date.now() + 5000;
        this._pingServer();
        if (this.state === 'STALE' || this.state === 'OFFLINE') {
          this.forceReconnect(false);
        }
      }
    });
  }

  _markLiveActivity() {
    const wasDown = this.state === 'STALE' || this.state === 'OFFLINE';
    this.lastLiveTs = Date.now();
    this.serverReachable = true;
    this.pingFailCount = 0;

    if (wasDown) {
      this.alertedDrop = false;
      bus.emit('toast:popup', {
        type: 'win',
        badge: 'ONLINE',
        title: 'ភ្ជាប់សេវាឡើងវិញជោគជ័យ!',
        body: 'តម្លៃមាសផ្ទាល់កំពុងអាប់ដេតធម្មតាវិញហើយ។',
        playSound: false,
        autoHideMs: 3200,
      });
    }
    this._evaluateState();
  }

  async _pingServer() {
    if (document.hidden || this.isPinging) return;
    if (!navigator.onLine) {
      this.serverReachable = false;
      this.pingFailCount = 2;
      this.lastPingMs = null;
      this._evaluateState();
      return;
    }

    this.isPinging = true;
    const t0 = performance.now();
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 3200);

    try {
      const res = await fetch(`/health?t=${Date.now()}`, {
        cache: 'no-store',
        signal: ctrl.signal,
      });
      if (res.ok) {
        this.serverReachable = true;
        this.pingFailCount = 0;
        this.lastPingMs = Math.round(performance.now() - t0);
      } else {
        this.pingFailCount++;
        if (this.pingFailCount >= 2) this.serverReachable = false;
        this.lastPingMs = null;
      }
    } catch {
      this.pingFailCount++;
      if (this.pingFailCount >= 2) this.serverReachable = false;
      this.lastPingMs = null;
    } finally {
      clearTimeout(tid);
      this.isPinging = false;
      this._evaluateState();
    }
  }

  _startLoops() {
    if (this.uiTimer) clearInterval(this.uiTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);

    this.uiTimer = setInterval(() => {
      if (document.hidden) return;
      this._evaluateState();

      // ភ្ជាប់ឡើងវិញស្វ័យប្រវត្តិពេលសេវាគាំង ឬដាច់ (តែទូរស័ព្ទមានអ៊ីនធឺណិត)
      if ((this.state === 'STALE' || this.state === 'OFFLINE') && navigator.onLine) {
        const now = Date.now();
        if (now - this.lastReconnectTryTs >= AUTO_RECONNECT_MS) {
          this.lastReconnectTryTs = now;
          socketService.connect();
          directFeed.resume();
        }
      }
    }, CHECK_UI_MS);

    this.pingTimer = setInterval(() => {
      if (!document.hidden) this._pingServer();
    }, HEARTBEAT_MS);
  }

  forceReconnect(showText = true) {
    this.graceUntilTs = Date.now() + 6000;
    this.lastReconnectTryTs = Date.now();
    if (showText && this.shieldAgeEl) {
      this.shieldAgeEl.textContent = 'កំពុងភ្ជាប់ឡើងវិញ...';
    }
    socketService.connect();
    directFeed.resume();
    this._pingServer();
  }

  _evaluateState() {
    const now = Date.now();
    const ageMs = now - this.lastLiveTs;
    const ageSec = Math.max(1, Math.floor(ageMs / 1000));
    const inGrace = now < this.graceUntilTs;
    const weekendClosed = isForexWeekendUTC();

    let nextState = 'LIVE';
    if (!navigator.onLine || !this.serverReachable) {
      nextState = 'OFFLINE';
    } else if (!inGrace && !weekendClosed && ageMs >= STALE_TICK_MS) {
      nextState = 'STALE';
    } else if (
      !inGrace &&
      ((!weekendClosed && ageMs >= SLOW_TICK_MS) ||
        this.pingFailCount === 1 ||
        (this.lastPingMs !== null && this.lastPingMs >= HIGH_PING_MS))
    ) {
      nextState = 'SLOW';
    }

    const prevState = this.state;
    this.state = nextState;
    if (prevState !== nextState) {
      bus.emit('network:state', { state: nextState, pingMs: this.lastPingMs, ageSec });
    }
    this._renderUI(nextState, ageSec);
  }

  _restorePillTimerIfOffline(pillTimer) {
    if (pillTimer && pillTimer.textContent === 'OFFLINE') {
      pillTimer.textContent = '--:--';
    }
  }

  _renderUI(state, ageSec) {
    const bar = $('offlineBar');
    const wsStatus = $('wsStatus');
    const wsDot = $('wsDot');
    const livePrice = $('livePrice');
    const pillTimer = $('pillTimer');

    if (state === 'LIVE') {
      bar?.classList.add('hidden');
      this.shieldEl?.classList.add('hidden');
      if (wsStatus) {
        wsStatus.textContent = this.lastPingMs !== null ? `TURBO ${this.lastPingMs}ms` : 'TURBO';
        wsStatus.className = 'hidden sm:inline-block text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-bold border border-emerald-500/20 shrink-0';
      }
      if (wsDot) wsDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-emerald-500';
      if (livePrice) livePrice.style.opacity = '1';
      this._restorePillTimerIfOffline(pillTimer);
      return;
    }

    if (state === 'SLOW') {
      this.shieldEl?.classList.add('hidden');
      if (bar) {
        bar.className = 'bg-amber-500 text-[#070b13] text-center text-[11px] font-black py-1.5 px-3';
        const pingTxt = this.lastPingMs !== null ? `Ping ${this.lastPingMs}ms` : `យឺត ${ageSec}s`;
        bar.textContent = `⚠️ អ៊ីនធឺណិតយឺត (${pingTxt}) — តម្លៃអាចយឺតជាងទីផ្សារពិត សូមប្រុងប្រយ័ត្ន`;
      }
      if (wsStatus) {
        wsStatus.textContent = this.lastPingMs !== null ? `SLOW ${this.lastPingMs}ms` : 'SLOW';
        wsStatus.className = 'hidden sm:inline-block text-[9px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold border border-amber-400/35 shrink-0';
      }
      if (wsDot) wsDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-amber-400';
      if (livePrice) livePrice.style.opacity = '0.85';
      this._restorePillTimerIfOffline(pillTimer);
      return;
    }

    // OFFLINE ឬ STALE (តម្លៃគាំង)
    const isNoInternet = !navigator.onLine;
    const isServerDown = navigator.onLine && !this.serverReachable;

    if (!this.alertedDrop) {
      this.alertedDrop = true;
      playAlertChime('warn');
    }

    if (bar) {
      bar.className = 'bg-rose-600 text-white text-center text-[11px] font-black py-1.5 px-3 animate-pulse';
      bar.textContent = isNoInternet
        ? `🚫 គ្មានសេវាអ៊ីនធឺណិត! តម្លៃគាំងអស់ ${ageSec} វិនាទី — ហាមចូល Order`
        : isServerDown
          ? `🚫 ទាក់ទង Server មិនបាន! តម្លៃគាំងអស់ ${ageSec} វិនាទី — ហាមចូល Order`
          : `🚫 តម្លៃឈប់អាប់ដេតអស់ ${ageSec} វិនាទី (សេវាស្លូខ្លាំង) — ហាមចូល Order`;
    }

    if (wsStatus) {
      wsStatus.textContent = isNoInternet ? 'NO NET' : isServerDown ? 'OFFLINE' : 'STALE';
      wsStatus.className = 'hidden sm:inline-block text-[9px] px-1.5 py-0.5 rounded bg-rose-500/25 text-rose-300 font-black border border-rose-500/40 shrink-0';
    }
    if (wsDot) wsDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-rose-500';
    if (livePrice) livePrice.style.opacity = '0.45';
    if (pillTimer) pillTimer.textContent = 'OFFLINE';

    if (this.shieldEl) {
      this.shieldEl.classList.remove('hidden');
      if (this.shieldTitleEl) {
        this.shieldTitleEl.textContent = isNoInternet
          ? 'ដាច់សេវាអ៊ីនធឺណិត!'
          : isServerDown
            ? 'ដាច់ការភ្ជាប់ Server!'
            : 'សេវាស្លូខ្លាំង • តម្លៃគាំង!';
      }
      if (this.shieldMsgEl) {
        this.shieldMsgEl.textContent = isNoInternet
          ? 'ទូរស័ព្ទ/កុំព្យូទ័រគ្មានអ៊ីនធឺណិត — តម្លៃលើអេក្រង់ជាតម្លៃចាស់ ហាមចូល Order!'
          : isServerDown
            ? 'ទាក់ទង Server មិនបាន — ប្រព័ន្ធកំពុងព្យាយាមភ្ជាប់ឡើងវិញដោយស្វ័យប្រវត្តិ។'
            : 'អ៊ីនធឺណិតយឺតខ្លាំង តម្លៃមិនបានអាប់ដេតទេ — ហាមចូល Order លើតម្លៃគាំងនេះ!';
      }
      if (this.shieldAgeEl) {
        this.shieldAgeEl.textContent = `តម្លៃគាំងអស់ ${ageSec} វិនាទី • កំពុងភ្ជាប់ស្វ័យប្រវត្តិ...`;
      }
    }
  }
}

export const networkGuard = new NetworkGuard();