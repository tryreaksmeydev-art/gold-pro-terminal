def generate_ai_reply(
    preset: str,
    data: dict,
    interval: str,
    mtf: dict,
    news: dict,
    inst: dict,
    brain: dict | None = None,
) -> dict:
    p, sig, conf, s = data["price"], data["signal"], data["confidence"], data["setup"]
    rsi, atr = data["rsi"], data["atr"]
    piv, pat, sess = data["pivot"], data["pattern"], data["session"]
    brain = brain or {}
    gnews = brain.get("global_news") or {}
    wnews = brain.get("war_news") or {}
    macro = brain.get("macro") or {}
    shield = brain.get("exit_shield") or {}
    pillars = brain.get("pillars") or {}
    stats = brain.get("signal_stats") or {}
    oflow = data.get("order_flow") or {}

    if preset == "brain_v8":
        rank_txt = f"{brain['ai_rank_pct']}%" if brain.get("ai_rank_pct") is not None else "--"
        wr_txt = (
            f"{stats['tf_win_rate']}% ({interval})"
            if stats.get("tf_win_rate") is not None
            else (f"{stats['win_rate']}% រួម" if stats.get("win_rate") is not None else "កំពុងប្រមូលស្ថិតិ")
        )
        lines = [
            f"សេចក្តីសម្រេច V10.6៖ **{brain.get('action_km', 'កំពុងវិភាគ...')}** (AI Rank: **{rank_txt}** | Score: **{brain.get('confidence_score', '--')}%**)",
            f"ប្រព័ន្ធការពារ (Exit Shield)៖ **{shield.get('title_km', 'សកម្ម')}** — {shield.get('action_km', '')}",
            f"Order Flow ផ្ទាល់ ({oflow.get('source', 'LIVE')})៖ កម្លាំងទិញ **{oflow.get('buy_pct', brain.get('buy_pressure_pct', 50))}%** | ស្ថិតិ AI ពិត៖ **{wr_txt}**",
            f"ម៉ាក្រូ & ព័ត៌មាន៖ DXY **{macro.get('dxy') or '--'}** | US10Y **{macro.get('us10y') or '--'}%** | VIX **{macro.get('vix') or '--'}** | សង្គ្រាម **{wnews.get('level', 'LOW')}**",
        ]
        if pillars:
            v8p = pillars.get("v8_ai", {})
            stp = pillars.get("structure_smc", {})
            mcp = pillars.get("macro_news", {})
            lines.append(
                f"ពិន្ទុ ៣ សសរស្តម្ភ៖ V8 ({v8p.get('buy', 0)}/{v8p.get('sell', 0)}) • "
                f"OB/SMC ({stp.get('buy', 0)}/{stp.get('sell', 0)}) • Macro ({mcp.get('buy', 0)}/{mcp.get('sell', 0)})"
            )
        pl = brain.get("plan") or brain.get("preview_plan")
        if pl:
            lines.append(
                f"ផែនការ៖ Entry **${pl['entry']}** | SL **${pl['sl']}** | "
                f"TP1 **${pl['tp']}** | BE @ **${pl.get('be_trigger', '--')}**"
            )
        for r in (brain.get("reasons") or [])[:2]:
            lines.append(f"មូលហេតុ៖ {r}")
        return {
            "title": f"ខួរក្បាល AI V10.6 • OB/SMC + Order Flow + Macro ({brain.get('bar_time') or '1H'})",
            "badge": brain.get("status", "WAIT"),
            "lines": lines,
        }

    if preset == "mtf_check":
        tf_summary = " | ".join([f"{t['label']}: **{t['bias']}**" for t in mtf["timeframes"]])
        lines = [
            f"លទ្ធផល 4 Timeframes៖ {tf_summary}",
            f"ការវាយតម្លៃរួម៖ **{mtf['verdict']}** (Buy: {mtf['buy_count']}/4 | Sell: {mtf['sell_count']}/4)",
            f"ការព្រមាន៖ **{mtf['warning']}**។" if mtf["warning"] else "Timeframe ធំ និងតូចកំពុងស្របទិសគ្នាល្អ អាចរង់ចាំចំណុចចូលតាមនិន្នាការមេបាន។",
        ]
        return {"title": "ផ្ទៀងផ្ទាត់ 4 Timeframes (MTF Confluence)", "badge": mtf["verdict"], "lines": lines}

    if preset == "news_check":
        g_line = f"ព័ត៌មានមាសពិភពលោក៖ **{gnews.get('bias', 'NEUTRAL')}** ({gnews.get('updated_at', '')})"
        w_line = f"ព័ត៌មានសង្គ្រាម៖ **{wnews.get('level', 'LOW')}** ({wnews.get('updated_at', '')})"
        m_line = f"ម៉ាក្រូអាមេរិក៖ DXY **{macro.get('dxy') or '--'}** | US10Y **{macro.get('us10y') or '--'}%** | Real10Y **{macro.get('real10') or '--'}%**"
        if news["danger"] and news["danger_event"]:
            ev = news["danger_event"]
            return {
                "title": "ព្រមានព័ត៌មានសេដ្ឋកិច្ចអាមេរិក (USD RED FOLDER)", "badge": "DANGER",
                "lines": [
                    f"ព័ត៌មានជិតដល់៖ **{ev['title']}** (ម៉ោងកម្ពុជា **{ev.get('time_kh', '--')}** | ក្រៅស្រុក **{ev.get('time_ny', '--')}**)",
                    f"Forecast: **{ev['forecast']}** | Previous: **{ev['previous']}**",
                    g_line, w_line, m_line,
                    "អនុសាសន៍៖ **ផ្អាកការចូល Order ថ្មីជាបន្ទាន់** ព្រោះមាសអាចលោតខ្លាំង និងរីក Spread។",
                ],
            }
        if news["upcoming"]:
            nxt = news["upcoming"][0]
            return {
                "title": "ប្រតិទិន USD • មាសពិភពលោក & សង្គ្រាម", "badge": gnews.get("bias", "SAFE NOW"),
                "lines": [
                    "បច្ចុប្បន្ន **គ្មានព័ត៌មានក្រហម USD** ក្នុងរយៈពេល ៦០ នាទីខាងមុខទេ។",
                    f"ព័ត៌មានធំបន្ទាប់៖ **{nxt['title']}** (កម្ពុជា **{nxt.get('time_kh', '--')}** | ក្រៅស្រុក **{nxt.get('time_ny', '--')}**)",
                    g_line, w_line, m_line,
                ],
            }
        return {
            "title": "ប្រតិទិន USD • មាសពិភពលោក & សង្គ្រាម", "badge": gnews.get("bias", "CLEAR"),
            "lines": ["គ្មានព័ត៌មាន USD កម្រិតក្រហមបន្ទាន់ទេ។", g_line, w_line, m_line],
        }

    if preset == "inst_levels":
        return {
            "title": "តំបន់តម្លៃស្ថាប័ន (Institutional Levels)", "badge": f"PRICE ${p}",
            "lines": [
                f"PDH (ខ្ពស់ម្សិលមិញ)៖ **${inst['pdh'] or '--'}** | PDL (ទាបម្សិលមិញ)៖ **${inst['pdl'] or '--'}**",
                f"Asian High៖ **${inst['asian_high'] or '--'}** | Asian Low៖ **${inst['asian_low'] or '--'}**",
                "យុទ្ធសាស្ត្រ៖ តាមដានការបោសសន្ទនីយភាព (Liquidity Sweep) លើតំបន់ទាំងនេះក្នុងវគ្គ London និង New York។",
            ],
        }

    if preset == "entry":
        v_line = f"ខួរក្បាល AI V10.6៖ **{brain.get('action_km', 'WAIT')}**"
        if brain.get("reasons"):
            v_line += f" ({brain['reasons'][0]})"
        return {
            "title": f"វិភាគចំណុចចូលជាក់ស្តែង ({interval})", "badge": brain.get("status", f"{sig} • {conf}%"),
            "lines": [
                v_line,
                f"តម្លៃបច្ចុប្បន្ន៖ **${p}** | Order Flow ទិញ៖ **{oflow.get('buy_pct', brain.get('buy_pressure_pct', 50))}%** | តំបន់ចូល៖ **{s['entry_zone']}**",
                mtf["warning"] if mtf["warning"] else f"MTF Confluence៖ **{mtf['verdict']}**",
                f"ការពារ៖ {shield.get('title_km', '')} — {brain.get('hud_hint_km') or s['advice']}។",
            ],
        }

    if preset == "risk_plan":
        pl = brain.get("plan") or brain.get("preview_plan") or s
        sl_d = pl.get("sl_dist", s["sl_dist"])
        tp1_v = pl.get("tp", pl.get("tp1", s["tp1"]))
        tp2_v = pl.get("tp2", s["tp2"])
        be_v = pl.get("be_trigger", "--")
        risk_p = pl.get("suggested_risk_pct", 0.75)
        return {
            "title": f"ផែនការ SL / TP និងវិន័យដើមទុន ({interval})", "badge": f"R:R {pl.get('rr', s['rr'])}",
            "lines": [
                f"ទិសដៅ៖ **{brain.get('side') if brain.get('can_trade') else sig}** | Entry៖ **${pl['entry']}** (Risk ណែនាំ៖ **{risk_p}%** នៃដើមទុន)",
                f"Stop Loss៖ **${pl['sl']}** (ចម្ងាយ ~${sl_d} ឬ {int(float(sl_d) * 10)} pips)",
                f"Take Profit 1៖ **${tp1_v}** (កាត់ 50%–60%) | Take Profit 2៖ **${tp2_v}**",
                f"ការពារដើមទុន (Zero-Risk)៖ ពេលដល់ **${be_v}** (50% នៃ TP1) ត្រូវរំកិល SL មកស្មើដើមភ្លាម។",
            ],
        }

    if preset == "deep_scan":
        return {
            "title": f"ស្កេនសូចនាករ 5-in-1 ({interval})", "badge": f"CONF {conf}%",
            "lines": [
                f"EMA20 (**${data['ema20']}**) vs EMA50 (**${data['ema50']}**)",
                f"RSI (14)៖ **{rsi}** | MACD Hist៖ **{data['macd_hist']:+.3f}**",
                f"Bollinger Bands៖ **${data['bb_lower']}** - **${data['bb_upper']}**",
                f"Pivot Point៖ **${piv}** | ATR (14)៖ **${atr}** | Order Flow ទិញ៖ **{oflow.get('buy_pct', 50)}%**",
            ],
        }

    if preset == "trend":
        tr = "និន្នាការឡើង (Uptrend)" if data["ema20"] > data["ema50"] else "និន្នាការចុះ (Downtrend)"
        return {
            "title": f"វិភាគនិន្នាការទីផ្សារ ({interval})", "badge": tr,
            "lines": [
                f"និន្នាការ {interval}៖ **{tr}** | MTF រួម៖ **{mtf['verdict']}**",
                f"តម្លៃ (**${p}**) នៅ **{'ខាងលើ' if p > data['ema20'] else 'ខាងក្រោម'}** EMA 20 (**${data['ema20']}**)",
                "គោលការណ៍ជួញដូរ៖ ចូល Order តែពេល Timeframe តូចស្របតាម 1H និង 4H។",
            ],
        }

    if preset == "pattern":
        return {
            "title": f"វិភាគទម្រង់ទៀន Price Action ({interval})", "badge": pat["name"],
            "lines": [
                f"ទម្រង់ទៀន៖ **{pat['name']}** ({pat['bias']})",
                f"អត្ថន័យ៖ {pat['desc']}។",
                f"PDH/PDL៖ **${inst['pdh'] or '--'}** / **${inst['pdl'] or '--'}**",
            ],
        }

    if preset == "volatility":
        vol = "ខ្ពស់ (High)" if atr >= 5.5 else ("មធ្យម (Moderate)" if atr >= 2.5 else "ទាប (Quiet)")
        return {
            "title": f"វាយតម្លៃហានិភ័យ និងវគ្គទីផ្សារ ({interval})", "badge": sess["name"],
            "lines": [
                f"វគ្គទីផ្សារ៖ **{sess['name']}** ({sess['liquidity']})",
                f"កម្រិតរំញ័រ ATR (14)៖ **${atr}** — ល្បឿន **{vol}**",
                f"ចំណាំ៖ {sess['note']}។",
            ],
        }

    return {
        "title": f"សង្ខេបទីផ្សារមាសពេញលេញ ({interval})", "badge": brain.get("status", f"{sig} • {conf}%"),
        "lines": [
            f"ខួរក្បាល AI V10.6៖ **{brain.get('action_km', 'WAIT')}** | Order Flow ទិញ៖ **{oflow.get('buy_pct', brain.get('buy_pressure_pct', 50))}%**",
            f"តម្លៃផ្ទាល់៖ **${p}** | វគ្គ៖ **{sess['name']}** | ព័ត៌មានមាស៖ **{gnews.get('bias', 'NEUTRAL')}** | សង្គ្រាម៖ **{wnews.get('level', 'LOW')}**",
            f"Exit Shield៖ **{shield.get('title_km', 'សកម្ម')}** | MTF 4-TF៖ **{mtf['verdict']}**",
            f"អនុសាសន៍៖ {brain.get('hud_hint_km') or s['advice']}",
        ],
    }