import os
import json
import threading
from datetime import datetime, timezone

from .config import STATS_PATH, BENCHMARK_LOT, COMM_PIPS
from .utils import TZ_KH, safe_float


class SignalWinRateTracker:
    """ចាប់រាល់សញ្ញា BUY/SELL (Market & Limit), រង់ចាំប៉ះតម្លៃពិត (Filled), ដក Spread+Comm, Loss-Streak & Atomic JSON"""

    def __init__(self, path: str = STATS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.data = {
            "wins": 0,
            "losses": 0,
            "gross_win_pips": 0.0,
            "gross_loss_pips": 0.0,
            "active": {},
            "recent": [],
        }
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.data["wins"] = int(d.get("wins", 0))
                    self.data["losses"] = int(d.get("losses", 0))
                    self.data["gross_win_pips"] = float(d.get("gross_win_pips", 0.0))
                    self.data["gross_loss_pips"] = float(d.get("gross_loss_pips", 0.0))
                    self.data["active"] = d.get("active") if isinstance(d.get("active"), dict) else {}
                    self.data["recent"] = d.get("recent") if isinstance(d.get("recent"), list) else []
        except Exception as e:
            print(f"[AUDIT] load reset (corrupt file): {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp_path = f"{self.path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except Exception as e:
            print(f"[AUDIT] save error: {e}")

    def _pips_to_usd(self, pips: float, lot: float = BENCHMARK_LOT) -> float:
        return round(pips * (lot * 10.0), 2)

    def get_loss_streak(self, tf: str) -> dict:
        """រាប់ចំនួនជើងខាតជាប់គ្នា (ក្នុងរយៈពេល ៦ ម៉ោងចុងក្រោយ) ដើម្បីបើក Circuit Breaker ការពារ Chop"""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff_ts = now_ts - 21600  # 6 hours window
        tf_streak = 0
        global_streak = 0

        with self._lock:
            recent = list(self.data.get("recent") or [])

        for r in recent:
            if int(r.get("ts", 0)) < cutoff_ts:
                break
            if r.get("result") == "LOSS":
                global_streak += 1
            else:
                break

        for r in recent:
            if r.get("tf") != tf:
                continue
            if int(r.get("ts", 0)) < cutoff_ts:
                break
            if r.get("result") == "LOSS":
                tf_streak += 1
            else:
                break

        active = (tf_streak >= 2) or (global_streak >= 3)
        return {
            "active": active,
            "tf_losses": tf_streak,
            "global_losses": global_streak,
            "reason_km": (
                f"🛡️ ប្រព័ន្ធការពារ Anti-Chop សកម្ម (ខាត {tf_streak} ជើងជាប់គ្នាលើ {tf}) — តម្រូវតែសញ្ញាខ្លាំង A/A+"
                if tf_streak >= 2
                else (
                    f"🛡️ ប្រព័ន្ធការពារ Anti-Chop សកម្ម (ខាត {global_streak} ជើងជាប់គ្នា) — កុំ Overtrade"
                    if global_streak >= 3
                    else ""
                )
            ),
        }

    def update_and_record(
        self,
        tf: str,
        price: float,
        high: float,
        low: float,
        new_signal: dict | None,
        spread: float = 0.30,
    ) -> dict:
        changed = False
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        now_kh = now_utc.astimezone(TZ_KH).strftime("%d/%m %H:%M")
        cost_pips = round((max(0.0, safe_float(spread, 0.30)) * 10.0) + COMM_PIPS, 1)

        with self._lock:
            act = self.data["active"].get(tf)
            if act and price > 0:
                hi = max(price, high) if high > 0 else price
                lo = min(price, low) if low > 0 else price

                if not act.get("filled", True):
                    if lo <= act["entry"] <= hi:
                        act["filled"] = True
                        act["open_kh"] = now_kh
                        act["ts"] = now_ts
                        changed = True
                    elif (now_ts - act.get("ts", now_ts)) > 7200:
                        self.data["active"].pop(tf, None)
                        act = None
                        changed = True

                if act and act.get("filled", True):
                    outcome, exit_px = None, price
                    if act["side"] == "BUY":
                        if lo <= act["sl"]:
                            outcome, exit_px = "LOSS", act["sl"]
                        elif hi >= act["tp"]:
                            outcome, exit_px = "WIN", act["tp"]
                    else:
                        if hi >= act["sl"]:
                            outcome, exit_px = "LOSS", act["sl"]
                        elif lo <= act["tp"]:
                            outcome, exit_px = "WIN", act["tp"]

                    if outcome:
                        diff = (exit_px - act["entry"]) if act["side"] == "BUY" else (act["entry"] - exit_px)
                        pips = round((diff * 10.0) - cost_pips, 1)
                        is_win = pips > 0
                        if is_win:
                            self.data["wins"] += 1
                            self.data["gross_win_pips"] = round(self.data["gross_win_pips"] + pips, 1)
                        else:
                            self.data["losses"] += 1
                            self.data["gross_loss_pips"] = round(self.data["gross_loss_pips"] + abs(pips), 1)

                        dur_min = max(1, int((now_ts - act.get("ts", now_ts)) // 60))
                        self.data["recent"].insert(0, {
                            "tf": tf, "side": act["side"], "grade": act.get("grade", "B"),
                            "is_sniper": bool(act.get("is_sniper", False)),
                            "entry_state": act.get("entry_state", "IN_ZONE"),
                            "entry": act["entry"], "exit": round(exit_px, 2),
                            "sl": act["sl"], "tp": act["tp"],
                            "pips": pips, "usd": self._pips_to_usd(pips),
                            "result": "WIN" if is_win else "LOSS",
                            "open_kh": act.get("open_kh", "--"), "close_kh": now_kh,
                            "dur_min": dur_min, "ts": now_ts,
                        })
                        self.data["recent"] = self.data["recent"][:30]
                        self.data["active"].pop(tf, None)
                        changed = True
                    elif (now_ts - act.get("ts", now_ts)) > 86400:
                        self.data["active"].pop(tf, None)
                        changed = True

            if new_signal and tf not in self.data["active"]:
                is_market = new_signal.get("entry_state", "IN_ZONE") == "IN_ZONE"
                self.data["active"][tf] = {
                    "tf": tf, "side": new_signal["side"],
                    "grade": new_signal.get("grade", "B"),
                    "confidence": new_signal.get("confidence", 50),
                    "is_sniper": bool(new_signal.get("is_sniper", False)),
                    "entry_state": new_signal.get("entry_state", "IN_ZONE"),
                    "filled": is_market,
                    "entry": new_signal["entry"], "sl": new_signal["sl"], "tp": new_signal["tp"],
                    "open_kh": now_kh, "ts": now_ts,
                }
                changed = True

            if changed:
                self._save()

            active_list = []
            floating_pips_total = 0.0
            for k_tf, a in self.data["active"].items():
                cur_px = price if price > 0 else a["entry"]
                is_filled = bool(a.get("filled", True))
                if is_filled:
                    diff = (cur_px - a["entry"]) if a["side"] == "BUY" else (a["entry"] - cur_px)
                    lpips = round((diff * 10.0) - cost_pips, 1)
                    floating_pips_total += lpips
                else:
                    lpips = 0.0
                sl_pips = int(round(max(abs(a["entry"] - a["sl"]), 0.1) * 10))
                tp_pips = int(round(max(abs(a["tp"] - a["entry"]), 0.1) * 10))
                active_list.append({
                    "tf": k_tf, "side": a["side"], "grade": a.get("grade", "B"),
                    "is_sniper": bool(a.get("is_sniper", False)),
                    "entry_state": a.get("entry_state", "IN_ZONE") if is_filled else "PULLBACK",
                    "filled": is_filled,
                    "entry": a["entry"], "sl": a["sl"], "tp": a["tp"],
                    "live_price": round(cur_px, 2),
                    "live_pips": lpips, "live_usd": self._pips_to_usd(lpips),
                    "sl_pips": sl_pips, "tp_pips": tp_pips,
                    "open_kh": a.get("open_kh", "--"),
                })

            wins, losses = self.data["wins"], self.data["losses"]
            total = wins + losses
            gw, gl = self.data["gross_win_pips"], self.data["gross_loss_pips"]
            net_pips = round(gw - gl, 1)
            pf = round(gw / gl, 2) if gl > 0 else (round(gw, 1) if gw > 0 else None)

            tf_rec = [r for r in self.data["recent"] if r.get("tf") == tf]
            tf_wins = sum(1 for r in tf_rec if r.get("result") == "WIN")
            tf_wr = round((tf_wins / len(tf_rec)) * 100.0, 1) if tf_rec else None

            return {
                "wins": wins,
                "losses": losses,
                "total": total,
                "win_rate": round((wins / total) * 100.0, 1) if total > 0 else None,
                "tf_win_rate": tf_wr,
                "net_pips": net_pips,
                "net_usd": self._pips_to_usd(net_pips),
                "floating_pips": round(floating_pips_total, 1),
                "floating_usd": self._pips_to_usd(floating_pips_total),
                "profit_factor": pf,
                "benchmark_lot": BENCHMARK_LOT,
                "cost_included": True,
                "active_count": len(active_list),
                "active_list": active_list,
                "recent": self.data["recent"][:12],
            }