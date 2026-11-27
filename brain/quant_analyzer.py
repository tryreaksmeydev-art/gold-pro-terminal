import pandas as pd
from .utils import safe_float


def analyze_microstructure(
    candles: list,
    price: float,
    ema20: float,
    ema50: float,
    atr_val: float,
    smc: dict,
    order_flow: dict | None = None,
) -> dict:
    res = {
        "buy_pts": 0, "sell_pts": 0,
        "notes_buy": [], "notes_sell": [],
        "bull_reversal": False, "bear_reversal": False,
        "close_confirmed_buy": False, "close_confirmed_sell": False,
        "buy_pressure_pct": 50,
        "falling_knife": False, "rising_rocket": False,
        "in_bullish_ob": None, "in_bearish_ob": None,
        "sweep_note": None, "climax": None,
    }
    if not candles or len(candles) < 8:
        return res

    c0, c1, c2 = candles[-1], candles[-2], candles[-3]
    o0, h0, l0, cl0 = safe_float(c0.get("open")), safe_float(c0.get("high")), safe_float(c0.get("low")), safe_float(c0.get("close"), price)
    o1, h1, l1, cl1 = safe_float(c1.get("open")), safe_float(c1.get("high")), safe_float(c1.get("low")), safe_float(c1.get("close"))
    o2, h2, l2, cl2 = safe_float(c2.get("open")), safe_float(c2.get("high")), safe_float(c2.get("low")), safe_float(c2.get("close"))
    if h1 <= 0 or l1 <= 0 or h1 < l1:
        return res

    # 0) Buy/Sell Pressure (%) — ប្រើ Real Order Flow ពី Binance/OKX មុន បើគ្មានទើបគណនាពីទៀន
    if order_flow and int(order_flow.get("trades_count", 0)) >= 20:
        buy_press = int(round(max(0.0, min(100.0, safe_float(order_flow.get("buy_pct"), 50.0)))))
    else:
        buy_vol, tot_vol = 0.0, 0.0
        for c in candles[-5:]:
            ch, cl_l, cc = safe_float(c.get("high")), safe_float(c.get("low")), safe_float(c.get("close"))
            rng = max(ch - cl_l, 0.01)
            wt = max(safe_float(c.get("volume"), 0.0), rng)
            buy_vol += max(0.0, min(1.0, (cc - cl_l) / rng)) * wt
            tot_vol += wt
        buy_press = int(round((buy_vol / tot_vol) * 100)) if tot_vol > 0 else 50

    res["buy_pressure_pct"] = buy_press
    if buy_press >= 62:
        res["buy_pts"] += 6
        res["notes_buy"].append(f"កម្លាំងលុយទិញឈ្នះ ({buy_press}%)")
    elif buy_press <= 38:
        res["sell_pts"] += 6
        res["notes_sell"].append(f"កម្លាំងលុយលក់ឈ្នះ ({100 - buy_press}%)")

    rng1, body1 = max(h1 - l1, 0.01), abs(cl1 - o1)
    up_wick1, lo_wick1 = h1 - max(o1, cl1), min(o1, cl1) - l1
    atr_ref = max(safe_float(atr_val, 2.0), 0.6)
    c1_green, c1_red, c0_green, c0_red = cl1 > o1, cl1 < o1, cl0 > o0, cl0 < o0

    # 1) Bullish OB & Bearish OB Reaction
    ob_buf = max(atr_ref * 0.35, 0.40)
    for ob in (smc.get("demand_blocks") or []):
        if str(ob.get("label", "OB")).upper() != "OB":
            continue
        b_bot, b_top = safe_float(ob.get("bottom")), safe_float(ob.get("top"))
        if 0 < b_bot and (b_bot - ob_buf) <= price <= (b_top + ob_buf):
            res["in_bullish_ob"] = (b_bot, b_top)
            if c0_green or c1_green:
                res["buy_pts"] += 10
                res["bull_reversal"] = True
                res["notes_buy"].insert(0, f"តម្លៃប៉ះ Bullish OB (${b_bot:.2f}–${b_top:.2f}) ហើយស្ទុះឡើង")
            break

    for sob in (smc.get("supply_blocks") or []):
        if str(sob.get("label", "OB")).upper() != "OB":
            continue
        s_bot, s_top = safe_float(sob.get("bottom")), safe_float(sob.get("top"))
        if 0 < s_bot and (s_bot - ob_buf) <= price <= (s_top + ob_buf):
            res["in_bearish_ob"] = (s_bot, s_top)
            if c0_red or c1_red:
                res["sell_pts"] += 10
                res["bear_reversal"] = True
                res["notes_sell"].insert(0, f"តម្លៃប៉ះ Bearish OB (${s_bot:.2f}–${s_top:.2f}) ហើយទម្លាក់ចុះ")
            break

    # 2) Candle Close Confirmation
    ema_top, ema_bot = max(ema20, ema50), min(ema20, ema50)
    if c1_green and cl1 > ema20 and body1 >= rng1 * 0.35:
        res["close_confirmed_buy"] = True
    if c1_red and cl1 < ema20 and body1 >= rng1 * 0.35:
        res["close_confirmed_sell"] = True

    # 3) Liquidity Sweep
    prev_window = candles[-16:-2]
    prev_low = min((safe_float(c.get("low"), 1e9) for c in prev_window), default=l1)
    prev_high = max((safe_float(c.get("high"), 0.0) for c in prev_window), default=h1)
    if l1 < prev_low and cl1 > prev_low and lo_wick1 >= body1 * 0.8:
        res["buy_pts"] += 10
        res["bull_reversal"] = True
        res["sweep_note"] = f"Liquidity Sweep (បោស SL ក្រោម ${prev_low:.2f})"
        res["notes_buy"].append(res["sweep_note"])
    elif h1 > prev_high and cl1 < prev_high and up_wick1 >= body1 * 0.8:
        res["sell_pts"] += 10
        res["bear_reversal"] = True
        res["sweep_note"] = f"Liquidity Sweep (បោស SL លើ ${prev_high:.2f})"
        res["notes_sell"].append(res["sweep_note"])

    # 4) Recent SMC Break
    last_ts, prev6_ts = safe_float(c0.get("time"), 0.0), safe_float(candles[-6].get("time"), 0.0)
    for br in reversed(smc.get("breaks") or []):
        end_t = safe_float(br.get("end_time"), 0.0)
        if prev6_ts <= end_t <= last_ts + 86400:
            btype, lbl = str(br.get("type") or ""), br.get("label") or "BOS"
            if btype.endswith("_UP"):
                res["buy_pts"] += 10
                res["notes_buy"].append(f"SMC បែករចនាសម្ព័ន្ធឡើង ({lbl})")
            elif btype.endswith("_DOWN") or btype.endswith("_DN"):
                res["sell_pts"] += 10
                res["notes_sell"].append(f"SMC បែករចនាសម្ព័ន្ធចុះ ({lbl})")
            break

    # 5) Reversal, EMA & Momentum
    bull_pin = (lo_wick1 / rng1) >= 0.46 and lo_wick1 > body1 * 1.15
    bear_pin = (up_wick1 / rng1) >= 0.46 and up_wick1 > body1 * 1.15
    bull_engulf = c1_green and (cl2 < o2) and cl1 >= o2 and body1 >= rng1 * 0.40
    bear_engulf = c1_red and (cl2 > o2) and cl1 <= o2 and body1 >= rng1 * 0.40

    if (bull_pin or bull_engulf or (c1_green and body1 >= rng1 * 0.45)) and cl0 >= l1:
        res["bull_reversal"] = True
    if (bear_pin or bear_engulf or (c1_red and body1 >= rng1 * 0.45)) and cl0 <= h1:
        res["bear_reversal"] = True

    if price < ema20 and ((c1_red and c0_red) or (c1_red and cl0 < l1) or ((ema20 - price) > atr_ref * 0.70 and not res["bull_reversal"])):
        res["falling_knife"] = True
    if price > ema20 and ((c1_green and c0_green) or (c1_green and cl0 > h1) or ((price - ema20) > atr_ref * 0.70 and not res["bear_reversal"])):
        res["rising_rocket"] = True

    if price > ema20 and ema20 >= ema50:
        res["buy_pts"] += 14
        res["notes_buy"].append("និន្នាការ EMA 20 > EMA 50")
    elif price < ema20 and ema20 <= ema50:
        res["sell_pts"] += 14
        res["notes_sell"].append("និន្នាការ EMA 20 < EMA 50")
    elif price > ema20:
        res["buy_pts"] += 7
    elif price < ema20:
        res["sell_pts"] += 7

    if price > ema20 and (c1_green or c0_green) and cl0 >= cl2:
        res["buy_pts"] += 7
    elif price < ema20 and (c1_red or c0_red) and cl0 <= cl2:
        res["sell_pts"] += 7

    if bull_engulf:
        res["buy_pts"] += 7
        res["notes_buy"].append("ទៀន Bullish Engulfing")
    elif bear_engulf:
        res["sell_pts"] += 7
        res["notes_sell"].append("ទៀន Bearish Engulfing")

    if price > ema_top and c0_green and (cl0 - o0) >= atr_ref * 0.45 and o0 <= ema_top + atr_ref * 0.35:
        res["buy_pts"] += 12
        res["bull_reversal"] = True
        res["falling_knife"] = False
        res["notes_buy"].insert(0, f"ទៀនបៃតងហក់បែក EMA (${ema_top:.2f})")
    elif price < ema_bot and c0_red and (o0 - cl0) >= atr_ref * 0.45 and o0 >= ema_bot - atr_ref * 0.35:
        res["sell_pts"] += 12
        res["bear_reversal"] = True
        res["rising_rocket"] = False
        res["notes_sell"].insert(0, f"ទៀនក្រហមបែកធ្លាក់ក្រោម EMA (${ema_bot:.2f})")

    if rng1 >= atr_ref * 2.8:
        res["climax"] = f"ទៀនហក់ធំជ្រុល (${rng1:.2f}) — គួរប្រើ Lot តូច"

    return res


