import re
import time
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import MetaTrader5 as mt5
    MT5_INSTALLED = True
except Exception:
    mt5 = None
    MT5_INSTALLED = False

from .config import (
    http_session,
    io_executor,
    OANDA_API_KEY,
    TWELVE_DATA_KEY,
    FINNHUB_API_KEY,
    safe_float,
    format_dual_time,
)
from .cache import cache

_quote_state_lock = threading.Lock()
_last_quote_state = {"price": 0.0, "ts": 0.0}

# រក្សាតម្លៃ Forex Spot ចុងក្រោយទុក 20s ការពារកុំឱ្យធ្លាក់ទៅតម្លៃ Crypto ពេលអ៊ីនធឺណិតយឺត ១–២ វិនាទី
_forex_sticky_lock = threading.Lock()
_last_good_forex_quote = {"quote": None, "ts": 0.0}

# Twelve Data Rate-Limit Guard (ការពារកុំឱ្យបុក 8/min និងបង្វែរស្វ័យប្រវត្តិពេលអស់ Credit)
_td_lock = threading.Lock()
_td_call_times = deque(maxlen=10)
_td_cooldown_until = 0.0

_mt5_lock = threading.Lock()
_mt5_state = {"ready": False, "symbol": "", "last_try": 0.0}


def _td_can_call() -> bool:
    """ពិនិត្យថាអាចហៅ Twelve Data បានឬទេ (កំណត់អតិបរមា 6 ដង/នាទី ដើម្បីកុំឱ្យបុក 8/8)"""
    now = time.monotonic()
    with _td_lock:
        if now < _td_cooldown_until:
            return False
        while _td_call_times and (now - _td_call_times[0]) > 62.0:
            _td_call_times.popleft()
        if len(_td_call_times) >= 6:
            return False
        _td_call_times.append(now)
        return True


def _td_set_cooldown(seconds: float):
    global _td_cooldown_until
    with _td_lock:
        _td_cooldown_until = max(_td_cooldown_until, time.monotonic() + seconds)


# ==================== OPTIONAL DIRECT MT5 BRIDGE (SILENT FALLBACK IF NOT USED) ====================
def _ensure_mt5_symbol() -> str | None:
    if not MT5_INSTALLED or mt5 is None:
        return None
    now = time.monotonic()
    with _mt5_lock:
        if _mt5_state["ready"] and _mt5_state["symbol"]:
            return _mt5_state["symbol"]
        if (now - _mt5_state["last_try"]) < 60.0:
            return None
        _mt5_state["last_try"] = now
        try:
            if not mt5.initialize():
                return None
            for sym in ("XAUUSD", "XAUUSDm", "XAUUSDc", "XAUUSD.pro", "XAUUSD.raw", "GOLD"):
                info = mt5.symbol_info(sym)
                if info is not None:
                    mt5.symbol_select(sym, True)
                    _mt5_state["ready"] = True
                    _mt5_state["symbol"] = sym
                    return sym
        except Exception:
            pass
    return None


def _fetch_mt5_quote() -> dict | None:
    sym = _ensure_mt5_symbol()
    if not sym or mt5 is None:
        return None
    try:
        tk = mt5.symbol_info_tick(sym)
        if tk and tk.bid > 1000 and tk.ask > 1000:
            bid, ask = round(float(tk.bid), 2), round(float(tk.ask), 2)
            closed = _is_forex_weekend_utc()
            return {
                "price": bid,
                "mid": round((bid + ask) / 2, 2),
                "bid": bid,
                "ask": ask,
                "spread": round(max(0.0, ask - bid), 2),
                "is_forex": True,
                "market_closed": closed,
                "feed": f"MT5 Direct • {sym}" if not closed else f"Market Closed • MT5 {sym}",
            }
    except Exception:
        with _mt5_lock:
            _mt5_state["ready"] = False
    return None


