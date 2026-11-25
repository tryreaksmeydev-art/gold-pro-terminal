import os
import math
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import requests
from requests.adapters import HTTPAdapter
import pandas as pd
import numpy as np
import yfinance as yf
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ==================== HTTP SESSION POOL ====================
http_session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)
http_session.headers.update({"User-Agent": "GoldProTerminal/4.0"})

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()

VALID_INTERVALS = ("5m", "15m", "1h", "4h", "1d")
ALLOWED_PRESETS = {
    "entry", "risk_plan", "deep_scan", "mtf_check",
    "news_check", "inst_levels", "trend", "pattern", "volatility", "summary"
}

class ChatRequest(BaseModel):
    message: str
    interval: str = "15m"
    preset: str | None = None

# ==================== THREAD-SAFE CACHE WITH STALE FALLBACK ====================
class SafeCache:
    def __init__(self):
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def _get_key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            if key not in self._key_locks:
                self._key_locks[key] = threading.Lock()
            return self._key_locks[key]

    def get_or_fetch(self, key: str, ttl: float, fetcher):
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry and (now - entry[0]) < ttl:
                return entry[1]

        key_lock = self._get_key_lock(key)
        with key_lock:
            now = time.monotonic()
            with self._lock:
                entry = self._data.get(key)
                if entry and (now - entry[0]) < ttl:
                    return entry[1]
            try:
                value = fetcher()
                if value is not None:
                    with self._lock:
                        self._data[key] = (time.monotonic(), value)
                    return value
            except Exception:
                pass
            with self._lock:
                entry = self._data.get(key)
                return entry[1] if entry else None

cache = SafeCache()
mtf_executor = ThreadPoolExecutor(max_workers=4)

def safe_float(val, default: float = 0.0, digits: int = 2) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return round(f, digits)
    except Exception:
        return default

# ==================== SHARED WEBSOCKET MANAGER ====================
class ConnectionManager:
    def __init__(self):
        self.clients: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, interval: str):
        await ws.accept()
        async with self._lock:
            self.clients[ws] = interval

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.clients.pop(ws, None)

    async def snapshot(self) -> list[tuple[WebSocket, str]]:
        async with self._lock:
            return list(self.clients.items())

    async def count(self) -> int:
        async with self._lock:
            return len(self.clients)

manager = ConnectionManager()

# ==================== MARKET DATA FETCHERS ====================
def _fetch_gold_raw(interval: str) -> tuple[pd.DataFrame, str, bool] | None:
    if OANDA_API_KEY:
        try:
            gran = {"5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}.get(interval, "M15")
            url = f"https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity={gran}&count=150&price=M"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=8).json()
            if "candles" in res and len(res["candles"]) > 5:
                rows = [{
                    "datetime": pd.to_datetime(c["time"], utc=True),
                    "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
                    "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]),
                } for c in res["candles"]]
                return pd.DataFrame(rows), "OANDA Spot • XAU/USD", True
        except Exception:
            pass

    if TWELVE_DATA_KEY:
        try:
            td_int = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}.get(interval, "15min")
            url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={td_int}&outputsize=150&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=8).json()
            if "values" in res and len(res["values"]) > 5:
                df = pd.DataFrame(res["values"])
                df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna().sort_values("datetime").reset_index(drop=True)
                if len(df) > 5:
                    return df, "Twelve Data Spot • XAU/USD", True
        except Exception:
            pass

    try:
        bn_int = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval, "15m")
        url = f"https://data-api.binance.vision/api/v3/klines?symbol=PAXGUSDT&interval={bn_int}&limit=150"
        res = http_session.get(url, timeout=6)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) > 5:
                rows = [{
                    "datetime": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                } for k in raw]
                return pd.DataFrame(rows), "Gold Spot Live • PAXG/USD", True
    except Exception:
        pass

    try:
        ticker = yf.Ticker("GC=F")
        if interval == "4h":
            df = ticker.history(period="1mo", interval="1h")
            if df is not None and not df.empty:
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max", "Low": "min", "Close": "last"
                }).dropna()
        else:
            period = {"5m": "5d", "15m": "5d", "1h": "1mo", "1d": "6mo"}.get(interval, "5d")
            df = ticker.history(period=period, interval=interval)

        if df is not None and not df.empty:
            df = df.reset_index()
            tcol = "Datetime" if "Datetime" in df.columns else "Date"
            df = df.rename(columns={tcol: "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close"})
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df[["datetime", "open", "high", "low", "close"]].dropna().tail(150).reset_index(drop=True)
            if len(df) > 5:
                return df, "Yahoo Futures • GC=F", False
    except Exception:
        pass
    return None

def fetch_gold_data(interval: str = "15m") -> tuple[pd.DataFrame, str, bool]:
    ttl = 45.0 if (TWELVE_DATA_KEY and not OANDA_API_KEY) else 20.0
    res = cache.get_or_fetch(f"gold_{interval}", ttl, lambda: _fetch_gold_raw(interval))
    if res is None:
        return pd.DataFrame(), "Reconnecting...", False
    return res

