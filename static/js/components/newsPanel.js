import { bus } from '../core/eventBus.js';
import { $, escHtml, safeUrl, toNum } from '../core/utils.js';

class NewsPanel {
  init() {
    bus.on('market:full', ({ data }) => this.renderAll(data));
  }

  renderAll(data) {
    this.renderCoreMetrics(data);
    this.renderMatrix(data.matrix);
    this.renderTradeSetup(data);
    if (data.inst_levels) this.renderInstLevels(data.inst_levels);
    if (data.mtf) this.renderMTF(data.mtf);
    if (data.news) this.renderUsdNews(data.news);
    if (data.brain) this.renderBrainCard(data.brain);
  }

  renderCoreMetrics(data) {
    const sigStr = String(data.signal || 'WAIT');
    const confVal = Math.max(0, Math.min(100, Number(data.confidence) || 0));
    if ($('signalBadge')) {
      $('signalBadge').textContent = sigStr;
      $('signalBadge').className = sigStr.includes('BUY')
        ? 'mt-1 text-base sm:text-lg font-black text-emerald-400'
        : sigStr.includes('SELL')
          ? 'mt-1 text-base sm:text-lg font-black text-rose-400'
          : 'mt-1 text-base sm:text-lg font-black text-yellow-400';
    }
    if ($('confBadge')) $('confBadge').textContent = `${confVal}%`;
    if ($('confBar')) {
      $('confBar').style.width = `${confVal}%`;
      $('confBar').className = sigStr.includes('BUY')
        ? 'h-full bg-emerald-400 transition-all duration-500'
        : sigStr.includes('SELL')
          ? 'h-full bg-rose-400 transition-all duration-500'
          : 'h-full bg-yellow-400 transition-all duration-500';
    }

    if ($('rsiVal')) $('rsiVal').textContent = data.rsi ?? '--';
    if ($('atrVal')) $('atrVal').textContent = `ATR: $${data.atr ?? '--'}`;
    if ($('supportVal')) $('supportVal').textContent = data.support != null ? `$${data.support}` : '--';
    if ($('resistanceVal')) $('resistanceVal').textContent = data.resistance != null ? `$${data.resistance}` : '--';
  }

  renderMatrix(matrix) {
    const el = $('techMatrix');
    if (!el || !Array.isArray(matrix)) return;
    el.innerHTML = matrix.map((item) => {
      const cls = item.status === 'BUY'
        ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25'
        : item.status === 'SELL'
          ? 'bg-rose-500/15 text-rose-400 border-rose-500/25'
          : 'bg-yellow-500/15 text-yellow-300 border-yellow-500/25';
      return `<div class="bg-black/30 border border-white/5 rounded-xl p-2.5"><div class="flex items-center justify-between gap-1"><span class="text-[10px] text-gray-400">${escHtml(item.name)}</span><span class="text-[9px] px-1.5 py-0.5 rounded font-bold border ${cls}">${escHtml(item.status)}</span></div><div class="text-xs font-bold text-gray-100 mt-1 truncate tabular-nums">${escHtml(item.value)}</div></div>`;
    }).join('');
  }

  renderTradeSetup(data) {
    const usePlan = (data.brain?.can_trade && data.brain?.plan)
      ? data.brain.plan
      : (data.brain?.preview_plan || data.setup || null);

    if (usePlan) {
      const slNum = toNum(usePlan.sl);
      const tp1Num = toNum(usePlan.tp ?? usePlan.tp1);
      const tp2Num = toNum(usePlan.tp2 ?? data.setup?.tp2);
      if ($('slVal')) $('slVal').textContent = slNum !== null ? `$${slNum.toFixed(2)}` : '--';
      if ($('tp1Val')) $('tp1Val').textContent = tp1Num !== null ? `$${tp1Num.toFixed(2)}` : '--';
      if ($('tp2Val')) $('tp2Val').textContent = tp2Num !== null ? `$${tp2Num.toFixed(2)}` : '--';
      if ($('rrVal')) $('rrVal').textContent = `R:R ${usePlan.rr || data.setup?.rr || '2:1'}`;
    }

    if ($('reasonsList')) {
      const list = Array.isArray(data.reasons) && data.reasons.length ? data.reasons : ['កំពុងវិភាគសូចនាករ...'];
      $('reasonsList').innerHTML = list.map((r) => `<li>${escHtml(r)}</li>`).join('');
    }
  }

