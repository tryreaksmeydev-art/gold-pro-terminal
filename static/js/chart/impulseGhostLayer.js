import { bus } from '../core/eventBus.js';
import { toNum } from '../core/utils.js';
import { directFeed } from '../services/directFeed.js';

function isForexWeekendUTC() {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun, 5=Fri, 6=Sat
  const hr = now.getUTCHours();
  return day === 6 || (day === 5 && hr >= 22) || (day === 0 && hr < 22);
}

/**
 * ImpulseGhostLayer — ប្រព័ន្ធព្យាករណ៍ទៀនវែងមុនទីផ្សារ (Pre-Candle Impulse Projection)
 * - ភ្ជាប់ផ្ទាល់ជាមួយ SmcOverlay.registerLayer() ដោយមិនប៉ះពាល់កូដក្រាហ្វិកដើម
 * - ចាប់តែពេលមានសញ្ញាទៀនវែងពិតប្រាកដ (Score >= 70 & Room >= 1.35x ATR)
 * - ប្រើពណ៌ Cyan (ឡើង) / Magenta (ចុះ) បែបថ្លា-ដាច់ៗ មិនច្រឡំទៀនទីផ្សារពិត
 */
class ImpulseGhostLayer {
  constructor() {
    this.overlay = null;
    this.unregister = null;
    this.enabled = true;

    this.lastData = null;
    this.lastPrice = 0;
    this.lastCandleTime = 0;
    this.prevCandleTime = 0;
    this.tickHistory = [];
    this.depth = { bidPct: 50, askPct: 50 };

    // Anti-Flicker & Smooth Interpolation State
    this.activeProj = null;
    this.smoothTarget = null;
    this.lastTriggeredAt = 0;
    this.holdMs = 4500;
  }

  init(smcOverlay) {
    if (!smcOverlay || typeof smcOverlay.registerLayer !== 'function') return;
    if (this.unregister) this.unregister();

    this.overlay = smcOverlay;
    this.unregister = smcOverlay.registerLayer((ctx, plotW, plotH, helpers) => {
      this._draw(ctx, plotW, plotH, helpers);
    });

    this._bindEvents();
  }

  setEnabled(flag) {
    this.enabled = Boolean(flag);
    this.overlay?.scheduleRender();
  }

  _bindEvents() {
    bus.on('market:full', ({ data }) => {
      if (!data) return;
      this.lastData = data;
      const candles = data.candles || [];
      if (candles.length >= 2) {
        this.lastCandleTime = Number(candles[candles.length - 1].time || 0);
        this.prevCandleTime = Number(candles[candles.length - 2].time || 0);
      }
      const px = toNum(data.price);
      if (px !== null && px > 1000) this.lastPrice = px;
      this._evaluateProjection();
      this.overlay?.scheduleRender();
    });

    bus.on('price:updated', ({ price }) => {
      if (!Number.isFinite(price) || price <= 1000) return;
      this.lastPrice = price;
      const now = Date.now();
      this.tickHistory.push({ ts: now, price });
      while (this.tickHistory.length > 2 && now - this.tickHistory[0].ts > 10000) {
        this.tickHistory.shift();
      }
      this._evaluateProjection();
      this.overlay?.scheduleRender();
    });

    bus.on('market:depth', (d) => {
      if (d) this.depth = d;
    });
  }

  _getVelocity10s() {
    if (this.tickHistory.length < 2) return 0;
    return this.tickHistory[this.tickHistory.length - 1].price - this.tickHistory[0].price;
  }

