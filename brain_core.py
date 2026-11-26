# ==========================================================
# brain_core.py - ប្រើរួមគ្នារវាង train_v8.py និង brain_service.py
# ធានាថាការគណនា Features ពេល Train និងពេល Live ត្រូវគ្នា 100%
# ==========================================================
import os
import numpy as np
import pandas as pd

SL_MULT = 1.5
RR = 2.0
HORIZON = 22
COST_FRAC = 1.0e-4
STRESS_FRAC = 0.6e-4
RISK = 0.01

BASE_FEATURES = [
    "adx_14", "di_diff", "dist_ema20_atr", "dist_ema50_atr", "ema20_50_spread",
    "ema20_slope_3", "dist_dopen_atr", "dist_wopen_atr", "rsi_14", "macd_hist_atr",
    "atr_ratio", "efficiency_10", "body_ratio", "wick_imbalance", "dist_res24_atr",
    "dist_sup24_atr", "h4_trend", "h4_rsi", "h4_eff", "hour_sin", "hour_cos", "cand_side",
]
EVENT_FEATURES = ["ev_to_next_h", "ev_since_last_h"]
MACRO_FEATURES = [
    "dxy_r1", "dxy_r5", "dxy_z20", "us10y_d5", "vix", "vix_d5",
    "spx_r5", "oil_r5", "real10", "real10_d5"
]

MACRO_TICKERS = {
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "vix": "^VIX",
    "spx": "^GSPC",
    "oil": "CL=F"
}


def download_macro(start="2025-01-01"):
    """ទាញទិន្នន័យម៉ាក្រូពិភពលោក (Yahoo Finance + FRED Real Yield ឥតគិតថ្លៃ)"""
    import yfinance as yf
    cols = {}
    for k, t in MACRO_TICKERS.items():
        try:
            d = yf.download(t, start=start, interval="1d", progress=False, auto_adjust=True)
            if d is not None and not d.empty:
                if isinstance(d.columns, pd.MultiIndex):
                    d.columns = d.columns.get_level_values(0)
                s = d["Close"].copy()
                s.index = pd.to_datetime(s.index).tz_localize(None)
                cols[k] = s
            else:
                cols[k] = pd.Series(dtype=float)
        except Exception as e:
            print(f"[macro] {t} failed: {e}")
            cols[k] = pd.Series(dtype=float)

    m = pd.DataFrame(cols)
    try:
        r = pd.read_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10")
        r.columns = ["date", "real10"]
        r["date"] = pd.to_datetime(r["date"])
        r["real10"] = pd.to_numeric(r["real10"], errors="coerce")
        m = m.join(r.set_index("date")["real10"], how="outer")
    except Exception as e:
        print(f"[macro] FRED real yield failed: {e}")
        m["real10"] = np.nan

    if m.empty:
        idx = pd.date_range(end=pd.Timestamp.utcnow().tz_localize(None), periods=30, freq="D")
        m = pd.DataFrame(0.0, index=idx, columns=list(MACRO_TICKERS.keys()) + ["real10"])
    return m.sort_index().ffill()


def macro_features(m):
    f = pd.DataFrame(index=m.index)
    f["dxy_r1"] = m["dxy"].pct_change(1) * 100
    f["dxy_r5"] = m["dxy"].pct_change(5) * 100
    f["dxy_z20"] = (m["dxy"] - m["dxy"].rolling(20).mean()) / m["dxy"].rolling(20).std()
    f["us10y_d5"] = m["us10y"].diff(5)
    f["vix"] = m["vix"]
    f["vix_d5"] = m["vix"].diff(5)
    f["spx_r5"] = m["spx"].pct_change(5) * 100
    f["oil_r5"] = m["oil"].pct_change(5) * 100
    f["real10"] = m["real10"]
    f["real10_d5"] = m["real10"].diff(5)
    f = f.shift(1)
    f[["real10", "real10_d5"]] = f[["real10", "real10_d5"]].shift(1)
    return f


def get_event_times(y0, y1, csv_path=None):
    """កាលវិភាគ NFP (ថ្ងៃសុក្រទី ១ នៃខែ ម៉ោង 08:30 New York) + events.csv បើមាន"""
    ev = []
    for y in range(y0, y1 + 1):
        for mth in range(1, 13):
            d = pd.Timestamp(y, mth, 1)
            while d.dayofweek != 4:
                d += pd.Timedelta(days=1)
            t = pd.Timestamp(f"{y}-{mth:02d}-{d.day:02d} 08:30").tz_localize("America/New_York")
            ev.append(t.tz_convert("UTC"))
    ev = pd.DatetimeIndex(ev)
    if csv_path and os.path.exists(csv_path):
        extra = pd.to_datetime(pd.read_csv(csv_path)["datetime"], utc=True)
        ev = ev.append(pd.DatetimeIndex(extra))
    return ev.sort_values().unique()


def _hours(x):
    return pd.DatetimeIndex(x).tz_convert("UTC").values.astype("datetime64[ns]").astype("int64") / 3.6e12