  renderInstLevels(inst) {
    if ($('pdhVal')) $('pdhVal').textContent = inst.pdh ? `$${inst.pdh}` : '--';
    if ($('pdlVal')) $('pdlVal').textContent = inst.pdl ? `$${inst.pdl}` : '--';
    if ($('asianHighVal')) $('asianHighVal').textContent = inst.asian_high ? `$${inst.asian_high}` : '--';
    if ($('asianLowVal')) $('asianLowVal').textContent = inst.asian_low ? `$${inst.asian_low}` : '--';
  }

  renderUsdNews(n) {
    const bar = $('newsBanner');
    if (!bar) return;

    if (n.danger && n.danger_event) {
      const ev = n.danger_event;
      bar.className = 'rounded-2xl px-3.5 py-2.5 flex items-center justify-between gap-2 text-xs bg-rose-500/20 border border-rose-500/50 animate-pulse';
      if ($('newsText')) $('newsText').innerHTML = `<b class="text-rose-300">ព្រមានព័ត៌មានក្រហម USD៖</b> ${escHtml(ev.title)} (នៅសល់ ${ev.mins_until} នាទី)`;
      if ($('newsSubTime')) $('newsSubTime').textContent = `កម្ពុជា ${ev.time_kh || '--'} | ${ev.time_ny || '--'} (${ev.time_utc || '--'})`;
      if ($('newsBadge')) {
        $('newsBadge').textContent = 'DANGER';
        $('newsBadge').className = 'shrink-0 text-[10px] px-2 py-0.5 rounded-full bg-rose-500 text-white font-black';
      }
    } else if (n.upcoming?.length) {
      const ev = n.upcoming[0];
      bar.className = 'glass rounded-2xl px-3.5 py-2.5 flex items-center justify-between gap-2 text-xs';
      if ($('newsText')) $('newsText').innerHTML = `<span class="text-gray-400">ព័ត៌មាន USD បន្ទាប់៖</span> <b class="text-white">${escHtml(ev.title)}</b> <span class="text-sky-300">(ក្នុង ${(ev.mins_until / 60).toFixed(1)} ម៉ោង)</span>`;
      if ($('newsSubTime')) $('newsSubTime').textContent = `កម្ពុជា ${ev.time_kh || '--'} | ${ev.time_ny || '--'}`;
      if ($('newsBadge')) {
        $('newsBadge').textContent = 'SAFE NOW';
        $('newsBadge').className = 'shrink-0 text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 font-bold border border-emerald-500/25';
      }
    } else {
      bar.className = 'glass rounded-2xl px-3.5 py-2.5 flex items-center justify-between gap-2 text-xs';
      if ($('newsText')) $('newsText').textContent = 'គ្មានព័ត៌មានសេដ្ឋកិច្ច USD កម្រិតក្រហមបន្ទាន់ក្នុងពេលនេះទេ';
      if ($('newsSubTime')) $('newsSubTime').textContent = n.fetched_kh ? `ចាប់ទិន្នន័យ៖ កម្ពុជា ${n.fetched_kh} | ${n.fetched_ny}` : '';
      if ($('newsBadge')) {
        $('newsBadge').textContent = 'CLEAR';
        $('newsBadge').className = 'shrink-0 text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 font-bold border border-emerald-500/25';
      }
    }
  }