def score_fundamentals(
    dxy_r1: float,
    us10y_d5: float,
    vix_val: float | None,
    global_news: dict,
    war_news: dict,
) -> dict:
    buy_f, sell_f = 0, 0
    notes_buy, notes_sell = [], []

    if dxy_r1 <= -0.30:
        buy_f += 7
        notes_buy.append(f"DXY ចុះខ្សោយ ({dxy_r1:+.2f}%)")
    elif dxy_r1 <= -0.10:
        buy_f += 3
    elif dxy_r1 >= 0.30:
        sell_f += 7
        notes_sell.append(f"DXY ឡើងខ្លាំង ({dxy_r1:+.2f}%)")
    elif dxy_r1 >= 0.10:
        sell_f += 3

    if us10y_d5 <= -0.08:
        buy_f += 5
        notes_buy.append("US10Y ធ្លាក់ចុះ")
    elif us10y_d5 >= 0.08:
        sell_f += 5
        notes_sell.append("US10Y ហក់ឡើង")

    if vix_val is not None and vix_val >= 22.0:
        buy_f += 3

    g_bias, g_score = global_news.get("bias", "NEUTRAL"), safe_float(global_news.get("score"), 0.0)
    if g_bias == "BULLISH":
        buy_f += 6
        notes_buy.append(f"ព័ត៌មានមាសគាំទ្រ BUY ({g_score:+.2f})")
    elif g_bias == "BEARISH":
        sell_f += 6
        notes_sell.append(f"ព័ត៌មានមាសគាំទ្រ SELL ({g_score:+.2f})")

    w_level, w_impact = war_news.get("level", "LOW"), war_news.get("gold_impact", "NEUTRAL")
    if w_level == "HIGH" and w_impact == "BULLISH":
        buy_f += 5
        notes_buy.append("សង្គ្រាម HIGH ជំរុញ Safe-Haven")
    elif w_level == "MEDIUM" and w_impact == "BULLISH":
        buy_f += 3
    elif w_level == "COOLING":
        sell_f += 4
        notes_sell.append("សង្គ្រាមធូរស្រាល")

    return {"buy_pts": min(25, buy_f), "sell_pts": min(25, sell_f), "notes_buy": notes_buy, "notes_sell": notes_sell}


