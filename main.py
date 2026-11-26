import os
import math
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import requests
from requests.adapters import HTTPAdapter
import pandas as pd
import numpy as np
import yfinance as yf
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from brain_service import brain_service

# ==================== HTTP SESSION POOL ====================
http_session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)
http_session.headers.update({"User-Agent": "TriPisalGoldTerminal/8.4"})

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()

VALID_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
ALLOWED_PRESETS = {
    "brain_v8", "entry", "risk_plan", "deep_scan", "mtf_check",
    "news_check", "inst_levels", "trend", "pattern", "volatility", "summary"
}


class ChatRequest(BaseModel):
    message: str
    interval: str = "5m"
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
            except Exception as e:
                print(f"[CACHE:{key}] fetch error: {e}")
            with self._lock:
                entry = self._data.get(key)
                return entry[1] if entry else None


cache = SafeCache()
# កំណត់ 2 workers ដើម្បីសន្សំ CPU/RAM លើ Render Free (0.1 CPU / 512 MB RAM)
mtf_executor = ThreadPoolExecutor(max_workers=2)


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

    async def safe_send(self, ws: WebSocket, payload: dict, timeout: float = 3.0) -> bool:
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=timeout)
            return True
        except Exception:
            await self.disconnect(ws)
            return False


manager = ConnectionManager()


# ==================== REAL FOREX SPOT XAU/USD QUOTE ENGINE ====================
def _fetch_swissquote_xau() -> dict | None:
    """ទាញតម្លៃមាស Forex Spot XAU/USD ផ្ទាល់ពីធនាគារ Swissquote (ឥតគិតថ្លៃ មិនបាច់ API Key)"""
    try:
        url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
        res = http_session.get(url, timeout=(2.5, 3.5))
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and data:
                best = None
                for item in data:
                    prices = item.get("spreadProfilePrices") or []
                    for sp in prices:
                        bid = safe_float(sp.get("bid"), 0.0)
                        ask = safe_float(sp.get("ask"), 0.0)
                        if bid > 1000 and ask > 1000:
                            if sp.get("spreadProfile", "").lower() in ("standard", "prime"):
                                best = (bid, ask)
                                break
                            if best is None:
                                best = (bid, ask)
                    if best:
                        break
                if best:
                    bid, ask = best
                    return {
                        "price": safe_float((bid + ask) / 2),
                        "bid": bid,
                        "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)),
                        "is_forex": True,
                        "feed": "Forex Spot Live • XAU/USD"
                    }
    except Exception:
        pass
    return None


