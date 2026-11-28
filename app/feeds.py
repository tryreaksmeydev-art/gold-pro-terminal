import re
import time
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

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


# ==================== FOREX MARKET HOURS & OHLC SANITIZER ====================
def _is_forex_weekend_utc(now_utc: datetime | None = None) -> bool:
    """ទីផ្សារ Forex XAU/USD បិទពីថ្ងៃសុក្រ 22:00 UTC ដល់ថ្ងៃអាទិត្យ 22:00 UTC"""
    dt = now_utc or datetime.now(timezone.utc)
    wd = dt.weekday()  # 0=Mon .. 4=Fri, 5=Sat, 6=Sun
    return wd == 5 or (wd == 4 and dt.hour >= 22) or (wd == 6 and dt.hour < 22)


def _trim_weekend_candles(df: pd.DataFrame) -> pd.DataFrame:
    """កាត់ចោលទៀន Crypto ចុងសប្តាហ៍ (សុក្រ >= 22:00 UTC ដល់ អាទិត្យ < 22:00 UTC) ឱ្យត្រូវនឹង MT5"""
    if df is None or df.empty or "datetime" not in df.columns:
        return df
    dt_s = pd.to_datetime(df["datetime"], utc=True)
    wd = dt_s.dt.weekday
    hr = dt_s.dt.hour
    is_wknd = (wd == 5) | ((wd == 4) & (hr >= 22)) | ((wd == 6) & (hr < 22))
    trimmed = df.loc[~is_wknd].reset_index(drop=True)
    return trimmed if len(trimmed) > 5 else df


def _clean_ohlc_df(df: pd.DataFrame) -> pd.DataFrame:
    """សម្អាតទៀនឱ្យស្អាតកម្រិត Pro (លុបម៉ោងជាន់, កែ High/Low និងច្រោះ Bad Tick >$40)"""
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

    # ច្រោះ Bad Tick តែ ១ ទៀនដែលលោតខុសប្រក្រតីលើស $45 ធៀបនឹង Median 5 ទៀនជុំវិញ
    med_c = df["close"].rolling(5, center=True, min_periods=1).median()
    valid_mask = (df["close"] > 1000.0) & ((df["close"] - med_c).abs() <= 45.0)
    if valid_mask.sum() >= 5:
        df = df.loc[valid_mask].reset_index(drop=True)

    # ធានាថា High >= max(O,C) និង Low <= min(O,C) ជានិច្ច
    oc_max = np.maximum(df["open"].to_numpy(), df["close"].to_numpy())
    oc_min = np.minimum(df["open"].to_numpy(), df["close"].to_numpy())
    df["high"] = np.maximum(df["high"].to_numpy(), oc_max)
    df["low"] = np.minimum(df["low"].to_numpy(), oc_min)
    return df


# ==================== LIVE FOREX SPOT XAU/USD QUOTE ENGINE ====================
def _fetch_swissquote_xau() -> dict | None:
    try:
        url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
        res = http_session.get(url, timeout=(2.0, 3.0))
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and data:
                best = None
                for item in data:
                    prices = item.get("spreadProfilePrices") or []
                    for sp in prices:
                        bid = safe_float(sp.get("bid"), 0.0)
                        ask = safe_float(sp.get("ask"), 0.0)
                        if bid > 1000 and ask > 1000 and ask >= bid:
                            if sp.get("spreadProfile", "").lower() in ("standard", "prime"):
                                best = (bid, ask)
                                break
                            if best is None:
                                best = (bid, ask)
                    if best:
                        break
                if best:
                    bid, ask = best
                    closed = _is_forex_weekend_utc()
                    return {
                        "price": bid,  # ប្រើ Bid ដូច MT5 ដើម្បីឱ្យ Header និងក្រាហ្វិកស៊ីគ្នា ១០០%
                        "mid": safe_float((bid + ask) / 2),
                        "bid": bid,
                        "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)),
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
                    "price": bid, "mid": safe_float((bid + ask) / 2), "bid": bid, "ask": ask,
                    "spread": safe_float(max(0.0, ask - bid)), "is_forex": False,
                    "market_closed": _is_forex_weekend_utc(), "feed": "PAXG/USD",
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
                        "price": bid, "mid": safe_float((bid + ask) / 2), "bid": bid, "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)), "is_forex": False,
                        "market_closed": _is_forex_weekend_utc(), "feed": "XAUT/USD",
                    }
    except Exception:
        pass
    return None


