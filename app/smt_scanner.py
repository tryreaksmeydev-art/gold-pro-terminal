from concurrent.futures import TimeoutError as FuturesTimeout
import numpy as np
import pandas as pd
import yfinance as yf

from .config import mtf_executor, http_session, safe_float
from .cache import cache
from .feeds import fetch_gold_data


# ==================== #2 GOLD (XAU) vs SILVER (XAG) SMT DIVERGENCE ====================
def _fetch_silver_df_raw(interval: str) -> pd.DataFrame | None:
    try:
        ticker = yf.Ticker("SI=F")
        if interval == "4h":
            df = ticker.history(period="1mo", interval="1h")
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max", "Low": "min", "Close": "last",
                }).dropna()
        else:
            period = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "1mo", "1d": "3mo"}.get(interval, "5d")
            yf_int = interval if interval in ("1m", "5m", "15m", "1h", "1d") else "15m"
            df = ticker.history(period=period, interval=yf_int)

        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            tcol = "Datetime" if "Datetime" in df.columns else ("Date" if "Date" in df.columns else df.columns[0])
            df = df.rename(columns={
                tcol: "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close",
            })
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df[["datetime", "open", "high", "low", "close"]].dropna().tail(80).reset_index(drop=True)
            if len(df) >= 15:
                return df
    except Exception:
        pass
    return None


def _fetch_silver_live_px() -> float | None:
    try:
        url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAG/USD"
        res = http_session.get(url, timeout=(1.8, 2.5))
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and data:
                for item in data:
                    for sp in (item.get("spreadProfilePrices") or []):
                        bid, ask = safe_float(sp.get("bid"), 0.0), safe_float(sp.get("ask"), 0.0)
                        if bid > 5 and ask > 5:
                            return safe_float((bid + ask) / 2.0, 0.0, 3)
    except Exception:
        pass
    return None


def get_smt_divergence(gold_df: pd.DataFrame | None, interval: str = "15m") -> dict:
    """
    ចាប់សញ្ញា SMT Divergence រវាងមាស (XAU) និងប្រាក់ (XAG)៖
    - BULL_SMT: មាសទម្លាក់បាតថ្មី (Lower Low) តែប្រាក់មិនចុះតាម (Higher Low) -> បោស SL ត្រៀមឡើង
    - BEAR_SMT: មាសឡើងកំពូលថ្មី (Higher High) តែប្រាក់មិនឡើងតាម (Lower High) -> បោស SL ត្រៀមចុះ
    """
    empty = {
        "signal": "NONE",
        "label_km": "XAU/XAG ស្របគ្នា",
        "detail_km": "មាស និងប្រាក់កំពុងដើរស្របទិសដៅគ្នាធម្មតា",
        "silver_px": None,
        "strength": 0,
    }
    if gold_df is None or len(gold_df) < 20:
        return empty

    ttl = 25.0 if interval in ("1m", "5m") else 45.0
    xag_df = cache.get_or_fetch(f"silver_{interval}", ttl, lambda: _fetch_silver_df_raw(interval))
    xag_live = cache.get_or_fetch("silver_live_px", 10.0, _fetch_silver_live_px)

    if xag_df is None or len(xag_df) < 20:
        empty["silver_px"] = xag_live
        return empty

    try:
        # ប្រៀបធៀប Swing ១០ ទៀនមុន (prev) ធៀបនឹង ៨ ទៀនចុងក្រោយ (recent)
        g_hi = gold_df["high"].to_numpy(dtype=float)[-18:]
        g_lo = gold_df["low"].to_numpy(dtype=float)[-18:]
        s_hi = xag_df["high"].to_numpy(dtype=float)[-18:]
        s_lo = xag_df["low"].to_numpy(dtype=float)[-18:]
        if len(g_hi) < 16 or len(s_hi) < 16:
            empty["silver_px"] = xag_live or safe_float(xag_df["close"].iloc[-1], 0.0, 3)
            return empty

        g_prev_hi, g_rec_hi = float(np.max(g_hi[:10])), float(np.max(g_hi[10:]))
        g_prev_lo, g_rec_lo = float(np.min(g_lo[:10])), float(np.min(g_lo[10:]))
        s_prev_hi, s_rec_hi = float(np.max(s_hi[:10])), float(np.max(s_hi[10:]))
        s_prev_lo, s_rec_lo = float(np.min(s_lo[:10])), float(np.min(s_lo[10:]))

        silver_px = xag_live or safe_float(xag_df["close"].iloc[-1], 0.0, 3)

        # Bullish SMT: មាសចុះបាតថ្មី តែប្រាក់រឹងមិនព្រមចុះតាម
        if g_rec_lo < g_prev_lo * 0.9997 and s_rec_lo > s_prev_lo * 1.0004:
            return {
                "signal": "BULL_SMT",
                "label_km": "SMT ▲ បោស SL ឡើង",
                "detail_km": "មាសទម្លាក់បាតថ្មី តែប្រាក់ (XAG) មិនចុះតាម — ស្ថាប័នលួចប្រមូលទិញ",
                "silver_px": silver_px,
                "strength": 9,
            }

        # Bearish SMT: មាសឡើងកំពូលថ្មី តែប្រាក់ខ្សោយមិនឡើងតាម
        if g_rec_hi > g_prev_hi * 1.0003 and s_rec_hi < s_prev_hi * 0.9996:
            return {
                "signal": "BEAR_SMT",
                "label_km": "SMT ▼ បោស SL ចុះ",
                "detail_km": "មាសហក់កំពូលថ្មី តែប្រាក់ (XAG) មិនឡើងតាម — អន្ទាក់លក់ចេញ",
                "silver_px": silver_px,
                "strength": 9,
            }

        empty["silver_px"] = silver_px
        return empty
    except Exception:
        return empty