def _fetch_mt5_candles(interval: str, limit: int) -> tuple[pd.DataFrame, str, bool, str] | None:
    sym = _ensure_mt5_symbol()
    if not sym or mt5 is None:
        return None
    tf_map = {
        "1m": mt5.TIMEFRAME_M1,
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "1h": mt5.TIMEFRAME_H1,
        "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1,
    }
    try:
        rates = mt5.copy_rates_from_pos(sym, tf_map.get(interval, mt5.TIMEFRAME_M5), 0, limit)
        if rates is not None and len(rates) > 5:
            df = pd.DataFrame(rates)
            df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df["volume"] = pd.to_numeric(df.get("tick_volume", 0.0), errors="coerce").fillna(0.0)
            df = _clean_ohlc_df(df[["datetime", "open", "high", "low", "close", "volume"]], is_crypto=False)
            if len(df) > 5:
                closed = _is_forex_weekend_utc()
                return df, (f"Market Closed • MT5 {sym}" if closed else f"MT5 Direct • {sym}"), not closed, "MT5"
    except Exception:
        with _mt5_lock:
            _mt5_state["ready"] = False
    return None


# ==================== FOREX MARKET HOURS & INSTITUTIONAL OHLC SANITIZER ====================
def _is_forex_weekend_utc(now_utc: datetime | None = None) -> bool:
    """ទីផ្សារ Forex XAU/USD បិទពីថ្ងៃសុក្រ 22:00 UTC ដល់ថ្ងៃអាទិត្យ 22:00 UTC"""
    dt = now_utc or datetime.now(timezone.utc)
    wd = dt.weekday()
    return wd == 5 or (wd == 4 and dt.hour >= 22) or (wd == 6 and dt.hour < 22)


def _trim_weekend_candles(df: pd.DataFrame) -> pd.DataFrame:
    """កាត់ចោលទៀនចុងសប្តាហ៍ (សុក្រ >= 22:00 UTC ដល់ អាទិត្យ < 22:00 UTC) ឱ្យត្រូវនឹង MT5"""
    if df is None or df.empty or "datetime" not in df.columns:
        return df
    dt_s = pd.to_datetime(df["datetime"], utc=True)
    wd = dt_s.dt.weekday
    hr = dt_s.dt.hour
    is_wknd = (wd == 5) | ((wd == 4) & (hr >= 22)) | ((wd == 6) & (hr < 22))
    trimmed = df.loc[~is_wknd].reset_index(drop=True)
    return trimmed if len(trimmed) > 5 else df


def _trim_trailing_dead_candles(df: pd.DataFrame) -> pd.DataFrame:
    """កាត់ចោលទៀនស្លាប់រាបស្មើជាប់គ្នានៅចុងក្រាហ្វិក (ពេលផ្សារបិទមុនម៉ោងដូច Black Friday / Holiday)"""
    if df is None or len(df) < 15:
        return df
    ranges = (df["high"] - df["low"]).to_numpy(dtype=float)
    bodies = (df["close"] - df["open"]).abs().to_numpy(dtype=float)

    cut = len(df)
    while cut > 15 and ranges[cut - 1] <= 0.45 and bodies[cut - 1] <= 0.25:
        cut -= 1

    if (len(df) - cut) >= 4:
        return df.iloc[:cut].reset_index(drop=True)
    return df


def _is_flat_barcode_df(df: pd.DataFrame) -> bool:
    """ចាប់សម្គាល់ទៀនស្លាប់/Barcode (ឧ. ទៀនចុងសប្តាហ៍ដែលតម្លៃជាប់ស្ងៀម < $1.20)"""
    if df is None or len(df) < 20:
        return True
    tail = df.tail(30)
    total_span = float(tail["high"].max() - tail["low"].min())
    return total_span < 1.20


def _align_df_to_forex_bid(df: pd.DataFrame) -> pd.DataFrame:
    """តម្រឹមគម្លាតតូច Bid/Mid ឱ្យចុងទៀនស៊ីគ្នានឹងតម្លៃ Bid (ការពារទៀនធ្លាក់ក្លែងក្លាយ)"""
    if df is None or df.empty:
        return df
    with _forex_sticky_lock:
        q = _last_good_forex_quote.get("quote")
    if not q:
        q = _fetch_swissquote_xau()
    bid = safe_float((q or {}).get("bid"), 0.0)
    last_c = safe_float(df["close"].iloc[-1], 0.0)
    if bid > 1000 and last_c > 1000:
        delta = bid - last_c
        if 0.05 <= abs(delta) <= 5.0:
            df = df.copy()
            for col in ("open", "high", "low", "close"):
                df[col] = (df[col] + delta).round(2)
    return df


