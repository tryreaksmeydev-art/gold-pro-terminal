import re
import math
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone


def _get_tz(name: str, fallback_hours: int):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=fallback_hours))


TZ_KH = _get_tz("Asia/Phnom_Penh", 7)
TZ_NY = _get_tz("America/New_York", -5)


def safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def normalize_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:90]


def recency_weight(pub_dt: datetime | None, now_utc: datetime) -> float:
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


def is_forex_weekend_closed(now_utc: datetime) -> bool:
    wd, hr = now_utc.weekday(), now_utc.hour
    return wd == 5 or (wd == 4 and hr >= 22) or (wd == 6 and hr < 22)


def get_session_killzone(now_utc: datetime) -> dict:
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


def format_dual_time(dt_utc: datetime | None) -> dict:
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


def parse_rss_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    s = date_str.strip()
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