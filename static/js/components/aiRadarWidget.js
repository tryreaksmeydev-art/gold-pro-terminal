import { COMM_PIPS } from '../core/config.js';
import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, escHtml, toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';
import { makeResizable } from './draggable.js';

class AiRadarWidget {
  constructor() {
    this.el = null;
    this.scrollBody = null;
    this.visible = false;
    this.auditLot = 0.01;
    this.lastBrain = null;
    this.lastSetup = null;
    this.lastPrice = null;
    this.lastInterval = '15m';

    this.tickHistory = [];
    this.depth = { bidPct: 50, askPct: 50, bidVol: 0, askVol: 0, source: 'WAIT' };
    this.nextScanAt = Date.now() + 5000;
    this.scanTimer = null;
  }

  init(prefs = {}) {
    this.el = $('neuralScannerBox');
    this.visible = Boolean(prefs.showScanner);
    this.auditLot = Number(prefs.auditLot) || 0.01;

    this._injectProTelemetryUI();
    this._setupScrollableAndReorderLayout();

    const stage = $('chartStage');
    if (this.el && stage) {
      this._initSmartDrag(this.el, stage);
      makeResizable(this.el, $('nsResizeHandle'), stage, { minWidth: 250, maxWidth: 430 });
    }

    this.setVisible(this.visible, false);

    bus.on('interval:changed', ({ interval }) => {
      if (interval) this.lastInterval = interval;
    });

    bus.on('market:full', ({ data, fromCache }) => {
      if (data?.interval) this.lastInterval = data.interval;
      if (!fromCache) this.nextScanAt = Date.now() + 5000;
      this.update(data);
    });

    bus.on('price:updated', ({ price }) => this._onLiveTick(price));
    bus.on('market:depth', (d) => this._onOrderBookDepth(d));
    bus.on('candle:countdown', ({ text, remSec }) => this._updateCandleTimer(text, remSec));
    bus.on('chart:resized', () => {
      this._fitMaxHeight();
      this._clampPosition();
    });
    bus.on('audit:lotChanged', ({ lot }) => {
      this.auditLot = Number(lot) || 0.01;
      this._updateProjectedPnl();
      if (this.lastPrice) this._updateLiveExitTiming(this.lastPrice);
    });

    window.addEventListener('resize', () => {
      this._fitMaxHeight();
      this._clampPosition();
    }, { passive: true });

    window.addEventListener('orientationchange', () => {
      setTimeout(() => {
        this._fitMaxHeight();
        this._clampPosition();
      }, 220);
    }, { passive: true });

    this.scanTimer = setInterval(() => this._updateScanCountdown(), 500);
  }

  /**
   * Smart Drag:
   * - Header: អូសបានគ្រប់ទិស (ឆ្វេង-ស្តាំ-លើ-ក្រោម) ទាំង Mouse និង Touch
   * - Body (Mouse): ចុចអូសបានគ្រប់កន្លែងក្រៅពីប៊ូតុង/Scrollbar ហើយ Scroll ដោយកង់ Mouse
   * - Body (Touch): អូសបញ្ឈរ = Scroll មើលលើ-ក្រោម | អូសផ្តេក = រំកិលប្រអប់ឆ្វេង-ស្តាំ
   */
  _initSmartDrag(el, stage) {
    let tracking = false;
    let dragging = false;
    let pointerId = null;
    let fromBody = false;
    let isTouch = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    const beginDrag = (e) => {
      const stageRect = stage.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      startLeft = elRect.left - stageRect.left;
      startTop = elRect.top - stageRect.top;
      el.style.right = 'auto';
      el.style.bottom = 'auto';
      el.style.left = `${Math.round(startLeft)}px`;
      el.style.top = `${Math.round(startTop)}px`;
      dragging = true;
      try { el.setPointerCapture(e.pointerId); } catch {}
    };

    el.addEventListener('pointerdown', (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      if (e.target.closest('button, input, a, #nsResizeHandle')) return;

      const inBody = Boolean(e.target.closest('#nsScrollBody'));
      if (inBody && e.pointerType === 'mouse' && this.scrollBody) {
        const sbRect = this.scrollBody.getBoundingClientRect();
        if (e.clientX >= sbRect.right - 12) return; // ទុកចន្លោះអូស Scrollbar
      }

      tracking = true;
      dragging = false;
      pointerId = e.pointerId;
      fromBody = inBody;
      isTouch = e.pointerType === 'touch' || e.pointerType === 'pen';
      startX = e.clientX;
      startY = e.clientY;

      if (!fromBody) {
        beginDrag(e);
      }
    });

    el.addEventListener('pointermove', (e) => {
      if (!tracking || e.pointerId !== pointerId) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      if (!dragging) {
        if (Math.hypot(dx, dy) < 6) return;
        if (fromBody && isTouch) {
          // លើទូរស័ព្ទ៖ បើអូសបញ្ឈរ -> ទុកឱ្យ ScrollBody រំកិលតាមធម្មជាតិ
          if (Math.abs(dy) >= Math.abs(dx) * 1.1) {
            tracking = false;
            return;
          }
        }
        beginDrag(e);
      }

      if (dragging) {
        const maxL = Math.max(4, stage.clientWidth - el.offsetWidth - 4);
        const maxT = Math.max(4, stage.clientHeight - el.offsetHeight - 4);
        const nextL = Math.max(4, Math.min(maxL, startLeft + dx));
        const nextT = Math.max(4, Math.min(maxT, startTop + dy));
        el.style.left = `${Math.round(nextL)}px`;
        el.style.top = `${Math.round(nextT)}px`;
      }
    });

    const endDrag = (e) => {
      if (e.pointerId !== pointerId) return;
      if (dragging) {
        try { el.releasePointerCapture(e.pointerId); } catch {}
      }
      tracking = false;
      dragging = false;
      pointerId = null;
    };

    el.addEventListener('pointerup', endDrag);
    el.addEventListener('pointercancel', endDrag);
  }