  _findLiquidityTarget(side, price, atr, data, brain) {
    const minReach = Math.max(2.4, atr * 1.35);
    const maxReach = Math.max(5.5, atr * 2.85);
    const candidates = [];

    const plan = (brain?.can_trade && brain?.plan) ? brain.plan : (brain?.preview_plan || data?.setup);
    const tp1 = toNum(plan?.tp ?? plan?.tp1);
    const tp2 = toNum(plan?.tp2);
    if (tp1 !== null) candidates.push({ p: tp1, tag: 'TP1' });
    if (tp2 !== null) candidates.push({ p: tp2, tag: 'TP2' });

    const smc = data?.smc || {};
    for (const lv of (smc.levels || [])) {
      const lp = toNum(lv.price);
      if (lp !== null) candidates.push({ p: lp, tag: lv.label || 'LIQ' });
    }

    if (side === 'BUY') {
      for (const b of (smc.supply_blocks || [])) {
        if (String(b.label || 'OB').toUpperCase() === 'OB') {
          const bot = toNum(b.bottom);
          if (bot !== null) candidates.push({ p: bot - 0.30, tag: 'OB' });
        }
      }
      const valid = candidates
        .filter((c) => (c.p - price) >= minReach && (c.p - price) <= maxReach)
        .sort((a, b) => a.p - b.p);
      if (valid.length) return valid[0];
      return { p: Number((price + Math.min(maxReach, Math.max(minReach, atr * 1.85))).toFixed(2)), tag: 'ATR' };
    }

    for (const b of (smc.demand_blocks || [])) {
      if (String(b.label || 'OB').toUpperCase() === 'OB') {
        const top = toNum(b.top);
        if (top !== null) candidates.push({ p: top + 0.30, tag: 'OB' });
      }
    }
    const valid = candidates
      .filter((c) => (price - c.p) >= minReach && (price - c.p) <= maxReach)
      .sort((a, b) => b.p - a.p);
    if (valid.length) return valid[0];
    return { p: Number((price - Math.min(maxReach, Math.max(minReach, atr * 1.85))).toFixed(2)), tag: 'ATR' };
  }

  _hasImmediateWall(side, price, atr, data) {
    const wallDist = Math.max(1.2, atr * 0.75);
    const smc = data?.smc || {};
    if (side === 'BUY') {
      return (smc.supply_blocks || []).some((b) => {
        if (String(b.label || 'OB').toUpperCase() !== 'OB') return false;
        const bot = toNum(b.bottom);
        return bot !== null && bot > price && (bot - price) < wallDist;
      });
    }
    return (smc.demand_blocks || []).some((b) => {
      if (String(b.label || 'OB').toUpperCase() !== 'OB') return false;
      const top = toNum(b.top);
      return top !== null && top < price && (price - top) < wallDist;
    });
  }