def _denoise_crypto_wicks(df: pd.DataFrame) -> pd.DataFrame:
    """
    ច្រោះសរសៃទៀនលោតខុសធម្មតារបស់ Crypto (PAXG/XAUT Thin-Book Spikes)
    ដើម្បីកុំឱ្យខួរក្បាល AI ច្រឡំថាជា Liquidity Sweep ឬគណនា OB/SL ខុសពី Forex ពិត
    """
    if df is None or len(df) < 12:
        return df
    df = df.copy()
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    oc_max = np.maximum(o, c)
    oc_min = np.minimum(o, c)
    ranges = np.maximum(h - l, 0.25)
    med_range = pd.Series(ranges).rolling(15, center=True, min_periods=5).median().to_numpy()
    med_range = np.maximum(med_range, 0.6)

    max_wick = np.maximum(med_range * 3.0, 4.5)
    up_wick = h - oc_max
    dn_wick = oc_min - l

    h_clean = np.where(up_wick > max_wick, oc_max + max_wick, h)
    l_clean = np.where(dn_wick > max_wick, oc_min - max_wick, l)

    df["high"] = np.maximum(h_clean, oc_max)
    df["low"] = np.minimum(l_clean, oc_min)
    return df


def _clean_ohlc_df(df: pd.DataFrame, is_crypto: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    else:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df.drop_duplicates(subset=["datetime"], keep="last").sort_values("datetime").reset_index(drop=True)
    if len(df) < 3:
        return df

    # ច្រោះ Bad Tick លោតខុសធម្មតា > $40 ធៀបនឹង Median ជុំវិញ
    med_c = df["close"].rolling(5, center=True, min_periods=1).median()
    valid_mask = (df["close"] > 1000.0) & ((df["close"] - med_c).abs() <= 40.0)
    if valid_mask.sum() >= 5:
        df = df.loc[valid_mask].reset_index(drop=True)

    oc_max = np.maximum(df["open"].to_numpy(), df["close"].to_numpy())
    oc_min = np.minimum(df["open"].to_numpy(), df["close"].to_numpy())
    df["high"] = np.maximum(df["high"].to_numpy(), oc_max)
    df["low"] = np.minimum(df["low"].to_numpy(), oc_min)

    if is_crypto:
        df = _denoise_crypto_wicks(df)
    return df


# ==================== LIVE FOREX SPOT XAU/USD QUOTE ENGINE ====================
def _fetch_swissquote_xau() -> dict | None:
    """ទាញតម្លៃ Forex Spot XAU/USD ផ្ទាល់ពី Swissquote (0 Credit ឥតគិតថ្លៃ 24/5)"""
    try:
        url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
        res = http_session.get(url, timeout=(2.0, 3.0))
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and data:
                candidates = []
                for item in data:
                    for sp in (item.get("spreadProfilePrices") or []):
                        bid = safe_float(sp.get("bid"), 0.0)
                        ask = safe_float(sp.get("ask"), 0.0)
                        if bid > 1000 and ask > 1000 and ask >= bid:
                            spread = ask - bid
                            if spread <= 4.0:
                                prof = str(sp.get("spreadProfile", "")).lower()
                                prio = 0 if prof in ("prime", "standard") else 1
                                candidates.append((prio, spread, bid, ask))
                if candidates:
                    candidates.sort(key=lambda x: (x[0], x[1]))
                    _, spread, bid, ask = candidates[0]
                    closed = _is_forex_weekend_utc()
                    return {
                        "price": round(bid, 2),
                        "mid": round((bid + ask) / 2, 2),
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "spread": round(max(0.0, spread), 2),
                        "is_forex": True,
                        "market_closed": closed,
                        "feed": "Market Closed • XAU/USD" if closed else "Forex Spot Live • XAU/USD",
                    }
    except Exception:
        pass
    return None


def _fetch_binance_paxg_ticker() -> dict | None:
    try:
        url = "https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=PAXGUSDT"
        res = http_session.get(url, timeout=(2.0, 3.0))
        if res.status_code == 200:
            d = res.json()
            bid, ask = safe_float(d.get("bidPrice")), safe_float(d.get("askPrice"))
            if bid > 1000 and ask > 1000:
                return {
                    "price": round(bid, 2),
                    "mid": round((bid + ask) / 2, 2),
                    "bid": round(bid, 2),
                    "ask": round(ask, 2),
                    "spread": round(max(0.0, ask - bid), 2),
                    "is_forex": False,
                    "market_closed": _is_forex_weekend_utc(),
                    "feed": "PAXG/USD",
                }
    except Exception:
        pass
    return None


def _fetch_okx_xaut_ticker() -> dict | None:
    try:
        url = "https://www.okx.com/api/v5/market/ticker?instId=XAUT-USDT"
        res = http_session.get(url, timeout=(2.0, 3.0))
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                bid, ask = safe_float(data[0].get("bidPx")), safe_float(data[0].get("askPx"))
                if bid > 1000 and ask > 1000:
                    return {
                        "price": round(bid, 2),
                        "mid": round((bid + ask) / 2, 2),
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "spread": round(max(0.0, ask - bid), 2),
                        "is_forex": False,
                        "market_closed": _is_forex_weekend_utc(),
                        "feed": "XAUT/USD",
                    }
    except Exception:
        pass
    return None


def _remember_forex_quote(q: dict) -> dict:
    with _forex_sticky_lock:
        _last_good_forex_quote["quote"] = dict(q)
        _last_good_forex_quote["ts"] = time.monotonic()
    return q


def _fetch_live_quote_raw() -> dict | None:
    closed = _is_forex_weekend_utc()

    mt5_q = _fetch_mt5_quote()
    if mt5_q:
        return _remember_forex_quote(mt5_q)

    if OANDA_API_KEY:
        try:
            url = "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity=S5&count=1&price=BA"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.0, 3.0)).json()
            if "candles" in res and res["candles"]:
                c = res["candles"][-1]
                bid, ask = safe_float(c["bid"]["c"]), safe_float(c["ask"]["c"])
                if bid > 1000 and ask > 1000:
                    return _remember_forex_quote({
                        "price": round(bid, 2),
                        "mid": round((bid + ask) / 2, 2),
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "spread": round(max(0.0, ask - bid), 2),
                        "is_forex": True,
                        "market_closed": closed,
                        "feed": "Market Closed • OANDA XAU/USD" if closed else "OANDA Spot • XAU/USD",
                    })
        except Exception:
            pass

    # Swissquote រត់រាល់វិនាទី 0 Credit មិនប៉ះពាល់ Twelve Data Limit ឡើយ
    sq = _fetch_swissquote_xau()
    if sq:
        return _remember_forex_quote(sq)

    # Sticky Forex Guard: បើ Swissquote ទើបដាច់មិនដល់ 20s ប្រើតម្លៃ Forex ចុងក្រោយជំនួសឱ្យលោតទៅ Crypto ឬស៊ី Credit
    with _forex_sticky_lock:
        sticky_q = _last_good_forex_quote["quote"]
        sticky_age = time.monotonic() - _last_good_forex_quote["ts"]
    if sticky_q and sticky_age <= 20.0:
        return dict(sticky_q)

    if TWELVE_DATA_KEY and _td_can_call():
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=(2.0, 3.0)).json()
            if res.get("code") == 429:
                _td_set_cooldown(90.0)
            elif "price" in res:
                p = safe_float(res["price"])
                if p > 1000:
                    return _remember_forex_quote({
                        "price": round(p, 2),
                        "mid": round(p + 0.12, 2),
                        "bid": round(p, 2),
                        "ask": round(p + 0.25, 2),
                        "spread": 0.25,
                        "is_forex": True,
                        "market_closed": closed,
                        "feed": "Market Closed • XAU/USD" if closed else "Twelve Data • XAU/USD",
                    })
        except Exception:
            pass

    if closed:
        try:
            df_c = yf.Ticker("GC=F").history(period="2d", interval="1h")
            if df_c is not None and not df_c.empty:
                p = safe_float(df_c["Close"].iloc[-1], 0.0)
                if p > 1000:
                    return {
                        "price": round(p, 2),
                        "mid": round(p + 0.15, 2),
                        "bid": round(p, 2),
                        "ask": round(p + 0.30, 2),
                        "spread": 0.30,
                        "is_forex": True,
                        "market_closed": True,
                        "feed": "Market Closed • Gold Close",
                    }
        except Exception:
            pass

    bn = _fetch_binance_paxg_ticker()
    if bn:
        return bn

    return _fetch_okx_xaut_ticker()


