# ==========================================================
# brain_service.py - V10.4 Institutional Quant Sniper Engine
# ពង្រឹងបន្ថែម៖
#   1) FRED Official US Macro Engine (API + Free CSV Fallback)
#   2) Finnhub News + GDELT + RSS (Word-Boundary + Time-Decay)
#   3) Real-Time Order Flow Integration + Cross-Source Glitch Gate
#   4) 4-Layer Institutional Confluence Filter + BE@50%TP1 Plan
#   5) Fixed SignalWinRateTracker (No Same-Candle Fakeout + Per-TF Stats)
# ==========================================================
import os
import re
import csv
import io
import json
import math
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter

import brain_core as core

BASE_DIR = os.path.dirname(__file__)
MODEL_PATH = os.path.join(BASE_DIR, "models", "gold_brain_v8.pkl")
EVENTS_CSV = os.path.join(BASE_DIR, "models", "events.csv")
STATS_PATH = os.path.join(BASE_DIR, "models", "signal_stats.json")

FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()


def _get_tz(name: str, fallback_hours: int):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=fallback_hours))


TZ_KH = _get_tz("Asia/Phnom_Penh", 7)
TZ_NY = _get_tz("America/New_York", -5)


def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _normalize_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:90]


def _compile_kw_map(kw_dict: dict[str, float]) -> list[tuple[re.Pattern, float]]:
    return [(re.compile(r"\b" + re.escape(kw.lower()) + r"\b"), float(w)) for kw, w in kw_dict.items()]


def _compile_filter_regex(words: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w.lower()) for w in words) + r")\b")


def _recency_weight(pub_dt: datetime | None, now_utc: datetime) -> float:
    if pub_dt is None:
        return 0.6
    age_hours = max(0.0, (now_utc - pub_dt).total_seconds() / 3600.0)
    if age_hours <= 3.0:
        return 1.0
    if age_hours <= 12.0:
        return 0.70
    if age_hours <= 24.0:
        return 0.35
    return 0.10


def _is_forex_weekend_closed(now_utc: datetime) -> bool:
    wd, hr = now_utc.weekday(), now_utc.hour
    return wd == 5 or (wd == 4 and hr >= 22) or (wd == 6 and hr < 22)


def _get_session_killzone(now_utc: datetime) -> dict:
    hr = now_utc.hour
    if 12 <= hr < 16:
        return {"code": "NY_OVERLAP", "name_km": "NY-London Overlap (ម៉ោងមាស)", "quality": "PRIME", "bonus": 5}
    if 7 <= hr < 11:
        return {"code": "LONDON_KZ", "name_km": "London Killzone", "quality": "HIGH", "bonus": 4}
    if 16 <= hr < 20:
        return {"code": "NY_PM", "name_km": "New York Session", "quality": "GOOD", "bonus": 2}
    if 0 <= hr < 7:
        return {"code": "ASIAN", "name_km": "Asian Session", "quality": "NORMAL", "bonus": 0}
    return {"code": "DEAD_ZONE", "name_km": "ម៉ោងស្ងាត់ (Rollover)", "quality": "LOW", "bonus": -6}


def _format_dual_time(dt_utc: datetime | None) -> dict:
    if dt_utc is None:
        return {"kh": "--", "utc": "--", "ny": "--", "ts": 0}
    try:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc) if dt_utc.tzinfo is None else dt_utc.astimezone(timezone.utc)
        return {
            "kh": dt_utc.astimezone(TZ_KH).strftime("%d/%m %H:%M"),
            "utc": dt_utc.strftime("%H:%M UTC"),
            "ny": dt_utc.astimezone(TZ_NY).strftime("%H:%M NY"),
            "ts": int(dt_utc.timestamp()),
        }
    except Exception:
        return {"kh": "--", "utc": "--", "ny": "--", "ts": 0}


def _parse_rss_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    s = str(date_str).strip()
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        except Exception:
            pass
    try:
        dt = parsedate_to_datetime(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


BULLISH_GOLD_WORDS = {
    "rate cut": 2.6, "dovish": 2.3, "central bank buys": 2.6, "central bank gold": 2.2,
    "safe-haven": 2.4, "safe haven": 2.4, "gold surges": 2.0, "gold jumps": 1.8,
    "gold rallies": 1.8, "gold climbs": 1.6, "weak dollar": 2.1, "dollar falls": 1.9,
    "dollar drops": 1.9, "recession": 2.3, "inflation fears": 1.6, "stagflation": 2.2,
    "yields fall": 2.1, "yields drop": 1.9, "record high": 1.6, "crisis": 2.0,
    "tariff": 1.7, "trade war": 2.3, "uncertainty": 1.5, "debt ceiling": 1.8,
    "bank crisis": 2.5, "fed pause": 1.8, "unemployment rises": 1.9, "etf inflows": 2.0
}

BEARISH_GOLD_WORDS = {
    "rate hike": -2.7, "hawkish": -2.4, "ceasefire": -2.5, "peace deal": -2.4,
    "truce": -2.2, "strong dollar": -2.3, "dollar surges": -2.1, "dollar rallies": -1.9,
    "yields surge": -2.3, "yields rise": -1.9, "yields jump": -2.1,
    "gold drops": -1.8, "gold falls": -1.8, "gold slides": -1.8, "gold slips": -1.5,
    "profit taking": -1.5, "strong jobs": -2.1, "nfp beats": -2.3,
    "higher for longer": -2.4, "risk-on": -1.5, "inflation cools": -1.3, "etf outflows": -2.0
}

GOLD_RELEVANT_FILTER = (
    "gold", "xau", "bullion", "fed", "powell", "fomc", "inflation", "cpi", "pce",
    "dollar", "dxy", "treasury", "yield", "tariff", "central bank", "recession", "jobs", "nfp"
)

WAR_HIGH_WORDS = {
    "missile": 3.0, "airstrike": 2.8, "air strike": 2.8, "nuclear": 3.5,
    "invasion": 3.0, "bombing": 2.6, "retaliation": 2.5, "escalation": 2.5,
    "drone attack": 2.5, "war escalates": 3.0, "middle east war": 3.0,
    "strait of hormuz": 3.0, "red sea attack": 2.5, "martial law": 2.5
}

WAR_MED_WORDS = {
    "war": 1.8, "conflict": 1.6, "military": 1.4, "troops": 1.5, "sanctions": 1.8,
    "iran": 1.5, "israel": 1.5, "gaza": 1.5, "ukraine": 1.5, "russia": 1.4,
    "taiwan": 1.8, "north korea": 1.8, "nato": 1.5, "attack": 1.6, "military strike": 1.8
}

WAR_DEESCALATE_WORDS = {
    "ceasefire": -3.0, "peace talks": -2.2, "peace deal": -2.8, "truce": -2.5,
    "de-escalation": -2.5, "withdraws troops": -2.2, "hostage deal": -2.0
}

_BULLISH_PATTERNS = _compile_kw_map(BULLISH_GOLD_WORDS)
_BEARISH_PATTERNS = _compile_kw_map(BEARISH_GOLD_WORDS)
_GOLD_FILTER_RE = _compile_filter_regex(GOLD_RELEVANT_FILTER)
_WAR_HIGH_PATTERNS = _compile_kw_map(WAR_HIGH_WORDS)
_WAR_MED_PATTERNS = _compile_kw_map(WAR_MED_WORDS)
_WAR_COOL_PATTERNS = _compile_kw_map(WAR_DEESCALATE_WORDS)

GLOBAL_RSS_FEEDS = [
    ("Yahoo Gold/DXY", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F,DX-Y.NYB&region=US&lang=en-US"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("CNBC Macro", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("Investing Gold", "https://www.investing.com/rss/news_11.rss"),
    ("Kitco Gold", "https://www.kitco.com/rss/gold.xml"),
]

WAR_RSS_FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]

GDELT_WAR_API = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=(missile%20OR%20airstrike%20OR%20%22middle%20east%22%20OR%20ukraine%20OR%20sanctions%20OR%20ceasefire)%20sourcelang:eng"
    "&mode=artlist&maxrecords=8&timespan=12h&format=json"
)

FRED_SERIES = ("DGS10", "DFII10", "CPIAUCSL", "UNRATE", "FEDFUNDS")