  renderMTF(m) {
    if (!m?.timeframes?.length) return;
    if ($('mtfVerdict')) {
      $('mtfVerdict').textContent = m.verdict;
      $('mtfVerdict').className = m.aligned === 'BUY'
        ? 'text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-bold border border-emerald-500/30'
        : m.aligned === 'SELL'
          ? 'text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-bold border border-rose-500/30'
          : 'text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-bold border border-amber-500/30';
    }
    if ($('mtfGrid')) {
      $('mtfGrid').innerHTML = m.timeframes.map((t) => {
        const cls = t.bias === 'BUY'
          ? 'border-emerald-500/35 bg-emerald-500/10 text-emerald-400'
          : t.bias === 'SELL'
            ? 'border-rose-500/35 bg-rose-500/10 text-rose-400'
            : 'border-white/10 bg-black/30 text-yellow-300';
        return `<div class="rounded-xl p-2 text-center border ${cls}"><div class="text-[10px] text-gray-300 font-semibold">${escHtml(t.label)}</div><div class="text-xs font-black mt-0.5">${escHtml(t.bias)}</div><div class="text-[9px] text-gray-400 mt-0.5">RSI ${escHtml(t.rsi)}</div></div>`;
      }).join('');
    }
    if ($('mtfWarning')) {
      if (m.warning) {
        if ($('mtfWarningText')) $('mtfWarningText').textContent = m.warning;
        $('mtfWarning').classList.remove('hidden');
      } else {
        $('mtfWarning').classList.add('hidden');
      }
    }
  }

  renderGlobalNews(gn) {
    if ($('gNewsUpdated')) $('gNewsUpdated').textContent = gn.updated_at ? `ចាប់ទិន្នន័យ៖ ${gn.updated_at}` : '';
    if ($('gNewsSummary')) $('gNewsSummary').textContent = gn.summary_km || '';
    if ($('gNewsBiasBadge')) {
      $('gNewsBiasBadge').textContent = `${gn.bias || 'NEUTRAL'}${typeof gn.score === 'number' ? ` (${gn.score >= 0 ? '+' : ''}${gn.score.toFixed(2)})` : ''}`;
      $('gNewsBiasBadge').className = gn.bias === 'BULLISH'
        ? 'text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-bold border border-emerald-500/30'
        : gn.bias === 'BEARISH'
          ? 'text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-bold border border-rose-500/30'
          : 'text-[10px] px-2 py-0.5 rounded-full bg-white/5 text-gray-300 font-bold border border-white/10';
    }
    if ($('gNewsList')) {
      $('gNewsList').innerHTML = gn.headlines?.length
        ? gn.headlines.map((it) => {
          const u = safeUrl(it.link);
          const tagCls = it.impact === 'BULLISH'
            ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25'
            : it.impact === 'BEARISH'
              ? 'bg-rose-500/15 text-rose-400 border-rose-500/25'
              : 'bg-white/5 text-gray-400 border-white/10';
          return `<div class="flex items-start justify-between gap-2 bg-black/30 border border-white/5 rounded-xl px-2.5 py-2"><div class="min-w-0 flex-1"><div class="text-[11px] text-gray-100 leading-snug">${u ? `<a href="${u}" target="_blank" rel="noopener noreferrer" class="hover:text-yellow-300">${escHtml(it.title)}</a>` : escHtml(it.title)}</div><div class="text-[9.5px] text-gray-400 mt-0.5 tabular-nums">${escHtml(it.source)} • ${it.time_kh ? `កម្ពុជា ${escHtml(it.time_kh)} | ${escHtml(it.time_ny || '')}` : ''}</div></div><span class="shrink-0 text-[9px] px-1.5 py-0.5 rounded font-bold border ${tagCls}">${escHtml(it.impact)}</span></div>`;
        }).join('')
        : '<div class="text-[11px] text-gray-500">មិនទាន់មានព័ត៌មានថ្មីៗទេ។</div>';
    }
  }

