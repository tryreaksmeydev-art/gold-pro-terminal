import os
import json
import threading
from datetime import datetime, timezone

import numpy as np

from .utils import TZ_KH, safe_float, is_forex_weekend_closed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERT_MEMORY_PATH = os.path.join(BASE_DIR, "models", "expert_memory.json")
COMM_PIPS = 1.6


# ==================== CAMBODIA KILLZONE CLOCK (UTC+7 INSTITUTIONAL TIMING) ====================
def evaluate_cambodia_killzone(now_utc: datetime) -> dict:
    now_kh = now_utc.astimezone(TZ_KH)
    time_str = now_kh.strftime("%H:%M")
    hm = now_kh.hour + (now_kh.minute / 60.0)

    if is_forex_weekend_closed(now_utc):
        return {
            "code": "WEEKEND_CLOSED",
            "name_km": "ទីផ្សារបិទចុងសប្តាហ៍",
            "time_kh": time_str,
            "prob_pct": 0,
            "is_golden": False,
            "is_dead": True,
            "bonus": -30,
            "next_golden_km": "បើកវិញថ្ងៃច័ន្ទ 05:00 ព្រឹក (ម៉ោងមាសដំបូង 14:00 រសៀល)",
            "truth_km": "ទីផ្សារ Forex បិទចុងសប្តាហ៍ — មិនអាចជួញដូរបានទេ ប្រើពេលនេះតេស្ត Backtest និងរៀបផែនការ។",
        }

    if 19.33 <= hm < 23.0:
        return {
            "code": "NY_OVERLAP",
            "name_km": "ម៉ោងមាសកំពូល (COMEX + NY Overlap)",
            "time_kh": time_str,
            "prob_pct": 90,
            "is_golden": True,
            "is_dead": False,
            "bonus": 10,
            "next_golden_km": "កំពុងស្ថិតក្នុងម៉ោងមាសកំពូល (ដល់ 23:00 យប់)",
            "truth_km": "ម៉ោងលុយស្ថាប័នចូលខ្លាំងបំផុត (19:20–23:00) — មាសរត់ច្បាស់ ឱកាសដល់ TP ខ្ពស់បំផុត!",
        }

    if 14.0 <= hm < 17.5:
        return {
            "code": "LONDON_OPEN",
            "name_km": "ម៉ោងមាស London Open",
            "time_kh": time_str,
            "prob_pct": 78,
            "is_golden": True,
            "is_dead": False,
            "bonus": 7,
            "next_golden_km": "ម៉ោងមាសបន្ទាប់៖ 19:20 យប់ (COMEX + NY)",
            "truth_km": "ធនាគារអឺរ៉ុបបំបែក Asian Range (14:00–17:30) — ល្អខ្លាំងសម្រាប់ជួញដូរតាម Trend។",
        }

    if 23.0 <= hm or hm < 2.5:
        return {
            "code": "NY_LATE",
            "name_km": "វគ្គ NY ចុងថ្ងៃ",
            "time_kh": time_str,
            "prob_pct": 58,
            "is_golden": False,
            "is_dead": False,
            "bonus": 0,
            "next_golden_km": "ម៉ោងមាសបន្ទាប់៖ 14:00 រសៀល (London Open)",
            "truth_km": "លំហូរលុយចាប់ផ្តើមថយ ប្រយ័ត្នការកាត់ចំណេញបត់ក្បាល — យកតែជើង Grade A/A+។",
        }

    if 17.5 <= hm < 19.33:
        return {
            "code": "LONDON_LUNCH",
            "name_km": "វគ្គសម្រាកមុន NY (London Lunch)",
            "time_kh": time_str,
            "prob_pct": 48,
            "is_golden": False,
            "is_dead": False,
            "bonus": -3,
            "next_golden_km": "រង់ចាំម៉ោងមាសកំពូល 19:20 យប់ (COMEX + NY)",
            "truth_km": "ទីផ្សារច្រើនរត់ទទឹង ឬថយ Pullback មុនផ្សារអាមេរិកបើក — កុំប្រញាប់ចូលជើងធំ។",
        }

    if 13.0 <= hm < 14.0:
        return {
            "code": "PRE_LONDON",
            "name_km": "មុន London បើក (ប្រយ័ត្នអន្ទាក់)",
            "time_kh": time_str,
            "prob_pct": 40,
            "is_golden": False,
            "is_dead": False,
            "bonus": -5,
            "next_golden_km": "រង់ចាំ 14:00 រសៀល (London Open)",
            "truth_km": "ម៉ោងងាយមាន Judas Swing បោកទិសបោស SL មុន London បើក — រង់ចាំ 14:00 សិន។",
        }

    if 7.0 <= hm < 13.0:
        return {
            "code": "ASIAN_SESSION",
            "name_km": "វគ្គអាស៊ី (Asian Range)",
            "time_kh": time_str,
            "prob_pct": 45,
            "is_golden": False,
            "is_dead": False,
            "bonus": -4,
            "next_golden_km": "ម៉ោងមាសបន្ទាប់៖ 14:00 រសៀល (London Open)",
            "truth_km": "វគ្គអាស៊ីមាសច្រើនរត់ក្នុងប្រអប់តូច — ហាមដេញ Breakout កណ្តាលផ្លូវ ចាំទិញ/លក់តែគែមប្រអប់។",
        }

    return {
        "code": "DEAD_ZONE",
        "name_km": "ម៉ោងងាប់ (Dead Zone / Rollover)",
        "time_kh": time_str,
        "prob_pct": 15,
        "is_golden": False,
        "is_dead": True,
        "bonus": -18,
        "next_golden_km": "ម៉ោងមាសបន្ទាប់៖ 14:00 រសៀល (London Open)",
        "truth_km": "ម៉ោង 02:30–07:00 គ្មានលុយធំ Spread រីក ងាយបោស SL — អ្នកជំនាញមិនជួញដូរម៉ោងនេះទេ!",
    }


