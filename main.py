# ==========================================================
# main.py - TRI PISAL Gold XAU/USD Pro Terminal (V10.6 Final Hardened)
# ពង្រឹងបន្ថែម៖
#   1) Zero-Deadlock Multi-Pool Architecture (mtf / extras / io)
#   2) Real-Time Order Flow Engine (Binance aggTrades + OKX Trades)
#   3) Thread-Safe Cross-Source Sanity Check (Swissquote vs Binance vs OKX)
#   4) Dual Economic Calendar (ForexFactory + Finnhub Free Tier)
#   5) Full Sync with brain_service.py V10.6 (OB-Sync, Exit Shield, Real Audit)
# ==========================================================
import os
import re
import math
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
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


# ==================== TIMEZONE HELPERS (KH LOCAL / NY / UTC) ====================
def _get_tz(name: str, fallback_hours: int):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=fallback_hours))


TZ_KH = _get_tz("Asia/Phnom_Penh", 7)
TZ_NY = _get_tz("America/New_York", -5)


def format_dual_time(dt_utc: datetime | None) -> dict:
    if dt_utc is None:
        return {"kh": "--", "ny": "--", "utc": "--"}
    try:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc) if dt_utc.tzinfo is None else dt_utc.astimezone(timezone.utc)
        return {
            "kh": dt_utc.astimezone(TZ_KH).strftime("%d/%m %H:%M"),
            "ny": dt_utc.astimezone(TZ_NY).strftime("%H:%M NY"),
            "utc": dt_utc.strftime("%H:%M UTC"),
        }
    except Exception:
        return {"kh": "--", "ny": "--", "utc": "--"}


# ==================== HTTP SESSION POOL & CONFIG ====================
http_session = requests.Session()
adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=1)
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)
http_session.headers.update({"User-Agent": "TriPisalGoldTerminal/10.6-Final"})

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()

VALID_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
ALLOWED_PRESETS = {
    "brain_v8", "entry", "risk_plan", "deep_scan", "mtf_check",
    "news_check", "inst_levels", "trend", "pattern", "volatility", "summary"
}

NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


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
# បំបែក 3 ThreadPools ដាច់ដោយឡែក ដើម្បីការពារ Deadlock ១០០%
mtf_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mtf")
extras_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="extras")
io_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="io")

_basis_lock = threading.Lock()
_last_valid_basis = 0.0

_quote_state_lock = threading.Lock()
_last_quote_state = {"price": 0.0, "ts": 0.0}


def safe_float(val, default: float = 0.0, digits: int = 2) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return round(f, digits)
    except Exception:
        return default


# ==================== SHARED CONCURRENT WEBSOCKET MANAGER ====================
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

    async def safe_send(self, ws: WebSocket, payload: dict, timeout: float = 2.5) -> bool:
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=timeout)
            return True
        except Exception:
            await self.disconnect(ws)
            return False

    async def broadcast_many(self, pairs: list[tuple[WebSocket, dict]], timeout: float = 2.5):
        if not pairs:
            return
        await asyncio.gather(
            *(self.safe_send(ws, payload, timeout=timeout) for ws, payload in pairs),
            return_exceptions=True,
        )


manager = ConnectionManager()


# ==================== REAL FOREX SPOT XAU/USD QUOTE ENGINE ====================
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
                    return {
                        "price": safe_float((bid + ask) / 2),
                        "bid": bid,
                        "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)),
                        "is_forex": True,
                        "feed": "Forex Spot Live • XAU/USD",
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
                    "price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask,
                    "spread": safe_float(max(0.0, ask - bid)), "is_forex": False, "feed": "PAXG/USD"
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
                        "price": safe_float((bid + ask) / 2), "bid": bid, "ask": ask,
                        "spread": safe_float(max(0.0, ask - bid)), "is_forex": False, "feed": "XAUT/USD"
                    }
    except Exception:
        pass
    return None


def _fetch_live_quote_raw() -> dict | None:
    if OANDA_API_KEY:
        try:
            url = "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity=S5&count=1&price=BA"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.0, 3.0)).json()
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
            res = http_session.get(url, timeout=(2.0, 3.0)).json()
            if "price" in res:
                p = safe_float(res["price"])
                if p > 0:
                    return {"price": p, "bid": p, "ask": safe_float(p + 0.25), "spread": 0.25, "is_forex": True, "feed": "Twelve Data • XAU/USD"}
        except Exception:
            pass

    bn = _fetch_binance_paxg_ticker()
    if bn:
        return bn

    return _fetch_okx_xaut_ticker()