  renderWarNews(wn) {
    if ($('wNewsUpdated')) $('wNewsUpdated').textContent = wn.updated_at ? `ចាប់ទិន្នន័យ៖ ${wn.updated_at}` : '';
    if ($('wNewsSummary')) $('wNewsSummary').textContent = wn.summary_km || '';
    if ($('wNewsLevelBadge')) {
      $('wNewsLevelBadge').textContent = wn.level || 'LOW';
      $('wNewsLevelBadge').className = wn.level === 'HIGH'
        ? 'text-[10px] px-2 py-0.5 rounded-full bg-rose-500 text-white font-black'
        : wn.level === 'MEDIUM'
          ? 'text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-bold border border-amber-400/35'
          : wn.level === 'COOLING'
            ? 'text-[10px] px-2 py-0.5 rounded-full bg-sky-500/20 text-sky-300 font-bold border border-sky-400/30'
            : 'text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 font-bold border border-emerald-500/25';
    }
    if ($('wNewsList')) {
      $('wNewsList').innerHTML = wn.headlines?.length
        ? wn.headlines.map((it) => {
          const u = safeUrl(it.link);
          const tagCls = it.severity === 'ESCALATION'
            ? 'bg-rose-500/20 text-rose-300 border-rose-500/35'
            : it.severity === 'PEACE/COOL'
              ? 'bg-sky-500/15 text-sky-300 border-sky-400/30'
              : 'bg-amber-500/15 text-amber-300 border-amber-400/30';
          return `<div class="flex items-start justify-between gap-2 bg-black/30 border border-white/5 rounded-xl px-2.5 py-2"><div class="min-w-0 flex-1"><div class="text-[11px] text-gray-100 leading-snug">${u ? `<a href="${u}" target="_blank" rel="noopener noreferrer" class="hover:text-yellow-300">${escHtml(it.title)}</a>` : escHtml(it.title)}</div><div class="text-[9.5px] text-gray-400 mt-0.5 tabular-nums">${escHtml(it.source)} • ${it.time_kh ? `កម្ពុជា ${escHtml(it.time_kh)} | ${escHtml(it.time_ny || '')}` : ''}</div></div><span class="shrink-0 text-[9px] px-1.5 py-0.5 rounded font-bold border ${tagCls}">${escHtml(it.severity)}</span></div>`;
        }).join('')
        : '<div class="text-[11px] text-gray-500">គ្មានព័ត៌មានសង្គ្រាមបន្ទាន់ទេ។</div>';
    }
  }