  _evaluateProjection() {
    // ការពារថ្ងៃបិទផ្សារ ឬគ្មានទិន្នន័យ
    if (!this.enabled || isForexWeekendUTC() || !this.lastData || this.lastPrice <= 1000) {
      this.activeProj = null;
      this.smoothTarget = null;
      return;
    }

    const data = this.lastData;
    const brain = data.brain || {};
    const news = data.news || {};
    const quant = data.quant || brain.quant || {};
    const smt = data.smt || brain.smt || {};
    const mtf = data.mtf || {};

    // Gate 6: ហាមគូសពេលមានព័ត៌មានក្រហម ឬខួរក្បាលប្លុកហានិភ័យ
    if (news.danger || brain.is_danger || brain.status === 'BLOCKED') {
      this.activeProj = null;
      this.smoothTarget = null;
      return;
    }

    const price = this.lastPrice;
    const atr = Math.max(0.8, toNum(data.atr) ?? 2.0);
    const ema20 = toNum(data.ema20) ?? price;
    const ema50 = toNum(data.ema50) ?? price;
    const vwap = toNum(quant.vwap) ?? price;
    const cvdPct = toNum(quant.cvd_pct) ?? 50;
    const cvdDiv = String(quant.cvd_divergence || 'NONE');
    const squeeze = String(quant.squeeze_state || 'NORMAL');
    const smtSig = String(smt.signal || 'NONE');
    const mtfAl = String(mtf.aligned || 'MIXED');
    const vel = this._getVelocity10s();
    const depth = this.depth || directFeed.getDepth();

    // កំណត់ទិសដៅបេក្ខជន (BUY / SELL)
    let side = null;
    if (brain.can_trade && (brain.side === 'BUY' || brain.side === 'SELL')) {
      side = brain.side;
    } else if (brain.score_breakdown?.candidate_side === 'BUY' || brain.score_breakdown?.candidate_side === 'SELL') {
      side = brain.score_breakdown.candidate_side;
    } else if (price > ema20 && ema20 > ema50 && vel >= 0.45) {
      side = 'BUY';
    } else if (price < ema20 && ema20 < ema50 && vel <= -0.45) {
      side = 'SELL';
    }

    if (!side) {
      this._clearIfExpired();
      return;
    }

    // បើមានជញ្ជាំង OB បាំងនៅពីមុខជិតពេក -> មិនគូសទៀនវែងទេ
    if (this._hasImmediateWall(side, price, atr, data)) {
      this.activeProj = null;
      this.smoothTarget = null;
      return;
    }

    // គណនាពិន្ទុផ្ទុះទៀនវែង (Impulse Score 0 - 100)
    let score = 0;
    let gatesPassed = 0;

    // Gate 1: ខួរក្បាល AI V8
    const bConf = Number(brain.confidence_score || 0);
    if (brain.can_trade && brain.side === side) {
      score += (brain.grade === 'A+' || brain.grade === 'A') ? 32 : 24;
      gatesPassed += 1;
    } else if (bConf >= 65 && brain.score_breakdown?.candidate_side === side) {
      score += 18;
      gatesPassed += 1;
    }

    // Gate 2: Squeeze / Tick Velocity (កម្លាំងផ្ទុះពិត)
    const velFav = side === 'BUY' ? vel : -vel;
    if (squeeze === 'FIRING' || velFav >= 0.45) {
      score += 22;
      gatesPassed += 1;
    } else if (squeeze === 'COILING' || velFav >= 0.25) {
      score += 12;
    }

    // Gate 3: Order Book 100ms + CVD លុយធំ
    const bookFav = side === 'BUY' ? depth.bidPct : depth.askPct;
    const cvdFav = side === 'BUY' ? (cvdPct >= 56 || cvdDiv === 'BULL_DIV') : (cvdPct <= 44 || cvdDiv === 'BEAR_DIV');
    if (bookFav >= 58 || cvdFav) {
      score += 20;
      gatesPassed += 1;
    }

    // Gate 4: និន្នាការ VWAP + EMA + MTF + SMT
    const trendFav = side === 'BUY' ? (price >= vwap && price >= ema20) : (price <= vwap && price <= ema20);
    const smtFav = (side === 'BUY' && smtSig === 'BULL_SMT') || (side === 'SELL' && smtSig === 'BEAR_SMT');
    const smtAgainst = (side === 'BUY' && smtSig === 'BEAR_SMT') || (side === 'SELL' && smtSig === 'BULL_SMT');
    if (smtAgainst) score -= 18;
    if (trendFav || mtfAl === side || smtFav) {
      score += 18;
      gatesPassed += 1;
    }

    // Gate 5: ពិនិត្យចម្ងាយគោលដៅ (ត្រូវតែ >= 1.35x ATR ដើម្បីកុំចាប់ទៀនខ្លីៗ)
    const targetObj = this._findLiquidityTarget(side, price, atr, data, brain);
    const reachUsd = Math.abs(targetObj.p - price);
    const minImpulseUsd = Math.max(2.4, atr * 1.35);

    if (reachUsd >= minImpulseUsd) {
      score += 12;
      gatesPassed += 1;
    }

    score = Math.max(0, Math.min(98, score));

    // ចាប់តែកន្លែងខ្លាំងពិតប្រាកដ (Score >= 70 និងឆ្លងកាត់យ៉ាងតិច 4 កត្តា)
    if (score >= 70 && gatesPassed >= 4 && reachUsd >= minImpulseUsd) {
      this.lastTriggeredAt = Date.now();
      if (this.smoothTarget === null || this.activeProj?.side !== side) {
        this.smoothTarget = targetObj.p;
      } else {
        this.smoothTarget = this.smoothTarget + (targetObj.p - this.smoothTarget) * 0.25;
      }

      this.activeProj = {
        side,
        open: price,
        target: Number(this.smoothTarget.toFixed(2)),
        reachUsd: Number(Math.abs(this.smoothTarget - price).toFixed(2)),
        score: Math.round(score),
        tag: targetObj.tag,
        wickTip: Number((side === 'BUY' ? this.smoothTarget + atr * 0.28 : this.smoothTarget - atr * 0.28).toFixed(2)),
      };
    } else {
      this._clearIfExpired();
    }
  }

  _clearIfExpired() {
    if (this.activeProj && (Date.now() - this.lastTriggeredAt) > this.holdMs) {
      this.activeProj = null;
      this.smoothTarget = null;
    }
  }