def _fetch_live_quote_raw() -> dict | None:
    if OANDA_API_KEY:
        try:
            url = "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity=S5&count=1&price=BA"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.5, 3.5)).json()
            if "candles" in res and res["candles"]:
                c = res["candles"][-1]
                bid, ask = safe_float(c["bid"]["c"]), safe_float(c["ask"]["c"])
                if bid > 0 and ask > 0:
                    return {
                        "price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)), "is_forex": True, "feed": "OANDA Spot • XAU/USD"
                    }
        except Exception:
            pass

    sq = _fetch_swissquote_xau()
    if sq:
        return sq

    if TWELVE_DATA_KEY:
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=(2.5, 3.5)).json()
            if "price" in res:
                p = safe_float(res["price"])
                if p > 0:
                    return {"price": p, "bid": p, "ask": safe_float(p + 0.25), "spread": 0.25, "is_forex": True, "feed": "Twelve Data • XAU/USD"}
        except Exception:
            pass

    try:
        url = "https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=PAXGUSDT"
        res = http_session.get(url, timeout=(2.5, 3.5))
        if res.status_code == 200:
            d = res.json()
            bid, ask = safe_float(d.get("bidPrice")), safe_float(d.get("askPrice"))
            if bid > 0 and ask > 0:
                return {"price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask, "spread": safe_float(max(0.0, ask - bid)), "is_forex": False, "feed": "PAXG/USD"}
    except Exception:
        pass

    try:
        url = "https://www.okx.com/api/v5/market/ticker?instId=XAUT-USDT"
        res = http_session.get(url, timeout=(2.5, 3.5))
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                bid, ask = safe_float(data[0].get("bidPx")), safe_float(data[0].get("askPx"))
                if bid > 0 and ask > 0:
                    return {"price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask, "spread": safe_float(max(0.0, ask - bid)), "is_forex": False, "feed": "XAUT/USD"}
    except Exception:
        pass
    return None


def fetch_live_quote() -> dict | None:
    return cache.get_or_fetch("live_quote", 2.0, _fetch_live_quote_raw)


# ==================== CANDLE DATA FETCHERS ====================
def _fetch_gold_raw(interval: str) -> tuple[pd.DataFrame, str, bool, str] | None:
    limit = 260 if interval == "1h" else 160

    if OANDA_API_KEY:
        try:
            gran = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}.get(interval, "M5")
            url = f"https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity={gran}&count={limit}&price=M"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(3, 5)).json()
            if "candles" in res and len(res["candles"]) > 5:
                rows = [{
                    "datetime": pd.to_datetime(c["time"], utc=True),
                    "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
                    "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]),
                    "volume": float(c.get("volume", 0.0)),
                } for c in res["candles"]]
                return pd.DataFrame(rows), "OANDA Spot • XAU/USD", True, "OANDA"
        except Exception as e:
            print(f"[OANDA] {e}")

    if TWELVE_DATA_KEY:
        try:
            td_int = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}.get(interval, "5min")
            url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={td_int}&outputsize={limit}&apikey={TWELVE_DATA_KEY}"
            res = http_session.get(url, timeout=(3, 5)).json()
            if "values" in res and len(res["values"]) > 5:
                df = pd.DataFrame(res["values"])
                df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df["volume"] = 0.0
                df = df.dropna().sort_values("datetime").reset_index(drop=True)
                if len(df) > 5:
                    return df, "Twelve Data Spot • XAU/USD", True, "TWELVE"
        except Exception as e:
            print(f"[TwelveData] {e}")

    try:
        bn_int = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval, "5m")
        url = f"https://data-api.binance.vision/api/v3/klines?symbol=PAXGUSDT&interval={bn_int}&limit={limit}"
        res = http_session.get(url, timeout=(3, 5))
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list) and len(raw) > 5:
                rows = [{
                    "datetime": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]),
                } for k in raw]
                return pd.DataFrame(rows), "Gold Spot Live • PAXG/USD", True, "PAXG"
    except Exception as e:
        print(f"[Binance] {e}")

    try:
        okx_bar = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}.get(interval, "5m")
        okx_lim = min(limit, 300)
        url = f"https://www.okx.com/api/v5/market/candles?instId=XAUT-USDT&bar={okx_bar}&limit={okx_lim}"
        res = http_session.get(url, timeout=(3, 5))
        if res.status_code == 200:
            raw = res.json().get("data", [])
            if isinstance(raw, list) and len(raw) > 5:
                rows = [{
                    "datetime": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0.0,
                } for k in raw]
                df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
                return df, "Gold Spot Live • XAUT/USD", True, "XAUT"
    except Exception as e:
        print(f"[OKX] {e}")

    try:
        ticker = yf.Ticker("GC=F")
        if interval == "4h":
            df = ticker.history(period="1mo", interval="1h")
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
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
                "Low": "low", "Close": "close", "Volume": "volume"
            })
            if "volume" not in df.columns:
                df["volume"] = 0.0
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df[["datetime", "open", "high", "low", "close", "volume"]].dropna().tail(limit).reset_index(drop=True)
            if len(df) > 5:
                return df, "Yahoo Futures • GC=F", False, "YAHOO"
    except Exception as e:
        print(f"[Yahoo] {e}")
    return None


def fetch_gold_data(interval: str = "5m") -> tuple[pd.DataFrame, str, bool, str]:
    ttl = 45.0 if (TWELVE_DATA_KEY and not OANDA_API_KEY) else (10.0 if interval == "1m" else 18.0)
    res = cache.get_or_fetch(f"gold_{interval}", ttl, lambda: _fetch_gold_raw(interval))
    if res is None:
        return pd.DataFrame(), "Reconnecting...", False, ""
    return res


def _fetch_usd_news_raw() -> dict:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    res = http_session.get(url, timeout=(3, 5))
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


# ==================== SMART MONEY CONCEPTS (SMC) STRUCTURE ENGINE ====================
def _dedupe_blocks(blocks: list[dict], min_gap: float, max_items: int) -> list[dict]:
    """ការពារកុំឲ្យប្រអប់ Order Block ត្រួតជាន់គ្នា រើសយកតែតំបន់ស្អាតៗ"""
    selected: list[dict] = []
    for b in blocks:
        mid = (b["top"] + b["bottom"]) / 2.0
        if all(abs(mid - ((s["top"] + s["bottom"]) / 2.0)) >= min_gap for s in selected):
            selected.append(b)
            if len(selected) >= max_items:
                break
    return selected