def _fetch_live_quote_raw() -> dict | None:
    closed = _is_forex_weekend_utc()

    if OANDA_API_KEY:
        try:
            url = "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity=S5&count=1&price=BA"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.0, 3.0)).json()
            if "candles" in res and res["candles"]:
                c = res["candles"][-1]
                bid, ask = safe_float(c["bid"]["c"]), safe_float(c["ask"]["c"])
                if bid > 0 and ask > 0:
                    return {
                        "price": bid, "mid": safe_float((bid + ask) / 2), "bid": bid, "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)), "is_forex": True,
                        "market_closed": closed,
                        "feed": "Market Closed • OANDA XAU/USD" if closed else "OANDA Spot • XAU/USD",
                    }
        except Exception:
            pass

    sq = _fetch_swissquote_xau()
    if sq:
        return sq

    if TWELVE_DATA_KEY:
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=(2.0, 3.0)).json()
            if "price" in res:
                p = safe_float(res["price"])
                if p > 0:
                    return {
                        "price": p, "mid": safe_float(p + 0.12), "bid": p, "ask": safe_float(p + 0.25),
                        "spread": 0.25, "is_forex": True, "market_closed": closed,
                        "feed": "Market Closed • XAU/USD" if closed else "Twelve Data • XAU/USD",
                    }
        except Exception:
            pass

    # ថ្ងៃបិទផ្សារ៖ កុំយកតម្លៃ Crypto PAXG ដែលលោតខុសពី Forex មកបង្ហាញ បើអាចយកតម្លៃបិទថ្ងៃសុក្របាន
    if closed:
        try:
            df_c = yf.Ticker("GC=F").history(period="2d", interval="1h")
            if df_c is not None and not df_c.empty:
                p = safe_float(df_c["Close"].iloc[-1], 0.0)
                if p > 1000:
                    return {
                        "price": p, "mid": safe_float(p + 0.15), "bid": p, "ask": safe_float(p + 0.30),
                        "spread": 0.30, "is_forex": True, "market_closed": True,
                        "feed": "Market Closed • Gold Close",
                    }
        except Exception:
            pass

    bn = _fetch_binance_paxg_ticker()
    if bn:
        return bn

    return _fetch_okx_xaut_ticker()


def fetch_live_quote() -> dict | None:
    ttl = 60.0 if _is_forex_weekend_utc() else 1.8
    return cache.get_or_fetch("live_quote", ttl, _fetch_live_quote_raw)


# ==================== REAL-TIME ORDER FLOW ENGINE (BINANCE + OKX) ====================
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
    # ថ្ងៃបិទផ្សារ Forex៖ បង្កក Order Flow កុំឱ្យទិន្នន័យ Crypto បោកខួរក្បាល AI
    if _is_forex_weekend_utc():
        return {
            "buy_pct": 50.0, "sell_pct": 50.0,
            "buy_vol": 0.0, "sell_vol": 0.0, "trades_count": 0, "source": "CLOSED",
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
        "buy_pct": 50.0, "sell_pct": 50.0,
        "buy_vol": 0.0, "sell_vol": 0.0, "trades_count": 0, "source": "CANDLES",
    }


# ==================== THREAD-SAFE CROSS-SOURCE SANITY CHECK ====================
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


