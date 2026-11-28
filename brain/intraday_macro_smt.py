import time
import threading
from datetime import datetime, timezone

import numpy as np
import yfinance as yf

from .utils import safe_float, is_forex_weekend_closed as is_forex_weekend_utc


class IntradayMacroSmtEngine:
    """
    ម៉ាស៊ីនវាស់សន្ទុះ 5m ផ្ទាល់របស់ទីផ្សារពិភពលោក (DXY, US10Y, Silver SMT)
    - ដោះស្រាយ Error '$SI=F possibly delisted' ដោយប្រើ Multi-Fallback (XAGUSD=X -> SLV -> SI=F)
    - រត់ជាមួយ Cache 60 វិនាទី (មិនឱ្យយឺត Server) និងបង្កក 100% ថ្ងៃបិទផ្សារចុងសប្តាហ៍
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache_ts = 0.0
        self._cache_data = self._empty_state()

    def _empty_state(self, note: str = "កំពុងភ្ជាប់ទិន្នន័យម៉ាក្រូ 5m...") -> dict:
        return {
            "dxy_px": None,
            "dxy_chg_5m": 0.0,
            "dxy_dir": "FLAT",
            "us10y_px": None,
            "us10y_chg_5m": 0.0,
            "us10y_dir": "FLAT",
            "silver_px": None,
            "silver_chg_5m": 0.0,
            "silver_dir": "FLAT",
            "silver_source": "--",
            "macro_gold_bias": "NEUTRAL",
            "smt_signal": "NONE",
            "smt_km": "ប្រាក់ (XAG) ដើរស្របនឹងមាស",
            "summary_km": note,
            "updated_ts": 0,
        }

    def _fetch_5m_series(self, symbols: tuple[str, ...]) -> tuple[np.ndarray | None, str]:
        for sym in symbols:
            try:
                tkr = yf.Ticker(sym)
                df = tkr.history(period="2d", interval="5m")
                if df is not None and not df.empty and len(df) >= 8:
                    closes = df["Close"].dropna().to_numpy(dtype=float)
                    if len(closes) >= 8 and closes[-1] > 0:
                        return closes[-24:], sym
            except Exception:
                continue
        return None, "--"

    def _calc_momentum(self, arr: np.ndarray | None, flat_thresh_pct: float = 0.03) -> tuple[float | None, float, str]:
        if arr is None or len(arr) < 4:
            return None, 0.0, "FLAT"
        last_v = float(arr[-1])
        ref_v = float(arr[-4])  # 15 នាទីចុងក្រោយ (3 ទៀន 5m)
        if ref_v <= 0:
            return round(last_v, 3), 0.0, "FLAT"
        chg_pct = ((last_v - ref_v) / ref_v) * 100.0
        direction = "UP" if chg_pct >= flat_thresh_pct else ("DOWN" if chg_pct <= -flat_thresh_pct else "FLAT")
        return round(last_v, 3), round(chg_pct, 3), direction

    def _compute_smt(self, gold_candles: list[dict], silver_arr: np.ndarray | None) -> tuple[str, str]:
        if not gold_candles or len(gold_candles) < 10 or silver_arr is None or len(silver_arr) < 10:
            return "NONE", "ប្រាក់ (XAG) ដើរស្របនឹងមាស"

        g_closes = np.array([safe_float(c.get("close"), 0.0) for c in gold_candles[-12:]], dtype=float)
        if np.any(g_closes <= 0):
            return "NONE", "ប្រាក់ (XAG) ដើរស្របនឹងមាស"

        s_win = silver_arr[-12:]
        g_prev_lo, g_rec_lo = float(np.min(g_closes[:-3])), float(np.min(g_closes[-3:]))
        g_prev_hi, g_rec_hi = float(np.max(g_closes[:-3])), float(np.max(g_closes[-3:]))

        s_prev_lo, s_rec_lo = float(np.min(s_win[:-3])), float(np.min(s_win[-3:]))
        s_prev_hi, s_rec_hi = float(np.max(s_win[:-3])), float(np.max(s_win[-3:]))

        # Bullish SMT: មាសចុះបំបែកបាតថ្មី តែប្រាក់រឹងមិនព្រមចុះតាម
        if g_rec_lo < g_prev_lo and s_rec_lo > s_prev_lo:
            return "BULL_SMT", "⚡ SMT ▲ ៖ ប្រាក់ (XAG) រឹងមិនចុះតាមមាស — ស្ថាប័នលួចប្រមូលទិញ!"
        # Bearish SMT: មាសឡើងបំបែកកំពូលថ្មី តែប្រាក់ខ្សោយមិនព្រមឡើងតាម
        if g_rec_hi > g_prev_hi and s_rec_hi < s_prev_hi:
            return "BEAR_SMT", "⚡ SMT ▼ ៖ ប្រាក់ (XAG) ខ្សោយមិនឡើងតាមមាស — អន្ទាក់លក់ចុះ!"

        return "NONE", "ប្រាក់ (XAG) ដើរស្របនឹងមាស"

    def get_snapshot(self, gold_candles: list[dict] | None = None, force: bool = False) -> dict:
        now_utc = datetime.now(timezone.utc)
        if is_forex_weekend_utc(now_utc):
            return self._empty_state("ផ្សារបិទចុងសប្តាហ៍ — បង្កកទិន្នន័យ DXY / US10Y / Silver")

        now_mono = time.monotonic()
        with self._lock:
            if not force and (now_mono - self._cache_ts) < 60.0 and self._cache_data.get("updated_ts", 0) > 0:
                return dict(self._cache_data)

        dxy_arr, _ = self._fetch_5m_series(("DX-Y.NYB", "UUP"))
        y10_arr, _ = self._fetch_5m_series(("^TNX",))
        ag_arr, ag_src = self._fetch_5m_series(("XAGUSD=X", "SLV", "SI=F"))

        dxy_px, dxy_chg, dxy_dir = self._calc_momentum(dxy_arr, flat_thresh_pct=0.025)
        y10_px, y10_chg, y10_dir = self._calc_momentum(y10_arr, flat_thresh_pct=0.08)
        ag_px, ag_chg, ag_dir = self._calc_momentum(ag_arr, flat_thresh_pct=0.05)

        # មាសដើរបញ្ច្រាសជាមួយដុល្លារ (DXY) និងការប្រាក់អាមេរិក (US10Y)
        gold_macro_pts = 0
        if dxy_dir == "DOWN":
            gold_macro_pts += 2
        elif dxy_dir == "UP":
            gold_macro_pts -= 2

        if y10_dir == "DOWN":
            gold_macro_pts += 1
        elif y10_dir == "UP":
            gold_macro_pts -= 1

        if ag_dir == "UP":
            gold_macro_pts += 1
        elif ag_dir == "DOWN":
            gold_macro_pts -= 1

        macro_bias = "BULLISH_GOLD" if gold_macro_pts >= 2 else ("BEARISH_GOLD" if gold_macro_pts <= -2 else "NEUTRAL")
        smt_sig, smt_km = self._compute_smt(gold_candles or [], ag_arr)

        dxy_arrow = "▲" if dxy_dir == "UP" else ("▼" if dxy_dir == "DOWN" else "→")
        y10_arrow = "▲" if y10_dir == "UP" else ("▼" if y10_dir == "DOWN" else "→")
        ag_arrow = "▲" if ag_dir == "UP" else ("▼" if ag_dir == "DOWN" else "→")

        dxy_str = f"DXY {dxy_px:.2f}{dxy_arrow}" if dxy_px else "DXY --"
        y10_str = f"US10Y {y10_px:.2f}%{y10_arrow}" if y10_px else "US10Y --"
        ag_str = f"Silver {ag_px:.2f}{ag_arrow}" if ag_px else "Silver --"

        bias_km = (
            "គាំទ្រមាសឡើង ▲" if macro_bias == "BULLISH_GOLD"
            else ("ដាក់សម្ពាធមាសចុះ ▼" if macro_bias == "BEARISH_GOLD" else "ម៉ាក្រូស្មើ")
        )

        snap = {
            "dxy_px": dxy_px,
            "dxy_chg_5m": dxy_chg,
            "dxy_dir": dxy_dir,
            "us10y_px": y10_px,
            "us10y_chg_5m": y10_chg,
            "us10y_dir": y10_dir,
            "silver_px": ag_px,
            "silver_chg_5m": ag_chg,
            "silver_dir": ag_dir,
            "silver_source": ag_src,
            "macro_gold_bias": macro_bias,
            "smt_signal": smt_sig,
            "smt_km": smt_km,
            "summary_km": f"{dxy_str} • {y10_str} • {ag_str} ({bias_km})",
            "updated_ts": int(now_utc.timestamp()),
        }

        with self._lock:
            self._cache_ts = now_mono
            self._cache_data = snap
        return dict(snap)


intraday_macro_smt = IntradayMacroSmtEngine()