def fetch_live_quote() -> dict | None:
    ttl = 60.0 if _is_forex_weekend_utc() else 1.5
    return cache.get_or_fetch("live_quote", ttl, _fetch_live_quote_raw)


# ==================== REAL-TIME ORDER FLOW ENGINE ====================
def _fetch_binance_order_flow() -> tuple[float, float, int]:
    try:
        url = "https://data-api.binance.vision/api/v3/aggTrades?symbol=PAXGUSDT&limit=100"
        res = http_session.get(url, timeout=(2.0, 3.0))
        if res.status_code == 200:
            trades = res.json()
            if isinstance(trades, list) and trades:
                buy_v, sell_v = 0.0, 0.0
                for t in trades:
                    qty = float(t.get("q") or 0.0)
                    if t.get("m"):
                        sell_v += qty
                    else:
                        buy_v += qty
                return buy_v, sell_v, len(trades)
    except Exception:
        pass
    return 0.0, 0.0, 0


def _fetch_okx_order_flow() -> tuple[float, float, int]:
    try:
        url = "https://www.okx.com/api/v5/market/trades?instId=XAUT-USDT&limit=80"
        res = http_session.get(url, timeout=(2.0, 3.0))
        if res.status_code == 200:
            trades = res.json().get("data") or []
            if isinstance(trades, list) and trades:
                buy_v, sell_v = 0.0, 0.0
                for t in trades:
                    sz = float(t.get("sz") or 0.0)
                    if t.get("side") == "buy":
                        buy_v += sz
                    else:
                        sell_v += sz
                return buy_v, sell_v, len(trades)
    except Exception:
        pass
    return 0.0, 0.0, 0


