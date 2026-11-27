import os
import time
import threading

import joblib
import numpy as np
import pandas as pd

from .config import MODEL_PATH
from .utils import safe_float, format_dual_time

try:
    import brain_core as core
except Exception as _core_err:
    core = None
    print(f"[BRAIN:ML] brain_core unavailable (fallback to Quant Structure): {_core_err}")


class V8ModelEngine:
    """គ្រប់គ្រងការផ្ទុកម៉ូដែល V8 (.pkl) និងការទស្សន៍ទាយ Ensemble ដោយមាន Bar-Level Cache"""

    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.bundle: dict | None = None
        self.load_error: str | None = None

        self._lock = threading.Lock()
        self._bar_cache = {"key": None, "res": None}
        self._last_reload_try = 0.0

        self.load_model()

    def load_model(self):
        if not os.path.exists(self.model_path):
            self.load_error = f"រកមិនឃើញ File ម៉ូដែល: {self.model_path}"
            print(f"[BRAIN:ML] {self.load_error}")
            return
        try:
            loaded = joblib.load(self.model_path)
            if not isinstance(loaded, dict) or "models" not in loaded or "features" not in loaded:
                raise ValueError("ទម្រង់ File ម៉ូដែលមិនត្រឹមត្រូវ")
            self.bundle = loaded
            self.load_error = None
            print(
                f"[BRAIN:ML] Loaded {self.bundle.get('version', 'V8')} (V10.6 Modular) | "
                f"features={len(self.bundle.get('features', []))} | "
                f"cutoff={self.bundle.get('min_percentile', 0.50)}"
            )
        except Exception as e:
            self.bundle = None
            self.load_error = f"បើកម៉ូដែលមិនបាន: {e}"
            print(f"[BRAIN:ML] {self.load_error}")

    def ensure_loaded(self):
        if self.bundle is not None:
            return
        now = time.monotonic()
        if (now - self._last_reload_try) > 60.0:
            self._last_reload_try = now
            self.load_model()

    @property
    def is_ready(self) -> bool:
        return self.bundle is not None and core is not None

    @property
    def min_percentile(self) -> float:
        return safe_float(self.bundle.get("min_percentile", 0.50), 0.50) if self.bundle else 0.50

    @property
    def grade_a_plus_pct(self) -> float:
        return safe_float(self.bundle.get("grade_a_plus_pct", 0.65), 0.65) if self.bundle else 0.65

    @property
    def version_label(self) -> str:
        return f"{self.bundle.get('version', 'V8')}-MODULAR-V10.6" if self.bundle else "V10.6-MODULAR"

    @property
    def deployable(self) -> bool:
        return bool(self.bundle.get("deployable", False)) if self.bundle else False

    def _score_row_detailed(self, row_df: pd.DataFrame) -> dict:
        models = self.bundle["models"]
        wts = self.bundle.get("weights", (0.30, 0.35, 0.35))
        feats = self.bundle["features"]

        X = (
            row_df.reindex(columns=feats, fill_value=0.0)
            .astype(float)
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        lr, lg, cb = models
        p_lr = float(lr.predict_proba(X)[0, 1])
        p_lg = float(lg.predict_proba(X)[0, 1])
        p_cb = float(cb.predict_proba(X)[0, 1])

        prob = wts[0] * p_lr + wts[1] * p_lg + wts[2] * p_cb
        agreement = max(0.0, min(1.0, 1.0 - (float(np.std([p_lr, p_lg, p_cb])) * 3.0)))

        ref = self.bundle.get("ref_oof_scores")
        pct = float(np.searchsorted(ref, prob) / len(ref)) if (ref is not None and len(ref) > 0) else prob
        return {"prob": prob, "pct": max(0.0, min(1.0, pct)), "agreement": round(agreement, 2)}

    def infer_cached(
        self,
        df_1h: pd.DataFrame | None,
        feat_m: pd.DataFrame | None,
        ev_times: list,
        atr_chart: float,
    ) -> dict | None:
        if not self.is_ready or df_1h is None or len(df_1h) < 60:
            return None

        try:
            cache_key = (
                str(df_1h.iloc[-2].get("datetime", df_1h.index[-2])),
                round(safe_float(df_1h.iloc[-2].get("close"), 0.0), 2),
                self.bundle.get("version", "V8"),
            )
        except Exception:
            cache_key = None

        with self._lock:
            if cache_key is not None and self._bar_cache["key"] == cache_key and self._bar_cache["res"] is not None:
                return self._bar_cache["res"]

        feat_df = core.build_features(df_1h, feat_m, ev_times) if feat_m is not None else pd.DataFrame()
        if feat_df is None or len(feat_df) < 2:
            return None

        closed_row = feat_df.iloc[-2:-1]
        r0 = closed_row.iloc[0]
        cand_side = int(safe_float(r0.get("cand_side"), 0))
        atr_1h = max(safe_float(r0.get("atr_raw"), atr_chart), 0.1)
        bar_dt = r0["datetime"].to_pydatetime() if hasattr(r0.get("datetime"), "to_pydatetime") else None
        bt = format_dual_time(bar_dt)

        buy_row = closed_row.copy()
        buy_row["cand_side"] = 1
        d_buy = self._score_row_detailed(buy_row)

        sell_row = closed_row.copy()
        sell_row["cand_side"] = -1
        d_sell = self._score_row_detailed(sell_row)

        if cand_side == 1:
            ai_prob, ai_pct, agreement = d_buy["prob"], d_buy["pct"], d_buy["agreement"]
        elif cand_side == -1:
            ai_prob, ai_pct, agreement = d_sell["prob"], d_sell["pct"], d_sell["agreement"]
        elif d_buy["pct"] >= d_sell["pct"]:
            ai_prob, ai_pct, agreement = d_buy["prob"], d_buy["pct"], d_buy["agreement"]
        else:
            ai_prob, ai_pct, agreement = d_sell["prob"], d_sell["pct"], d_sell["agreement"]

        res = {
            "r0": r0,
            "cand_side": cand_side,
            "atr_1h": atr_1h,
            "bar_time": f"{bt['kh']} KH ({bt['utc']})",
            "d_buy": d_buy,
            "d_sell": d_sell,
            "ai_prob": ai_prob,
            "ai_pct": ai_pct,
            "agreement": agreement,
        }
        if cache_key is not None:
            with self._lock:
                self._bar_cache = {"key": cache_key, "res": res}
        return res