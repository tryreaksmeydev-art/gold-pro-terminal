import os
import json
import threading
from datetime import datetime, timezone

import numpy as np

from .utils import TZ_KH, safe_float

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERT_MEMORY_PATH = os.path.join(BASE_DIR, "models", "expert_memory.json")
COMM_PIPS = 1.6


class ExpertSelfLearningMemory:
    """
    ខួរក្បាលចងចាំ និងរៀនពីកំហុសខ្លួនឯង (Online Self-Learning & Mistake Blacklist)៖
    - កត់ត្រា Pattern Signature នៃរាល់សញ្ញា 5m ដែលបានបញ្ចេញ
    - តាមដានលទ្ធផលពិត (WIN / TRAIL_WIN / LOSS) ដោយដក Spread + Commission
    - ដាក់បញ្ជីខ្មៅ (Blacklist) លើទម្រង់ណាដែលធ្លាប់ខាត ដើម្បីកុំឱ្យដើរជាន់កំហុសដដែល
    - បង្កើនពិន្ទុទំនុកចិត្ត (Bayesian Edge Boost) លើទម្រង់ណាដែលឈ្នះជាប់ៗគ្នា
    """

    def __init__(self, path: str = EXPERT_MEMORY_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.state = {
            "total_wins": 0,
            "total_losses": 0,
            "mistakes_blocked": 0,
            "net_pips": 0.0,
            "patterns": {},      # sig -> {wins, losses, streak_loss, streak_win, last_ts, note}
            "active_trade": None,
            "learned_log": [],   # ប្រវត្តិមេរៀនឈ្នះ/ខាតថ្មីៗ
        }
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k in self.state:
                        if k in raw:
                            self.state[k] = raw[k]
        except Exception as e:
            print(f"[EXPERT_MEM] load fallback: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"[EXPERT_MEM] save error: {e}")

    def inspect_pattern(self, sig: str) -> dict:
        """ពិនិត្យបទពិសោធន៍ចាស់របស់ខួរក្បាលលើ Pattern នេះ (Bayesian Win-Rate + Blacklist)"""
        with self._lock:
            rec = dict(self.state["patterns"].get(sig) or {})

        w = int(rec.get("wins", 0))
        l = int(rec.get("losses", 0))
        sl_streak = int(rec.get("streak_loss", 0))
        sw_streak = int(rec.get("streak_win", 0))
        n = w + l
        # Bayesian smoothed win rate (ចាប់ផ្តើមពី 50% ហើយប្រែប្រួលតាមទិន្នន័យពិត)
        bayes_wr = round(((w + 2.0) / (n + 4.0)) * 100.0, 1)
        raw_wr = round((w / n) * 100.0, 1) if n > 0 else None

        blacklisted = (sl_streak >= 2) or (n >= 3 and (w / n) < 0.40)
        boosted = (not blacklisted) and ((sw_streak >= 2) or (n >= 3 and (w / n) >= 0.65))

        score_adj = 0
        status_km = "ទម្រង់ថ្មី (កំពុងសិក្សា)"
        if blacklisted:
            score_adj = -25
            status_km = f"🚫 ប្លុកកំហុសចាស់ (ធ្លាប់ឈ្នះ {w} / ខាត {l})"
        elif boosted:
            score_adj = min(12, 6 + sw_streak * 2)
            status_km = f"★ ទម្រង់ឈ្នះខ្ពស់ (ឈ្នះ {w} / ខាត {l} • {raw_wr}%)"
        elif l >= 1 and w == 0:
            score_adj = -8
            status_km = f"⚠️ ប្រុងប្រយ័ត្ន (ទម្រង់នេះធ្លាប់ខាត {l} ដង)"
        elif w >= 1:
            score_adj = 4
            status_km = f"✓ ធ្លាប់ឈ្នះ {w} / ខាត {l} ({raw_wr}%)"

        return {
            "signature": sig,
            "wins": w,
            "losses": l,
            "samples": n,
            "raw_win_rate": raw_wr,
            "bayes_win_rate": bayes_wr,
            "blacklisted": blacklisted,
            "boosted": boosted,
            "score_adj": score_adj,
            "status_km": status_km,
        }

    def note_mistake_blocked(self):
        with self._lock:
            self.state["mistakes_blocked"] = int(self.state.get("mistakes_blocked", 0)) + 1
            self._save()

    def track_and_learn(
        self,
        price: float,
        high: float,
        low: float,
        spread: float,
        new_trade: dict | None,
    ) -> dict:
        """តាមដានជើងសកម្ម 5m ផ្ទាល់ រួចកត់ត្រាមេរៀនឈ្នះ/ខាតចូលខួរក្បាលដោយស្វ័យប្រវត្តិ"""
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        now_kh = now_utc.astimezone(TZ_KH).strftime("%d/%m %H:%M")
        cost_pips = round((max(0.0, safe_float(spread, 0.25)) * 10.0) + COMM_PIPS, 1)
        changed = False

        with self._lock:
            act = self.state.get("active_trade")
            if act and price > 0:
                hi = max(price, high) if high > 0 else price
                lo = min(price, low) if low > 0 else price

                # ពិនិត្យថាជើង Limit ត្រូវបានប៉ះ (Filled) ឬនៅ
                if not act.get("filled", True):
                    if lo <= act["entry"] <= hi:
                        act["filled"] = True
                        act["filled_ts"] = now_ts
                        act["open_kh"] = now_kh
                        changed = True
                    elif (now_ts - int(act.get("created_ts", now_ts))) > 1800:
                        # ហួស 30 នាទីមិនប៉ះ Entry -> លុបចោល
                        self.state["active_trade"] = None
                        act = None
                        changed = True

                if act and act.get("filled", True):
                    side = act["side"]
                    entry = float(act["entry"])
                    sl = float(act["sl"])
                    tp1 = float(act["tp1"])
                    be_trigger = float(act["be_trigger"])

                    # ពិនិត្យថាដល់ BE (ការពារចំណេញ) ហើយឬនៅ
                    if not act.get("be_locked"):
                        if (side == "BUY" and hi >= be_trigger) or (side == "SELL" and lo <= be_trigger):
                            act["be_locked"] = True
                            act["sl"] = round(entry + (0.25 if side == "BUY" else -0.25), 2)
                            sl = act["sl"]
                            changed = True

                    outcome, exit_px = None, price
                    if side == "BUY":
                        if lo <= sl:
                            outcome = "TRAIL_WIN" if act.get("be_locked") else "LOSS"
                            exit_px = sl
                        elif hi >= tp1:
                            outcome, exit_px = "WIN", tp1
                    else:
                        if hi >= sl:
                            outcome = "TRAIL_WIN" if act.get("be_locked") else "LOSS"
                            exit_px = sl
                        elif lo <= tp1:
                            outcome, exit_px = "WIN", tp1

                    if outcome:
                        diff = (exit_px - entry) if side == "BUY" else (entry - exit_px)
                        pips = round((diff * 10.0) - cost_pips, 1)
                        is_win = pips > 0

                        sig = act["signature"]
                        pat = self.state["patterns"].setdefault(
                            sig, {"wins": 0, "losses": 0, "streak_loss": 0, "streak_win": 0, "last_ts": now_ts}
                        )
                        if is_win:
                            self.state["total_wins"] = int(self.state["total_wins"]) + 1
                            pat["wins"] = int(pat["wins"]) + 1
                            pat["streak_win"] = int(pat.get("streak_win", 0)) + 1
                            pat["streak_loss"] = 0
                            lesson_km = f"✓ រៀនទម្រង់ឈ្នះ ({side} +{pips:.0f}p) — បង្កើនពិន្ទុទំនុកចិត្ត"
                        else:
                            self.state["total_losses"] = int(self.state["total_losses"]) + 1
                            pat["losses"] = int(pat["losses"]) + 1
                            pat["streak_loss"] = int(pat.get("streak_loss", 0)) + 1
                            pat["streak_win"] = 0
                            lesson_km = f"✕ ចងចាំកំហុស ({side} {pips:.0f}p) — បន្ថយពិន្ទុ/ប្លុកទម្រង់នេះ"

                        pat["last_ts"] = now_ts
                        self.state["net_pips"] = round(float(self.state.get("net_pips", 0.0)) + pips, 1)
                        self.state["learned_log"].insert(0, {
                            "time_kh": now_kh,
                            "side": side,
                            "result": "WIN" if is_win else "LOSS",
                            "pips": pips,
                            "entry": round(entry, 2),
                            "exit": round(exit_px, 2),
                            "signature": sig,
                            "lesson_km": lesson_km,
                        })
                        self.state["learned_log"] = self.state["learned_log"][:15]
                        self.state["active_trade"] = None
                        changed = True
                    elif (now_ts - int(act.get("filled_ts", now_ts))) > 14400:
                        self.state["active_trade"] = None
                        changed = True

            # បើកជើងតាមដានថ្មី បើមិនទាន់មានជើងសកម្ម
            if new_trade and self.state.get("active_trade") is None:
                self.state["active_trade"] = {
                    "side": new_trade["side"],
                    "signature": new_trade["signature"],
                    "entry": new_trade["entry"],
                    "sl": new_trade["sl"],
                    "tp1": new_trade["tp1"],
                    "be_trigger": new_trade["be_trigger"],
                    "filled": bool(new_trade.get("is_market", True)),
                    "be_locked": False,
                    "created_ts": now_ts,
                    "filled_ts": now_ts,
                    "open_kh": now_kh,
                }
                changed = True

            if changed:
                self._save()

            tw = int(self.state["total_wins"])
            tl = int(self.state["total_losses"])
            tot = tw + tl
            blacklisted_cnt = sum(
                1 for p in self.state["patterns"].values()
                if int(p.get("streak_loss", 0)) >= 2
                or ((int(p.get("wins", 0)) + int(p.get("losses", 0))) >= 3
                    and int(p.get("wins", 0)) / max(1, int(p.get("wins", 0)) + int(p.get("losses", 0))) < 0.40)
            )
            winning_cnt = sum(
                1 for p in self.state["patterns"].values()
                if int(p.get("streak_win", 0)) >= 2
                or ((int(p.get("wins", 0)) + int(p.get("losses", 0))) >= 3
                    and int(p.get("wins", 0)) / max(1, int(p.get("wins", 0)) + int(p.get("losses", 0))) >= 0.65)
            )
            iq_level = min(99, 60 + min(25, tot * 2) + min(14, int(self.state["mistakes_blocked"])))

            return {
                "total_wins": tw,
                "total_losses": tl,
                "total_trades": tot,
                "win_rate": round((tw / tot) * 100.0, 1) if tot > 0 else None,
                "net_pips": round(float(self.state.get("net_pips", 0.0)), 1),
                "mistakes_blocked": int(self.state.get("mistakes_blocked", 0)),
                "patterns_learned": len(self.state["patterns"]),
                "blacklisted_patterns": blacklisted_cnt,
                "winning_patterns": winning_cnt,
                "iq_level": iq_level,
                "active_trade": self.state.get("active_trade"),
                "recent_lessons": self.state["learned_log"][:6],
            }


class AdvancedExpertAnalyst:
    """
    អ្នកជំនាញវិភាគកម្រិតខ្ពស់ (5m Institutional Precision Engine)៖
    - Dealing Range Premium/Discount & OTE (61.8% - 78.6%)
    - Liquidity Sweep Detector (Stop-Hunt Reversal)
    - Zero-Contradiction OB Entry Calibration
    - Exact Buy/Sell Stop & Trailing Profit-Lock Map
    - Self-Learning Adaptive Brain Memory
    """

    def __init__(self):
        self.memory = ExpertSelfLearningMemory()

    def _compute_dealing_range(self, candles: list[dict], price: float) -> dict:
        if not candles or len(candles) < 12:
            return {
                "high": price + 5.0, "low": price - 5.0, "eq": price,
                "zone": "EQUILIBRIUM", "pct": 50.0,
                "ote_buy_hi": price, "ote_buy_lo": price - 2.0,
                "ote_sell_lo": price, "ote_sell_hi": price + 2.0,
                "sweep": "NONE", "sweep_km": "គ្មានការបោស SL ថ្មីៗ",
            }

        win = candles[-48:] if len(candles) >= 48 else candles
        highs = np.array([safe_float(c.get("high"), price) for c in win], dtype=float)
        lows = np.array([safe_float(c.get("low"), price) for c in win], dtype=float)
        closes = np.array([safe_float(c.get("close"), price) for c in win], dtype=float)

        r_hi = float(np.max(highs))
        r_lo = float(np.min(lows))
        span = max(r_hi - r_lo, 1.0)
        eq = r_lo + span * 0.50
        pos_pct = round(((price - r_lo) / span) * 100.0, 1)

        if pos_pct >= 62.0:
            zone = "PREMIUM"
        elif pos_pct <= 38.0:
            zone = "DISCOUNT"
        else:
            zone = "EQUILIBRIUM"

        # ICT OTE (Optimal Trade Entry: 61.8% - 78.6% retracement)
        ote_buy_hi = round(r_hi - span * 0.618, 2)
        ote_buy_lo = round(r_hi - span * 0.786, 2)
        ote_sell_lo = round(r_lo + span * 0.618, 2)
        ote_sell_hi = round(r_lo + span * 0.786, 2)

        # Liquidity Sweep Check (3 ទៀនចុងក្រោយបោសលើ/ក្រោម Swing មុន ហើយបិទត្រឡប់ចូលវិញ)
        sweep, sweep_km = "NONE", "គ្មានការបោស SL ថ្មីៗ"
        if len(win) >= 15:
            prev_hi = float(np.max(highs[:-3]))
            prev_lo = float(np.min(lows[:-3]))
            rec_hi = float(np.max(highs[-3:]))
            rec_lo = float(np.min(lows[-3:]))
            last_c = float(closes[-1])

            if rec_lo < prev_lo and last_c > prev_lo:
                sweep = "BULL_SWEEP"
                sweep_km = f"⚡ ទើបបោស SL ក្រោម (${prev_lo:.2f}) រួចទាញឡើងវិញ — ស្ថាប័នប្រមូលទិញ!"
            elif rec_hi > prev_hi and last_c < prev_hi:
                sweep = "BEAR_SWEEP"
                sweep_km = f"⚡ ទើបបោស SL លើ (${prev_hi:.2f}) រួចទម្លាក់វិញ — ស្ថាប័នលក់ចេញ!"

        return {
            "high": round(r_hi, 2),
            "low": round(r_lo, 2),
            "eq": round(eq, 2),
            "zone": zone,
            "pct": pos_pct,
            "ote_buy_hi": ote_buy_hi,
            "ote_buy_lo": ote_buy_lo,
            "ote_sell_lo": ote_sell_lo,
            "ote_sell_hi": ote_sell_hi,
            "sweep": sweep,
            "sweep_km": sweep_km,
        }

    def _find_nearest_ob(self, blocks: list[dict], price: float, want_below: bool) -> dict | None:
        obs = [b for b in (blocks or []) if str(b.get("label", "OB")).upper() == "OB"]
        if not obs:
            return None
        if want_below:
            below = [b for b in obs if safe_float(b.get("bottom"), 0.0) <= price + 1.0]
            if not below:
                return None
            return max(below, key=lambda b: safe_float(b.get("top"), 0.0))
        above = [b for b in obs if safe_float(b.get("top"), 0.0) >= price - 1.0]
        if not above:
            return None
        return min(above, key=lambda b: safe_float(b.get("bottom"), 0.0))

    def analyze(self, payload_5m: dict) -> dict:
        """វិភាគស៊ីជម្រៅលើទៀន 5m ពេញលេញ គ្មានការទាយ និងគ្មានទំនាស់តំបន់"""
        price = safe_float(payload_5m.get("price"), 0.0)
        spread = safe_float(payload_5m.get("spread"), 0.25)
        atr = max(safe_float(payload_5m.get("atr"), 2.0), 0.8)
        ema20 = safe_float(payload_5m.get("ema20"), price)
        ema50 = safe_float(payload_5m.get("ema50"), price)
        rsi = safe_float(payload_5m.get("rsi"), 50.0)
        candles = payload_5m.get("candles") or []
        smc = payload_5m.get("smc") or {}
        quant = payload_5m.get("quant") or {}
        mtf = payload_5m.get("mtf") or {}
        smt = payload_5m.get("smt") or {}
        news = payload_5m.get("news") or {}
        brain = payload_5m.get("brain") or {}

        now_ts = int(datetime.now(timezone.utc).timestamp())
        candle_rem_sec = 300 - (now_ts % 300)

        dr = self._compute_dealing_range(candles, price)
        vwap = safe_float(quant.get("vwap"), price)
        poc = safe_float(quant.get("poc"), price)
        cvd_pct = int(safe_float(quant.get("cvd_pct"), 50))
        cvd_div = str(quant.get("cvd_divergence") or "NONE")
        squeeze = str(quant.get("squeeze_state") or "NORMAL")
        smt_sig = str(smt.get("signal") or "NONE")
        sess_name = str((payload_5m.get("session") or {}).get("name") or "Active")
        sess_tag = "OVERLAP" if "Overlap" in sess_name else ("NY" if "New York" in sess_name else ("LDN" if "London" in sess_name else "ASIA"))

        bull_ob = self._find_nearest_ob(smc.get("demand_blocks"), price, want_below=True)
        bear_ob = self._find_nearest_ob(smc.get("supply_blocks"), price, want_below=False)

        # 1) គណនាពិន្ទុស្ថាប័ន 8 កត្តា (Institutional Confluence Matrix)
        buy_score, sell_score = 0, 0
        pro_factors_buy, pro_factors_sell = [], []

        # Factor 1: Trend Structure (EMA20/50 + SMC Bias)
        if price > ema20 and ema20 >= ema50:
            buy_score += 18
            pro_factors_buy.append("និន្នាការ 5m ឡើងរឹងមាំ (លើ EMA 20/50)")
        elif price < ema20 and ema20 <= ema50:
            sell_score += 18
            pro_factors_sell.append("និន្នាការ 5m ចុះរឹងមាំ (ក្រោម EMA 20/50)")

        if smc.get("bias") == "BULLISH":
            buy_score += 10
        elif smc.get("bias") == "BEARISH":
            sell_score += 10

        # Factor 2: Premium / Discount (ICT Institutional Rule)
        if dr["zone"] == "DISCOUNT":
            buy_score += 16
            sell_score = max(0, sell_score - 12)
            pro_factors_buy.append(f"តម្លៃនៅតំបន់ថោក Discount ({dr['pct']}%) — ស្ថាប័នចូលទិញ")
        elif dr["zone"] == "PREMIUM":
            sell_score += 16
            buy_score = max(0, buy_score - 12)
            pro_factors_sell.append(f"តម្លៃនៅតំបន់ថ្លៃ Premium ({dr['pct']}%) — ស្ថាប័នចូលលក់")

        # Factor 3: Liquidity Sweep (Stop-Hunt)
        if dr["sweep"] == "BULL_SWEEP":
            buy_score += 15
            pro_factors_buy.insert(0, dr["sweep_km"])
        elif dr["sweep"] == "BEAR_SWEEP":
            sell_score += 15
            pro_factors_sell.insert(0, dr["sweep_km"])

        # Factor 4: Session VWAP & POC
        vwap_dist = price - vwap
        if 0.1 <= vwap_dist <= max(atr * 3.0, 6.0):
            buy_score += 10
            pro_factors_buy.append(f"លើ VWAP (${vwap:.2f}) • POC ${poc:.2f}")
        elif -max(atr * 3.0, 6.0) <= vwap_dist <= -0.1:
            sell_score += 10
            pro_factors_sell.append(f"ក្រោម VWAP (${vwap:.2f}) • POC ${poc:.2f}")

        # Factor 5: CVD & Hidden Divergence
        if cvd_div == "BULL_DIV" or cvd_pct >= 58:
            buy_score += 12
            pro_factors_buy.append(f"លំហូរលុយធំគាំទ្រទិញ (CVD {cvd_pct}%)")
        elif cvd_div == "BEAR_DIV" or cvd_pct <= 42:
            sell_score += 12
            pro_factors_sell.append(f"លំហូរលុយធំគាំទ្រលក់ (CVD {cvd_pct}%)")

        # Factor 6: Gold/Silver SMT Divergence
        if smt_sig == "BULL_SMT":
            buy_score += 12
            pro_factors_buy.insert(0, "XAU/XAG SMT ▲ — ប្រាក់មិនចុះតាមមាស")
        elif smt_sig == "BEAR_SMT":
            sell_score += 12
            pro_factors_sell.insert(0, "XAU/XAG SMT ▼ — ប្រាក់មិនឡើងតាមមាស")

        # Factor 7: MTF 15m/1H/4H Alignment
        mtf_al = str(mtf.get("aligned") or "MIXED")
        if mtf_al == "BUY":
            buy_score += 12
            pro_factors_buy.append("MTF 4 Timeframes ស្របទិស BUY")
        elif mtf_al == "SELL":
            sell_score += 12
            pro_factors_sell.append("MTF 4 Timeframes ស្របទិស SELL")

        # Factor 8: 10-Year V8 AI Brain Consensus
        if brain.get("side") == "BUY" and brain.get("can_trade"):
            buy_score += 12
        elif brain.get("side") == "SELL" and brain.get("can_trade"):
            sell_score += 12

        # ជ្រើសរើសទិសដៅឈ្នះ
        bias = "BUY" if buy_score >= sell_score else "SELL"
        raw_score = buy_score if bias == "BUY" else sell_score
        opp_score = sell_score if bias == "BUY" else buy_score
        edge_margin = raw_score - opp_score

        # 2) បង្កើត Pattern Signature និងផ្ទៀងផ្ទាត់ជាមួយខួរក្បាលរៀនពីកំហុស (Self-Learning Check)
        vwap_tag = "ABOVE_VWAP" if price >= vwap else "BELOW_VWAP"
        cvd_tag = "CVD_BUY" if cvd_pct >= 55 else ("CVD_SELL" if cvd_pct <= 45 else "CVD_FLAT")
        pattern_sig = f"{bias}|{dr['zone']}|{vwap_tag}|{cvd_tag}|{mtf_al}|{sess_tag}"
        mem_check = self.memory.inspect_pattern(pattern_sig)

        final_score = max(15, min(98, raw_score + mem_check["score_adj"]))

        # 3) គណនាចំណុចចូល និងចំណុចចេញ Zero-Contradiction (ហាមជាន់ Bearish/Bullish OB)
        danger_blocks = []
        if news.get("danger"):
            ev = news.get("danger_event") or {}
            danger_blocks.append(f"ព័ត៌មានក្រហម USD ({ev.get('title', 'Red News')}) — ហាមចូលដាច់ខាត")
        if brain.get("is_danger") and brain.get("reasons"):
            danger_blocks.append(brain["reasons"][0])
        if mem_check["blacklisted"]:
            danger_blocks.append(f"ខួរក្បាលប្លុកកំហុសចាស់៖ ទម្រង់ {dr['zone']}/{vwap_tag} ធ្លាប់ខាត {mem_check['losses']} ដង")
            self.memory.note_mistake_blocked()

        if bias == "BUY":
            avoid_zone = (
                (round(safe_float(bear_ob["bottom"]), 2), round(safe_float(bear_ob["top"]), 2))
                if bear_ob else (round(dr["high"] - atr * 0.5, 2), dr["high"])
            )
            # ចំណុច Entry BUY ត្រូវស្ថិតនៅក្រោមតំបន់ហាម (Bearish OB) ជានិច្ច (Zero-Contradiction)
            safe_cap = avoid_zone[0] - max(atr * 0.35, 0.8)
            ideal_buy = min(price, max(ema20, dr["ote_buy_hi"], vwap))
            if bull_ob and safe_float(bull_ob["top"]) <= safe_cap:
                ideal_buy = max(ideal_buy, safe_float(bull_ob["top"]))
            entry = round(min(ideal_buy, safe_cap), 2) if safe_cap > dr["low"] else round(min(price, ema20), 2)

            sl_anchor = safe_float(bull_ob["bottom"], entry - atr * 1.6) if bull_ob else (entry - atr * 1.6)
            sl = round(min(entry - max(atr * 1.35, 2.2), sl_anchor - atr * 0.35), 2)
            risk = max(entry - sl, 1.5)
            be_trigger = round(entry + risk * 1.0, 2)
            tp1 = round(entry + risk * 1.8, 2)
            tp2 = round(entry + risk * 2.8, 2)
            # ប្រសិនបើ TP1 លើស Bearish OB ពេក បន្ទាប TP1 មកមុខ Bearish OB ដើម្បីកុំឱ្យធ្លាក់វិញកាត់ចំណេញ
            if bear_ob and safe_float(bear_ob["bottom"]) > entry + risk * 1.2 and tp1 > safe_float(bear_ob["bottom"]):
                tp1 = round(safe_float(bear_ob["bottom"]) - 0.4, 2)
            trailing_stop = round(max(ema20 - atr * 0.45, price - atr * 1.15), 2)
            invalidation_5m = sl

            if abs(price - entry) <= max(atr * 0.45, 1.0) and price <= safe_cap:
                entry_mode = "NOW"
                entry_km = f"ចូលទិញ (BUY) ឥឡូវនៅ ${entry:.2f} (រង់ចាំទៀន 5m បិទបៃតង)"
            else:
                entry_mode = "WAIT_PULLBACK"
                entry_km = f"ចាំទិញ (BUY) ពេលថយមក ${entry:.2f} (កុំដេញទិញនៅកំពូល)"

            avoid_km = f"កុំទិញក្នុងតំបន់ ${avoid_zone[0]:.2f} – ${avoid_zone[1]:.2f} (តំបន់អ្នកលក់ Bearish OB)"
            profit_stop_km = f"បើទិញ៖ កាត់ចំណេញ 50% នៅ ${tp1:.2f} • រំកិល SL ស្មើដើមពេលដល់ ${be_trigger:.2f} • បើធ្លាក់បិទក្រោម ${trailing_stop:.2f} កាត់ចំណេញចេញភ្លាម"
            loss_stop_km = f"ឈប់ទិញដាច់ខាត បើទៀន 5m បិទក្រោម ${invalidation_5m:.2f} (កាត់ខាត SL)"
            reasons = pro_factors_buy[:4]

        else:
            avoid_zone = (
                (round(safe_float(bull_ob["bottom"]), 2), round(safe_float(bull_ob["top"]), 2))
                if bull_ob else (dr["low"], round(dr["low"] + atr * 0.5, 2))
            )
            # ចំណុច Entry SELL ត្រូវស្ថិតនៅលើតំបន់ហាម (Bullish OB) ជានិច្ច (Zero-Contradiction)
            safe_floor = avoid_zone[1] + max(atr * 0.35, 0.8)
            ideal_sell = max(price, min(ema20, dr["ote_sell_lo"], vwap))
            if bear_ob and safe_float(bear_ob["bottom"]) >= safe_floor:
                ideal_sell = min(ideal_sell, safe_float(bear_ob["bottom"]))
            entry = round(max(ideal_sell, safe_floor), 2) if safe_floor < dr["high"] else round(max(price, ema20), 2)

            sl_anchor = safe_float(bear_ob["top"], entry + atr * 1.6) if bear_ob else (entry + atr * 1.6)
            sl = round(max(entry + max(atr * 1.35, 2.2), sl_anchor + atr * 0.35), 2)
            risk = max(sl - entry, 1.5)
            be_trigger = round(entry - risk * 1.0, 2)
            tp1 = round(entry - risk * 1.8, 2)
            tp2 = round(entry - risk * 2.8, 2)
            if bull_ob and safe_float(bull_ob["top"]) < entry - risk * 1.2 and tp1 < safe_float(bull_ob["top"]):
                tp1 = round(safe_float(bull_ob["top"]) + 0.4, 2)
            trailing_stop = round(min(ema20 + atr * 0.45, price + atr * 1.15), 2)
            invalidation_5m = sl

            if abs(price - entry) <= max(atr * 0.45, 1.0) and price >= safe_floor:
                entry_mode = "NOW"
                entry_km = f"ចូលលក់ (SELL) ឥឡូវនៅ ${entry:.2f} (រង់ចាំទៀន 5m បិទក្រហម)"
            else:
                entry_mode = "WAIT_PULLBACK"
                entry_km = f"ចាំលក់ (SELL) ពេលងើបមក ${entry:.2f} (កុំដេញលក់នៅបាត)"

            avoid_km = f"កុំលក់ក្នុងតំបន់ ${avoid_zone[0]:.2f} – ${avoid_zone[1]:.2f} (តំបន់អ្នកទិញ Bullish OB)"
            profit_stop_km = f"បើលក់៖ កាត់ចំណេញ 50% នៅ ${tp1:.2f} • រំកិល SL ស្មើដើមពេលដល់ ${be_trigger:.2f} • បើឡើងបិទលើ ${trailing_stop:.2f} កាត់ចំណេញចេញភ្លាម"
            loss_stop_km = f"ឈប់លក់ដាច់ខាត បើទៀន 5m បិទលើ ${invalidation_5m:.2f} (កាត់ខាត SL)"
            reasons = pro_factors_sell[:4]

        rr_val = round(abs(tp1 - entry) / max(0.5, abs(entry - sl)), 2)

        # 4) សេចក្តីសម្រេចចុងក្រោយ (Verdict)
        can_execute = (
            len(danger_blocks) == 0
            and final_score >= 62
            and edge_margin >= 16
            and rr_val >= 1.35
        )

        if danger_blocks:
            verdict_code = "BLOCKED"
            grade = "RISK"
            verdict_km = "⛔ ហាមចូល (ការពារអន្ទាក់ & កំហុស)"
            sub_km = danger_blocks[0]
        elif can_execute and entry_mode == "NOW":
            verdict_code = f"READY_{bias}"
            grade = "A+" if final_score >= 78 else "A"
            verdict_km = f"🔥 ត្រៀមចូល {'ទិញ (BUY)' if bias == 'BUY' else 'លក់ (SELL)'} • {grade} ({final_score}%)"
            sub_km = (
                f"រង់ចាំ {candle_rem_sec} វិនាទីទៀតបិទទៀន 5m បញ្ជាក់ រួចចូលតាមផែនការ!"
                if candle_rem_sec <= 60
                else "សញ្ញាស្ថាប័នស្របគ្នា — ដាក់ SL ដាច់ខាត និងគ្រប់គ្រង Lot 0.5%–1%"
            )
        elif can_execute:
            verdict_code = f"LIMIT_{bias}"
            grade = "A" if final_score >= 70 else "B+"
            verdict_km = f"⏳ រង់ចាំ {'ទិញ (BUY)' if bias == 'BUY' else 'លក់ (SELL)'} Limit @ ${entry:.2f} ({grade})"
            sub_km = "តម្លៃមិនទាន់ដល់ចំណុចល្អទេ — ហាមដេញតម្លៃ រង់ចាំមកប៉ះតំបន់ចូលសិន"
        else:
            verdict_code = "WAIT"
            grade = "WATCH"
            verdict_km = f"⏳ រង់ចាំសញ្ញាច្បាស់ (ផ្អៀង {bias} • {final_score}%)"
            sub_km = "ពិន្ទុស្ថាប័នមិនទាន់គ្រប់ស្តង់ដាទេ — រក្សាដើមទុនចាំឱកាស A/A+"

        # 5) ឱ្យខួរក្បាលរៀន និងតាមដានលទ្ធផលពិតដោយស្វ័យប្រវត្តិ
        last_hi = safe_float(candles[-1].get("high"), price) if candles else price
        last_lo = safe_float(candles[-1].get("low"), price) if candles else price
        new_trade = (
            {
                "side": bias,
                "signature": pattern_sig,
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "be_trigger": be_trigger,
                "is_market": entry_mode == "NOW",
            }
            if can_execute
            else None
        )
        mem_stats = self.memory.track_and_learn(price, last_hi, last_lo, spread, new_trade)

        return {
            "name_km": "អ្នកជំនាញវិភាគកម្រិតខ្ពស់ (5m Institutional AI)",
            "interval": "5m",
            "price": round(price, 2),
            "spread": round(spread, 2),
            "candle_rem_sec": candle_rem_sec,
            "verdict_code": verdict_code,
            "grade": grade,
            "bias": bias,
            "can_execute": can_execute,
            "confidence": int(final_score),
            "verdict_km": verdict_km,
            "sub_km": sub_km,
            "plan": {
                "entry": entry,
                "entry_mode": entry_mode,
                "sl": sl,
                "be_trigger": be_trigger,
                "trailing_stop": trailing_stop,
                "tp1": tp1,
                "tp2": tp2,
                "rr": f"{rr_val} : 1",
                "invalidation_5m": invalidation_5m,
                "avoid_zone": [avoid_zone[0], avoid_zone[1]],
            },
            "guidance": {
                "trend_km": f"ផ្សារ 5m {'កំពុងឡើង (Uptrend)' if bias == 'BUY' else 'កំពុងចុះ (Downtrend)'} ${dr['low']:.2f} → ${dr['high']:.2f} • តំបន់ {dr['zone']} ({dr['pct']}%)",
                "entry_km": entry_km,
                "avoid_km": avoid_km,
                "profit_stop_km": profit_stop_km,
                "loss_stop_km": loss_stop_km,
            },
            "institutional": {
                "pd_zone": dr["zone"],
                "pd_pct": dr["pct"],
                "eq": dr["eq"],
                "sweep": dr["sweep"],
                "sweep_km": dr["sweep_km"],
                "vwap": round(vwap, 2),
                "poc": round(poc, 2),
                "cvd_pct": cvd_pct,
                "cvd_div": cvd_div,
                "smt": smt_sig,
                "squeeze": squeeze,
                "rsi": round(rsi, 1),
                "mtf_aligned": mtf_al,
            },
            "reasons": reasons,
            "pattern_memory": mem_check,
            "learning_brain": mem_stats,
        }


expert_analyst = AdvancedExpertAnalyst()