def _fetch_order_flow_raw() -> dict:
    if _is_forex_weekend_utc():
        return {
            "buy_pct": 50.0,
            "sell_pct": 50.0,
            "buy_vol": 0.0,
            "sell_vol": 0.0,
            "trades_count": 0,
            "source": "CLOSED",
        }

    f_bn = io_executor.submit(_fetch_binance_order_flow)
    f_okx = io_executor.submit(_fetch_okx_order_flow)
    try:
        bn_b, bn_s, bn_n = f_bn.result(timeout=3.5)
    except Exception:
        bn_b, bn_s, bn_n = 0.0, 0.0, 0
    try:
        ok_b, ok_s, ok_n = f_okx.result(timeout=3.5)
    except Exception:
        ok_b, ok_s, ok_n = 0.0, 0.0, 0

    tot_b, tot_s, tot_n = bn_b + ok_b, bn_s + ok_s, bn_n + ok_n
    tot_v = tot_b + tot_s
    sources = []
    if bn_n > 0:
        sources.append("Binance")
    if ok_n > 0:
        sources.append("OKX")

    buy_pct = round((tot_b / tot_v) * 100.0, 1) if tot_v > 0 else 50.0
    return {
        "buy_pct": buy_pct,
        "sell_pct": round(100.0 - buy_pct, 1),
        "buy_vol": round(tot_b, 3),
        "sell_vol": round(tot_s, 3),
        "trades_count": tot_n,
        "source": "+".join(sources) if sources else "CANDLES",
    }


def fetch_order_flow() -> dict:
    ttl = 60.0 if _is_forex_weekend_utc() else 5.0
    res = cache.get_or_fetch("order_flow", ttl, _fetch_order_flow_raw)
    return res or {
        "buy_pct": 50.0,
        "sell_pct": 50.0,
        "buy_vol": 0.0,
        "sell_vol": 0.0,
        "trades_count": 0,
        "source": "CANDLES",
    }


# ==================== CROSS-SOURCE SANITY CHECK ====================
def _fetch_cross_prices_raw() -> dict:
    f_sq = io_executor.submit(_fetch_swissquote_xau)
    f_bn = io_executor.submit(_fetch_binance_paxg_ticker)
    f_okx = io_executor.submit(_fetch_okx_xaut_ticker)
    out = {}
    for name, fut in (("swissquote", f_sq), ("binance", f_bn), ("okx", f_okx)):
        try:
            q = fut.result(timeout=3.2)
            if q and q.get("price", 0) > 1000:
                out[name] = float(q["price"])
        except Exception:
            pass
    return out