def fetch_live_quote() -> dict | None:
    return cache.get_or_fetch("live_quote", 1.8, _fetch_live_quote_raw)


# ==================== REAL-TIME ORDER FLOW ENGINE (BINANCE + OKX) ====================
def _fetch_binance_order_flow() -> tuple[float, float, int]:
    """Binance aggTrades: m=False -> Taker Buy, m=True -> Taker Sell"""
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
    """OKX recent trades: side='buy' -> Buy, side='sell' -> Sell"""
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
    # ប្រើ io_executor ដាច់ដោយឡែក ការពារ Deadlock ១០០%
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

    tot_b = bn_b + ok_b
    tot_s = bn_s + ok_s
    tot_n = bn_n + ok_n
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
    res = cache.get_or_fetch("order_flow", 5.0, _fetch_order_flow_raw)
    return res or {"buy_pct": 50.0, "sell_pct": 50.0, "buy_vol": 0.0, "sell_vol": 0.0, "trades_count": 0, "source": "CANDLES"}


# ==================== THREAD-SAFE CROSS-SOURCE SANITY CHECK ====================
def _fetch_cross_prices_raw() -> dict:
    # ប្រើ io_executor ដាច់ដោយឡែក ការពារ Deadlock ១០០%
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
    now = time.monotonic()
    spike_glitch = False
    spike_note = ""

    # 1) ពិនិត្យការលោតតម្លៃភ្លាមៗខុសធម្មតា (> $35 ក្នុងរយៈពេលក្រោម 20 វិនាទី) ដោយប្រើ Lock
    with _quote_state_lock:
        if _last_quote_state["price"] > 1000 and (now - _last_quote_state["ts"]) < 20.0:
            jump = abs(live_price - _last_quote_state["price"])
            if jump > 35.0:
                spike_glitch = True
                spike_note = f"តម្លៃលោតខុសធម្មតា ${jump:.2f} ភ្លាមៗ"
        if live_price > 1000 and not spike_glitch:
            _last_quote_state["price"] = live_price
            _last_quote_state["ts"] = now

    # 2) ផ្ទៀងផ្ទាត់តម្លៃរវាងប្រភពច្រើន (Swissquote vs Binance vs OKX)
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


# ==================== CANDLE DATA FETCHERS ====================
def _fetch_gold_raw(interval: str) -> tuple[pd.DataFrame, str, bool, str] | None:
    limit = 260 if interval == "1h" else 160

    if OANDA_API_KEY:
        try:
            gran = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}.get(interval, "M5")
            url = f"https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles?granularity={gran}&count={limit}&price=M"
            res = http_session.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, timeout=(2.5, 4.5)).json()
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
            res = http_session.get(url, timeout=(2.5, 4.5)).json()
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
                return pd.DataFrame(rows), "Gold Spot Live • PAXG/USD", True, "PAXG"
    except Exception as e:
        print(f"[Binance] {e}")

    try:
        okx_bar = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}.get(interval, "5m")
        url = f"https://www.okx.com/api/v5/market/candles?instId=XAUT-USDT&bar={okx_bar}&limit={min(limit, 300)}"
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

    # 1) ForexFactory Primary Feed
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

    # 2) Finnhub Secondary Calendar Merge (Free Tier)
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
        "fetched_kh": f_time["kh"], "fetched_ny": f_time["ny"], "fetched_utc": f_time["utc"]
    }


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
                "top": ob_top, "bottom": ob_bot, "label": "OB"
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
                "top": ob_top, "bottom": ob_bot, "label": "OB"
            })

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

    active_demand = []
    for b in reversed(raw_demand):
        idx = b["idx"]
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