def _fetch_live_quote_raw() -> dict | None:
    if OANDA_API_KEY:
        try:
            url = "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity=S5&count=1&price=BA"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=4).json()
            if "candles" in res and res["candles"]:
                c = res["candles"][-1]
                bid, ask = safe_float(c["bid"]["c"]), safe_float(c["ask"]["c"])
                return {"price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask, "spread": safe_float(ask - bid)}
        except Exception:
            pass

    if TWELVE_DATA_KEY:
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=4).json()
            if "price" in res:
                p = safe_float(res["price"])
                if p > 0:
                    return {"price": p, "bid": p, "ask": safe_float(p + 0.25), "spread": 0.25}
        except Exception:
            pass

    try:
        url = "https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=PAXGUSDT"
        res = http_session.get(url, timeout=4)
        if res.status_code == 200:
            d = res.json()
            bid, ask = safe_float(d.get("bidPrice")), safe_float(d.get("askPrice"))
            if bid > 0 and ask > 0:
                return {"price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask, "spread": safe_float(ask - bid)}
    except Exception:
        pass

    try:
        p = safe_float(yf.Ticker("GC=F").fast_info.get("lastPrice"), 0.0)
        if p > 0:
            return {"price": p, "bid": p, "ask": safe_float(p + 0.30), "spread": 0.30}
    except Exception:
        pass
    return None

def fetch_live_quote() -> dict | None:
    ttl = 12.0 if (TWELVE_DATA_KEY and not OANDA_API_KEY) else 1.2
    return cache.get_or_fetch("live_quote", ttl, _fetch_live_quote_raw)

def _fetch_usd_news_raw() -> dict:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    res = http_session.get(url, timeout=6)
    res.raise_for_status()
    events = res.json()
    now_utc = datetime.now(timezone.utc)
    upcoming, danger, danger_event = [], False, None

    for ev in events:
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
        item = {
            "title": ev.get("title", "USD High-Impact News"),
            "mins_until": round(mins_until),
            "forecast": ev.get("forecast") or "--",
            "previous": ev.get("previous") or "--",
        }
        if -30 <= mins_until <= 60:
            danger = True
            if danger_event is None:
                danger_event = item
        upcoming.append(item)

    upcoming.sort(key=lambda x: x["mins_until"])
    return {"danger": danger, "danger_event": danger_event, "upcoming": upcoming[:4]}

def fetch_usd_news() -> dict:
    res = cache.get_or_fetch("usd_news", 900.0, _fetch_usd_news_raw)
    return res or {"danger": False, "danger_event": None, "upcoming": []}

# ==================== PRECISION BUY/SELL + DEMAND ZONE ENGINE ====================
def generate_chart_signals(df: pd.DataFrame) -> tuple[list[dict], dict | None, dict]:
    """
    ប្រព័ន្ធចាប់សញ្ញាចំបង្គោលទៀន (Fast ATR Supertrend + Swing Reversal + Demand/Supply Zone)
    បញ្ជូនកូអរដោនេជាក់លាក់សម្រាប់គូរប្រអប់ BUY/SELL ធំៗ និងផ្ទៃ Demand Zone ពណ៌ខៀវ
    """
    n = len(df)
    if n < 20:
        return [], None, {"demand": None, "supply": None}

    open_p = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    rsi = df["rsi"].to_numpy(dtype=float)
    timestamps = [int(pd.Timestamp(t).timestamp()) for t in df["datetime"]]

    # Fast ATR Supertrend (mult=1.45 ដើម្បីចាប់ចំណុចបត់ក្បាលបានរហ័សដូច TradingView)
    mult = 1.45
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
    last_swing_high = float(np.max(high[:10]))
    last_swing_low = float(np.min(low[:10]))
    struct_bias = 0

    for i in range(14, n):
        ts = timestamps[i]
        cur_atr = max(float(atr[i]), 1.0)

        # រក Swing High / Swing Low (3-bar fractal)
        if i >= 18:
            if high[i - 2] == np.max(high[i - 4:i + 1]):
                last_swing_high = float(high[i - 2])
            if low[i - 2] == np.min(low[i - 4:i + 1]):
                last_swing_low = float(low[i - 2])

        # លក្ខខណ្ឌចាប់សញ្ញា BUY (Supertrend ឡើង ឬ Reversal Pin/Engulfing នៅបាត)
        is_st_buy = (direction[i] == 1 and direction[i - 1] == -1)
        is_rev_buy = (
            i - last_sig_idx >= 5
            and last_sig_side != "BUY"
            and rsi[i] < 40
            and low[i] <= np.min(low[max(0, i - 8):i + 1]) * 1.0003
            and close[i] > open_p[i]
            and (close[i] - low[i]) > (high[i] - low[i]) * 0.55
        )

        # លក្ខខណ្ឌចាប់សញ្ញា SELL (Supertrend ចុះ ឬ Reversal នៅកំពូល)
        is_st_sell = (direction[i] == -1 and direction[i - 1] == 1)
        is_rev_sell = (
            i - last_sig_idx >= 5
            and last_sig_side != "SELL"
            and rsi[i] > 60
            and high[i] >= np.max(high[max(0, i - 8):i + 1]) * 0.9997
            and close[i] < open_p[i]
            and (high[i] - close[i]) > (high[i] - low[i]) * 0.55
        )

        if (is_st_buy or is_rev_buy) and (i - last_sig_idx >= 3):
            entry_p = safe_float(close[i])
            sl_p = safe_float(min(low[i] - cur_atr * 0.7, entry_p - cur_atr * 1.3))
            risk = max(entry_p - sl_p, 1.0)
            tp1_p = safe_float(entry_p + risk * 2.0)
            tp2_p = safe_float(entry_p + risk * 3.0)
            signals.append({
                "time": ts, "price": safe_float(low[i]), "type": "BUY",
                "label": "BUY", "sublabel": "INV BUY" if is_rev_buy and not is_st_buy else ""
            })
            active_trade = {
                "side": "BUY", "time": ts, "entry": entry_p, "sl": sl_p,
                "tp1": tp1_p, "tp2": tp2_p, "rr": "2 : 1", "tp1_hit": False, "sl_hit": False
            }
            last_sig_idx = i
            last_sig_side = "BUY"
            continue

        if (is_st_sell or is_rev_sell) and (i - last_sig_idx >= 3):
            entry_p = safe_float(close[i])
            sl_p = safe_float(max(high[i] + cur_atr * 0.7, entry_p + cur_atr * 1.3))
            risk = max(sl_p - entry_p, 1.0)
            tp1_p = safe_float(entry_p - risk * 2.0)
            tp2_p = safe_float(entry_p - risk * 3.0)
            signals.append({
                "time": ts, "price": safe_float(high[i]), "type": "SELL",
                "label": "SELL", "sublabel": "INV SELL" if is_rev_sell and not is_st_sell else ""
            })
            active_trade = {
                "side": "SELL", "time": ts, "entry": entry_p, "sl": sl_p,
                "tp1": tp1_p, "tp2": tp2_p, "rr": "2 : 1", "tp1_hit": False, "sl_hit": False
            }
            last_sig_idx = i
            last_sig_side = "SELL"
            continue

        # តាមដាន TP1 Hit ឬ SL Hit
        if active_trade and not active_trade["tp1_hit"] and not active_trade["sl_hit"]:
            if active_trade["side"] == "BUY":
                if high[i] >= active_trade["tp1"]:
                    active_trade["tp1_hit"] = True
                    signals.append({"time": ts, "price": safe_float(high[i]), "type": "TP1", "label": "TP1", "sublabel": ""})
                    continue
                elif low[i] <= active_trade["sl"]:
                    active_trade["sl_hit"] = True
                    signals.append({"time": ts, "price": safe_float(low[i]), "type": "SL", "label": "SL", "sublabel": ""})
                    continue
            else:
                if low[i] <= active_trade["tp1"]:
                    active_trade["tp1_hit"] = True
                    signals.append({"time": ts, "price": safe_float(low[i]), "type": "TP1", "label": "TP1", "sublabel": ""})
                    continue
                elif high[i] >= active_trade["sl"]:
                    active_trade["sl_hit"] = True
                    signals.append({"time": ts, "price": safe_float(high[i]), "type": "SL", "label": "SL", "sublabel": ""})
                    continue

        # សញ្ញា CHoCH ពេលបំបែករចនាសម្ព័ន្ធ Swing
        if close[i] > last_swing_high and struct_bias != 1:
            struct_bias = 1
            signals.append({"time": ts, "price": safe_float(high[i]), "type": "CHoCH_UP", "label": "CHoCH", "sublabel": ""})
        elif close[i] < last_swing_low and struct_bias != -1:
            struct_bias = -1
            signals.append({"time": ts, "price": safe_float(low[i]), "type": "CHoCH_DN", "label": "CHoCH", "sublabel": ""})

    # គណនាតំបន់ Demand Zone (ប្រអប់ពណ៌ខៀវនៅបាត) និង Supply Zone (នៅកំពូល) ពី 45 ទៀនចុងក្រោយ
    lookback = min(45, n)
    rec_low = low[-lookback:]
    rec_high = high[-lookback:]
    rec_open = open_p[-lookback:]
    rec_close = close[-lookback:]
    last_atr = max(float(atr[-1]), 1.2)

    low_idx = int(np.argmin(rec_low))
    demand_bottom = safe_float(rec_low[low_idx])
    demand_top = safe_float(max(rec_open[low_idx], rec_close[low_idx], demand_bottom + last_atr * 0.9))

    high_idx = int(np.argmax(rec_high))
    supply_top = safe_float(rec_high[high_idx])
    supply_bottom = safe_float(min(rec_open[high_idx], rec_close[high_idx], supply_top - last_atr * 0.9))

    zones = {
        "demand": {"top": demand_top, "bottom": demand_bottom},
        "supply": {"top": supply_top, "bottom": supply_bottom}
    }

    return signals[-24:], active_trade, zones

# ==================== ANALYSIS ENGINE ====================
def get_market_session() -> dict:
    hour = datetime.now(timezone.utc).hour
    if 12 <= hour < 16:
        return {"name": "London + NY Overlap", "liquidity": "ខ្ពស់បំផុត (High Volatility)", "note": "ទីផ្សាររត់លឿនខ្លាំង ត្រូវប្រើ Stop Loss ជានិច្ច"}
    elif 7 <= hour < 12:
        return {"name": "London Session", "liquidity": "ខ្ពស់ (Active)", "note": "និន្នាការច្បាស់លាស់ ល្អសម្រាប់ជួញដូរតាម Trend"}
    elif 16 <= hour < 21:
        return {"name": "New York Session", "liquidity": "ខ្ពស់ (Active)", "note": "មានឥទ្ធិពលខ្លាំងពីព័ត៌មានសេដ្ឋកិច្ចអាមេរិក (USD)"}
    return {"name": "Asian Session", "liquidity": "មធ្យម-ទាប (Range)", "note": "តម្លៃភាគច្រើនរត់ក្នុងប្រអប់ Asian High/Low"}

def compute_tf_bias(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 25:
        return {"bias": "NEUTRAL", "rsi": 50.0}
    c = df["close"]
    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
    price = c.iloc[-1]
    delta = c.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = safe_float((100 - (100 / (1 + rs))).fillna(50).iloc[-1], 50.0, 1)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_hist = safe_float((ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()).iloc[-1], 0.0, 3)

    score = (1 if ema20 > ema50 else -1) + (1 if price > ema20 else -1) + (1 if macd_hist > 0 else -1)
    bias = "BUY" if score >= 2 else ("SELL" if score <= -2 else "NEUTRAL")
    return {"bias": bias, "rsi": rsi}

def _compute_mtf_raw() -> dict:
    tfs = [("15m", "15m"), ("1h", "1H"), ("4h", "4H"), ("1d", "1D")]
    futures = {code: mtf_executor.submit(fetch_gold_data, code) for code, _ in tfs}
    items, buy_cnt, sell_cnt = {}, 0, 0
    for code, label in tfs:
        df, _, _ = futures[code].result()
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
        "buy_count": buy_cnt, "sell_count": sell_cnt
    }

def get_mtf_confluence() -> dict:
    res = cache.get_or_fetch("mtf", 60.0, _compute_mtf_raw)
    return res or {"timeframes": [], "verdict": "SYNC", "aligned": "MIXED", "warning": None, "buy_count": 0, "sell_count": 0}

def _compute_inst_levels_raw() -> dict:
    pdh = pdl = asian_high = asian_low = None
    df_d, _, _ = fetch_gold_data("1d")
    if len(df_d) >= 2:
        pdh = safe_float(df_d.iloc[-2]["high"], 0.0) or None
        pdl = safe_float(df_d.iloc[-2]["low"], 0.0) or None

    df_15m, _, _ = fetch_gold_data("15m")
    if not df_15m.empty:
        dts = pd.to_datetime(df_15m["datetime"], utc=True)
        asian_df = df_15m[(dts.dt.hour >= 0) & (dts.dt.hour < 7)]
        if not asian_df.empty:
            latest_date = pd.to_datetime(asian_df["datetime"], utc=True).dt.date.max()
            day_asian = asian_df[pd.to_datetime(asian_df["datetime"], utc=True).dt.date == latest_date]
            if not day_asian.empty:
                asian_high = safe_float(day_asian["high"].max(), 0.0) or None
                asian_low = safe_float(day_asian["low"].min(), 0.0) or None
    return {"pdh": pdh, "pdl": pdl, "asian_high": asian_high, "asian_low": asian_low}

def get_institutional_levels() -> dict:
    res = cache.get_or_fetch("inst_levels", 60.0, _compute_inst_levels_raw)
    return res or {"pdh": None, "pdl": None, "asian_high": None, "asian_low": None}

def detect_candle_pattern(df: pd.DataFrame) -> dict:
    if len(df) < 3:
        return {"name": "Standard Candle", "bias": "NEUTRAL", "desc": "មិនទាន់មានទម្រង់ទៀនពិសេស"}
    c1, c2 = df.iloc[-2], df.iloc[-1]
    body = abs(c2["close"] - c2["open"])
    rng = max(c2["high"] - c2["low"], 0.01)
    upper_wick = c2["high"] - max(c2["open"], c2["close"])
    lower_wick = min(c2["open"], c2["close"]) - c2["low"]

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
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(50)

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    bb_mid = df["close"].rolling(window=20).mean().bfill()
    bb_std = df["close"].rolling(window=20).std().fillna(0)
    df["bb_upper"] = (bb_mid + 2 * bb_std).bfill()
    df["bb_lower"] = (bb_mid - 2 * bb_std).bfill()

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=10).mean().bfill()

    chart_signals, active_signal, zones = generate_chart_signals(df)

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    price = safe_float(latest["close"])
    prev_c = safe_float(prev["close"], price)
    change = safe_float(price - prev_c)
    change_pct = safe_float((change / prev_c) * 100 if prev_c else 0.0)
    rsi = safe_float(latest["rsi"], 50.0)
    ema20 = safe_float(latest["ema20"], price)
    ema50 = safe_float(latest["ema50"], price)
    macd_hist = safe_float(latest["macd_hist"], 0.0, 3)
    bb_upper = safe_float(latest["bb_upper"], price)
    bb_lower = safe_float(latest["bb_lower"], price)
    atr = max(safe_float(latest["atr"], 2.5), 1.0)

    recent = df.tail(30)
    support = safe_float(recent["low"].min(), price)
    resistance = safe_float(recent["high"].max(), price)
    pivot = safe_float((resistance + support + price) / 3, price)
    pattern = detect_candle_pattern(df)
    session = get_market_session()

    score, reasons = 0, []
    ema_status = "BUY" if ema20 > ema50 else "SELL"
    score += 2 if ema20 > ema50 else -2
    reasons.append(f"EMA 20 (${ema20}) {'នៅលើ' if ema20 > ema50 else 'នៅក្រោម'} EMA 50 (${ema50}) — {'និន្នាការឡើង' if ema20 > ema50 else 'និន្នាការចុះ'}")

    score += 1 if price > ema20 else -1
    macd_status = "BUY" if macd_hist > 0 else "SELL"
    score += 1 if macd_hist > 0 else -1
    reasons.append(f"MACD Histogram ({macd_hist:+.3f}) — {'កម្លាំងទិញមានប្រៀប' if macd_hist > 0 else 'កម្លាំងលក់មានប្រៀប'}")

    if rsi < 35:
        rsi_status, score = "BUY", score + 1
        reasons.append(f"RSI ({rsi}) នៅតំបន់ Oversold — មានឱកាសងើបឡើងវិញ")
    elif rsi > 65:
        rsi_status, score = "SELL", score - 1
        reasons.append(f"RSI ({rsi}) នៅតំបន់ Overbought — ប្រយ័ត្នការធ្លាក់ចុះ")
    else:
        rsi_status = "NEUTRAL"
        reasons.append(f"RSI ({rsi}) នៅតំបន់មានលំនឹង")

    bb_status = "SELL" if price >= bb_upper * 0.999 else ("BUY" if price <= bb_lower * 1.001 else "NEUTRAL")

    if score >= 3:
        signal, confidence = "STRONG BUY", 86
    elif score >= 1:
        signal, confidence = "BUY", 68
    elif score <= -3:
        signal, confidence = "STRONG SELL", 86
    elif score <= -1:
        signal, confidence = "SELL", 68
    else:
        signal, confidence = "NEUTRAL", 50

    if active_signal:
        entry_val = active_signal["entry"]
        sl = active_signal["sl"]
        tp1 = active_signal["tp1"]
        tp2 = active_signal["tp2"]
        rr_txt = active_signal["rr"]
        side_txt = active_signal["side"]
        e_low, e_high = safe_float(min(price, entry_val)), safe_float(max(price, entry_val))
        action_text = f"សញ្ញាក្រាហ្វិកសកម្ម៖ {side_txt} នៅ ${entry_val} (SL: ${sl} | TP1: ${tp1})"
    elif "BUY" in signal:
        entry_val = price
        e_low, e_high = safe_float(min(price, ema20)), price
        sl, tp1, tp2 = safe_float(price - atr * 1.4), safe_float(price + atr * 2.8), safe_float(price + atr * 4.0)
        rr_txt = "2 : 1"
        action_text = f"រង់ចាំចូល BUY ក្នុងតំបន់ ${e_low} - ${e_high} ដោយមានទៀនបញ្ជាក់"
    elif "SELL" in signal:
        entry_val = price
        e_low, e_high = price, safe_float(max(price, ema20))
        sl, tp1, tp2 = safe_float(price + atr * 1.4), safe_float(price - atr * 2.8), safe_float(price - atr * 4.0)
        rr_txt = "2 : 1"
        action_text = f"រង់ចាំចូល SELL ក្នុងតំបន់ ${e_low} - ${e_high} ពេលតម្លៃងើបប៉ះ EMA 20"
    else:
        entry_val = price
        e_low, e_high = support, resistance
        sl, tp1, tp2 = safe_float(support - atr), resistance, safe_float(resistance + atr)
        rr_txt = "1.5 : 1"
        action_text = "ទីផ្សារកំពុងរត់ទទឹង (Sideway) — គួររង់ចាំ Breakout សិន"

    matrix = [
        {"name": "EMA 20 / 50", "value": f"${ema20} / ${ema50}", "status": ema_status},
        {"name": "RSI (14)", "value": f"{rsi}", "status": rsi_status},
        {"name": "MACD Hist", "value": f"{macd_hist:+.3f}", "status": macd_status},
        {"name": "Bollinger", "value": f"${bb_lower} - ${bb_upper}", "status": bb_status},
        {"name": "Price Action", "value": pattern["name"], "status": pattern["bias"]},
        {"name": "Pivot Point", "value": f"${pivot}", "status": "BUY" if price >= pivot else "SELL"},
    ]

    candles, ema20_line, ema50_line = [], [], []
    last_ts = -1
    for _, row in df.iterrows():
        ts = int(pd.Timestamp(row["datetime"]).timestamp())
        if ts <= last_ts:
            continue
        last_ts = ts
        candles.append({
            "time": ts,
            "open": safe_float(row["open"]), "high": safe_float(row["high"]),
            "low": safe_float(row["low"]), "close": safe_float(row["close"]),
        })
        ema20_line.append({"time": ts, "value": safe_float(row["ema20"])})
        ema50_line.append({"time": ts, "value": safe_float(row["ema50"])})

    return {
        "price": price, "change": change, "change_pct": change_pct,
        "rsi": rsi, "ema20": ema20, "ema50": ema50, "macd_hist": macd_hist,
        "bb_upper": bb_upper, "bb_lower": bb_lower, "atr": atr,
        "support": support, "resistance": resistance, "pivot": pivot,
        "pattern": pattern, "session": session, "signal": signal,
        "confidence": confidence, "reasons": reasons, "matrix": matrix,
        "chart_signals": chart_signals, "active_signal": active_signal, "zones": zones,
        "setup": {
            "entry": entry_val, "entry_zone": f"${e_low} - ${e_high}",
            "sl": sl, "sl_dist": safe_float(abs(entry_val - sl), 2.5),
            "tp1": tp1, "tp2": tp2, "rr": rr_txt, "advice": action_text
        },
        "candles": candles, "ema20_line": ema20_line, "ema50_line": ema50_line,
    }