def compute_smc_structure(df: pd.DataFrame) -> dict:
    """
    ម៉ាស៊ីន Smart Money Concepts (SMC):
    - Multi-level Demand Order Blocks / FVG (ប្រអប់ខៀវ) និង Supply Order Blocks (ប្រអប់ក្រហម)
    - បន្ទាត់ Weak High / Strong High និង Strong Low / Weak Low
    - បន្ទាត់ដាច់ៗ BOS (Break of Structure) និង CHoCH (Change of Character)
    """
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

    # ១. រក Swing Highs / Swing Lows (Pivot window = 3)
    w = 3
    last_sh_idx, last_sh_val = 0, float(high[0])
    last_sl_idx, last_sl_val = 0, float(low[0])
    sh_broken, sl_broken = False, False
    trend = 0  # 1 = Bullish, -1 = Bearish

    breaks: list[dict] = []
    raw_demand: list[dict] = []
    raw_supply: list[dict] = []

    for i in range(w, n):
        # ពិនិត្យ Pivot នៅទីតាំង p = i - w
        p = i - w
        if p >= w:
            if high[p] == np.max(high[p - w:p + w + 1]):
                last_sh_idx, last_sh_val = p, float(high[p])
                sh_broken = False
            if low[p] == np.min(low[p - w:p + w + 1]):
                last_sl_idx, last_sl_val = p, float(low[p])
                sl_broken = False

        # Break Above Swing High -> BOS UP ឬ CHoCH UP + បង្កើត Demand OB
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
            # រកទៀនក្រហមចុងក្រោយមុនការហក់ឡើង (Bullish Order Block)
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
                "top": ob_top, "bottom": ob_bot, "label": "OB"
            })

        # Break Below Swing Low -> BOS DN ឬ CHoCH DN + បង្កើត Supply OB
        if not sl_broken and close[i] < last_sl_val and (i - last_sl_idx) >= 2:
            sl_broken = True
            is_choch = (trend == 1)
            trend = -1
            breaks.append({
                "start_time": timestamps[last_sl_idx],
                "end_time": timestamps[i],
                "price": safe_float(last_sl_val),
                "type": "CHoCH_DN" if is_choch else "BOS_DN",
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
                "top": ob_top, "bottom": ob_bot, "label": "OB"
            })

        # ២. បន្ថែម Fair Value Gaps (FVG) ដែលមានទំហំសំខាន់
        if i >= 2:
            gap_up = low[i] - high[i - 2]
            if gap_up >= last_atr * 0.35:
                raw_demand.append({
                    "idx": i - 1, "start_time": timestamps[i - 1],
                    "top": safe_float(low[i]), "bottom": safe_float(high[i - 2]), "label": "FVG"
                })
            gap_dn = low[i - 2] - high[i]
            if gap_dn >= last_atr * 0.35:
                raw_supply.append({
                    "idx": i - 1, "start_time": timestamps[i - 1],
                    "top": safe_float(low[i - 2]), "bottom": safe_float(high[i]), "label": "FVG"
                })

    # ៣. ចម្រាញ់យកតែ Order Block / FVG ដែលមិនទាន់បែក (Unmitigated)
    active_demand = []
    for b in reversed(raw_demand):
        idx = b["idx"]
        # មិនទាន់ត្រូវទៀនបិទទម្លុះចុះក្រោម និងស្ថិតនៅក្រោមឬកៀកតម្លៃបច្ចុប្បន្ន
        if b["bottom"] <= cur_price + last_atr * 0.4:
            after_closes = close[idx + 1:]
            if len(after_closes) == 0 or float(np.min(after_closes)) >= b["bottom"] - last_atr * 0.15:
                active_demand.append({
                    "start_time": b["start_time"], "top": b["top"],
                    "bottom": b["bottom"], "label": b["label"]
                })

    active_supply = []
    for b in reversed(raw_supply):
        idx = b["idx"]
        if b["top"] >= cur_price - last_atr * 0.4:
            after_closes = close[idx + 1:]
            if len(after_closes) == 0 or float(np.max(after_closes)) <= b["top"] + last_atr * 0.15:
                active_supply.append({
                    "start_time": b["start_time"], "top": b["top"],
                    "bottom": b["bottom"], "label": b["label"]
                })

    demand_blocks = _dedupe_blocks(active_demand, min_gap=last_atr * 1.1, max_items=4)
    supply_blocks = _dedupe_blocks(active_supply, min_gap=last_atr * 1.1, max_items=3)

    # ៤. កំណត់បន្ទាត់ Weak High / Strong High និង Strong Low / Weak Low (ដូច TradingView SMC)
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