# ==================== AUTO FORWARD-TEST WIN-RATE TRACKER (FIXED) ====================
class SignalWinRateTracker:
    """កត់ត្រាស្វ័យប្រវត្តិ និងវាស់ Win Rate ពិតប្រាកដ (ការពារ Same-Candle Fakeout + បំបែកតាម TF)"""
    def __init__(self, path: str = STATS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.data = {"wins": 0, "losses": 0, "by_tf": {}, "active": {}, "recent": []}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.data["wins"] = int(d.get("wins", 0))
                    self.data["losses"] = int(d.get("losses", 0))
                    self.data["by_tf"] = d.get("by_tf") if isinstance(d.get("by_tf"), dict) else {}
                    self.data["active"] = d.get("active") if isinstance(d.get("active"), dict) else {}
                    self.data["recent"] = d.get("recent") if isinstance(d.get("recent"), list) else []
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def update_and_record(
        self, tf: str, price: float, high: float, low: float, candle_ts: int, new_signal: dict | None
    ) -> dict:
        changed = False
        now_ts = int(time.time())
        with self._lock:
            # 1) ពិនិត្យជើងកំពុងរត់ថាតើ Hit SL ឬ Hit TP1
            act = self.data["active"].get(tf)
            if act and price > 0:
                entry_cts = int(act.get("entry_candle_ts", 0))
                # បើនៅលើទៀនចូលដំបូង ប្រើតែ Live Tick Price កុំឱ្យជាប់ High/Low មុនពេលចូល Order
                is_same_candle = (entry_cts > 0 and candle_ts > 0 and candle_ts <= entry_cts)
                hi = price if is_same_candle else (max(price, high) if high > 0 else price)
                lo = price if is_same_candle else (min(price, low) if low > 0 else price)

                outcome = None
                # ពិនិត្យ SL មុន (Conservative Institutional Rule បើទៀនតែមួយប៉ះទាំង SL និង TP)
                if act["side"] == "BUY":
                    if lo <= act["sl"]:
                        outcome = "LOSS"
                    elif hi >= act["tp"]:
                        outcome = "WIN"
                else:
                    if hi >= act["sl"]:
                        outcome = "LOSS"
                    elif lo <= act["tp"]:
                        outcome = "WIN"

                if outcome:
                    tf_stat = self.data["by_tf"].setdefault(tf, {"wins": 0, "losses": 0})
                    if outcome == "WIN":
                        self.data["wins"] += 1
                        tf_stat["wins"] += 1
                    else:
                        self.data["losses"] += 1
                        tf_stat["losses"] += 1

                    self.data["recent"].insert(0, {
                        "tf": tf, "side": act["side"], "entry": act["entry"],
                        "sl": act["sl"], "tp": act["tp"], "result": outcome, "ts": now_ts
                    })
                    self.data["recent"] = self.data["recent"][:30]
                    self.data["active"].pop(tf, None)
                    changed = True
                elif (now_ts - act.get("ts", now_ts)) > 86400:
                    self.data["active"].pop(tf, None)
                    changed = True

            # 2) កត់ត្រាសញ្ញា Sniper ថ្មី (បើមិនទាន់មានជើងកំពុងរត់លើ TF នេះ)
            if new_signal and tf not in self.data["active"]:
                self.data["active"][tf] = {
                    "side": new_signal["side"],
                    "entry": new_signal["entry"],
                    "sl": new_signal["sl"],
                    "tp": new_signal["tp"],
                    "entry_candle_ts": int(candle_ts),
                    "ts": now_ts,
                }
                changed = True

            if changed:
                self._save()

            wins, losses = self.data["wins"], self.data["losses"]
            total = wins + losses
            wr = round((wins / total) * 100.0, 1) if total > 0 else None

            tf_s = self.data["by_tf"].get(tf, {"wins": 0, "losses": 0})
            tf_tot = tf_s["wins"] + tf_s["losses"]
            tf_wr = round((tf_s["wins"] / tf_tot) * 100.0, 1) if tf_tot > 0 else None

            return {
                "wins": wins,
                "losses": losses,
                "total": total,
                "win_rate": wr,
                "tf_wins": tf_s["wins"],
                "tf_losses": tf_s["losses"],
                "tf_win_rate": tf_wr,
                "active_count": len(self.data["active"]),
            }


class GoldBrainService:
    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.bundle = None
        self.load_error = None
        self.tracker = SignalWinRateTracker()

        self._lock = threading.Lock()
        self._macro_fetch_lock = threading.Lock()
        self._fred_fetch_lock = threading.Lock()
        self._news_fetch_lock = threading.Lock()
        self._war_fetch_lock = threading.Lock()

        self._macro_cache = {"ts": 0.0, "raw": None, "feat": None}
        self._fred_cache = {"ts": 0.0, "data": None}
        self._news_cache = {"ts": 0.0, "data": None}
        self._war_cache = {"ts": 0.0, "data": None}
        self._ev_cache = {"year": None, "times": None}
        self._v8_bar_cache = {"key": None, "res": None}
        self._last_signal_mem = {}
        self._last_reload_try = 0.0

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=24, max_retries=0)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._session.headers.update({"User-Agent": "TriPisalGoldTerminal/10.4"})

        self._load_model()

    def _load_model(self):
        if not os.path.exists(self.model_path):
            self.load_error = f"រកមិនឃើញ File ម៉ូដែល: {self.model_path}"
            print(f"[BRAIN] {self.load_error}")
            return
        try:
            loaded = joblib.load(self.model_path)
            if not isinstance(loaded, dict) or "models" not in loaded or "features" not in loaded:
                raise ValueError("ទម្រង់ File ម៉ូដែលមិនត្រឹមត្រូវ")
            self.bundle = loaded
            self.load_error = None
            print(
                f"[BRAIN] Loaded {self.bundle.get('version', 'V8')} (V10.4 Institutional Quant) | "
                f"features={len(self.bundle.get('features', []))} | "
                f"cutoff={self.bundle.get('min_percentile', 0.50)}"
            )
        except Exception as e:
            self.bundle = None
            self.load_error = f"បើកម៉ូដែលមិនបាន: {e}"
            print(f"[BRAIN] {self.load_error}")

    def _ensure_model(self):
        if self.bundle is not None:
            return
        now = time.monotonic()
        if (now - self._last_reload_try) > 60.0:
            self._last_reload_try = now
            self._load_model()

    def _http_get(self, url: str, timeout: tuple[float, float] = (2.0, 3.5)) -> requests.Response | None:
        try:
            r = self._session.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r
        except Exception:
            pass
        return None

    # ==================== FRED OFFICIAL US MACRO ENGINE ====================
    def _fetch_fred_series(self, series_id: str, limit: int = 15) -> list[float]:
        """ទាញទិន្នន័យ FRED ផ្លូវការ៖ ប្រើ FRED API បើមាន Key ឬ Public CSV ឥតគិតថ្លៃបើគ្មាន Key"""
        if FRED_API_KEY:
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
                f"&sort_order=desc&limit={limit}"
            )
            r = self._http_get(url, timeout=(2.5, 4.0))
            if r is not None:
                try:
                    obs = r.json().get("observations") or []
                    vals = []
                    for o in reversed(obs):
                        v = o.get("value")
                        if v not in (None, "", "."):
                            vals.append(float(v))
                    if vals:
                        return vals
                except Exception:
                    pass

        # Public Free CSV Fallback (No API Key Required)
        start_dt = (datetime.now(timezone.utc) - timedelta(days=450)).strftime("%Y-%m-%d")
        csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start_dt}"
        r = self._http_get(csv_url, timeout=(2.5, 4.5))
        if r is None:
            return []
        vals = []
        try:
            reader = csv.reader(io.StringIO(r.text))
            next(reader, None)
            for row in reader:
                if len(row) >= 2 and row[1] not in ("", "."):
                    vals.append(float(row[1]))
            return vals[-limit:]
        except Exception:
            return []

    def fetch_fred_macro(self, ttl: float = 3600.0) -> dict:
        now = time.monotonic()
        with self._lock:
            stale = self._fred_cache["data"]
            if stale is not None and (now - self._fred_cache["ts"]) < ttl:
                return stale

        if not self._fred_fetch_lock.acquire(blocking=False):
            return stale or self._empty_fred_macro()

        try:
            fetched = {}
            with ThreadPoolExecutor(max_workers=len(FRED_SERIES)) as ex:
                futs = {ex.submit(self._fetch_fred_series, sid, 15): sid for sid in FRED_SERIES}
                for f in as_completed(futs):
                    sid = futs[f]
                    try:
                        fetched[sid] = f.result()
                    except Exception:
                        fetched[sid] = []

            dgs10 = fetched.get("DGS10") or []
            dfii10 = fetched.get("DFII10") or []
            cpi = fetched.get("CPIAUCSL") or []
            unrate = fetched.get("UNRATE") or []
            fedfunds = fetched.get("FEDFUNDS") or []

            us10y = round(dgs10[-1], 2) if dgs10 else None
            us10y_chg5 = round(dgs10[-1] - dgs10[-6], 3) if len(dgs10) >= 6 else 0.0
            real10 = round(dfii10[-1], 2) if dfii10 else None
            real10_chg5 = round(dfii10[-1] - dfii10[-6], 3) if len(dfii10) >= 6 else 0.0
            cpi_yoy = round(((cpi[-1] / cpi[-13]) - 1.0) * 100.0, 2) if len(cpi) >= 13 else None
            unemp = round(unrate[-1], 2) if unrate else None
            unemp_chg = round(unrate[-1] - unrate[-2], 2) if len(unrate) >= 2 else 0.0
            fed_rate = round(fedfunds[-1], 2) if fedfunds else None
            fed_chg = round(fedfunds[-1] - fedfunds[-2], 2) if len(fedfunds) >= 2 else 0.0

            # គណនា FRED Official Macro Bias (-1.0 ដល់ +1.0)
            score = 0.0
            notes = []
            if real10_chg5 <= -0.06:
                score += 0.35; notes.append(f"Real Yield ធ្លាក់ ({real10_chg5:+.2f}%)")
            elif real10_chg5 >= 0.06:
                score -= 0.35; notes.append(f"Real Yield ឡើង ({real10_chg5:+.2f}%)")

            if us10y_chg5 <= -0.08:
                score += 0.25
            elif us10y_chg5 >= 0.08:
                score -= 0.25

            if fed_chg < -0.05:
                score += 0.25; notes.append(f"Fed កាត់ការប្រាក់ ({fed_rate}%)")
            elif fed_chg > 0.05:
                score -= 0.25; notes.append(f"Fed ដំឡើងការប្រាក់ ({fed_rate}%)")

            if unemp_chg >= 0.1:
                score += 0.20; notes.append(f"ភាពអត់ការងារកើន ({unemp}%)")
            elif unemp_chg <= -0.1:
                score -= 0.15

            if cpi_yoy is not None and cpi_yoy >= 3.2:
                score += 0.15; notes.append(f"អតិផរណា CPI ខ្ពស់ ({cpi_yoy}%)")

            score = max(-1.0, min(1.0, round(score, 2)))
            if score >= 0.20:
                bias, summary_km = "BULLISH", "ទិន្នន័យ FRED អាមេរិកគាំទ្រតម្លៃមាសឡើង"
            elif score <= -0.20:
                bias, summary_km = "BEARISH", "ទិន្នន័យ FRED អាមេរិកដាក់សម្ពាធឱ្យមាសចុះ"
            else:
                bias, summary_km = "NEUTRAL", "ទិន្នន័យម៉ាក្រូ FRED មានលំនឹង"

            if not any((us10y, real10, cpi_yoy, unemp, fed_rate)) and stale is not None:
                return stale

            f_time = _format_dual_time(datetime.now(timezone.utc))
            result = {
                "source": "FRED API" if FRED_API_KEY else "FRED Public",
                "us10y": us10y, "us10y_chg5": us10y_chg5,
                "real10": real10, "real10_chg5": real10_chg5,
                "cpi_yoy": cpi_yoy, "unemployment": unemp,
                "fed_rate": fed_rate, "bias": bias, "score": score,
                "summary_km": summary_km, "notes": notes[:3],
                "updated_at": f"កម្ពុជា {f_time['kh']} ({f_time['utc']})",
            }
            with self._lock:
                self._fred_cache = {"ts": time.monotonic(), "data": result}
            return result
        finally:
            self._fred_fetch_lock.release()

    def _empty_fred_macro(self) -> dict:
        return {
            "source": "FRED", "us10y": None, "us10y_chg5": 0.0,
            "real10": None, "real10_chg5": 0.0, "cpi_yoy": None,
            "unemployment": None, "fed_rate": None, "bias": "NEUTRAL",
            "score": 0.0, "summary_km": "កំពុងភ្ជាប់ទិន្នន័យ FRED...",
            "notes": [], "updated_at": "--",
        }

    def _get_macro(self, ttl: float = 1800.0):
        now = time.monotonic()
        with self._lock:
            if self._macro_cache["feat"] is not None and (now - self._macro_cache["ts"]) < ttl:
                return self._macro_cache["raw"], self._macro_cache["feat"]
            stale_raw, stale_feat = self._macro_cache["raw"], self._macro_cache["feat"]

        if not self._macro_fetch_lock.acquire(blocking=False):
            return stale_raw, stale_feat

        try:
            start_str = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d")
            raw_m = core.download_macro(start=start_str)
            feat_m = core.macro_features(raw_m)
            if raw_m is not None and not raw_m.empty and feat_m is not None and not feat_m.empty:
                with self._lock:
                    self._macro_cache = {"ts": time.monotonic(), "raw": raw_m, "feat": feat_m}
                return raw_m, feat_m
            return stale_raw, stale_feat
        except Exception as e:
            print(f"[BRAIN] macro fallback: {e}")
            return stale_raw, stale_feat
        finally:
            self._macro_fetch_lock.release()

    def _get_events(self):
        yr = datetime.now(timezone.utc).year
        with self._lock:
            if self._ev_cache["year"] == yr and self._ev_cache["times"] is not None:
                return self._ev_cache["times"]
        try:
            times = core.get_event_times(yr - 1, yr + 1, EVENTS_CSV)
        except Exception:
            times = []
        with self._lock:
            self._ev_cache = {"year": yr, "times": times}
        return times

    def _fetch_rss_items(self, source_name: str, url: str, limit: int = 16) -> list[tuple[str, str, str, str | None]]:
        r = self._http_get(url)
        if r is None:
            return []
        out = []
        try:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:limit]:
                t_el, l_el, p_el = item.find("title"), item.find("link"), item.find("pubDate")
                if t_el is not None and t_el.text:
                    out.append((
                        source_name, t_el.text.strip(),
                        l_el.text.strip() if (l_el is not None and l_el.text) else "",
                        p_el.text.strip() if (p_el is not None and p_el.text) else None,
                    ))
        except Exception:
            return []
        return out

    def _fetch_finnhub_news(self, limit: int = 15) -> list[tuple[str, str, str, str | None]]:
        if not FINNHUB_API_KEY:
            return []
        url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
        r = self._http_get(url, timeout=(2.0, 3.2))
        if r is None:
            return []
        out = []
        try:
            for item in (r.json() or [])[:limit]:
                headline = (item.get("headline") or "").strip()
                if headline:
                    src = item.get("source") or "Finnhub"
                    out.append((f"Finnhub/{src}", headline, item.get("url") or "", str(item.get("datetime") or "")))
        except Exception:
            return []
        return out

    def fetch_global_news(self, ttl: float = 300.0) -> dict:
        now = time.monotonic()
        with self._lock:
            stale_data = self._news_cache["data"]
            if stale_data is not None and (now - self._news_cache["ts"]) < ttl:
                return stale_data

        if not self._news_fetch_lock.acquire(blocking=False):
            return stale_data or self._empty_global_news()

        try:
            now_utc = datetime.now(timezone.utc)
            raw_items = []
            max_w = len(GLOBAL_RSS_FEEDS) + (1 if FINNHUB_API_KEY else 0)
            with ThreadPoolExecutor(max_workers=max_w) as ex:
                futs = [ex.submit(self._fetch_rss_items, src, url, 14) for src, url in GLOBAL_RSS_FEEDS]
                if FINNHUB_API_KEY:
                    futs.append(ex.submit(self._fetch_finnhub_news, 16))
                for f in as_completed(futs):
                    try:
                        raw_items.extend(f.result())
                    except Exception:
                        pass

            headlines, seen_keys, total_score = [], set(), 0.0
            for source_name, title, link, pub_raw in raw_items:
                t_low = title.lower()
                if not _GOLD_FILTER_RE.search(t_low):
                    continue
                key = _normalize_title_key(title)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)

                raw_score = 0.0
                for pat, w in _BULLISH_PATTERNS:
                    if pat.search(t_low):
                        raw_score += w
                for pat, w in _BEARISH_PATTERNS:
                    if pat.search(t_low):
                        raw_score += w

                pub_dt = _parse_rss_date(pub_raw)
                score = raw_score * _recency_weight(pub_dt, now_utc)
                total_score += score
                impact = "BULLISH" if raw_score >= 1.5 else ("BEARISH" if raw_score <= -1.5 else "NEUTRAL")
                tinfo = _format_dual_time(pub_dt or now_utc)
                headlines.append({
                    "source": source_name, "title": title, "link": link,
                    "time_kh": tinfo["kh"], "time_utc": tinfo["utc"], "time_ny": tinfo["ny"],
                    "ts": tinfo["ts"], "impact": impact, "score": round(score, 2),
                })

            if not headlines and stale_data is not None:
                with self._lock:
                    self._news_cache["ts"] = time.monotonic() - (ttl * 0.7)
                return stale_data

            headlines.sort(key=lambda x: x["ts"], reverse=True)
            norm_score = max(-1.0, min(1.0, round(total_score / 8.5, 2)))
            if norm_score >= 0.20:
                bias, summary_km = "BULLISH", "ព័ត៌មានសេដ្ឋកិច្ច និងមាសពិភពលោកកំពុងគាំទ្រឲ្យមាសឡើងថ្លៃ"
            elif norm_score <= -0.20:
                bias, summary_km = "BEARISH", "ព័ត៌មានសេដ្ឋកិច្ចពិភពលោកកំពុងដាក់សម្ពាធឲ្យមាសចុះថ្លៃ"
            else:
                bias, summary_km = "NEUTRAL", "ព័ត៌មានមាសពិភពលោកមានលំនឹងធម្មតា"

            f_time = _format_dual_time(now_utc)
            result = {
                "bias": bias, "score": norm_score, "summary_km": summary_km,
                "count": len(headlines), "headlines": headlines[:8],
                "fetched_kh": f_time["kh"], "fetched_utc": f_time["utc"], "fetched_ny": f_time["ny"],
                "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
            }
            with self._lock:
                self._news_cache = {"ts": time.monotonic(), "data": result}
            return result
        finally:
            self._news_fetch_lock.release()

    def _empty_global_news(self) -> dict:
        f_time = _format_dual_time(datetime.now(timezone.utc))
        return {
            "bias": "NEUTRAL", "score": 0.0, "summary_km": "កំពុងភ្ជាប់ព័ត៌មានមាសពិភពលោក...",
            "count": 0, "headlines": [],
            "fetched_kh": f_time["kh"], "fetched_utc": f_time["utc"], "fetched_ny": f_time["ny"],
            "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
        }

    def _fetch_gdelt_war(self) -> list[tuple[str, str, str, str | None]]:
        r = self._http_get(GDELT_WAR_API, timeout=(2.0, 3.2))
        if r is None:
            return []
        out = []
        try:
            for a in (r.json().get("articles") or [])[:8]:
                t = (a.get("title") or "").strip()
                if t:
                    out.append((a.get("domain") or "GDELT", t, a.get("url") or "", a.get("seendate")))
        except Exception:
            return []
        return out

    def fetch_war_news(self, ttl: float = 300.0) -> dict:
        now = time.monotonic()
        with self._lock:
            stale_data = self._war_cache["data"]
            if stale_data is not None and (now - self._war_cache["ts"]) < ttl:
                return stale_data

        if not self._war_fetch_lock.acquire(blocking=False):
            return stale_data or self._empty_war_news()

        try:
            now_utc = datetime.now(timezone.utc)
            raw_items = []
            with ThreadPoolExecutor(max_workers=len(WAR_RSS_FEEDS) + 1) as ex:
                futs = [ex.submit(self._fetch_rss_items, src, url, 20) for src, url in WAR_RSS_FEEDS]
                futs.append(ex.submit(self._fetch_gdelt_war))
                for f in as_completed(futs):
                    try:
                        raw_items.extend(f.result())
                    except Exception:
                        pass

            items, seen_keys, raw_tension = [], set(), 0.0
            for source_name, title, link, pub_raw in raw_items:
                clean_t = (title or "").strip()
                key = _normalize_title_key(clean_t)
                if not key or key in seen_keys:
                    continue
                t_low = clean_t.lower()
                sc_base = 0.0
                for pat, w in _WAR_HIGH_PATTERNS:
                    if pat.search(t_low):
                        sc_base += w
                for pat, w in _WAR_MED_PATTERNS:
                    if pat.search(t_low):
                        sc_base += w
                for pat, w in _WAR_COOL_PATTERNS:
                    if pat.search(t_low):
                        sc_base += w
                if abs(sc_base) < 1.5:
                    continue

                seen_keys.add(key)
                pub_dt = _parse_rss_date(pub_raw)
                sc = sc_base * _recency_weight(pub_dt, now_utc)
                raw_tension += sc
                severity = "ESCALATION" if sc_base >= 2.5 else ("PEACE/COOL" if sc_base <= -1.5 else "TENSION")
                tinfo = _format_dual_time(pub_dt or now_utc)
                items.append({
                    "source": source_name, "title": clean_t, "link": link or "",
                    "time_kh": tinfo["kh"], "time_utc": tinfo["utc"], "time_ny": tinfo["ny"],
                    "ts": tinfo["ts"], "severity": severity, "score": round(sc, 2),
                })

            if not raw_items and stale_data is not None:
                with self._lock:
                    self._war_cache["ts"] = time.monotonic() - (ttl * 0.7)
                return stale_data

            items.sort(key=lambda x: (abs(x["score"]), x["ts"]), reverse=True)
            norm = max(-1.0, min(1.0, round(raw_tension / 11.0, 2)))
            if norm >= 0.45:
                level, gold_impact, summary_km = "HIGH", "BULLISH", "ភាពតានតឹងសង្គ្រាមកើនឡើងខ្លាំង — កម្លាំងទិញមាស Safe-Haven ខ្ពស់"
            elif norm >= 0.18:
                level, gold_impact, summary_km = "MEDIUM", "BULLISH", "មានព័ត៌មានជម្លោះភូមិសាស្ត្រនយោបាយ — ជួយទ្រតម្លៃមាស"
            elif norm <= -0.25:
                level, gold_impact, summary_km = "COOLING", "BEARISH", "មានសញ្ញាបន្ធូរបន្ថយសង្គ្រាម/ឈប់បាញ់ — អាចធ្វើឲ្យមាសថយចុះបន្តិច"
            else:
                level, gold_impact, summary_km = "LOW", "NEUTRAL", "ស្ថានភាពភូមិសាស្ត្រនយោបាយស្ងប់ស្ងាត់ធម្មតា"

            f_time = _format_dual_time(now_utc)
            result = {
                "level": level, "score": norm, "gold_impact": gold_impact,
                "summary_km": summary_km, "count": len(items), "headlines": items[:6],
                "fetched_kh": f_time["kh"], "fetched_utc": f_time["utc"], "fetched_ny": f_time["ny"],
                "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
            }
            with self._lock:
                self._war_cache = {"ts": time.monotonic(), "data": result}
            return result
        finally:
            self._war_fetch_lock.release()

    def _empty_war_news(self) -> dict:
        f_time = _format_dual_time(datetime.now(timezone.utc))
        return {
            "level": "LOW", "score": 0.0, "gold_impact": "NEUTRAL",
            "summary_km": "កំពុងតាមដានស្ថានភាពភូមិសាស្ត្រនយោបាយ...",
            "count": 0, "headlines": [],
            "fetched_kh": f_time["kh"], "fetched_utc": f_time["utc"], "fetched_ny": f_time["ny"],
            "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
        }

    def _get_external_context_fast(self) -> tuple[dict, dict, dict, pd.DataFrame | None, pd.DataFrame | None]:
        now = time.monotonic()
        with self._lock:
            news_ok = self._news_cache["data"] is not None and (now - self._news_cache["ts"]) < 300.0
            war_ok = self._war_cache["data"] is not None and (now - self._war_cache["ts"]) < 300.0
            fred_ok = self._fred_cache["data"] is not None and (now - self._fred_cache["ts"]) < 3600.0
            macro_ok = self._macro_cache["feat"] is not None and (now - self._macro_cache["ts"]) < 1800.0
            if news_ok and war_ok and fred_ok and macro_ok:
                return (
                    self._news_cache["data"], self._war_cache["data"], self._fred_cache["data"],
                    self._macro_cache["raw"], self._macro_cache["feat"]
                )

        with ThreadPoolExecutor(max_workers=4) as ex:
            f_news = ex.submit(self.fetch_global_news)
            f_war = ex.submit(self.fetch_war_news)
            f_fred = ex.submit(self.fetch_fred_macro)
            f_macro = ex.submit(self._get_macro)
            raw_m, feat_m = f_macro.result()
            return f_news.result(), f_war.result(), f_fred.result(), raw_m, feat_m

    # ==================== PILLAR 1: 10-YEAR V8 ENSEMBLE + CONSENSUS ====================
    def _score_row_detailed(self, row_df: pd.DataFrame) -> dict:
        models = self.bundle["models"]
        wts = self.bundle.get("weights", (0.30, 0.35, 0.35))
        feats = self.bundle["features"]
        X = row_df.reindex(columns=feats, fill_value=0.0).astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        lr, lg, cb = models
        p_lr, p_lg, p_cb = float(lr.predict_proba(X)[0, 1]), float(lg.predict_proba(X)[0, 1]), float(cb.predict_proba(X)[0, 1])
        prob = wts[0] * p_lr + wts[1] * p_lg + wts[2] * p_cb
        agreement = max(0.0, min(1.0, 1.0 - (float(np.std([p_lr, p_lg, p_cb])) * 3.0)))
        ref = self.bundle.get("ref_oof_scores")
        pct = float(np.searchsorted(ref, prob) / len(ref)) if (ref is not None and len(ref) > 0) else prob
        return {"prob": prob, "pct": max(0.0, min(1.0, pct)), "agreement": round(agreement, 2)}

    def _infer_v8_cached(self, df_1h: pd.DataFrame, feat_m: pd.DataFrame | None, ev_times, atr_chart: float) -> dict | None:
        if self.bundle is None or df_1h is None or len(df_1h) < 60:
            return None
        try:
            cache_key = (
                str(df_1h.iloc[-2].get("datetime", df_1h.index[-2])),
                round(_safe_float(df_1h.iloc[-2].get("close"), 0.0), 2),
                self.bundle.get("version", "V8"),
            )
        except Exception:
            cache_key = None

        with self._lock:
            if cache_key is not None and self._v8_bar_cache["key"] == cache_key and self._v8_bar_cache["res"] is not None:
                return self._v8_bar_cache["res"]

        feat_df = core.build_features(df_1h, feat_m, ev_times) if feat_m is not None else pd.DataFrame()
        if len(feat_df) < 2:
            return None

        closed_row = feat_df.iloc[-2:-1]
        r0 = closed_row.iloc[0]
        cand_side = int(_safe_float(r0.get("cand_side"), 0))
        atr_1h = max(_safe_float(r0.get("atr_raw"), atr_chart), 0.1)
        bar_dt = r0["datetime"].to_pydatetime() if hasattr(r0.get("datetime"), "to_pydatetime") else None
        bt = _format_dual_time(bar_dt)

        buy_row = closed_row.copy(); buy_row["cand_side"] = 1; d_buy = self._score_row_detailed(buy_row)
        sell_row = closed_row.copy(); sell_row["cand_side"] = -1; d_sell = self._score_row_detailed(sell_row)

        if cand_side == 1:
            ai_prob, ai_pct, agreement = d_buy["prob"], d_buy["pct"], d_buy["agreement"]
        elif cand_side == -1:
            ai_prob, ai_pct, agreement = d_sell["prob"], d_sell["pct"], d_sell["agreement"]
        elif d_buy["pct"] >= d_sell["pct"]:
            ai_prob, ai_pct, agreement = d_buy["prob"], d_buy["pct"], d_buy["agreement"]
        else:
            ai_prob, ai_pct, agreement = d_sell["prob"], d_sell["pct"], d_sell["agreement"]

        res = {
            "r0": r0, "cand_side": cand_side, "atr_1h": atr_1h,
            "bar_time": f"{bt['kh']} KH ({bt['utc']})",
            "d_buy": d_buy, "d_sell": d_sell,
            "ai_prob": ai_prob, "ai_pct": ai_pct, "agreement": agreement,
        }
        if cache_key is not None:
            with self._lock:
                self._v8_bar_cache = {"key": cache_key, "res": res}
        return res

    # ==================== PILLAR 2: MICROSTRUCTURE + REAL ORDER FLOW + CLOSE CONFIRMATION ====================
    def _analyze_microstructure(
        self, candles: list, price: float, ema20: float, ema50: float, atr_val: float, smc: dict, order_flow: dict | None = None
    ) -> dict:
        res = {
            "buy_pts": 0, "sell_pts": 0,
            "notes_buy": [], "notes_sell": [],
            "bull_reversal": False, "bear_reversal": False,
            "close_confirmed_buy": False, "close_confirmed_sell": False,
            "buy_pressure_pct": 50, "order_flow_source": "CANDLES",
            "falling_knife": False, "rising_rocket": False,
            "sweep_note": None, "climax": None,
        }
        if not candles or len(candles) < 8:
            return res

        c0, c1, c2 = candles[-1], candles[-2], candles[-3]
        o0, h0, l0, cl0 = _safe_float(c0.get("open")), _safe_float(c0.get("high")), _safe_float(c0.get("low")), _safe_float(c0.get("close"), price)
        o1, h1, l1, cl1 = _safe_float(c1.get("open")), _safe_float(c1.get("high")), _safe_float(c1.get("low")), _safe_float(c1.get("close"))
        o2, h2, l2, cl2 = _safe_float(c2.get("open")), _safe_float(c2.get("high")), _safe_float(c2.get("low")), _safe_float(c2.get("close"))
        if h1 <= 0 or l1 <= 0 or h1 < l1:
            return res

        # 0) គណនា Buy/Sell Pressure (%) ដោយរួមបញ្ចូល Real-Time Order Flow (Binance aggTrade + OKX) ជាមួយទៀន 5 ដើម
        buy_vol, tot_vol = 0.0, 0.0
        for c in candles[-5:]:
            ch, cl_l, cc = _safe_float(c.get("high")), _safe_float(c.get("low")), _safe_float(c.get("close"))
            rng = max(ch - cl_l, 0.01)
            vol = _safe_float(c.get("volume"), 0.0)
            wt = vol if vol > 0 else rng
            buy_ratio = max(0.0, min(1.0, (cc - cl_l) / rng))
            buy_vol += buy_ratio * wt
            tot_vol += wt
        candle_press = (buy_vol / tot_vol) * 100.0 if tot_vol > 0 else 50.0

        of_buy = _safe_float((order_flow or {}).get("buy_pct"), -1.0)
        if 0.0 <= of_buy <= 100.0 and int((order_flow or {}).get("trades_count", 0)) >= 10:
            # ផ្តល់ទម្ងន់ 65% លើ Real-Time Order Flow ពិត + 35% លើទៀន 5 ដើម
            buy_press = int(round(of_buy * 0.65 + candle_press * 0.35))
            res["order_flow_source"] = str(order_flow.get("source") or "LIVE_FLOW")
        else:
            buy_press = int(round(candle_press))

        res["buy_pressure_pct"] = buy_press
        if buy_press >= 60:
            res["buy_pts"] += 7
            res["notes_buy"].append(f"Order Flow កម្លាំងទិញឈ្នះ ({buy_press}%)")
        elif buy_press <= 40:
            res["sell_pts"] += 7
            res["notes_sell"].append(f"Order Flow កម្លាំងលក់ឈ្នះ ({100 - buy_press}%)")

        rng1, body1 = max(h1 - l1, 0.01), abs(cl1 - o1)
        up_wick1, lo_wick1 = h1 - max(o1, cl1), min(o1, cl1) - l1
        atr_ref = max(_safe_float(atr_val, 2.0), 0.6)
        c1_green, c1_red = cl1 > o1, cl1 < o1
        c0_green, c0_red = cl0 > o0, cl0 < o0

        # 1) Candle Close Confirmation (ទៀនបិទចុងក្រោយបញ្ជាក់ទិសដៅពិតប្រាកដ)
        ema_top, ema_bot = max(ema20, ema50), min(ema20, ema50)
        if c1_green and cl1 > ema20 and body1 >= rng1 * 0.35:
            res["close_confirmed_buy"] = True
        if c1_red and cl1 < ema20 and body1 >= rng1 * 0.35:
            res["close_confirmed_sell"] = True

        # 2) Liquidity Sweep / Stop Hunt
        prev_window = candles[-16:-2]
        prev_low = min((_safe_float(c.get("low"), 1e9) for c in prev_window), default=l1)
        prev_high = max((_safe_float(c.get("high"), 0.0) for c in prev_window), default=h1)
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

        # 3) Recent SMC Break (BOS / CHoCH ក្នុង 6 ទៀនចុងក្រោយ)
        last_ts, prev6_ts = _safe_float(c0.get("time"), 0.0), _safe_float(candles[-6].get("time"), 0.0)
        for br in reversed(smc.get("breaks") or []):
            end_t = _safe_float(br.get("end_time"), 0.0)
            if prev6_ts <= end_t <= last_ts + 86400:
                btype, lbl = str(br.get("type") or ""), br.get("label") or "BOS"
                if btype.endswith("_UP"):
                    res["buy_pts"] += 10
                    res["notes_buy"].append(f"SMC បែករចនាសម្ព័ន្ធឡើង ({lbl})")
                elif btype.endswith("_DOWN") or btype.endswith("_DN"):
                    res["sell_pts"] += 10
                    res["notes_sell"].append(f"SMC បែករចនាសម្ព័ន្ធចុះ ({lbl})")
                break

        # 4) Reversal & Anti-Falling-Knife
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

        # 5) EMA Structure + Momentum + Breakout
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

    # ==================== PILLAR 3: FRED MACRO + NEWS + GEOPOLITICAL ====================
    def _score_fundamentals(
        self, dxy_r1: float, us10y_d5: float, vix_val: float | None,
        fred_macro: dict, global_news: dict, war_news: dict
    ) -> dict:
        buy_f, sell_f = 0, 0
        notes_buy, notes_sell = [], []

        if dxy_r1 <= -0.30:
            buy_f += 6; notes_buy.append(f"DXY ចុះខ្សោយ ({dxy_r1:+.2f}%)")
        elif dxy_r1 <= -0.10:
            buy_f += 3
        elif dxy_r1 >= 0.30:
            sell_f += 6; notes_sell.append(f"DXY ឡើងខ្លាំង ({dxy_r1:+.2f}%)")
        elif dxy_r1 >= 0.10:
            sell_f += 3

        eff_us10y_d5 = fred_macro.get("us10y_chg5") if fred_macro.get("us10y") is not None else us10y_d5
        if eff_us10y_d5 <= -0.08:
            buy_f += 4; notes_buy.append("US10Y ធ្លាក់ចុះ")
        elif eff_us10y_d5 >= 0.08:
            sell_f += 4; notes_sell.append("US10Y ហក់ឡើង")

        # FRED Official Macro Bias (US10Y Real Yield + CPI + Unemployment + Fed Rate)
        f_bias, f_score = fred_macro.get("bias", "NEUTRAL"), _safe_float(fred_macro.get("score"), 0.0)
        if f_bias == "BULLISH":
            buy_f += 5; notes_buy.append(f"FRED Macro គាំទ្រ BUY ({f_score:+.2f})")
        elif f_bias == "BEARISH":
            sell_f += 5; notes_sell.append(f"FRED Macro គាំទ្រ SELL ({f_score:+.2f})")

        if vix_val is not None and vix_val >= 22.0:
            buy_f += 2

        g_bias, g_score = global_news.get("bias", "NEUTRAL"), _safe_float(global_news.get("score"), 0.0)
        if g_bias == "BULLISH":
            buy_f += 5; notes_buy.append(f"ព័ត៌មានមាសគាំទ្រ BUY ({g_score:+.2f})")
        elif g_bias == "BEARISH":
            sell_f += 5; notes_sell.append(f"ព័ត៌មានមាសគាំទ្រ SELL ({g_score:+.2f})")

        w_level, w_impact = war_news.get("level", "LOW"), war_news.get("gold_impact", "NEUTRAL")
        if w_level == "HIGH" and w_impact == "BULLISH":
            buy_f += 4; notes_buy.append("សង្គ្រាម HIGH ជំរុញ Safe-Haven")
        elif w_level == "MEDIUM" and w_impact == "BULLISH":
            buy_f += 2
        elif w_level == "COOLING":
            sell_f += 3; notes_sell.append("សង្គ្រាមធូរស្រាល")

        return {"buy_pts": min(25, buy_f), "sell_pts": min(25, sell_f), "notes_buy": notes_buy, "notes_sell": notes_sell}

    def _compute_true_triggers(self, price: float, spread: float, ema20: float, ema50: float, candles: list, smc: dict) -> tuple[float, float]:
        """ធានា 100% ថា BUY Trigger > price ហើយ SELL Trigger < price ជានិច្ច"""
        min_gap = max(spread * 1.2, 0.35)
        recent = candles[-12:] if candles else []
        swing_hi = max((_safe_float(c.get("high"), price) for c in recent), default=price + 1.5)
        swing_lo = min((_safe_float(c.get("low"), price) for c in recent), default=price - 1.5)

        up_cands = [x for x in (ema20, ema50, swing_hi) if x > price + min_gap]
        for sob in (smc.get("supply_blocks") or []):
            t = _safe_float(sob.get("top"), 0.0)
            if t > price + min_gap:
                up_cands.append(t)
        buy_trig = round(min(up_cands) if up_cands else (price + max(min_gap, 1.0)), 2)

        dn_cands = [x for x in (ema20, ema50, swing_lo) if 0 < x < price - min_gap]
        for ob in (smc.get("demand_blocks") or []):
            b = _safe_float(ob.get("bottom"), 0.0)
            if 0 < b < price - min_gap:
                dn_cands.append(b)
        sell_trig = round(max(dn_cands) if dn_cands else (price - max(min_gap, 1.0)), 2)
        return buy_trig, sell_trig

    def _describe_market(self, r0, payload: dict, global_news: dict, war_news: dict, session_kz: dict) -> dict:
        price = _safe_float(payload.get("price"), 0.0)
        ema20, ema50 = _safe_float(payload.get("ema20"), price), _safe_float(payload.get("ema50"), price)
        rsi, atr = _safe_float(payload.get("rsi"), 50.0), _safe_float(payload.get("atr"), 2.5)
        smc_bias = (payload.get("smc") or {}).get("bias", "NEUTRAL")
        adx = _safe_float(r0.get("adx_14"), 20.0) if (r0 is not None and pd.notna(r0.get("adx_14"))) else 20.0

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

    def _build_precision_plan(
        self, side: str, price: float, payload: dict, atr_chart: float, spread: float, inst: dict, grade: str, is_scalp: bool = False
    ) -> dict | None:
        if side not in ("BUY", "SELL") or price <= 0:
            return None

        candles = payload.get("candles") or []
        ema20 = _safe_float(payload.get("ema20"), price)
        smc = payload.get("smc") or {}
        atr_val = max(_safe_float(atr_chart, 2.0), 0.6)
        spd = max(_safe_float(spread, 0.30), 0.15)

        recent = candles[-(12 if is_scalp else 18):]
        swing_low = min((_safe_float(c.get("low"), price) for c in recent), default=price - atr_val * 1.4)
        swing_high = max((_safe_float(c.get("high"), price) for c in recent), default=price + atr_val * 1.4)

        stretch = (price - ema20) if side == "BUY" else (ema20 - price)
        if stretch <= atr_val * 0.95:
            entry_state, entry_km, entry_p = "IN_ZONE", "ចូលបានឥឡូវ", round(price, 2)
        elif stretch <= atr_val * 1.80:
            entry_state, entry_km = "PULLBACK", "ដាក់ Limit នៅ EMA"
            entry_p = round(ema20 + atr_val * 0.25, 2) if side == "BUY" else round(ema20 - atr_val * 0.25, 2)
        else:
            entry_state, entry_km = "EXTENDED", "ហួសចំណុច (ដាក់ Limit)"
            entry_p = round(ema20 + atr_val * 0.20, 2) if side == "BUY" else round(ema20 - atr_val * 0.20, 2)

        # ធានា SL ធំជាង Spread គ្រប់គ្រាន់ (យ៉ាងតិច 4x Spread)
        min_sl = max(atr_val * (0.95 if is_scalp else 1.10), spd * 4.0, 2.00)
        max_sl = max(min_sl * 1.85, atr_val * (1.75 if is_scalp else 2.20))
        buf = max(atr_val * 0.25, spd * 0.8)
        tp1_mult, tp2_mult = (1.65, 2.60) if is_scalp else (2.0, 3.2)

        if side == "BUY":
            anchor_low = swing_low
            for ob in (smc.get("demand_blocks") or []):
                ob_bot = _safe_float(ob.get("bottom"), 0.0)
                if 0 < ob_bot < entry_p and (entry_p - ob_bot) <= max_sl * 1.1:
                    anchor_low = min(anchor_low, ob_bot)
                    break
            sl_dist = max(min_sl, min(max_sl, entry_p - (anchor_low - buf)))
            sl_p = round(entry_p - sl_dist, 2)
            tp1_p = round(entry_p + sl_dist * tp1_mult, 2)
            tp2_p = round(entry_p + sl_dist * tp2_mult, 2)
            for sob in (smc.get("supply_blocks") or []):
                sob_bot = _safe_float(sob.get("bottom"), 0.0)
                if entry_p + sl_dist * 1.50 < sob_bot < tp1_p:
                    tp1_p = round(sob_bot - 0.35, 2); break
            pdh = _safe_float(inst.get("pdh"), 0.0)
            if pdh > 0 and entry_p + sl_dist * 1.50 < pdh < tp1_p:
                tp1_p = round(pdh - 0.35, 2)
            if tp1_p <= entry_p + 0.5:
                tp1_p = round(entry_p + sl_dist * 1.6, 2)
            if tp2_p <= tp1_p + 0.6:
                tp2_p = round(tp1_p + sl_dist, 2)
            be_trigger = round(entry_p + (tp1_p - entry_p) * 0.50, 2)
        else:
            anchor_high = swing_high
            for sob in (smc.get("supply_blocks") or []):
                sob_top = _safe_float(sob.get("top"), 0.0)
                if sob_top > entry_p and (sob_top - entry_p) <= max_sl * 1.1:
                    anchor_high = max(anchor_high, sob_top)
                    break
            sl_dist = max(min_sl, min(max_sl, (anchor_high + buf) - entry_p))
            sl_p = round(entry_p + sl_dist, 2)
            tp1_p = round(entry_p - sl_dist * tp1_mult, 2)
            tp2_p = round(entry_p - sl_dist * tp2_mult, 2)
            for ob in (smc.get("demand_blocks") or []):
                ob_top = _safe_float(ob.get("top"), 0.0)
                if tp1_p < ob_top < entry_p - sl_dist * 1.50:
                    tp1_p = round(ob_top + 0.35, 2); break
            pdl = _safe_float(inst.get("pdl"), 0.0)
            if pdl > 0 and tp1_p < pdl < entry_p - sl_dist * 1.50:
                tp1_p = round(pdl + 0.35, 2)
            if tp1_p >= entry_p - 0.5:
                tp1_p = round(entry_p - sl_dist * 1.6, 2)
            if tp2_p >= tp1_p - 0.6:
                tp2_p = round(tp1_p - sl_dist, 2)
            be_trigger = round(entry_p - (entry_p - tp1_p) * 0.50, 2)

        rr1_val = round(abs(tp1_p - entry_p) / max(sl_dist, 0.1), 2)
        rr2_val = round(abs(tp2_p - entry_p) / max(sl_dist, 0.1), 2)
        risk_pct = 1.0 if grade == "A+" else (0.75 if grade == "A" else 0.5)
        return {
            "entry": entry_p, "sl": sl_p, "tp": tp1_p, "tp1": tp1_p, "tp2": tp2_p,
            "be_trigger": be_trigger, "partial_close_pct": 60,
            "sl_dist": round(sl_dist, 2), "sl_pips": int(round(sl_dist * 10)),
            "tp_pips": int(round(abs(tp1_p - entry_p) * 10)), "tp2_pips": int(round(abs(tp2_p - entry_p) * 10)),
            "rr_num": rr1_val, "rr": f"{rr1_val:.1f} : 1", "rr2": f"{rr2_val:.1f} : 1",
            "entry_state": entry_state, "entry_km": entry_km,
            "exit_km": f"ដល់ ${be_trigger:.2f} រំកិល SL ស្មើដើម • កាត់ 60% នៅ ${tp1_p:.2f} ទុកសល់រត់ទៅ ${tp2_p:.2f}",
            "suggested_risk_pct": risk_pct,
        }

    def evaluate(self, df_1h: pd.DataFrame, payload: dict, session: requests.Session | None = None) -> dict:
        self._ensure_model()
        global_news, war_news, fred_macro, raw_m, feat_m = self._get_external_context_fast()

        hard_blocks, trap_blocks, soft_warnings = [], [], []
        buy_reasons, sell_reasons = [], []

        price = _safe_float(payload.get("price"), 0.0)
        spread = _safe_float(payload.get("spread"), 0.25)
        rsi = _safe_float(payload.get("rsi"), 50.0)
        ema20 = _safe_float(payload.get("ema20"), price)
        ema50 = _safe_float(payload.get("ema50"), price)
        atr_chart = max(_safe_float(payload.get("atr"), 2.0), 0.6)
        interval_key = str(payload.get("interval") or "15m")
        cal_news = payload.get("news") or {}
        mtf = payload.get("mtf") or {}
        inst = payload.get("inst_levels") or {}
        smc = payload.get("smc") or {}
        candles = payload.get("candles") or []
        order_flow = payload.get("order_flow") or {}
        sanity = payload.get("sanity") or {}

        now_utc = datetime.now(timezone.utc)
        session_kz = _get_session_killzone(now_utc)

        macro_snapshot = {
            "dxy": None,
            "us10y": fred_macro.get("us10y"),
            "vix": None,
            "real10": fred_macro.get("real10"),
            "cpi_yoy": fred_macro.get("cpi_yoy"),
            "unemployment": fred_macro.get("unemployment"),
            "fed_rate": fred_macro.get("fed_rate"),
            "fred_bias": fred_macro.get("bias", "NEUTRAL"),
        }
        dxy_r1, us10y_d5 = 0.0, 0.0
        ai_prob, ai_pct, cand_side, bar_time, v8_agreement = None, None, 0, None, 0.0
        v8_res, r0 = None, None

        if raw_m is not None and not raw_m.empty:
            last_m = raw_m.iloc[-1]
            macro_snapshot["dxy"] = round(float(last_m["dxy"]), 2) if pd.notna(last_m.get("dxy")) else None
            macro_snapshot["vix"] = round(float(last_m["vix"]), 2) if pd.notna(last_m.get("vix")) else None
            if macro_snapshot["us10y"] is None and pd.notna(last_m.get("us10y")):
                macro_snapshot["us10y"] = round(float(last_m["us10y"]), 2)
            if macro_snapshot["real10"] is None and pd.notna(last_m.get("real10")):
                macro_snapshot["real10"] = round(float(last_m["real10"]), 2)
        if feat_m is not None and not feat_m.empty:
            lf = feat_m.iloc[-1]
            dxy_r1, us10y_d5 = _safe_float(lf.get("dxy_r1"), 0.0), _safe_float(lf.get("us10y_d5"), 0.0)

        # ---------- PILLAR 1: 10-YEAR V8 ENSEMBLE (Max 30 pts) ----------
        p1_buy, p1_sell, v8_confirmed_side = 0, 0, "NONE"
        min_pct = _safe_float(self.bundle.get("min_percentile", 0.50), 0.50) if self.bundle else 0.50
        a_plus_pct = _safe_float(self.bundle.get("grade_a_plus_pct", 0.65), 0.65) if self.bundle else 0.65

        if self.bundle is None:
            soft_warnings.append("ម៉ូដែល V8 មិនទាន់ផ្ទុក — កំពុងប្រើ Quant Structure")
        elif df_1h is not None and len(df_1h) >= 60:
            try:
                v8_res = self._infer_v8_cached(df_1h, feat_m, self._get_events(), atr_chart)
                if v8_res:
                    r0, cand_side, bar_time = v8_res["r0"], v8_res["cand_side"], v8_res["bar_time"]
                    ai_prob, ai_pct, v8_agreement = v8_res["ai_prob"], v8_res["ai_pct"], v8_res["agreement"]
                    d_buy, d_sell = v8_res["d_buy"], v8_res["d_sell"]
                    ag_mult = 0.65 + (0.35 * v8_agreement)
                    if cand_side == 1 and d_buy["pct"] >= min_pct:
                        v8_confirmed_side, p1_buy = "BUY", int(round(30 * ag_mult))
                        buy_reasons.append(f"V8 អនុម័ត BUY (Rank {d_buy['pct']*100:.0f}%)")
                    elif cand_side == -1 and d_sell["pct"] >= min_pct:
                        v8_confirmed_side, p1_sell = "SELL", int(round(30 * ag_mult))
                        sell_reasons.append(f"V8 អនុម័ត SELL (Rank {d_sell['pct']*100:.0f}%)")
                    else:
                        diff_pct = d_buy["pct"] - d_sell["pct"]
                        if d_buy["pct"] >= min_pct and diff_pct >= 0.04:
                            p1_buy = int(round(18 * ag_mult)); buy_reasons.append(f"V8 ផ្អៀង BUY ({d_buy['pct']*100:.0f}%)")
                        elif d_sell["pct"] >= min_pct and diff_pct <= -0.04:
                            p1_sell = int(round(18 * ag_mult)); sell_reasons.append(f"V8 ផ្អៀង SELL ({d_sell['pct']*100:.0f}%)")
            except Exception as e:
                soft_warnings.append(f"V8 Sync: {str(e)[:50]}")

        market_state = self._describe_market(r0, payload, global_news, war_news, session_kz)

        # ---------- HARD SAFETY GATES (LAYER 3 + GLITCH PROTECTION) ----------
        if price <= 0:
            hard_blocks.append("មិនទាន់ទទួលបានតម្លៃមាសផ្ទាល់")
        if _is_forex_weekend_closed(now_utc):
            hard_blocks.append("ទីផ្សារ Forex បិទចុងសប្តាហ៍ (បើកអាទិត្យ 22:00 UTC)")
        if sanity.get("glitch_detected"):
            hard_blocks.append(f"រកឃើញតម្លៃលោតខុសប្រក្រតី ({sanity.get('note', 'Cross-Source Mismatch')}) — ផ្អាកការជួញដូរ")
        if cal_news.get("danger"):
            ev = cal_news.get("danger_event") or {}
            hard_blocks.append(f"ព័ត៌មានក្រហម USD ({ev.get('title', 'Red News')}) ជិតចេញ")
        if spread > 1.50:
            hard_blocks.append(f"Spread រីកខ្ពស់ខ្លាំង (${spread:.2f}) — ហានិភ័យ Slippage")
        elif spread > 0.80:
            soft_warnings.append(f"Spread រាងធំ (${spread:.2f}) — គួរប្រើ Lot តូច (0.5%)")
        if interval_key in ("1m", "5m"):
            soft_warnings.append(f"Timeframe {interval_key} មាន Noise ខ្ពស់ — 15m/1H មានស្ថិរភាពជាង")

        # ---------- PILLAR 2: MICROSTRUCTURE + ORDER FLOW + SMC + MTF (Max 45 pts) ----------
        ms = self._analyze_microstructure(candles, price, ema20, ema50, atr_chart, smc, order_flow=order_flow)
        p2_buy, p2_sell = ms["buy_pts"], ms["sell_pts"]
        buy_reasons.extend(ms["notes_buy"]); sell_reasons.extend(ms["notes_sell"])

        smc_bias = smc.get("bias", "NEUTRAL")
        if smc_bias == "BULLISH":
            p2_buy += 8
        elif smc_bias == "BEARISH":
            p2_sell += 8

        tfs = {t.get("label"): t.get("bias") for t in (mtf.get("timeframes") or [])}
        htf_buy = sum(1 for k in ("1H", "4H", "1D") if tfs.get(k) == "BUY")
        htf_sell = sum(1 for k in ("1H", "4H", "1D") if tfs.get(k) == "SELL")
        if mtf.get("aligned") == "BUY":
            p2_buy += 14; buy_reasons.append("MTF 4 Timeframes ស្របទិស BUY")
        elif mtf.get("aligned") == "SELL":
            p2_sell += 14; sell_reasons.append("MTF 4 Timeframes ស្របទិស SELL")
        else:
            if htf_buy >= 2: p2_buy += 8
            if htf_sell >= 2: p2_sell += 8

        p2_buy, p2_sell = min(45, p2_buy), min(45, p2_sell)
        if ms["falling_knife"]: p1_buy = int(p1_buy * 0.40)
        if ms["rising_rocket"]: p1_sell = int(p1_sell * 0.40)

        # ---------- PILLAR 3: FRED MACRO + NEWS + WAR + KILLZONE (Max 25 pts) ----------
        fund = self._score_fundamentals(dxy_r1, us10y_d5, macro_snapshot.get("vix"), fred_macro, global_news, war_news)
        p3_buy, p3_sell = fund["buy_pts"], fund["sell_pts"]
        buy_reasons.extend(fund["notes_buy"]); sell_reasons.extend(fund["notes_sell"])

        buy_pts = max(0, min(100, p1_buy + p2_buy + p3_buy + (session_kz["bonus"] if p2_buy > p2_sell else 0)))
        sell_pts = max(0, min(100, p1_sell + p2_sell + p3_sell + (session_kz["bonus"] if p2_sell > p2_buy else 0)))
        if rsi >= 72.0: buy_pts = max(0, buy_pts - 8)
        if rsi <= 28.0: sell_pts = max(0, sell_pts - 8)

        # ---------- DECISION & INSTITUTIONAL CONFLUENCE ----------
        margin = buy_pts - sell_pts
        with self._lock:
            prev_mem = self._last_signal_mem.get(interval_key)
        was_same = prev_mem is not None and (time.monotonic() - prev_mem["ts"]) < 90.0
        base_thresh = 50 if interval_key in ("1m", "5m") else 48
        buy_thresh = (base_thresh - 4) if (was_same and prev_mem["side"] == "BUY") else base_thresh
        sell_thresh = (base_thresh - 4) if (was_same and prev_mem["side"] == "SELL") else base_thresh

        candidate_side, raw_score, pass_reasons = "NONE", max(buy_pts, sell_pts), []
        if buy_pts >= buy_thresh and margin >= 14:
            candidate_side, raw_score, pass_reasons = "BUY", buy_pts, buy_reasons
        elif sell_pts >= sell_thresh and (-margin) >= 14:
            candidate_side, raw_score, pass_reasons = "SELL", sell_pts, sell_reasons

        is_scalp = (candidate_side != "NONE") and (mtf.get("aligned") != candidate_side or raw_score < 64)
        ema_top, ema_bot = max(ema20, ema50), min(ema20, ema50)
        bias_for_preview = candidate_side if candidate_side != "NONE" else ("BUY" if price > ema_top else ("SELL" if price < ema_bot else ("BUY" if buy_pts >= sell_pts else "SELL")))
        preview_plan = self._build_precision_plan(bias_for_preview, price, payload, atr_chart, spread, inst, "A", is_scalp=is_scalp) if price > 0 else None

        # ---------- របាំងដែក Sniper (Layers 1 & 4) ----------
        real_risk_blocks = []
        if candidate_side == "BUY":
            if price < ema_bot:
                real_risk_blocks.append(f"ហាម BUY បញ្ច្រាស Trend៖ តម្លៃនៅក្រោម EMA 20/50 (${ema_bot:.2f})")
            elif ms["falling_knife"] or (price < ema20 and not ms["bull_reversal"]):
                real_risk_blocks.append("ហាមចាប់កាំបិតធ្លាក់ — ចាំឡើងផុត EMA 20 សិន")
            if rsi >= 76.0:
                real_risk_blocks.append(f"RSI ({rsi:.0f}) ខ្ពស់ជ្រុល — ចាំថយសិន")
            if preview_plan and preview_plan.get("rr_num", 2.0) < 1.50:
                trap_blocks.append(f"R:R ទាប ({preview_plan['rr']}) — ត្រូវការយ៉ាងតិច 1.5 : 1")
        elif candidate_side == "SELL":
            if price > ema_top:
                real_risk_blocks.append(f"ហាម SELL បញ្ច្រាស Trend៖ តម្លៃនៅលើ EMA 20/50 (${ema_top:.2f})")
            elif ms["rising_rocket"] or (price > ema20 and not ms["bear_reversal"]):
                real_risk_blocks.append("ហាមទប់ឡាន — ចាំធ្លាក់ក្រោម EMA 20 សិន")
            if rsi <= 24.0:
                real_risk_blocks.append(f"RSI ({rsi:.0f}) ទាបជ្រុល — ចាំងើបសិន")
            if preview_plan and preview_plan.get("rr_num", 2.0) < 1.50:
                trap_blocks.append(f"R:R ទាប ({preview_plan['rr']}) — ត្រូវការយ៉ាងតិច 1.5 : 1")

        # គណនា Trigger ពិតប្រាកដ (BUY > price, SELL < price 100%)
        buy_trig, sell_trig = self._compute_true_triggers(price, spread, ema20, ema50, candles, smc)

        high_conf_alert, sniper_text_km, close_confirmed = False, "", False
        confluence_checks = {"trend_close": False, "orderflow_mtf": False, "macro_news": not bool(hard_blocks), "risk_rr": False}

        if hard_blocks or real_risk_blocks:
            status, can_trade, grade, final_side, is_danger = "BLOCKED", False, "RISK", "NONE", True
            confidence_score = 20
            action_km = "⛔ ហាមចូល (មានហានិភ័យខ្ពស់)"
            reasons = hard_blocks + real_risk_blocks + soft_warnings
            hud_hint_km, toast_detail_km, plan = f"⛔ {reasons[0]}", reasons[0], None
        elif candidate_side == "NONE" or trap_blocks:
            status, can_trade, grade, final_side, is_danger = "WAIT", False, "WATCH", "NONE", False
            confidence_score = min(58, max(35, raw_score))
            action_km = f"⏳ ត្រៀមចូល (ផ្អៀង {bias_for_preview})"
            reasons = trap_blocks if trap_blocks else [f"រង់ចាំបែកលើ ${buy_trig:.2f} ដើម្បី BUY ឬក្រោម ${sell_trig:.2f} ដើម្បី SELL"]
            hud_hint_km, toast_detail_km, plan = f"BUY > ${buy_trig:.2f} • SELL < ${sell_trig:.2f}", reasons[0], None
        else:
            final_side, can_trade, is_danger, status = candidate_side, True, False, f"APPROVED_{candidate_side}"
            close_confirmed = ms["close_confirmed_buy"] if final_side == "BUY" else ms["close_confirmed_sell"]
            with self._lock:
                self._last_signal_mem[interval_key] = {"side": final_side, "ts": time.monotonic()}

            if v8_confirmed_side == final_side and ai_pct is not None and ai_pct >= a_plus_pct and raw_score >= 76:
                grade = "A+"
            elif raw_score >= 68:
                grade = "A"
            elif raw_score >= 56:
                grade = "B+"
            else:
                grade = "B"

            confidence_score = raw_score
            plan = self._build_precision_plan(final_side, price, payload, atr_chart, spread, inst, grade, is_scalp=(grade == "B"))

            # ផ្ទៀងផ្ទាត់ 4-Layer Institutional Confluence Rules
            mtf_not_opp = (final_side == "BUY" and mtf.get("aligned") != "SELL" and htf_sell < 2) or (final_side == "SELL" and mtf.get("aligned") != "BUY" and htf_buy < 2)
            flow_aligned = (final_side == "BUY" and ms["buy_pressure_pct"] >= 55) or (final_side == "SELL" and ms["buy_pressure_pct"] <= 45)
            risk_ok = bool(plan and plan.get("rr_num", 0.0) >= 1.50 and plan.get("sl_dist", 0.0) >= max(spread * 4.0, 1.5))

            confluence_checks = {
                "trend_close": bool(close_confirmed),
                "orderflow_mtf": bool(flow_aligned and mtf_not_opp),
                "macro_news": bool(not hard_blocks and spread <= 1.20),
                "risk_rr": risk_ok,
            }

            if (
                grade in ("A+", "A") and confidence_score >= 68
                and all(confluence_checks.values())
                and plan and plan.get("entry_state") == "IN_ZONE"
            ):
                high_conf_alert = True
                act_word = "ទិញ (BUY)" if final_side == "BUY" else "លក់ (SELL)"
                sniper_text_km = (
                    f"🔥 ឱកាសខ្ពស់ {confidence_score}% ({grade}) • គួរ{act_word} នៅ ${plan['entry']:.2f} "
                    f"| SL ${plan['sl']:.2f} | TP1 ${plan['tp']:.2f} (Risk {plan['suggested_risk_pct']}%)"
                )

            side_km = "ទិញ (BUY)" if final_side == "BUY" else "លក់ (SELL)"
            conf_tag = "បិទទៀនបញ្ជាក់" if close_confirmed else "ទៀនកំពុងរត់"
            if plan and plan["entry_state"] == "PULLBACK":
                action_km = f"ដាក់ {side_km} Limit នៅ ${plan['entry']:.2f} • {grade}"
                hud_hint_km = f"⏳ {final_side} Limit @ ${plan['entry']:.2f}"
            else:
                action_km = f"⚡ ចូល {side_km} • {grade} ({conf_tag})"
                hud_hint_km = f"{'🔥' if high_conf_alert else '⚡'} {final_side} ({grade} • {confidence_score}%)"
            reasons = (pass_reasons[:3] if pass_reasons else ["សញ្ញាបច្ចេកទេសស្របគ្នា"]) + soft_warnings
            toast_detail_km = sniper_text_km or " • ".join(reasons[:2])

        # អាប់ដេត Auto Forward-Test Win-Rate Tracker ពិតប្រាកដ (មានការពារ Same-Candle Fakeout)
        last_c = candles[-1] if candles else {}
        last_hi = _safe_float(last_c.get("high"), price)
        last_lo = _safe_float(last_c.get("low"), price)
        last_cts = int(_safe_float(last_c.get("time"), 0))
        sniper_rec = {"side": final_side, "entry": plan["entry"], "sl": plan["sl"], "tp": plan["tp"]} if (high_conf_alert and plan) else None
        signal_stats = self.tracker.update_and_record(interval_key, price, last_hi, last_lo, last_cts, sniper_rec)

        return {
            "version": f"{self.bundle.get('version', 'V8')}-QUANT-V10.4" if self.bundle else "V10.4-QUANT",
            "deployable": bool(self.bundle.get("deployable", False)) if self.bundle else False,
            "tactical_mode": "V8_CONFIRMED" if (can_trade and v8_confirmed_side == final_side) else ("SCALP" if (can_trade and grade == "B") else ("QUANT_CONFLUENCE" if can_trade else "WAIT")),
            "status": status, "grade": grade, "is_danger": is_danger,
            "high_conf_alert": high_conf_alert, "close_confirmed": close_confirmed,
            "buy_pressure_pct": ms["buy_pressure_pct"], "order_flow_source": ms["order_flow_source"],
            "confluence_checks": confluence_checks,
            "sniper_text_km": sniper_text_km,
            "confidence_score": int(confidence_score), "action_km": action_km,
            "hud_hint_km": hud_hint_km, "toast_detail_km": toast_detail_km,
            "can_trade": can_trade, "side": final_side,
            "ai_rank_pct": round(ai_pct * 100, 1) if ai_pct is not None else None,
            "ai_prob": round(ai_prob * 100, 1) if ai_prob is not None else None,
            "v8_agreement_pct": int(round(v8_agreement * 100)) if v8_res else None,
            "cutoff_pct": round(min_pct * 100, 0), "bar_time": bar_time,
            "session_edge": session_kz, "liquidity_sweep": ms["sweep_note"],
            "market_state": market_state, "reasons": reasons, "passed": pass_reasons, "warnings": soft_warnings,
            "plan": plan, "preview_plan": preview_plan, "signal_stats": signal_stats,
            "pillars": {
                "v8_ai": {"buy": p1_buy, "sell": p1_sell, "max": 30},
                "structure_smc": {"buy": p2_buy, "sell": p2_sell, "max": 45},
                "macro_news": {"buy": p3_buy, "sell": p3_sell, "max": 25},
            },
            "score_breakdown": {"buy_pts": buy_pts, "sell_pts": sell_pts, "margin": margin, "candidate_side": candidate_side},
            "macro": macro_snapshot, "fred_macro": fred_macro,
            "global_news": global_news, "war_news": war_news,
        }


brain_service = GoldBrainService()