# ==================== CLEAN TREND-FILTERED BUY/SELL + DEMAND ZONE ENGINE ====================
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
                "label": "BUY", "sublabel": "INV BUY" if is_rev_buy and not is_st_buy else ""
            })
            active_trade = {
                "side": "BUY", "time": ts, "entry": entry_p, "sl": sl_p,
                "tp1": tp1_p, "tp2": tp2_p, "rr": "2 : 1", "tp1_hit": False, "sl_hit": False
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
                if low[i] <= active_trade["sl"]:
                    active_trade["sl_hit"] = True
                    signals.append({"time": ts, "price": safe_float(low[i]), "type": "SL", "label": "SL", "sublabel": ""})
                    continue
                elif high[i] >= active_trade["tp1"]:
                    active_trade["tp1_hit"] = True
                    signals.append({"time": ts, "price": safe_float(high[i]), "type": "TP1", "label": "TP1", "sublabel": ""})
                    continue
            else:
                if high[i] >= active_trade["sl"]:
                    active_trade["sl_hit"] = True
                    signals.append({"time": ts, "price": safe_float(high[i]), "type": "SL", "label": "SL", "sublabel": ""})
                    continue
                elif low[i] <= active_trade["tp1"]:
                    active_trade["tp1_hit"] = True
                    signals.append({"time": ts, "price": safe_float(low[i]), "type": "TP1", "label": "TP1", "sublabel": ""})
                    continue

    lookback = min(45, n)
    rec_low, rec_high, rec_open, rec_close = low[-lookback:], high[-lookback:], open_p[-lookback:], close[-lookback:]
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

    return signals[-20:], active_trade, zones


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
        "buy_count": buy_cnt, "sell_count": sell_cnt
    }


def get_mtf_confluence() -> dict:
    res = cache.get_or_fetch("mtf", 45.0, _compute_mtf_raw)
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
    return {k: (safe_float(v + basis) if v is not None else None) for k, v in raw.items()}


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
    if "volume" not in df.columns:
        df["volume"] = 0.0
    else:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
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
            "volume": safe_float(row["volume"], 0.0, 4),
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
    try:
        df_1h, _, _, raw_1h = fetch_gold_data("1h")
        if df_1h is not None and not df_1h.empty:
            df_1h = df_1h.copy()
            if basis != 0.0 and raw_1h not in ("OANDA", "TWELVE"):
                for col in ("open", "high", "low", "close"):
                    df_1h[col] = df_1h[col] + basis
        return brain_service.evaluate(df_1h, payload)
    except Exception as e:
        print(f"[BRAIN_EVAL] fallback after error: {e}")
        return {
            "version": "V10.6-FALLBACK",
            "status": "WAIT", "grade": "WATCH", "can_trade": False, "side": "NONE",
            "is_danger": False, "confidence_score": 35,
            "action_km": "⏳ កំពុងភ្ជាប់ខួរក្បាល AI ឡើងវិញ...",
            "hud_hint_km": "កំពុងសមកាលកម្មទិន្នន័យ...",
            "reasons": ["កំពុងសមកាលកម្មទិន្នន័យឡើងវិញ សូមរង់ចាំបន្តិច"],
        }


def attach_extras_sync(payload: dict) -> dict:
    out = dict(payload)
    basis = float(out.get("basis_offset") or 0.0)
    price = float(out.get("price") or 0.0)

    # ទាញទិន្នន័យជំនួយទាំង ៥ ដំណាលគ្នា ដោយមាន Timeout & Safe Fallback
    f_mtf = extras_executor.submit(get_mtf_confluence)
    f_news = extras_executor.submit(fetch_usd_news)
    f_inst = extras_executor.submit(get_institutional_levels, basis)
    f_flow = extras_executor.submit(fetch_order_flow)
    f_san = extras_executor.submit(check_cross_source_sanity, price, basis)

    def _safe_res(fut, default):
        try:
            return fut.result(timeout=9.0)
        except Exception:
            return default

    out["mtf"] = _safe_res(f_mtf, {"timeframes": [], "verdict": "SYNC", "aligned": "MIXED", "warning": None, "buy_count": 0, "sell_count": 0})
    out["news"] = _safe_res(f_news, {"danger": False, "danger_event": None, "upcoming": []})
    out["inst_levels"] = _safe_res(f_inst, {"pdh": None, "pdl": None, "asian_high": None, "asian_low": None})
    out["order_flow"] = _safe_res(f_flow, {"buy_pct": 50.0, "sell_pct": 50.0, "buy_vol": 0.0, "sell_vol": 0.0, "trades_count": 0, "source": "CANDLES"})
    out["sanity"] = _safe_res(f_san, {"ok": True, "glitch_detected": False, "deviation": 0.0, "sources_checked": 0, "note": "OK"})
    out["brain"] = _evaluate_brain_v8(out, basis=basis)
    return out


