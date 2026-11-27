import threading

from brain import brain_service
from .config import extras_executor, safe_float
from .cache import cache
from .feeds import (
    fetch_gold_data,
    fetch_live_quote,
    fetch_usd_news,
    fetch_order_flow,
    check_cross_source_sanity,
)
from .indicators import (
    calculate_indicators,
    get_mtf_confluence,
    get_institutional_levels,
)
from .smt_scanner import get_smt_divergence, get_mtf_scanner

_basis_lock = threading.Lock()
_last_valid_basis = 0.0


def _evaluate_brain_v8(payload: dict, basis: float = 0.0) -> dict:
    try:
        df_1h, _, _, raw_1h = fetch_gold_data("1h")
        if df_1h is not None and not df_1h.empty:
            df_1h = df_1h.copy()
            if basis != 0.0 and raw_1h not in ("OANDA", "TWELVE"):
                for col in ("open", "high", "low", "close"):
                    df_1h[col] = df_1h[col] + basis
        return brain_service.evaluate(df_1h, payload)
    except Exception as e:
        print(f"[BRAIN_EVAL] fallback after error: {e}")
        return {
            "version": "V11.0-FALLBACK",
            "status": "WAIT", "grade": "WATCH", "can_trade": False, "side": "NONE",
            "is_danger": False, "confidence_score": 35,
            "action_km": "⏳ កំពុងភ្ជាប់ខួរក្បាល AI ឡើងវិញ...",
            "hud_hint_km": "កំពុងសមកាលកម្មទិន្នន័យ...",
            "reasons": ["កំពុងសមកាលកម្មទិន្នន័យឡើងវិញ សូមរង់ចាំបន្តិច"],
        }


def attach_extras_sync(payload: dict, gold_df=None) -> dict:
    out = dict(payload)
    basis = float(out.get("basis_offset") or 0.0)
    price = float(out.get("price") or 0.0)
    interval = str(out.get("interval") or "15m")

    f_mtf = extras_executor.submit(get_mtf_confluence)
    f_news = extras_executor.submit(fetch_usd_news)
    f_inst = extras_executor.submit(get_institutional_levels, basis)
    f_flow = extras_executor.submit(fetch_order_flow)
    f_san = extras_executor.submit(check_cross_source_sanity, price, basis)
    f_smt = extras_executor.submit(get_smt_divergence, gold_df, interval)
    f_scan = extras_executor.submit(get_mtf_scanner)

    def _safe_res(fut, default):
        try:
            return fut.result(timeout=8.5)
        except Exception:
            return default

    out["mtf"] = _safe_res(f_mtf, {
        "timeframes": [], "verdict": "SYNC", "aligned": "MIXED",
        "warning": None, "buy_count": 0, "sell_count": 0,
    })
    out["news"] = _safe_res(f_news, {"danger": False, "danger_event": None, "upcoming": []})
    out["inst_levels"] = _safe_res(f_inst, {"pdh": None, "pdl": None, "asian_high": None, "asian_low": None})
    out["order_flow"] = _safe_res(f_flow, {
        "buy_pct": 50.0, "sell_pct": 50.0,
        "buy_vol": 0.0, "sell_vol": 0.0, "trades_count": 0, "source": "CANDLES",
    })
    out["sanity"] = _safe_res(f_san, {
        "ok": True, "glitch_detected": False,
        "deviation": 0.0, "sources_checked": 0, "note": "OK",
    })
    out["smt"] = _safe_res(f_smt, {
        "signal": "NONE", "label_km": "XAU/XAG ស្របគ្នា",
        "detail_km": "", "silver_px": None, "strength": 0,
    })
    out["tf_scanner"] = _safe_res(f_scan, {"items": [], "best": None})
    out["brain"] = _evaluate_brain_v8(out, basis=basis)
    return out


def _build_full_payload_uncached(interval: str, include_extras: bool = True) -> dict | None:
    global _last_valid_basis
    df, source, is_spot, raw_feed = fetch_gold_data(interval)
    if df.empty or len(df) < 5:
        return None
    quote = fetch_live_quote()
    df = df.copy()
    basis = 0.0

    if quote and quote["price"] > 0:
        live_p = quote["price"]
        raw_last = float(df.iloc[-1]["close"])
        if raw_feed not in ("OANDA", "TWELVE"):
            if quote.get("is_forex"):
                cand_basis = live_p - raw_last
                if abs(cand_basis) < 120:
                    basis = cand_basis
                    with _basis_lock:
                        _last_valid_basis = basis
                source = quote.get("feed", "Forex Spot Live • XAU/USD")
                is_spot = True
            else:
                with _basis_lock:
                    basis = _last_valid_basis
            if basis != 0.0:
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] + basis

        target_p = live_p if (quote.get("is_forex") or raw_feed in ("OANDA", "TWELVE")) else (live_p + basis)
        df.iloc[-1, df.columns.get_loc("close")] = target_p
        if target_p > df.iloc[-1]["high"]:
            df.iloc[-1, df.columns.get_loc("high")] = target_p
        if target_p < df.iloc[-1]["low"]:
            df.iloc[-1, df.columns.get_loc("low")] = target_p

    payload = calculate_indicators(df)
    payload["source"] = source
    payload["is_spot"] = is_spot
    payload["is_forex"] = bool(quote.get("is_forex")) if quote else False
    payload["raw_feed"] = raw_feed
    payload["basis_offset"] = safe_float(basis, 0.0, 4)
    payload["interval"] = interval
    payload["bid"] = quote["bid"] if quote else payload["price"]
    payload["ask"] = quote["ask"] if quote else safe_float(payload["price"] + 0.25)
    payload["spread"] = quote["spread"] if quote else 0.25
    payload["type"] = "full"

    if include_extras:
        payload = attach_extras_sync(payload, gold_df=df)
    return payload


def build_full_payload_sync(interval: str, include_extras: bool = True) -> dict | None:
    """Shared short-TTL cache: បើកច្រើន Tab ព្រមគ្នាក៏ Server គណនាតែ ១ ដង"""
    if include_extras:
        return cache.get_or_fetch(
            f"full_payload_{interval}",
            3.5,
            lambda: _build_full_payload_uncached(interval, True),
        )
    return _build_full_payload_uncached(interval, False)