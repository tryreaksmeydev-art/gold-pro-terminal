# ==========================================================
# brain_service.py - ម៉ាស៊ីនខួរក្បាល V8 (10-Year Model) + SMC Fusion
# + Free Global Gold/Macro News APIs + Free War/Geopolitical Radar APIs
# ==========================================================
import os
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np
import pandas as pd
import requests

import brain_core as core

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "gold_brain_v8.pkl")
EVENTS_CSV = os.path.join(os.path.dirname(__file__), "models", "events.csv")

# ពាក្យគន្លឹះសេដ្ឋកិច្ច និងមាសពិភពលោក
BULLISH_GOLD_WORDS = {
    "rate cut": 2.5, "dovish": 2.2, "central bank buys": 2.6, "central bank gold": 2.2,
    "safe-haven": 2.3, "safe haven": 2.3, "gold surges": 2.0, "gold jumps": 1.8,
    "gold rallies": 1.8, "gold climbs": 1.6, "weak dollar": 2.0, "dollar falls": 1.8,
    "dollar drops": 1.8, "recession": 2.2, "inflation fears": 1.6, "stagflation": 2.2,
    "yields fall": 2.0, "yields drop": 1.8, "record high": 1.6, "crisis": 2.0,
    "tariff": 1.6, "trade war": 2.2, "uncertainty": 1.5, "debt ceiling": 1.8,
    "bank crisis": 2.4, "fed pause": 1.8, "unemployment rises": 1.8
}

BEARISH_GOLD_WORDS = {
    "rate hike": -2.6, "hawkish": -2.3, "ceasefire": -2.5, "peace deal": -2.4,
    "truce": -2.2, "strong dollar": -2.2, "dollar surges": -2.0, "dollar rallies": -1.8,
    "yields surge": -2.2, "yields rise": -1.8, "yields jump": -2.0,
    "gold drops": -1.8, "gold falls": -1.8, "gold slides": -1.8, "gold slips": -1.5,
    "profit taking": -1.5, "strong jobs": -2.0, "nfp beats": -2.2,
    "higher for longer": -2.3, "risk-on": -1.5, "inflation cools": -1.2
}

GOLD_RELEVANT_FILTER = (
    "gold", "xau", "bullion", "fed", "powell", "fomc", "inflation", "cpi", "pce",
    "dollar", "dxy", "treasury", "yield", "tariff", "central bank", "recession", "jobs", "nfp"
)

# ពាក្យគន្លឹះសង្គ្រាម និងភូមិសាស្ត្រនយោបាយ (រុញមាសឡើងជា Safe-Haven)
WAR_HIGH_WORDS = {
    "missile": 3.0, "airstrike": 2.8, "air strike": 2.8, "nuclear": 3.5,
    "invasion": 3.0, "bombing": 2.6, "retaliation": 2.5, "escalation": 2.5,
    "drone attack": 2.5, "war escalates": 3.0, "middle east war": 3.0,
    "strait of hormuz": 3.0, "red sea attack": 2.5, "martial law": 2.5
}

WAR_MED_WORDS = {
    "war": 1.8, "conflict": 1.6, "military": 1.4, "troops": 1.5, "sanctions": 1.8,
    "iran": 1.5, "israel": 1.5, "gaza": 1.5, "ukraine": 1.5, "russia": 1.4,
    "taiwan": 1.8, "north korea": 1.8, "nato": 1.5, "attack": 1.6, "strike": 1.4
}

WAR_DEESCALATE_WORDS = {
    "ceasefire": -3.0, "peace talks": -2.2, "peace deal": -2.8, "truce": -2.5,
    "de-escalation": -2.5, "withdraws troops": -2.2, "hostage deal": -2.0
}