# ==================== CANDLE DATA FETCHERS (FOREX-ALIGNED + WEEKEND FREEZE) ====================
def _fetch_yahoo_gold(interval: str, limit: int) -> tuple[pd.DataFrame, str, bool, str] | None:
    try:
        ticker = yf.Ticker("GC=F")
        if interval == "4h":
            df = ticker.history(period="1mo", interval="1h")
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
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
                tcol: "datetime", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            if "volume" not in df.columns:
                df["volume"] = 0.0
            df = _clean_ohlc_df(df[["datetime", "open", "high", "low", "close", "volume"]]).tail(limit).reset_index(drop=True)
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

    if OANDA_API_KEY:
        try:
            gran = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}.get(interval, "M5")
            url = f"https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity={gran}&count={limit}&price=B"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.5, 4.5)).json()
            if "candles" in res and len(res["candles"]) > 5:
                rows = [{
                    "datetime": pd.to_datetime(c["time"], utc=True),
                    "open": float(c["bid"]["o"]), "high": float(c["bid"]["h"]),
                    "low": float(c["bid"]["l"]), "close": float(c["bid"]["c"]),
                    "volume": float(c.get("volume", 0.0)),
                } for c in res["candles"]]
                df = _clean_ohlc_df(pd.DataFrame(rows))
                return df, ("Market Closed • OANDA XAU/USD" if closed else "OANDA Spot • XAU/USD"), not closed, "OANDA"
        except Exception as e:
            print(f"[OANDA] {e}")

    if TWELVE_DATA_KEY:
        try:
            td_int = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}.get(interval, "5min")
            url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={td_int}&outputsize={limit}&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=(2.5, 4.5)).json()
            if "values" in res and len(res["values"]) > 5:
                df = pd.DataFrame(res["values"])
                df = _clean_ohlc_df(df)
                if len(df) > 5:
                    return df, ("Market Closed • XAU/USD" if closed else "Twelve Data Spot • XAU/USD"), not closed, "TWELVE"
        except Exception as e:
            print(f"[TwelveData] {e}")

    # ថ្ងៃបិទផ្សារចុងសប្តាហ៍៖ ផ្តល់អាទិភាព Yahoo GC=F មុន PAXG ដើម្បីឱ្យរូបរាងទៀនថ្ងៃសុក្រដូច MT5 ១០០%
    if closed:
        yf_res = _fetch_yahoo_gold(interval, limit)
        if yf_res is not None:
            return yf_res

    # Binance PAXG (ដកទៀនចុងសប្តាហ៍ចេញជានិច្ច ដើម្បីកុំឱ្យបែកទម្រង់ខុសពី Forex MT5)
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
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]),
                } for k in raw]
                df = _trim_weekend_candles(_clean_ohlc_df(pd.DataFrame(rows))).tail(limit).reset_index(drop=True)
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
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0.0,
                } for k in raw]
                df = _trim_weekend_candles(_clean_ohlc_df(pd.DataFrame(rows))).tail(limit).reset_index(drop=True)
                if len(df) > 5:
                    return df, ("Market Closed • Gold Close" if closed else "Gold Spot Live • XAUT/USD"), not closed, "XAUT"
    except Exception as e:
        print(f"[OKX] {e}")

    return _fetch_yahoo_gold(interval, limit)


def fetch_gold_data(interval: str = "5m") -> tuple[pd.DataFrame, str, bool, str]:
    if _is_forex_weekend_utc():
        ttl = 120.0
    else:
        ttl = 45.0 if (TWELVE_DATA_KEY and not OANDA_API_KEY) else (8.0 if interval == "1m" else 15.0)
    res = cache.get_or_fetch(f"gold_{interval}", ttl, lambda: _fetch_gold_raw(interval))
    if res is None:
        return pd.DataFrame(), "Reconnecting...", False, ""
    return res


# ==================== DUAL ECONOMIC CALENDAR (FOREXFACTORY + FINNHUB) ====================
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
        "danger": False, "danger_event": None, "upcoming": [],
        "fetched_kh": f_time["kh"], "fetched_ny": f_time["ny"], "fetched_utc": f_time["utc"],
    }