  _clampPosition() {
    const stage = $('chartStage');
    if (!this.el || !stage || !this.visible) return;
    const stageRect = stage.getBoundingClientRect();
    const elRect = this.el.getBoundingClientRect();
    if (!elRect.width || !stageRect.width) return;

    const curL = elRect.left - stageRect.left;
    const curT = elRect.top - stageRect.top;
    const maxL = Math.max(4, stage.clientWidth - this.el.offsetWidth - 4);
    const maxT = Math.max(4, stage.clientHeight - this.el.offsetHeight - 4);

    if (curL < 4 || curL > maxL || curT < 4 || curT > maxT) {
      this.el.style.right = 'auto';
      this.el.style.bottom = 'auto';
      this.el.style.left = `${Math.round(Math.max(4, Math.min(maxL, curL)))}px`;
      this.el.style.top = `${Math.round(Math.max(4, Math.min(maxT, curT)))}px`;
    }
  }

  _setupScrollableAndReorderLayout() {
    if (!this.el || $('nsScrollBody')) return;

    const children = Array.from(this.el.children);
    if (children.length < 2) return;

    const closeBtn = $('btnCloseRadar');
    const headerEl = children.find((c) => closeBtn && c.contains(closeBtn)) || children[0];
    const resizeEl = $('nsResizeHandle');

    this.el.classList.add('ns-radar-shell');
    headerEl.classList.add('ns-radar-header');

    const body = document.createElement('div');
    body.id = 'nsScrollBody';
    body.className = 'ns-scroll-body space-y-2';

    const rest = children.filter((c) => c !== headerEl && c !== resizeEl);
    // បើ HTML មាន Wrapper តែ ១ គ្របផ្នែកខាងក្នុង ត្រូវដកកូនខាងក្នុងមករៀបលំដាប់ផ្ទាល់ក្នុង #nsScrollBody
    const itemsToMove = (rest.length === 1 && rest[0].querySelector('#nsVerdictCard'))
      ? Array.from(rest[0].children)
      : rest;

    for (const item of itemsToMove) {
      body.appendChild(item);
    }
    if (rest.length === 1 && rest[0] !== body && rest[0].parentElement === this.el && rest[0].children.length === 0) {
      rest[0].remove();
    }

    // រៀបលំដាប់ថ្មី៖ ✅ (#nsLiveProBox) នៅលើគេបង្អស់ — ❌ (Flow/V8/OB-SMC/Macro/DXY) នៅក្រោមគេបង្អស់
    const proBox = $('nsLiveProBox');
    const bottomIds = ['nsFlow', 'nsBrain', 'nsSmc', 'nsNews', 'nsDxy', 'nsUs10y', 'nsWarMini'];
    const moveToBottom = [];

    for (const ch of Array.from(body.children)) {
      if (ch === proBox) continue;
      if (ch.id === 'nsVerdictCard' || ch.querySelector('#nsVerdictCard') || ch.querySelector('#nsGuideList')) {
        continue;
      }
      const isCrossedSection = bottomIds.some((id) => ch.id === id || ch.querySelector(`#${id}`));
      if (isCrossedSection) {
        moveToBottom.push(ch);
      }
    }

    if (proBox) {
      body.prepend(proBox);
    }
    for (const el of moveToBottom) {
      body.appendChild(el);
    }

    if (resizeEl) {
      this.el.insertBefore(body, resizeEl);
    } else {
      this.el.appendChild(body);
    }
    this.scrollBody = body;
    this._fitMaxHeight();
  }