def _build_full_payload_uncached(interval: str, include_extras: bool = True) -> dict | None:
    global _last_valid_basis
    df, source, is_spot, raw_feed = fetch_gold_data(interval)
    if df.empty or len(df) < 5:
        return None
    quote = fetch_live_quote()
    df = df.copy()
    basis = 0.0

    if quote and quote["price"] > 0:
        live_p = quote["price"]
        raw_last = float(df.iloc[-1]["close"])
        if raw_feed not in ("OANDA", "TWELVE"):
            if quote.get("is_forex"):
                cand_basis = live_p - raw_last
                if abs(cand_basis) < 120:
                    basis = cand_basis
                    with _basis_lock:
                        _last_valid_basis = basis
                source = quote.get("feed", "Forex Spot Live • XAU/USD")
                is_spot = True
            else:
                with _basis_lock:
                    basis = _last_valid_basis
            if basis != 0.0:
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] + basis

        target_p = live_p if (quote.get("is_forex") or raw_feed in ("OANDA", "TWELVE")) else (live_p + basis)
        df.iloc[-1, df.columns.get_loc("close")] = target_p
        if target_p > df.iloc[-1]["high"]:
            df.iloc[-1, df.columns.get_loc("high")] = target_p
        if target_p < df.iloc[-1]["low"]:
            df.iloc[-1, df.columns.get_loc("low")] = target_p

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


def build_full_payload_sync(interval: str, include_extras: bool = True) -> dict | None:
    """Shared short-TTL cache: បើកច្រើន Tab ព្រមគ្នាក៏ Server គណនាតែ ១ ដង"""
    if include_extras:
        return cache.get_or_fetch(f"full_payload_{interval}", 6.0, lambda: _build_full_payload_uncached(interval, True))
    return _build_full_payload_uncached(interval, False)


# ==================== HIGH-SPEED CONCURRENT BROADCASTER + WARMUP ====================
def _warmup_caches():
    try:
        fetch_live_quote()
        fetch_gold_data("15m")
        fetch_gold_data("1h")
        fetch_usd_news()
        fetch_order_flow()
        brain_service._get_external_context_fast()
        print("[WARMUP] Core gold, Order Flow, News & Macro feeds pre-warmed (V10.6 Final)")
    except Exception as e:
        print(f"[WARMUP] skipped: {e}")


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

            # អាប់ដេត Full Snapshot + AI V10.6 រៀងរាល់ 15 វិនាទី (Parallel គ្រប់ TF)
            if cycle % 6 == 0:
                unique_tfs = list(set(tf for _, tf in clients))
                results = await asyncio.gather(
                    *(asyncio.to_thread(build_full_payload_sync, tf, True) for tf in unique_tfs),
                    return_exceptions=True,
                )
                tf_payloads = {
                    tf: res for tf, res in zip(unique_tfs, results)
                    if isinstance(res, dict)
                }
                pairs = [(ws, tf_payloads[tf]) for ws, tf in clients if tf in tf_payloads]
                await manager.broadcast_many(pairs, timeout=3.5)
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
                await manager.broadcast_many([(ws, tick_msg) for ws, _ in clients], timeout=2.0)

            if cycle % 10 == 0:
                await manager.broadcast_many([(ws, {"type": "ping"}) for ws, _ in clients], timeout=2.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[BROADCAST] recovered from error: {e}")
            await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_warmup_caches, daemon=True).start()
    task = asyncio.create_task(broadcast_loop())
    try:
        yield
    finally:
        task.cancel()
        mtf_executor.shutdown(wait=False)
        extras_executor.shutdown(wait=False)
        io_executor.shutdown(wait=False)
        http_session.close()


app = FastAPI(title="TRI PISAL • Gold XAU/USD Pro Terminal (V10.6 Final Hardened)", lifespan=lifespan)