def build_full_payload_sync(interval: str) -> dict | None:
    df, source, is_spot = fetch_gold_data(interval)
    if df.empty:
        return None
    quote = fetch_live_quote()
    if quote and quote["price"]:
        live_p = quote["price"]
        df = df.copy()
        df.iloc[-1, df.columns.get_loc("close")] = live_p
        if live_p > df.iloc[-1]["high"]:
            df.iloc[-1, df.columns.get_loc("high")] = live_p
        if live_p < df.iloc[-1]["low"]:
            df.iloc[-1, df.columns.get_loc("low")] = live_p

    payload = calculate_indicators(df)
    payload["source"] = source
    payload["is_spot"] = is_spot
    payload["interval"] = interval
    payload["bid"] = quote["bid"] if quote else payload["price"]
    payload["ask"] = quote["ask"] if quote else safe_float(payload["price"] + 0.25)
    payload["spread"] = quote["spread"] if quote else 0.25
    payload["mtf"] = get_mtf_confluence()
    payload["news"] = fetch_usd_news()
    payload["inst_levels"] = get_institutional_levels()
    payload["type"] = "full"
    return payload

# ==================== SHARED BACKGROUND BROADCASTER ====================
async def broadcast_loop():
    cycle = 0
    while True:
        await asyncio.sleep(1.5)
        if await manager.count() == 0:
            continue
        cycle += 1
        clients = await manager.snapshot()
        if not clients:
            continue

        if cycle % 20 == 0:
            unique_tfs = set(tf for _, tf in clients)
            tf_payloads = {}
            for tf in unique_tfs:
                tf_payloads[tf] = await asyncio.to_thread(build_full_payload_sync, tf)
            for ws, tf in clients:
                data = tf_payloads.get(tf)
                if data:
                    try:
                        await ws.send_json(data)
                    except Exception:
                        await manager.disconnect(ws)
            continue

        quote = await asyncio.to_thread(fetch_live_quote)
        if quote:
            tick_msg = {
                "type": "tick", "price": quote["price"], "bid": quote["bid"],
                "ask": quote["ask"], "spread": quote["spread"], "ts": int(time.time())
            }
            for ws, _ in clients:
                try:
                    await ws.send_json(tick_msg)
                except Exception:
                    await manager.disconnect(ws)

        if cycle % 14 == 0:
            for ws, _ in clients:
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    await manager.disconnect(ws)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()
    mtf_executor.shutdown(wait=False)