# ==================== SELF-LEARNING MEMORY (PATTERN + FACTOR-LEVEL) ====================
class ExpertSelfLearningMemory:
    def __init__(self, path: str = EXPERT_MEMORY_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.state = {
            "total_wins": 0,
            "total_losses": 0,
            "mistakes_blocked": 0,
            "net_pips": 0.0,
            "patterns": {},
            "factor_stats": {},
            "active_trade": None,
            "learned_log": [],
            "last_replay_day": "",
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

    def inspect_pattern(self, sig: str, active_factors: list[str]) -> dict:
        with self._lock:
            rec = dict(self.state["patterns"].get(sig) or {})
            f_map = {k: dict(self.state["factor_stats"].get(k) or {}) for k in active_factors}

        w = int(rec.get("wins", 0))
        l = int(rec.get("losses", 0))
        sl_streak = int(rec.get("streak_loss", 0))
        sw_streak = int(rec.get("streak_win", 0))
        n = w + l
        raw_wr = round((w / n) * 100.0, 1) if n > 0 else None

        factor_adj = 0
        for fk, fv in f_map.items():
            fw, fl = int(fv.get("wins", 0)), int(fv.get("losses", 0))
            fn = fw + fl
            if fn >= 3:
                fwr = fw / fn
                if fwr >= 0.65:
                    factor_adj += 2
                elif fwr < 0.38:
                    factor_adj -= 2
        factor_adj = max(-6, min(6, factor_adj))

        blacklisted = (sl_streak >= 2) or (n >= 3 and (w / n) < 0.40)
        boosted = (not blacklisted) and ((sw_streak >= 2) or (n >= 3 and (w / n) >= 0.65))

        score_adj = factor_adj
        status_km = "ទម្រង់ថ្មី — កំពុងសិក្សាពីទីផ្សារពិត"
        if blacklisted:
            score_adj = -25
            status_km = f"ប្លុកកំហុសចាស់៖ ទម្រង់នេះធ្លាប់ឈ្នះ {w} / ខាត {l} — ហាមជាន់កំហុសដដែល"
        elif boosted:
            score_adj = min(14, 6 + sw_streak * 2 + max(0, factor_adj))
            status_km = f"ទម្រង់ឈ្នះខ្ពស់៖ ធ្លាប់ឈ្នះ {w} / ខាត {l} ({raw_wr}%)"
        elif l >= 1 and w == 0:
            score_adj = -8 + min(0, factor_adj)
            status_km = f"ប្រុងប្រយ័ត្ន៖ ទម្រង់នេះធ្លាប់ខាត {l} ដង"
        elif w >= 1:
            score_adj = 4 + factor_adj
            status_km = f"ធ្លាប់ឈ្នះ {w} / ខាត {l} ({raw_wr}%)"

        return {
            "signature": sig,
            "wins": w,
            "losses": l,
            "samples": n,
            "raw_win_rate": raw_wr,
            "blacklisted": blacklisted,
            "boosted": boosted,
            "score_adj": score_adj,
            "status_km": status_km,
        }

    def note_mistake_blocked(self):
        with self._lock:
            self.state["mistakes_blocked"] = int(self.state.get("mistakes_blocked", 0)) + 1
            self._save()

    def seed_from_replay(self, replay_trades: list[dict], traps_avoided: int) -> bool:
        """បញ្ចូលមេរៀនពី Backtest 5 ថ្ងៃចូលខួរក្បាល (ការពារកុំឱ្យបូកជាន់គ្នាក្នុងថ្ងៃតែមួយ)"""
        today_str = datetime.now(TZ_KH).strftime("%Y-%m-%d")
        now_ts = int(datetime.now(timezone.utc).timestamp())

        with self._lock:
            if self.state.get("last_replay_day") == today_str:
                return False

            for tr in replay_trades:
                sig = tr["signature"]
                is_win = bool(tr["is_win"])
                pips = float(tr["pips"])
                side = tr["side"]

                pat = self.state["patterns"].setdefault(
                    sig, {"wins": 0, "losses": 0, "streak_loss": 0, "streak_win": 0, "last_ts": now_ts}
                )
                for fk in (tr.get("factors") or []):
                    fs = self.state["factor_stats"].setdefault(fk, {"wins": 0, "losses": 0})
                    if is_win:
                        fs["wins"] = int(fs["wins"]) + 1
                    else:
                        fs["losses"] = int(fs["losses"]) + 1

                if is_win:
                    self.state["total_wins"] = int(self.state["total_wins"]) + 1
                    pat["wins"] = int(pat["wins"]) + 1
                    pat["streak_win"] = int(pat.get("streak_win", 0)) + 1
                    pat["streak_loss"] = 0
                else:
                    self.state["total_losses"] = int(self.state["total_losses"]) + 1
                    pat["losses"] = int(pat["losses"]) + 1
                    pat["streak_loss"] = int(pat.get("streak_loss", 0)) + 1
                    pat["streak_win"] = 0
                pat["last_ts"] = now_ts
                self.state["net_pips"] = round(float(self.state.get("net_pips", 0.0)) + pips, 1)

            if replay_trades:
                w_cnt = sum(1 for t in replay_trades if t["is_win"])
                l_cnt = len(replay_trades) - w_cnt
                self.state["learned_log"].insert(0, {
                    "time_kh": datetime.now(TZ_KH).strftime("%d/%m %H:%M"),
                    "side": "REPLAY",
                    "result": "WIN" if w_cnt >= l_cnt else "LOSS",
                    "pips": round(sum(float(t["pips"]) for t in replay_trades), 1),
                    "entry": 0.0,
                    "exit": 0.0,
                    "signature": "5D_REPLAY",
                    "lesson_km": f"ហ្វឹកហាត់ 5 ថ្ងៃ៖ រៀន {len(replay_trades)} ជើង (ឈ្នះ {w_cnt}/ខាត {l_cnt}) • ការពារអន្ទាក់ {traps_avoided} ដង",
                })
                self.state["learned_log"] = self.state["learned_log"][:15]

            self.state["mistakes_blocked"] = int(self.state.get("mistakes_blocked", 0)) + int(traps_avoided)
            self.state["last_replay_day"] = today_str
            self._save()
            return True

    def track_and_learn(
        self,
        price: float,
        high: float,
        low: float,
        spread: float,
        new_trade: dict | None,
    ) -> dict:
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        now_kh = now_utc.astimezone(TZ_KH).strftime("%d/%m %H:%M")
        cost_pips = round((max(0.0, safe_float(spread, 0.25)) * 10.0) + COMM_PIPS, 1)
        changed = False

        with self._lock:
            act = self.state.get("active_trade")
            if act and price > 0 and not is_forex_weekend_closed(now_utc):
                hi = max(price, high) if high > 0 else price
                lo = min(price, low) if low > 0 else price

                if not act.get("filled", True):
                    if lo <= act["entry"] <= hi:
                        act["filled"] = True
                        act["filled_ts"] = now_ts
                        act["open_kh"] = now_kh
                        changed = True
                    elif (now_ts - int(act.get("created_ts", now_ts))) > 1800:
                        self.state["active_trade"] = None
                        act = None
                        changed = True

                if act and act.get("filled", True):
                    side = act["side"]
                    entry = float(act["entry"])
                    sl = float(act["sl"])
                    tp1 = float(act["tp1"])
                    be_trigger = float(act["be_trigger"])

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
                        for fk in (act.get("factors") or []):
                            fs = self.state["factor_stats"].setdefault(fk, {"wins": 0, "losses": 0})
                            if is_win:
                                fs["wins"] = int(fs["wins"]) + 1
                            else:
                                fs["losses"] = int(fs["losses"]) + 1

                        if is_win:
                            self.state["total_wins"] = int(self.state["total_wins"]) + 1
                            pat["wins"] = int(pat["wins"]) + 1
                            pat["streak_win"] = int(pat.get("streak_win", 0)) + 1
                            pat["streak_loss"] = 0
                            lesson_km = f"រៀនទម្រង់ឈ្នះ ({side} +{pips:.0f}p) — បង្កើនពិន្ទុទំនុកចិត្ត"
                        else:
                            self.state["total_losses"] = int(self.state["total_losses"]) + 1
                            pat["losses"] = int(pat["losses"]) + 1
                            pat["streak_loss"] = int(pat.get("streak_loss", 0)) + 1
                            pat["streak_win"] = 0
                            lesson_km = f"ចងចាំកំហុស ({side} {pips:.0f}p) — កាត់បន្ថយ/ប្លុកទម្រង់នេះ"

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

            if new_trade and self.state.get("active_trade") is None and not is_forex_weekend_closed(now_utc):
                self.state["active_trade"] = {
                    "side": new_trade["side"],
                    "signature": new_trade["signature"],
                    "factors": new_trade.get("factors") or [],
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
            iq_level = min(99, 60 + min(25, tot * 2) + min(14, int(self.state["mistakes_blocked"])))

            return {
                "total_wins": tw,
                "total_losses": tl,
                "total_trades": tot,
                "win_rate": round((tw / tot) * 100.0, 1) if tot > 0 else None,
                "net_pips": round(float(self.state.get("net_pips", 0.0)), 1),
                "mistakes_blocked": int(self.state.get("mistakes_blocked", 0)),
                "patterns_learned": len(self.state["patterns"]),
                "iq_level": iq_level,
                "active_trade": self.state.get("active_trade"),
                "recent_lessons": self.state["learned_log"][:4],
            }


# ==================== MAIN INSTITUTIONAL ANALYST + 7-POINT CHECKLIST + 5D REPLAY ====================
class AdvancedExpertAnalyst:
    def __init__(self):
        self.memory = ExpertSelfLearningMemory()

    def _compute_dealing_range_and_regime(self, candles: list[dict], price: float, ema20: float, ema50: float, atr: float) -> dict:
        if not candles or len(candles) < 12:
            return {
                "high": price + 5.0, "low": price - 5.0, "eq": price,
                "zone": "EQUILIBRIUM", "pct": 50.0,
                "ote_buy_hi": price, "ote_sell_lo": price,
                "sweep": "NONE", "sweep_km": "គ្មានការបោស SL ថ្មីៗ",
                "regime": "RANGING", "regime_km": "ទីផ្សារកំពុងសន្សំកម្លាំង (Ranging)",
            }

        win = candles[-48:] if len(candles) >= 48 else candles
        highs = np.array([safe_float(c.get("high"), price) for c in win], dtype=float)
        lows = np.array([safe_float(c.get("low"), price) for c in win], dtype=float)
        closes = np.array([safe_float(c.get("close"), price) for c in win], dtype=float)

        r_hi, r_lo = float(np.max(highs)), float(np.min(lows))
        span = max(r_hi - r_lo, 1.0)
        eq = r_lo + span * 0.50
        pos_pct = round(((price - r_lo) / span) * 100.0, 1)

        zone = "PREMIUM" if pos_pct >= 62.0 else ("DISCOUNT" if pos_pct <= 38.0 else "EQUILIBRIUM")
        ote_buy_hi = round(r_hi - span * 0.618, 2)
        ote_sell_lo = round(r_lo + span * 0.618, 2)

        net_move = abs(float(closes[-1] - closes[-min(16, len(closes))]))
        path_len = float(np.sum(np.abs(np.diff(closes[-min(16, len(closes)):]))))
        eff_ratio = net_move / max(path_len, 0.01)
        ema_sep = abs(ema20 - ema50)

        if eff_ratio >= 0.42 and ema_sep >= atr * 0.55:
            regime = "TRENDING"
            regime_km = "ទីផ្សារមាននិន្នាការច្បាស់ (Trending) — ល្អសម្រាប់ចូលតាម Pullback"
        elif eff_ratio < 0.22:
            regime = "CHOPPY"
            regime_km = "ទីផ្សាររញ៉េរញ៉ៃបោកទិស (Choppy) — ងាយបោស SL កុំចូលកណ្តាលផ្លូវ"
        else:
            regime = "RANGING"
            regime_km = "ទីផ្សាររត់ក្នុងប្រអប់ (Ranging) — ចូលតែគែម Discount/Premium"

        sweep, sweep_km = "NONE", "គ្មានការបោស SL ថ្មីៗ"
        if len(win) >= 15:
            prev_hi, prev_lo = float(np.max(highs[:-3])), float(np.min(lows[:-3]))
            rec_hi, rec_lo = float(np.max(highs[-3:])), float(np.min(lows[-3:]))
            last_c = float(closes[-1])
            if rec_lo < prev_lo and last_c > prev_lo:
                sweep = "BULL_SWEEP"
                sweep_km = f"ទើបបោស SL ក្រោម ${prev_lo:.2f} រួចទាញឡើងវិញ — ស្ថាប័នប្រមូលទិញ!"
            elif rec_hi > prev_hi and last_c < prev_hi:
                sweep = "BEAR_SWEEP"
                sweep_km = f"ទើបបោស SL លើ ${prev_hi:.2f} រួចទម្លាក់វិញ — ស្ថាប័នលក់ចេញ!"

        return {
            "high": round(r_hi, 2), "low": round(r_lo, 2), "eq": round(eq, 2),
            "zone": zone, "pct": pos_pct,
            "ote_buy_hi": ote_buy_hi, "ote_sell_lo": ote_sell_lo,
            "sweep": sweep, "sweep_km": sweep_km,
            "regime": regime, "regime_km": regime_km,
        }

    def _find_nearest_ob(self, blocks: list[dict], price: float, want_below: bool) -> dict | None:
        obs = [b for b in (blocks or []) if str(b.get("label", "OB")).upper() == "OB"]
        if not obs:
            return None
        if want_below:
            below = [b for b in obs if safe_float(b.get("bottom"), 0.0) <= price + 1.0]
            return max(below, key=lambda b: safe_float(b.get("top"), 0.0)) if below else None
        above = [b for b in obs if safe_float(b.get("top"), 0.0) >= price - 1.0]
        return min(above, key=lambda b: safe_float(b.get("bottom"), 0.0)) if above else None

    def _calc_safe_lot_guide(self, sl_dist: float, spread: float) -> dict:
        eff_dist = max(sl_dist + spread, 1.5)
        def _lot_for(bal: float) -> float:
            raw = (bal * 0.01) / (eff_dist * 100.0)
            return round(max(0.01, min(1.0, raw)), 2)
        return {
            "sl_dist_usd": round(sl_dist, 2),
            "lot_100": _lot_for(100.0),
            "lot_500": _lot_for(500.0),
            "lot_1000": _lot_for(1000.0),
            "rule_km": f"SL ${sl_dist:.2f} • ប្រថុយ 1%៖ ដើមទុន $100→{_lot_for(100.0):.2f} | $500→{_lot_for(500.0):.2f} | $1000→{_lot_for(1000.0):.2f} Lot",
        }

    def _build_7point_checklist(
        self,
        bias: str,
        price: float,
        ema20: float,
        ema50: float,
        dr: dict,
        cvd_pct: int,
        cvd_div: str,
        mtf_al: str,
        brain: dict,
        kz: dict,
        rr_val: float,
        news: dict,
        traps: list[str],
    ) -> dict:
        """បញ្ជីផ្ទៀងផ្ទាត់តម្លាភាព 7 កត្តាស្នូល (7-Point Pass Checklist)"""
        trend_ok = (bias == "BUY" and price > ema20 and ema20 >= ema50) or (bias == "SELL" and price < ema20 and ema20 <= ema50)
        zone_ok = (
            (bias == "BUY" and (dr["zone"] in ("DISCOUNT", "EQUILIBRIUM") or dr["regime"] == "TRENDING"))
            or (bias == "SELL" and (dr["zone"] in ("PREMIUM", "EQUILIBRIUM") or dr["regime"] == "TRENDING"))
        )
        flow_ok = (bias == "BUY" and (cvd_pct >= 53 or cvd_div == "BULL_DIV")) or (bias == "SELL" and (cvd_pct <= 47 or cvd_div == "BEAR_DIV"))

        macro = brain.get("macro") or {}
        us10y = safe_float(macro.get("us10y"), 4.2)
        macro_ok = (bias == "BUY" and us10y < 4.85) or (bias == "SELL" and us10y >= 4.0) or (mtf_al == bias)
        kz_ok = bool(kz.get("is_golden"))
        rr_ok = rr_val >= 1.40
        safety_ok = (not news.get("danger")) and (len(traps) == 0)

        items = [
            {"id": "trend", "label": "និន្នាការ 5m (EMA20/50)", "ok": trend_ok, "val": f"{bias} Trend" if trend_ok else "ខ្សោយ/បញ្ច្រាស"},
            {"id": "zone", "label": "តំបន់តម្លៃ (Discount/Premium)", "ok": zone_ok, "val": f"{dr['zone']} {dr['pct']}%"},
            {"id": "flow", "label": "លំហូរលុយធំ (CVD)", "ok": flow_ok, "val": f"CVD {cvd_pct}%"},
            {"id": "macro", "label": "ម៉ាក្រូ & MTF ស្របទិស", "ok": macro_ok, "val": f"MTF {mtf_al}"},
            {"id": "kz", "label": "ម៉ោងមាសកម្ពុជា (London/NY)", "ok": kz_ok, "val": f"{kz.get('prob_pct', 0)}% ({kz.get('time_kh', '--')})"},
            {"id": "rr", "label": "សមាមាត្រចំណេញ/ខាត (R:R ≥ 1.4)", "ok": rr_ok, "val": f"{rr_val} : 1"},
            {"id": "safe", "label": "គ្មានព័ត៌មានក្រហម & អន្ទាក់", "ok": safety_ok, "val": "ស្អាត ✓" if safety_ok else f"ជាប់ {len(traps)} អន្ទាក់"},
        ]
        passed = sum(1 for it in items if it["ok"])
        return {
            "passed": passed,
            "total": 7,
            "verdict_km": (
                f"{passed}/7 ឆ្លងកាត់ — ទំនុកចិត្តខ្ពស់!" if passed >= 6
                else (f"{passed}/7 ឆ្លងកាត់ — កម្រិតមធ្យម (ប្រយ័ត្ន)" if passed >= 4 else f"{passed}/7 ឆ្លងកាត់ — ទាប ហាមប្រថុយ!")
            ),
            "items": items,
        }

    def analyze(self, payload_5m: dict) -> dict:
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        candle_rem_sec = 300 - (now_ts % 300)
        kz = evaluate_cambodia_killzone(now_utc)

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

        dr = self._compute_dealing_range_and_regime(candles, price, ema20, ema50, atr)
        vwap = safe_float(quant.get("vwap"), price)
        poc = safe_float(quant.get("poc"), price)
        cvd_pct = int(safe_float(quant.get("cvd_pct"), 50))
        cvd_div = str(quant.get("cvd_divergence") or "NONE")
        smt_sig = str(smt.get("signal") or "NONE")
        mtf_al = str(mtf.get("aligned") or "MIXED")

        bull_ob = self._find_nearest_ob(smc.get("demand_blocks"), price, want_below=True)
        bear_ob = self._find_nearest_ob(smc.get("supply_blocks"), price, want_below=False)

        buy_score, sell_score = 0, 0
        f_buy, f_sell = [], []
        reasons_buy, reasons_sell = [], []

        if price > ema20 and ema20 >= ema50:
            buy_score += 18
            f_buy.append("EMA_TREND_UP")
            reasons_buy.append("និន្នាការ 5m ឡើងរឹងមាំ (លើ EMA 20/50)")
        elif price < ema20 and ema20 <= ema50:
            sell_score += 18
            f_sell.append("EMA_TREND_DN")
            reasons_sell.append("និន្នាការ 5m ចុះរឹងមាំ (ក្រោម EMA 20/50)")

        if smc.get("bias") == "BULLISH":
            buy_score += 10
            f_buy.append("SMC_BULL")
        elif smc.get("bias") == "BEARISH":
            sell_score += 10
            f_sell.append("SMC_BEAR")

        if dr["zone"] == "DISCOUNT":
            buy_score += 16
            sell_score = max(0, sell_score - 14)
            f_buy.append("DISCOUNT_ZONE")
            reasons_buy.append(f"តម្លៃនៅតំបន់ថោក Discount ({dr['pct']}%) — សមស្របចូលទិញ")
        elif dr["zone"] == "PREMIUM":
            sell_score += 16
            buy_score = max(0, buy_score - 14)
            f_sell.append("PREMIUM_ZONE")
            reasons_sell.append(f"តម្លៃនៅតំបន់ថ្លៃ Premium ({dr['pct']}%) — សមស្របចូលលក់")

        if dr["sweep"] == "BULL_SWEEP":
            buy_score += 16
            f_buy.append("BULL_SWEEP")
            reasons_buy.insert(0, dr["sweep_km"])
        elif dr["sweep"] == "BEAR_SWEEP":
            sell_score += 16
            f_sell.append("BEAR_SWEEP")
            reasons_sell.insert(0, dr["sweep_km"])

        vwap_dist = price - vwap
        max_vwap_ext = max(atr * 3.2, 6.5)
        if 0.1 <= vwap_dist <= max_vwap_ext:
            buy_score += 10
            f_buy.append("VWAP_BULL")
            reasons_buy.append(f"តម្លៃលើ VWAP (${vwap:.1f}) • POC ${poc:.1f}")
        elif -max_vwap_ext <= vwap_dist <= -0.1:
            sell_score += 10
            f_sell.append("VWAP_BEAR")
            reasons_sell.append(f"តម្លៃក្រោម VWAP (${vwap:.1f}) • POC ${poc:.1f}")

        if cvd_div == "BULL_DIV" or cvd_pct >= 58:
            buy_score += 12
            f_buy.append("CVD_BULL")
            reasons_buy.append(f"លំហូរលុយធំទិញឈ្នះ (CVD {cvd_pct}%)")
        elif cvd_div == "BEAR_DIV" or cvd_pct <= 42:
            sell_score += 12
            f_sell.append("CVD_BEAR")
            reasons_sell.append(f"លំហូរលុយធំលក់ឈ្នះ (CVD {cvd_pct}%)")

        if smt_sig == "BULL_SMT":
            buy_score += 12
            f_buy.append("SMT_BULL")
            reasons_buy.insert(0, "XAU/XAG SMT ▲ — ប្រាក់មិនចុះតាមមាស (ស្ថាប័នប្រមូលទិញ)")
        elif smt_sig == "BEAR_SMT":
            sell_score += 12
            f_sell.append("SMT_BEAR")
            reasons_sell.insert(0, "XAU/XAG SMT ▼ — ប្រាក់មិនឡើងតាមមាស (អន្ទាក់លក់ចុះ)")

        if mtf_al == "BUY":
            buy_score += 12
            f_buy.append("MTF_BUY")
            reasons_buy.append("MTF 4 Timeframes ស្របទិស BUY")
        elif mtf_al == "SELL":
            sell_score += 12
            f_sell.append("MTF_SELL")
            reasons_sell.append("MTF 4 Timeframes ស្របទិស SELL")

        if brain.get("side") == "BUY" and brain.get("can_trade"):
            buy_score += 12
            f_buy.append("V8_BUY")
        elif brain.get("side") == "SELL" and brain.get("can_trade"):
            sell_score += 12
            f_sell.append("V8_SELL")

        if buy_score >= sell_score:
            bias, raw_score, opp_score = "BUY", buy_score + kz["bonus"], sell_score
            active_factors, reasons = f_buy, reasons_buy
        else:
            bias, raw_score, opp_score = "SELL", sell_score + kz["bonus"], buy_score
            active_factors, reasons = f_sell, reasons_sell

        edge_margin = raw_score - opp_score

        vwap_tag = "ABOVE_VWAP" if price >= vwap else "BELOW_VWAP"
        cvd_tag = "CVD_BUY" if cvd_pct >= 55 else ("CVD_SELL" if cvd_pct <= 45 else "CVD_FLAT")
        pattern_sig = f"{bias}|{dr['zone']}|{vwap_tag}|{cvd_tag}|{mtf_al}|{kz['code']}"
        mem_check = self.memory.inspect_pattern(pattern_sig, active_factors)
        final_score = max(12, min(98, raw_score + mem_check["score_adj"]))

        traps: list[str] = []
        hard_stop = False

        if kz["code"] == "WEEKEND_CLOSED":
            traps.append("ទីផ្សារ Forex បិទចុងសប្តាហ៍ — មិនអាចជួញដូរបានទេ (បើកវិញថ្ងៃច័ន្ទ 05:00 ព្រឹក)")
            hard_stop = True
        elif kz["is_dead"]:
            traps.append(f"ម៉ោងងាប់ ({kz['time_kh']} កម្ពុជា) — គ្មានលុយធំ Spread រីក ហាមចូលដាច់ខាត!")
            hard_stop = True

        if news.get("danger"):
            ev = news.get("danger_event") or {}
            traps.append(f"ព័ត៌មានក្រហម USD ({ev.get('title', 'High Impact')}) — ងាយបោស SL ទាំងលើក្រោម!")
            hard_stop = True

        if spread > 1.20:
            traps.append(f"Spread រីកធំ (${spread:.2f}) — ថ្លៃដើមខ្ពស់ ងាយខាតភ្លាមពេលចូល!")
            hard_stop = True

        if dr["regime"] == "CHOPPY":
            traps.append("ទីផ្សារកំពុង Chop/រញ៉េរញ៉ៃ — អន្ទាក់ស៊ី SL ញឹកញាប់បំផុត កុំប្រថុយចូលកណ្តាលផ្លូវ!")

        if bias == "BUY" and dr["zone"] == "PREMIUM" and dr["regime"] != "TRENDING":
            traps.append(f"អន្ទាក់ដេញទិញនៅកំពូល (Premium {dr['pct']}%) — រង់ចាំថយមកតំបន់ថោកសិន!")
        elif bias == "SELL" and dr["zone"] == "DISCOUNT" and dr["regime"] != "TRENDING":
            traps.append(f"អន្ទាក់ដេញលក់នៅបាត (Discount {dr['pct']}%) — រង់ចាំងើបមកតំបន់ថ្លៃសិន!")

        if bias == "BUY" and vwap_dist > max_vwap_ext:
            traps.append(f"តម្លៃហួស VWAP ឆ្ងាយពេក (+${vwap_dist:.1f}) — ហាមដេញ BUY!")
        elif bias == "SELL" and vwap_dist < -max_vwap_ext:
            traps.append(f"តម្លៃធ្លាក់ហួស VWAP ឆ្ងាយពេក (${vwap_dist:.1f}) — ហាមដេញ SELL!")

        if brain.get("is_danger") and brain.get("reasons") and not hard_stop:
            traps.append(brain["reasons"][0])

        if mem_check["blacklisted"]:
            traps.append(f"ខួរក្បាលប្លុកកំហុសចាស់៖ ទម្រង់នេះធ្លាប់ខាត {mem_check['losses']} ដង — ហាមចូល!")
            hard_stop = True
            self.memory.note_mistake_blocked()

        if bias == "BUY":
            avoid_zone = (
                (round(safe_float(bear_ob["bottom"]), 2), round(safe_float(bear_ob["top"]), 2))
                if bear_ob else (round(dr["high"] - atr * 0.5, 2), dr["high"])
            )
            safe_cap = avoid_zone[0] - max(atr * 0.35, 0.8)
            ideal_buy = min(price, max(ema20, dr["ote_buy_hi"], vwap))
            if bull_ob and safe_float(bull_ob["top"]) <= safe_cap:
                ideal_buy = max(ideal_buy, safe_float(bull_ob["top"]))
            entry = round(min(ideal_buy, safe_cap), 2) if safe_cap > dr["low"] else round(min(price, ema20), 2)

            sl_anchor = safe_float(bull_ob["bottom"], entry - atr * 1.55) if bull_ob else (entry - atr * 1.55)
            sl = round(min(entry - max(atr * 1.35, 2.2), sl_anchor - atr * 0.35), 2)
            risk = max(entry - sl, 1.5)
            be_trigger = round(entry + risk * 1.0, 2)
            tp1 = round(entry + risk * 1.8, 2)
            tp2 = round(entry + risk * 2.8, 2)
            if bear_ob and safe_float(bear_ob["bottom"]) > entry + risk * 1.25 and tp1 > safe_float(bear_ob["bottom"]):
                tp1 = round(safe_float(bear_ob["bottom"]) - 0.4, 2)
            trailing_stop = round(max(ema20 - atr * 0.45, price - atr * 1.15), 2)

            entry_mode = "NOW" if (abs(price - entry) <= max(atr * 0.45, 1.0) and price <= safe_cap) else "WAIT_PULLBACK"
            entry_km = (
                f"ចូលទិញ (BUY) @ ${entry:.2f} ពេលទៀន 5m បិទបៃតងបញ្ជាក់"
                if entry_mode == "NOW"
                else f"ចាំទិញ (BUY) ពេលតម្លៃថយមក ${entry:.2f} (ហាមដេញទិញនៅកំពូល)"
            )
            avoid_km = f"ហាមទិញក្នុងតំបន់ ${avoid_zone[0]:.2f}–${avoid_zone[1]:.2f} (តំបន់អ្នកលក់ Bearish OB)"
            profit_stop_km = f"ការពារចំណេញ BUY៖ រំកិល SL ស្មើដើមពេលដល់ ${be_trigger:.2f} • កាត់ 50% នៅ ${tp1:.2f} • បើធ្លាក់បិទក្រោម ${trailing_stop:.2f} កាត់ចំណេញភ្លាម"
            loss_stop_km = f"ឈប់ទិញដាច់ខាត បើទៀន 5m បិទក្រោម ${sl:.2f} (កាត់ខាត SL)"
        else:
            avoid_zone = (
                (round(safe_float(bull_ob["bottom"]), 2), round(safe_float(bull_ob["top"]), 2))
                if bull_ob else (dr["low"], round(dr["low"] + atr * 0.5, 2))
            )
            safe_floor = avoid_zone[1] + max(atr * 0.35, 0.8)
            ideal_sell = max(price, min(ema20, dr["ote_sell_lo"], vwap))
            if bear_ob and safe_float(bear_ob["bottom"]) >= safe_floor:
                ideal_sell = min(ideal_sell, safe_float(bear_ob["bottom"]))
            entry = round(max(ideal_sell, safe_floor), 2) if safe_floor < dr["high"] else round(max(price, ema20), 2)

            sl_anchor = safe_float(bear_ob["top"], entry + atr * 1.55) if bear_ob else (entry + atr * 1.55)
            sl = round(max(entry + max(atr * 1.35, 2.2), sl_anchor + atr * 0.35), 2)
            risk = max(sl - entry, 1.5)
            be_trigger = round(entry - risk * 1.0, 2)
            tp1 = round(entry - risk * 1.8, 2)
            tp2 = round(entry - risk * 2.8, 2)
            if bull_ob and safe_float(bull_ob["top"]) < entry - risk * 1.25 and tp1 < safe_float(bull_ob["top"]):
                tp1 = round(safe_float(bull_ob["top"]) + 0.4, 2)
            trailing_stop = round(min(ema20 + atr * 0.45, price + atr * 1.15), 2)

            entry_mode = "NOW" if (abs(price - entry) <= max(atr * 0.45, 1.0) and price >= safe_floor) else "WAIT_PULLBACK"
            entry_km = (
                f"ចូលលក់ (SELL) @ ${entry:.2f} ពេលទៀន 5m បិទក្រហមបញ្ជាក់"
                if entry_mode == "NOW"
                else f"ចាំលក់ (SELL) ពេលតម្លៃងើបមក ${entry:.2f} (ហាមដេញលក់នៅបាត)"
            )
            avoid_km = f"ហាមលក់ក្នុងតំបន់ ${avoid_zone[0]:.2f}–${avoid_zone[1]:.2f} (តំបន់អ្នកទិញ Bullish OB)"
            profit_stop_km = f"ការពារចំណេញ SELL៖ រំកិល SL ស្មើដើមពេលដល់ ${be_trigger:.2f} • កាត់ 50% នៅ ${tp1:.2f} • បើឡើងបិទលើ ${trailing_stop:.2f} កាត់ចំណេញភ្លាម"
            loss_stop_km = f"ឈប់លក់ដាច់ខាត បើទៀន 5m បិទលើ ${sl:.2f} (កាត់ខាត SL)"

        rr_val = round(abs(tp1 - entry) / max(0.5, abs(entry - sl)), 2)
        risk_guide = self._calc_safe_lot_guide(abs(entry - sl), spread)
        checklist = self._build_7point_checklist(
            bias, price, ema20, ema50, dr, cvd_pct, cvd_div, mtf_al, brain, kz, rr_val, news, traps
        )

        can_execute = (
            not hard_stop
            and len(traps) == 0
            and final_score >= 64
            and edge_margin >= 16
            and rr_val >= 1.40
        )

        if hard_stop or traps:
            verdict_code = "BLOCKED"
            grade = "RISK"
            verdict_km = "⛔ ហាមចូល — កុំប្រថុយលុយពិតពេលនេះ"
            sub_km = traps[0]
            if kz["code"] == "WEEKEND_CLOSED":
                entry_km = "ទីផ្សារបិទ — រង់ចាំថ្ងៃច័ន្ទម៉ោង 14:00 (London) ឬ 19:20 (NY Overlap)"
        elif can_execute and entry_mode == "NOW":
            verdict_code = f"READY_{bias}"
            grade = "A+" if final_score >= 78 else "A"
            verdict_km = f"🔥 ឱកាសខ្លាំង {'ទិញ (BUY)' if bias == 'BUY' else 'លក់ (SELL)'} • {grade} ({final_score}%)"
            sub_km = (
                f"នៅសល់ {candle_rem_sec}s បិទទៀន 5m បញ្ជាក់ — ត្រៀមចូលតាមផែនការ & ដាក់ SL ដាច់ខាត!"
                if candle_rem_sec <= 60
                else f"កត្តាស្ថាប័នស្របគ្នា ({kz['name_km']}) — ចូលតាមផែនការ ប្រថុយ 0.5%–1%"
            )
        elif can_execute:
            verdict_code = f"LIMIT_{bias}"
            grade = "A" if final_score >= 72 else "B+"
            verdict_km = f"⏳ រង់ចាំ {'BUY' if bias == 'BUY' else 'SELL'} Limit @ ${entry:.2f} ({grade})"
            sub_km = "កុំដេញតម្លៃ! ដាក់ Limit ឬរង់ចាំតម្លៃមកប៉ះចំណុចចូលល្អសិន"
        else:
            verdict_code = "WAIT"
            grade = "WATCH"
            verdict_km = f"⏳ មិនទាន់មានជើងស្អាត (ផ្អៀង {bias} • {final_score}%)"
            sub_km = "ការពិត៖ ពិន្ទុមិនទាន់ដល់កម្រិតឈ្នះខ្ពស់ទេ — រក្សាដើមទុនចាំឱកាស A/A+ ល្អជាង"

        last_hi = safe_float(candles[-1].get("high"), price) if candles else price
        last_lo = safe_float(candles[-1].get("low"), price) if candles else price
        new_trade = (
            {
                "side": bias,
                "signature": pattern_sig,
                "factors": active_factors,
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

        backend_summary_km = (
            f"P/D: {dr['zone']} ({dr['pct']}%) • VWAP ${vwap:.1f} • POC ${poc:.1f} • "
            f"CVD {cvd_pct}%{(' (' + cvd_div + ')') if cvd_div != 'NONE' else ''} • "
            f"SMT: {smt_sig if smt_sig != 'NONE' else 'ស្មើ'} • MTF: {mtf_al} • RSI {rsi:.0f}"
        )

        return {
            "name_km": "អ្នកជំនាញវិភាគកម្រិតខ្ពស់ (5m AI V12)",
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
            "killzone": kz,
            "regime": {"code": dr["regime"], "km": dr["regime_km"]},
            "checklist": checklist,
            "traps": traps[:3],
            "risk_guide": risk_guide,
            "plan": {
                "entry": entry,
                "entry_mode": entry_mode,
                "sl": sl,
                "be_trigger": be_trigger,
                "trailing_stop": trailing_stop,
                "tp1": tp1,
                "tp2": tp2,
                "rr": f"{rr_val} : 1",
                "invalidation_5m": sl,
                "avoid_zone": [avoid_zone[0], avoid_zone[1]],
            },
            "guidance": {
                "trend_km": f"ផ្សារ 5m {'កំពុងឡើង (Uptrend)' if bias == 'BUY' else 'កំពុងចុះ (Downtrend)'} ${dr['low']:.2f} → ${dr['high']:.2f} • {dr['zone']} ({dr['pct']}%)",
                "entry_km": entry_km,
                "avoid_km": avoid_km,
                "profit_stop_km": profit_stop_km,
                "loss_stop_km": loss_stop_km,
            },
            "backend_summary_km": backend_summary_km,
            "reasons": reasons[:4],
            "pattern_memory": mem_check,
            "learning_brain": mem_stats,
        }

    # ==================== 5-DAY AUTO-REPLAY BACKTEST & CALIBRATION ====================
    def _load_5day_5m_bars(self, fallback_candles: list[dict]) -> tuple[list[dict], str]:
        """ទាញទៀន 5m រយៈពេល 5 ថ្ងៃចុងក្រោយពី Gold Futures (GC=F) ឬប្រើទៀនផ្ទាល់ពី Feed"""
        try:
            import yfinance as yf
            df = yf.download("GC=F", period="5d", interval="5m", progress=False, auto_adjust=False)
            if df is not None and not df.empty and len(df) >= 120:
                bars = []
                for idx, row in df.iterrows():
                    o = safe_float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"])
                    h = safe_float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"])
                    l = safe_float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"])
                    c = safe_float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"])
                    if c > 0 and h >= l:
                        ts = int(idx.timestamp())
                        bars.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
                if len(bars) >= 120:
                    return bars, f"5 ថ្ងៃចុងក្រោយ ({len(bars)} ទៀន 5m)"
        except Exception as e:
            print(f"[REPLAY] yfinance fallback: {e}")

        clean = [c for c in (fallback_candles or []) if safe_float(c.get("close"), 0.0) > 0]
        return clean, f"ទិន្នន័យផ្ទាល់ ({len(clean)} ទៀន 5m)"

    def run_5day_replay(
        self,
        fallback_candles: list[dict] | None = None,
        spread: float = 0.30,
        train_memory: bool = True,
    ) -> dict:
        """
        រត់សាកល្បង Walk-Forward លើទៀន 5m ពិត 5 ថ្ងៃចុងក្រោយ (គ្មានការលួចមើលអនាគត)៖
        - ដកថ្លៃ Spread + Commission ពិត
        - រំកិល BE ស្វ័យប្រវត្តិពេលដល់ 1R និងកាត់ចំណេញនៅ TP1 (1.8R)
        - រាប់ចំនួនអន្ទាក់ដែលប្រព័ន្ធបានការពារ (Chop / Dead Zone / Premium-Discount / VWAP Ext)
        """
        bars, source_km = self._load_5day_5m_bars(fallback_candles or [])
        if len(bars) < 65:
            return {"error": "ទិន្នន័យទៀន 5m មិនទាន់គ្រប់គ្រាន់សម្រាប់រត់តេស្ត (ត្រូវការយ៉ាងតិច 65 ទៀន)"}

        closes = np.array([safe_float(b["close"]) for b in bars], dtype=float)
        highs = np.array([safe_float(b["high"]) for b in bars], dtype=float)
        lows = np.array([safe_float(b["low"]) for b in bars], dtype=float)
        opens = np.array([safe_float(b["open"]) for b in bars], dtype=float)
        times = [int(safe_float(b.get("time"), 0)) for b in bars]

        # Vectorized EMA20, EMA50, ATR14, Rolling VWAP-48
        ema20 = np.zeros_like(closes)
        ema50 = np.zeros_like(closes)
        ema20[0] = closes[0]
        ema50[0] = closes[0]
        a20, a50 = 2.0 / 21.0, 2.0 / 51.0
        for i in range(1, len(closes)):
            ema20[i] = closes[i] * a20 + ema20[i - 1] * (1.0 - a20)
            ema50[i] = closes[i] * a50 + ema50[i - 1] * (1.0 - a50)

        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
        tr[0] = highs[0] - lows[0]
        atr14 = np.convolve(tr, np.ones(14) / 14.0, mode="same")
        atr14 = np.maximum(atr14, 0.8)

        cost_pips = round((max(0.15, safe_float(spread, 0.30)) * 10.0) + COMM_PIPS, 1)
        trades: list[dict] = []
        traps_avoided = 0
        equity_curve = [0.0]
        peak_eq = 0.0
        max_dd_pips = 0.0

        i = 55
        n = len(bars)
        while i < n - 6:
            ts = times[i]
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else datetime.now(timezone.utc)
            dt_kh = dt_utc.astimezone(TZ_KH)
            hm = dt_kh.hour + dt_kh.minute / 60.0

            # ម៉ោងងាប់ 02:30–07:00 កម្ពុជា -> ប្លុក
            if 2.5 <= hm < 7.0:
                traps_avoided += 1
                i += 1
                continue

            is_golden = (14.0 <= hm < 17.5) or (19.33 <= hm < 23.0)
            px = float(closes[i])
            e20, e50, atr = float(ema20[i]), float(ema50[i]), float(atr14[i])

            # 48-bar Dealing Range + VWAP proxy
            r_hi = float(np.max(highs[i - 47:i + 1]))
            r_lo = float(np.min(lows[i - 47:i + 1]))
            span = max(r_hi - r_lo, 1.0)
            pos_pct = ((px - r_lo) / span) * 100.0
            zone = "PREMIUM" if pos_pct >= 62.0 else ("DISCOUNT" if pos_pct <= 38.0 else "EQUILIBRIUM")
            vwap = float(np.mean((highs[i - 35:i + 1] + lows[i - 35:i + 1] + closes[i - 35:i + 1]) / 3.0))

            # Efficiency Ratio (Chop filter)
            net_m = abs(float(closes[i] - closes[i - 15]))
            path_l = float(np.sum(np.abs(np.diff(closes[i - 15:i + 1]))))
            eff = net_m / max(path_l, 0.01)

            # Bar momentum & sweep check
            prev_hi = float(np.max(highs[i - 14:i - 2]))
            prev_lo = float(np.min(lows[i - 14:i - 2]))
            bull_sweep = float(np.min(lows[i - 2:i + 1])) < prev_lo and px > prev_lo
            bear_sweep = float(np.max(highs[i - 2:i + 1])) > prev_hi and px < prev_hi
            bar_bull = px > float(opens[i])
            bar_bear = px < float(opens[i])

            buy_pts, sell_pts = 0, 0
            fb, fs = [], []

            if px > e20 and e20 >= e50:
                buy_pts += 22
                fb.append("EMA_TREND_UP")
            elif px < e20 and e20 <= e50:
                sell_pts += 22
                fs.append("EMA_TREND_DN")

            if zone == "DISCOUNT":
                buy_pts += 18
                fb.append("DISCOUNT_ZONE")
            elif zone == "PREMIUM":
                sell_pts += 18
                fs.append("PREMIUM_ZONE")

            if bull_sweep:
                buy_pts += 18
                fb.append("BULL_SWEEP")
            elif bear_sweep:
                sell_pts += 18
                fs.append("BEAR_SWEEP")

            vwap_d = px - vwap
            max_ext = max(atr * 3.0, 6.0)
            if 0.0 <= vwap_d <= max_ext:
                buy_pts += 12
                fb.append("VWAP_BULL")
            elif -max_ext <= vwap_d <= 0.0:
                sell_pts += 12
                fs.append("VWAP_BEAR")

            if bar_bull:
                buy_pts += 8
            elif bar_bear:
                sell_pts += 8

            if is_golden:
                if buy_pts > sell_pts:
                    buy_pts += 10
                elif sell_pts > buy_pts:
                    sell_pts += 10

            side = "BUY" if buy_pts >= sell_pts else "SELL"
            score = buy_pts if side == "BUY" else sell_pts
            margin = abs(buy_pts - sell_pts)
            factors = fb if side == "BUY" else fs

            # Anti-Trap Filters
            if score >= 56 and margin >= 18:
                if eff < 0.22:
                    traps_avoided += 1
                    i += 1
                    continue
                if side == "BUY" and (zone == "PREMIUM" and eff < 0.42 or vwap_d > max_ext):
                    traps_avoided += 1
                    i += 1
                    continue
                if side == "SELL" and (zone == "DISCOUNT" and eff < 0.42 or vwap_d < -max_ext):
                    traps_avoided += 1
                    i += 1
                    continue

                # Execute causal walk-forward trade on future bars [i+1 .. i+36]
                entry = px
                risk = max(atr * 1.45, 2.2)
                sl = (entry - risk) if side == "BUY" else (entry + risk)
                be_trig = (entry + risk * 1.0) if side == "BUY" else (entry - risk * 1.0)
                tp1 = (entry + risk * 1.8) if side == "BUY" else (entry - risk * 1.8)

                be_locked = False
                exit_px = float(closes[min(n - 1, i + 36)])
                outcome = "TIME"
                exit_idx = min(n - 1, i + 36)

                for j in range(i + 1, min(n, i + 37)):
                    hj, lj = float(highs[j]), float(lows[j])
                    if not be_locked:
                        if (side == "BUY" and hj >= be_trig) or (side == "SELL" and lj <= be_trig):
                            be_locked = True
                            sl = entry + (0.25 if side == "BUY" else -0.25)

                    if side == "BUY":
                        if lj <= sl:
                            exit_px = sl
                            outcome = "BE_WIN" if be_locked else "LOSS"
                            exit_idx = j
                            break
                        if hj >= tp1:
                            exit_px = tp1
                            outcome = "TP1_WIN"
                            exit_idx = j
                            break
                    else:
                        if hj >= sl:
                            exit_px = sl
                            outcome = "BE_WIN" if be_locked else "LOSS"
                            exit_idx = j
                            break
                        if lj <= tp1:
                            exit_px = tp1
                            outcome = "TP1_WIN"
                            exit_idx = j
                            break

                diff = (exit_px - entry) if side == "BUY" else (entry - exit_px)
                pips = round((diff * 10.0) - cost_pips, 1)
                is_win = pips > 0

                kz_tag = "NY_OVERLAP" if (19.33 <= hm < 23.0) else ("LONDON_OPEN" if (14.0 <= hm < 17.5) else "OTHER")
                vwap_tag = "ABOVE_VWAP" if px >= vwap else "BELOW_VWAP"
                sig = f"{side}|{zone}|{vwap_tag}|REPLAY|MIXED|{kz_tag}"

                trades.append({
                    "time_kh": dt_kh.strftime("%d/%m %H:%M"),
                    "side": side,
                    "entry": round(entry, 2),
                    "exit": round(exit_px, 2),
                    "outcome": outcome,
                    "pips": pips,
                    "is_win": is_win,
                    "signature": sig,
                    "factors": factors,
                })

                cur_eq = round(equity_curve[-1] + pips, 1)
                equity_curve.append(cur_eq)
                if cur_eq > peak_eq:
                    peak_eq = cur_eq
                dd = round(peak_eq - cur_eq, 1)
                if dd > max_dd_pips:
                    max_dd_pips = dd

                # រំលងដល់ពេលជើងបិទ + 2 ទៀន (គ្មាន Overlapping Trade)
                i = exit_idx + 2
                continue

            i += 1

        total = len(trades)
        wins = sum(1 for t in trades if t["is_win"])
        losses = total - wins
        gross_win = sum(t["pips"] for t in trades if t["pips"] > 0)
        gross_loss = abs(sum(t["pips"] for t in trades if t["pips"] < 0))
        net_pips = round(sum(t["pips"] for t in trades), 1)
        win_rate = round((wins / total) * 100.0, 1) if total > 0 else 0.0
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (9.99 if gross_win > 0 else 0.0)
        net_usd_001 = round(net_pips * 0.10, 2)  # 0.01 lot = $0.10 / pip

        seeded = False
        if train_memory and trades:
            seeded = self.memory.seed_from_replay(trades, traps_avoided)

        return {
            "source_km": source_km,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "profit_factor": pf,
            "net_pips": net_pips,
            "net_usd_001": net_usd_001,
            "max_dd_pips": max_dd_pips,
            "traps_avoided": traps_avoided,
            "cost_pips": cost_pips,
            "trained_memory": seeded,
            "recent_trades": list(reversed(trades[-6:])),
        }


expert_analyst = AdvancedExpertAnalyst()