def event_features(dt, ev_times):
    th = _hours(pd.DatetimeIndex(dt) + pd.Timedelta(hours=1))
    evh = _hours(ev_times)
    idx = np.searchsorted(evh, th, side="right")
    nxt = np.where(idx < len(evh), evh[np.minimum(idx, len(evh) - 1)], np.inf)
    prv = np.where(idx > 0, evh[np.maximum(idx - 1, 0)], -np.inf)
    to_next = np.clip(nxt - th, 0, 96)
    since = np.clip(th - prv, 0, 96)
    blackout = (to_next <= 1.0) | (since <= 1.0)
    return to_next, since, blackout


def build_features(df1h, macro_f, ev_times):
    df = df1h.copy()
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).astype("datetime64[ns, UTC]")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime")
    df = df[df["datetime"].dt.dayofweek < 5].drop_duplicates("datetime").reset_index(drop=True)

    # 4H regime (shift 1 -> zero look-ahead)
    d4h = df.set_index("datetime").resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    c4 = d4h["close"]
    e20, e50 = c4.ewm(span=20, adjust=False).mean(), c4.ewm(span=50, adjust=False).mean()
    d4 = c4.diff()
    rsi4 = 100 - 100 / (
        1 + d4.clip(lower=0).rolling(14).mean() /
        (-d4.clip(upper=0)).rolling(14).mean().replace(0, np.nan)
    )
    eff4 = (c4 - c4.shift(8)).abs() / c4.diff().abs().rolling(8).sum().replace(0, np.nan)
    m4 = pd.DataFrame(index=d4h.index)
    m4["h4_trend"] = ((e20 - e50) / c4).shift(1)
    m4["h4_rsi"] = rsi4.shift(1)
    m4["h4_eff"] = eff4.shift(1)
    m4 = m4.dropna().reset_index()
    m4["datetime"] = m4["datetime"].astype("datetime64[ns, UTC]")
    df = pd.merge_asof(df, m4, on="datetime", direction="backward")

    # Macro merge
    mf = macro_f.copy()
    mf.index = pd.DatetimeIndex(mf.index)
    if mf.index.tz is None:
        mf.index = mf.index.tz_localize("UTC")
    mf.index.name = "datetime"
    mf = mf.reset_index()
    mf["datetime"] = mf["datetime"].astype("datetime64[ns, UTC]")
    df = pd.merge_asof(df, mf, on="datetime", direction="backward")

    df["day_key"] = df["datetime"].dt.floor("D")
    df["week_key"] = (
        df["datetime"].dt.tz_localize(None).dt.to_period("W")
        .apply(lambda r: r.start_time).dt.tz_localize("UTC")
    )
    dopen = df.groupby("day_key")["open"].transform("first")
    wopen = df.groupby("week_key")["open"].transform("first")

    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().replace(0, np.nan)
    rng = (h - l).replace(0, np.nan)

    up, dn = h - h.shift(1), l.shift(1) - l
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    trs = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / trs
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / trs
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    ema20, ema50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    delta = c.diff()

    df["atr_raw"] = atr
    df["adx_14"] = adx
    df["di_diff"] = (pdi - mdi) / 100.0
    df["dist_ema20_atr"] = (c - ema20) / atr
    df["dist_ema50_atr"] = (c - ema50) / atr
    df["ema20_50_spread"] = (ema20 - ema50) / atr
    df["ema20_slope_3"] = (ema20 - ema20.shift(3)) / atr
    df["dist_dopen_atr"] = (c - dopen) / atr
    df["dist_wopen_atr"] = (c - wopen) / atr
    df["rsi_14"] = 100 - 100 / (
        1 + delta.clip(lower=0).rolling(14).mean() /
        (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan)
    )
    df["macd_hist_atr"] = (macd - macd.ewm(span=9, adjust=False).mean()) / atr
    df["atr_ratio"] = atr / tr.rolling(50).mean().replace(0, np.nan)
    df["efficiency_10"] = (c - c.shift(10)).abs() / c.diff().abs().rolling(10).sum().replace(0, np.nan)
    df["body_ratio"] = (c - o) / rng
    df["wick_imbalance"] = ((np.minimum(o, c) - l) - (h - np.maximum(o, c))) / rng
    df["dist_res24_atr"] = (h.rolling(24).max() - c) / atr
    df["dist_sup24_atr"] = (c - l.rolling(24).min()) / atr
    hr = df["datetime"].dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * hr / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * hr / 24.0)

    to_next, since, blackout = event_features(df["datetime"], ev_times)
    df["ev_to_next_h"], df["ev_since_last_h"], df["ev_blackout"] = to_next, since, blackout

    active = (hr >= 6) & (hr <= 19) & (~df["ev_blackout"])
    buy = (
        active & (df["h4_trend"] > 0) & (df["ema20_50_spread"] > 0)
        & df["dist_ema20_atr"].between(-1.5, 1.5) & (df["body_ratio"] > 0.05)
    )
    sell = (
        active & (df["h4_trend"] < 0) & (df["ema20_50_spread"] < 0)
        & df["dist_ema20_atr"].between(-1.5, 1.5) & (df["body_ratio"] < -0.05)
    )
    df["cand_side"] = np.where(buy, 1, np.where(sell, -1, 0))
    return df