def check_cross_source_sanity(live_price: float, basis: float = 0.0) -> dict:
    if _is_forex_weekend_utc():
        return {
            "ok": True,
            "glitch_detected": False,
            "deviation": 0.0,
            "sources_checked": 1,
            "note": "ទីផ្សារបិទចុងសប្តាហ៍ (បង្កកតម្លៃបិទថ្ងៃសុក្រ)",
        }

    now = time.monotonic()
    spike_glitch = False
    spike_note = ""

    with _quote_state_lock:
        if _last_quote_state["price"] > 1000 and (now - _last_quote_state["ts"]) < 20.0:
            jump = abs(live_price - _last_quote_state["price"])
            if jump > 35.0:
                spike_glitch = True
                spike_note = f"តម្លៃលោតខុសធម្មតា ${jump:.2f} ភ្លាមៗ"
        if live_price > 1000 and not spike_glitch:
            _last_quote_state["price"] = live_price
            _last_quote_state["ts"] = now

    cross = cache.get_or_fetch("cross_sanity", 12.0, _fetch_cross_prices_raw) or {}
    adjusted = []
    if "swissquote" in cross:
        adjusted.append(cross["swissquote"])
    if "binance" in cross:
        adjusted.append(cross["binance"] + basis)
    if "okx" in cross:
        adjusted.append(cross["okx"] + basis)

    max_dev = 0.0
    cross_glitch = False
    cross_note = "ទិន្នន័យតម្លៃស្របគ្នាល្អ"

    if len(adjusted) >= 2 and live_price > 1000:
        med = float(np.median(adjusted))
        max_dev = round(abs(live_price - med), 2)
        if max_dev > 25.0:
            cross_glitch = True
            cross_note = f"តម្លៃខុសគ្នារវាងប្រភព ${max_dev:.2f}"

    glitch = spike_glitch or cross_glitch
    return {
        "ok": not glitch,
        "glitch_detected": glitch,
        "deviation": max_dev,
        "sources_checked": len(adjusted),
        "note": spike_note if spike_glitch else cross_note,
    }


# ==================== CANDLE DATA FETCHERS (TRUE FOREX FIRST + CREDIT-SAFE) ====================
def _fetch_yahoo_gold(interval: str, limit: int) -> tuple[pd.DataFrame, str, bool, str] | None:
    try:
        ticker = yf.Ticker("GC=F")
        if interval == "4h":
            df = ticker.history(period="1mo", interval="1h")
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.resample("4h").agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }).dropna()
        else:
            period = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "2mo", "1d": "6mo"}.get(interval, "5d")
            df = ticker.history(period=period, interval=interval)

        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            tcol = "Datetime" if "Datetime" in df.columns else ("Date" if "Date" in df.columns else df.columns[0])
            df = df.rename(columns={
                tcol: "datetime",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })
            if "volume" not in df.columns:
                df["volume"] = 0.0
            df = _clean_ohlc_df(
                df[["datetime", "open", "high", "low", "close", "volume"]],
                is_crypto=False,
            ).tail(limit).reset_index(drop=True)
            if len(df) > 5:
                closed = _is_forex_weekend_utc()
                label = "Market Closed • Gold Futures" if closed else "Yahoo Futures • GC=F"
                return df, label, False, "YAHOO"
    except Exception as e:
        print(f"[Yahoo] {e}")
    return None


