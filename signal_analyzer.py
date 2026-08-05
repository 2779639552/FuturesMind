"""FuturesMind Signal Analyzer — P1/P2/P3 analysis features.

Anomaly detection, bull-bear divergence, lead-lag analysis (Granger),
cross-platform divergence, author profiling, event extraction, simulated trading.
"""

import json
import math
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from dataclasses import dataclass, field

SENTIMENT_DIR = Path(os.path.expanduser("~/.tradingagents/external_data"))
THINK2_TRENDS = Path(os.environ.get(
    "THINK2_DIR", os.path.expanduser("~/Desktop/思路2/validate")
)) / "output" / "trends"

# ── Helpers ────────────────────────────────────────────────────────

def _load_sentiment(variety: str) -> dict | None:
    path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_trends(variety: str) -> dict | None:
    """Load trends sentiment JSON, trying multiple naming conventions."""
    # Try direct code match first
    path = THINK2_TRENDS / f"{variety}_sentiment.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Try looking up Chinese name from VARIETY_METADATA
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA
        meta = VARIETY_METADATA.get(variety, {})
        chinese_name = meta.get("name", "")
        if chinese_name:
            path = THINK2_TRENDS / f"{chinese_name}_sentiment.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
    except ImportError:
        pass

    # Try glob search
    for f in THINK2_TRENDS.glob("*_sentiment.json"):
        if variety in f.stem or f.stem.startswith(variety):
            with open(f, "r", encoding="utf-8") as fp:
                return json.load(fp)

    return None


def _load_price(variety: str) -> dict | None:
    """Load price JSON, trying multiple naming conventions."""
    path = THINK2_TRENDS / f"{variety}_price.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Try Chinese name
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA
        meta = VARIETY_METADATA.get(variety, {})
        chinese_name = meta.get("name", "")
        if chinese_name:
            path = THINK2_TRENDS / f"{chinese_name}_price.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
    except ImportError:
        pass

    # Try glob
    for f in THINK2_TRENDS.glob("*_price.json"):
        if variety in f.stem or f.stem.startswith(variety):
            with open(f, "r", encoding="utf-8") as fp:
                return json.load(fp)

    return None


def pearson_r(x, y):
    n = len(x)
    if n < 3:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


# ═══════════════════════════════════════════════════════════════════
# P1-1: Sentiment Anomaly Detection
# ═══════════════════════════════════════════════════════════════════

def detect_anomalies(variety: str, threshold_std: float = 2.0) -> list[dict]:
    """Detect days where sentiment deviates significantly from its moving average.

    Uses: z-score = (score - MA_7d) / std_7d.  |z| > threshold = anomaly.
    """
    sent_data = _load_sentiment(variety)
    if not sent_data:
        return []

    series = sent_data.get("data", {}).get("daily_series", [])
    if len(series) < 14:
        return []

    scores = [s.get("avg_score", 0) for s in series]
    dates = [s["date"] for s in series]
    anomalies = []

    for i in range(7, len(scores)):
        window = scores[i - 7:i]
        ma = sum(window) / 7
        std = math.sqrt(sum((x - ma) ** 2 for x in window) / 7)
        if std == 0:
            continue
        z = (scores[i] - ma) / std
        if abs(z) > threshold_std:
            anomalies.append({
                "date": dates[i],
                "score": round(scores[i], 3),
                "ma_7d": round(ma, 3),
                "z_score": round(z, 2),
                "direction": "bullish" if z > 0 else "bearish",
                "type": "spike_up" if z > 0 else "spike_down",
            })

    return anomalies


# ═══════════════════════════════════════════════════════════════════
# P1-2: Bull-Bear Divergence Index
# ═══════════════════════════════════════════════════════════════════

def compute_divergence(variety: str) -> dict | None:
    """Compute bull-bear divergence: how split is market sentiment?

    Divergence = 1 - |bullish_ratio - bearish_ratio|
    High divergence (>0.7) = strong consensus; Low (<0.4) = split/uncertain.
    """
    sent_data = _load_sentiment(variety)
    if not sent_data:
        return None

    # Bull/bear ratios are at social_sentiment level, NOT in daily series entries
    ss = sent_data.get("data", {}).get("social_sentiment", {})
    bull = ss.get("bullish_ratio", 0) or 0
    bear = ss.get("bearish_ratio", 0) or 0
    neutral = 1 - bull - bear if (bull + bear) <= 1 else 0

    divergence = round(1 - abs(bull - bear), 3)
    latest_date = sent_data.get("data", {}).get("daily_series", [{}])[-1].get("date", "")

    # Trend: compare current vs 7 days ago using daily series avg_score
    series = sent_data.get("data", {}).get("daily_series", [])
    if len(series) >= 7:
        recent = [s.get("avg_score", 0) for s in series[-7:]]
        prev_week = [s.get("avg_score", 0) for s in series[-14:-7]] if len(series) >= 14 else recent
        recent_avg = sum(recent) / len(recent) if recent else 0
        prev_avg = sum(prev_week) / len(prev_week) if prev_week else 0
        if abs(recent_avg - prev_avg) > 0.1:
            trend = "improving" if abs(recent_avg) < abs(prev_avg) else "diverging"
        else:
            trend = "steady"
    else:
        trend = "steady"

    # Total notes from social_sentiment
    total_notes = ss.get("total_posts_analyzed", 0)

    # Interpret
    if divergence < 0.3:
        label = "极度分歧"
        signal = "趋势可能反转"
    elif divergence < 0.5:
        label = "明显分歧"
        signal = "方向不确定"
    elif divergence < 0.7:
        label = "中度共识"
        signal = "趋势可能延续"
    else:
        label = "高度共识"
        signal = "警惕拥挤交易"

    return {
        "variety": variety,
        "date": latest_date,
        "bullish_ratio": round(bull, 3),
        "bearish_ratio": round(bear, 3),
        "neutral_ratio": round(neutral, 3),
        "divergence": divergence,
        "label": label,
        "signal": signal,
        "trend": trend,
        "total_notes": total_notes,
    }


# ═══════════════════════════════════════════════════════════════════
# P1-3: Lead-Lag Analysis (simplified Granger-style)
# ═══════════════════════════════════════════════════════════════════

def analyze_lead_lag(variety: str, max_lag: int = 5) -> dict | None:
    """Test if sentiment leads price or vice versa.

    For each lag 1..max_lag:
      - Sentiment(t) → Price(t+lag): does today's mood predict future price?
      - Price(t) → Sentiment(t+lag): does today's price move predict future mood?

    Returns correlations at each lag to determine which leads.
    """
    sent_data = _load_sentiment(variety)
    price_data = _load_price(variety)
    if not sent_data or not price_data:
        return None

    series = sent_data.get("data", {}).get("daily_series", [])
    prices_raw = price_data.get("prices", [])

    if len(series) < 20 or len(prices_raw) < 20:
        return None

    # Build aligned arrays
    price_map = {str(p["date"])[:10]: float(p["close"]) for p in prices_raw}
    dates = []
    scores = []
    price_changes = []

    for s in series:
        d = s["date"]
        if d not in price_map:
            continue
        dates.append(d)
        scores.append(s.get("avg_score", 0))

    for i in range(len(dates)):
        if i == 0:
            price_changes.append(0)
        else:
            p0 = price_map.get(dates[i - 1], 0)
            p1 = price_map.get(dates[i], 0)
            price_changes.append((p1 - p0) / p0 if p0 else 0)

    # Lead-lag correlations
    sent_leads = {}  # sentiment(t) → price_change(t+lag)
    price_leads = {}  # price_change(t) → sentiment(t+lag)

    for lag in range(1, max_lag + 1):
        s_lead = []
        p_lead = []
        for i in range(len(dates) - lag):
            # Sentiment leading price
            s_lead.append((scores[i], price_changes[i + lag]))
            # Price leading sentiment
            p_lead.append((price_changes[i], scores[i + lag]))

        if len(s_lead) >= 5:
            sent_leads[f"lag_{lag}d"] = round(pearson_r(
                [x[0] for x in s_lead], [x[1] for x in s_lead]
            ), 4)
            price_leads[f"lag_{lag}d"] = round(pearson_r(
                [x[0] for x in p_lead], [x[1] for x in p_lead]
            ), 4)

    # Determine which leads
    max_sent = max(sent_leads.values(), key=abs) if sent_leads else 0
    max_price = max(price_leads.values(), key=abs) if price_leads else 0

    conclusion = "sentiment_leads" if abs(max_sent) > abs(max_price) else "price_leads"

    return {
        "variety": variety,
        "data_points": len(dates),
        "sentiment_leads_price": sent_leads,
        "price_leads_sentiment": price_leads,
        "conclusion": conclusion,
        "max_sent_corr": max_sent,
        "max_price_corr": max_price,
    }


# ═══════════════════════════════════════════════════════════════════
# P2-1: Cross-Platform Sentiment Divergence
# ═══════════════════════════════════════════════════════════════════

def analyze_cross_platform(variety: str) -> dict | None:
    """Compare sentiment across platforms. Weibo=retail, Zhihu=informed, XHS=social.

    Cross-platform divergence may signal: retail bullish + informed bearish = danger.
    """
    trends = _load_trends(variety)
    if not trends:
        return None

    series = trends.get("series", [])
    if not series:
        return None

    latest = series[-1]
    platform_scores = latest.get("platform_scores", {})
    if not platform_scores:
        return None

    platforms = {}
    for plat, pdata in platform_scores.items():
        platforms[plat] = {
            "avg_score": round(pdata.get("avg_score", 0), 3),
            "simple_avg": round(pdata.get("simple_avg", 0), 3),
            "total_notes": pdata.get("total_notes", 0),
            "bullish_ratio": round(pdata.get("bullish_ratio", 0), 3),
            "bearish_ratio": round(pdata.get("bearish_ratio", 0), 3),
        }

    # Check for cross-platform divergence
    scores_list = [p["avg_score"] for p in platforms.values()]
    if len(scores_list) >= 2:
        max_s = max(scores_list)
        min_s = min(scores_list)
        spread = max_s - min_s
        conflict = "high" if spread > 0.5 else "moderate" if spread > 0.3 else "low"
    else:
        spread = 0
        conflict = "insufficient"

    return {
        "variety": variety,
        "date": latest["date"],
        "platforms": platforms,
        "spread": round(spread, 3),
        "conflict_level": conflict,
        "interpretation": _interpret_cross_platform(platforms, conflict),
    }


