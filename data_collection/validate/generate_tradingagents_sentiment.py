"""
generate_tradingagents_sentiment.py
====================================
Convert 思路2 trends data → TradingAgents external_data/{VARIETY}_sentiment.json

Reads: output/trends/*_sentiment.json + _index.json
Writes: ~/.tradingagents/external_data/{VARIETY}_sentiment.json

Usage:
  python generate_tradingagents_sentiment.py
  python generate_tradingagents_sentiment.py --variety 螺纹钢
  python generate_tradingagents_sentiment.py --min-notes 3   # only varieties with ≥3 notes
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Paths ---
TRENDS_DIR = Path(__file__).parent / "output" / "trends"
OUTPUT_DIR = Path.home() / ".tradingagents" / "external_data"

# TradingAgents variety code mapping (Chinese name → symbol)
VARIETY_NAME_TO_SYMBOL = {
    "螺纹钢": "RB",
    "热卷": "HC",
    "铁矿石": "I",
    "焦炭": "J",
    "焦煤": "JM",
    "硅铁": "SF",
    "锰硅": "SM",
    "线材": "WR",
    "铜": "CU",
    "铝": "AL",
    "锌": "ZN",
    "铅": "PB",
    "镍": "NI",
    "锡": "SN",
    "黄金": "AU",
    "白银": "AG",
    "原油": "SC",
    "PTA": "TA",
    "甲醇": "MA",
    "PVC": "V",
    "PP": "PP",
    "塑料": "L",
    "橡胶": "RU",
    "沥青": "BU",
    "尿素": "UR",
    "纯碱": "SA",
    "玻璃": "FG",
    "乙二醇": "EG",
    "苯乙烯": "EB",
    "短纤": "PF",
    "豆粕": "M",
    "豆油": "Y",
    "棕榈油": "P",
    "菜粕": "RM",
    "菜油": "OI",
    "白糖": "SR",
    "棉花": "CF",
    "玉米": "C",
    "淀粉": "CS",
    "鸡蛋": "JD",
    "生猪": "LH",
    "苹果": "AP",
    "红枣": "CJ",
    "花生": "PK",
    "工业硅": "SI",
    "碳酸锂": "LC",
    "氧化铝": "AO",
    "烧碱": "SH",
    "对二甲苯": "PX",
    # Financial futures
    "上证50股指期货": "IH",
    "沪深300股指期货": "IF",
    "中证500股指期货": "IC",
    "中证1000股指期货": "IM",
    "2年期国债期货": "TS",
    "5年期国债期货": "TF",
    "10年期国债期货": "T",
    "30年期国债期货": "TL",
}

# Reverse mapping for fuzzy lookup
SYMBOL_TO_CHINESE = {v: k for k, v in VARIETY_NAME_TO_SYMBOL.items()}

# Sector classification
SECTOR_MAP = {
    "RB": "黑色系",
    "HC": "黑色系",
    "I": "黑色系",
    "J": "黑色系",
    "JM": "黑色系",
    "SF": "黑色系",
    "SM": "黑色系",
    "WR": "黑色系",
    "CU": "有色金属",
    "AL": "有色金属",
    "ZN": "有色金属",
    "PB": "有色金属",
    "NI": "有色金属",
    "SN": "有色金属",
    "AU": "贵金属",
    "AG": "贵金属",
    "SC": "能源化工",
    "TA": "能源化工",
    "MA": "能源化工",
    "V": "能源化工",
    "PP": "能源化工",
    "L": "能源化工",
    "RU": "能源化工",
    "BU": "能源化工",
    "UR": "能源化工",
    "SA": "能源化工",
    "FG": "能源化工",
    "EG": "能源化工",
    "EB": "能源化工",
    "PF": "能源化工",
    "SI": "能源化工",
    "LC": "能源化工",
    "AO": "有色金属",
    "SH": "能源化工",
    "PX": "能源化工",
    "M": "农产品",
    "Y": "农产品",
    "P": "农产品",
    "RM": "农产品",
    "OI": "农产品",
    "SR": "农产品",
    "CF": "农产品",
    "C": "农产品",
    "CS": "农产品",
    "JD": "农产品",
    "LH": "农产品",
    "AP": "农产品",
    "CJ": "农产品",
    "PK": "农产品",
    "IF": "金融期货",
    "IH": "金融期货",
    "IC": "金融期货",
    "IM": "金融期货",
    "T": "金融期货",
    "TF": "金融期货",
    "TS": "金融期货",
    "TL": "金融期货",
}

BEIJING_TZ = timezone(timedelta(hours=8))


def load_trends_data(trends_dir) -> dict:
    if isinstance(trends_dir, str):
        trends_dir = Path(trends_dir)
    """Load all variety sentiment + index data from trends directory."""
    index_path = trends_dir / "_index.json"
    global_weights_path = trends_dir / "_global_weights.json"

    index = {}
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)

    global_weights = {}
    if global_weights_path.exists():
        with open(global_weights_path, encoding="utf-8") as f:
            global_weights = json.load(f)

    varieties = {}
    for fpath in sorted(trends_dir.glob("*_sentiment.json")):
        name = fpath.stem.replace("_sentiment", "")
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        varieties[name] = {"sentiment": data}

    # Load weights
    for fpath in sorted(trends_dir.glob("*_weights.json")):
        name = fpath.stem.replace("_weights", "")
        if name in varieties:
            with open(fpath, encoding="utf-8") as f:
                varieties[name]["weights"] = json.load(f)

    return varieties, index, global_weights


def compute_summary(series: list) -> dict:
    """Compute summary stats from daily sentiment series."""
    if not series:
        return {}

    # Filter to last 90 days
    cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=90)).strftime("%Y-%m-%d")
    recent = [d for d in series if d.get("date", "") >= cutoff]

    if not recent:
        recent = series[-30:] if len(series) > 30 else series

    scores = [d.get("avg_score", 0) for d in recent]
    total_notes = sum(d.get("note_count", 0) for d in recent)
    total_bull = sum(d.get("bull_count", 0) for d in recent)
    total_bear = sum(d.get("bear_count", 0) for d in recent)
    total_neutral = total_notes - total_bull - total_bear

    avg_score = sum(scores) / len(scores) if scores else 0

    # Trend direction
    if len(scores) >= 3:
        first_half = sum(scores[: len(scores) // 2]) / (len(scores) // 2)
        second_half = sum(scores[len(scores) // 2 :]) / (len(scores) - len(scores) // 2)
        if second_half > first_half + 0.1:
            trend = "bullish_improving"
            trend_cn = "情绪转暖 ↑"
        elif second_half < first_half - 0.1:
            trend = "bearish_worsening"
            trend_cn = "情绪转冷 ↓"
        else:
            trend = "stable"
            trend_cn = "情绪平稳 →"
    else:
        trend = "insufficient_data"
        trend_cn = "数据不足"

    # Platform breakdown
    platform_totals = {}
    for d in recent:
        for plat, count in d.get("platform_counts", {}).items():
            platform_totals[plat] = platform_totals.get(plat, 0) + count

    # Determine overall sentiment level
    # Score range: -1 (strong bearish) to +1 (strong bullish)
    if avg_score > 0.3:
        sentiment_level = "bullish"
        sentiment_cn = "看多"
    elif avg_score > 0.08:
        sentiment_level = "slightly_bullish"
        sentiment_cn = "偏多"
    elif avg_score > -0.08:
        sentiment_level = "neutral"
        sentiment_cn = "中性"
    elif avg_score > -0.3:
        sentiment_level = "slightly_bearish"
        sentiment_cn = "偏空"
    else:
        sentiment_level = "bearish"
        sentiment_cn = "看空"

    # Check for extreme readings (potential reversal signals)
    is_extreme = abs(avg_score) > 0.5
    extreme_note = ""
    if is_extreme:
        if sentiment_level == "bullish":
            extreme_note = "⚠️ 情绪极度看多（可能为见顶信号——散户一致性看多往往是反向指标）"
        else:
            extreme_note = "⚠️ 情绪极度看空（可能为见底信号——散户恐慌性看空往往是反向指标）"

    return {
        "avg_score": round(avg_score, 3),
        "sentiment_level": sentiment_level,
        "sentiment_label": sentiment_cn,
        "trend": trend,
        "trend_label": trend_cn,
        "total_notes": total_notes,
        "bullish_notes": total_bull,
        "bearish_notes": total_bear,
        "neutral_notes": total_neutral,
        "bullish_ratio": round(total_bull / total_notes, 3) if total_notes else 0,
        "bearish_ratio": round(total_bear / total_notes, 3) if total_notes else 0,
        "neutral_ratio": round(total_neutral / total_notes, 3) if total_notes else 0,
        "date_range": f"{recent[0]['date']} ~ {recent[-1]['date']}" if recent else "N/A",
        "total_days": len(recent),
        "platforms": platform_totals,
        "is_extreme": is_extreme,
        "extreme_note": extreme_note,
    }


def generate_sentiment_json(
    variety_name: str, trends_data: dict, index_data: dict, weights_data: dict
) -> dict | None:
    """Generate TradingAgents-format sentiment JSON for one variety."""
    symbol = VARIETY_NAME_TO_SYMBOL.get(variety_name)
    if not symbol:
        return None

    sentiment_file = trends_data.get("sentiment", {})
    series = sentiment_file.get("series", [])
    weights = trends_data.get("weights", {})

    if not series:
        return None

    summary = compute_summary(series)

    # Index entry for this variety
    idx_entry = index_data.get(variety_name, {})

    # Daily series (last 30 days for LLM prompt size)
    daily_series = []
    for d in series[-30:]:
        daily_series.append(
            {
                "date": d.get("date", ""),
                "avg_score": round(d.get("avg_score", 0), 3),
                "simple_avg": round(d.get("simple_avg", 0), 3),
                "note_count": d.get("note_count", 0),
                "bull_count": d.get("bull_count", 0),
                "bear_count": d.get("bear_count", 0),
                "weighted_score": round(d.get("weighted_score", 0), 3),
                "platforms": d.get("platform_counts", {}),
                "top_authors": d.get("top_authors", []),
            }
        )

    # Platform weights from backtest
    platform_weights = weights.get("platform_weights", {})
    weight_source = weights.get("weight_source", "not_calibrated")

    # Combined metrics (sentiment-price correlation)
    combined = idx_entry.get("combined_metrics", {})

    # Sector
    sector = SECTOR_MAP.get(symbol, "未知")

    # Generate the output document
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S")

    output = {
        "variety": symbol,
        "variety_name": variety_name,
        "sector": sector,
        "updated": now_str,
        "source": "思路2多平台采集：微博+知乎+小红书 | 规则引擎+LLM双引擎情感分析",
        "source_platforms": list(summary.get("platforms", {}).keys()),
        "stale_after_hours": 48,
        "data": {
            "social_sentiment": {
                "bullish_ratio": summary["bullish_ratio"],
                "bearish_ratio": summary["bearish_ratio"],
                "neutral_ratio": summary["neutral_ratio"],
                "total_posts_analyzed": summary["total_notes"],
                "date_range": summary["date_range"],
                "platforms": summary["platforms"],
                "overall_sentiment": summary["sentiment_level"],
                "overall_sentiment_label": summary["sentiment_label"],
                "avg_score": summary["avg_score"],
                "trend": summary["trend"],
                "trend_label": summary["trend_label"],
                "is_extreme": summary["is_extreme"],
                "extreme_note": summary["extreme_note"],
            },
            "daily_series": daily_series,
            "platform_weights": {
                "weights": platform_weights,
                "source": weight_source,
                "note": "平台权重来自回测：基于方向准确率优化。xhs/weibo/zhihu 分别代表小红书/微博/知乎的贡献权重。",
            },
            "sentiment_price_correlation": {
                "direction_accuracy": combined.get("direction_accuracy"),
                "pearson_r": combined.get("pearson_r"),
                "data_points": combined.get("data_points"),
                "note": "情绪-价格相关性回测。>0.5 表示情绪对短期方向有预测力，<0.3 表示情绪为噪声或反向指标。",
            },
            "index_summary": {
                "total_days": idx_entry.get("total_days", summary["total_days"]),
                "total_notes": idx_entry.get("total_notes", summary["total_notes"]),
                "avg_sentiment": idx_entry.get("avg_sentiment", summary["avg_score"]),
                "recent_trend": idx_entry.get("recent_trend", summary["trend_label"]),
                "by_platform": idx_entry.get("by_platform", summary["platforms"]),
            },
            "methodology": {
                "sentiment_engine": "规则引擎(7级分类) + LLM引擎(Claude/GPT/DeepSeek)",
                "ner_engine": "FuturesNER — 50品种×多别名+合约代码+价格识别",
                "multimodal": "三通道融合：Emoji + 文本 + 图片(VLM: granite2B+qwen2.5vl:3b)",
                "aggregation": "按(品种, 日期)聚合 → 作者加权 → 平台加权平均 → 时序序列",
                "weight_formula": {
                    "engagement": "1 + (likes + comments×2 + shares×0.5) ^ 0.3",
                    "author_fans": "1 + ln(1 + followers) × 0.03  (max 1.5x, 缺数据=1.0)",
                    "author_volume": "1 + ln(total_posts) × 0.08  (max 1.4x, 反映领域专注度)",
                    "final": "engagement_weight × fans_weight × volume_weight",
                },
                "limitations": [
                    "数据量有限（日均1-3条），低数据量品种信号噪声大",
                    "小红书反爬严格，主要数据来自微博+知乎",
                    "散户情绪可能为反向指标（一致性看多≈顶部）",
                    "无持仓数据（散户净多/净空比），纯文本情绪分析",
                    "情感规则引擎对期货领域讽刺/反语识别有限",
                ],
            },
        },
    }

    return output


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate TradingAgents sentiment JSONs")
    parser.add_argument("--variety", type=str, help="Specific variety name (Chinese)")
    parser.add_argument(
        "--min-notes", type=int, default=1, help="Minimum total notes to generate (default: 1)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't write")
    args = parser.parse_args()

    if not TRENDS_DIR.exists():
        print(f"ERROR: Trends directory not found: {TRENDS_DIR}")
        sys.exit(1)

    varieties, index, global_weights = load_trends_data(TRENDS_DIR)
    print(f"Loaded {len(varieties)} varieties from {TRENDS_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0

    for vname in sorted(varieties.keys()):
        if args.variety and vname != args.variety:
            continue

        output = generate_sentiment_json(vname, varieties[vname], index, global_weights)

        if output is None:
            print(f"  SKIP {vname}: no symbol mapping")
            skipped += 1
            continue

        if output["data"]["social_sentiment"]["total_posts_analyzed"] < args.min_notes:
            print(
                f"  SKIP {vname} ({output['data']['social_sentiment']['total_posts_analyzed']} notes < {args.min_notes})"
            )
            skipped += 1
            continue

        symbol = output["variety"]
        out_path = OUTPUT_DIR / f"{symbol}_sentiment.json"

        if args.dry_run:
            print(
                f"  WOULD WRITE {vname} → {out_path} "
                f"({output['data']['social_sentiment']['total_posts_analyzed']} notes, "
                f"sentiment={output['data']['social_sentiment']['overall_sentiment_label']})"
            )
            generated += 1
            continue

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(
            f"  WROTE {vname} → {out_path} "
            f"({output['data']['social_sentiment']['total_posts_analyzed']} notes, "
            f"sentiment={output['data']['social_sentiment']['overall_sentiment_label']})"
        )
        generated += 1

    print(f"\nDone. Generated: {generated}, Skipped: {skipped}")
    print(f"Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