def compute_exit_shield(
    price: float,
    ema20: float,
    rsi: float,
    spread: float,
    ms: dict,
    cal_news: dict,
    active_side: str,
) -> dict:
    if cal_news.get("danger"):
        return {"state": "EXIT_NOW", "level": "DANGER", "title_km": "🛑 ឈប់ភ្លាម (ព័ត៌មានក្រហម USD)", "action_km": "បិទ Order ទាំងអស់មុនព័ត៌មានចេញ"}
    if spread > 1.50:
        return {"state": "EXIT_NOW", "level": "DANGER", "title_km": "🛑 ឈប់ភ្លាម (Spread រីកខ្លាំង)", "action_km": f"Spread ${spread:.2f} — ផ្អាកជួញដូរ"}
    if rsi >= 76.0 or (active_side == "BUY" and ms.get("in_bearish_ob")):
        return {"state": "TAKE_PROFIT", "level": "WARN", "title_km": "💰 គួរបិទ BUY / ការពារចំណេញភ្លាម", "action_km": "តម្លៃបុក Bearish OB ឬ RSI ខ្ពស់ — កាត់យកចំណេញ ឬរំកិល SL កៀកតម្លៃ"}
    if rsi <= 24.0 or (active_side == "SELL" and ms.get("in_bullish_ob")):
        return {"state": "TAKE_PROFIT", "level": "WARN", "title_km": "💰 គួរបិទ SELL / ការពារចំណេញភ្លាម", "action_km": "តម្លៃបុក Bullish OB ឬ RSI ទាប — កាត់យកចំណេញ ឬរំកិល SL កៀកតម្លៃ"}
    if active_side == "BUY" and (ms.get("falling_knife") or price < ema20):
        return {"state": "CUT_NOW", "level": "DANGER", "title_km": "🛑 បញ្ឈប់ BUY ភ្លាម (បែកក្រោម EMA 20)", "action_km": "និន្នាការប្តូរចុះ — កាត់ខាតលឿនកុំកាន់បន្ត"}
    if active_side == "SELL" and (ms.get("rising_rocket") or price > ema20):
        return {"state": "CUT_NOW", "level": "DANGER", "title_km": "🛑 បញ្ឈប់ SELL ភ្លាម (បែកលើ EMA 20)", "action_km": "និន្នាការប្តូរឡើង — កាត់ខាតលឿនកុំកាន់បន្ត"}
    if active_side in ("BUY", "SELL"):
        return {"state": "HOLD_SAFE", "level": "SAFE", "title_km": f"🛡️ កាន់ {active_side} បាន (សុវត្ថិភាព)", "action_km": "ដាក់ SL ដាច់ខាត • ចំណេញ 50% រំកិល BE ភ្លាម"}
    return {"state": "STANDBY", "level": "INFO", "title_km": "🛡️ ប្រព័ន្ធការពារសកម្ម", "action_km": "រង់ចាំចំណុចចូលច្បាស់ (មិនដេញតម្លៃ)"}