def _interpret_cross_platform(platforms: dict, conflict: str) -> str:
    if conflict == "insufficient":
        return "数据不足，无法判断"
    if conflict == "low":
        return "各平台情绪一致，信号可信度较高"

    weibo = platforms.get("weibo", {}).get("avg_score", 0)
    zhihu = platforms.get("zhihu", {}).get("avg_score", 0)

    if weibo > 0.1 and zhihu < -0.1:
        return "散户看多但专业社区看空——警惕情绪泡沫"
    elif weibo < -0.1 and zhihu > 0.1:
        return "散户恐慌但专业社区看多——可能是抄底机会"
    elif conflict == "high":
        return "各平台分歧严重，市场方向不确定"
    return "平台间情绪有差异，建议综合判断"


# ═══════════════════════════════════════════════════════════════════
# P1-4: Author Profiling
# ═══════════════════════════════════════════════════════════════════

def get_top_authors(limit: int = 20) -> list[dict]:
    """Get top authors ranked by influence (posts * engagement * log(fans))."""
    index_path = THINK2_TRENDS / "_author_index.json"
    if not index_path.exists():
        return []

    with open(index_path, "r", encoding="utf-8") as f:
        authors = json.load(f)

    result = []
    for uid, data in authors.items():
        if not isinstance(data, dict):
            continue
        # Actual fields: name, posts, fans, avg_engagement
        name = data.get("name", str(uid))
        posts = data.get("posts", 0) or 0
        fans = data.get("fans", 0) or 0
        engagement = data.get("avg_engagement", 0) or 0

        # Influence: fans is primary (log10), posts sqrt-dampened, engagement as modifier
        if fans > 0:
            fans_score = math.log10(fans + 1)  # log10: 1K→3, 1万→4, 10万→5, 100万→6
            posts_score = math.sqrt(max(posts, 1))
            eng_score = math.log(engagement + 1) if engagement > 0 else 0
            influence = round(fans_score * 0.70 + posts_score * 0.10 + eng_score * 0.20, 1)
        elif posts > 0:
            influence = round(math.sqrt(posts) * 0.5, 1)
        else:
            influence = 0.0

        result.append({
            "name": name,
            "uid": uid,
            "posts": posts,
            "fans": fans,
            "avg_engagement": round(engagement, 1),
            "influence": influence,
        })

    result.sort(key=lambda x: -x["influence"])
    return result[:limit]


# ═══════════════════════════════════════════════════════════════════
# P1-5: Event Extraction (keyword-based)
# ═══════════════════════════════════════════════════════════════════

EVENT_KEYWORDS = {
    "停产/检修": ["停产", "检修", "停工", "限产", "减产"],
    "政策/监管": ["发改委", "工信部", "证监会", "交易所", "保证金", "手续费", "限仓"],
    "进出口": ["进口", "出口", "关税", "反倾销", "海关", "配额"],
    "天气/灾害": ["台风", "暴雨", "洪涝", "干旱", "地震", "极端天气"],
    "矿山/油田": ["矿山", "矿难", "溃坝", "油田", "钻井", "爆炸"],
    "地缘政治": ["制裁", "战争", "冲突", "脱欧", "北约", "中东"],
    "库存/交割": ["库存大增", "库存大降", "仓单", "逼仓", "交割"],
    "宏观数据": ["GDP", "CPI", "PMI", "加息", "降息", "非农", "美联储"],
}


def extract_events(variety: str = "", days: int = 7) -> list[dict]:
    """Extract key events from recent posts using keyword matching."""
    # Read directly from JSONL batch files (database may not be populated)
    import glob as _glob
    think2_output = Path(os.environ.get("THINK2_DIR", os.path.expanduser("~/Desktop/思路2/validate"))) / "output"
    if not think2_output.exists():
        return []

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    posts = []
    seen = set()
    for fpath in sorted(_glob.glob(str(think2_output / "batch_*.jsonl"))):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip(): continue
                    d = json.loads(line)
                    nid = d.get("note_id", "")
                    if nid in seen: continue
                    seen.add(nid)
                    t = (d.get("publish_time", "") or "")[:10]
                    if t < since: continue
                    if variety and variety not in json.dumps(d.get("varieties", [])): continue
                    posts.append(d)
                    if len(posts) >= 500: break
        except Exception: pass
        if len(posts) >= 500: break

    events = []
    for post in posts:
        title = (post.get("title", "") or "") + " " + (post.get("content", "") or "")
        matched_categories = []
        for category, keywords in EVENT_KEYWORDS.items():
            for kw in keywords:
                if kw in title:
                    matched_categories.append(category)
                    break
        if matched_categories:
            # Extract variety names and sentiment
            varieties_raw = post.get("varieties", [])
            if isinstance(varieties_raw, str):
                try: varieties_raw = json.loads(varieties_raw)
                except (json.JSONDecodeError, TypeError): varieties_raw = []
            variety_names = [v["name"] if isinstance(v, dict) else str(v) for v in varieties_raw] if isinstance(varieties_raw, list) else []
            sentiment_label = post.get("sentiment", "neutral")
            sentiment_score = post.get("sentiment_score", 0)

            events.append({
                "date": (post.get("publish_time", "") or "")[:10],
                "platform": post.get("platform", "?"),
                "title": (post.get("title", "") or "")[:120],
                "categories": matched_categories,
                "url": post.get("url", ""),
                "varieties": variety_names[:5],
                "sentiment": sentiment_label,
                "sentiment_score": round(sentiment_score, 2) if isinstance(sentiment_score, (int, float)) else 0,
            })

    # Deduplicate similar events
    seen_titles = set()
    unique_events = []
    for e in events:
        key = e["title"][:50]
        if key not in seen_titles:
            seen_titles.add(key)
            unique_events.append(e)

    return unique_events[:50]


# ═══════════════════════════════════════════════════════════════════
# P3-D: Dual MA Crossover (DMAC) — proven trend-following
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# Contrarian Sentiment Strategy
# ═══════════════════════════════════════════════════════════════════