# Free RSS Feeds សម្រាប់មាស និងសេដ្ឋកិច្ចពិភពលោក
GLOBAL_RSS_FEEDS = [
    ("Yahoo Gold/DXY", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F,DX-Y.NYB&region=US&lang=en-US"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("CNBC Macro", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("Investing Gold", "https://www.investing.com/rss/news_11.rss"),
    ("Kitco Gold", "https://www.kitco.com/rss/gold.xml"),
]

# Free RSS & API សម្រាប់ព័ត៌មានសង្គ្រាម និងភូមិសាស្ត្រនយោបាយ
WAR_RSS_FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]
GDELT_WAR_API = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=(missile%20OR%20airstrike%20OR%20%22middle%20east%22%20OR%20ukraine%20OR%20sanctions%20OR%20ceasefire)%20sourcelang:eng"
    "&mode=artlist&maxrecords=8&timespan=12h&format=json"
)


class GoldBrainService:
    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.bundle = None
        self.load_error = None
        self._lock = threading.Lock()
        self._macro_cache = {"ts": 0.0, "raw": None, "feat": None}
        self._news_cache = {"ts": 0.0, "data": None}
        self._war_cache = {"ts": 0.0, "data": None}
        self._ev_cache = {"year": None, "times": None}
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "TriPisalGoldTerminal/8.4"})
        self._load_model()

    def _load_model(self):
        if not os.path.exists(self.model_path):
            self.load_error = f"រកមិនឃើញ File ម៉ូដែល: {self.model_path}"
            print(f"[BRAIN] {self.load_error}")
            return
        try:
            self.bundle = joblib.load(self.model_path)
            self.load_error = None
            print(
                f"[BRAIN] Loaded {self.bundle.get('version', 'V8')} | "
                f"features={len(self.bundle.get('features', []))} | "
                f"cutoff={self.bundle.get('min_percentile', 0.50)}"
            )
        except Exception as e:
            self.bundle = None
            self.load_error = f"បើកម៉ូដែលមិនបាន: {e}"
            print(f"[BRAIN] {self.load_error}")

    def _get_macro(self, ttl: float = 1800.0):
        now = time.monotonic()
        with self._lock:
            if self._macro_cache["feat"] is not None and (now - self._macro_cache["ts"]) < ttl:
                return self._macro_cache["raw"], self._macro_cache["feat"]

        start_str = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d")
        raw_m = core.download_macro(start=start_str)
        feat_m = core.macro_features(raw_m)
        with self._lock:
            self._macro_cache = {"ts": time.monotonic(), "raw": raw_m, "feat": feat_m}
        return raw_m, feat_m

    def _get_events(self):
        yr = datetime.now(timezone.utc).year
        with self._lock:
            if self._ev_cache["year"] == yr and self._ev_cache["times"] is not None:
                return self._ev_cache["times"]
        times = core.get_event_times(yr - 1, yr + 1, EVENTS_CSV)
        with self._lock:
            self._ev_cache = {"year": yr, "times": times}
        return times

    def fetch_global_news(self, session: requests.Session | None = None, ttl: float = 300.0) -> dict:
        """ទាញព័ត៌មានមាស និងសេដ្ឋកិច្ចពិភពលោកឥតគិតថ្លៃ + គណនា Sentiment"""
        now = time.monotonic()
        with self._lock:
            if self._news_cache["data"] is not None and (now - self._news_cache["ts"]) < ttl:
                return self._news_cache["data"]

        sess = session or self._session
        headlines, seen_titles, total_score = [], set(), 0.0

        for source_name, url in GLOBAL_RSS_FEEDS:
            try:
                r = sess.get(url, timeout=(2.5, 4.0))
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:14]:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    pub_el = item.find("pubDate")
                    if title_el is None or not title_el.text:
                        continue
                    title = title_el.text.strip()
                    t_low = title.lower()
                    if title in seen_titles or not any(k in t_low for k in GOLD_RELEVANT_FILTER):
                        continue
                    seen_titles.add(title)

                    score = 0.0
                    for kw, w in BULLISH_GOLD_WORDS.items():
                        if kw in t_low:
                            score += w
                    for kw, w in BEARISH_GOLD_WORDS.items():
                        if kw in t_low:
                            score += w

                    total_score += score
                    impact = "BULLISH" if score >= 1.5 else ("BEARISH" if score <= -1.5 else "NEUTRAL")
                    headlines.append({
                        "source": source_name,
                        "title": title,
                        "link": link_el.text.strip() if (link_el is not None and link_el.text) else "",
                        "pubDate": pub_el.text.strip() if (pub_el is not None and pub_el.text) else "",
                        "impact": impact,
                        "score": round(score, 2)
                    })
            except Exception:
                continue

        norm_score = max(-1.0, min(1.0, round(total_score / 9.0, 2)))
        if norm_score >= 0.22:
            bias = "BULLISH"
            summary_km = "ព័ត៌មានសេដ្ឋកិច្ច និងមាសពិភពលោកកំពុងគាំទ្រឲ្យមាសឡើងថ្លៃ"
        elif norm_score <= -0.22:
            bias = "BEARISH"
            summary_km = "ព័ត៌មានសេដ្ឋកិច្ចពិភពលោកកំពុងដាក់សម្ពាធឲ្យមាសចុះថ្លៃ"
        else:
            bias = "NEUTRAL"
            summary_km = "ព័ត៌មានមាសពិភពលោកមានលំនឹងធម្មតា"

        result = {
            "bias": bias,
            "score": norm_score,
            "summary_km": summary_km,
            "count": len(headlines),
            "headlines": headlines[:8],
            "updated_at": datetime.now(timezone.utc).strftime("%H:%M UTC")
        }
        with self._lock:
            self._news_cache = {"ts": time.monotonic(), "data": result}
        return result

    def fetch_war_news(self, session: requests.Session | None = None, ttl: float = 300.0) -> dict:
        """ទាញព័ត៌មានសង្គ្រាម និងភូមិសាស្ត្រនយោបាយ (GDELT Free API + BBC + Al Jazeera)"""
        now = time.monotonic()
        with self._lock:
            if self._war_cache["data"] is not None and (now - self._war_cache["ts"]) < ttl:
                return self._war_cache["data"]

        sess = session or self._session
        items, seen, raw_tension = [], set(), 0.0

        def _eval_war_headline(source_name: str, title: str, link: str):
            nonlocal raw_tension
            clean_t = (title or "").strip()
            if not clean_t or clean_t in seen:
                return
            t_low = clean_t.lower()
            sc = 0.0
            for kw, w in WAR_HIGH_WORDS.items():
                if kw in t_low:
                    sc += w
            for kw, w in WAR_MED_WORDS.items():
                if kw in t_low:
                    sc += w
            for kw, w in WAR_DEESCALATE_WORDS.items():
                if kw in t_low:
                    sc += w
            if abs(sc) < 1.4:
                return
            seen.add(clean_t)
            raw_tension += sc
            severity = "ESCALATION" if sc >= 2.5 else ("PEACE/COOL" if sc <= -1.5 else "TENSION")
            items.append({
                "source": source_name,
                "title": clean_t,
                "link": link or "",
                "severity": severity,
                "score": round(sc, 2)
            })

        # 1) BBC World & Al Jazeera RSS (លឿន និងស្ថិរភាពខ្ពស់)
        for src, url in WAR_RSS_FEEDS:
            try:
                r = sess.get(url, timeout=(2.5, 4.0))
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.content)
                for it in root.findall(".//item")[:20]:
                    t_el = it.find("title")
                    l_el = it.find("link")
                    if t_el is not None and t_el.text:
                        _eval_war_headline(src, t_el.text, l_el.text if l_el is not None else "")
            except Exception:
                continue

        # 2) GDELT Free Conflict API (ឥតគិតថ្លៃ មិនបាច់ API Key)
        try:
            r = sess.get(GDELT_WAR_API, timeout=(2.5, 4.0))
            if r.status_code == 200:
                arts = r.json().get("articles") or []
                for a in arts[:8]:
                    _eval_war_headline(
                        a.get("domain") or "GDELT",
                        a.get("title") or "",
                        a.get("url") or ""
                    )
        except Exception:
            pass

        items.sort(key=lambda x: abs(x["score"]), reverse=True)
        norm = max(-1.0, min(1.0, round(raw_tension / 12.0, 2)))

        if norm >= 0.45:
            level = "HIGH"
            gold_impact = "BULLISH"
            summary_km = "ភាពតានតឹងសង្គ្រាមកើនឡើងខ្លាំង — កម្លាំងទិញមាសការពារហានិភ័យ (Safe-Haven) ខ្ពស់"
        elif norm >= 0.18:
            level = "MEDIUM"
            gold_impact = "BULLISH"
            summary_km = "មានព័ត៌មានជម្លោះភូមិសាស្ត្រនយោបាយ — ជួយទ្រតម្លៃមាសមិនឲ្យចុះខ្លាំង"
        elif norm <= -0.25:
            level = "COOLING"
            gold_impact = "BEARISH"
            summary_km = "មានសញ្ញាបន្ធូរបន្ថយសង្គ្រាម/ឈប់បាញ់ — អាចធ្វើឲ្យមាសថយចុះបន្តិច"
        else:
            level = "LOW"
            gold_impact = "NEUTRAL"
            summary_km = "ស្ថានភាពភូមិសាស្ត្រនយោបាយស្ងប់ស្ងាត់ធម្មតា"

        result = {
            "level": level,
            "score": norm,
            "gold_impact": gold_impact,
            "summary_km": summary_km,
            "count": len(items),
            "headlines": items[:6],
            "updated_at": datetime.now(timezone.utc).strftime("%H:%M UTC")
        }
        with self._lock:
            self._war_cache = {"ts": time.monotonic(), "data": result}
        return result

    def _score_row(self, row_df: pd.DataFrame) -> tuple[float, float]:
        models = self.bundle["models"]
        wts = self.bundle.get("weights", (0.30, 0.35, 0.35))
        feats = self.bundle["features"]
        X = row_df[feats].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        lr, lg, cb = models
        prob = float(
            wts[0] * lr.predict_proba(X)[0, 1]
            + wts[1] * lg.predict_proba(X)[0, 1]
            + wts[2] * cb.predict_proba(X)[0, 1]
        )
        ref = self.bundle["ref_oof_scores"]
        pct = float(np.searchsorted(ref, prob) / len(ref))
        return prob, pct

    def _describe_market(self, r0, payload: dict, global_news: dict, war_news: dict) -> dict:
        ema20 = float(payload.get("ema20") or 0.0)
        ema50 = float(payload.get("ema50") or 0.0)
        rsi = float(payload.get("rsi") or 50.0)
        atr = float(payload.get("atr") or 2.5)
        sess = (payload.get("session") or {}).get("name", "Market")
        smc_bias = (payload.get("smc") or {}).get("bias", "NEUTRAL")

        adx = float(r0["adx_14"]) if (r0 is not None and pd.notna(r0.get("adx_14"))) else 20.0
        h4_tr = float(r0["h4_trend"]) if (r0 is not None and pd.notna(r0.get("h4_trend"))) else (1.0 if ema20 >= ema50 else -1.0)

        if adx < 18:
            trend_code = "SIDEWAY"
            trend_km = "ទីផ្សាររត់ទទឹង (Sideway)"
        elif h4_tr > 0 and ema20 >= ema50:
            trend_code = "UPTREND"
            trend_km = "ទីផ្សារឡើងខ្លាំង (Strong Uptrend)" if adx >= 28 else "ទីផ្សារកំពុងឡើង (Uptrend)"
        elif h4_tr < 0 and ema20 <= ema50:
            trend_code = "DOWNTREND"
            trend_km = "ទីផ្សារចុះខ្លាំង (Strong Downtrend)" if adx >= 28 else "ទីផ្សារកំពុងចុះ (Downtrend)"
        else:
            trend_code = "PULLBACK"
            trend_km = "ទីផ្សារកំពុងកែតម្រូវតម្លៃ (Pullback)"

        vol_km = "រត់លឿនខ្លាំង" if atr >= 5.5 else ("ល្បឿនមធ្យមល្អ" if atr >= 2.2 else "ស្ងប់ស្ងាត់")
        war_tag = f" • សង្គ្រាម: {war_news.get('level', 'LOW')}" if war_news.get("level") in ("HIGH", "MEDIUM") else ""
        summary_km = f"{trend_km} • SMC {smc_bias} • {vol_km} ({sess}){war_tag}"

        return {
            "trend_code": trend_code,
            "trend_km": trend_km,
            "volatility_km": vol_km,
            "summary_km": summary_km,
            "adx": round(adx, 1),
            "rsi": round(rsi, 1),
            "smc_bias": smc_bias,
            "news_km": global_news.get("summary_km", "")
        }

    def _build_precision_plan(
        self, side: str, price: float, payload: dict, atr: float, inst: dict, grade: str
    ) -> dict | None:
        """
        គណនា Entry / SL / TP1 / TP2 តាម SMC Order Block + Swing High/Low
        និងប្រាប់ច្បាស់ទាំងចំណុចចូល (entry_km) និងយុទ្ធសាស្ត្រចេញ (exit_km)
        """
        if side not in ("BUY", "SELL") or price <= 0:
            return None

        candles = payload.get("candles") or []
        ema20 = float(payload.get("ema20") or price)
        smc = payload.get("smc") or {}
        atr_val = max(float(atr or 2.0), 0.8)

        recent = candles[-18:]
        swing_low = min((float(c["low"]) for c in recent), default=price - atr_val * 1.4)
        swing_high = max((float(c["high"]) for c in recent), default=price + atr_val * 1.4)

        min_sl = atr_val * 0.90
        max_sl = atr_val * 2.00
        buf = atr_val * 0.30

        if side == "BUY":
            # ប្រើ SMC Demand OB ដែលនៅក្រោមតម្លៃបច្ចុប្បន្ន បើមាន
            anchor_low = swing_low
            for ob in (smc.get("demand_blocks") or []):
                if ob["bottom"] < price and (price - ob["bottom"]) <= max_sl * 1.1:
                    anchor_low = min(anchor_low, float(ob["bottom"]))
                    break

            raw_dist = price - (anchor_low - buf)
            sl_dist = max(min_sl, min(max_sl, raw_dist))
            sl_p = round(price - sl_dist, 2)
            tp1_p = round(price + sl_dist * 1.6, 2)
            tp2_p = round(price + sl_dist * 2.6, 2)

            # កុំឲ្យ TP1 បុកហួស Supply OB ឬ PDH
            for sob in (smc.get("supply_blocks") or []):
                if price + sl_dist * 1.15 < float(sob["bottom"]) < tp1_p:
                    tp1_p = round(float(sob["bottom"]) - 0.30, 2)
                    break
            pdh = inst.get("pdh")
            if pdh and price + sl_dist * 1.15 < pdh < tp1_p:
                tp1_p = round(pdh - 0.30, 2)
            if tp2_p <= tp1_p + 0.6:
                tp2_p = round(tp1_p + sl_dist, 2)

            stretch = price - ema20
        else:
            anchor_high = swing_high
            for sob in (smc.get("supply_blocks") or []):
                if sob["top"] > price and (sob["top"] - price) <= max_sl * 1.1:
                    anchor_high = max(anchor_high, float(sob["top"]))
                    break

            raw_dist = (anchor_high + buf) - price
            sl_dist = max(min_sl, min(max_sl, raw_dist))
            sl_p = round(price + sl_dist, 2)
            tp1_p = round(price - sl_dist * 1.6, 2)
            tp2_p = round(price - sl_dist * 2.6, 2)

            for ob in (smc.get("demand_blocks") or []):
                if tp1_p < float(ob["top"]) < price - sl_dist * 1.15:
                    tp1_p = round(float(ob["top"]) + 0.30, 2)
                    break
            pdl = inst.get("pdl")
            if pdl and tp1_p < pdl < price - sl_dist * 1.15:
                tp1_p = round(pdl + 0.30, 2)
            if tp2_p >= tp1_p - 0.6:
                tp2_p = round(tp1_p - sl_dist, 2)

            stretch = ema20 - price

        rr1_val = round(abs(tp1_p - price) / max(sl_dist, 0.1), 1)
        rr2_val = round(abs(tp2_p - price) / max(sl_dist, 0.1), 1)

        if stretch <= atr_val * 0.75:
            entry_state, entry_km = "IN_ZONE", "ចូលបានឥឡូវ"
        elif stretch <= atr_val * 1.35:
            entry_state, entry_km = "PULLBACK", "រង់ចាំថយបន្តិច"
        else:
            entry_state, entry_km = "EXTENDED", "ហួសចំណុច (កុំដេញ)"

        exit_km = f"កាត់ 50% នៅ ${tp1_p:.2f} • រំកិល SL ស្មើដើម"

        return {
            "entry": round(price, 2),
            "sl": sl_p,
            "tp": tp1_p,
            "tp2": tp2_p,
            "sl_dist": round(sl_dist, 2),
            "sl_pips": int(round(sl_dist * 10)),
            "tp_pips": int(round(abs(tp1_p - price) * 10)),
            "tp2_pips": int(round(abs(tp2_p - price) * 10)),
            "rr": f"{rr1_val:g} : 1",
            "rr2": f"{rr2_val:g} : 1",
            "entry_state": entry_state,
            "entry_km": entry_km,
            "exit_km": exit_km,
            "suggested_risk_pct": 1.0 if grade == "A+" else (0.75 if grade in ("A", "B+") else 0.5),
        }

    def evaluate(self, df_1h: pd.DataFrame, payload: dict, session: requests.Session | None = None) -> dict:
        global_news = self.fetch_global_news(session=session)
        war_news = self.fetch_war_news(session=session)
        hard_blocks, soft_warnings, pass_reasons = [], [], []

        price = float(payload.get("price") or 0.0)
        spread = float(payload.get("spread") or 0.25)
        cal_news = payload.get("news") or {}
        mtf = payload.get("mtf") or {}
        inst = payload.get("inst_levels") or {}
        smc = payload.get("smc") or {}
        active_sig = payload.get("active_signal") or {}
        chart_signal_txt = str(payload.get("signal") or "NEUTRAL")

        macro_snapshot = {"dxy": None, "us10y": None, "vix": None, "real10": None}
        ai_prob, ai_pct, cand_side, atr_1h, bar_time = None, None, 0, None, None
        r0 = None

        # 1) វិភាគដោយខួរក្បាល 10-Year V8 (1H Closed Bar)
        if self.bundle is not None and df_1h is not None and len(df_1h) >= 60:
            try:
                raw_m, feat_m = self._get_macro()
                if raw_m is not None and not raw_m.empty:
                    last_m = raw_m.iloc[-1]
                    macro_snapshot = {
                        "dxy": round(float(last_m["dxy"]), 2) if pd.notna(last_m.get("dxy")) else None,
                        "us10y": round(float(last_m["us10y"]), 2) if pd.notna(last_m.get("us10y")) else None,
                        "vix": round(float(last_m["vix"]), 2) if pd.notna(last_m.get("vix")) else None,
                        "real10": round(float(last_m["real10"]), 2) if pd.notna(last_m.get("real10")) else None,
                    }

                ev_times = self._get_events()
                feat_df = core.build_features(df_1h, feat_m, ev_times)
                if len(feat_df) >= 2:
                    closed_row = feat_df.iloc[-2:-1]
                    r0 = closed_row.iloc[0]
                    cand_side = int(r0.get("cand_side", 0))
                    atr_1h = float(r0["atr_raw"]) if pd.notna(r0.get("atr_raw")) else float(payload.get("atr") or 4.0)
                    bar_time = (r0["datetime"] + pd.Timedelta(hours=7)).strftime("%Y-%m-%d %H:%M UTC+7")
                    ai_prob, ai_pct = self._score_row(closed_row)
            except Exception as e:
                soft_warnings.append(f"V8 1H Sync: {str(e)[:60]}")

        market_state = self._describe_market(r0, payload, global_news, war_news)

        # 2) របាំងសុវត្ថិភាពពិតប្រាកដ
        now_utc = datetime.now(timezone.utc)
        if now_utc.weekday() >= 5:
            hard_blocks.append("ទីផ្សារ Forex បិទថ្ងៃសៅរ៍-អាទិត្យ — រង់ចាំថ្ងៃច័ន្ទ")
        if cal_news.get("danger"):
            ev = cal_news.get("danger_event") or {}
            hard_blocks.append(f"មានព័ត៌មានក្រហម USD ({ev.get('title', 'Red News')}) ជិតចេញ — គួរផ្អាកសិន")
        if spread > 1.50:
            hard_blocks.append(f"Spread រីកខ្ពស់ខ្លាំង (${spread:.2f}) — ហានិភ័យ Slippage")
        elif spread > 0.80:
            soft_warnings.append(f"Spread រាងធំបន្តិច (${spread:.2f}) — គួរប្រើ Lot តូច")

        # 3) កំណត់ទិសដៅ (V8 10-Year Model -> Live Tactical + SMC + MTF)
        min_pct = float(self.bundle.get("min_percentile", 0.50)) if self.bundle else 0.50
        a_plus_pct = float(self.bundle.get("grade_a_plus_pct", 0.65)) if self.bundle else 0.65

        v8_side = "BUY" if cand_side == 1 else ("SELL" if cand_side == -1 else "NONE")
        mtf_aligned = mtf.get("aligned", "MIXED")
        smc_bias = smc.get("bias", "NEUTRAL")
        live_side = active_sig.get("side") or (
            "BUY" if "BUY" in chart_signal_txt else ("SELL" if "SELL" in chart_signal_txt else "NONE")
        )

        tactical_mode = "WAIT"
        final_side = "NONE"
        grade = "WAIT"
        confidence_score = 40

        if v8_side != "NONE" and (ai_pct is None or ai_pct >= min_pct):
            final_side = v8_side
            tactical_mode = "V8_CONFIRMED"
            is_a_plus = ai_pct is not None and ai_pct >= a_plus_pct
            grade = "A+" if is_a_plus else "A"
            confidence_score = 92 if is_a_plus else 84
            pass_reasons.append(
                f"ខួរក្បាល 10 ឆ្នាំ V8 អនុម័ត {final_side} (Rank {ai_pct*100:.1f}%)"
                if ai_pct is not None else f"ខួរក្បាល V8 អនុម័ត {final_side}"
            )
        elif live_side in ("BUY", "SELL"):
            final_side = live_side
            if mtf_aligned == final_side:
                tactical_mode = "LIVE_TACTICAL"
                grade = "B+"
                confidence_score = 78
                pass_reasons.append(f"ក្រាហ្វិកផ្ទាល់ + MTF 4 Timeframes ស្របទិស {final_side}")
            elif mtf_aligned == "MIXED":
                tactical_mode = "LIVE_TACTICAL"
                grade = "B"
                confidence_score = 66
                pass_reasons.append(f"សញ្ញាក្រាហ្វិកផ្ទាល់ចង្អុល {final_side} (Scalping / ប្រើ Lot សមរម្យ)")
            else:
                tactical_mode = "COUNTER_TREND"
                grade = "WAIT"
                confidence_score = 42
                soft_warnings.append(f"ក្រាហ្វិកតូចចង្អុល {final_side} ប៉ុន្តែ MTF ធំកំពុង {mtf_aligned} — គួររង់ចាំសិន")

        # បូកពិន្ទុ SMC Structure
        if (final_side == "BUY" and smc_bias == "BULLISH") or (final_side == "SELL" and smc_bias == "BEARISH"):
            confidence_score = min(98, confidence_score + 4)
            pass_reasons.append(f"រចនាសម្ព័ន្ធ SMC ({smc_bias}) ស្របទិសជាមួយ {final_side}")

        # បូកពិន្ទុព័ត៌មានមាស/សេដ្ឋកិច្ចពិភពលោក
        g_bias = global_news.get("bias", "NEUTRAL")
        g_score = float(global_news.get("score", 0.0))
        if final_side == "BUY" and g_bias == "BULLISH":
            confidence_score = min(98, confidence_score + 4)
            pass_reasons.append(f"ព័ត៌មានមាសពិភពលោកគាំទ្រ BUY ({g_score:+.2f})")
        elif final_side == "SELL" and g_bias == "BEARISH":
            confidence_score = min(98, confidence_score + 4)
            pass_reasons.append(f"ព័ត៌មានមាសពិភពលោកគាំទ្រ SELL ({g_score:+.2f})")
        elif (final_side == "BUY" and g_bias == "BEARISH" and g_score <= -0.35) or (
            final_side == "SELL" and g_bias == "BULLISH" and g_score >= 0.35
        ):
            confidence_score = max(35, confidence_score - 7)
            soft_warnings.append(f"ប្រយ័ត្ន៖ ព័ត៌មានសេដ្ឋកិច្ចពិភពលោក ({g_bias}) កំពុងផ្ទុយពី {final_side}")

        # បូកពិន្ទុព័ត៌មានសង្គ្រាម / ភូមិសាស្ត្រនយោបាយ
        w_level = war_news.get("level", "LOW")
        w_impact = war_news.get("gold_impact", "NEUTRAL")
        if w_level in ("HIGH", "MEDIUM") and w_impact == "BULLISH":
            if final_side == "BUY":
                confidence_score = min(99, confidence_score + (6 if w_level == "HIGH" else 3))
                pass_reasons.append(f"ព័ត៌មានសង្គ្រាម ({w_level}) រុញកម្លាំងទិញ Safe-Haven ឡើង")
            elif final_side == "SELL":
                confidence_score = max(35, confidence_score - (8 if w_level == "HIGH" else 4))
                soft_warnings.append(f"ប្រយ័ត្ន SELL៖ ភាពតានតឹងសង្គ្រាម ({w_level}) អាចធ្វើឲ្យមាសហក់ឡើងវិញភ្លាមៗ")

        atr_chart = max(float(payload.get("atr") or 2.0), 0.8)
        atr_use = atr_1h if (tactical_mode == "V8_CONFIRMED" and atr_1h) else atr_chart

        # 4) បង្កើតផែនការ Entry / Exit (SMC + Structure)
        plan = self._build_precision_plan(final_side, price, payload, atr_use, inst, grade)
        if plan and plan["entry_state"] == "EXTENDED":
            soft_warnings.append("តម្លៃរត់ឆ្ងាយពី EMA 20 ហើយ — គួររង់ចាំថយចូលតំបន់សិន កុំដេញ")

        # 5) សេចក្តីសម្រេចចុងក្រោយ
        if hard_blocks:
            status = "BLOCKED"
            can_trade = False
            grade = "WAIT"
            confidence_score = 20
            action_km = "ហាមចូល (ផ្អាកសិន — មានហានិភ័យខ្ពស់)"
            reasons = hard_blocks + soft_warnings
        elif final_side in ("BUY", "SELL") and tactical_mode in ("V8_CONFIRMED", "LIVE_TACTICAL"):
            status = f"APPROVED_{final_side}"
            can_trade = True
            side_km = "គួរទិញ (BUY)" if final_side == "BUY" else "គួរលក់ (SELL)"
            action_km = f"{side_km} • កម្រិត {grade}"
            reasons = pass_reasons + soft_warnings
        else:
            status = "WAIT"
            can_trade = False
            action_km = "មិនទាន់គួរចូល (រង់ចាំទិសដៅច្បាស់សិន)"
            reasons = soft_warnings or ["ទីផ្សារកំពុងរត់ទទឹង ឬ Timeframe មិនទាន់ស្របគ្នា"]

        return {
            "version": self.bundle.get("version", "V8") if self.bundle else "TACTICAL",
            "deployable": bool(self.bundle.get("deployable", False)) if self.bundle else False,
            "tactical_mode": tactical_mode,
            "status": status,
            "grade": grade,
            "confidence_score": int(confidence_score),
            "action_km": action_km,
            "can_trade": can_trade,
            "side": final_side,
            "ai_rank_pct": round(ai_pct * 100, 1) if ai_pct is not None else None,
            "ai_prob": round(ai_prob * 100, 1) if ai_prob is not None else None,
            "cutoff_pct": round(min_pct * 100, 0),
            "bar_time": bar_time,
            "market_state": market_state,
            "reasons": reasons,
            "passed": pass_reasons,
            "warnings": soft_warnings,
            "plan": plan,
            "macro": macro_snapshot,
            "global_news": global_news,
            "war_news": war_news,
        }


brain_service = GoldBrainService()