  renderBrainCard(b) {
    if (!b) return;
    if ($('brainBarTime')) $('brainBarTime').textContent = b.bar_time ? `ទៀន ${b.bar_time}` : '';
    if ($('brainRankVal')) $('brainRankVal').textContent = b.ai_rank_pct != null ? `${b.ai_rank_pct}% / ${b.cutoff_pct || 50}%` : `${b.confidence_score || '--'}%`;
    if ($('brainModeBadge')) $('brainModeBadge').textContent = b.tactical_mode === 'V8_CONFIRMED' ? 'V8 CONFIRMED' : (b.tactical_mode || 'V10 PRO');
    if ($('brainActionText')) $('brainActionText').textContent = b.action_km || '--';
    if ($('brainMarketText')) $('brainMarketText').textContent = b.market_state?.summary_km || 'កំពុងតាមដាន...';

    const card = $('brainCard'), icon = $('brainIconBox'), act = $('brainActionText'), pill = $('brainStatusPill');
    if (card && icon && act && pill) {
      if (b.status === 'APPROVED_BUY') {
        card.className = 'glass rounded-2xl p-3.5 border-l-4 border-l-emerald-400 space-y-2.5';
        icon.className = 'w-8 h-8 rounded-xl bg-emerald-500/20 border border-emerald-400/40 flex items-center justify-center text-emerald-300 shrink-0';
        act.className = 'text-xs sm:text-sm font-black text-emerald-400 mt-0.5';
        pill.textContent = `BUY • ${b.grade || 'A'}`;
        pill.className = 'text-[10px] sm:text-[11px] px-2.5 py-1 rounded-xl bg-emerald-500 text-[#070b13] font-black';
      } else if (b.status === 'APPROVED_SELL') {
        card.className = 'glass rounded-2xl p-3.5 border-l-4 border-l-rose-500 space-y-2.5';
        icon.className = 'w-8 h-8 rounded-xl bg-rose-500/20 border border-rose-400/40 flex items-center justify-center text-rose-300 shrink-0';
        act.className = 'text-xs sm:text-sm font-black text-rose-400 mt-0.5';
        pill.textContent = `SELL • ${b.grade || 'A'}`;
        pill.className = 'text-[10px] sm:text-[11px] px-2.5 py-1 rounded-xl bg-rose-500 text-white font-black';
      } else if (b.status === 'BLOCKED' || b.is_danger) {
        card.className = 'glass rounded-2xl p-3.5 border-l-4 border-l-rose-500 space-y-2.5';
        icon.className = 'w-8 h-8 rounded-xl bg-rose-500/15 border border-rose-400/35 flex items-center justify-center text-rose-300 shrink-0';
        act.className = 'text-xs sm:text-sm font-black text-rose-300 mt-0.5';
        pill.textContent = 'ហាមចូល';
        pill.className = 'text-[10px] sm:text-[11px] px-2.5 py-1 rounded-xl bg-rose-500/25 text-rose-300 border border-rose-400/40 font-black';
      } else {
        card.className = 'glass rounded-2xl p-3.5 border-l-4 border-l-amber-400 space-y-2.5';
        icon.className = 'w-8 h-8 rounded-xl bg-amber-500/15 border border-amber-400/35 flex items-center justify-center text-amber-300 shrink-0';
        act.className = 'text-xs sm:text-sm font-black text-amber-300 mt-0.5';
        pill.textContent = 'ត្រៀមចូល';
        pill.className = 'text-[10px] sm:text-[11px] px-2.5 py-1 rounded-xl bg-sky-500/20 text-sky-300 border border-sky-400/35 font-black';
      }
    }

    const planBox = $('brainPlanBox');
    if (planBox) {
      if (b.can_trade && b.plan && toNum(b.plan.entry) !== null) {
        planBox.classList.remove('hidden');
        if ($('bpEntry')) $('bpEntry').textContent = `$${Number(b.plan.entry).toFixed(2)}`;
        if ($('bpSl')) $('bpSl').textContent = toNum(b.plan.sl) !== null ? `$${Number(b.plan.sl).toFixed(2)}` : '--';
        const tpv = toNum(b.plan.tp ?? b.plan.tp1);
        if ($('bpTp')) $('bpTp').textContent = tpv !== null ? `$${tpv.toFixed(2)}` : '--';
        if ($('bpRisk')) $('bpRisk').textContent = b.plan.suggested_risk_pct != null ? `${b.plan.suggested_risk_pct}%` : '--';
      } else {
        planBox.classList.add('hidden');
      }
    }

    if ($('brainReasonsList')) {
      const reasons = b.reasons?.length ? b.reasons : ['កំពុងរង់ចាំលក្ខខណ្ឌច្បាស់លាស់'];
      $('brainReasonsList').innerHTML = reasons.map((r) => `<li>${escHtml(r)}</li>`).join('');
    }

    if (b.macro) {
      if ($('macroDxy')) $('macroDxy').textContent = b.macro.dxy ?? '--';
      if ($('macroUs10y')) $('macroUs10y').textContent = b.macro.us10y != null ? `${b.macro.us10y}%` : '--';
      if ($('macroVix')) $('macroVix').textContent = b.macro.vix ?? '--';
      if ($('macroReal10')) $('macroReal10').textContent = b.macro.real10 != null ? `${b.macro.real10}%` : '--';
    }
    if (b.global_news) this.renderGlobalNews(b.global_news);
    if (b.war_news) this.renderWarNews(b.war_news);
  }
}

export const newsPanel = new NewsPanel();