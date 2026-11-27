from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import mtf_executor, safe_float
from .cache import cache
from .feeds import fetch_gold_data


# ==================== WILDER SMOOTHING HELPERS (MT5 / TRADINGVIEW STANDARD) ====================
def _wilder_rsi(close_s: pd.Series, period: int = 14) -> pd.Series:
    delta = close_s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss > 0.0, np.where(avg_gain > 0.0, 100.0, 50.0))
    return rsi.fillna(50.0).clip(0.0, 100.0)


def _wilder_atr(high_s: pd.Series, low_s: pd.Series, close_s: pd.Series, period: int = 14) -> pd.Series:
    prev_c = close_s.shift(1)
    tr = pd.concat([
        (high_s - low_s).abs(),
        (high_s - prev_c).abs(),
        (low_s - prev_c).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=1, adjust=False).mean()
    return atr.bfill().fillna(2.0).clip(lower=0.1)


# ==================== SMART MONEY CONCEPTS (SMC) STRUCTURE ENGINE ====================
def _dedupe_blocks(blocks: list[dict], min_gap: float, max_items: int) -> list[dict]:
    selected: list[dict] = []
    for b in blocks:
        mid = (b["top"] + b["bottom"]) / 2.0
        if all(abs(mid - ((s["top"] + s["bottom"]) / 2.0)) >= min_gap for s in selected):
            selected.append(b)
            if len(selected) >= max_items:
                break
    return selected


def compute_smc_structure(df: pd.DataFrame) -> dict:
    n = len(df)
    empty = {"demand_blocks": [], "supply_blocks": [], "levels": [], "breaks": [], "bias": "NEUTRAL"}
    if n < 25:
        return empty

    open_p = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = np.nan_to_num(df["atr"].to_numpy(dtype=float), nan=1.5, posinf=1.5, neginf=1.5)
    timestamps = [int(pd.Timestamp(t).timestamp()) for t in df["datetime"]]
    cur_price = float(close[-1])
    last_atr = max(float(atr[-1]), 1.0)

    w = 3
    last_sh_idx, last_sh_val = 0, float(high[0])
    last_sl_idx, last_sl_val = 0, float(low[0])
    sh_broken, sl_broken = False, False
    trend = 0

    breaks: list[dict] = []
    raw_demand: list[dict] = []
    raw_supply: list[dict] = []

    for i in range(w, n):
        p = i - w
        if p >= w:
            if high[p] == np.max(high[p - w:p + w + 1]):
                last_sh_idx, last_sh_val = p, float(high[p])
                sh_broken = False
            if low[p] == np.min(low[p - w:p + w + 1]):
                last_sl_idx, last_sl_val = p, float(low[p])
                sl_broken = False

        if not sh_broken and close[i] > last_sh_val and (i - last_sh_idx) >= 2:
            sh_broken = True
            is_choch = (trend == -1)
            trend = 1
            breaks.append({
                "start_time": timestamps[last_sh_idx],
                "end_time": timestamps[i],
                "price": safe_float(last_sh_val),
                "type": "CHoCH_UP" if is_choch else "BOS_UP",
                "label": "CHoCH" if is_choch else "BOS",
            })
            seg_start = max(0, last_sl_idx - 2)
            ob_idx = last_sl_idx
            for k in range(i - 1, seg_start - 1, -1):
                if close[k] < open_p[k]:
                    ob_idx = k
                    break
            ob_bot = safe_float(low[ob_idx])
            ob_top = safe_float(max(open_p[ob_idx], close[ob_idx], ob_bot + last_atr * 0.45))
            if ob_top - ob_bot > last_atr * 1.4:
                ob_top = safe_float(ob_bot + last_atr * 1.1)
            raw_demand.append({
                "idx": ob_idx, "start_time": timestamps[ob_idx],
                "top": ob_top, "bottom": ob_bot, "label": "OB",
            })

        if not sl_broken and close[i] < last_sl_val and (i - last_sl_idx) >= 2:
            sl_broken = True
            is_choch = (trend == 1)
            trend = -1
            breaks.append({
                "start_time": timestamps[last_sl_idx],
                "end_time": timestamps[i],
                "price": safe_float(last_sl_val),
                "type": "CHoCH_DOWN" if is_choch else "BOS_DOWN",
                "label": "CHoCH" if is_choch else "BOS",
            })
            seg_start = max(0, last_sh_idx - 2)
            ob_idx = last_sh_idx
            for k in range(i - 1, seg_start - 1, -1):
                if close[k] > open_p[k]:
                    ob_idx = k
                    break
            ob_top = safe_float(high[ob_idx])
            ob_bot = safe_float(min(open_p[ob_idx], close[ob_idx], ob_top - last_atr * 0.45))
            if ob_top - ob_bot > last_atr * 1.4:
                ob_bot = safe_float(ob_top - last_atr * 1.1)
            raw_supply.append({
                "idx": ob_idx, "start_time": timestamps[ob_idx],
                "top": ob_top, "bottom": ob_bot, "label": "OB",
            })

        if i >= 2:
            gap_up = low[i] - high[i - 2]
            if gap_up >= last_atr * 0.35:
                raw_demand.append({
                    "idx": i - 1, "start_time": timestamps[i - 1],
                    "top": safe_float(low[i]), "bottom": safe_float(high[i - 2]), "label": "FVG",
                })
            gap_dn = low[i - 2] - high[i]
            if gap_dn >= last_atr * 0.35:
                raw_supply.append({
                    "idx": i - 1, "start_time": timestamps[i - 1],
                    "top": safe_float(low[i - 2]), "bottom": safe_float(high[i]), "label": "FVG",
                })

    active_demand = []
    for b in reversed(raw_demand):
        idx = b["idx"]
        if b["bottom"] <= cur_price + last_atr * 0.4:
            after_closes = close[idx + 1:]
            if len(after_closes) == 0 or float(np.min(after_closes)) >= b["bottom"] - last_atr * 0.15:
                active_demand.append({
                    "start_time": b["start_time"], "top": b["top"],
                    "bottom": b["bottom"], "label": b["label"],
                })

    active_supply = []
    for b in reversed(raw_supply):
        idx = b["idx"]
        if b["top"] >= cur_price - last_atr * 0.4:
            after_closes = close[idx + 1:]
            if len(after_closes) == 0 or float(np.max(after_closes)) <= b["top"] + last_atr * 0.15:
                active_supply.append({
                    "start_time": b["start_time"], "top": b["top"],
                    "bottom": b["bottom"], "label": b["label"],
                })

    demand_blocks = _dedupe_blocks(active_demand, min_gap=last_atr * 1.1, max_items=4)
    supply_blocks = _dedupe_blocks(active_supply, min_gap=last_atr * 1.1, max_items=3)

    look = min(100, n)
    offset = n - look
    hi_idx = offset + int(np.argmax(high[offset:]))
    lo_idx = offset + int(np.argmin(low[offset:]))

    if trend >= 0:
        high_label, low_label = "Weak High", "Strong Low"
        bias = "BULLISH" if trend == 1 else "NEUTRAL"
    else:
        high_label, low_label = "Strong High", "Weak Low"
        bias = "BEARISH"

    levels = [
        {"price": safe_float(high[hi_idx]), "time": timestamps[hi_idx], "label": high_label, "kind": "high"},
        {"price": safe_float(low[lo_idx]), "time": timestamps[lo_idx], "label": low_label, "kind": "low"},
    ]

    return {
        "demand_blocks": demand_blocks,
        "supply_blocks": supply_blocks,
        "levels": levels,
        "breaks": breaks[-12:],
        "bias": bias,
    }


# ==================== INSTITUTIONAL QUANT ENGINE (VWAP, POC, CVD, SQUEEZE) ====================
def compute_institutional_quant(df_plot: pd.DataFrame) -> tuple[dict, list[dict]]:
    n = len(df_plot)
    empty = {
        "vwap": None, "vwap_bias": "NEUTRAL", "vwap_dist": 0.0,
        "poc": None, "vah": None, "val": None, "vp_zone": "IN_VA",
        "cvd_bias": "NEUTRAL", "cvd_divergence": "NONE", "cvd_pct": 50,
        "squeeze_state": "NORMAL", "profile": [],
    }
    if n < 20:
        return empty, []

    high = df_plot["high"].to_numpy(dtype=float)
    low = df_plot["low"].to_numpy(dtype=float)
    close = df_plot["close"].to_numpy(dtype=float)
    vol = np.nan_to_num(df_plot["volume"].to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    ema20 = np.nan_to_num(df_plot["ema20"].to_numpy(dtype=float), nan=close)
    atr = np.nan_to_num(df_plot["atr"].to_numpy(dtype=float), nan=1.5, posinf=1.5, neginf=1.5)
    bb_up = np.nan_to_num(df_plot["bb_upper"].to_numpy(dtype=float), nan=close)
    bb_lo = np.nan_to_num(df_plot["bb_lower"].to_numpy(dtype=float), nan=close)
    dts = pd.to_datetime(df_plot["datetime"], utc=True)
    timestamps = [int(pd.Timestamp(t).timestamp()) for t in dts]

    # ប្រើ Real Volume ជាអាទិភាព; បើ Feed គ្មាន Volume ប្រើ Tick-Range Activity Proxy
    rng = np.maximum(high - low, 0.05)
    eff_vol = np.where(vol > 0.0, vol, rng * 10.0)
    typical = (high + low + close) / 3.0

    # 1) Session VWAP (Reset រៀងរាល់ថ្ងៃថ្មី UTC 00:00)
    dates = dts.dt.date.to_numpy()
    vwap_arr = np.empty(n, dtype=float)
    cum_pv, cum_v = 0.0, 0.0
    for i in range(n):
        if i == 0 or dates[i] != dates[i - 1]:
            cum_pv, cum_v = 0.0, 0.0
        cum_pv += typical[i] * eff_vol[i]
        cum_v += eff_vol[i]
        vwap_arr[i] = (cum_pv / cum_v) if cum_v > 0.0 else typical[i]

    cur_px = float(close[-1])
    cur_vwap = safe_float(vwap_arr[-1], cur_px)
    vwap_dist = safe_float(cur_px - cur_vwap)
    vwap_bias = "BUY" if vwap_dist > 0.25 else ("SELL" if vwap_dist < -0.25 else "NEUTRAL")

    vwap_line: list[dict] = []
    last_ts = -1
    for i in range(n):
        ts = timestamps[i]
        if ts <= last_ts:
            continue
        last_ts = ts
        vwap_line.append({"time": ts, "value": safe_float(vwap_arr[i], cur_px)})

    # 2) Volume Profile (24 Price Bins -> POC, VAH, VAL)
    p_min, p_max = float(np.min(low)), float(np.max(high))
    if p_max - p_min < 0.2:
        p_max = p_min + 1.0
    n_bins = 24
    edges = np.linspace(p_min, p_max, n_bins + 1)
    bin_idx = np.clip(np.digitize(typical, edges) - 1, 0, n_bins - 1)
    bin_vols = np.zeros(n_bins, dtype=float)
    np.add.at(bin_vols, bin_idx, eff_vol)

    poc_i = int(np.argmax(bin_vols))
    poc = safe_float((edges[poc_i] + edges[poc_i + 1]) / 2.0, cur_px)
    tot_v = float(np.sum(bin_vols))
    va_target = tot_v * 0.70
    lo_i, hi_i, acc_v = poc_i, poc_i, float(bin_vols[poc_i])
    while acc_v < va_target and (lo_i > 0 or hi_i < n_bins - 1):
        v_up = bin_vols[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        v_dn = bin_vols[lo_i - 1] if lo_i > 0 else -1.0
        if v_up >= v_dn and hi_i < n_bins - 1:
            hi_i += 1
            acc_v += float(bin_vols[hi_i])
        elif lo_i > 0:
            lo_i -= 1
            acc_v += float(bin_vols[lo_i])
        else:
            break

    vah = safe_float(edges[hi_i + 1], cur_px)
    val = safe_float(edges[lo_i], cur_px)
    vp_zone = "ABOVE_VA" if cur_px > vah else ("BELOW_VA" if cur_px < val else "IN_VA")

    max_bv = max(float(np.max(bin_vols)), 1e-6)
    profile = [
        {
            "p": safe_float((edges[i] + edges[i + 1]) / 2.0),
            "pct": round(float(bin_vols[i] / max_bv), 3),
            "is_poc": i == poc_i,
        }
        for i in range(n_bins)
        if bin_vols[i] > 0.0
    ]

    # 3) CVD (Cumulative Volume Delta) & Hidden Institutional Divergence
    buy_share = np.clip((close - low) / rng, 0.0, 1.0)
    bar_delta = eff_vol * (2.0 * buy_share - 1.0)
    cvd = np.cumsum(bar_delta)
    recent_b = float(np.sum(eff_vol[-12:] * buy_share[-12:]))
    recent_t = max(float(np.sum(eff_vol[-12:])), 1e-6)
    cvd_pct = int(round((recent_b / recent_t) * 100.0))
    cvd_bias = "BUY" if cvd_pct >= 56 else ("SELL" if cvd_pct <= 44 else "NEUTRAL")

    look = min(12, n - 1)
    px_diff = float(close[-1] - close[-1 - look])
    cvd_diff = float(cvd[-1] - cvd[-1 - look])
    atr_thr = max(float(atr[-1]) * 0.4, 0.35)
    cvd_div = "NONE"
    if px_diff < -atr_thr and cvd_diff > 0.0:
        cvd_div = "BULL_DIV"   # តម្លៃចុះ តែលុយធំលួចប្រមូលទិញ
    elif px_diff > atr_thr and cvd_diff < 0.0:
        cvd_div = "BEAR_DIV"   # តម្លៃឡើង តែលុយធំលួចបញ្ចេញលក់

    # 4) Volatility Squeeze (Bollinger Bands នៅក្នុង Keltner Channel)
    kc_up = ema20 + 1.5 * atr
    kc_lo = ema20 - 1.5 * atr
    sq_now = bool(bb_up[-1] < kc_up[-1] and bb_lo[-1] > kc_lo[-1])
    sq_prev = bool(bb_up[-2] < kc_up[-2] and bb_lo[-2] > kc_lo[-2])
    squeeze_state = "COILING" if sq_now else ("FIRING" if sq_prev else "NORMAL")

    return {
        "vwap": cur_vwap, "vwap_bias": vwap_bias, "vwap_dist": vwap_dist,
        "poc": poc, "vah": vah, "val": val, "vp_zone": vp_zone,
        "cvd_bias": cvd_bias, "cvd_divergence": cvd_div, "cvd_pct": cvd_pct,
        "squeeze_state": squeeze_state, "profile": profile,
    }, vwap_line


# ==================== TREND-FILTERED BUY/SELL + DEMAND ZONE ENGINE ====================
def generate_chart_signals(df: pd.DataFrame) -> tuple[list[dict], dict | None, dict]:
    n = len(df)
    if n < 20:
        return [], None, {"demand": None, "supply": None}

    open_p = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ema20 = np.nan_to_num(df["ema20"].to_numpy(dtype=float), nan=close)
    ema50 = np.nan_to_num(df["ema50"].to_numpy(dtype=float), nan=close)
    atr = np.nan_to_num(df["atr"].to_numpy(dtype=float), nan=1.0, posinf=1.0, neginf=1.0)
    rsi = np.nan_to_num(df["rsi"].to_numpy(dtype=float), nan=50.0, posinf=50.0, neginf=50.0)
    timestamps = [int(pd.Timestamp(t).timestamp()) for t in df["datetime"]]

    mult = 1.65
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + mult * atr
    lower_basic = hl2 - mult * atr

    final_upper = np.copy(upper_basic)
    final_lower = np.copy(lower_basic)
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        final_upper[i] = upper_basic[i] if (upper_basic[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = lower_basic[i] if (lower_basic[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]) else final_lower[i - 1]

        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if close[i] > final_upper[i] else -1

    signals: list[dict] = []
    active_trade = None
    last_sig_idx = -10
    last_sig_side = None

    for i in range(14, n):
        ts = timestamps[i]
        cur_atr = max(float(atr[i]), 0.8)
        bar_range = max(high[i] - low[i], 0.01)
        has_momentum = bar_range >= cur_atr * 0.45

        is_st_buy = (direction[i] == 1 and direction[i - 1] == -1 and close[i] > ema20[i] and has_momentum)
        is_rev_buy = (
            i - last_sig_idx >= 6
            and last_sig_side != "BUY"
            and has_momentum
            and rsi[i] < 34
            and low[i] <= np.min(low[max(0, i - 10):i + 1]) * 1.0003
            and close[i] > open_p[i]
            and (close[i] - low[i]) > bar_range * 0.60
            and close[i] >= ema50[i] - cur_atr * 1.5
        )

        is_st_sell = (direction[i] == -1 and direction[i - 1] == 1 and close[i] < ema20[i] and has_momentum)
        is_rev_sell = (
            i - last_sig_idx >= 6
            and last_sig_side != "SELL"
            and has_momentum
            and rsi[i] > 66
            and high[i] >= np.max(high[max(0, i - 10):i + 1]) * 0.9997
            and close[i] < open_p[i]
            and (high[i] - close[i]) > bar_range * 0.60
            and close[i] <= ema50[i] + cur_atr * 1.5
        )

        if (is_st_buy or is_rev_buy) and (i - last_sig_idx >= 4):
            entry_p = safe_float(close[i])
            sl_p = safe_float(min(low[i] - cur_atr * 0.7, entry_p - max(cur_atr * 1.3, 1.8)))
            risk = max(entry_p - sl_p, 1.2)
            tp1_p = safe_float(entry_p + risk * 2.0)
            tp2_p = safe_float(entry_p + risk * 3.0)
            signals.append({
                "time": ts, "price": safe_float(low[i]), "type": "BUY",
                "label": "BUY", "sublabel": "INV BUY" if is_rev_buy and not is_st_buy else "",
            })
            active_trade = {
                "side": "BUY", "time": ts, "entry": entry_p, "sl": sl_p,
                "tp1": tp1_p, "tp2": tp2_p, "rr": "2 : 1", "tp1_hit": False, "sl_hit": False,
            }
            last_sig_idx = i
            last_sig_side = "BUY"
            continue

        if (is_st_sell or is_rev_sell) and (i - last_sig_idx >= 4):
            entry_p = safe_float(close[i])
            sl_p = safe_float(max(high[i] + cur_atr * 0.7, entry_p + max(cur_atr * 1.3, 1.8)))
            risk = max(sl_p - entry_p, 1.2)
            tp1_p = safe_float(entry_p - risk * 2.0)
            tp2_p = safe_float(entry_p - risk * 3.0)
            signals.append({
                "time": ts, "price": safe_float(high[i]), "type": "SELL",
                "label": "SELL", "sublabel": "INV SELL" if is_rev_sell and not is_st_sell else "",
            })
            active_trade = {
                "side": "SELL", "time": ts, "entry": entry_p, "sl": sl_p,
                "tp1": tp1_p, "tp2": tp2_p, "rr": "2 : 1", "tp1_hit": False, "sl_hit": False,
            }
            last_sig_idx = i
            last_sig_side = "SELL"
            continue

        # Worst-case check: SL មានអាទិភាពមុន TP1
        if active_trade and not active_trade["tp1_hit"] and not active_trade["sl_hit"]:
            if active_trade["side"] == "BUY":
                if low[i] <= active_trade["sl"]:
                    active_trade["sl_hit"] = True
                    signals.append({"time": ts, "price": safe_float(low[i]), "type": "SL", "label": "SL", "sublabel": ""})
                    continue
                if high[i] >= active_trade["tp1"]:
                    active_trade["tp1_hit"] = True
                    signals.append({"time": ts, "price": safe_float(high[i]), "type": "TP1", "label": "TP1", "sublabel": ""})
                    continue
            else:
                if high[i] >= active_trade["sl"]:
                    active_trade["sl_hit"] = True
                    signals.append({"time": ts, "price": safe_float(high[i]), "type": "SL", "label": "SL", "sublabel": ""})
                    continue
                if low[i] <= active_trade["tp1"]:
                    active_trade["tp1_hit"] = True
                    signals.append({"time": ts, "price": safe_float(low[i]), "type": "TP1", "label": "TP1", "sublabel": ""})
                    continue

    lookback = min(45, n)
    rec_low, rec_high = low[-lookback:], high[-lookback:]
    rec_open, rec_close = open_p[-lookback:], close[-lookback:]
    last_atr = max(float(atr[-1]), 1.0)

    low_idx = int(np.argmin(rec_low))
    demand_bottom = safe_float(rec_low[low_idx])
    demand_top = safe_float(max(rec_open[low_idx], rec_close[low_idx], demand_bottom + last_atr * 0.9))

    high_idx = int(np.argmax(rec_high))
    supply_top = safe_float(rec_high[high_idx])
    supply_bottom = safe_float(min(rec_open[high_idx], rec_close[high_idx], supply_top - last_atr * 0.9))

    zones = {
        "demand": {"top": demand_top, "bottom": demand_bottom},
        "supply": {"top": supply_top, "bottom": supply_bottom},
    }
    return signals[-20:], active_trade, zones


# ==================== SESSIONS, MTF CONFLUENCE & INSTITUTIONAL LEVELS ====================
def get_market_session() -> dict:
    hour = datetime.now(timezone.utc).hour
    if 12 <= hour < 16:
        return {"name": "London + NY Overlap", "liquidity": "ខ្ពស់បំផុត (High Volatility)", "note": "ទីផ្សាររត់លឿនខ្លាំង ត្រូវប្រើ Stop Loss ជានិច្ច"}
    if 7 <= hour < 12:
        return {"name": "London Session", "liquidity": "ខ្ពស់ (Active)", "note": "និន្នាការច្បាស់លាស់ ល្អសម្រាប់ជួញដូរតាម Trend"}
    if 16 <= hour < 21:
        return {"name": "New York Session", "liquidity": "ខ្ពស់ (Active)", "note": "មានឥទ្ធិពលខ្លាំងពីព័ត៌មានសេដ្ឋកិច្ចអាមេរិក (USD)"}
    return {"name": "Asian Session", "liquidity": "មធ្យម-ទាប (Range)", "note": "តម្លៃភាគច្រើនរត់ក្នុងប្រអប់ Asian High/Low"}


def compute_tf_bias(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 25 or "close" not in df.columns:
        return {"bias": "NEUTRAL", "rsi": 50.0}
    c = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(c) < 25:
        return {"bias": "NEUTRAL", "rsi": 50.0}

    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    price = float(c.iloc[-1])
    rsi = safe_float(_wilder_rsi(c, 14).iloc[-1], 50.0, 1)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_hist = safe_float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1], 0.0, 3)

    score = (1 if ema20 > ema50 else -1) + (1 if price > ema20 else -1) + (1 if macd_hist > 0 else -1)
    bias = "BUY" if score >= 2 else ("SELL" if score <= -2 else "NEUTRAL")
    return {"bias": bias, "rsi": rsi}


def _compute_mtf_raw() -> dict:
    tfs = [("15m", "15m"), ("1h", "1H"), ("4h", "4H"), ("1d", "1D")]
    futures = {code: mtf_executor.submit(fetch_gold_data, code) for code, _ in tfs}
    items, buy_cnt, sell_cnt = {}, 0, 0
    for code, label in tfs:
        try:
            df, _, _, _ = futures[code].result(timeout=8)
        except (FuturesTimeout, Exception):
            df = pd.DataFrame()
        res = compute_tf_bias(df)
        items[code] = {"label": label, "bias": res["bias"], "rsi": res["rsi"]}
        buy_cnt += 1 if res["bias"] == "BUY" else 0
        sell_cnt += 1 if res["bias"] == "SELL" else 0

    htf_buy = (items["1h"]["bias"] == "BUY") + (items["4h"]["bias"] == "BUY")
    htf_sell = (items["1h"]["bias"] == "SELL") + (items["4h"]["bias"] == "SELL")
    ltf_bias = items["15m"]["bias"]

    if buy_cnt >= 3:
        verdict, warning, aligned = "STRONG ALIGNMENT BUY", None, "BUY"
    elif sell_cnt >= 3:
        verdict, warning, aligned = "STRONG ALIGNMENT SELL", None, "SELL"
    elif ltf_bias == "BUY" and htf_sell >= 2:
        verdict, aligned = "COUNTER-TREND WARNING", "MIXED"
        warning = "ប្រយ័ត្ន៖ 15m លោត BUY ប៉ុន្តែ 1H និង 4H កំពុងចុះ (Bearish) — កុំប្រញាប់ទិញ"
    elif ltf_bias == "SELL" and htf_buy >= 2:
        verdict, aligned = "COUNTER-TREND WARNING", "MIXED"
        warning = "ប្រយ័ត្ន៖ 15m លោត SELL ប៉ុន្តែ 1H និង 4H កំពុងឡើង (Bullish) — កុំប្រញាប់លក់"
    else:
        verdict, aligned = "MIXED / WAIT", "MIXED"
        warning = "Timeframe ធំ និងតូចមិនទាន់ស្របគ្នាទេ គួររង់ចាំសញ្ញាច្បាស់លាស់សិន"

    return {
        "timeframes": [items["15m"], items["1h"], items["4h"], items["1d"]],
        "verdict": verdict, "aligned": aligned, "warning": warning,
        "buy_count": buy_cnt, "sell_count": sell_cnt,
    }


def get_mtf_confluence() -> dict:
    res = cache.get_or_fetch("mtf", 45.0, _compute_mtf_raw)
    return res or {
        "timeframes": [], "verdict": "SYNC", "aligned": "MIXED",
        "warning": None, "buy_count": 0, "sell_count": 0,
    }


def _compute_inst_levels_raw() -> dict:
    pdh = pdl = asian_high = asian_low = None
    df_d, _, _, _ = fetch_gold_data("1d")
    if df_d is not None and len(df_d) >= 2:
        pdh = safe_float(df_d.iloc[-2]["high"], 0.0) or None
        pdl = safe_float(df_d.iloc[-2]["low"], 0.0) or None

    df_15m, _, _, _ = fetch_gold_data("15m")
    if df_15m is not None and not df_15m.empty and "datetime" in df_15m.columns:
        dts = pd.to_datetime(df_15m["datetime"], utc=True)
        asian_df = df_15m[(dts.dt.hour >= 0) & (dts.dt.hour < 7)]
        if not asian_df.empty:
            asian_dts = pd.to_datetime(asian_df["datetime"], utc=True)
            latest_date = asian_dts.dt.date.max()
            day_asian = asian_df[asian_dts.dt.date == latest_date]
            if not day_asian.empty:
                asian_high = safe_float(day_asian["high"].max(), 0.0) or None
                asian_low = safe_float(day_asian["low"].min(), 0.0) or None
    return {"pdh": pdh, "pdl": pdl, "asian_high": asian_high, "asian_low": asian_low}


def get_institutional_levels(basis: float = 0.0) -> dict:
    raw = cache.get_or_fetch("inst_levels_raw", 60.0, _compute_inst_levels_raw)
    if not raw:
        return {"pdh": None, "pdl": None, "asian_high": None, "asian_low": None}
    return {k: (safe_float(v + basis) if v is not None else None) for k, v in raw.items()}


# ==================== CANDLE PATTERNS & MAIN INDICATOR PIPELINE ====================
def detect_candle_pattern(df: pd.DataFrame) -> dict:
    if len(df) < 3:
        return {"name": "Standard Candle", "bias": "NEUTRAL", "desc": "មិនទាន់មានទម្រង់ទៀនពិសេស"}
    c1, c2 = df.iloc[-2], df.iloc[-1]
    body = abs(float(c2["close"]) - float(c2["open"]))
    rng = max(float(c2["high"]) - float(c2["low"]), 0.01)
    upper_wick = float(c2["high"]) - max(float(c2["open"]), float(c2["close"]))
    lower_wick = min(float(c2["open"]), float(c2["close"])) - float(c2["low"])

    if c1["close"] < c1["open"] and c2["close"] > c2["open"] and c2["close"] > c1["open"] and c2["open"] <= c1["close"]:
        return {"name": "Bullish Engulfing", "bias": "BUY", "desc": "ទៀនទិញលេបទៀនលក់ — កម្លាំងទិញវាយបកខ្លាំង"}
    if c1["close"] > c1["open"] and c2["close"] < c2["open"] and c2["close"] < c1["open"] and c2["open"] >= c1["close"]:
        return {"name": "Bearish Engulfing", "bias": "SELL", "desc": "ទៀនលក់លេបទៀនទិញ — កម្លាំងលក់សង្កត់ខ្លាំង"}
    if lower_wick > body * 2.0 and upper_wick < body * 0.8:
        return {"name": "Bullish Pin Bar", "bias": "BUY", "desc": "ស្រមោលក្រោមវែង — កម្លាំងទ្រតម្លៃពីក្រោមខ្លាំង"}
    if upper_wick > body * 2.0 and lower_wick < body * 0.8:
        return {"name": "Bearish Pin Bar", "bias": "SELL", "desc": "ស្រមោលលើវែង — កម្លាំងលក់ទប់តម្លៃពីលើខ្លាំង"}
    if body / rng < 0.15:
        return {"name": "Doji Indecision", "bias": "NEUTRAL", "desc": "ទីផ្សារកំពុងស្ទាក់ស្ទើរ រង់ចាំទៀនបន្ទាប់បញ្ជាក់ទិសដៅ"}
    return (
        {"name": "Bullish Momentum", "bias": "BUY", "desc": "ទៀនបិទបៃតង — កម្លាំងទិញមានប្រៀបជាង"}
        if c2["close"] > c2["open"]
        else {"name": "Bearish Momentum", "bias": "SELL", "desc": "ទៀនបិទក្រហម — កម្លាំងលក់មានប្រៀបជាង"}
    )


def calculate_indicators(df: pd.DataFrame) -> dict:
    df = df.copy()
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    else:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    df = (
        df.dropna(subset=["datetime", "open", "high", "low", "close"])
        .drop_duplicates(subset=["datetime"], keep="last")
        .sort_values("datetime")
        .reset_index(drop=True)
    )

    if df.empty:
        return {
            "price": 0.0, "change": 0.0, "change_pct": 0.0,
            "rsi": 50.0, "ema20": 0.0, "ema50": 0.0, "macd_hist": 0.0,
            "bb_upper": 0.0, "bb_lower": 0.0, "atr": 2.0,
            "support": 0.0, "resistance": 0.0, "pivot": 0.0,
            "pattern": {"name": "Waiting", "bias": "NEUTRAL", "desc": "កំពុងរង់ចាំទិន្នន័យ"},
            "session": get_market_session(), "signal": "NEUTRAL", "confidence": 50,
            "reasons": ["កំពុងរង់ចាំទិន្នន័យក្រាហ្វិក"], "matrix": [],
            "chart_signals": [], "active_signal": None,
            "zones": {"demand": None, "supply": None},
            "smc": {"demand_blocks": [], "supply_blocks": [], "levels": [], "breaks": [], "bias": "NEUTRAL"},
            "quant": {
                "vwap": None, "vwap_bias": "NEUTRAL", "vwap_dist": 0.0,
                "poc": None, "vah": None, "val": None, "vp_zone": "IN_VA",
                "cvd_bias": "NEUTRAL", "cvd_divergence": "NONE", "cvd_pct": 50,
                "squeeze_state": "NORMAL", "profile": [],
            },
            "setup": {"entry": 0.0, "entry_zone": "--", "sl": 0.0, "sl_dist": 2.5, "tp1": 0.0, "tp2": 0.0, "rr": "2 : 1", "advice": "--"},
            "candles": [], "ema20_line": [], "ema50_line": [], "vwap_line": [],
        }

    # Core Indicators (Wilder RSI & ATR + EMA + MACD + Bollinger Bands)
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["rsi"] = _wilder_rsi(df["close"], 14)

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    bb_mid = df["close"].rolling(window=20, min_periods=1).mean()
    bb_std = df["close"].rolling(window=20, min_periods=1).std().fillna(0.0)
    df["bb_upper"] = bb_mid + 2.0 * bb_std
    df["bb_lower"] = bb_mid - 2.0 * bb_std
    df["atr"] = _wilder_atr(df["high"], df["low"], df["close"], 14)

    df_plot = df.tail(150).reset_index(drop=True)
    chart_signals, active_signal, zones = generate_chart_signals(df_plot)
    smc = compute_smc_structure(df_plot)
    quant, vwap_line = compute_institutional_quant(df_plot)

    latest = df_plot.iloc[-1]
    prev = df_plot.iloc[-2] if len(df_plot) > 1 else latest

    price = safe_float(latest["close"])
    prev_c = safe_float(prev["close"], price)
    change = safe_float(price - prev_c)
    change_pct = safe_float((change / prev_c) * 100.0 if prev_c else 0.0)
    rsi = safe_float(latest["rsi"], 50.0)
    ema20 = safe_float(latest["ema20"], price)
    ema50 = safe_float(latest["ema50"], price)
    macd_hist = safe_float(latest["macd_hist"], 0.0, 3)
    bb_upper = safe_float(latest["bb_upper"], price)
    bb_lower = safe_float(latest["bb_lower"], price)
    atr = max(safe_float(latest["atr"], 2.0), 0.8)

    recent = df_plot.tail(30)
    support = safe_float(recent["low"].min(), price)
    resistance = safe_float(recent["high"].max(), price)
    pivot = safe_float((resistance + support + price) / 3.0, price)
    pattern = detect_candle_pattern(df_plot)
    session = get_market_session()

    # Multi-Factor Technical Score (EMA + MACD + SMC + RSI + VWAP + CVD)
    score, reasons = 0, []
    ema_status = "BUY" if ema20 > ema50 else "SELL"
    score += 2 if ema20 > ema50 else -2
    reasons.append(
        f"EMA 20 (${ema20}) {'នៅលើ' if ema20 > ema50 else 'នៅក្រោម'} EMA 50 (${ema50}) — "
        f"{'និន្នាការឡើង' if ema20 > ema50 else 'និន្នាការចុះ'}"
    )

    score += 1 if price > ema20 else -1
    macd_status = "BUY" if macd_hist > 0 else "SELL"
    score += 1 if macd_hist > 0 else -1
    reasons.append(f"MACD Histogram ({macd_hist:+.3f}) — {'កម្លាំងទិញមានប្រៀប' if macd_hist > 0 else 'កម្លាំងលក់មានប្រៀប'}")

    if smc["bias"] == "BULLISH":
        score += 1
    elif smc["bias"] == "BEARISH":
        score -= 1

    if quant.get("vwap") is not None:
        if quant["vwap_bias"] == "BUY":
            score += 1
            reasons.append(f"តម្លៃនៅលើ Session VWAP (${quant['vwap']}) • POC ${quant['poc']}")
        elif quant["vwap_bias"] == "SELL":
            score -= 1
            reasons.append(f"តម្លៃនៅក្រោម Session VWAP (${quant['vwap']}) • POC ${quant['poc']}")

    if quant.get("cvd_divergence") == "BULL_DIV":
        score += 2
        reasons.insert(0, "CVD Divergence — លុយស្ថាប័នកំពុងលួចប្រមូលទិញ")
    elif quant.get("cvd_divergence") == "BEAR_DIV":
        score -= 2
        reasons.insert(0, "CVD Divergence — លុយស្ថាប័នកំពុងលួចលក់ចេញ")

    if rsi < 35:
        rsi_status, score = "BUY", score + 1
        reasons.append(f"RSI ({rsi}) នៅតំបន់ Oversold — មានឱកាសងើបឡើងវិញ")
    elif rsi > 65:
        rsi_status, score = "SELL", score - 1
        reasons.append(f"RSI ({rsi}) នៅតំបន់ Overbought — ប្រយ័ត្នការធ្លាក់ចុះ")
    else:
        rsi_status = "NEUTRAL"
        reasons.append(f"RSI ({rsi}) នៅតំបន់មានលំនឹង")

    if score >= 4:
        signal, confidence = "STRONG BUY", 88
    elif score >= 1:
        signal, confidence = "BUY", 70
    elif score <= -4:
        signal, confidence = "STRONG SELL", 88
    elif score <= -1:
        signal, confidence = "SELL", 70
    else:
        signal, confidence = "NEUTRAL", 50

    if active_signal:
        entry_val, sl, tp1, tp2, rr_txt = active_signal["entry"], active_signal["sl"], active_signal["tp1"], active_signal["tp2"], active_signal["rr"]
        e_low, e_high = safe_float(min(price, entry_val)), safe_float(max(price, entry_val))
        action_text = f"សញ្ញាក្រាហ្វិកសកម្ម៖ {active_signal['side']} នៅ ${entry_val} (SL: ${sl} | TP1: ${tp1})"
    elif "BUY" in signal:
        entry_val, e_low, e_high = price, safe_float(min(price, ema20)), price
        sl, tp1, tp2, rr_txt = safe_float(price - atr * 1.4), safe_float(price + atr * 2.8), safe_float(price + atr * 4.0), "2 : 1"
        action_text = f"រង់ចាំចូល BUY ក្នុងតំបន់ ${e_low} - ${e_high} ដោយមានទៀនបញ្ជាក់"
    elif "SELL" in signal:
        entry_val, e_low, e_high = price, price, safe_float(max(price, ema20))
        sl, tp1, tp2, rr_txt = safe_float(price + atr * 1.4), safe_float(price - atr * 2.8), safe_float(price - atr * 4.0), "2 : 1"
        action_text = f"រង់ចាំចូល SELL ក្នុងតំបន់ ${e_low} - ${e_high} ពេលតម្លៃងើបប៉ះ EMA 20"
    else:
        entry_val, e_low, e_high = price, support, resistance
        sl, tp1, tp2, rr_txt = safe_float(support - atr), resistance, safe_float(resistance + atr), "1.5 : 1"
        action_text = "ទីផ្សារកំពុងរត់ទទឹង (Sideway) — គួររង់ចាំ Breakout សិន"

    vwap_val = quant.get("vwap")
    matrix = [
        {"name": "EMA 20 / 50", "value": f"${ema20} / ${ema50}", "status": ema_status},
        {"name": "RSI (14)", "value": f"{rsi}", "status": rsi_status},
        {"name": "MACD Hist", "value": f"{macd_hist:+.3f}", "status": macd_status},
        {"name": "SMC Structure", "value": smc["bias"], "status": "BUY" if smc["bias"] == "BULLISH" else ("SELL" if smc["bias"] == "BEARISH" else "NEUTRAL")},
        {"name": "VWAP / POC", "value": f"${vwap_val or pivot} / ${quant.get('poc') or pivot}", "status": quant.get("vwap_bias", "NEUTRAL")},
        {"name": "Price Action", "value": pattern["name"], "status": pattern["bias"]},
    ]

    # Safe Unix-Second Serialization (ដំណើរការត្រូវ ១០០% លើ Pandas គ្រប់ជំនាន់)
    ts_arr = np.array([int(pd.Timestamp(t).timestamp()) for t in df_plot["datetime"]], dtype=np.int64)
    o_arr = df_plot["open"].to_numpy(dtype=float)
    h_arr = df_plot["high"].to_numpy(dtype=float)
    l_arr = df_plot["low"].to_numpy(dtype=float)
    c_arr = df_plot["close"].to_numpy(dtype=float)
    v_arr = df_plot["volume"].to_numpy(dtype=float)
    e20_arr = df_plot["ema20"].to_numpy(dtype=float)
    e50_arr = df_plot["ema50"].to_numpy(dtype=float)

    candles, ema20_line, ema50_line = [], [], []
    last_ts = -1
    for i in range(len(ts_arr)):
        ts = int(ts_arr[i])
        if ts <= last_ts:
            continue
        last_ts = ts
        candles.append({
            "time": ts,
            "open": safe_float(o_arr[i]),
            "high": safe_float(h_arr[i]),
            "low": safe_float(l_arr[i]),
            "close": safe_float(c_arr[i]),
            "volume": safe_float(v_arr[i], 0.0, 4),
        })
        ema20_line.append({"time": ts, "value": safe_float(e20_arr[i], price)})
        ema50_line.append({"time": ts, "value": safe_float(e50_arr[i], price)})

    return {
        "price": price, "change": change, "change_pct": change_pct,
        "rsi": rsi, "ema20": ema20, "ema50": ema50, "macd_hist": macd_hist,
        "bb_upper": bb_upper, "bb_lower": bb_lower, "atr": atr,
        "support": support, "resistance": resistance, "pivot": pivot,
        "pattern": pattern, "session": session, "signal": signal,
        "confidence": confidence, "reasons": reasons[:5], "matrix": matrix,
        "chart_signals": chart_signals, "active_signal": active_signal,
        "zones": zones, "smc": smc, "quant": quant,
        "setup": {
            "entry": entry_val, "entry_zone": f"${e_low} - ${e_high}",
            "sl": sl, "sl_dist": safe_float(abs(entry_val - sl), 2.5),
            "tp1": tp1, "tp2": tp2, "rr": rr_txt, "advice": action_text,
        },
        "candles": candles, "ema20_line": ema20_line, "ema50_line": ema50_line, "vwap_line": vwap_line,
    }