# ==================== PRECISION BUY/SELL + DEMAND ZONE ENGINE ====================
def generate_chart_signals(df: pd.DataFrame) -> tuple[list[dict], dict | None, dict]:
    n = len(df)
    if n < 20:
        return [], None, {"demand": None, "supply": None}

    open_p = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = np.nan_to_num(df["atr"].to_numpy(dtype=float), nan=1.0, posinf=1.0, neginf=1.0)
    rsi = np.nan_to_num(df["rsi"].to_numpy(dtype=float), nan=50.0, posinf=50.0, neginf=50.0)
    timestamps = [int(pd.Timestamp(t).timestamp()) for t in df["datetime"]]

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

    for i in range(14, n):
        ts = timestamps[i]
        cur_atr = max(float(atr[i]), 0.8)

        bar_range = max(high[i] - low[i], 0.01)
        is_st_buy = (direction[i] == 1 and direction[i - 1] == -1)
        is_rev_buy = (
            i - last_sig_idx >= 5
            and last_sig_side != "BUY"
            and rsi[i] < 40
            and low[i] <= np.min(low[max(0, i - 8):i + 1]) * 1.0003
            and close[i] > open_p[i]
            and (close[i] - low[i]) > bar_range * 0.55
        )

        is_st_sell = (direction[i] == -1 and direction[i - 1] == 1)
        is_rev_sell = (
            i - last_sig_idx >= 5
            and last_sig_side != "SELL"
            and rsi[i] > 60
            and high[i] >= np.max(high[max(0, i - 8):i + 1]) * 0.9997
            and close[i] < open_p[i]
            and (high[i] - close[i]) > bar_range * 0.55
        )

        if (is_st_buy or is_rev_buy) and (i - last_sig_idx >= 3):
            entry_p = safe_float(close[i])
            sl_p = safe_float(min(low[i] - cur_atr * 0.7, entry_p - cur_atr * 1.3))
            risk = max(entry_p - sl_p, 0.8)
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
            risk = max(sl_p - entry_p, 0.8)
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

    lookback = min(45, n)
    rec_low = low[-lookback:]
    rec_high = high[-lookback:]
    rec_open = open_p[-lookback:]
    rec_close = close[-lookback:]
    last_atr = max(float(atr[-1]), 1.0)

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
        try:
            df, _, _, _ = futures[code].result(timeout=10)
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
        "buy_count": buy_cnt, "sell_count": sell_cnt
    }


def get_mtf_confluence() -> dict:
    res = cache.get_or_fetch("mtf", 60.0, _compute_mtf_raw)
    return res or {"timeframes": [], "verdict": "SYNC", "aligned": "MIXED", "warning": None, "buy_count": 0, "sell_count": 0}