# ==================== #3 MULTI-TIMEFRAME SNIPER SCANNER (1m - 4H) ====================
def _score_single_tf_fast(df: pd.DataFrame | None) -> dict:
    if df is None or len(df) < 30:
        return {"side": "WAIT", "grade": "WAIT", "score": 50, "rsi": 50.0, "is_sniper": False}

    c = pd.to_numeric(df["close"], errors="coerce").dropna()
    h = pd.to_numeric(df["high"], errors="coerce").dropna()
    l = pd.to_numeric(df["low"], errors="coerce").dropna()
    if len(c) < 30:
        return {"side": "WAIT", "grade": "WAIT", "score": 50, "rsi": 50.0, "is_sniper": False}

    px = float(c.iloc[-1])
    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

    delta = c.diff()
    ag = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    al = (-delta).clip(lower=0.0).ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    rs = ag / al.replace(0.0, np.nan)
    rsi = safe_float((100.0 - (100.0 / (1.0 + rs))).fillna(50.0).iloc[-1], 50.0, 1)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])

    prev_c = float(c.iloc[-2])
    rng = max(float(h.iloc[-1]) - float(l.iloc[-1]), 0.05)
    body_bull = px > prev_c and ((px - float(l.iloc[-1])) / rng) >= 0.55
    body_bear = px < prev_c and ((float(h.iloc[-1]) - px) / rng) >= 0.55

    buy_s, sell_s = 0, 0
    if px > ema20 and ema20 > ema50:
        buy_s += 36
    elif px > ema20:
        buy_s += 18
    if px < ema20 and ema20 < ema50:
        sell_s += 36
    elif px < ema20:
        sell_s += 18

    if hist > 0:
        buy_s += 20
    elif hist < 0:
        sell_s += 20

    if 42 <= rsi <= 68:
        buy_s += 14
    elif rsi < 32:
        buy_s += 10
    if 32 <= rsi <= 58:
        sell_s += 14
    elif rsi > 68:
        sell_s += 10

    if body_bull:
        buy_s += 14
    if body_bear:
        sell_s += 14

    if buy_s >= 64 and (buy_s - sell_s) >= 20:
        grade = "A+" if buy_s >= 80 else ("A" if buy_s >= 72 else "B")
        return {"side": "BUY", "grade": grade, "score": min(95, buy_s), "rsi": rsi, "is_sniper": grade in ("A+", "A")}
    if sell_s >= 64 and (sell_s - buy_s) >= 20:
        grade = "A+" if sell_s >= 80 else ("A" if sell_s >= 72 else "B")
        return {"side": "SELL", "grade": grade, "score": min(95, sell_s), "rsi": rsi, "is_sniper": grade in ("A+", "A")}

    return {"side": "WAIT", "grade": "WAIT", "score": max(buy_s, sell_s), "rsi": rsi, "is_sniper": False}


def _compute_mtf_scanner_raw() -> dict:
    tfs = [("1m", "1m"), ("5m", "5m"), ("15m", "15m"), ("1h", "1H"), ("4h", "4H")]
    futures = {code: mtf_executor.submit(fetch_gold_data, code) for code, _ in tfs}
    items = []
    best = None

    for code, label in tfs:
        try:
            df, _, _, _ = futures[code].result(timeout=6.0)
        except (FuturesTimeout, Exception):
            df = None
        sc = _score_single_tf_fast(df)
        entry = {"tf": code, "label": label, **sc}
        items.append(entry)
        if sc["side"] in ("BUY", "SELL"):
            if best is None or sc["score"] > best["score"]:
                best = entry

    return {"items": items, "best": best}


def get_mtf_scanner() -> dict:
    res = cache.get_or_fetch("mtf_sniper_scanner", 15.0, _compute_mtf_scanner_raw)
    return res or {"items": [], "best": None}