  _fitMaxHeight() {
    if (!this.el) return;
    const stage = $('chartStage');
    const stageH = stage ? stage.clientHeight : window.innerHeight - 80;
    const maxH = Math.max(210, stageH - 16);
    this.el.style.maxHeight = `${maxH}px`;
  }

  _injectProTelemetryUI() {
    if (!this.el || $('nsLiveProBox')) return;

    // 1) Header: នាឡិការាប់ថយក្រោយទៀន + AI 5s (គ្មានប៊ូតុងសំឡេង)
    const closeBtn = $('btnCloseRadar');
    if (closeBtn && closeBtn.parentElement && !$('nsTimerStrip')) {
      const strip = document.createElement('div');
      strip.id = 'nsTimerStrip';
      strip.className = 'flex items-center gap-1 ml-auto mr-1 tabular-nums shrink-0';
      strip.innerHTML = `
        <span id="nsCandleCountdown" title="ពេលនៅសល់មុនទៀនបិទ" class="px-1.5 py-0.5 rounded bg-sky-500/20 border border-sky-400/35 text-[9px] font-black text-sky-300">ទៀន --:--</span>
        <span id="nsScanCountdown" title="ពេល AI ស្កេនម្តងទៀត" class="px-1.5 py-0.5 rounded bg-yellow-400/15 border border-yellow-400/35 text-[9px] font-black text-yellow-300">AI 5s</span>
      `;
      closeBtn.parentElement.insertBefore(strip, closeBtn);
    }

    // 2) ✅ Pro Telemetry Box (នឹងត្រូវរៀបទៅនៅលើគេបង្អស់)
    const vCard = $('nsVerdictCard');
    if (vCard && vCard.parentElement) {
      const box = document.createElement('div');
      box.id = 'nsLiveProBox';
      box.className = 'rounded-xl bg-[#071020]/90 border border-cyan-400/25 p-2 space-y-1.5';
      box.innerHTML = `
        <div id="nsCircuitBanner" class="hidden rounded-lg bg-amber-500/20 border border-amber-400/50 px-2 py-1 text-[9px] font-black text-amber-200 text-center leading-snug"></div>

        <div class="space-y-1">
          <div class="flex items-center justify-between text-[8px] text-gray-400">
            <span class="font-bold text-yellow-300">ស្កេន 5 TF (ចុចប្តូរភ្លាម)</span>
            <span id="nsScannerBest" class="font-black text-gray-300">--</span>
          </div>
          <div id="nsTfScannerBar" class="grid grid-cols-5 gap-1"></div>
        </div>

        <div id="nsCopyBar" class="grid grid-cols-3 gap-1 pt-1 border-t border-white/10">
          <button type="button" data-copy-field="entry" class="ns-copy-btn rounded-lg bg-yellow-400/10 hover:bg-yellow-400/20 border border-yellow-400/30 px-1.5 py-1 text-[8.5px] font-black text-yellow-200 tabular-nums active:scale-95 transition">Copy Entry</button>
          <button type="button" data-copy-field="sl" class="ns-copy-btn rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-400/35 px-1.5 py-1 text-[8.5px] font-black text-rose-200 tabular-nums active:scale-95 transition">Copy SL</button>
          <button type="button" data-copy-field="tp" class="ns-copy-btn rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-400/35 px-1.5 py-1 text-[8.5px] font-black text-emerald-200 tabular-nums active:scale-95 transition">Copy TP1</button>
        </div>

        <div class="flex items-center justify-between gap-1 text-[8.5px] pt-1 border-t border-white/10">
          <span class="text-cyan-300 font-bold">Order Book 100ms</span>
          <span id="nsDepthTxt" class="font-black text-gray-200 tabular-nums">Bid 50% • Ask 50%</span>
        </div>
        <div class="w-full h-1.5 rounded-full bg-rose-500/45 overflow-hidden flex">
          <div id="nsDepthBar" class="h-full bg-emerald-400 transition-all duration-100" style="width:50%"></div>
        </div>
        <div class="flex items-center justify-between gap-1 text-[8.5px] pt-0.5 border-t border-white/10 tabular-nums">
          <span class="text-gray-400">ល្បឿនតម្លៃ (10s): <b id="nsVelocity" class="text-white">$0.00</b></span>
          <span id="nsLivePnlBadge" class="font-black text-sky-300">LIVE --</span>
        </div>
        <div id="nsTimingAdvisor" class="rounded-lg bg-white/[.04] border border-white/10 px-2 py-1 text-[9.5px] font-bold text-cyan-200 text-center leading-snug">
          ⚡ កំពុងវាស់ល្បឿន និងចំណុចកាត់ចំណេញផ្ទាល់...
        </div>
        <div class="grid grid-cols-5 gap-1 pt-1 border-t border-white/10 text-center text-[7.5px] tabular-nums">
          <div class="rounded bg-black/40 py-0.5 px-0.5"><div class="text-gray-400">VWAP</div><div id="nsVwap" class="font-black text-purple-300">--</div></div>
          <div class="rounded bg-black/40 py-0.5 px-0.5"><div class="text-gray-400">POC</div><div id="nsPoc" class="font-black text-yellow-300">--</div></div>
          <div class="rounded bg-black/40 py-0.5 px-0.5"><div class="text-gray-400">CVD</div><div id="nsCvd" class="font-black text-sky-300">--</div></div>
          <div class="rounded bg-black/40 py-0.5 px-0.5"><div class="text-gray-400">Squeeze</div><div id="nsSqueeze" class="font-black text-gray-200">--</div></div>
          <div class="rounded bg-black/40 py-0.5 px-0.5"><div class="text-gray-400">SMT</div><div id="nsSmt" class="font-black text-gray-300">--</div></div>
        </div>
      `;
      vCard.insertAdjacentElement('beforebegin', box);

      box.querySelectorAll('.ns-copy-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          this._handleCopyField(btn.dataset.copyField, btn);
        });
      });

      $('nsTfScannerBar')?.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-scan-tf]');
        if (!btn) return;
        e.stopPropagation();
        this._switchTimeframe(btn.dataset.scanTf);
      });
    }
  }

  _getActivePlan() {
    const b = this.lastBrain;
    return (b?.can_trade && b?.plan) ? b.plan : (b?.preview_plan || this.lastSetup);
  }

  async _handleCopyField(field, btnEl) {
    const plan = this._getActivePlan();
    if (!plan) return;
    const raw = field === 'entry' ? plan.entry : field === 'sl' ? plan.sl : (plan.tp ?? plan.tp1);
    const val = toNum(raw);
    if (val === null) return;
    const text = val.toFixed(2);

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      const oldTxt = btnEl.textContent;
      btnEl.textContent = `✓ ${text}`;
      setTimeout(() => { btnEl.textContent = oldTxt; }, 1200);
    } catch {}
  }

  _updateCopyButtons() {
    const plan = this._getActivePlan();
    const bar = $('nsCopyBar');
    if (!bar) return;
    const eBtn = bar.querySelector('[data-copy-field="entry"]');
    const sBtn = bar.querySelector('[data-copy-field="sl"]');
    const tBtn = bar.querySelector('[data-copy-field="tp"]');
    const ent = toNum(plan?.entry);
    const sl = toNum(plan?.sl);
    const tp = toNum(plan?.tp ?? plan?.tp1);

    if (eBtn) eBtn.textContent = ent !== null ? `📋 E ${ent.toFixed(1)}` : 'Copy Entry';
    if (sBtn) sBtn.textContent = sl !== null ? `📋 SL ${sl.toFixed(1)}` : 'Copy SL';
    if (tBtn) tBtn.textContent = tp !== null ? `📋 TP ${tp.toFixed(1)}` : 'Copy TP1';
  }

  _switchTimeframe(tf) {
    if (!tf) return;
    const target =
      document.querySelector(`button[data-tf="${tf}"]`) ||
      document.querySelector(`button[data-interval="${tf}"]`) ||
      Array.from(document.querySelectorAll('button')).find(
        (b) => !b.closest('#nsTfScannerBar') && b.textContent.trim().toLowerCase() === tf.toLowerCase()
      );
    if (target) {
      target.click();
    } else {
      bus.emit('interval:changed', { interval: tf });
    }
  }

  _renderTfScanner(scanner) {
    const bar = $('nsTfScannerBar');
    const bestEl = $('nsScannerBest');
    if (!bar) return;

    const items = scanner?.items || [];
    if (!items.length) return;

    bar.innerHTML = items.map((it) => {
      const isActive = String(it.tf).toLowerCase() === String(this.lastInterval).toLowerCase();
      const ring = isActive ? 'ring-1 ring-yellow-300' : '';
      let cls = 'bg-white/[.05] border-white/10 text-gray-300';
      let tag = 'WAIT';

      if (it.side === 'BUY') {
        cls = it.is_sniper
          ? 'bg-emerald-500/25 border-emerald-400/60 text-emerald-200'
          : 'bg-emerald-500/15 border-emerald-400/35 text-emerald-300';
        tag = `B•${it.grade}`;
      } else if (it.side === 'SELL') {
        cls = it.is_sniper
          ? 'bg-rose-500/25 border-rose-400/60 text-rose-200'
          : 'bg-rose-500/15 border-rose-400/35 text-rose-300';
        tag = `S•${it.grade}`;
      }

      return `<button type="button" data-scan-tf="${escHtml(it.tf)}" class="rounded-lg border px-1 py-0.5 text-center leading-tight active:scale-95 transition ${cls} ${ring}">
        <div class="text-[8px] font-black">${escHtml(it.label)}</div>
        <div class="text-[7.5px] font-bold opacity-95">${escHtml(tag)}</div>
      </button>`;
    }).join('');

    if (bestEl) {
      const best = scanner.best;
      if (best) {
        bestEl.textContent = `ល្អបំផុត៖ ${best.label} ${best.side} (${best.grade})`;
        bestEl.className = `font-black ${best.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`;
      } else {
        bestEl.textContent = 'គ្មានសញ្ញាខ្លាំង';
        bestEl.className = 'font-bold text-gray-400';
      }
    }
  }

  _updateCandleTimer(text, remSec) {
    const el = $('nsCandleCountdown');
    if (!el) return;
    el.textContent = `ទៀន ${text}`;
    el.className = remSec <= 10
      ? 'px-1.5 py-0.5 rounded bg-rose-500/25 border border-rose-400/50 text-[9px] font-black text-rose-300 animate-pulse'
      : 'px-1.5 py-0.5 rounded bg-sky-500/20 border border-sky-400/35 text-[9px] font-black text-sky-300';
  }

  _updateScanCountdown() {
    const el = $('nsScanCountdown');
    if (!el) return;
    const rem = Math.max(0, Math.ceil((this.nextScanAt - Date.now()) / 1000));
    el.textContent = rem > 0 ? `AI ${rem}s` : 'AI ↻';
  }

  _onOrderBookDepth(d) {
    this.depth = d;
    if (!this.visible) return;
    const txt = $('nsDepthTxt');
    const bar = $('nsDepthBar');
    if (txt) {
      txt.textContent = `Bid ${d.bidPct}% (${d.bidVol}) • Ask ${d.askPct}%`;
      txt.className = `font-black tabular-nums ${d.bidPct >= 60 ? 'text-emerald-400' : d.askPct >= 60 ? 'text-rose-400' : 'text-gray-200'}`;
    }
    if (bar) bar.style.width = `${Math.max(5, Math.min(95, d.bidPct))}%`;
  }

  _onLiveTick(price) {
    if (!Number.isFinite(price) || price <= 0) return;
    this.lastPrice = price;

    const now = Date.now();
    this.tickHistory.push({ ts: now, price });
    while (this.tickHistory.length > 2 && now - this.tickHistory[0].ts > 10000) {
      this.tickHistory.shift();
    }

    if (!this.visible) return;
    this._updateLiveExitTiming(price);
  }

  _getVelocity10s(price) {
    if (this.tickHistory.length < 2) return 0;
    return price - this.tickHistory[0].price;
  }

  _updateLiveExitTiming(price) {
    const vel = this._getVelocity10s(price);
    const velEl = $('nsVelocity');
    if (velEl) {
      velEl.textContent = `${vel >= 0 ? '+' : ''}$${vel.toFixed(2)}`;
      velEl.className = vel >= 0.35 ? 'text-emerald-400 font-black' : vel <= -0.35 ? 'text-rose-400 font-black' : 'text-gray-200 font-bold';
    }

    const pnlEl = $('nsLivePnlBadge');
    const advEl = $('nsTimingAdvisor');
    if (!pnlEl || !advEl) return;

    const b = this.lastBrain;
    const canTrade = Boolean(b?.can_trade && (b.side === 'BUY' || b.side === 'SELL') && b.plan);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const lotMult = this.auditLot * 10.0;

    if (!canTrade) {
      const plan = b?.preview_plan || this.lastSetup;
      const ent = toNum(plan?.entry);
      pnlEl.textContent = `តម្លៃ $${price.toFixed(2)}`;
      pnlEl.className = 'font-black text-sky-300';

      if (ent !== null) {
        const dist = Math.abs(price - ent);
        const dir = price < ent ? 'ឡើងដល់' : 'ចុះដល់';
        advEl.textContent = `⏳ រង់ចាំតម្លៃ${dir} $${ent.toFixed(2)} (នៅសល់ $${dist.toFixed(2)}) • Order Book ${this.depth.bidPct >= 55 ? 'ទិញឈ្នះ' : this.depth.askPct >= 55 ? 'លក់ឈ្នះ' : 'ស្មើគ្នា'}`;
        advEl.className = 'rounded-lg bg-white/[.04] border border-white/10 px-2 py-1 text-[9.5px] font-bold text-gray-200 text-center leading-snug';
      }
      return;
    }

    const side = b.side;
    const entry = Number(b.plan.entry);
    const sl = Number(b.plan.sl);
    const tp1 = Number(b.plan.tp ?? b.plan.tp1);
    const tpDist = Math.max(0.5, Math.abs(tp1 - entry));
    const slDist = Math.max(0.5, Math.abs(entry - sl));

    const fav = side === 'BUY' ? (price - entry) : (entry - price);
    const netPips = (fav * 10.0) - costPips;
    const netUsd = netPips * lotMult;
    const progPct = (fav / tpDist) * 100;
    const velFav = side === 'BUY' ? vel : -vel;
    const bookAgainst = side === 'BUY' ? this.depth.askPct >= 62 : this.depth.bidPct >= 62;

    pnlEl.textContent = `Net ${netUsd >= 0 ? '+' : ''}$${netUsd.toFixed(2)} (${netPips >= 0 ? '+' : ''}${netPips.toFixed(0)}p)`;
    pnlEl.className = `font-black ${netUsd > 0 ? 'text-emerald-400' : netUsd < 0 ? 'text-rose-400' : 'text-gray-200'}`;

    if (fav >= tpDist) {
      advEl.textContent = `🎯 ដល់ TP1 (+$${netUsd.toFixed(2)}) — កាត់យកចំណេញ 60%–100% ឥឡូវ!`;
      advEl.className = 'rounded-lg bg-emerald-500/25 border border-emerald-400/60 px-2 py-1 text-[9.5px] font-black text-emerald-200 text-center leading-snug animate-pulse';
    } else if (progPct >= 50) {
      advEl.textContent = `🛡️ ចំណេញ +$${netUsd.toFixed(2)} (${Math.round(progPct)}% TP1) — រំកិល SL ស្មើដើម ($${entry.toFixed(2)}) ភ្លាម!`;
      advEl.className = 'rounded-lg bg-emerald-500/20 border border-emerald-400/45 px-2 py-1 text-[9.5px] font-black text-emerald-300 text-center leading-snug';
    } else if (netUsd > 0.35 && (velFav <= -0.35 || bookAgainst)) {
      advEl.textContent = `💰 ចំណេញ +$${netUsd.toFixed(2)} តែកម្លាំងចាប់ផ្តើមបត់ — កាត់យកចំណេញសិនបាន!`;
      advEl.className = 'rounded-lg bg-amber-500/20 border border-amber-400/50 px-2 py-1 text-[9.5px] font-black text-amber-200 text-center leading-snug';
    } else if (netUsd > 0 && velFav >= 0.30) {
      advEl.textContent = `🚀 កម្លាំងរុញខ្លាំង (+$${netUsd.toFixed(2)}) — កាន់បន្តទៅ TP1 $${tp1.toFixed(2)}`;
      advEl.className = 'rounded-lg bg-emerald-500/15 border border-emerald-400/35 px-2 py-1 text-[9.5px] font-bold text-emerald-300 text-center leading-snug';
    } else if ((-fav) >= slDist * 0.55) {
      advEl.textContent = `⚠️ ដើរបញ្ច្រាស (-$${Math.abs(netUsd).toFixed(2)}) កៀក SL — ត្រៀមកាត់ខាតលឿន!`;
      advEl.className = 'rounded-lg bg-rose-500/25 border border-rose-500/55 px-2 py-1 text-[9.5px] font-black text-rose-200 text-center leading-snug animate-pulse';
    } else {
      const toTp = Math.max(0, tpDist - fav);
      advEl.textContent = `⚡ កំពុងរត់ ${side} • នៅសល់ $${toTp.toFixed(2)} ដល់ TP1 (SL $${sl.toFixed(2)})`;
      advEl.className = 'rounded-lg bg-sky-500/15 border border-sky-400/30 px-2 py-1 text-[9.5px] font-bold text-sky-200 text-center leading-snug';
    }
  }

  setVisible(flag, persist = true) {
    this.visible = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.showScanner = this.visible;
      Store.savePrefs(prefs);
    }

    const chk = $('chkScanner');
    if (chk) chk.checked = this.visible;
    this.el?.classList.toggle('hidden', !this.visible);

    if (this.visible) {
      this._fitMaxHeight();
      this._onOrderBookDepth(directFeed.getDepth());
      if (this.lastPrice) this._updateLiveExitTiming(this.lastPrice);
      requestAnimationFrame(() => this._clampPosition());
    }

    bus.emit('radar:toggled', { visible: this.visible });
  }

  update(data) {
    const brain = data.brain;
    if (!brain) return;
    this.lastBrain = brain;
    this.lastSetup = data.setup || null;

    const sh = brain.exit_shield;
    const bp = brain.buy_pressure_pct ?? 50;

    if ($('nsFlow')) {
      $('nsFlow').textContent = bp >= 50 ? `Buy ${bp}%` : `Sell ${100 - bp}%`;
      $('nsFlow').className = `font-black tabular-nums ${bp >= 55 ? 'text-emerald-400' : bp <= 45 ? 'text-rose-400' : 'text-sky-300'}`;
    }
    if ($('nsBrain')) {
      $('nsBrain').textContent = brain.ai_rank_pct != null ? `Rank ${brain.ai_rank_pct}%` : `${brain.confidence_score || 0}%`;
    }
    if ($('nsSmc')) {
      $('nsSmc').textContent = `${data.smc?.bias || 'NEUTRAL'} • ${data.mtf?.aligned || 'MIX'}`;
    }
    if ($('nsNews')) {
      $('nsNews').textContent = `${brain.global_news?.bias || 'NEUT'} • War ${brain.war_news?.level || 'LOW'}`;
    }
    if ($('nsAction')) {
      $('nsAction').textContent = brain.sniper_text_km || brain.action_km || 'កំពុងវិភាគ...';
    }
    if ($('nsExit')) {
      $('nsExit').textContent = sh ? `${sh.title_km} — ${sh.action_km}` : 'ប្រព័ន្ធការពារសកម្ម';
    }

    const cbEl = $('nsCircuitBanner');
    const cb = brain.circuit_breaker;
    if (cbEl) {
      if (cb?.active && cb.reason_km) {
        cbEl.textContent = cb.reason_km;
        cbEl.classList.remove('hidden');
      } else {
        cbEl.classList.add('hidden');
      }
    }

    const vCard = $('nsVerdictCard');
    const actEl = $('nsAction');
    if (vCard && actEl) {
      if (brain.can_trade && brain.side === 'BUY') {
        vCard.className = 'rounded-xl bg-emerald-500/15 border border-emerald-400/50 p-2 text-center space-y-1';
        actEl.className = 'text-[11px] font-black text-emerald-300';
      } else if (brain.can_trade && brain.side === 'SELL') {
        vCard.className = 'rounded-xl bg-rose-500/15 border border-rose-500/50 p-2 text-center space-y-1';
        actEl.className = 'text-[11px] font-black text-rose-300';
      } else if (sh?.level === 'DANGER') {
        vCard.className = 'rounded-xl bg-rose-600/20 border border-rose-500/60 p-2 text-center space-y-1';
        actEl.className = 'text-[11px] font-black text-rose-300';
      } else {
        vCard.className = 'rounded-xl bg-black/55 border border-yellow-400/35 p-2 text-center space-y-1';
        actEl.className = 'text-[11px] font-black text-yellow-300';
      }
    }

    if (brain.beginner_guide) this._renderBeginnerGuide(brain.beginner_guide);
    if (data.tf_scanner) this._renderTfScanner(data.tf_scanner);

    const q = data.quant || brain.quant;
    if (q) {
      if ($('nsVwap')) {
        $('nsVwap').textContent = q.vwap ? `$${Number(q.vwap).toFixed(0)}` : '--';
        $('nsVwap').className = `font-black ${q.vwap_bias === 'BUY' ? 'text-emerald-400' : q.vwap_bias === 'SELL' ? 'text-rose-400' : 'text-purple-300'}`;
      }
      if ($('nsPoc')) $('nsPoc').textContent = q.poc ? `$${Number(q.poc).toFixed(0)}` : '--';
      if ($('nsCvd')) {
        const divTxt = q.cvd_divergence === 'BULL_DIV' ? 'DIV▲' : (q.cvd_divergence === 'BEAR_DIV' ? 'DIV▼' : `${q.cvd_pct ?? 50}%`);
        $('nsCvd').textContent = divTxt;
        $('nsCvd').className = `font-black ${q.cvd_divergence === 'BULL_DIV' || q.cvd_bias === 'BUY' ? 'text-emerald-400' : (q.cvd_divergence === 'BEAR_DIV' || q.cvd_bias === 'SELL' ? 'text-rose-400' : 'text-sky-300')}`;
      }
      if ($('nsSqueeze')) {
        const sq = q.squeeze_state;
        $('nsSqueeze').textContent = sq === 'FIRING' ? '🔥ផ្ទុះ' : (sq === 'COILING' ? '⚡សន្សំ' : 'ធម្មតា');
        $('nsSqueeze').className = `font-black ${sq === 'FIRING' ? 'text-rose-300 animate-pulse' : (sq === 'COILING' ? 'text-amber-300' : 'text-gray-300')}`;
      }
    }

    const smt = data.smt || brain.smt;
    if ($('nsSmt') && smt) {
      if (smt.signal === 'BULL_SMT') {
        $('nsSmt').textContent = 'SMT▲';
        $('nsSmt').className = 'font-black text-emerald-400 animate-pulse';
      } else if (smt.signal === 'BEAR_SMT') {
        $('nsSmt').textContent = 'SMT▼';
        $('nsSmt').className = 'font-black text-rose-400 animate-pulse';
      } else {
        $('nsSmt').textContent = smt.silver_px ? `Ag$${Number(smt.silver_px).toFixed(1)}` : 'ស្មើ';
        $('nsSmt').className = 'font-bold text-gray-300';
      }
    }

    this._updateCopyButtons();
    this._updateProjectedPnl();
    if (this.lastPrice) this._updateLiveExitTiming(this.lastPrice);
  }

  _renderBeginnerGuide(bg) {
    const listEl = $('nsGuideList');
    if (!listEl || !Array.isArray(bg.rows)) return;

    const cfg = {
      range: { sym: '↔', bg: 'bg-violet-500/20 text-violet-300 border-violet-400/35', hl: 'text-violet-300' },
      up: { sym: '↗', bg: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/35', hl: 'text-emerald-300' },
      down: { sym: '↘', bg: 'bg-rose-500/20 text-rose-300 border-rose-400/35', hl: 'text-rose-300' },
      target_buy: { sym: '◎', bg: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/35', hl: 'text-emerald-300' },
      target_sell: { sym: '◎', bg: 'bg-rose-500/20 text-rose-300 border-rose-400/35', hl: 'text-rose-300' },
      avoid: { sym: '⊘', bg: 'bg-rose-500/20 text-rose-300 border-rose-400/35', hl: 'text-rose-300' },
      stop: { sym: '!', bg: 'bg-amber-500/20 text-amber-300 border-amber-400/35', hl: 'text-amber-300' },
    };

    listEl.innerHTML = bg.rows.map((r) => {
      const c = cfg[r.icon] || cfg.range;
      return `<div class="flex items-center gap-2 rounded-xl bg-white/[.035] border border-white/10 px-2 py-1.5 text-[10px] leading-snug"><span class="w-5 h-5 rounded-lg border ${c.bg} flex items-center justify-center font-black text-[10px] shrink-0">${c.sym}</span><div class="text-gray-100 font-semibold min-w-0">${escHtml(r.prefix)} <b class="${c.hl} font-black tabular-nums">${escHtml(r.hl)}</b> ${escHtml(r.suffix)}</div></div>`;
    }).join('');

    if ($('nsGuideFooter')) $('nsGuideFooter').textContent = bg.footer_km || '';
    if ($('nsDxy')) $('nsDxy').textContent = bg.dxy || '--';
    if ($('nsUs10y')) $('nsUs10y').textContent = bg.us10y || '--';
    if ($('nsWarMini')) $('nsWarMini').textContent = `សង្គ្រាម៖ ${bg.war_km || 'ស្ងប់ស្ងាត់'}`;
  }

  _updateProjectedPnl() {
    const el = $('nsProjPnl');
    if (!el) return;
    const plan = this._getActivePlan();
    if (!plan || toNum(plan.entry) === null || toNum(plan.sl) === null) {
      el.textContent = '--';
      return;
    }

    const entry = Number(plan.entry);
    const sl = Number(plan.sl);
    const tp = Number(plan.tp ?? plan.tp1);
    const costPips = (Math.max(0, directFeed.getSpread()) * 10.0) + COMM_PIPS;
    const winPips = Math.max(0, Math.round((Math.abs(tp - entry) * 10) - costPips));
    const lossPips = Math.round((Math.abs(entry - sl) * 10) + costPips);
    const mult = this.auditLot * 10.0;

    el.textContent = `Lot ${this.auditLot.toFixed(2)} (Net) ៖ TP1 +$${(winPips * mult).toFixed(2)} | SL -$${(lossPips * mult).toFixed(2)}`;
  }
}

export const aiRadarWidget = new AiRadarWidget();