def _compute_inst_levels_raw() -> dict:
    pdh = pdl = asian_high = asian_low = None
    df_d, _, _, _ = fetch_gold_data("1d")
    if len(df_d) >= 2:
        pdh = safe_float(df_d.iloc[-2]["high"], 0.0) or None
        pdl = safe_float(df_d.iloc[-2]["low"], 0.0) or None

    df_15m, _, _, _ = fetch_gold_data("15m")
    if not df_15m.empty:
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
    return {
        k: (safe_float(v + basis) if v is not None else None)
        for k, v in raw.items()
    }


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
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

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
    df["atr"] = tr.rolling(window=14).mean().bfill().fillna(2.0)

    # គណនាទាំង Signal ទៀន និង Smart Money Concepts (OB, FVG, Weak High, Strong Low, BOS, CHoCH)
    df_plot = df.tail(150).reset_index(drop=True)
    chart_signals, active_signal, zones = generate_chart_signals(df_plot)
    smc = compute_smc_structure(df_plot)

    latest = df_plot.iloc[-1]
    prev = df_plot.iloc[-2] if len(df_plot) > 1 else latest

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
    atr = max(safe_float(latest["atr"], 2.0), 0.8)

    recent = df_plot.tail(30)
    support = safe_float(recent["low"].min(), price)
    resistance = safe_float(recent["high"].max(), price)
    pivot = safe_float((resistance + support + price) / 3, price)
    pattern = detect_candle_pattern(df_plot)
    session = get_market_session()

    score, reasons = 0, []
    ema_status = "BUY" if ema20 > ema50 else "SELL"
    score += 2 if ema20 > ema50 else -2
    reasons.append(f"EMA 20 (${ema20}) {'នៅលើ' if ema20 > ema50 else 'នៅក្រោម'} EMA 50 (${ema50}) — {'និន្នាការឡើង' if ema20 > ema50 else 'និន្នាការចុះ'}")

    score += 1 if price > ema20 else -1
    macd_status = "BUY" if macd_hist > 0 else "SELL"
    score += 1 if macd_hist > 0 else -1
    reasons.append(f"MACD Histogram ({macd_hist:+.3f}) — {'កម្លាំងទិញមានប្រៀប' if macd_hist > 0 else 'កម្លាំងលក់មានប្រៀប'}")

    if smc["bias"] == "BULLISH":
        score += 1
    elif smc["bias"] == "BEARISH":
        score -= 1

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
        {"name": "SMC Structure", "value": smc["bias"], "status": "BUY" if smc["bias"] == "BULLISH" else ("SELL" if smc["bias"] == "BEARISH" else "NEUTRAL")},
        {"name": "Price Action", "value": pattern["name"], "status": pattern["bias"]},
        {"name": "Pivot Point", "value": f"${pivot}", "status": "BUY" if price >= pivot else "SELL"},
    ]

    candles, ema20_line, ema50_line = [], [], []
    last_ts = -1
    for _, row in df_plot.iterrows():
        ts = int(pd.Timestamp(row["datetime"]).timestamp())
        if ts <= last_ts:
            continue
        last_ts = ts
        candles.append({
            "time": ts,
            "open": safe_float(row["open"]), "high": safe_float(row["high"]),
            "low": safe_float(row["low"]), "close": safe_float(row["close"]),
        })
        ema20_line.append({"time": ts, "value": safe_float(row["ema20"], price)})
        ema50_line.append({"time": ts, "value": safe_float(row["ema50"], price)})

    return {
        "price": price, "change": change, "change_pct": change_pct,
        "rsi": rsi, "ema20": ema20, "ema50": ema50, "macd_hist": macd_hist,
        "bb_upper": bb_upper, "bb_lower": bb_lower, "atr": atr,
        "support": support, "resistance": resistance, "pivot": pivot,
        "pattern": pattern, "session": session, "signal": signal,
        "confidence": confidence, "reasons": reasons, "matrix": matrix,
        "chart_signals": chart_signals, "active_signal": active_signal,
        "zones": zones, "smc": smc,
        "setup": {
            "entry": entry_val, "entry_zone": f"${e_low} - ${e_high}",
            "sl": sl, "sl_dist": safe_float(abs(entry_val - sl), 2.5),
            "tp1": tp1, "tp2": tp2, "rr": rr_txt, "advice": action_text
        },
        "candles": candles, "ema20_line": ema20_line, "ema50_line": ema50_line,
    }


def _evaluate_brain_v8(payload: dict, basis: float = 0.0) -> dict:
    """ទាញទៀន 1H (260 ទៀន) តម្រឹមតម្លៃ Forex Spot រួចបញ្ជូនទៅខួរក្បាល V8"""
    df_1h, _, _, raw_1h = fetch_gold_data("1h")
    if df_1h is not None and not df_1h.empty:
        df_1h = df_1h.copy()
        if basis != 0.0 and raw_1h not in ("OANDA", "TWELVE"):
            for col in ("open", "high", "low", "close"):
                df_1h[col] = df_1h[col] + basis
    return brain_service.evaluate(df_1h, payload, session=http_session)


def attach_extras_sync(payload: dict) -> dict:
    """បន្ថែម MTF, USD News, Institutional Levels, និងខួរក្បាល V8 + ព័ត៌មានសង្គ្រាម/ពិភពលោក"""
    out = dict(payload)
    basis = float(out.get("basis_offset") or 0.0)
    out["mtf"] = get_mtf_confluence()
    out["news"] = fetch_usd_news()
    out["inst_levels"] = get_institutional_levels(basis)
    out["brain"] = _evaluate_brain_v8(out, basis=basis)
    return out


