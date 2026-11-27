import os
import re

# Root directory នៃគម្រោង (ឡើង ១ កម្រិតពី brain/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "models", "gold_brain_v8.pkl")
EVENTS_CSV = os.path.join(BASE_DIR, "models", "events.csv")
STATS_PATH = os.path.join(BASE_DIR, "models", "signal_stats.json")

BENCHMARK_LOT = 0.01  # ខ្នាតគោល 0.01 Lot (Frontend សារ៉េ 0.01 - 0.05 បាន)
COMM_PIPS = 0.7       # ថ្លៃ Commission មធ្យម $7/Lot = 0.7 pips លើ XAU/USD

USER_AGENT = "TriPisalGoldTerminal/10.6-Modular"

BULLISH_GOLD_WORDS: dict[str, float] = {
    "rate cut": 2.6, "dovish": 2.3, "central bank buys": 2.6, "central bank gold": 2.2,
    "safe-haven": 2.4, "safe haven": 2.4, "gold surges": 2.0, "gold jumps": 1.8,
    "gold rallies": 1.8, "gold climbs": 1.6, "weak dollar": 2.1, "dollar falls": 1.9,
    "dollar drops": 1.9, "recession": 2.3, "inflation fears": 1.6, "stagflation": 2.2,
    "yields fall": 2.1, "yields drop": 1.9, "record high": 1.6, "crisis": 2.0,
    "tariff": 1.7, "trade war": 2.3, "uncertainty": 1.5, "debt ceiling": 1.8,
    "bank crisis": 2.5, "fed pause": 1.8, "unemployment rises": 1.9, "etf inflows": 2.0,
}

BEARISH_GOLD_WORDS: dict[str, float] = {
    "rate hike": -2.7, "hawkish": -2.4, "ceasefire": -2.5, "peace deal": -2.4,
    "truce": -2.2, "strong dollar": -2.3, "dollar surges": -2.1, "dollar rallies": -1.9,
    "yields surge": -2.3, "yields rise": -1.9, "yields jump": -2.1,
    "gold drops": -1.8, "gold falls": -1.8, "gold slides": -1.8, "gold slips": -1.5,
    "profit taking": -1.5, "strong jobs": -2.1, "nfp beats": -2.3,
    "higher for longer": -2.4, "risk-on": -1.5, "inflation cools": -1.3, "etf outflows": -2.0,
}

GOLD_RELEVANT_FILTER: tuple[str, ...] = (
    "gold", "xau", "bullion", "fed", "powell", "fomc", "inflation", "cpi", "pce",
    "dollar", "dxy", "treasury", "yield", "tariff", "central bank", "recession", "jobs", "nfp",
)

WAR_HIGH_WORDS: dict[str, float] = {
    "missile": 3.0, "airstrike": 2.8, "air strike": 2.8, "nuclear": 3.5,
    "invasion": 3.0, "bombing": 2.6, "retaliation": 2.5, "escalation": 2.5,
    "drone attack": 2.5, "war escalates": 3.0, "middle east war": 3.0,
    "strait of hormuz": 3.0, "red sea attack": 2.5, "martial law": 2.5,
}

WAR_MED_WORDS: dict[str, float] = {
    "war": 1.8, "conflict": 1.6, "military": 1.4, "troops": 1.5, "sanctions": 1.8,
    "iran": 1.5, "israel": 1.5, "gaza": 1.5, "ukraine": 1.5, "russia": 1.4,
    "taiwan": 1.8, "north korea": 1.8, "nato": 1.5, "attack": 1.6, "military strike": 1.8,
}

WAR_DEESCALATE_WORDS: dict[str, float] = {
    "ceasefire": -3.0, "peace talks": -2.2, "peace deal": -2.8, "truce": -2.5,
    "de-escalation": -2.5, "withdraws troops": -2.2, "hostage deal": -2.0,
}


def _compile_kw_map(kw_dict: dict[str, float]) -> list[tuple[re.Pattern, float]]:
    return [(re.compile(r"\b" + re.escape(kw.lower()) + r"\b"), float(w)) for kw, w in kw_dict.items()]


def _compile_filter_regex(words: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w.lower()) for w in words) + r")\b")


BULLISH_PATTERNS = _compile_kw_map(BULLISH_GOLD_WORDS)
BEARISH_PATTERNS = _compile_kw_map(BEARISH_GOLD_WORDS)
GOLD_FILTER_RE = _compile_filter_regex(GOLD_RELEVANT_FILTER)
WAR_HIGH_PATTERNS = _compile_kw_map(WAR_HIGH_WORDS)
WAR_MED_PATTERNS = _compile_kw_map(WAR_MED_WORDS)
WAR_COOL_PATTERNS = _compile_kw_map(WAR_DEESCALATE_WORDS)

GLOBAL_RSS_FEEDS: list[tuple[str, str]] = [
    ("Yahoo Gold/DXY", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F,DX-Y.NYB&region=US&lang=en-US"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("CNBC Macro", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("Investing Gold", "https://www.investing.com/rss/news_11.rss"),
    ("Kitco Gold", "https://www.kitco.com/rss/gold.xml"),
]

WAR_RSS_FEEDS: list[tuple[str, str]] = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]

GDELT_WAR_API = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=(missile%20OR%20airstrike%20OR%20%22middle%20east%22%20OR%20ukraine%20OR%20sanctions%20OR%20ceasefire)%20sourcelang:eng"
    "&mode=artlist&maxrecords=8&timespan=12h&format=json"
)