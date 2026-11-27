import time
import threading
from datetime import datetime, timezone

import pandas as pd

from .config import MODEL_PATH
from .utils import safe_float, is_forex_weekend_closed, get_session_killzone
from .audit_tracker import SignalWinRateTracker
from .news_service import ExternalContextService
from .ml_engine import V8ModelEngine
from . import quant_analyzer as qa


class GoldBrainService:
    """Orchestrator នៃខួរក្បាល AI V11.2 (V8 ML + OB/SMC + Quant + SMT + Anti-Trap Hard Gates)"""

    def __init__(self, model_path: str = MODEL_PATH):
        self.ml = V8ModelEngine(model_path=model_path)
        self.context = ExternalContextService()
        self.tracker = SignalWinRateTracker()

        self._lock = threading.Lock()
        self._last_signal_mem: dict[str, dict] = {}

    @property
    def bundle(self):
        return self.ml.bundle

    @property
    def load_error(self):
        return self.ml.load_error

    def _get_external_context_fast(self):
        return self.context.get_external_context_fast()

    def fetch_global_news(self, session=None, ttl: float = 300.0) -> dict:
        return self.context.fetch_global_news(ttl=ttl)

    def fetch_war_news(self, session=None, ttl: float = 300.0) -> dict:
        return self.context.fetch_war_news(ttl=ttl)

    def evaluate(self, df_1h: pd.DataFrame | None, payload: dict, session=None) -> dict:
        self.ml.ensure_loaded()
        global_news, war_news, raw_m, feat_m = self.context.get_external_context_fast()

        hard_blocks, trap_blocks, soft_warnings = [], [], []
        buy_reasons, sell_reasons = [], []

        price = safe_float(payload.get("price"), 0.0)
        spread = safe_float(payload.get("spread"), 0.25)
        rsi = safe_float(payload.get("rsi"), 50.0)
        ema20 = safe_float(payload.get("ema20"), price)
        ema50 = safe_float(payload.get("ema50"), price)
        atr_chart = max(safe_float(payload.get("atr"), 2.0), 0.6)
        interval_key = str(payload.get("interval") or "15m")

        cal_news = payload.get("news") or {}
        mtf = payload.get("mtf") or {}
        inst = payload.get("inst_levels") or {}
        smc = payload.get("smc") or {}
        candles = payload.get("candles") or []
        order_flow = payload.get("order_flow") or None
        sanity = payload.get("sanity") or {}
        quant = payload.get("quant") or {}
        smt = payload.get("smt") or {}

        # Adaptive Circuit Breaker (Anti-Chop)
        circuit_breaker = self.tracker.get_loss_streak(interval_key)
        if circuit_breaker.get("active") and circuit_breaker.get("reason_km"):
            soft_warnings.append(circuit_breaker["reason_km"])

        now_utc = datetime.now(timezone.utc)
        session_kz = get_session_killzone(now_utc)

        macro_snapshot = {"dxy": None, "us10y": None, "vix": None, "real10": None}
        dxy_r1, us10y_d5 = 0.0, 0.0
        if raw_m is not None and not raw_m.empty:
            last_m = raw_m.iloc[-1]
            macro_snapshot = {
                "dxy": round(float(last_m["dxy"]), 2) if pd.notna(last_m.get("dxy")) else None,
                "us10y": round(float(last_m["us10y"]), 2) if pd.notna(last_m.get("us10y")) else None,
                "vix": round(float(last_m["vix"]), 2) if pd.notna(last_m.get("vix")) else None,
                "real10": round(float(last_m["real10"]), 2) if pd.notna(last_m.get("real10")) else None,
            }
        if feat_m is not None and not feat_m.empty:
            lf = feat_m.iloc[-1]
            dxy_r1, us10y_d5 = safe_float(lf.get("dxy_r1"), 0.0), safe_float(lf.get("us10y_d5"), 0.0)

        # ---------- PILLAR 1: 10-YEAR V8 ENSEMBLE (Max 30 pts) ----------
        p1_buy, p1_sell, v8_confirmed_side = 0, 0, "NONE"
        ai_prob, ai_pct, bar_time, v8_agreement = None, None, None, 0.0
        v8_res, r0 = None, None
        min_pct = self.ml.min_percentile
        a_plus_pct = self.ml.grade_a_plus_pct

        if not self.ml.is_ready:
            soft_warnings.append("ម៉ូដែល V8 មិនទាន់ផ្ទុក — កំពុងប្រើ Quant Structure")
        elif df_1h is not None and len(df_1h) >= 60:
            try:
                v8_res = self.ml.infer_cached(df_1h, feat_m, self.context.get_events(), atr_chart)
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
                            p1_buy = int(round(16 * ag_mult))
                            buy_reasons.append(f"V8 ផ្អៀង BUY ({d_buy['pct']*100:.0f}%)")
                        elif d_sell["pct"] >= min_pct and diff_pct <= -0.04:
                            p1_sell = int(round(16 * ag_mult))
                            sell_reasons.append(f"V8 ផ្អៀង SELL ({d_sell['pct']*100:.0f}%)")
            except Exception as e:
                soft_warnings.append(f"V8 Sync: {str(e)[:50]}")

        market_state = qa.describe_market(r0, payload, global_news, war_news, session_kz)

        # ---------- HARD SAFETY GATES ----------
        if price <= 0:
            hard_blocks.append("មិនទាន់ទទួលបានតម្លៃមាសផ្ទាល់")
        if sanity.get("glitch_detected"):
            hard_blocks.append(f"ប្រព័ន្ធការពារ Feed Glitch៖ {sanity.get('note', 'តម្លៃខុសប្រក្រតី')}")
        if is_forex_weekend_closed(now_utc):
            hard_blocks.append("ទីផ្សារ Forex បិទចុងសប្តាហ៍ (បើកអាទិត្យ 22:00 UTC)")
        if cal_news.get("danger"):
            ev = cal_news.get("danger_event") or {}
            hard_blocks.append(f"ព័ត៌មានក្រហម USD ({ev.get('title', 'Red News')}) ជិតចេញ")
        if spread > 1.50:
            hard_blocks.append(f"Spread រីកខ្ពស់ខ្លាំង (${spread:.2f}) — ហានិភ័យ Slippage")
        elif spread > 0.80:
            soft_warnings.append(f"Spread រាងធំ (${spread:.2f}) — គួរប្រើ Lot តូច")

        # ---------- PILLAR 2: MICROSTRUCTURE + OB/SMC + QUANT + SMT + MTF (Max 45 pts) ----------
        ms = qa.analyze_microstructure(candles, price, ema20, ema50, atr_chart, smc, order_flow=order_flow)
        p2_buy, p2_sell = ms["buy_pts"], ms["sell_pts"]
        buy_reasons.extend(ms["notes_buy"])
        sell_reasons.extend(ms["notes_sell"])

        smc_bias = smc.get("bias", "NEUTRAL")
        if smc_bias == "BULLISH":
            p2_buy += 7
        elif smc_bias == "BEARISH":
            p2_sell += 7

        tfs = {t.get("label"): t.get("bias") for t in (mtf.get("timeframes") or [])}
        htf_buy = sum(1 for k in ("1H", "4H", "1D") if tfs.get(k) == "BUY")
        htf_sell = sum(1 for k in ("1H", "4H", "1D") if tfs.get(k) == "SELL")
        if mtf.get("aligned") == "BUY":
            p2_buy += 14
            buy_reasons.append("MTF 4 Timeframes ស្របទិស BUY")
        elif mtf.get("aligned") == "SELL":
            p2_sell += 14
            sell_reasons.append("MTF 4 Timeframes ស្របទិស SELL")
        else:
            if htf_buy >= 2:
                p2_buy += 7
            if htf_sell >= 2:
                p2_sell += 7

        # Institutional Quant Scoring (មាន VWAP Overextension Penalty ការពារដេញទិញនៅកំពូល)
        vwap_val = safe_float(quant.get("vwap"), 0.0)
        vwap_dist = (price - vwap_val) if (vwap_val > 0 and price > 0) else 0.0
        cvd_div = str(quant.get("cvd_divergence") or "NONE")
        cvd_pct = int(safe_float(quant.get("cvd_pct"), 50))
        max_healthy_vwap = max(atr_chart * 3.2, 6.5)

        if quant:
            if vwap_val > 0:
                if 0.25 <= vwap_dist <= max_healthy_vwap:
                    p2_buy += 5
                    buy_reasons.append(f"តម្លៃលើ VWAP សមស្រប (${vwap_val:.1f})")
                elif vwap_dist > max_healthy_vwap:
                    p2_buy = max(0, p2_buy - 10)  # ហួស VWAP ឆ្ងាយពេក ដកពិន្ទុ BUY
                elif -max_healthy_vwap <= vwap_dist <= -0.25:
                    p2_sell += 5
                    sell_reasons.append(f"តម្លៃក្រោម VWAP សមស្រប (${vwap_val:.1f})")
                elif vwap_dist < -max_healthy_vwap:
                    p2_sell = max(0, p2_sell - 10)  # ហួស VWAP ក្រោមឆ្ងាយពេក ដកពិន្ទុ SELL

            if cvd_div == "BULL_DIV":
                p2_buy += 9
                p2_sell = max(0, p2_sell - 8)
                buy_reasons.insert(0, "CVD Divergence ▲ — ស្ថាប័នលួចប្រមូលទិញ")
            elif cvd_div == "BEAR_DIV":
                p2_sell += 9
                p2_buy = max(0, p2_buy - 8)
                sell_reasons.insert(0, "CVD Divergence ▼ — ស្ថាប័នលួចលក់ចេញ")

            if cvd_pct >= 60:
                p2_buy += 5
            elif cvd_pct <= 40:
                p2_sell += 5

            poc_v = safe_float(quant.get("poc"), 0.0)
            if poc_v > 0:
                if price > poc_v and (price - poc_v) <= atr_chart * 1.4:
                    p2_buy += 4
                elif price < poc_v and (poc_v - price) <= atr_chart * 1.4:
                    p2_sell += 4

            if quant.get("squeeze_state") == "FIRING":
                if p2_buy > p2_sell:
                    p2_buy += 4
                    buy_reasons.append("Volatility Squeeze កំពុងផ្ទុះឡើង!")
                elif p2_sell > p2_buy:
                    p2_sell += 4
                    sell_reasons.append("Volatility Squeeze កំពុងផ្ទុះចុះ!")

        # Gold/Silver SMT Divergence
        smt_sig = smt.get("signal", "NONE")
        if smt_sig == "BULL_SMT":
            p2_buy += 9
            p2_sell = max(0, p2_sell - 7)
            buy_reasons.insert(0, "XAU/XAG SMT ▲ — ប្រាក់មិនចុះតាមមាស (បោស SL ត្រៀមឡើង)")
        elif smt_sig == "BEAR_SMT":
            p2_sell += 9
            p2_buy = max(0, p2_buy - 7)
            sell_reasons.insert(0, "XAU/XAG SMT ▼ — ប្រាក់មិនឡើងតាមមាស (អន្ទាក់លក់ចុះ)")

        p2_buy, p2_sell = min(45, p2_buy), min(45, p2_sell)
        if ms["falling_knife"]:
            p1_buy = int(p1_buy * 0.35)
        if ms["rising_rocket"]:
            p1_sell = int(p1_sell * 0.35)

        # ---------- PILLAR 3: MACRO + NEWS + KILLZONE (Max 25 pts) ----------
        fund = qa.score_fundamentals(dxy_r1, us10y_d5, macro_snapshot.get("vix"), global_news, war_news)
        p3_buy, p3_sell = fund["buy_pts"], fund["sell_pts"]
        buy_reasons.extend(fund["notes_buy"])
        sell_reasons.extend(fund["notes_sell"])

        buy_pts = max(0, min(100, p1_buy + p2_buy + p3_buy + (session_kz["bonus"] if p2_buy > p2_sell else 0)))
        sell_pts = max(0, min(100, p1_sell + p2_sell + p3_sell + (session_kz["bonus"] if p2_sell > p2_buy else 0)))
        if rsi >= 70.0:
            buy_pts = max(0, buy_pts - 10)
        if rsi <= 30.0:
            sell_pts = max(0, sell_pts - 10)

        # ---------- ANTI-TRAP CONTEXT DETECTION (CHoCH / OB CEILING / LIVE CANDLE) ----------
        recent_bull_break, recent_bear_break = False, False
        if candles and len(candles) >= 8:
            win_start_ts = safe_float(candles[-8].get("time"), 0.0)
            for br in reversed(smc.get("breaks") or []):
                end_t = safe_float(br.get("end_time"), 0.0)
                if end_t >= win_start_ts:
                    bt = str(br.get("type") or "")
                    if bt.endswith("_DOWN") or bt.endswith("_DN"):
                        recent_bear_break = True
                    elif bt.endswith("_UP"):
                        recent_bull_break = True
                    break

        near_bear_ob = None
        for sob in (smc.get("supply_blocks") or []):
            if str(sob.get("label", "OB")).upper() != "OB":
                continue
            s_bot, s_top = safe_float(sob.get("bottom")), safe_float(sob.get("top"))
            if s_bot > 0 and (s_bot - atr_chart * 0.75) <= price <= (s_top + atr_chart * 0.35):
                near_bear_ob = (s_bot, s_top)
                break

        near_bull_ob = None
        for ob in (smc.get("demand_blocks") or []):
            if str(ob.get("label", "OB")).upper() != "OB":
                continue
            b_bot, b_top = safe_float(ob.get("bottom")), safe_float(ob.get("top"))
            if b_bot > 0 and (b_bot - atr_chart * 0.35) <= price <= (b_top + atr_chart * 0.75):
                near_bull_ob = (b_bot, b_top)
                break

        live_c = candles[-1] if candles else {}
        c0_o, c0_c = safe_float(live_c.get("open"), price), safe_float(live_c.get("close"), price)
        live_candle_red = (c0_o - c0_c) >= max(atr_chart * 0.30, 0.35)
        live_candle_green = (c0_c - c0_o) >= max(atr_chart * 0.30, 0.35)

        # ---------- DECISION & STRICT PRO THRESHOLDS ----------
        margin = buy_pts - sell_pts
        with self._lock:
            prev_mem = self._last_signal_mem.get(interval_key)
        was_same = prev_mem is not None and (time.monotonic() - prev_mem["ts"]) < 75.0

        cb_active = bool(circuit_breaker.get("active"))
        # ដំឡើងស្តង់ដាចម្រោះឱ្យខ្ពស់ជាងមុន (Quality > Quantity) ការពារចូលជើងខ្សោយលើ 1m/5m
        base_thresh = (66 if cb_active else 58) if interval_key in ("1m", "5m") else (64 if cb_active else 56)
        min_margin = 22 if cb_active else 18

        buy_thresh = (base_thresh - 3) if (was_same and prev_mem["side"] == "BUY" and not cb_active) else base_thresh
        sell_thresh = (base_thresh - 3) if (was_same and prev_mem["side"] == "SELL" and not cb_active) else base_thresh

        candidate_side, raw_score, pass_reasons = "NONE", max(buy_pts, sell_pts), []
        if buy_pts >= buy_thresh and margin >= min_margin:
            candidate_side, raw_score, pass_reasons = "BUY", buy_pts, buy_reasons
        elif sell_pts >= sell_thresh and (-margin) >= min_margin:
            candidate_side, raw_score, pass_reasons = "SELL", sell_pts, sell_reasons

        is_scalp = (candidate_side != "NONE") and (mtf.get("aligned") != candidate_side or raw_score < 68)
        ema_top, ema_bot = max(ema20, ema50), min(ema20, ema50)
        bias_for_preview = candidate_side if candidate_side != "NONE" else (
            "BUY" if price > ema_top else ("SELL" if price < ema_bot else ("BUY" if buy_pts >= sell_pts else "SELL"))
        )
        preview_plan = qa.build_precision_plan(bias_for_preview, price, payload, atr_chart, spread, inst, "A", is_scalp=is_scalp) if price > 0 else None

        # ---------- 5-LAYER ANTI-TRAP HARD GATES ----------
        real_risk_blocks = []
        if cb_active and candidate_side != "NONE" and raw_score < 68:
            trap_blocks.append("Anti-Chop៖ កម្រិតពិន្ទុមិនទាន់ដល់ A — រង់ចាំសញ្ញាច្បាស់ជាងនេះ")

        if candidate_side == "BUY":
            if price < ema_bot:
                real_risk_blocks.append(f"ហាម BUY បញ្ច្រាស Trend៖ តម្លៃនៅក្រោម EMA 20/50 (${ema_bot:.2f})")
            elif ms["falling_knife"] or (price < ema20 and not ms["bull_reversal"]):
                real_risk_blocks.append("ហាមចាប់កាំបិតធ្លាក់ — ចាំឡើងផុត EMA 20 សិន")
            if recent_bear_break:
                real_risk_blocks.append("ហាម BUY៖ ក្រាហ្វិកទើបបែក CHoCH/BOS ចុះ (សញ្ញាប្តូរទិស)")
            if cvd_div == "BEAR_DIV" or cvd_pct <= 38:
                real_risk_blocks.append(f"ហាម BUY៖ លុយធំកំពុងលក់ចេញ (CVD {cvd_pct}% / {cvd_div})")
            if near_bear_ob is not None:
                trap_blocks.append(f"ហាម BUY បុកកំពូល Bearish OB (${near_bear_ob[0]:.2f}–${near_bear_ob[1]:.2f})")
            if vwap_val > 0 and vwap_dist > max(atr_chart * 3.8, 8.0):
                trap_blocks.append(f"ហួស VWAP ឆ្ងាយពេក (+${vwap_dist:.1f}) — ហាមដេញទិញនៅកំពូល")
            if live_candle_red and ms["buy_pressure_pct"] < 52:
                trap_blocks.append("ទៀនផ្ទាល់កំពុងទម្លាក់ក្រហម — ចាំឈប់ចុះសិន")
            if rsi >= 74.0:
                real_risk_blocks.append(f"RSI ({rsi:.0f}) ខ្ពស់ជ្រុល — ចាំថយសិន")
            if preview_plan and preview_plan.get("rr_num", 2.0) < 1.40:
                trap_blocks.append(f"R:R ទាប ({preview_plan['rr']}) — ជាប់តំបន់ទប់លើ")

        elif candidate_side == "SELL":
            if price > ema_top:
                real_risk_blocks.append(f"ហាម SELL បញ្ច្រាស Trend៖ តម្លៃនៅលើ EMA 20/50 (${ema_top:.2f})")
            elif ms["rising_rocket"] or (price > ema20 and not ms["bear_reversal"]):
                real_risk_blocks.append("ហាមទប់ឡាន — ចាំធ្លាក់ក្រោម EMA 20 សិន")
            if recent_bull_break:
                real_risk_blocks.append("ហាម SELL៖ ក្រាហ្វិកទើបបែក CHoCH/BOS ឡើង (សញ្ញាប្តូរទិស)")
            if cvd_div == "BULL_DIV" or cvd_pct >= 62:
                real_risk_blocks.append(f"ហាម SELL៖ លុយធំកំពុងប្រមូលទិញ (CVD {cvd_pct}% / {cvd_div})")
            if near_bull_ob is not None:
                trap_blocks.append(f"ហាម SELL បុកបាត Bullish OB (${near_bull_ob[0]:.2f}–${near_bull_ob[1]:.2f})")
            if vwap_val > 0 and vwap_dist < -max(atr_chart * 3.8, 8.0):
                trap_blocks.append(f"ហួស VWAP ក្រោមឆ្ងាយពេក (${vwap_dist:.1f}) — ហាមដេញលក់នៅបាត")
            if live_candle_green and ms["buy_pressure_pct"] > 48:
                trap_blocks.append("ទៀនផ្ទាល់កំពុងហក់បៃតង — ចាំឈប់ឡើងសិន")
            if rsi <= 26.0:
                real_risk_blocks.append(f"RSI ({rsi:.0f}) ទាបជ្រុល — ចាំងើបសិន")
            if preview_plan and preview_plan.get("rr_num", 2.0) < 1.40:
                trap_blocks.append(f"R:R ទាប ({preview_plan['rr']}) — ជាប់តំបន់ទ្រក្រោម")

        buy_trig, sell_trig = qa.compute_true_triggers(price, spread, ema20, ema50, candles, smc)

        high_conf_alert, sniper_text_km, close_confirmed = False, "", False
        if hard_blocks or real_risk_blocks:
            status, can_trade, grade, final_side, is_danger = "BLOCKED", False, "RISK", "NONE", True
            confidence_score = 20
            action_km = "⛔ ហាមចូល (ការពារអន្ទាក់)"
            reasons = hard_blocks + real_risk_blocks + soft_warnings
            hud_hint_km, toast_detail_km, plan = f"⛔ {reasons[0]}", reasons[0], None
        elif candidate_side == "NONE" or trap_blocks:
            status, can_trade, grade, final_side, is_danger = "WAIT", False, "WATCH", "NONE", False
            confidence_score = min(56, max(35, raw_score))
            action_km = f"⏳ រង់ចាំចំណុចល្អ (ផ្អៀង {bias_for_preview})"
            reasons = trap_blocks if trap_blocks else [f"រង់ចាំបែកលើ ${buy_trig:.2f} ដើម្បី BUY ឬក្រោម ${sell_trig:.2f} ដើម្បី SELL"]
            hud_hint_km, toast_detail_km, plan = f"BUY > ${buy_trig:.2f} • SELL < ${sell_trig:.2f}", reasons[0], None
        else:
            final_side, can_trade, is_danger, status = candidate_side, True, False, f"APPROVED_{candidate_side}"
            close_confirmed = ms["close_confirmed_buy"] if final_side == "BUY" else ms["close_confirmed_sell"]
            with self._lock:
                self._last_signal_mem[interval_key] = {"side": final_side, "ts": time.monotonic()}

            if v8_confirmed_side == final_side and ai_pct is not None and ai_pct >= a_plus_pct and raw_score >= 76:
                grade = "A+"
            elif raw_score >= 70:
                grade = "A"
            elif raw_score >= 62:
                grade = "B+"
            else:
                grade = "B"

            confidence_score = raw_score
            plan = qa.build_precision_plan(final_side, price, payload, atr_chart, spread, inst, grade, is_scalp=(grade == "B"))

            mtf_not_opp = (
                (final_side == "BUY" and mtf.get("aligned") != "SELL" and htf_sell < 2)
                or (final_side == "SELL" and mtf.get("aligned") != "BUY" and htf_buy < 2)
            )
            if grade in ("A+", "A") and confidence_score >= 70 and close_confirmed and mtf_not_opp and plan and plan.get("entry_state") == "IN_ZONE" and plan.get("rr_num", 0.0) >= 1.5:
                high_conf_alert = True
                act_word = "ទិញ (BUY)" if final_side == "BUY" else "លក់ (SELL)"
                sniper_text_km = f"🔥 ឱកាសខ្ពស់ {confidence_score}% ({grade}) • គួរ{act_word} នៅ ${plan['entry']:.2f} • SL ${plan['sl']:.2f} • TP1 ${plan['tp']:.2f}"

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

        exit_shield = qa.compute_exit_shield(price, ema20, rsi, spread, ms, cal_news, final_side if can_trade else "NONE")
        beginner_guide = qa.build_beginner_guide(
            price, interval_key, market_state, smc, candles,
            final_side, can_trade, plan, preview_plan,
            buy_trig, sell_trig, global_news, war_news, macro_snapshot,
        )
        last_hi = safe_float(candles[-1].get("high"), price) if candles else price
        last_lo = safe_float(candles[-1].get("low"), price) if candles else price
        audit_rec = (
            {
                "side": final_side, "grade": grade, "confidence": int(confidence_score),
                "is_sniper": high_conf_alert, "entry_state": plan.get("entry_state", "IN_ZONE"),
                "entry": plan["entry"], "sl": plan["sl"], "tp": plan["tp"],
            }
            if (can_trade and plan)
            else None
        )
        signal_stats = self.tracker.update_and_record(
            interval_key, price, last_hi, last_lo, audit_rec, spread=spread
        )

        return {
            "version": self.ml.version_label,
            "deployable": self.ml.deployable,
            "tactical_mode": (
                "V8_CONFIRMED" if (can_trade and v8_confirmed_side == final_side)
                else ("SCALP" if (can_trade and grade == "B") else ("QUANT_CONFLUENCE" if can_trade else "WAIT"))
            ),
            "status": status, "grade": grade, "is_danger": is_danger,
            "high_conf_alert": high_conf_alert, "close_confirmed": close_confirmed,
            "buy_pressure_pct": ms["buy_pressure_pct"], "sniper_text_km": sniper_text_km,
            "confidence_score": int(confidence_score), "action_km": action_km,
            "hud_hint_km": hud_hint_km, "toast_detail_km": toast_detail_km,
            "can_trade": can_trade, "side": final_side,
            "ai_rank_pct": round(ai_pct * 100, 1) if ai_pct is not None else None,
            "ai_prob": round(ai_prob * 100, 1) if ai_prob is not None else None,
            "v8_agreement_pct": int(round(v8_agreement * 100)) if v8_res else None,
            "cutoff_pct": round(min_pct * 100, 0), "bar_time": bar_time,
            "session_edge": session_kz, "liquidity_sweep": ms["sweep_note"],
            "market_state": market_state, "exit_shield": exit_shield,
            "beginner_guide": beginner_guide, "quant": quant,
            "smt": smt, "circuit_breaker": circuit_breaker,
            "reasons": reasons, "passed": pass_reasons, "warnings": soft_warnings,
            "plan": plan, "preview_plan": preview_plan, "signal_stats": signal_stats,
            "pillars": {
                "v8_ai": {"buy": p1_buy, "sell": p1_sell, "max": 30},
                "structure_smc": {"buy": p2_buy, "sell": p2_sell, "max": 45},
                "macro_news": {"buy": p3_buy, "sell": p3_sell, "max": 25},
            },
            "score_breakdown": {"buy_pts": buy_pts, "sell_pts": sell_pts, "margin": margin, "candidate_side": candidate_side},
            "macro": macro_snapshot, "global_news": global_news, "war_news": war_news,
        }


brain_service = GoldBrainService()