def build_beginner_guide(
    price: float,
    interval_key: str,
    market_state: dict,
    smc: dict,
    candles: list,
    active_side: str,
    can_trade: bool,
    plan: dict | None,
    preview_plan: dict | None,
    buy_trig: float,
    sell_trig: float,
    global_news: dict,
    war_news: dict,
    macro: dict,
) -> dict:
    recent = candles[-20:] if candles else []
    rng_hi = max((safe_float(c.get("high"), price) for c in recent), default=price + 3.0)
    rng_lo = min((safe_float(c.get("low"), price) for c in recent), default=price - 3.0)
    tf_km = {"1m": "១នាទី", "5m": "៥នាទី", "15m": "១៥នាទី", "1h": "១ម៉ោង", "4h": "៤ម៉ោង", "1d": "១ថ្ងៃ"}.get(interval_key, interval_key)

    ref_plan = plan or preview_plan
    bias_side = active_side if active_side in ("BUY", "SELL") else ("BUY" if (ref_plan and ref_plan["tp"] > ref_plan["entry"]) else "SELL")

    tcode = market_state.get("trend_code", "SIDEWAY")
    if tcode == "SIDEWAY" or (not can_trade and abs(rng_hi - rng_lo) <= 8.0):
        row1 = {"icon": "range", "prefix": "ផ្សារកំពុង Sideway", "hl": f"{rng_lo:,.2f} – {rng_hi:,.2f}", "suffix": "កុំចូលនៅកណ្តាល"}
    elif "UP" in tcode or "BULL" in tcode:
        row1 = {"icon": "up", "prefix": "ផ្សារកំពុងឡើង (Uptrend)", "hl": f"{rng_lo:,.2f} → {rng_hi:,.2f}", "suffix": "ផ្តោតរកឱកាស BUY"}
    else:
        row1 = {"icon": "down", "prefix": "ផ្សារកំពុងចុះ (Downtrend)", "hl": f"{rng_hi:,.2f} → {rng_lo:,.2f}", "suffix": "ផ្តោតរកឱកាស SELL"}

    if ref_plan:
        ent = ref_plan["entry"]
        if bias_side == "BUY":
            row2 = {"icon": "target_buy", "prefix": "ចូលទិញ (BUY) នៅ" if (can_trade and ref_plan.get("entry_state") == "IN_ZONE") else "ចាំទិញ (BUY) នៅ", "hl": f"${ent:,.2f}", "suffix": "កុំដេញទិញនៅកំពូល"}
        else:
            row2 = {"icon": "target_sell", "prefix": "ចូលលក់ (SELL) នៅ" if (can_trade and ref_plan.get("entry_state") == "IN_ZONE") else "ចាំលក់ (SELL) នៅ", "hl": f"${ent:,.2f}", "suffix": "កុំដេញលក់នៅបាត"}
    else:
        row2 = {"icon": "target_buy", "prefix": "ចាំបែកលើ", "hl": f"${buy_trig:,.2f}", "suffix": f"ដើម្បី BUY ឬក្រោម ${sell_trig:,.2f} SELL"}

    bull_obs = [b for b in (smc.get("demand_blocks") or []) if str(b.get("label", "OB")).upper() == "OB"] or (smc.get("demand_blocks") or [])
    bear_obs = [b for b in (smc.get("supply_blocks") or []) if str(b.get("label", "OB")).upper() == "OB"] or (smc.get("supply_blocks") or [])
    if bias_side == "SELL":
        if bull_obs:
            b_lo, b_hi = safe_float(bull_obs[0].get("bottom"), rng_lo), safe_float(bull_obs[0].get("top"), rng_lo + 2.0)
            row3 = {"icon": "avoid", "prefix": "កុំលក់នៅ", "hl": f"{b_lo:,.2f} – {b_hi:,.2f}", "suffix": "អ្នកទិញច្រើន (Bullish OB)"}
        else:
            row3 = {"icon": "avoid", "prefix": "កុំលក់នៅ", "hl": f"{rng_lo:,.2f} – {rng_lo + 2.5:,.2f}", "suffix": "តំបន់ទ្រក្រោម (Support)"}
    else:
        if bear_obs:
            s_lo, s_hi = safe_float(bear_obs[0].get("bottom"), rng_hi - 2.0), safe_float(bear_obs[0].get("top"), rng_hi)
            row3 = {"icon": "avoid", "prefix": "កុំទិញនៅ", "hl": f"{s_lo:,.2f} – {s_hi:,.2f}", "suffix": "អ្នកលក់ច្រើន (Bearish OB)"}
        else:
            row3 = {"icon": "avoid", "prefix": "កុំទិញនៅ", "hl": f"{rng_hi - 2.5:,.2f} – {rng_hi:,.2f}", "suffix": "តំបន់ទប់លើ (Resistance)"}

    stop_px = ref_plan["sl"] if ref_plan else (sell_trig if bias_side == "BUY" else buy_trig)
    if bias_side == "BUY":
        row4 = {"icon": "stop", "prefix": f"ឈប់ទិញ បើទៀន {tf_km} បិទក្រោម", "hl": f"${stop_px:,.2f}", "suffix": ""}
    else:
        row4 = {"icon": "stop", "prefix": f"ឈប់លក់ បើទៀន {tf_km} បិទលើ", "hl": f"${stop_px:,.2f}", "suffix": ""}

    w_lvl = war_news.get("level", "LOW")
    w_km = "តានតឹងខ្លាំង (ទ្រមាស)" if w_lvl == "HIGH" else ("មធ្យម" if w_lvl == "MEDIUM" else ("ធូរស្រាល" if w_lvl == "COOLING" else "ស្ងប់ស្ងាត់"))
    g_bias = global_news.get("bias", "NEUTRAL")
    g_km = "គាំទ្រឡើង" if g_bias == "BULLISH" else ("សម្ពាធចុះ" if g_bias == "BEARISH" else "លំនឹង")

    return {
        "rows": [row1, row2, row3, row4],
        "war_km": w_km, "war_level": w_lvl,
        "econ_km": g_km, "econ_bias": g_bias,
        "dxy": f"{macro.get('dxy')}" if macro.get("dxy") is not None else "--",
        "us10y": f"{macro.get('us10y')}%" if macro.get("us10y") is not None else "--",
        "footer_km": f"ទៀនបិទ = ទៀនចប់ {tf_km} មិនមែនគ្រាន់តែប៉ះ • គណនាផ្ទាល់ពីទីផ្សារពិត",
    }


