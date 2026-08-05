"""Real-time & historical futures price fetcher via AKShare."""
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("price_fetcher")

# Cache directory for price data
PRICE_DIR = Path(os.environ.get(
    "THINK2_DIR", os.path.expanduser("~/Desktop/思路2/validate")
)) / "output" / "trends"

# AKShare variety name → TradingAgents code mapping
NAME_TO_CODE = {
    "螺纹钢": "RB", "热轧卷板": "HC", "铁矿石": "I", "焦炭": "J", "焦煤": "JM",
    "硅铁": "SF", "锰硅": "SM", "不锈钢": "SS",
    "沪铜": "CU", "沪铝": "AL", "沪锌": "ZN", "沪镍": "NI", "沪铅": "PB", "沪锡": "SN",
    "黄金": "AU", "白银": "AG",
    "原油": "SC", "燃油": "FU", "沥青": "BU", "PTA": "TA", "甲醇": "MA",
    "纯碱": "SA", "PVC": "V", "玻璃": "FG", "尿素": "UR", "橡胶": "RU",
    "塑料": "L", "PP": "PP", "乙二醇": "EG", "苯乙烯": "EB", "短纤": "PF",
    "豆粕": "M", "豆油": "Y", "棕榈油": "P", "菜粕": "RM", "菜油": "OI",
    "白糖": "SR", "棉花": "CF", "玉米": "C", "淀粉": "CS",
    "生猪": "LH", "鸡蛋": "JD", "苹果": "AP", "红枣": "CJ", "花生": "PK",
    "上证50": "IH", "沪深300": "IF", "中证500": "IC", "中证1000": "IM",
    "二年国债": "TS", "五年国债": "TF", "十年国债": "T", "三十年国债": "TL",
}


# Default major varieties for live ticker (keep < 24 for speed)
DEFAULT_LIVE_VARIETIES = [
    "RB", "J", "JM", "I", "HC", "SM", "SF",  # 黑色系(含合金)
    "CU", "AU", "AG",  # 有色/贵金属
    "SC", "TA", "MA", "SA", "FG", "UR", "RU",  # 能化
    "M", "Y", "P", "SR", "CF", "RM", "OI", "C",  # 农产品
]


def fetch_realtime_prices(varieties: Optional[list[str]] = None) -> dict:
    """Fetch real-time futures prices from AKShare.

    Args:
        varieties: List of TradingAgents variety codes (e.g., ['RB','J']).
                   If None, fetch DEFAULT_LIVE_VARIETIES.

    Returns:
        {code: {price, change_pct, volume, timestamp, name}, ...}
    """
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed")
        return {}

    # Build reverse mapping: code → name
    code_to_name = {v: k for k, v in NAME_TO_CODE.items()}
    target_codes = set(varieties) if varieties else set(DEFAULT_LIVE_VARIETIES)

    result = {}

    # Fetch all real-time futures data at once
    try:
        df = ak.futures_zh_realtime(symbol="螺纹钢")  # any symbol triggers full fetch
        # Actually, this returns only 螺纹钢. Let's try a broader approach.
    except Exception:
        df = None

    # Fetch one-by-one for requested varieties
    for code in target_codes:
        name = code_to_name.get(code)
        if not name:
            continue
        try:
            df = ak.futures_zh_realtime(symbol=name)
            if df is None or df.empty:
                continue
            # Get the main contract row (index 0)
            row = df.iloc[0]
            result[code] = {
                "price": float(row["trade"]) if row.get("trade") else 0,
                "change_pct": float(row["changepercent"]) if row.get("changepercent") else 0,
                "volume": int(row.get("volume", 0)) if row.get("volume") else 0,
                "name": name,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            logger.debug(f"AKShare fetch failed for {name}: {e}")

    return result


def update_price_files(varieties: Optional[list[str]] = None) -> dict:
    """Fetch latest daily data and update price JSON files.

    Adds new days to existing price files. Does NOT overwrite existing data.
    """
    code_to_name = {v: k for k, v in NAME_TO_CODE.items()}
    target = varieties if varieties else list(code_to_name.keys())

    updated = {}
    for code in target:
        name = code_to_name.get(code)
        if not name:
            continue
        try:
            updated[code] = _update_single_price(code, name)
        except Exception as e:
            logger.warning(f"Price update failed for {code}: {e}")

    return updated


def _update_single_price(code: str, name: str) -> dict:
    """Update price JSON for a single variety using AKShare daily data."""
    import akshare as ak
    from datetime import date

    # Load existing data
    price_path = PRICE_DIR / f"{name}_price.json"
    existing = {}
    if price_path.exists():
        with open(price_path, "r", encoding="utf-8") as f:
            existing = json.load(f)

    existing_prices = existing.get("prices", [])
    existing_dates = {p["date"] for p in existing_prices}

    # Fetch latest daily data
    new_count = 0
    try:
        # Use Sina daily data
        df = ak.futures_zh_daily_sina(symbol=f"{code}0")
        if df is None or df.empty:
            return {"code": code, "error": "no data", "new": 0}

        for _, row in df.iterrows():
            dt = str(row["date"])[:10]
            if dt in existing_dates:
                continue
            existing_prices.append({
                "date": dt,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "change_pct": 0,
            })
            existing_dates.add(dt)
            new_count += 1

        # Sort by date
        existing_prices.sort(key=lambda x: x["date"])

        # Save
        existing["prices"] = existing_prices
        existing["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing["variety_code"] = code
        existing["variety_name"] = name

        PRICE_DIR.mkdir(parents=True, exist_ok=True)
        with open(price_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"  {code} ({name}): {new_count} new days, {len(existing_prices)} total")
        return {"code": code, "name": name, "new": new_count, "total": len(existing_prices)}

    except Exception as e:
        logger.warning(f"  {code}: {e}")
        return {"code": code, "error": str(e)[:100], "new": 0}


# ── Live price cache ──
_live_cache: dict = {}
_last_fetch: float = 0
CACHE_TTL = 60  # seconds


def get_cached_prices(varieties: Optional[list[str]] = None) -> dict:
    """Get live prices. Sync fetch on first call (~15s), instant thereafter (60s cache)."""
    import time
    global _last_fetch
    now = time.time()
    if not _live_cache or (now - _last_fetch) > CACHE_TTL:
        try:
            # Fetch only top 8 for first load speed
            to_fetch = DEFAULT_LIVE_VARIETIES[:8] if not _live_cache else DEFAULT_LIVE_VARIETIES
            result = fetch_realtime_prices(to_fetch)
            _live_cache.update(result)
            _last_fetch = now
            logger.info(f"Fetched {len(result)} live prices, cache={len(_live_cache)}")
        except Exception as e:
            logger.warning(f"Fetch failed: {e}")
    if varieties:
        return {k: v for k, v in _live_cache.items() if k in varieties}
    return dict(_live_cache)


if __name__ == "__main__":
    # Test
    print("=== Real-time prices ===")
    rt = fetch_realtime_prices(["RB", "J", "M", "AG", "SC"])
    for code, data in rt.items():
        print(f"  {code} ({data['name']}): {data['price']}  ({data['change_pct']:+.2%})")

    print("\n=== Update price files ===")
    result = update_price_files(["RB", "J", "M", "AG", "SC"])
    for code, info in result.items():
        print(f"  {code}: {info.get('new', 0)} new, {info.get('total', 0)} total")