  _draw(ctx, plotW, plotH, helpers) {
    if (!this.enabled || !this.activeProj || !this.overlay?.chart || isForexWeekendUTC()) return;
    const p = this.activeProj;

    const yOpen = helpers.priceToCoordinate(p.open);
    const yTarget = helpers.priceToCoordinate(p.target);
    const yWick = helpers.priceToCoordinate(p.wickTip);
    if (yOpen === null || yTarget === null) return;

    const tsScale = this.overlay.chart.timeScale();
    const xLast = this.lastCandleTime ? tsScale.timeToCoordinate(this.lastCandleTime) : null;
    const xPrev = this.prevCandleTime ? tsScale.timeToCoordinate(this.prevCandleTime) : null;
    if (xLast === null) return;

    const barGap = (xPrev !== null && xLast > xPrev) ? Math.max(8, Math.min(26, xLast - xPrev)) : 12;
    const ghostW = Math.max(7, Math.min(16, barGap * 0.68));
    const xGhost = Math.min(plotW - 22, xLast + Math.max(barGap * 1.15, ghostW + 5));
    if (xGhost < 10 || xGhost > plotW - 10) return;

    const isBuy = p.side === 'BUY';
    // ពណ៌ប្លែកស្អាត មិនច្រឡំទៀនទីផ្សារពិត (Cyan សម្រាប់ឡើង / Magenta សម្រាប់ចុះ)
    const strokeCol = isBuy ? '#22d3ee' : '#f472b6';
    const glowCol = isBuy ? 'rgba(34, 211, 238, 0.35)' : 'rgba(244, 114, 182, 0.35)';
    const fillTop = isBuy ? 'rgba(34, 211, 238, 0.28)' : 'rgba(244, 114, 182, 0.08)';
    const fillBot = isBuy ? 'rgba(34, 211, 238, 0.06)' : 'rgba(244, 114, 182, 0.28)';
    const textCol = isBuy ? '#a5f3fc' : '#fbcfe8';

    const bodyTop = Math.min(yOpen, yTarget);
    const bodyH = Math.max(12, Math.abs(yTarget - yOpen));

    ctx.save();

    // 1) សរសៃទៀនព្យាករណ៍ (Dashed Wick)
    if (yWick !== null) {
      ctx.strokeStyle = strokeCol;
      ctx.lineWidth = 1.2;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(xGhost, yTarget);
      ctx.lineTo(xGhost, yWick);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 2) តួទៀនព្យាករណ៍ថ្លា + ស៊ុមដាច់ៗភ្លឺ (Glowing Ghost Body)
    const grad = ctx.createLinearGradient(xGhost, bodyTop, xGhost, bodyTop + bodyH);
    grad.addColorStop(0, fillTop);
    grad.addColorStop(1, fillBot);

    ctx.shadowColor = glowCol;
    ctx.shadowBlur = 10;
    ctx.fillStyle = grad;
    helpers.drawRoundRect(ctx, xGhost - ghostW / 2, bodyTop, ghostW, bodyH, 3);
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.strokeStyle = strokeCol;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);

    // 3) ព្រួញទិសដៅ ▲ / ▼
    ctx.fillStyle = strokeCol;
    ctx.beginPath();
    if (isBuy) {
      const ay = Math.max(10, bodyTop - 6);
      ctx.moveTo(xGhost, ay - 5);
      ctx.lineTo(xGhost - 4.5, ay + 1);
      ctx.lineTo(xGhost + 4.5, ay + 1);
    } else {
      const ay = Math.min(plotH - 10, bodyTop + bodyH + 6);
      ctx.moveTo(xGhost, ay + 5);
      ctx.lineTo(xGhost - 4.5, ay - 1);
      ctx.lineTo(xGhost + 4.5, ay - 1);
    }
    ctx.closePath();
    ctx.fill();

    // 4) ខ្សែគោលដៅដាច់ៗ + ផ្លាកប្រាប់កម្រិតឡើង/ចុះដល់ត្រឹមណា
    ctx.strokeStyle = isBuy ? 'rgba(34, 211, 238, 0.72)' : 'rgba(244, 114, 182, 0.72)';
    ctx.lineWidth = 1.1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(xGhost + ghostW / 2 + 2, yTarget);
    ctx.lineTo(plotW - 4, yTarget);
    ctx.stroke();
    ctx.setLineDash([]);

    const label = `⚡ ${isBuy ? '▲' : '▼'} $${p.target.toFixed(1)} (${isBuy ? '+' : '-'}$${p.reachUsd.toFixed(1)} • ${p.score}%)`;
    ctx.font = '800 9.5px system-ui, sans-serif';
    const tw = ctx.measureText(label).width;
    const bw = tw + 12;
    const bh = 18;
    const bx = Math.max(6, Math.min(plotW - bw - 6, xGhost - bw / 2));
    const by = isBuy
      ? Math.max(6, bodyTop - bh - 14)
      : Math.min(plotH - bh - 6, bodyTop + bodyH + 14);

    helpers.drawRoundRect(ctx, bx, by, bw, bh, 5);
    ctx.fillStyle = 'rgba(7, 13, 26, 0.90)';
    ctx.fill();
    ctx.strokeStyle = strokeCol;
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = textCol;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, bx + bw / 2, by + bh / 2 + 0.5);

    ctx.restore();
  }
}

export const impulseGhostLayer = new ImpulseGhostLayer();