def compute_true_triggers(price: float, spread: float, ema20: float, ema50: float, candles: list, smc: dict) -> tuple[float, float]:
    min_gap = max(spread * 1.2, 0.35)
    recent = candles[-12:] if candles else []
    swing_hi = max((safe_float(c.get("high"), price) for c in recent), default=price + 1.5)
    swing_lo = min((safe_float(c.get("low"), price) for c in recent), default=price - 1.5)

    up_cands = [x for x in (ema20, ema50, swing_hi) if x > price + min_gap]
    for sob in (smc.get("supply_blocks") or []):
        t = safe_float(sob.get("top"), 0.0)
        if t > price + min_gap:
            up_cands.append(t)
    buy_trig = round(min(up_cands) if up_cands else (price + max(min_gap, 1.0)), 2)

    dn_cands = [x for x in (ema20, ema50, swing_lo) if 0 < x < price - min_gap]
    for ob in (smc.get("demand_blocks") or []):
        b = safe_float(ob.get("bottom"), 0.0)
        if 0 < b < price - min_gap:
            dn_cands.append(b)
    sell_trig = round(max(dn_cands) if dn_cands else (price - max(min_gap, 1.0)), 2)
    return buy_trig, sell_trig


def describe_market(r0, payload: dict, global_news: dict, war_news: dict, session_kz: dict) -> dict:
    price = safe_float(payload.get("price"), 0.0)
    ema20, ema50 = safe_float(payload.get("ema20"), price), safe_float(payload.get("ema50"), price)
    rsi, atr = safe_float(payload.get("rsi"), 50.0), safe_float(payload.get("atr"), 2.5)
    smc_bias = (payload.get("smc") or {}).get("bias", "NEUTRAL")
    adx = safe_float(r0.get("adx_14"), 20.0) if (r0 is not None and pd.notna(r0.get("adx_14"))) else 20.0

    if price > ema20 and ema20 > ema50:
        trend_code, trend_km = "UPTREND", ("ទីផ្សារឡើងខ្លាំង (Strong Uptrend)" if adx >= 25 else "ទីផ្សារកំពុងឡើង (Uptrend)")
    elif price < ema20 and ema20 < ema50:
        trend_code, trend_km = "DOWNTREND", ("ទីផ្សារចុះខ្លាំង (Strong Downtrend)" if adx >= 25 else "ទីផ្សារកំពុងចុះ (Downtrend)")
    elif price < ema20:
        trend_code, trend_km = "BEARISH_PRESSURE", "សម្ពាធលក់ក្រោម EMA 20"
    elif price > ema20:
        trend_code, trend_km = "BULLISH_PRESSURE", "សម្ពាធទិញលើ EMA 20"
    else:
        trend_code, trend_km = "SIDEWAY", "ទីផ្សាររត់ទទឹង (Sideway)"

    vol_km = "រត់លឿនខ្លាំង" if atr >= 5.5 else ("ល្បឿនល្អ" if atr >= 1.8 else "ស្ងប់ស្ងាត់")
    war_tag = f" • សង្គ្រាម: {war_news.get('level', 'LOW')}" if war_news.get("level") in ("HIGH", "MEDIUM") else ""
    return {
        "trend_code": trend_code, "trend_km": trend_km, "volatility_km": vol_km,
        "summary_km": f"{trend_km} • SMC {smc_bias} • {vol_km} ({session_kz['name_km']}){war_tag}",
        "adx": round(adx, 1), "rsi": round(rsi, 1), "smc_bias": smc_bias,
        "news_km": global_news.get("summary_km", ""),
    }