def run_contrarian_sentiment(
    variety: str = "",
    horizon: int = 3,
    trend_window: int = 5,
    start_date: str = "2025-01-01",
) -> dict:
    """Contrarian sentiment: trade AGAINST the trend when sentiment disagrees.

    Logic:
      - Price falling (N-day trend < 0) + sentiment bullish (score > 0) → LONG (bottom signal)
      - Price rising (N-day trend > 0) + sentiment bearish (score < 0) → SHORT (top signal)
      - Exit: fixed horizon or when sentiment aligns with trend again

    This exploits the finding that sentiment is a CONTRARIAN indicator (55.9% accurate
    when opposing the trend vs 50.0% when following it).
    """
    all_contrarian = []
    all_momentum = []  # Comparison: follow-the-trend (same signals, reversed logic)

    vlist = [variety] if variety else _get_all_varieties_with_data()

    for var in vlist:
        price_data = _load_price(var)
        sent_data = _load_trends(var) or _load_sentiment(var)
        if not price_data or not sent_data:
            continue
        px = price_data.get("prices", [])
        if len(px) < trend_window + horizon + 10:
            continue

        # Build forward-filled sentiment
        sent_series = sent_data.get("data", {}).get("daily_series", sent_data.get("series", []))
        raw_sent = {s.get("date", ""): s.get("avg_score", 0) for s in sent_series}
        sd_sorted = sorted(raw_sent.keys())
        sent_map = {}
        last_s = 0; si = 0
        for d in sorted(str(x["date"])[:10] for x in px):
            while si < len(sd_sorted) and sd_sorted[si] <= d:
                last_s = raw_sent[sd_sorted[si]]; si += 1
            sent_map[d] = last_s

        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        # Positions: 0=flat, 1=long, -1=short. Tracks last entry for both strategies
        pos_c = 0; entry_px_c = 0; entry_d_c = ""  # contrarian
        pos_m = 0; entry_px_m = 0; entry_d_m = ""  # momentum

        for i in range(trend_window, n - horizon):
            d = dates[i]
            if d < start_date:
                continue
            ss = sent_map.get(d, 0)
            px_now = closes[i]
            trend = (closes[i] - closes[i - trend_window]) / closes[i - trend_window] if closes[i - trend_window] else 0

            # --- Contrarian Entry ---
            # Price down + sentiment up → potential bottom → LONG
            c_long = trend < -0.01 and ss > 0.1
            # Price up + sentiment down → potential top → SHORT
            c_short = trend > 0.01 and ss < -0.1

            # --- Momentum Entry (comparison) ---
            # Price down + sentiment down → trend continuation → SHORT
            m_short = trend < -0.01 and ss < -0.1
            # Price up + sentiment up → trend continuation → LONG
            m_long = trend > 0.01 and ss > 0.1

            # Exit conditions (both strategies use fixed horizon)
            exit_idx = min(i + horizon, n - 1)

            # Process contrarian
            if pos_c != 0 and entry_d_c and i >= dates.index(entry_d_c) + horizon:
                exit_px = closes[exit_idx]
                if pos_c == 1:
                    pnl = (exit_px - entry_px_c) / entry_px_c * 100
                else:
                    pnl = (entry_px_c - exit_px) / entry_px_c * 100
                all_contrarian.append({"variety": var, "entry": entry_d_c, "exit": d,
                                       "direction": "long" if pos_c == 1 else "short",
                                       "pnl": round(pnl, 2),
                                       "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                                       "signal": "contrarian"})
                pos_c = 0

            if pos_c == 0:
                if c_long:
                    pos_c = 1; entry_px_c = px_now; entry_d_c = d
                elif c_short:
                    pos_c = -1; entry_px_c = px_now; entry_d_c = d

            # Process momentum
            if pos_m != 0 and entry_d_m and i >= dates.index(entry_d_m) + horizon:
                exit_px_m = closes[exit_idx]
                if pos_m == 1:
                    pnl = (exit_px_m - entry_px_m) / entry_px_m * 100
                else:
                    pnl = (entry_px_m - exit_px_m) / entry_px_m * 100
                all_momentum.append({"variety": var, "entry": entry_d_m, "exit": d,
                                     "direction": "long" if pos_m == 1 else "short",
                                     "pnl": round(pnl, 2),
                                     "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                                     "signal": "momentum"})
                pos_m = 0

            if pos_m == 0:
                if m_long:
                    pos_m = 1; entry_px_m = px_now; entry_d_m = d
                elif m_short:
                    pos_m = -1; entry_px_m = px_now; entry_d_m = d

    if not all_contrarian and not all_momentum:
        return {"total_trades": 0, "message": "No signals"}

    # Build curves
    all_dates = sorted(set(t["entry"] for t in all_contrarian + all_momentum))
    c_map = {}; m_map = {}
    for t in all_contrarian: c_map[t["entry"]] = c_map.get(t["entry"], 0) + t["pnl"]
    for t in all_momentum: m_map[t["entry"]] = m_map.get(t["entry"], 0) + t["pnl"]

    c_curve = []; m_curve = []; cum_c = 0; cum_m = 0
    for d in all_dates:
        cum_c += c_map.get(d, 0); cum_m += m_map.get(d, 0)
        c_curve.append(round(cum_c, 2)); m_curve.append(round(cum_m, 2))

    def wr(ts): return round(sum(1 for t in ts if t["outcome"]=="win")/len(ts), 3) if ts else 0

    # Compute advanced metrics per sub-strategy
    c_pnls = [t["pnl"] for t in all_contrarian]
    m_pnls = [t["pnl"] for t in all_momentum]
    all_entry_dates = sorted(set(t["entry"] for t in all_contrarian + all_momentum))
    td = max((datetime.strptime(all_entry_dates[-1],"%Y-%m-%d")-datetime.strptime(all_entry_dates[0],"%Y-%m-%d")).days*252//365, 20) if len(all_entry_dates)>=2 else 252
    adv_c = compute_advanced_metrics(trade_pnls=c_pnls, total_trading_days=td) if c_pnls else {}
    adv_m = compute_advanced_metrics(trade_pnls=m_pnls, total_trading_days=td) if m_pnls else {}

    return {
        "strategy": "contrarian_sentiment",
        "contrarian": {"trades": len(all_contrarian), "win_rate": wr(all_contrarian),
                        "total_pnl": round(c_curve[-1], 2) if c_curve else 0,
                        "label": "逆情绪(背离)", "advanced_metrics": adv_c},
        "consensus": {"trades": len(all_momentum), "win_rate": wr(all_momentum),
                      "total_pnl": round(m_curve[-1], 2) if m_curve else 0,
                      "label": "情绪共识", "advanced_metrics": adv_m},
        "dates": all_dates,
        "curves": {"contrarian": c_curve, "consensus": m_curve},
        "variety": variety or "all",
        "horizon": horizon, "trend_window": trend_window,
    }


# ═══════════════════════════════════════════════════════════════════
# Adaptive Sentiment Strategy (auto-choose contrarian vs momentum)
# ═══════════════════════════════════════════════════════════════════

def run_adaptive_sentiment(
    variety: str = "",
    horizon: int = 3,
    trend_window: int = 5,
    start_date: str = "2025-01-01",
) -> dict:
    """Adaptive sentiment: auto-choose contrarian or momentum based on conditions.

    Decision rules (empirically validated):
      - Trend > 2% AND sentiment agrees → MOMENTUM (crowd is right in trends)
      - Trend < 1% → CONTRARIAN (sentiment as reversal signal in ranges)
      - Sentiment DISAGREES with trend → CONTRARIAN (divergence = reversal)
      - Otherwise → MOMENTUM (default follow)
    """
    all_adaptive = []
    all_momentum = []  # baseline
    decisions = {"contrarian": 0, "momentum": 0}

    vlist = [variety] if variety else _get_all_varieties_with_data()

    for var in vlist:
        price_data = _load_price(var)
        sent_data = _load_trends(var) or _load_sentiment(var)
        if not price_data or not sent_data:
            continue
        px = price_data.get("prices", [])
        if len(px) < trend_window + horizon + 10:
            continue

        sent_series = sent_data.get("data", {}).get("daily_series", sent_data.get("series", []))
        raw_sent = {s.get("date", ""): s.get("avg_score", 0) for s in sent_series}
        sd_sorted = sorted(raw_sent.keys())
        sent_map = {}
        last_s = 0; si = 0
        for d in sorted(str(x["date"])[:10] for x in px):
            while si < len(sd_sorted) and sd_sorted[si] <= d:
                last_s = raw_sent[sd_sorted[si]]; si += 1
            sent_map[d] = last_s

        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        pos_a = 0; entry_px_a = 0; entry_d_a = ""  # adaptive
        pos_m = 0; entry_px_m = 0; entry_d_m = ""  # momentum baseline

        for i in range(trend_window, n - horizon):
            d = dates[i]
            if d < start_date:
                continue
            ss = sent_map.get(d, 0)
            px_now = closes[i]
            trend = (closes[i] - closes[i - trend_window]) / closes[i - trend_window] if closes[i - trend_window] else 0
            trend_pct = abs(trend) * 100
            divergence = (trend > 0.01 and ss < -0.1) or (trend < -0.01 and ss > 0.1)

            # Adaptive: choose contrarian or momentum based on conditions
            # Distinguish two types of divergence (empirically validated):
            #   Type A (涨+看空): 60% accuracy → HIGH confidence contrarian (top signal)
            #   Type B (跌+看多): 21% accuracy → SKIP (catching falling knife)
            diverge_bearish = (trend > 0.01 and ss < -0.1)  # price up + bearish = top signal
            diverge_bullish = (trend < -0.01 and ss > 0.1)  # price down + bullish = bottom fishing

            if diverge_bearish:
                # Price up + crowd bearish → SHORT (60% accurate across all varieties)
                use_contrarian = True
                a_long = False
                a_short = True
            elif diverge_bullish:
                # Price down + crowd bullish → treat by trend strength:
                if trend_pct > 3:
                    # Strong crash + bullish = denial phase → skip
                    use_contrarian = False
                    a_long = False; a_short = False
                    decisions["skipped"] = decisions.get("skipped", 0) + 1
                else:
                    # Moderate dip + bullish = potential bottom → CONTRARIAN long
                    use_contrarian = True
                    a_long = True; a_short = False
            elif trend_pct > 2:
                # Strong trend → MOMENTUM (follow the crowd)
                use_contrarian = False
            elif trend_pct < 1:
                # Weak/ranging trend → CONTRARIAN (reversal signals)
                use_contrarian = True
            else:
                # Default: MOMENTUM
                use_contrarian = False

            if diverge_bearish:
                decisions["contrarian"] += 1
            elif diverge_bullish and use_contrarian:
                decisions["contrarian"] += 1
            elif diverge_bullish:
                decisions["skipped"] = decisions.get("skipped", 0) + 1
            elif use_contrarian:
                decisions["contrarian"] += 1
                a_long = trend < -0.01 and ss > 0.1
                a_short = trend > 0.01 and ss < -0.1
            else:
                decisions["momentum"] += 1
                a_long = trend > 0.01 and ss > 0.1
                a_short = trend < -0.01 and ss < -0.1

            # Momentum baseline
            m_long = trend > 0.01 and ss > 0.1
            m_short = trend < -0.01 and ss < -0.1

            exit_idx = min(i + horizon, n - 1)
            exit_px = closes[exit_idx]

            # Process adaptive
            if pos_a != 0 and entry_d_a and i >= dates.index(entry_d_a) + horizon:
                if pos_a == 1:
                    pnl = (exit_px - entry_px_a) / entry_px_a * 100
                else:
                    pnl = (entry_px_a - exit_px) / entry_px_a * 100
                all_adaptive.append({"variety": var, "entry": entry_d_a, "exit": d,
                                     "direction": "long" if pos_a == 1 else "short",
                                     "pnl": round(pnl, 2),
                                     "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                                     "signal": "adaptive"})
                pos_a = 0

            if pos_a == 0:
                if a_long:
                    pos_a = 1; entry_px_a = px_now; entry_d_a = d
                elif a_short:
                    pos_a = -1; entry_px_a = px_now; entry_d_a = d

            # Process momentum
            if pos_m != 0 and entry_d_m and i >= dates.index(entry_d_m) + horizon:
                exit_px_m = closes[exit_idx]
                if pos_m == 1:
                    pnl = (exit_px_m - entry_px_m) / entry_px_m * 100
                else:
                    pnl = (entry_px_m - exit_px_m) / entry_px_m * 100
                all_momentum.append({"variety": var, "entry": entry_d_m, "exit": d,
                                     "direction": "long" if pos_m == 1 else "short",
                                     "pnl": round(pnl, 2),
                                     "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                                     "signal": "momentum"})
                pos_m = 0

            if pos_m == 0:
                if m_long:
                    pos_m = 1; entry_px_m = px_now; entry_d_m = d
                elif m_short:
                    pos_m = -1; entry_px_m = px_now; entry_d_m = d

    if not all_adaptive and not all_momentum:
        return {"total_trades": 0, "message": "No signals"}

    all_dates = sorted(set(t["entry"] for t in all_adaptive + all_momentum))
    a_map = {}; m_map = {}
    for t in all_adaptive: a_map[t["entry"]] = a_map.get(t["entry"], 0) + t["pnl"]
    for t in all_momentum: m_map[t["entry"]] = m_map.get(t["entry"], 0) + t["pnl"]

    a_curve = []; m_curve = []; cum_a = 0; cum_m = 0
    for d in all_dates:
        cum_a += a_map.get(d, 0); cum_m += m_map.get(d, 0)
        a_curve.append(round(cum_a, 2)); m_curve.append(round(cum_m, 2))

    def wr(ts): return round(sum(1 for t in ts if t["outcome"]=="win")/len(ts), 3) if ts else 0

    # Compute advanced metrics per sub-strategy
    a_pnls = [t["pnl"] for t in all_adaptive]
    m_pnls = [t["pnl"] for t in all_momentum]
    all_entry_dates2 = sorted(set(t["entry"] for t in all_adaptive + all_momentum))
    td2 = max((datetime.strptime(all_entry_dates2[-1],"%Y-%m-%d")-datetime.strptime(all_entry_dates2[0],"%Y-%m-%d")).days*252//365, 20) if len(all_entry_dates2)>=2 else 252
    adv_a = compute_advanced_metrics(trade_pnls=a_pnls, total_trading_days=td2) if a_pnls else {}
    adv_m2 = compute_advanced_metrics(trade_pnls=m_pnls, total_trading_days=td2) if m_pnls else {}

    return {
        "strategy": "adaptive_sentiment",
        "adaptive": {"trades": len(all_adaptive), "win_rate": wr(all_adaptive),
                      "total_pnl": round(a_curve[-1], 2) if a_curve else 0,
                      "label": "自适应情绪", "advanced_metrics": adv_a},
        "consensus": {"trades": len(all_momentum), "win_rate": wr(all_momentum),
                      "total_pnl": round(m_curve[-1], 2) if m_curve else 0,
                      "label": "情绪共识", "advanced_metrics": adv_m2},
        "decisions": decisions,
        "dates": all_dates,
        "curves": {"adaptive": a_curve, "consensus": m_curve},
        "variety": variety or "all",
        "horizon": horizon, "trend_window": trend_window,
    }


# ═══════════════════════════════════════════════════════════════════
# Donchian Channel Breakout (Turtle-style)
# ═══════════════════════════════════════════════════════════════════

def run_donchian_strategy(variety="", period=20, start_date="2025-01-01"):
    """Donchian Channel Breakout — Turtle Trading classic.
    Entry: price breaks above N-day high → LONG; breaks below N-day low → SHORT
    Exit: price crosses back below/below the opposite side
    """
    all_trades = []
    vlist = [variety] if variety else _get_all_varieties_with_data()[:10]
    for var in vlist:
        p = _load_price(var)
        if not p or len(p.get("prices",[])) < period + 5: continue
        px = p["prices"]; closes = [float(x["close"]) for x in px]
        highs = [float(x["high"]) for x in px]
        lows = [float(x["low"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]; n = len(closes)

        pos = 0; entry_px = 0; entry_d = ""
        for i in range(period, n):
            d = dates[i]
            if d < start_date: continue
            hh = max(highs[i-period:i])
            ll = min(lows[i-period:i])
            cu = closes[i] > hh  # Breakout up
            cd = closes[i] < ll  # Breakout down

            if pos == 1 and cd and entry_px:
                pnl = (closes[i] - entry_px) / entry_px * 100
                all_trades.append({"variety": var, "entry": entry_d, "exit": d, "direction": "long",
                                   "pnl": round(pnl,2), "outcome": "win" if pnl>0.15 else ("loss" if pnl<-0.15 else "breakeven"), "signal": "donchian"})
                pos = 0
            elif pos == -1 and cu and entry_px:
                pnl = (entry_px - closes[i]) / entry_px * 100
                all_trades.append({"variety": var, "entry": entry_d, "exit": d, "direction": "short",
                                   "pnl": round(pnl,2), "outcome": "win" if pnl>0.15 else ("loss" if pnl<-0.15 else "breakeven"), "signal": "donchian"})
                pos = 0

            if pos == 0:
                if cu: pos = 1; entry_px = closes[i]; entry_d = d
                elif cd: pos = -1; entry_px = closes[i]; entry_d = d

        if pos != 0 and entry_px:
            final = closes[-1]
            pnl = (final-entry_px)/entry_px*100 if pos==1 else (entry_px-final)/entry_px*100
            all_trades.append({"variety": var, "entry": entry_d, "exit": dates[-1], "direction": "long" if pos==1 else "short",
                               "pnl": round(pnl,2), "outcome": "win" if pnl>0.15 else ("loss" if pnl<-0.15 else "breakeven"), "signal": "donchian"})

    if not all_trades: return {"total_trades": 0}
    wins = [t for t in all_trades if t["outcome"]=="win"]
    pnls = [t["pnl"] for t in all_trades]; avg = sum(pnls)/len(pnls)
    std = (sum((x-avg)**2 for x in pnls)/len(pnls))**0.5 if len(pnls)>1 else 1
    cum = 0; peak = 0; dd = 0
    for p in pnls: cum+=p; peak=max(peak,cum); dd=max(dd,peak-cum)
    # Estimate trading days
    entry_dates = sorted(set(t["entry"] for t in all_trades))
    td = max((datetime.strptime(entry_dates[-1],"%Y-%m-%d")-datetime.strptime(entry_dates[0],"%Y-%m-%d")).days*252//365, len(pnls)*period) if len(entry_dates)>=2 else len(pnls)*period
    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=td)
    return {"strategy":"donchian","total_trades":len(all_trades),
            "win_count":len(wins),"loss_count":len(all_trades)-len(wins),
            "win_rate":round(len(wins)/len(all_trades),3) if all_trades else 0,
            "avg_pnl_pct":round(avg,2),"sharpe_like":advanced["sharpe_like"],"max_drawdown_pct":advanced["max_drawdown_pct"],
            "long_trades":len([t for t in all_trades if t["direction"]=="long"]),
            "short_trades":len([t for t in all_trades if t["direction"]=="short"]),
            "period":period,"advanced_metrics":advanced,"recent_trades":all_trades}


# ═══════════════════════════════════════════════════════════════════
# Time-Series Momentum (Moskowitz et al. 2012)
# ═══════════════════════════════════════════════════════════════════

def run_momentum_strategy(variety="", lookback=5, hold=3, start_date="2025-01-01"):
    """Time-series momentum: go long if N-day return > 0, short if < 0, hold M days.
    Re-evaluates every day — much higher frequency than crossover strategies.
    """
    all_trades = []
    vlist = [variety] if variety else _get_all_varieties_with_data()[:10]
    for var in vlist:
        p = _load_price(var)
        if not p or len(p.get("prices",[])) < lookback + hold + 5: continue
        px = p["prices"]; closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]; n = len(closes)

        pos = 0; entry_px = 0; entry_d = ""; entry_i = 0
        for i in range(lookback, n):
            d = dates[i]
            if d < start_date: continue
            ret = (closes[i] - closes[i-lookback]) / closes[i-lookback] if closes[i-lookback] else 0
            sig = 1 if ret > 0.005 else (-1 if ret < -0.005 else 0)

            # Exit after holding `hold` days
            if pos != 0 and entry_i > 0 and i - entry_i >= hold:
                exit_px = closes[i]
                pnl = (exit_px-entry_px)/entry_px*100 if pos==1 else (entry_px-exit_px)/entry_px*100
                all_trades.append({"variety": var, "entry": entry_d, "exit": d, "direction": "long" if pos==1 else "short",
                                   "pnl": round(pnl,2), "outcome": "win" if pnl>0.15 else ("loss" if pnl<-0.15 else "breakeven"), "signal": "momentum"})
                pos = 0

            # Entry
            if pos == 0 and sig != 0:
                pos = sig; entry_px = closes[i]; entry_d = d; entry_i = i

        if pos != 0 and entry_px:
            final = closes[-1]
            pnl = (final-entry_px)/entry_px*100 if pos==1 else (entry_px-final)/entry_px*100
            all_trades.append({"variety": var, "entry": entry_d, "exit": dates[-1], "direction": "long" if pos==1 else "short",
                               "pnl": round(pnl,2), "outcome": "win" if pnl>0.15 else ("loss" if pnl<-0.15 else "breakeven"), "signal": "momentum"})

    if not all_trades: return {"total_trades": 0}
    wins = [t for t in all_trades if t["outcome"]=="win"]
    pnls = [t["pnl"] for t in all_trades]; avg = sum(pnls)/len(pnls)
    std = (sum((x-avg)**2 for x in pnls)/len(pnls))**0.5 if len(pnls)>1 else 1
    cum = 0; peak = 0; dd = 0
    for p in pnls: cum+=p; peak=max(peak,cum); dd=max(dd,peak-cum)
    entry_dates = sorted(set(t["entry"] for t in all_trades))
    td = max((datetime.strptime(entry_dates[-1],"%Y-%m-%d")-datetime.strptime(entry_dates[0],"%Y-%m-%d")).days*252//365, len(pnls)*hold) if len(entry_dates)>=2 else len(pnls)*hold
    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=td)
    return {"strategy":"momentum","total_trades":len(all_trades),
            "win_count":len(wins),"loss_count":len(all_trades)-len(wins),
            "win_rate":round(len(wins)/len(all_trades),3) if all_trades else 0,
            "avg_pnl_pct":round(avg,2),"sharpe_like":advanced["sharpe_like"],"max_drawdown_pct":advanced["max_drawdown_pct"],
            "lookback":lookback,"hold":hold,"advanced_metrics":advanced,"recent_trades":all_trades}


# ═══════════════════════════════════════════════════════════════════
# Momentum + Adaptive Sentiment Fusion
# ═══════════════════════════════════════════════════════════════════

def run_momentum_adaptive(
    variety: str = "",
    lookback: int = 5,
    hold: int = 3,
    trend_window: int = 5,
    start_date: str = "2025-01-01",
) -> dict:
    """Pure price momentum + adaptive sentiment overlay.

    Baseline: pure price momentum (N-day return → long/short, no sentiment).
    Adaptive: switches to contrarian when sentiment diverges from price trend.

    Decision tree (per day):
      1. Divergence (trend vs sentiment opposite):
         - Type A (涨+看空): SHORT — contrarian, 60% accurate top signal
         - Type B (跌+看多) + strong crash(>3%): SKIP — catching falling knife
         - Type B (跌+看多) + moderate dip: LONG — contrarian bottom
      2. No divergence:
         - Strong trend (>2%): pure momentum (trust the price)
         - Weak/ranging (<1%): contrarian (price noise, sentiment as signal)
         - Moderate (1-2%): pure momentum (default)

    Returns:
        adaptive: momentum + adaptive overlay trades
        momentum_baseline: pure momentum only (same as run_momentum_strategy)
        curves, stats, decisions count
    """
    all_adaptive = []     # Momentum + adaptive overlay
    all_baseline = []     # Pure momentum baseline
    decisions = {"momentum": 0, "contrarian": 0, "skipped": 0, "quality_skip": 0}

    vlist = [variety] if variety else _get_all_varieties_with_data()[:10]

    for var in vlist:
        price_data = _load_price(var)
        sent_data = _load_trends(var) or _load_sentiment(var)
        if not price_data or not sent_data:
            continue
        px = price_data.get("prices", [])
        if len(px) < max(lookback, trend_window) + hold + 10:
            continue

        # Build forward-filled sentiment map
        sent_series = sent_data.get("data", {}).get("daily_series", sent_data.get("series", []))
        raw_sent = {s.get("date", ""): s.get("avg_score", 0) for s in sent_series}
        sd_sorted = sorted(raw_sent.keys())
        sent_map = {}
        last_s = 0; si = 0
        for d in sorted(str(x["date"])[:10] for x in px):
            while si < len(sd_sorted) and sd_sorted[si] <= d:
                last_s = raw_sent[sd_sorted[si]]; si += 1
            sent_map[d] = last_s

        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        pos_a = 0; entry_px_a = 0; entry_d_a = ""; entry_i_a = 0  # adaptive
        pos_b = 0; entry_px_b = 0; entry_d_b = ""; entry_i_b = 0  # baseline

        # Rolling baseline quality tracker (last N closed trades)
        baseline_recent_pnls = []  # stores PnL of last 10 closed baseline trades
        BASELINE_WINDOW = 10

        for i in range(max(lookback, trend_window), n):
            d = dates[i]
            if d < start_date:
                continue
            ss = sent_map.get(d, 0)
            px_now = closes[i]

            # Pure momentum signal (lookback return)
            mom_ret = (closes[i] - closes[i - lookback]) / closes[i - lookback] if closes[i - lookback] else 0
            mom_sig = 1 if mom_ret > 0.005 else (-1 if mom_ret < -0.005 else 0)

            # Trend and divergence
            trend = (closes[i] - closes[i - trend_window]) / closes[i - trend_window] if closes[i - trend_window] else 0
            trend_pct = abs(trend) * 100

            diverge_bearish = (trend > 0.01 and ss < -0.1)   # price up + bearish = top signal
            diverge_bullish = (trend < -0.01 and ss > 0.1)   # price down + bullish = bottom signal

            # ── Baseline quality check ──
            baseline_is_good = False
            if len(baseline_recent_pnls) >= 5:
                recent_wr = sum(1 for p in baseline_recent_pnls if p > 0.15) / len(baseline_recent_pnls)
                recent_total = sum(baseline_recent_pnls)
                baseline_is_good = recent_total > 0 and recent_wr >= 0.45

            # ── Adaptive decision (quality-gated) ──
            if diverge_bearish:
                # Type A (涨+看空): always contrarian — empirically 60% accurate
                a_long = False; a_short = True
                decisions["contrarian"] += 1
            elif diverge_bullish:
                if baseline_is_good:
                    # Baseline is profitable → trust it, don't fade
                    a_long = mom_sig == 1; a_short = mom_sig == -1
                    decisions["quality_skip"] += 1
                elif trend_pct > 3:
                    # Strong crash + bullish + baseline losing = denial → skip
                    a_long = False; a_short = False
                    decisions["skipped"] += 1
                else:
                    # Moderate dip + bullish + baseline losing → contrarian LONG
                    a_long = True; a_short = False
                    decisions["contrarian"] += 1
            elif trend_pct > 2:
                # Strong trend: always pure momentum
                a_long = mom_sig == 1; a_short = mom_sig == -1
                decisions["momentum"] += 1
            elif baseline_is_good:
                # Weak/moderate trend + baseline healthy → stick to momentum
                a_long = mom_sig == 1; a_short = mom_sig == -1
                decisions["momentum"] += 1
            else:
                # Weak/moderate trend + baseline struggling → contrarian (fade sentiment)
                a_long = ss < -0.1; a_short = ss > 0.1
                decisions["contrarian"] += 1

            # ── Baseline: pure momentum only ──
            b_long = mom_sig == 1; b_short = mom_sig == -1

            # Exit after holding `hold` days
            exit_idx = min(i + hold, n - 1)
            exit_px = closes[exit_idx]

            def _close(pos, entry_px, entry_d, entry_i, px_now, d, direction, trades_list, label):
                if pos == 1:
                    pnl = (px_now - entry_px) / entry_px * 100
                else:
                    pnl = (entry_px - px_now) / entry_px * 100
                trades_list.append({
                    "variety": var, "entry": entry_d, "exit": d,
                    "direction": direction, "pnl": round(pnl, 2),
                    "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                    "signal": label,
                })

            # Process adaptive
            if pos_a != 0 and entry_d_a and i - entry_i_a >= hold:
                _close(pos_a, entry_px_a, entry_d_a, entry_i_a, exit_px, d,
                       "long" if pos_a == 1 else "short", all_adaptive, "mom_ad")
                pos_a = 0
            if pos_a == 0:
                if a_long:
                    pos_a = 1; entry_px_a = px_now; entry_d_a = d; entry_i_a = i
                elif a_short:
                    pos_a = -1; entry_px_a = px_now; entry_d_a = d; entry_i_a = i

            # Process baseline — also track PnL for quality gating
            if pos_b != 0 and entry_d_b and i - entry_i_b >= hold:
                if pos_b == 1:
                    bl_pnl = (exit_px - entry_px_b) / entry_px_b * 100
                else:
                    bl_pnl = (entry_px_b - exit_px) / entry_px_b * 100
                all_baseline.append({
                    "variety": var, "entry": entry_d_b, "exit": d,
                    "direction": "long" if pos_b == 1 else "short",
                    "pnl": round(bl_pnl, 2),
                    "outcome": "win" if bl_pnl > 0.15 else ("loss" if bl_pnl < -0.15 else "breakeven"),
                    "signal": "momentum",
                })
                # Track for quality gating
                baseline_recent_pnls.append(bl_pnl)
                if len(baseline_recent_pnls) > BASELINE_WINDOW:
                    baseline_recent_pnls.pop(0)
                pos_b = 0
            if pos_b == 0:
                if b_long:
                    pos_b = 1; entry_px_b = px_now; entry_d_b = d; entry_i_b = i
                elif b_short:
                    pos_b = -1; entry_px_b = px_now; entry_d_b = d; entry_i_b = i

        # Close final positions
        final_px = closes[-1]
        if pos_a != 0 and entry_px_a:
            pnl = (final_px - entry_px_a) / entry_px_a * 100 if pos_a == 1 else (entry_px_a - final_px) / entry_px_a * 100
            all_adaptive.append({"variety": var, "entry": entry_d_a, "exit": dates[-1],
                                "direction": "long" if pos_a == 1 else "short",
                                "pnl": round(pnl, 2),
                                "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                                "signal": "mom_ad"})
        if pos_b != 0 and entry_px_b:
            pnl = (final_px - entry_px_b) / entry_px_b * 100 if pos_b == 1 else (entry_px_b - final_px) / entry_px_b * 100
            all_baseline.append({"variety": var, "entry": entry_d_b, "exit": dates[-1],
                                "direction": "long" if pos_b == 1 else "short",
                                "pnl": round(pnl, 2),
                                "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                                "signal": "momentum"})

    if not all_adaptive and not all_baseline:
        return {"total_trades": 0, "message": "No signals"}

    # Build cumulative curves
    all_dates = sorted(set(t["entry"] for t in all_adaptive + all_baseline))
    a_map = {}; b_map = {}
    for t in all_adaptive: a_map[t["entry"]] = a_map.get(t["entry"], 0) + t["pnl"]
    for t in all_baseline: b_map[t["entry"]] = b_map.get(t["entry"], 0) + t["pnl"]

    a_curve = []; b_curve = []; cum_a = 0; cum_b = 0
    for d in all_dates:
        cum_a += a_map.get(d, 0); cum_b += b_map.get(d, 0)
        a_curve.append(round(cum_a, 2)); b_curve.append(round(cum_b, 2))

    def wr(ts): return round(sum(1 for t in ts if t["outcome"]=="win")/len(ts), 3) if ts else 0

    # Compute advanced metrics per sub-strategy
    a_pnls = [t["pnl"] for t in all_adaptive]
    b_pnls = [t["pnl"] for t in all_baseline]
    all_eds = sorted(set(t["entry"] for t in all_adaptive + all_baseline))
    td_ma = max((datetime.strptime(all_eds[-1],"%Y-%m-%d")-datetime.strptime(all_eds[0],"%Y-%m-%d")).days*252//365, 20) if len(all_eds)>=2 else 252
    adv_a = compute_advanced_metrics(trade_pnls=a_pnls, total_trading_days=td_ma) if a_pnls else {}
    adv_b = compute_advanced_metrics(trade_pnls=b_pnls, total_trading_days=td_ma) if b_pnls else {}

    return {
        "strategy": "momentum_adaptive",
        "adaptive": {"trades": len(all_adaptive), "win_rate": wr(all_adaptive),
                      "total_pnl": round(a_curve[-1], 2) if a_curve else 0,
                      "label": "动量+自适应", "advanced_metrics": adv_a},
        "momentum_baseline": {"trades": len(all_baseline), "win_rate": wr(all_baseline),
                              "total_pnl": round(b_curve[-1], 2) if b_curve else 0,
                              "label": "纯动量(基线)", "advanced_metrics": adv_b},
        "decisions": decisions,
        "dates": all_dates,
        "curves": {"adaptive": a_curve, "momentum_baseline": b_curve},
        "variety": variety or "all",
        "lookback": lookback, "hold": hold, "trend_window": trend_window,
    }


# ═══════════════════════════════════════════════════════════════════
# Risk Management wrapper for any strategy
# ═══════════════════════════════════════════════════════════════════

def apply_risk_management(
    trades: list,
    prices: list[dict],
    stop_loss_pct: float = 0,
    trailing_stop_pct: float = 0,
    max_holding: int = 20,
) -> list:
    """Apply stop-loss and risk controls to a list of trades.

    Args:
        trades: List of {entry, direction, entry_px (implied), ...}
        prices: OHLCV price data for stop-loss checks
        stop_loss_pct: Fixed stop-loss % from entry (0=disabled)
        trailing_stop_pct: Trailing stop % from peak (0=disabled)
        max_holding: Max holding days before forced exit

    Returns:
        Modified trades with stop-loss exits applied
    """
    if not stop_loss_pct and not trailing_stop_pct:
        return trades

    # Build price timeline
    price_map = {}
    for p in prices:
        price_map[str(p["date"])[:10]] = float(p["close"])

    modified = []
    for t in trades:
        entry_d = t.get("entry", "")
        direction = t.get("direction", "long")
        entry_px = price_map.get(entry_d, 0)
        if not entry_px:
            modified.append(t)
            continue

        # Check if price hit stop-loss before original exit
        exit_d = t.get("exit", "")
        exit_px = price_map.get(exit_d, entry_px)
        dates = sorted([d for d in price_map if entry_d <= d <= exit_d])
        peak_px = entry_px
        stopped_out = False
        stop_px = 0

        for d in dates:
            px = price_map[d]
            if direction == "long":
                # Fixed stop: exit if price drops below entry * (1 - stop_loss%)
                if stop_loss_pct and px <= entry_px * (1 - stop_loss_pct / 100):
                    stopped_out = True; stop_px = px; exit_d = d; break
                # Trailing stop: update peak, exit if falls below peak * (1 - trailing%)
                if px > peak_px:
                    peak_px = px
                trail_level = peak_px * (1 - trailing_stop_pct / 100)
                # Never let trailing stop go below entry (protect breakeven)
                trail_level = max(trail_level, entry_px)
                if trailing_stop_pct and px <= trail_level:
                    stopped_out = True; stop_px = px; exit_d = d; break
            else:  # short
                if stop_loss_pct and px >= entry_px * (1 + stop_loss_pct / 100):
                    stopped_out = True; stop_px = px; exit_d = d; break
                if px < peak_px:
                    peak_px = px
                trail_level = peak_px * (1 + trailing_stop_pct / 100)
                # Never let trailing stop go above entry (protect breakeven)
                trail_level = min(trail_level, entry_px)
                if trailing_stop_pct and px >= trail_level:
                    stopped_out = True; stop_px = px; exit_d = d; break

        if stopped_out:
            if direction == "long":
                pnl = (stop_px - entry_px) / entry_px * 100
            else:
                pnl = (entry_px - stop_px) / entry_px * 100
            t = dict(t)
            t["pnl"] = round(pnl, 2)
            t["exit"] = exit_d
            t["outcome"] = "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven")
            t["stopped_out"] = True

        modified.append(t)

    return modified


# ═══════════════════════════════════════════════════════════════════
# P3: Simulated Trading Backtest
# ═══════════════════════════════════════════════════════════════════

@dataclass
@dataclass
class TradeRecord:
    variety: str
    entry_date: str
    exit_date: str
    direction: str
    signal_value: float
    entry_price: float
    exit_price: float
    pnl_pct: float
    outcome: str  # win / loss / breakeven
    horizon: int


# ═══════════════════════════════════════════════════════════════════
# Shared Advanced Metrics Calculator
# ═══════════════════════════════════════════════════════════════════

def compute_advanced_metrics(
    trade_pnls: list[float],
    total_trading_days: int = 252,
    daily_returns: list[float] | None = None,
) -> dict:
    """Compute comprehensive backtest metrics from trade PnL data.

    Args:
        trade_pnls: Per-trade PnL percentages (e.g., [1.5, -0.8, 2.1, ...])
        total_trading_days: Estimated trading days in the backtest period (252 = 1 year)
        daily_returns: Optional list of daily returns for precise volatility calculation

    Returns dict with:
        sharpe_ratio, sortino_ratio, calmar_ratio,
        annualized_return, annualized_volatility, volatility_pct,
        max_drawdown_pct, max_drawdown_duration,
        profit_factor, avg_win_pct, avg_loss_pct, win_loss_ratio,
        sharpe_like (backward compat)
    """
    n = len(trade_pnls)
    RISK_FREE_RATE = 2.5  # China 10Y government bond ≈ 2.5% annual

    if n == 0:
        return {
            "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "annualized_return": 0.0, "annualized_volatility": 0.0,
            "max_drawdown_pct": 0.0, "max_drawdown_duration": 0,
            "profit_factor": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "win_loss_ratio": 0.0, "volatility_pct": 0.0, "sharpe_like": 0.0,
        }

    # ── Basic stats ──
    wins = [p for p in trade_pnls if p > 0.1]
    losses = [p for p in trade_pnls if p < -0.1]
    breakevens = [p for p in trade_pnls if -0.1 <= p <= 0.1]

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else (99.0 if avg_win > 0 else 0.0)

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    total_return_pct = sum(trade_pnls)
    avg_pnl = total_return_pct / n
    std_pnl = math.sqrt(sum((p - avg_pnl) ** 2 for p in trade_pnls) / n) if n > 1 else 0.0

    # ── Annualization ──
    total_trading_days = max(total_trading_days, 1)
    total_return_decimal = total_return_pct / 100.0

    if total_return_decimal > -1.0:
        annualized_return = ((1 + total_return_decimal) ** (252.0 / total_trading_days) - 1) * 100
    else:
        annualized_return = -100.0  # total loss

    # Trades per year for scaling trade-level std → annual
    trades_per_year = n * 252.0 / total_trading_days if total_trading_days > 0 else n

    # Volatility: use daily returns if available, otherwise scale from trade PnLs
    if daily_returns and len(daily_returns) > 1:
        daily_std = math.sqrt(sum((r - sum(daily_returns) / len(daily_returns)) ** 2
                                  for r in daily_returns) / len(daily_returns))
        annualized_vol = daily_std * math.sqrt(252) * 100  # convert to %
    else:
        annualized_vol = std_pnl * math.sqrt(trades_per_year) if trades_per_year > 0 else 0.0

    # ── Sharpe Ratio ──
    sharpe = ((annualized_return - RISK_FREE_RATE) / annualized_vol
              if annualized_vol > 0 else (9.99 if annualized_return > RISK_FREE_RATE else 0.0))

    # ── Sortino Ratio (downside deviation only) ──
    downside_returns = [p for p in trade_pnls if p < 0]
    if downside_returns:
        downside_var = sum(p ** 2 for p in downside_returns) / len(trade_pnls)
        downside_std_trade = math.sqrt(downside_var)
        downside_std_annual = downside_std_trade * math.sqrt(trades_per_year) if trades_per_year > 0 else 0.0
        sortino = ((annualized_return - RISK_FREE_RATE) / downside_std_annual
                   if downside_std_annual > 0 else (9.99 if annualized_return > RISK_FREE_RATE else 0.0))
    else:
        sortino = 9.99 if annualized_return > RISK_FREE_RATE else 0.0

    # ── Max Drawdown & Duration ──
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    current_dd_len = 0
    max_dd_duration = 0

    for p in trade_pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
            current_dd_len = 0
        else:
            current_dd_len += 1
            max_dd_duration = max(max_dd_duration, current_dd_len)
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # ── Calmar Ratio ──
    calmar = (annualized_return / max_dd
              if max_dd > 0 else (99.0 if annualized_return > 0 else 0.0))

    # ── Legacy sharpe_like for backward compat ──
    sharpe_like = round(avg_pnl / std_pnl, 2) if std_pnl > 0 else 0.0

    return {
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "calmar_ratio": round(calmar, 2),
        "annualized_return": round(annualized_return, 1),
        "annualized_volatility": round(annualized_vol, 1),
        "volatility_pct": round(std_pnl, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_duration": max_dd_duration,
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "win_loss_ratio": round(win_loss_ratio, 2),
        "sharpe_like": sharpe_like,
    }


def run_simulated_trading(
    variety: str = "",
    horizon: int = 3,
    signal_threshold: float = 0.2,
    start_date: str = "2025-01-01",
) -> dict:
    """Simulated trading based on sentiment signals.

    Rules:
    - If avg_score > +threshold at day T → go LONG at T+1 open, exit at T+horizon close
    - If avg_score < -threshold at day T → go SHORT at T+1 open, exit at T+horizon close
    - Track all trades and compute win rate, avg return, Sharpe, max drawdown.
    """
    all_trades = []
    varieties_to_test = [variety] if variety else _get_all_varieties_with_data()

    for var in varieties_to_test:
        sent_data = _load_sentiment(var)
        price_data = _load_price(var)
        if not sent_data or not price_data:
            continue

        series = sent_data.get("data", {}).get("daily_series", [])
        prices_raw = price_data.get("prices", [])
        if len(series) < 20:
            continue

        price_map = {}
        for p in prices_raw:
            d = str(p["date"])[:10]
            price_map[d] = float(p["close"])

        # Build date-aligned arrays
        dates = []
        scores = []
        for s in series:
            d = s["date"]
            if d in price_map and d >= start_date:
                dates.append(d)
                scores.append(s.get("avg_score", 0))

        # Generate trades
        for i in range(len(dates) - horizon):
            signal = scores[i]
            if abs(signal) < signal_threshold:
                continue

            direction = "long" if signal > 0 else "short"
            entry_date = dates[i]
            exit_date = dates[i + horizon] if i + horizon < len(dates) else dates[-1]

            entry_px = price_map.get(entry_date, 0)
            exit_px = price_map.get(exit_date, 0)
            if not entry_px or not exit_px:
                continue

            if direction == "long":
                pnl = (exit_px - entry_px) / entry_px * 100
                outcome = "win" if pnl > 0.1 else ("loss" if pnl < -0.1 else "breakeven")
            else:
                pnl = (entry_px - exit_px) / entry_px * 100
                outcome = "win" if pnl > 0.1 else ("loss" if pnl < -0.1 else "breakeven")

            all_trades.append(TradeRecord(
                variety=var, entry_date=entry_date, exit_date=exit_date,
                direction=direction, signal_value=round(signal, 3),
                entry_price=round(entry_px, 2), exit_price=round(exit_px, 2),
                pnl_pct=round(pnl, 2), outcome=outcome,
                horizon=(dates.index(exit_date) - dates.index(entry_date)),
            ))

    if not all_trades:
        return {"total_trades": 0, "message": "No trades generated"}

    # Compute statistics
    wins = [t for t in all_trades if t.outcome == "win"]
    losses = [t for t in all_trades if t.outcome == "loss"]
    long_trades = [t for t in all_trades if t.direction == "long"]
    short_trades = [t for t in all_trades if t.direction == "short"]

    pnls = [t.pnl_pct for t in all_trades]

    # Estimate trading days from trade date range
    all_entry_dates = sorted(set(t.entry_date for t in all_trades))
    if len(all_entry_dates) >= 2:
        d0 = datetime.strptime(all_entry_dates[0], "%Y-%m-%d")
        d1 = datetime.strptime(all_entry_dates[-1], "%Y-%m-%d")
        trading_days = max((d1 - d0).days * 252 // 365, len(pnls))
    else:
        trading_days = max(len(pnls) * horizon, 20)

    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=trading_days)

    avg_pnl = sum(pnls) / len(pnls) if pnls else 0

    # By variety
    by_variety = defaultdict(lambda: {"trades": 0, "wins": 0, "avg_pnl": 0})
    for t in all_trades:
        by_variety[t.variety]["trades"] += 1
        if t.outcome == "win":
            by_variety[t.variety]["wins"] += 1
    for v in by_variety:
        by_variety[v]["win_rate"] = round(
            by_variety[v]["wins"] / by_variety[v]["trades"], 3
        ) if by_variety[v]["trades"] else 0

    return {
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(
            len([t for t in long_trades if t.outcome == "win"]) / len(long_trades), 3
        ) if long_trades else 0,
        "short_win_rate": round(
            len([t for t in short_trades if t.outcome == "win"]) / len(short_trades), 3
        ) if short_trades else 0,
        "horizon": horizon,
        "signal_threshold": signal_threshold,
        "by_variety": dict(by_variety),
        "advanced_metrics": advanced,
        "recent_trades": [
            {"variety": t.variety, "entry": t.entry_date, "exit": t.exit_date,
             "dir": t.direction, "pnl": t.pnl_pct, "outcome": t.outcome}
            for t in all_trades
        ],
    }


# ═══════════════════════════════════════════════════════════════════
# P3-B: Sentiment Trailing Exit Strategy
# ═══════════════════════════════════════════════════════════════════
# Factor builders
# ═══════════════════════════════════════════════════════════════════

def _compute_fundamental_factors(prices: list[dict]) -> dict[str, list]:
    """Compute fundamental/technical factors from OHLCV price data.

    Returns dict with arrays aligned to input prices:
      - ma_5, ma_20: simple moving averages
      - ma_signal: 1 if price > MA20 (uptrend), -1 if below (downtrend)
      - momentum_5: 5-day return (pct)
      - vol_ratio: volume / 20-day avg volume (>1 = high activity)
      - vol_signal: 1 if vol_ratio > 1.2 (active), -1 if < 0.8 (quiet)
      - fund_score: combined fundamental score in [-1, +1]
    """
    n = len(prices)
    closes = [p["close"] for p in prices]
    volumes = [p.get("volume", 0) for p in prices]

    ma_5 = []
    ma_20 = []
    ma_signal = []
    momentum_5 = []
    vol_ratio = []
    vol_signal = []

    for i in range(n):
        # MA
        if i >= 4:
            ma_5.append(sum(closes[i-4:i+1]) / 5)
        else:
            ma_5.append(closes[i])
        if i >= 19:
            ma_20.append(sum(closes[i-19:i+1]) / 20)
        else:
            ma_20.append(closes[i])

        # MA signal
        ma_signal.append(1.0 if closes[i] > ma_20[i] else -1.0)

        # Momentum
        if i >= 5:
            momentum_5.append((closes[i] - closes[i-5]) / closes[i-5] if closes[i-5] else 0)
        else:
            momentum_5.append(0.0)

        # Volume
        if volumes[i] > 0:
            if i >= 20:
                avg_vol = sum(volumes[i-19:i+1]) / 20
                vr = volumes[i] / avg_vol if avg_vol > 0 else 1.0
            else:
                vr = 1.0
        else:
            vr = 1.0
        vol_ratio.append(vr)
        vol_signal.append(1.0 if vr > 1.2 else (-1.0 if vr < 0.8 else 0.0))

    # Combined fundamental score: 60% trend + 25% momentum + 15% volume
    fund_score = []
    for i in range(n):
        m = momentum_5[i]
        # Normalize momentum to [-1, 1]
        m_norm = max(-1.0, min(1.0, m * 20))  # 5% return → score=1.0
        fs = 0.6 * ma_signal[i] + 0.25 * m_norm + 0.15 * vol_signal[i]
        fund_score.append(round(fs, 3))

    return {
        "ma_5": ma_5, "ma_20": ma_20, "ma_signal": ma_signal,
        "momentum_5": momentum_5, "vol_ratio": vol_ratio,
        "vol_signal": vol_signal, "fund_score": fund_score,
    }


# ═══════════════════════════════════════════════════════════════════
# P3-C: Multi-Strategy Comparison (Fundamental / Sentiment / Combined)
# ═══════════════════════════════════════════════════════════════════

def run_strategy_comparison(
    variety: str = "RB",
    horizon: int = 5,
    signal_threshold: float = 0.2,
    fund_threshold: float = 0.3,
) -> dict:
    """Run three strategies and return PnL curves for comparison.

    Strategies:
      1. Pure Fundamental: trade on MA crossover + momentum + volume
         - fund_score > +threshold → go LONG
         - fund_score < -threshold → go SHORT
      2. Fundamental + Sentiment: both must agree
         - fund_score > +threshold AND sent_score > 0 → LONG
         - fund_score < -threshold AND sent_score < 0 → SHORT
      3. Buy & Hold (price curve): benchmark

    All strategies use fixed horizon for exit.
    """
    all_trades_fund = []
    all_trades_combo = []

    sent_data = _load_trends(variety)
    price_data = _load_price(variety)
    if not sent_data or not price_data:
        return {"error": f"No data for {variety}"}

    # _load_trends returns {'series': [...]} or {'data': {'daily_series': [...]}}
    if "data" in sent_data:
        series = sent_data.get("data", {}).get("daily_series", [])
    else:
        series = sent_data.get("series", [])
    prices_raw = price_data.get("prices", [])
    if len(series) < 20 or len(prices_raw) < 30:
        return {"error": "Insufficient data (need 30+ days)"}

    # Build aligned date arrays
    price_map = {}
    for p in prices_raw:
        price_map[str(p["date"])[:10]] = float(p["close"])

    # Compute fundamental factors
    factors = _compute_fundamental_factors(prices_raw)
    fund_scores = factors["fund_score"]
    price_dates = [str(p["date"])[:10] for p in prices_raw]

    # Build date-aligned sentiment
    sent_map = {}
    for s in series:
        d = s.get("date", "")
        if d:
            # _load_trends uses 'avg_score', _load_sentiment uses 'avg_score' too
            sent_map[d] = s.get("avg_score", s.get("sentiment_score", 0))

    # Merge: only use dates that appear in BOTH price and sentiment
    dates = []
    prices = []
    sent_scores = []
    f_scores = []
    for i, d in enumerate(price_dates):
        if d in sent_map:
            dates.append(d)
            prices.append(price_map[d])
            sent_scores.append(sent_map[d])
            f_scores.append(fund_scores[i])

    n = len(dates)
    if n < horizon + 10:
        return {"error": "Too few overlapping data points"}

    # --- Strategy 1: Pure Fundamental ---
    for i in range(n - horizon):
        fs = f_scores[i]
        if abs(fs) < fund_threshold:
            continue
        direction = "long" if fs > 0 else "short"
        entry_px = prices[i]
        exit_px = prices[i + horizon] if i + horizon < n else prices[-1]
        if not entry_px or not exit_px:
            continue
        if direction == "long":
            pnl = (exit_px - entry_px) / entry_px * 100
        else:
            pnl = (entry_px - exit_px) / entry_px * 100
        outcome = "win" if pnl > 0.1 else ("loss" if pnl < -0.1 else "breakeven")
        all_trades_fund.append({
            "date": dates[i], "direction": direction,
            "pnl": round(pnl, 2), "outcome": outcome,
        })

    # --- Strategy 2: Fundamental + Sentiment ---
    for i in range(n - horizon):
        fs = f_scores[i]
        ss = sent_scores[i]
        if abs(fs) < fund_threshold:
            continue
        # Both must agree on direction
        if fs > 0 and ss > 0:
            direction = "long"
        elif fs < 0 and ss < 0:
            direction = "short"
        else:
            continue  # Disagreement → no trade
        entry_px = prices[i]
        exit_px = prices[i + horizon] if i + horizon < n else prices[-1]
        if not entry_px or not exit_px:
            continue
        if direction == "long":
            pnl = (exit_px - entry_px) / entry_px * 100
        else:
            pnl = (entry_px - exit_px) / entry_px * 100
        outcome = "win" if pnl > 0.1 else ("loss" if pnl < -0.1 else "breakeven")
        all_trades_combo.append({
            "date": dates[i], "direction": direction,
            "pnl": round(pnl, 2), "outcome": outcome,
        })

    # --- Build PnL curves (cumulative) ---
    def build_pnl_curve(trades: list, n_days: int, start_idx: int) -> list[float]:
        """Map trades to daily cumulative PnL curve."""
        curve = [0.0] * n_days
        cum = 0.0
        trade_idx = 0
        for i in range(n_days):
            while trade_idx < len(trades) and dates.index(trades[trade_idx]["date"]) <= i:
                pass  # Trades are accounted at entry
            trade_idx = 0  # Reset
        # Simpler approach: just compute cumulative from trade sequence
        cum_curve = []
        cum = 0.0
        trade_dates = [t["date"] for t in trades]
        for i in range(n_days):
            d = dates[i]
            if d in trade_dates:
                idx = trade_dates.index(d)
                cum += trades[idx]["pnl"]
            cum_curve.append(round(cum, 2))
        return cum_curve

    # Better approach: date-indexed cumulative PnL
    fund_pnl_map = {t["date"]: t["pnl"] for t in all_trades_fund}
    combo_pnl_map = {t["date"]: t["pnl"] for t in all_trades_combo}

    fund_curve = []
    combo_curve = []
    price_curve = []
    cum_fund = 0.0
    cum_combo = 0.0
    base_price = prices[0] if prices else 1

    for i in range(n):
        d = dates[i]
        if d in fund_pnl_map:
            cum_fund += fund_pnl_map[d]
        if d in combo_pnl_map:
            cum_combo += combo_pnl_map[d]
        fund_curve.append(round(cum_fund, 2))
        combo_curve.append(round(cum_combo, 2))
        price_curve.append(round((prices[i] - base_price) / base_price * 100, 2))

    # Statistics
    def win_rate(trades):
        if not trades:
            return 0
        return round(sum(1 for t in trades if t["outcome"] == "win") / len(trades), 3)

    # Compute advanced metrics per strategy
    sc_td = max(len(dates) * 252 // 365, 30) if dates else 252
    fund_pnls = [t["pnl"] for t in all_trades_fund]
    combo_pnls = [t["pnl"] for t in all_trades_combo]
    adv_fund = compute_advanced_metrics(trade_pnls=fund_pnls, total_trading_days=sc_td) if fund_pnls else {}
    adv_combo = compute_advanced_metrics(trade_pnls=combo_pnls, total_trading_days=sc_td) if combo_pnls else {}

    return {
        "variety": variety,
        "horizon": horizon,
        "fund_threshold": fund_threshold,
        "sent_threshold": signal_threshold,
        "dates": dates,
        "curves": {
            "fund_only": fund_curve,
            "fund_plus_sentiment": combo_curve,
            "price": price_curve,
        },
        "stats": {
            "fund_only": {
                "trades": len(all_trades_fund),
                "win_rate": win_rate(all_trades_fund),
                "final_pnl": round(cum_fund, 2),
                "advanced_metrics": adv_fund,
            },
            "fund_plus_sentiment": {
                "trades": len(all_trades_combo),
                "win_rate": win_rate(all_trades_combo),
                "final_pnl": round(cum_combo, 2),
                "advanced_metrics": adv_combo,
            },
        },
        "recent_trades_fund": all_trades_fund[-10:],
        "recent_trades_combo": all_trades_combo[-10:],
    }


def run_trailing_strategy(
    variety: str = "",
    signal_threshold: float = 0.2,
    max_holding: int = 10,
    start_date: str = "2025-01-01",
) -> dict:
    """Sentiment trailing exit strategy.

    Entry (same as fixed-horizon):
      - avg_score > +threshold at day T → go LONG at T+1
      - avg_score < -threshold at day T → go SHORT at T+1

    Exit (different from fixed-horizon):
      - If LONG:  exit when sentiment turns bearish (score < 0) OR max_holding days reached
      - If SHORT: exit when sentiment turns bullish (score > 0) OR max_holding days reached
      - If sentiment data ends before exit condition → exit at last available date

    This simulates "riding the sentiment wave" — stay in as long as the crowd agrees,
    exit when they change their mind.
    """
    all_trades = []
    varieties_to_test = [variety] if variety else _get_all_varieties_with_data()

    for var in varieties_to_test:
        sent_data = _load_sentiment(var)
        price_data = _load_price(var)
        if not sent_data or not price_data:
            continue

        series = sent_data.get("data", {}).get("daily_series", [])
        prices_raw = price_data.get("prices", [])
        if len(series) < 20:
            continue

        price_map = {}
        for p in prices_raw:
            d = str(p["date"])[:10]
            price_map[d] = float(p["close"])

        # Build date-aligned arrays
        dates = []
        scores = []
        for s in series:
            d = s["date"]
            if d in price_map and d >= start_date:
                dates.append(d)
                scores.append(s.get("avg_score", 0))

        # Generate trades with trailing exit
        i = 0
        while i < len(dates):
            signal = scores[i]
            if abs(signal) < signal_threshold:
                i += 1
                continue

            # --- ENTRY ---
            direction = "long" if signal > 0 else "short"
            entry_date = dates[i]
            entry_px = price_map.get(entry_date, 0)
            if not entry_px:
                i += 1
                continue

            # --- FIND EXIT ---
            exit_date = None
            exit_px = None
            exit_reason = "max_holding"

            for j in range(i + 1, min(i + max_holding + 1, len(dates))):
                later_score = scores[j]
                later_date = dates[j]

                # Check sentiment reversal
                if direction == "long" and later_score < 0:
                    exit_date = later_date
                    exit_reason = "sentiment_flip"
                    break
                elif direction == "short" and later_score > 0:
                    exit_date = later_date
                    exit_reason = "sentiment_flip"
                    break

            # If no flip within max_holding, exit at max_holding day
            if exit_date is None:
                exit_idx = min(i + max_holding, len(dates) - 1)
                exit_date = dates[exit_idx]
                exit_reason = "max_holding"

            exit_px = price_map.get(exit_date, 0)
            if not exit_px:
                i += 1
                continue

            # --- P&L ---
            if direction == "long":
                pnl = (exit_px - entry_px) / entry_px * 100
            else:
                pnl = (entry_px - exit_px) / entry_px * 100

            outcome = "win" if pnl > 0.1 else ("loss" if pnl < -0.1 else "breakeven")
            holding_days = dates.index(exit_date) - dates.index(entry_date)

            all_trades.append(TradeRecord(
                variety=var, entry_date=entry_date, exit_date=exit_date,
                direction=direction, signal_value=round(signal, 3),
                entry_price=round(entry_px, 2), exit_price=round(exit_px, 2),
                pnl_pct=round(pnl, 2), outcome=outcome,
                horizon=holding_days,
            ))

            # Move to the day after exit (no overlapping trades)
            i = dates.index(exit_date) + 1

    if not all_trades:
        return {"total_trades": 0, "message": "No trades generated"}

    # Compute statistics
    wins = [t for t in all_trades if t.outcome == "win"]
    losses = [t for t in all_trades if t.outcome == "loss"]
    long_trades = [t for t in all_trades if t.direction == "long"]
    short_trades = [t for t in all_trades if t.direction == "short"]

    pnls = [t.pnl_pct for t in all_trades]

    # Estimate trading days
    all_entry_dates = sorted(set(t.entry_date for t in all_trades))
    if len(all_entry_dates) >= 2:
        d0 = datetime.strptime(all_entry_dates[0], "%Y-%m-%d")
        d1 = datetime.strptime(all_entry_dates[-1], "%Y-%m-%d")
        trading_days = max((d1 - d0).days * 252 // 365, len(pnls))
    else:
        trading_days = len(pnls) * max_holding

    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=trading_days)
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0

    # Exit reason stats
    flip_exits = [t for t in all_trades if getattr(t, 'horizon', 0) < max_holding]
    flip_count = len(flip_exits)

    # By variety
    by_variety = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in all_trades:
        by_variety[t.variety]["trades"] += 1
        if t.outcome == "win":
            by_variety[t.variety]["wins"] += 1
    for v in by_variety:
        n = by_variety[v]["trades"]
        by_variety[v]["win_rate"] = round(by_variety[v]["wins"] / n, 3) if n else 0

    # Avg holding period
    avg_holding = round(sum(t.horizon for t in all_trades) / len(all_trades), 1) if all_trades else 0

    return {
        "strategy": "trailing_sentiment",
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(
            len([t for t in long_trades if t.outcome == "win"]) / len(long_trades), 3
        ) if long_trades else 0,
        "short_win_rate": round(
            len([t for t in short_trades if t.outcome == "win"]) / len(short_trades), 3
        ) if short_trades else 0,
        "signal_threshold": signal_threshold,
        "max_holding": max_holding,
        "avg_holding_days": avg_holding,
        "flip_exits": flip_count,
        "max_holding_exits": len(all_trades) - flip_count,
        "by_variety": dict(by_variety),
        "advanced_metrics": advanced,
        "recent_trades": [
            {"variety": t.variety, "entry": t.entry_date, "exit": t.exit_date,
             "dir": t.direction, "pnl": t.pnl_pct, "outcome": t.outcome,
             "days": t.horizon}
            for t in all_trades
        ],
    }


def _get_all_varieties_with_data() -> list[str]:
    """Get all varieties that have both sentiment and price data."""
    varieties = set()
    for f in SENTIMENT_DIR.glob("*_sentiment.json"):
        varieties.add(f.stem.replace("_sentiment", ""))
    return sorted(varieties)


# ═══════════════════════════════════════════════════════════════════
# Variety Comparison (multi-variety radar)
# ═══════════════════════════════════════════════════════════════════

def compare_varieties(varieties: list[str]) -> list[dict]:
    """Compare sentiment metrics across multiple varieties."""
    result = []
    for var in varieties:
        sent = _load_sentiment(var)
        if not sent:
            continue
        ss = sent.get("data", {}).get("social_sentiment", {})
        series = sent.get("data", {}).get("daily_series", [])
        latest = series[-1] if series else {}

        # 7-day trend
        if len(series) >= 7:
            recent_scores = [s.get("avg_score", 0) for s in series[-7:]]
            trend_7d = round(sum(recent_scores) / 7, 3)
        else:
            trend_7d = 0

        # Compute score from bull/bear ratio if overall_score is missing or 0
        bull = ss.get("bullish_ratio", 0) or 0
        bear = ss.get("bearish_ratio", 0) or 0
        raw_score = ss.get("overall_score", 0) or 0
        if raw_score == 0 and (bull > 0 or bear > 0):
            raw_score = round(bull - bear, 3)
        label = ss.get("overall_sentiment_label", "neutral")
        if not label or label == "neutral":
            if raw_score > 0.1:
                label = "偏多"
            elif raw_score < -0.1:
                label = "偏空"

        result.append({
            "variety": var,
            "name": sent.get("variety_name", var),
            "sector": sent.get("sector", ""),
            "score": raw_score,
            "label": label,
            "bullish_ratio": round(bull, 3),
            "bearish_ratio": round(bear, 3),
            "total_posts": ss.get("total_posts_analyzed", 0),
            "trend_7d": trend_7d,
            "trend_label": ss.get("trend_label", ""),
        })

    result.sort(key=lambda x: -abs(x["score"]))
    return result


def get_all_variety_scores() -> list[dict]:
    """Get sentiment scores for ALL varieties (for ranking dashboard)."""
    varieties = []
    for f in sorted(SENTIMENT_DIR.glob("*_sentiment.json")):
        var = f.stem.replace("_sentiment", "")
        sent = _load_sentiment(var)
        if not sent:
            continue
        ss = sent.get("data", {}).get("social_sentiment", {})
        series = sent.get("data", {}).get("daily_series", [])
        latest = series[-1] if series else {}

        # Compute score from bull/bear ratio if overall_score is 0
        bull = ss.get("bullish_ratio", 0) or 0
        bear = ss.get("bearish_ratio", 0) or 0
        raw_score = ss.get("overall_score", 0) or 0
        if raw_score == 0 and (bull > 0 or bear > 0):
            raw_score = round(bull - bear, 3)

        label = ss.get("overall_sentiment_label", "neutral")
        if not label or label == "neutral":
            if raw_score > 0.15:
                label = u"偏多"
            elif raw_score < -0.15:
                label = u"偏空"

        varieties.append({
            "code": var,
            "name": sent.get("variety_name", var),
            "sector": sent.get("sector", ""),
            "score": raw_score,
            "label": label,
            "bullish": round(bull * 100),
            "bearish": round(bear * 100),
            "posts": ss.get("total_posts_analyzed", 0),
            "date": latest.get("date", ""),
        })
    return sorted(varieties, key=lambda x: -abs(x["score"]))
