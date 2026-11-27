import { bus } from '../core/eventBus.js';
import { Store } from '../core/storage.js';
import { $, toNum } from '../core/utils.js';
import { chartEngine } from '../chart/chartEngine.js';
import { makeDraggable } from './draggable.js';

class HudWidget {
  constructor() {
    this.el = null;
    this.dragCtrl = null;
    this.visible = true;
    this.collapsed = true;
    this.activePlan = null;
    this.previewPlan = null;
    this.activeSide = 'WAIT';
  }

  init(prefs = {}) {
    this.el = $('chartHud');
    this.visible = prefs.showSlTp !== false;
    this.collapsed = Boolean(prefs.hudCollapsed);

    if (this.el && $('chartStage')) {
      this.dragCtrl = makeDraggable(this.el, $('chartStage'), {
        ignoreSelector: '#hudToggleBtn, #hudReasonBox',
        rightMargin: 68,
        bottomMargin: 24,
      });
    }

    this._applyVisible();
    this._applyCollapse();

    bus.on('market:full', ({ data }) => this.update(data));
    bus.on('price:updated', ({ price }) => this.updateProgress(price));
    bus.on('chart:resized', () => this.clamp());

    // ការពារកុំឱ្យ HUD និង AI Radar ជាន់បាំងគ្នាលើទូរស័ព្ទ (< 640px)
    bus.on('radar:toggled', ({ visible }) => {
      if (visible && window.innerWidth < 640 && !this.collapsed) {
        this.setCollapsed(true, false);
      }
    });
  }

  clamp() {
    this.dragCtrl?.clamp();
  }

  setVisible(flag) {
    this.visible = Boolean(flag);
    const prefs = Store.getPrefs();
    prefs.showSlTp = this.visible;
    Store.savePrefs(prefs);
    this._applyVisible();
    chartEngine.setSlTpPlan(this.activePlan, this.activeSide, this.visible);
  }

  _applyVisible() {
    this.el?.classList.toggle('hidden', !this.visible);
    if (this.visible) this.clamp();
  }

  setCollapsed(flag, persist = true) {
    this.collapsed = Boolean(flag);
    if (persist) {
      const prefs = Store.getPrefs();
      prefs.hudCollapsed = this.collapsed;
      Store.savePrefs(prefs);
    }
    this._applyCollapse();
  }

  toggleCollapse(e) {
    if (e) e.stopPropagation();
    this.setCollapsed(!this.collapsed, true);
  }

  _applyCollapse() {
    $('hudBody')?.classList.toggle('hidden', this.collapsed);
    $('hudMiniRow')?.classList.toggle('hidden', !this.collapsed);
    const chev = $('hudChevron');
    if (chev) chev.style.transform = this.collapsed ? 'rotate(0deg)' : 'rotate(180deg)';
    this.clamp();
  }