def build_precision_plan(
    side: str,
    price: float,
    payload: dict,
    atr_chart: float,
    spread: float,
    inst: dict,
    grade: str,
    is_scalp: bool = False,
) -> dict | None:
    if side not in ("BUY", "SELL") or price <= 0:
        return None

    candles = payload.get("candles") or []
    ema20 = safe_float(payload.get("ema20"), price)
    smc = payload.get("smc") or {}
    atr_val = max(safe_float(atr_chart, 2.0), 0.6)
    spd = max(safe_float(spread, 0.30), 0.15)

    recent = candles[-(12 if is_scalp else 18):]
    swing_low = min((safe_float(c.get("low"), price) for c in recent), default=price - atr_val * 1.4)
    swing_high = max((safe_float(c.get("high"), price) for c in recent), default=price + atr_val * 1.4)

    stretch = (price - ema20) if side == "BUY" else (ema20 - price)
    if stretch <= atr_val * 0.95:
        entry_state, entry_km, entry_p = "IN_ZONE", "ចូលបានឥឡូវ", round(price, 2)
    elif stretch <= atr_val * 1.80:
        entry_state, entry_km = "PULLBACK", "ដាក់ Limit នៅ EMA"
        entry_p = round(ema20 + atr_val * 0.25, 2) if side == "BUY" else round(ema20 - atr_val * 0.25, 2)
    else:
        entry_state, entry_km = "EXTENDED", "ហួសចំណុច (ដាក់ Limit)"
        entry_p = round(ema20 + atr_val * 0.20, 2) if side == "BUY" else round(ema20 - atr_val * 0.20, 2)

    min_sl = max(atr_val * (0.90 if is_scalp else 1.05), spd * 3.5, 2.00)
    max_sl = max(min_sl * 1.85, atr_val * (1.75 if is_scalp else 2.20))
    buf = max(atr_val * 0.25, spd * 0.8)
    tp1_mult, tp2_mult = (1.65, 2.60) if is_scalp else (2.0, 3.2)

    if side == "BUY":
        anchor_low = swing_low
        for ob in (smc.get("demand_blocks") or []):
            ob_bot = safe_float(ob.get("bottom"), 0.0)
            if 0 < ob_bot < entry_p and (entry_p - ob_bot) <= max_sl * 1.1:
                anchor_low = min(anchor_low, ob_bot)
                break
        sl_dist = max(min_sl, min(max_sl, entry_p - (anchor_low - buf)))
        sl_p = round(entry_p - sl_dist, 2)
        tp1_p = round(entry_p + sl_dist * tp1_mult, 2)
        tp2_p = round(entry_p + sl_dist * tp2_mult, 2)
        for sob in (smc.get("supply_blocks") or []):
            sob_bot = safe_float(sob.get("bottom"), 0.0)
            if entry_p + sl_dist * 1.25 < sob_bot < tp1_p:
                tp1_p = round(sob_bot - 0.35, 2)
                break
        pdh = safe_float(inst.get("pdh"), 0.0)
        if pdh > 0 and entry_p + sl_dist * 1.25 < pdh < tp1_p:
            tp1_p = round(pdh - 0.35, 2)
        if tp1_p <= entry_p + 0.5:
            tp1_p = round(entry_p + sl_dist * 1.6, 2)
        if tp2_p <= tp1_p + 0.6:
            tp2_p = round(tp1_p + sl_dist, 2)
    else:
        anchor_high = swing_high
        for sob in (smc.get("supply_blocks") or []):
            sob_top = safe_float(sob.get("top"), 0.0)
            if sob_top > entry_p and (sob_top - entry_p) <= max_sl * 1.1:
                anchor_high = max(anchor_high, sob_top)
                break
        sl_dist = max(min_sl, min(max_sl, (anchor_high + buf) - entry_p))
        sl_p = round(entry_p + sl_dist, 2)
        tp1_p = round(entry_p - sl_dist * tp1_mult, 2)
        tp2_p = round(entry_p - sl_dist * tp2_mult, 2)
        for ob in (smc.get("demand_blocks") or []):
            ob_top = safe_float(ob.get("top"), 0.0)
            if tp1_p < ob_top < entry_p - sl_dist * 1.25:
                tp1_p = round(ob_top + 0.35, 2)
                break
        pdl = safe_float(inst.get("pdl"), 0.0)
        if pdl > 0 and tp1_p < pdl < entry_p - sl_dist * 1.25:
            tp1_p = round(pdl + 0.35, 2)
        if tp1_p >= entry_p - 0.5:
            tp1_p = round(entry_p - sl_dist * 1.6, 2)
        if tp2_p >= tp1_p - 0.6:
            tp2_p = round(tp1_p - sl_dist, 2)

    be_trigger = round((entry_p + tp1_p) / 2.0, 2)
    rr1_val = round(abs(tp1_p - entry_p) / max(sl_dist, 0.1), 2)
    rr2_val = round(abs(tp2_p - entry_p) / max(sl_dist, 0.1), 2)
    return {
        "entry": entry_p, "sl": sl_p, "be_trigger": be_trigger,
        "tp": tp1_p, "tp1": tp1_p, "tp2": tp2_p,
        "sl_dist": round(sl_dist, 2), "sl_pips": int(round(sl_dist * 10)),
        "tp_pips": int(round(abs(tp1_p - entry_p) * 10)),
        "tp2_pips": int(round(abs(tp2_p - entry_p) * 10)),
        "rr_num": rr1_val, "rr": f"{rr1_val:.1f} : 1", "rr2": f"{rr2_val:.1f} : 1",
        "entry_state": entry_state, "entry_km": entry_km,
        "exit_km": f"ដល់ ${be_trigger:.2f} រំកិល BE • កាត់ 50% នៅ ${tp1_p:.2f}",
        "suggested_risk_pct": 1.0 if grade == "A+" else (0.75 if grade in ("A", "B+") else 0.5),
    }