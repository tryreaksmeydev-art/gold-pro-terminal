import os
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter


# ==================== AUTO-LOAD .env FILE ====================
def _load_dotenv_file():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)
    except Exception as e:
        print(f"[CONFIG] .env load warning: {e}")


_load_dotenv_file()


# ==================== TIMEZONE & FORMAT HELPERS ====================
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


def safe_float(val, default: float = 0.0, digits: int = 2) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return round(f, digits)
    except Exception:
        return default


# ==================== API KEYS & CONSTANTS ====================
APP_TITLE = "TRI PISAL • Gold XAU/USD Pro Terminal (V10.6 Modular)"
APP_VERSION = "10.6-modular-hardened"

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
TWELVE_DATA_KEY = (os.getenv("TWELVE_DATA_KEY") or os.getenv("TWELVE_DATA_API_KEY") or "").strip()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()

VALID_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
ALLOWED_PRESETS = {
    "brain_v8", "entry", "risk_plan", "deep_scan", "mtf_check",
    "news_check", "inst_levels", "trend", "pattern", "volatility", "summary",
}

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}


# ==================== SHARED HTTP POOL & ZERO-DEADLOCK EXECUTORS ====================
http_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=1)
http_session.mount("https://", _adapter)
http_session.mount("http://", _adapter)
http_session.headers.update({"User-Agent": "TriPisalGoldTerminal/10.6-Modular"})

# បំបែក 3 ThreadPools ដាច់ដោយឡែក ការពារ Deadlock ១០០%
mtf_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mtf")
extras_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="extras")
io_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="io")


def shutdown_resources():
    mtf_executor.shutdown(wait=False)
    extras_executor.shutdown(wait=False)
    io_executor.shutdown(wait=False)
    try:
        http_session.close()
    except Exception:
        pass