def _fetch_gold_raw(interval: str) -> tuple[pd.DataFrame, str, bool, str] | None:
    limit = 260 if interval == "1h" else 160
    closed = _is_forex_weekend_utc()

    mt5_bars = _fetch_mt5_candles(interval, limit)
    if mt5_bars is not None:
        return mt5_bars

    # អាទិភាពខ្ពស់បំផុតសម្រាប់អនឡាញ/ទូរស័ព្ទ៖ OANDA Spot XAU/USD
    if OANDA_API_KEY:
        try:
            gran = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}.get(interval, "M5")
            url = f"https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity={gran}&count={limit}&price=B"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.5, 4.5)).json()
            if "candles" in res and len(res["candles"]) > 5:
                rows = [{
                    "datetime": pd.to_datetime(c["time"], utc=True),
                    "open": float(c["bid"]["o"]),
                    "high": float(c["bid"]["h"]),
                    "low": float(c["bid"]["l"]),
                    "close": float(c["bid"]["c"]),
                    "volume": float(c.get("volume", 0.0)),
                } for c in res["candles"]]
                df = _clean_ohlc_df(pd.DataFrame(rows), is_crypto=False)
                df = _trim_trailing_dead_candles(df)
                if not _is_flat_barcode_df(df):
                    return df, ("Market Closed • OANDA XAU/USD" if closed else "OANDA Spot • XAU/USD"), not closed, "OANDA"
        except Exception as e:
            print(f"[OANDA] {e}")

    # Twelve Data Forex XAU/USD (មាន Rate-Limit Guard + Anti-Dead-Candle + Bid Align)
    if TWELVE_DATA_KEY and _td_can_call():
        try:
            td_int = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}.get(interval, "5min")
            out_sz = min(500, limit + 240) if closed else limit
            url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={td_int}&outputsize={out_sz}&timezone=UTC&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=(2.5, 4.5)).json()
            if res.get("code") == 429:
                msg = str(res.get("message", "")).lower()
                # បើអស់ប្រចាំថ្ងៃ សម្រាក 15 នាទី • បើអស់ប្រចាំនាទី សម្រាក 70 វិនាទី
                _td_set_cooldown(900.0 if "day" in msg else 70.0)
            elif "values" in res and len(res["values"]) > 5:
                df = _clean_ohlc_df(pd.DataFrame(res["values"]), is_crypto=False)
                df = _trim_weekend_candles(df)
                df = _trim_trailing_dead_candles(df).tail(limit).reset_index(drop=True)
                if len(df) > 20 and not _is_flat_barcode_df(df):
                    df = _align_df_to_forex_bid(df)
                    return df, ("Market Closed • XAU/USD" if closed else "Twelve Data Spot • XAU/USD"), not closed, "TWELVE"
        except Exception as e:
            print(f"[TwelveData] {e}")

    if closed:
        yf_res = _fetch_yahoo_gold(interval, limit)
        if yf_res is not None:
            return yf_res

    # Fallback Crypto PAXG (កាត់ទៀនចុងសប្តាហ៍ + ច្រោះសរសៃលោតខុសធម្មតា ធានា AI ដើររលូនជានិច្ច)
    try:
        bn_int = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval, "5m")
        fetch_lim = min(500, limit + 160) if closed else limit
        url = f"https://data-api.binance.vision/api/v3/klines?symbol=PAXGUSDT&interval={bn_int}&limit={fetch_lim}"
        res = http_session.get(url, timeout=(2.5, 4.5))
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) > 5:
                rows = [{
                    "datetime": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                } for k in raw]
                df = _trim_weekend_candles(_clean_ohlc_df(pd.DataFrame(rows), is_crypto=True)).tail(limit).reset_index(drop=True)
                if len(df) > 5:
                    return df, ("Market Closed • Gold Close" if closed else "Gold Spot Live • PAXG/USD"), not closed, "PAXG"
    except Exception as e:
        print(f"[Binance] {e}")

    try:
        okx_bar = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}.get(interval, "5m")
        url = f"https://www.okx.com/api/v5/market/candles?instId=XAUT-USDT&bar={okx_bar}&limit={min(300, limit + 120)}"
        res = http_session.get(url, timeout=(2.5, 4.5))
        if res.status_code == 200:
            raw = res.json().get("data", [])
            if isinstance(raw, list) and len(raw) > 5:
                rows = [{
                    "datetime": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0.0,
                } for k in raw]
                df = _trim_weekend_candles(_clean_ohlc_df(pd.DataFrame(rows), is_crypto=True)).tail(limit).reset_index(drop=True)
                if len(df) > 5:
                    return df, ("Market Closed • Gold Close" if closed else "Gold Spot Live • XAUT/USD"), not closed, "XAUT"
    except Exception as e:
        print(f"[OKX] {e}")

    return _fetch_yahoo_gold(interval, limit)


def fetch_gold_data(interval: str = "5m") -> tuple[pd.DataFrame, str, bool, str]:
    if _is_forex_weekend_utc():
        ttl = 600.0
    elif TWELVE_DATA_KEY and not OANDA_API_KEY:
        # សន្សំ Credit Twelve Data (Basic 8: 800/ថ្ងៃ, 8/នាទី) ឱ្យប្រើបានពេញ ២៤ ម៉ោង
        # តម្លៃ Live រត់រាល់វិនាទីតាម Swissquote (0 Credit) មិនប៉ះពាល់ដល់ក្រាហ្វិក និង AI ឡើយ
        ttl = {
            "1m": 120.0,   # 2 នាទីម្តង
            "5m": 180.0,   # 3 នាទីម្តង
            "15m": 600.0,  # 10 នាទីម្តង
            "1h": 1800.0,  # 30 នាទីម្តង
            "4h": 3600.0,  # 1 ម៉ោងម្តង
            "1d": 7200.0,  # 2 ម៉ោងម្តង
        }.get(interval, 180.0)
    else:
        ttl = 8.0 if interval == "1m" else 15.0

    res = cache.get_or_fetch(f"gold_{interval}", ttl, lambda: _fetch_gold_raw(interval))
    if res is None:
        return pd.DataFrame(), "Reconnecting...", False, ""
    return res


