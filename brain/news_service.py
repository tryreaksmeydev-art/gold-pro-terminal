import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from .config import (
    EVENTS_CSV,
    USER_AGENT,
    BULLISH_PATTERNS,
    BEARISH_PATTERNS,
    GOLD_FILTER_RE,
    WAR_HIGH_PATTERNS,
    WAR_MED_PATTERNS,
    WAR_COOL_PATTERNS,
    GLOBAL_RSS_FEEDS,
    WAR_RSS_FEEDS,
    GDELT_WAR_API,
)
from .utils import (
    normalize_title_key,
    recency_weight,
    format_dual_time,
    parse_rss_date,
)

try:
    import brain_core as core
except Exception:
    core = None


class ExternalContextService:
    """ទាញព័ត៌មានមាសពិភពលោក, សង្គ្រាម, Macro និង Events ដោយប្រើ Cache + Stale Fallback"""

    def __init__(self):
        self._lock = threading.Lock()
        self._macro_fetch_lock = threading.Lock()
        self._news_fetch_lock = threading.Lock()
        self._war_fetch_lock = threading.Lock()

        self._macro_cache = {"ts": 0.0, "raw": None, "feat": None}
        self._news_cache = {"ts": 0.0, "data": None}
        self._war_cache = {"ts": 0.0, "data": None}
        self._ev_cache = {"year": None, "times": None}

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=12, pool_maxsize=20, max_retries=0)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._session.headers.update({"User-Agent": USER_AGENT})

    def _http_get(self, url: str, timeout: tuple[float, float] = (2.0, 3.2)) -> requests.Response | None:
        try:
            r = self._session.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r
        except Exception:
            pass
        return None

    def _fetch_rss_items(self, source_name: str, url: str, limit: int = 16) -> list[tuple[str, str, str, str | None]]:
        r = self._http_get(url)
        if r is None:
            return []
        out = []
        try:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:limit]:
                t_el = item.find("title")
                l_el = item.find("link")
                p_el = item.find("pubDate")
                if t_el is not None and t_el.text:
                    out.append((
                        source_name,
                        t_el.text.strip(),
                        l_el.text.strip() if (l_el is not None and l_el.text) else "",
                        p_el.text.strip() if (p_el is not None and p_el.text) else None,
                    ))
        except Exception:
            return []
        return out

    # -------------------- MACRO & EVENTS --------------------
    def get_macro(self, ttl: float = 1800.0) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
        if core is None:
            return None, None
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
            print(f"[BRAIN:MACRO] fallback: {e}")
            return stale_raw, stale_feat
        finally:
            self._macro_fetch_lock.release()

    def get_events(self) -> list:
        if core is None:
            return []
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

    # -------------------- GLOBAL GOLD & MACRO NEWS --------------------
    def empty_global_news(self) -> dict:
        f_time = format_dual_time(datetime.now(timezone.utc))
        return {
            "bias": "NEUTRAL",
            "score": 0.0,
            "summary_km": "កំពុងភ្ជាប់ព័ត៌មានមាសពិភពលោក...",
            "count": 0,
            "headlines": [],
            "fetched_kh": f_time["kh"],
            "fetched_utc": f_time["utc"],
            "fetched_ny": f_time["ny"],
            "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
        }

    def fetch_global_news(self, ttl: float = 300.0) -> dict:
        now = time.monotonic()
        with self._lock:
            stale_data = self._news_cache["data"]
            if stale_data is not None and (now - self._news_cache["ts"]) < ttl:
                return stale_data
        if not self._news_fetch_lock.acquire(blocking=False):
            return stale_data or self.empty_global_news()

        try:
            now_utc = datetime.now(timezone.utc)
            raw_items = []
            with ThreadPoolExecutor(max_workers=len(GLOBAL_RSS_FEEDS)) as ex:
                futs = [ex.submit(self._fetch_rss_items, src, url, 14) for src, url in GLOBAL_RSS_FEEDS]
                for f in as_completed(futs):
                    try:
                        raw_items.extend(f.result())
                    except Exception:
                        pass

            headlines, seen_keys, total_score = [], set(), 0.0
            for source_name, title, link, pub_raw in raw_items:
                t_low = title.lower()
                if not GOLD_FILTER_RE.search(t_low):
                    continue
                key = normalize_title_key(title)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)

                raw_score = 0.0
                for pat, w in BULLISH_PATTERNS:
                    if pat.search(t_low):
                        raw_score += w
                for pat, w in BEARISH_PATTERNS:
                    if pat.search(t_low):
                        raw_score += w

                pub_dt = parse_rss_date(pub_raw)
                score = raw_score * recency_weight(pub_dt, now_utc)
                total_score += score
                impact = "BULLISH" if raw_score >= 1.5 else ("BEARISH" if raw_score <= -1.5 else "NEUTRAL")
                tinfo = format_dual_time(pub_dt or now_utc)
                headlines.append({
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "time_kh": tinfo["kh"],
                    "time_utc": tinfo["utc"],
                    "time_ny": tinfo["ny"],
                    "ts": tinfo["ts"],
                    "impact": impact,
                    "score": round(score, 2),
                })

            if not headlines and stale_data is not None:
                with self._lock:
                    self._news_cache["ts"] = time.monotonic() - (ttl * 0.7)
                return stale_data

            headlines.sort(key=lambda x: x["ts"], reverse=True)
            norm_score = max(-1.0, min(1.0, round(total_score / 8.5, 2)))
            if norm_score >= 0.20:
                bias, summary_km = "BULLISH", "ព័ត៌មានសេដ្ឋកិច្ច និងមាសពិភពលោកគាំទ្រឱ្យមាសឡើង"
            elif norm_score <= -0.20:
                bias, summary_km = "BEARISH", "ព័ត៌មានសេដ្ឋកិច្ចពិភពលោកដាក់សម្ពាធឱ្យមាសចុះ"
            else:
                bias, summary_km = "NEUTRAL", "ព័ត៌មានមាសពិភពលោកមានលំនឹងធម្មតា"

            f_time = format_dual_time(now_utc)
            result = {
                "bias": bias,
                "score": norm_score,
                "summary_km": summary_km,
                "count": len(headlines),
                "headlines": headlines[:8],
                "fetched_kh": f_time["kh"],
                "fetched_utc": f_time["utc"],
                "fetched_ny": f_time["ny"],
                "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
            }
            with self._lock:
                self._news_cache = {"ts": time.monotonic(), "data": result}
            return result
        finally:
            self._news_fetch_lock.release()

    # -------------------- WAR & GEOPOLITICAL RADAR --------------------
    def empty_war_news(self) -> dict:
        f_time = format_dual_time(datetime.now(timezone.utc))
        return {
            "level": "LOW",
            "score": 0.0,
            "gold_impact": "NEUTRAL",
            "summary_km": "កំពុងតាមដានស្ថានភាពភូមិសាស្ត្រនយោបាយ...",
            "count": 0,
            "headlines": [],
            "fetched_kh": f_time["kh"],
            "fetched_utc": f_time["utc"],
            "fetched_ny": f_time["ny"],
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
            return stale_data or self.empty_war_news()

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
                key = normalize_title_key(clean_t)
                if not key or key in seen_keys:
                    continue
                t_low = clean_t.lower()
                sc_base = 0.0
                for pat, w in WAR_HIGH_PATTERNS:
                    if pat.search(t_low):
                        sc_base += w
                for pat, w in WAR_MED_PATTERNS:
                    if pat.search(t_low):
                        sc_base += w
                for pat, w in WAR_COOL_PATTERNS:
                    if pat.search(t_low):
                        sc_base += w
                if abs(sc_base) < 1.5:
                    continue

                seen_keys.add(key)
                pub_dt = parse_rss_date(pub_raw)
                sc = sc_base * recency_weight(pub_dt, now_utc)
                raw_tension += sc
                severity = "ESCALATION" if sc_base >= 2.5 else ("PEACE/COOL" if sc_base <= -1.5 else "TENSION")
                tinfo = format_dual_time(pub_dt or now_utc)
                items.append({
                    "source": source_name,
                    "title": clean_t,
                    "link": link or "",
                    "time_kh": tinfo["kh"],
                    "time_utc": tinfo["utc"],
                    "time_ny": tinfo["ny"],
                    "ts": tinfo["ts"],
                    "severity": severity,
                    "score": round(sc, 2),
                })

            if not raw_items and stale_data is not None:
                with self._lock:
                    self._war_cache["ts"] = time.monotonic() - (ttl * 0.7)
                return stale_data

            items.sort(key=lambda x: (abs(x["score"]), x["ts"]), reverse=True)
            norm = max(-1.0, min(1.0, round(raw_tension / 11.0, 2)))
            if norm >= 0.45:
                level, gold_impact, summary_km = "HIGH", "BULLISH", "សង្គ្រាមតានតឹងខ្លាំង — កម្លាំងទិញមាស Safe-Haven ខ្ពស់"
            elif norm >= 0.18:
                level, gold_impact, summary_km = "MEDIUM", "BULLISH", "មានជម្លោះភូមិសាស្ត្រនយោបាយ — ជួយទ្រតម្លៃមាស"
            elif norm <= -0.25:
                level, gold_impact, summary_km = "COOLING", "BEARISH", "សង្គ្រាមធូរស្រាល/ឈប់បាញ់ — អាចឱ្យមាសថយចុះ"
            else:
                level, gold_impact, summary_km = "LOW", "NEUTRAL", "ភូមិសាស្ត្រនយោបាយស្ងប់ស្ងាត់ធម្មតា"

            f_time = format_dual_time(now_utc)
            result = {
                "level": level,
                "score": norm,
                "gold_impact": gold_impact,
                "summary_km": summary_km,
                "count": len(items),
                "headlines": items[:6],
                "fetched_kh": f_time["kh"],
                "fetched_utc": f_time["utc"],
                "fetched_ny": f_time["ny"],
                "updated_at": f"កម្ពុជា {f_time['kh']} • {f_time['ny']} ({f_time['utc']})",
            }
            with self._lock:
                self._war_cache = {"ts": time.monotonic(), "data": result}
            return result
        finally:
            self._war_fetch_lock.release()

    # -------------------- CONCURRENT CONTEXT FETCH --------------------
    def get_external_context_fast(self) -> tuple[dict, dict, pd.DataFrame | None, pd.DataFrame | None]:
        now = time.monotonic()
        with self._lock:
            news_ok = self._news_cache["data"] is not None and (now - self._news_cache["ts"]) < 300.0
            war_ok = self._war_cache["data"] is not None and (now - self._war_cache["ts"]) < 300.0
            macro_ok = self._macro_cache["feat"] is not None and (now - self._macro_cache["ts"]) < 1800.0
            if news_ok and war_ok and macro_ok:
                return (
                    self._news_cache["data"],
                    self._war_cache["data"],
                    self._macro_cache["raw"],
                    self._macro_cache["feat"],
                )

        with ThreadPoolExecutor(max_workers=3) as ex:
            f_news = ex.submit(self.fetch_global_news)
            f_war = ex.submit(self.fetch_war_news)
            f_macro = ex.submit(self.get_macro)
            try:
                gn = f_news.result(timeout=6.0)
            except Exception:
                gn = self.empty_global_news()
            try:
                wn = f_war.result(timeout=6.0)
            except Exception:
                wn = self.empty_war_news()
            try:
                rm, fm = f_macro.result(timeout=8.0)
            except Exception:
                rm, fm = None, None
            return gn, wn, rm, fm