def build_full_payload_sync(interval: str, include_extras: bool = True) -> dict | None:
    df, source, is_spot, raw_feed = fetch_gold_data(interval)
    if df.empty or len(df) < 5:
        return None
    quote = fetch_live_quote()
    df = df.copy()
    basis = 0.0

    # Auto-Calibrate ទៀនឲ្យត្រូវតម្លៃ Forex Spot XAU/USD ពិត (ដូច TradingView/MT5)
    if quote and quote["price"] > 0:
        live_p = quote["price"]
        raw_last = float(df.iloc[-1]["close"])
        if quote.get("is_forex") and raw_feed not in ("OANDA", "TWELVE"):
            cand_basis = live_p - raw_last
            if abs(cand_basis) < 120:
                basis = cand_basis
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] + basis
            source = quote.get("feed", "Forex Spot Live • XAU/USD")
            is_spot = True

        df.iloc[-1, df.columns.get_loc("close")] = live_p
        if live_p > df.iloc[-1]["high"]:
            df.iloc[-1, df.columns.get_loc("high")] = live_p
        if live_p < df.iloc[-1]["low"]:
            df.iloc[-1, df.columns.get_loc("low")] = live_p

    payload = calculate_indicators(df)
    payload["source"] = source
    payload["is_spot"] = is_spot
    payload["is_forex"] = bool(quote.get("is_forex")) if quote else False
    payload["raw_feed"] = raw_feed
    payload["basis_offset"] = safe_float(basis, 0.0, 4)
    payload["interval"] = interval
    payload["bid"] = quote["bid"] if quote else payload["price"]
    payload["ask"] = quote["ask"] if quote else safe_float(payload["price"] + 0.25)
    payload["spread"] = quote["spread"] if quote else 0.25
    payload["type"] = "full"

    if include_extras:
        payload = attach_extras_sync(payload)
    return payload