  update(data) {
    const brain = data.brain || null;
    const canTrade = Boolean(brain && brain.can_trade && (brain.side === 'BUY' || brain.side === 'SELL'));
    const isDanger = Boolean(brain && (brain.status === 'BLOCKED' || brain.is_danger));
    const side = canTrade ? brain.side : (isDanger ? 'STOP' : 'WATCH');
    const plan = (canTrade && brain?.plan) ? brain.plan : (brain?.preview_plan || data.setup || null);

    this.activePlan = canTrade ? plan : null;
    this.previewPlan = plan;
    this.activeSide = canTrade ? side : 'WAIT';

    chartEngine.setSlTpPlan(this.activePlan, this.activeSide, this.visible);

    if ($('hudSide')) $('hudSide').textContent = canTrade ? side : (isDanger ? 'ហាមចូល' : 'ត្រៀម');
    if ($('hudGrade')) {
      $('hudGrade').textContent = canTrade
        ? `${brain.grade || 'V10'} ${brain.confidence_score || ''}%`
        : (isDanger ? 'RISK' : `${brain?.confidence_score || 35}%`);
    }

    if (this.el && $('hudDot') && $('hudSide')) {
      if (side === 'BUY') {
        this.el.style.borderLeftColor = '#10b981';
        $('hudDot').className = 'w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0';
        $('hudSide').className = 'text-[11px] font-black tracking-wide text-emerald-400';
      } else if (side === 'SELL') {
        this.el.style.borderLeftColor = '#f43f5e';
        $('hudDot').className = 'w-1.5 h-1.5 rounded-full bg-rose-400 shrink-0';
        $('hudSide').className = 'text-[11px] font-black tracking-wide text-rose-400';
      } else if (isDanger) {
        this.el.style.borderLeftColor = '#ef4444';
        $('hudDot').className = 'w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0';
        $('hudSide').className = 'text-[11px] font-black tracking-wide text-rose-400';
      } else {
        this.el.style.borderLeftColor = '#facc15';
        $('hudDot').className = 'w-1.5 h-1.5 rounded-full bg-yellow-400 shrink-0';
        $('hudSide').className = 'text-[11px] font-black tracking-wide text-yellow-400';
      }
    }

    const ev = toNum(plan?.entry ?? data.price);
    const sv = toNum(plan?.sl);
    const t1 = toNum(plan?.tp ?? plan?.tp1);
    const t2 = toNum(plan?.tp2);

    if (plan && ev !== null && sv !== null && t1 !== null) {
      const sp = plan.sl_pips ?? Math.round(Math.abs(ev - sv) * 10);
      const p1 = plan.tp_pips ?? Math.round(Math.abs(t1 - ev) * 10);
      const p2 = plan.tp2_pips ?? (t2 !== null ? Math.round(Math.abs(t2 - ev) * 10) : null);

      if ($('hudMiniSl')) $('hudMiniSl').textContent = sv.toFixed(2);
      if ($('hudMiniTp')) $('hudMiniTp').textContent = t1.toFixed(2);
      if ($('hudEntry')) $('hudEntry').textContent = `$${ev.toFixed(2)}`;
      if ($('hudSl')) $('hudSl').textContent = `$${sv.toFixed(2)}`;
      if ($('hudTp')) $('hudTp').textContent = `$${t1.toFixed(2)}`;
      if ($('hudTp2')) $('hudTp2').textContent = t2 !== null ? `$${t2.toFixed(2)}` : '--';
      if ($('hudSlPips')) $('hudSlPips').textContent = sp ? `(-${sp})` : '';
      if ($('hudTpPips')) $('hudTpPips').textContent = p1 ? `(+${p1})` : '';
      if ($('hudTp2Pips')) $('hudTp2Pips').textContent = p2 ? `(+${p2})` : '';
      if ($('hudRr')) $('hudRr').textContent = plan.rr || '2 : 1';

      const sb = $('hudEntryState');
      if (sb) {
        if (isDanger) {
          sb.textContent = 'ហាមចូល';
          sb.className = 'text-[8.5px] px-1.5 py-0.5 rounded bg-rose-500/25 text-rose-300 font-black';
        } else if (!canTrade) {
          sb.textContent = 'ត្រៀមចូល';
          sb.className = 'text-[8.5px] px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 font-bold';
        } else {
          sb.textContent = plan.entry_state === 'PULLBACK' ? 'ដាក់ Limit' : `ចូល ${side}`;
          sb.className = side === 'BUY'
            ? 'text-[8.5px] px-1.5 py-0.5 rounded bg-emerald-500 text-[#070b13] font-black'
            : 'text-[8.5px] px-1.5 py-0.5 rounded bg-rose-500 text-white font-black';
        }
      }
    } else {
      ['hudMiniSl', 'hudMiniTp', 'hudEntry', 'hudSl', 'hudTp', 'hudTp2', 'hudRr'].forEach((id) => {
        if ($(id)) $(id).textContent = '--';
      });
      ['hudSlPips', 'hudTpPips', 'hudTp2Pips'].forEach((id) => {
        if ($(id)) $(id).textContent = '';
      });
    }

    const rBox = $('hudReasonBox');
    if (rBox) {
      rBox.textContent = brain?.hud_hint_km || brain?.action_km || 'កំពុងតាមដាន...';
      rBox.className = canTrade
        ? (side === 'BUY'
          ? 'w-full mt-1 px-2 py-1 rounded-lg bg-emerald-500/20 border border-emerald-400/35 text-[9.5px] font-black text-emerald-300 text-center truncate'
          : 'w-full mt-1 px-2 py-1 rounded-lg bg-rose-500/20 border border-rose-400/35 text-[9.5px] font-black text-rose-300 text-center truncate')
        : isDanger
          ? 'w-full mt-1 px-2 py-1 rounded-lg bg-rose-500/20 border border-rose-500/40 text-[9px] font-bold text-rose-300 text-center truncate'
          : 'w-full mt-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10 text-[9px] font-bold text-sky-300 text-center tabular-nums truncate';
    }

    const sh = brain?.exit_shield;
    const shEl = $('hudExitShield');
    if (sh && shEl) {
      shEl.textContent = sh.title_km;
      shEl.className = sh.level === 'DANGER'
        ? 'mt-1 px-2 py-1 rounded-lg bg-rose-500/25 border border-rose-500/50 text-[8.5px] leading-tight text-rose-200 font-black text-center animate-pulse'
        : sh.level === 'WARN'
          ? 'mt-1 px-2 py-1 rounded-lg bg-amber-500/20 border border-amber-400/45 text-[8.5px] leading-tight text-amber-200 font-black text-center'
          : 'mt-1 px-2 py-1 rounded-lg bg-emerald-500/10 border border-emerald-400/25 text-[8.5px] leading-tight text-emerald-300 font-bold text-center';
    }

    this.clamp();
    this.updateProgress(chartEngine.getLastPrice());
  }

  updateProgress(price) {
    const bar = $('hudProgBar');
    const txt = $('hudProgTxt');
    if (!bar || !txt) return;

    if (!this.activePlan || !Number.isFinite(price) || this.activeSide === 'WAIT') {
      bar.style.width = '50%';
      bar.className = 'h-full bg-gray-600 transition-all duration-200';
      txt.textContent = 'WAIT';
      txt.className = 'text-gray-400 tabular-nums';
      return;
    }

    const sl = toNum(this.activePlan.sl);
    const tp = toNum(this.activePlan.tp ?? this.activePlan.tp1);
    const ent = toNum(this.activePlan.entry);
    if (sl === null || tp === null || ent === null) return;

    const tot = Math.abs(tp - sl);
    if (!tot) return;
    const pct = Math.max(4, Math.min(100, this.activeSide === 'BUY' ? ((price - sl) / tot) * 100 : ((sl - price) / tot) * 100));
    bar.style.width = `${pct}%`;
    bar.className = pct >= 50 ? 'h-full bg-emerald-400 transition-all duration-200' : 'h-full bg-rose-400 transition-all duration-200';

    const diff = this.activeSide === 'BUY' ? (price - ent) : (ent - price);
    txt.textContent = `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}`;
    txt.className = diff >= 0 ? 'text-emerald-400 tabular-nums' : 'text-rose-400 tabular-nums';
  }

  getActivePlan() { return this.activePlan; }
  getPreviewPlan() { return this.previewPlan; }
  getActiveSide() { return this.activeSide; }
}

export const hudWidget = new HudWidget();