app = FastAPI(title="Gold XAU/USD Pro Terminal", lifespan=lifespan)

# ==================== AI CHAT ENGINE ====================
def generate_ai_reply(preset: str, data: dict, interval: str, mtf: dict, news: dict, inst: dict) -> dict:
    p, sig, conf, s = data["price"], data["signal"], data["confidence"], data["setup"]
    rsi, atr = data["rsi"], data["atr"]
    piv, pat, sess = data["pivot"], data["pattern"], data["session"]

    if preset == "mtf_check":
        tf_summary = " | ".join([f"{t['label']}: **{t['bias']}**" for t in mtf["timeframes"]])
        lines = [
            f"លទ្ធផល 4 Timeframes៖ {tf_summary}",
            f"ការវាយតម្លៃរួម៖ **{mtf['verdict']}** (Buy: {mtf['buy_count']}/4 | Sell: {mtf['sell_count']}/4)",
            f"ការព្រមាន៖ **{mtf['warning']}**។" if mtf["warning"] else "Timeframe ធំ និងតូចកំពុងស្របទិសគ្នាល្អ អាចរង់ចាំចំណុចចូលតាមនិន្នាការមេបាន។"
        ]
        return {"title": "ផ្ទៀងផ្ទាត់ 4 Timeframes (MTF Confluence)", "badge": mtf["verdict"], "lines": lines}

    if preset == "news_check":
        if news["danger"] and news["danger_event"]:
            ev = news["danger_event"]
            return {
                "title": "ព្រមានព័ត៌មានសេដ្ឋកិច្ចអាមេរិក (USD RED FOLDER)", "badge": "DANGER",
                "lines": [
                    f"ព័ត៌មានជិតដល់៖ **{ev['title']}** (នៅសល់ ~**{ev['mins_until']} នាទី**)",
                    f"Forecast: **{ev['forecast']}** | Previous: **{ev['previous']}**",
                    "អនុសាសន៍៖ **ផ្អាកការចូល Order ថ្មីជាបន្ទាន់** ព្រោះមាសអាចលោតខ្លាំង និងរីក Spread។"
                ]
            }
        if news["upcoming"]:
            nxt = news["upcoming"][0]
            return {
                "title": "ប្រតិទិនព័ត៌មាន USD សំខាន់បន្ទាប់", "badge": "SAFE NOW",
                "lines": [
                    "បច្ចុប្បន្ន **គ្មានព័ត៌មានក្រហម USD** ក្នុងរយៈពេល ៦០ នាទីខាងមុខទេ។",
                    f"ព័ត៌មានធំបន្ទាប់៖ **{nxt['title']}** (ក្នុង ~**{round(nxt['mins_until']/60, 1)} ម៉ោង**)",
                    f"Forecast: **{nxt['forecast']}** | Previous: **{nxt['previous']}**"
                ]
            }
        return {"title": "ប្រតិទិនព័ត៌មាន USD", "badge": "CLEAR", "lines": ["គ្មានព័ត៌មាន USD កម្រិតក្រហមបន្ទាន់ក្នុងពេលនេះទេ។"]}

    if preset == "inst_levels":
        return {
            "title": "តំបន់តម្លៃស្ថាប័ន (Institutional Levels)", "badge": f"PRICE ${p}",
            "lines": [
                f"PDH (ខ្ពស់ម្សិលមិញ)៖ **${inst['pdh'] or '--'}** | PDL (ទាបម្សិលមិញ)៖ **${inst['pdl'] or '--'}**",
                f"Asian High៖ **${inst['asian_high'] or '--'}** | Asian Low៖ **${inst['asian_low'] or '--'}**",
                "យុទ្ធសាស្ត្រ៖ តាមដានការបោសសន្ទនីយភាព (Liquidity Sweep) លើតំបន់ទាំងនេះក្នុងវគ្គ London និង New York។"
            ]
        }

    if preset == "entry":
        return {
            "title": f"វិភាគចំណុចចូលជាក់ស្តែង ({interval})", "badge": f"{sig} • {conf}%",
            "lines": [
                f"តម្លៃបច្ចុប្បន្ន៖ **${p}** | តំបន់ចូល៖ **{s['entry_zone']}**",
                mtf["warning"] if mtf["warning"] else f"MTF Confluence៖ **{mtf['verdict']}**",
                "ប្រយ័ត្ន៖ មានព័ត៌មានក្រហម USD ជិតចេញ — គួរផ្អាកសិន!" if news["danger"] else "ព័ត៌មាន USD៖ គ្មានព័ត៌មានបន្ទាន់",
                f"ការណែនាំ៖ {s['advice']}។"
            ]
        }

    if preset == "risk_plan":
        return {
            "title": f"ផែនការ SL / TP និងដើមទុន ({interval})", "badge": f"R:R {s['rr']}",
            "lines": [
                f"ទិសដៅ៖ **{sig}** | Entry៖ **${s['entry']}**",
                f"Stop Loss៖ **${s['sl']}** (ចម្ងាយ ~${s['sl_dist']} ឬ {int(s['sl_dist'] * 10)} pips)",
                f"Take Profit 1៖ **${s['tp1']}** | Take Profit 2៖ **${s['tp2']}**",
                "គន្លឹះ Pro៖ ពេលតម្លៃដល់ TP1 គួរបិទពាក់កណ្តាល និងរំកិល SL មកចំណុចដើម។"
            ]
        }

    if preset == "deep_scan":
        return {
            "title": f"ស្កេនសូចនាករ 5-in-1 ({interval})", "badge": f"CONF {conf}%",
            "lines": [
                f"EMA20 (**${data['ema20']}**) vs EMA50 (**${data['ema50']}**)",
                f"RSI (14)៖ **{rsi}** | MACD Hist៖ **{data['macd_hist']:+.3f}**",
                f"Bollinger Bands៖ **${data['bb_lower']}** - **${data['bb_upper']}**",
                f"Pivot Point៖ **${piv}** | ATR (14)៖ **${atr}**"
            ]
        }

    if preset == "trend":
        tr = "និន្នាការឡើង (Uptrend)" if data["ema20"] > data["ema50"] else "និន្នាការចុះ (Downtrend)"
        return {
            "title": f"វិភាគនិន្នាការទីផ្សារ ({interval})", "badge": tr,
            "lines": [
                f"និន្នាការ {interval}៖ **{tr}** | MTF រួម៖ **{mtf['verdict']}**",
                f"តម្លៃ (**${p}**) នៅ **{'ខាងលើ' if p > data['ema20'] else 'ខាងក្រោម'}** EMA 20 (**${data['ema20']}**)",
                "គោលការណ៍ជួញដូរ៖ ចូល Order តែពេល Timeframe តូចស្របតាម 1H និង 4H។"
            ]
        }

    if preset == "pattern":
        return {
            "title": f"វិភាគទម្រង់ទៀន Price Action ({interval})", "badge": pat["name"],
            "lines": [
                f"ទម្រង់ទៀន៖ **{pat['name']}** ({pat['bias']})",
                f"អត្ថន័យ៖ {pat['desc']}។",
                f"PDH/PDL៖ **${inst['pdh'] or '--'}** / **${inst['pdl'] or '--'}**"
            ]
        }

    if preset == "volatility":
        vol = "ខ្ពស់ (High)" if atr >= 5.5 else ("មធ្យម (Moderate)" if atr >= 2.5 else "ទាប (Quiet)")
        return {
            "title": f"វាយតម្លៃហានិភ័យ និងវគ្គទីផ្សារ ({interval})", "badge": sess["name"],
            "lines": [
                f"វគ្គទីផ្សារ៖ **{sess['name']}** ({sess['liquidity']})",
                f"កម្រិតរំញ័រ ATR (14)៖ **${atr}** — ល្បឿន **{vol}**",
                f"ចំណាំ៖ {sess['note']}។"
            ]
        }

    return {
        "title": f"សង្ខេបទីផ្សារមាសពេញលេញ ({interval})", "badge": f"{sig} • {conf}%",
        "lines": [
            f"តម្លៃផ្ទាល់៖ **${p}** | វគ្គ៖ **{sess['name']}**",
            f"MTF 4-TF៖ **{mtf['verdict']}** | ព័ត៌មាន USD៖ **{'ប្រយ័ត្ន RED NEWS' if news['danger'] else 'ធម្មតា'}**",
            f"PDH/PDL៖ **${inst['pdh'] or '--'}** / **${inst['pdl'] or '--'}**",
            f"អនុសាសន៍៖ {s['advice']} (SL: **${s['sl']}** | TP1: **${s['tp1']}**)"
        ]
    }