# ==================== RENDER-SAFE HYBRID TURBO BROADCASTER ====================
async def broadcast_loop():
    cycle = 0
    while True:
        try:
            await asyncio.sleep(2.5)
            if await manager.count() == 0:
                continue
            cycle += 1
            clients = await manager.snapshot()
            if not clients:
                continue

            # អាប់ដេតទៀន SMC សញ្ញា និងខួរក្បាល V8 រៀងរាល់ ៣០ វិនាទី
            if cycle % 12 == 0:
                unique_tfs = set(tf for _, tf in clients)
                tf_payloads = {}
                for tf in unique_tfs:
                    tf_payloads[tf] = await asyncio.to_thread(build_full_payload_sync, tf, True)
                for ws, tf in clients:
                    data = tf_payloads.get(tf)
                    if data:
                        await manager.safe_send(ws, data)
                continue

            quote = await asyncio.to_thread(fetch_live_quote)
            if quote:
                tick_msg = {
                    "type": "tick",
                    "price": quote["price"],
                    "bid": quote["bid"],
                    "ask": quote["ask"],
                    "spread": quote["spread"],
                    "is_forex": bool(quote.get("is_forex")),
                    "ts": int(time.time())
                }
                for ws, _ in clients:
                    await manager.safe_send(ws, tick_msg)

            if cycle % 10 == 0:
                for ws, _ in clients:
                    await manager.safe_send(ws, {"type": "ping"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[BROADCAST] recovered from error: {e}")
            await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broadcast_loop())
    try:
        yield
    finally:
        task.cancel()
        mtf_executor.shutdown(wait=False)
        http_session.close()


app = FastAPI(title="TRI PISAL • Gold XAU/USD Pro Terminal (V8 SMC AI)", lifespan=lifespan)


# ==================== AI CHAT ENGINE (INTEGRATED WITH V8 + SMC + WAR NEWS) ====================
def generate_ai_reply(
    preset: str, data: dict, interval: str,
    mtf: dict, news: dict, inst: dict, brain: dict | None = None
) -> dict:
    p, sig, conf, s = data["price"], data["signal"], data["confidence"], data["setup"]
    rsi, atr = data["rsi"], data["atr"]
    piv, pat, sess = data["pivot"], data["pattern"], data["session"]
    brain = brain or {}
    gnews = brain.get("global_news") or {}
    wnews = brain.get("war_news") or {}
    macro = brain.get("macro") or {}

    if preset == "brain_v8":
        rank_txt = f"{brain['ai_rank_pct']}%" if brain.get("ai_rank_pct") is not None else "--"
        lines = [
            f"សេចក្តីសម្រេច V8៖ **{brain.get('action_km', 'កំពុងវិភាគ...')}** (AI Rank: **{rank_txt}** | Score: **{brain.get('confidence_score', '--')}%**)",
            f"ព័ត៌មានពិភពលោក៖ **{gnews.get('bias', 'NEUTRAL')}** (Score {gnews.get('score', 0):+.2f}) — {gnews.get('summary_km', '')}",
            f"ហានិភ័យសង្គ្រាម/ភូមិសាស្ត្រ៖ **{wnews.get('level', 'LOW')}** — {wnews.get('summary_km', 'ស្ងប់ស្ងាត់')}",
            f"ម៉ាក្រូពិភពលោក៖ DXY **{macro.get('dxy') or '--'}** | US10Y **{macro.get('us10y') or '--'}%** | VIX **{macro.get('vix') or '--'}**",
        ]
        if brain.get("plan"):
            pl = brain["plan"]
            lines.append(f"ផែនការ V8៖ Entry **${pl['entry']}** ({pl.get('entry_km', '')}) | SL **${pl['sl']}** | TP1 **${pl['tp']}**")
        for r in (brain.get("reasons") or [])[:3]:
            lines.append(f"មូលហេតុ៖ {r}")
        return {
            "title": f"ខួរក្បាល AI V8 • SMC & ព័ត៌មានពិភពលោក ({brain.get('bar_time') or '1H'})",
            "badge": brain.get("status", "WAIT"),
            "lines": lines
        }

    if preset == "mtf_check":
        tf_summary = " | ".join([f"{t['label']}: **{t['bias']}**" for t in mtf["timeframes"]])
        lines = [
            f"លទ្ធផល 4 Timeframes៖ {tf_summary}",
            f"ការវាយតម្លៃរួម៖ **{mtf['verdict']}** (Buy: {mtf['buy_count']}/4 | Sell: {mtf['sell_count']}/4)",
            f"ការព្រមាន៖ **{mtf['warning']}**។" if mtf["warning"] else "Timeframe ធំ និងតូចកំពុងស្របទិសគ្នាល្អ អាចរង់ចាំចំណុចចូលតាមនិន្នាការមេបាន។"
        ]
        return {"title": "ផ្ទៀងផ្ទាត់ 4 Timeframes (MTF Confluence)", "badge": mtf["verdict"], "lines": lines}

    if preset == "news_check":
        g_line = f"ព័ត៌មានមាសពិភពលោក៖ **{gnews.get('bias', 'NEUTRAL')}** ({gnews.get('score', 0):+.2f}) — {gnews.get('summary_km', '')}"
        w_line = f"ព័ត៌មានសង្គ្រាម/ជម្លោះ៖ **{wnews.get('level', 'LOW')}** — {wnews.get('summary_km', 'គ្មានការរីករាលដាលថ្មី')}"
        if news["danger"] and news["danger_event"]:
            ev = news["danger_event"]
            return {
                "title": "ព្រមានព័ត៌មានសេដ្ឋកិច្ចអាមេរិក (USD RED FOLDER)", "badge": "DANGER",
                "lines": [
                    f"ព័ត៌មានជិតដល់៖ **{ev['title']}** (នៅសល់ ~**{ev['mins_until']} នាទី**)",
                    f"Forecast: **{ev['forecast']}** | Previous: **{ev['previous']}**",
                    g_line, w_line,
                    "អនុសាសន៍៖ **ផ្អាកការចូល Order ថ្មីជាបន្ទាន់** ព្រោះមាសអាចលោតខ្លាំង និងរីក Spread។"
                ]
            }
        if news["upcoming"]:
            nxt = news["upcoming"][0]
            return {
                "title": "ប្រតិទិន USD • មាសពិភពលោក & សង្គ្រាម", "badge": gnews.get("bias", "SAFE NOW"),
                "lines": [
                    "បច្ចុប្បន្ន **គ្មានព័ត៌មានក្រហម USD** ក្នុងរយៈពេល ៦០ នាទីខាងមុខទេ។",
                    f"ព័ត៌មានធំបន្ទាប់៖ **{nxt['title']}** (ក្នុង ~**{round(nxt['mins_until']/60, 1)} ម៉ោង**)",
                    g_line, w_line
                ]
            }
        return {"title": "ប្រតិទិន USD • មាសពិភពលោក & សង្គ្រាម", "badge": gnews.get("bias", "CLEAR"), "lines": ["គ្មានព័ត៌មាន USD កម្រិតក្រហមបន្ទាន់ទេ។", g_line, w_line]}

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
        v8_line = f"ខួរក្បាល AI V8៖ **{brain.get('action_km', 'WAIT')}**"
        if brain.get("reasons"):
            v8_line += f" ({brain['reasons'][0]})"
        return {
            "title": f"វិភាគចំណុចចូលជាក់ស្តែង ({interval})", "badge": brain.get("status", f"{sig} • {conf}%"),
            "lines": [
                v8_line,
                f"តម្លៃបច្ចុប្បន្ន៖ **${p}** | តំបន់ចូល៖ **{s['entry_zone']}**",
                mtf["warning"] if mtf["warning"] else f"MTF Confluence៖ **{mtf['verdict']}**",
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
                "គន្លឹះ Pro៖ កំណត់ហានិភ័យត្រឹម 0.75%–1% ក្នុង ១ ជើង និងគោរព Stop Loss ជានិច្ច។"
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
        "title": f"សង្ខេបទីផ្សារមាសពេញលេញ ({interval})", "badge": brain.get("status", f"{sig} • {conf}%"),
        "lines": [
            f"ខួរក្បាល AI V8៖ **{brain.get('action_km', 'WAIT')}**",
            f"តម្លៃផ្ទាល់៖ **${p}** | វគ្គ៖ **{sess['name']}** | ព័ត៌មានពិភពលោក៖ **{gnews.get('bias', 'NEUTRAL')}**",
            f"ហានិភ័យសង្គ្រាម៖ **{wnews.get('level', 'LOW')}** | MTF 4-TF៖ **{mtf['verdict']}**",
            f"អនុសាសន៍៖ {s['advice']} (SL: **${s['sl']}** | TP1: **${s['tp1']}**)"
        ]
    }


# ==================== ROUTES ====================
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "brain_loaded": brain_service.bundle is not None,
        "ts": int(time.time())
    }


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/gold")
async def get_gold_rest(interval: str = Query("5m", pattern="^(1m|5m|15m|1h|4h|1d)$")):
    payload = await asyncio.to_thread(build_full_payload_sync, interval, True)
    if not payload:
        return {"error": "កំពុងភ្ជាប់ទិន្នន័យមាសឡើងវិញ សូមរង់ចាំបន្តិច"}
    return payload


@app.get("/api/brain")
async def get_brain_status():
    payload = await asyncio.to_thread(build_full_payload_sync, "1h", True)
    if not payload:
        return {"error": "កំពុងភ្ជាប់ទិន្នន័យមាសឡើងវិញ"}
    return payload.get("brain", {})


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
    interval = req.interval if req.interval in VALID_INTERVALS else "5m"
    payload = await asyncio.to_thread(build_full_payload_sync, interval, True)
    if not payload:
        return {"title": "ស្ថានភាពទិន្នន័យ", "badge": "RETRY", "lines": ["កំពុងទាញទិន្នន័យឡើងវិញ សូមសាកល្បងម្តងទៀត។"]}
    return generate_ai_reply(
        req.preset, payload, interval,
        payload["mtf"], payload["news"], payload["inst_levels"], payload.get("brain")
    )


@app.websocket("/ws/gold/{interval}")
async def gold_websocket(websocket: WebSocket, interval: str):
    tf = interval if interval in VALID_INTERVALS else "5m"
    await manager.connect(websocket, tf)
    try:
        fast_payload = await asyncio.to_thread(build_full_payload_sync, tf, False)
        if fast_payload:
            ok = await manager.safe_send(websocket, fast_payload, timeout=5.0)
            if not ok:
                return

            full_payload = await asyncio.to_thread(attach_extras_sync, fast_payload)
            ok = await manager.safe_send(websocket, full_payload, timeout=6.0)
            if not ok:
                return

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


@app.get("/demo")
def serve_demo():
    return FileResponse("static/demo.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)