# ==================== AI CHAT ENGINE (V10.6 SYNCED) ====================
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
            lines.append(f"ពិន្ទុ ៣ សសរស្តម្ភ៖ V8 ({v8p.get('buy',0)}/{v8p.get('sell',0)}) • OB/SMC ({stp.get('buy',0)}/{stp.get('sell',0)}) • Macro ({mcp.get('buy',0)}/{mcp.get('sell',0)})")
        pl = brain.get("plan") or brain.get("preview_plan")
        if pl:
            lines.append(f"ផែនការ៖ Entry **${pl['entry']}** | SL **${pl['sl']}** | TP1 **${pl['tp']}** | BE @ **${pl.get('be_trigger', '--')}**")
        for r in (brain.get("reasons") or [])[:2]:
            lines.append(f"មូលហេតុ៖ {r}")
        return {
            "title": f"ខួរក្បាល AI V10.6 • OB/SMC + Order Flow + Macro ({brain.get('bar_time') or '1H'})",
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
                    "អនុសាសន៍៖ **ផ្អាកការចូល Order ថ្មីជាបន្ទាន់** ព្រោះមាសអាចលោតខ្លាំង និងរីក Spread។"
                ]
            }
        if news["upcoming"]:
            nxt = news["upcoming"][0]
            return {
                "title": "ប្រតិទិន USD • មាសពិភពលោក & សង្គ្រាម", "badge": gnews.get("bias", "SAFE NOW"),
                "lines": [
                    "បច្ចុប្បន្ន **គ្មានព័ត៌មានក្រហម USD** ក្នុងរយៈពេល ៦០ នាទីខាងមុខទេ។",
                    f"ព័ត៌មានធំបន្ទាប់៖ **{nxt['title']}** (កម្ពុជា **{nxt.get('time_kh', '--')}** | ក្រៅស្រុក **{nxt.get('time_ny', '--')}**)",
                    g_line, w_line, m_line
                ]
            }
        return {"title": "ប្រតិទិន USD • មាសពិភពលោក & សង្គ្រាម", "badge": gnews.get("bias", "CLEAR"), "lines": ["គ្មានព័ត៌មាន USD កម្រិតក្រហមបន្ទាន់ទេ។", g_line, w_line, m_line]}

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
        v_line = f"ខួរក្បាល AI V10.6៖ **{brain.get('action_km', 'WAIT')}**"
        if brain.get("reasons"):
            v_line += f" ({brain['reasons'][0]})"
        return {
            "title": f"វិភាគចំណុចចូលជាក់ស្តែង ({interval})", "badge": brain.get("status", f"{sig} • {conf}%"),
            "lines": [
                v_line,
                f"តម្លៃបច្ចុប្បន្ន៖ **${p}** | Order Flow ទិញ៖ **{oflow.get('buy_pct', brain.get('buy_pressure_pct', 50))}%** | តំបន់ចូល៖ **{s['entry_zone']}**",
                mtf["warning"] if mtf["warning"] else f"MTF Confluence៖ **{mtf['verdict']}**",
                f"ការពារ៖ {shield.get('title_km', '')} — {brain.get('hud_hint_km') or s['advice']}។"
            ]
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
                f"ការពារដើមទុន (Zero-Risk)៖ ពេលដល់ **${be_v}** (50% នៃ TP1) ត្រូវរំកិល SL មកស្មើដើមភ្លាម។"
            ]
        }

    if preset == "deep_scan":
        return {
            "title": f"ស្កេនសូចនាករ 5-in-1 ({interval})", "badge": f"CONF {conf}%",
            "lines": [
                f"EMA20 (**${data['ema20']}**) vs EMA50 (**${data['ema50']}**)",
                f"RSI (14)៖ **{rsi}** | MACD Hist៖ **{data['macd_hist']:+.3f}**",
                f"Bollinger Bands៖ **${data['bb_lower']}** - **${data['bb_upper']}**",
                f"Pivot Point៖ **${piv}** | ATR (14)៖ **${atr}** | Order Flow ទិញ៖ **{oflow.get('buy_pct', 50)}%**"
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
            f"ខួរក្បាល AI V10.6៖ **{brain.get('action_km', 'WAIT')}** | Order Flow ទិញ៖ **{oflow.get('buy_pct', brain.get('buy_pressure_pct', 50))}%**",
            f"តម្លៃផ្ទាល់៖ **${p}** | វគ្គ៖ **{sess['name']}** | ព័ត៌មានមាស៖ **{gnews.get('bias', 'NEUTRAL')}** | សង្គ្រាម៖ **{wnews.get('level', 'LOW')}**",
            f"Exit Shield៖ **{shield.get('title_km', 'សកម្ម')}** | MTF 4-TF៖ **{mtf['verdict']}**",
            f"អនុសាសន៍៖ {brain.get('hud_hint_km') or s['advice']}"
        ]
    }


# ==================== ROUTES ====================
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "version": "10.6-final-hardened",
        "brain_loaded": brain_service.bundle is not None,
        "finnhub_key": bool(FINNHUB_API_KEY),
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

            full_payload = await asyncio.to_thread(build_full_payload_sync, tf, True)
            if full_payload:
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
    return FileResponse("static/index.html", headers=NO_CACHE_HEADERS)


@app.get("/demo")
def serve_demo():
    return FileResponse("static/demo.html", headers=NO_CACHE_HEADERS)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)