# ==================== ROUTES ====================
@app.get("/health")
def health_check():
    return {"status": "ok", "ts": int(time.time())}

@app.get("/api/gold")
async def get_gold_rest(interval: str = Query("15m", pattern="^(5m|15m|1h|4h|1d)$")):
    payload = await asyncio.to_thread(build_full_payload_sync, interval)
    if not payload:
        return {"error": "កំពុងភ្ជាប់ទិន្នន័យមាសឡើងវិញ សូមរង់ចាំបន្តិច"}
    return payload

@app.post("/api/ai-chat")
async def ai_chat(req: ChatRequest):
    if req.preset not in ALLOWED_PRESETS:
        return {
            "rejected": True,
            "title": "សំណួរមិនទាន់មានក្នុងប្រព័ន្ធ",
            "badge": "NOTICE",
            "lines": [
                "សូមអភ័យទោស! សំណួរនេះមិនទាន់មានក្នុងប្រព័ន្ធវិភាគនៅឡើយទេ។",
                "សូមមេត្តាជ្រើសរើសសំណួរដែលមានស្រាប់ក្នុងបញ្ជីខាងក្រោម ដើម្បីទទួលបានការវិភាគជាក់ស្តែង និងសុក្រឹតបំផុត។"
            ]
        }
    interval = req.interval if req.interval in VALID_INTERVALS else "15m"
    payload = await asyncio.to_thread(build_full_payload_sync, interval)
    if not payload:
        return {"title": "ស្ថានភាពទិន្នន័យ", "badge": "RETRY", "lines": ["កំពុងទាញទិន្នន័យឡើងវិញ សូមសាកល្បងម្តងទៀត។"]}
    return generate_ai_reply(req.preset, payload, interval, payload["mtf"], payload["news"], payload["inst_levels"])

@app.websocket("/ws/gold/{interval}")
async def gold_websocket(websocket: WebSocket, interval: str):
    tf = interval if interval in VALID_INTERVALS else "15m"
    await manager.connect(websocket, tf)
    try:
        payload = await asyncio.to_thread(build_full_payload_sync, tf)
        if payload:
            await websocket.send_json(payload)
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await manager.disconnect(websocket)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_home():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)