# ==================== DUAL ECONOMIC CALENDAR ====================
def _fetch_finnhub_calendar(now_utc: datetime) -> list[dict]:
    if not FINNHUB_API_KEY:
        return []
    out = []
    try:
        d_from = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
        d_to = (now_utc + timedelta(days=5)).strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/calendar/economic?from={d_from}&to={d_to}&token={FINNHUB_API_KEY}"
        res = http_session.get(url, timeout=(2.5, 4.0))
        if res.status_code != 200:
            return []
        events = (res.json() or {}).get("economicCalendar") or []
        for ev in events:
            country = str(ev.get("country") or "").upper()
            impact = str(ev.get("impact") or "").lower()
            if country not in ("US", "USD") or impact not in ("high", "3"):
                continue
            t_str = str(ev.get("time") or "").strip()
            if not t_str:
                continue
            try:
                ev_dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                ev_dt = ev_dt.replace(tzinfo=timezone.utc) if ev_dt.tzinfo is None else ev_dt.astimezone(timezone.utc)
            except Exception:
                continue
            mins_until = (ev_dt - now_utc).total_seconds() / 60.0
            if mins_until < -30:
                continue
            tinfo = format_dual_time(ev_dt)
            out.append({
                "title": ev.get("event") or "USD High-Impact News",
                "mins_until": round(mins_until),
                "forecast": str(ev.get("estimate")) if ev.get("estimate") is not None else "--",
                "previous": str(ev.get("prev")) if ev.get("prev") is not None else "--",
                "time_kh": tinfo["kh"],
                "time_ny": tinfo["ny"],
                "time_utc": tinfo["utc"],
                "source": "Finnhub",
            })
    except Exception:
        pass
    return out


def _fetch_usd_news_raw() -> dict:
    now_utc = datetime.now(timezone.utc)
    upcoming, danger, danger_event = [], False, None
    seen_keys = set()

    def _add_item(item: dict):
        nonlocal danger, danger_event
        norm_title = re.sub(r"[^a-z0-9]+", "", item["title"].lower())[:24]
        key = (norm_title, item["time_utc"])
        if key in seen_keys:
            return
        seen_keys.add(key)
        if -30 <= item["mins_until"] <= 60:
            danger = True
            if danger_event is None or abs(item["mins_until"]) < abs(danger_event["mins_until"]):
                danger_event = item
        upcoming.append(item)

    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        res = http_session.get(url, timeout=(2.5, 4.5))
        if res.status_code == 200:
            for ev in res.json():
                if ev.get("country") != "USD" or ev.get("impact") != "High":
                    continue
                date_str = ev.get("date", "")
                if not date_str:
                    continue
                try:
                    ev_dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                except Exception:
                    continue
                mins_until = (ev_dt - now_utc).total_seconds() / 60.0
                if mins_until < -30:
                    continue
                tinfo = format_dual_time(ev_dt)
                _add_item({
                    "title": ev.get("title", "USD High-Impact News"),
                    "mins_until": round(mins_until),
                    "forecast": ev.get("forecast") or "--",
                    "previous": ev.get("previous") or "--",
                    "time_kh": tinfo["kh"],
                    "time_ny": tinfo["ny"],
                    "time_utc": tinfo["utc"],
                    "source": "ForexFactory",
                })
    except Exception as e:
        print(f"[CALENDAR:FF] {e}")

    for item in _fetch_finnhub_calendar(now_utc):
        _add_item(item)

    upcoming.sort(key=lambda x: x["mins_until"])
    f_time = format_dual_time(now_utc)
    return {
        "danger": danger,
        "danger_event": danger_event,
        "upcoming": upcoming[:4],
        "fetched_kh": f_time["kh"],
        "fetched_ny": f_time["ny"],
        "fetched_utc": f_time["utc"],
    }


def fetch_usd_news() -> dict:
    res = cache.get_or_fetch("usd_news", 900.0, _fetch_usd_news_raw)
    if res:
        return res
    f_time = format_dual_time(datetime.now(timezone.utc))
    return {
        "danger": False,
        "danger_event": None,
        "upcoming": [],
        "fetched_kh": f_time["kh"],
        "fetched_ny": f_time["ny"],
        "fetched_utc": f_time["utc"],
    }