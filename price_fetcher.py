"""Real-time & historical futures price fetcher via AKShare."""

# =============================================================================
# 【模块角色】
#   price_fetcher.py 是项目的"行情数据供给模块"。它通过国产财经数据接口
#   AKShare 拉取国内期货的实时行情与历史日线,并以两种方式对外提供数据:
#
#     1. 实时行情(fetch_realtime_prices / get_cached_prices):
#        按品种代码返回最新价、涨跌幅、成交量等,带 60 秒内存缓存,
#        供 Web 首页"实时行情"、自选列表等高频场景使用。
#     2. 历史日线(update_price_files / _update_single_price):
#        把每日 K 线数据增量追加到 JSON 文件(output/trends/品种名_price.json),
#        供趋势分析、回测等模块读取,不会覆盖已有历史数据。
#
#   本模块还维护了两张重要映射表:
#     - NAME_TO_CODE:AKShare 中文品种名 → TradingAgents 内部品种代码(约 50 个)。
#     - DEFAULT_LIVE_VARIETIES:默认实时展示的 24 个主力品种(黑色/有色/能化/农产品)。
#
#   提示:AKShare 依赖网络,接口可能变动,因此本模块大量使用 try/except,
#        单品种拉取失败不会导致整体崩溃。
# =============================================================================

import json
import logging
from datetime import datetime

from path_utils import resolve_think2_dir

logger = logging.getLogger("price_fetcher")

# Cache directory for price data
PRICE_DIR = resolve_think2_dir() / "output" / "trends"

# AKShare 中文品种名 → TradingAgents 内部品种代码 的映射表(约 50 个品种)。
# 覆盖黑色系(螺纹钢/铁矿/焦炭...)、有色贵金属(铜/铝/金/银...)、
# 能化(原油/PTA/甲醇/纯碱...)、农产品(豆粕/豆油/棕榈/白糖/棉花...)
# 以及股指国债(上证50/沪深300/中证500/五年国债...)。
# 用途:实时行情按中文名调用 AKShare,返回结果用代码表示;历史数据保存时也要靠它翻译。
# AKShare variety name → TradingAgents code mapping
NAME_TO_CODE = {
    "螺纹钢": "RB",
    "热轧卷板": "HC",
    "铁矿石": "I",
    "焦炭": "J",
    "焦煤": "JM",
    "硅铁": "SF",
    "锰硅": "SM",
    "不锈钢": "SS",
    "沪铜": "CU",
    "沪铝": "AL",
    "沪锌": "ZN",
    "沪镍": "NI",
    "沪铅": "PB",
    "沪锡": "SN",
    "黄金": "AU",
    "白银": "AG",
    "原油": "SC",
    "燃油": "FU",
    "沥青": "BU",
    "PTA": "TA",
    "甲醇": "MA",
    "纯碱": "SA",
    "PVC": "V",
    "玻璃": "FG",
    "尿素": "UR",
    "橡胶": "RU",
    "塑料": "L",
    "PP": "PP",
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
    "生猪": "LH",
    "鸡蛋": "JD",
    "苹果": "AP",
    "红枣": "CJ",
    "花生": "PK",
    "上证50": "IH",
    "沪深300": "IF",
    "中证500": "IC",
    "中证1000": "IM",
    "二年国债": "TS",
    "五年国债": "TF",
    "十年国债": "T",
    "三十年国债": "TL",
}


# 默认实时行情展示的主力品种清单(共 24 个)。
# 注释 (keep < 24 for speed) 表示:为保证刷新速度,清单控制在 24 个以内。
# 分组说明:黑色系(含合金)、有色/贵金属、能化、农产品四大板块各取代表性品种。
# 首次进入页面只拉前 8 个以加快首屏,随后补全其余品种(见 get_cached_prices)。
# Default major varieties for live ticker (keep < 24 for speed)
DEFAULT_LIVE_VARIETIES = [
    "RB",
    "J",
    "JM",
    "I",
    "HC",
    "SM",
    "SF",  # 黑色系(含合金)
    "CU",
    "AU",
    "AG",  # 有色/贵金属
    "SC",
    "TA",
    "MA",
    "SA",
    "FG",
    "UR",
    "RU",  # 能化
    "M",
    "Y",
    "P",
    "SR",
    "CF",
    "RM",
    "OI",
    "C",  # 农产品
]


def fetch_realtime_prices(varieties: list[str] | None = None) -> dict:
    """从 AKShare 抓取期货实时行情。

    【功能】按品种代码列表逐个调用 AKShare 接口,返回最新价/涨跌幅/成交量等。
    【参数】varieties: TradingAgents 品种代码列表(如 ['RB','J']);为 None 时
            抓取 DEFAULT_LIVE_VARIETIES 全部 24 个品种。
    【返回】dict: {代码: {price, change_pct, volume, timestamp, name}, ...}。
            任一品种失败仅跳过该品种,不会中断整体。
    【关键逻辑】
            - 先建立 code→name 反向映射,把代码翻译回中文名供 AKShare 调用。
            - 逐个品种调用 ak.futures_zh_realtime(symbol=中文名),取主连合约
              第一行(df.iloc[0])作为行情。
            - 数值字段做容错:接口缺字段时用 0 兜底。
            - AKShare 未安装时直接返回空字典,并记录 error 日志。

    Fetch real-time futures prices from AKShare.

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

    # 反向映射:代码 → 中文名(与 NAME_TO_CODE 方向相反,用于把代码翻译成接口参数)
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


def update_price_files(varieties: list[str] | None = None) -> dict:
    """拉取最新日线数据并增量更新价格 JSON 文件。

    【功能】对每个品种调用 _update_single_price,把 AKShare 的日 K 数据
            追加进对应的 *_price.json 文件。
    【参数】varieties: 品种代码列表;为 None 时处理 NAME_TO_CODE 里全部品种。
    【返回】dict: {代码: _update_single_price 的结果字典}。
    【关键逻辑】只"增量追加新日期",不会覆盖已有历史数据;单个品种失败被
                捕获并记录 warning,不影响其他品种。

    Fetch latest daily data and update price JSON files.

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
    """更新单个品种的价格 JSON 文件(增量追加日线)。

    【功能】读取已有的 {name}_price.json,从 AKShare 拉取该品种日线,
            把"本地没有的新日期"追加进 prices 列表后写回文件。
    【参数】code: 品种代码(如 "RB");name: 品种中文名(如 "螺纹钢")。
    【返回】dict: {"code","name","new"(新增天数),"total"(总天数)} 或错误信息。
    【关键逻辑】
            - 已有日期集合 existing_dates 用于去重,相同日期不重复追加。
            - 通过 ak.futures_zh_daily_sina(symbol=f"{code}0") 拉主连日线。
            - 新数据按日期升序排序后写回,并记录 updated 时间戳与品种信息。
            - 数据源为新浪(Sina)期货日线,change_pct 固定写 0(接口不直接提供)。
    """

    import akshare as ak

    # Load existing data
    price_path = PRICE_DIR / f"{name}_price.json"
    existing = {}
    if price_path.exists():
        with open(price_path, encoding="utf-8") as f:
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
            existing_prices.append(
                {
                    "date": dt,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "change_pct": 0,
                }
            )
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


# ── 实时行情内存缓存 ──
# _live_cache: 品种代码 → 行情字典 的内存缓存,避免每次请求都去打 AKShare。
# _last_fetch : 上一次刷新缓存的 Unix 时间戳(秒)。
# CACHE_TTL   : 缓存有效期,60 秒。60 秒内重复请求直接读缓存,不触发网络调用。
# ── Live price cache ──
_live_cache: dict = {}
_last_fetch: float = 0
CACHE_TTL = 60  # seconds


def get_cached_prices(varieties: list[str] | None = None) -> dict:
    """获取带 60 秒缓存保护的实时行情。

    【功能】对外提供实时行情的统一入口:缓存有效时秒回,失效时同步刷新缓存。
    【参数】varieties: 需要返回的品种代码列表;为 None 时返回全部缓存。
    【返回】dict: {代码: 行情字典};若指定 varieties 则只返回这些品种的子集。
    【关键逻辑】
            - 首次调用时缓存为空,网络拉取约 15 秒;之后 60 秒内直接返回缓存。
            - 首次只抓 DEFAULT_LIVE_VARIETIES 前 8 个(首屏提速),
              刷新时再补全到全部 24 个并合并进 _live_cache。
            - 刷新失败只记 warning,并继续用旧缓存兜底。
            - 返回前按 varieties 过滤;未指定时返回 _live_cache 的副本(防止外部篡改)。

    Get live prices. Sync fetch on first call (~15s), instant thereafter (60s cache).
    """
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
    # 直接运行本文件时执行的自测代码:打印部分品种的实时行情并更新价格文件。
    # Test
    print("=== Real-time prices ===")
    rt = fetch_realtime_prices(["RB", "J", "M", "AG", "SC"])
    for code, data in rt.items():
        print(f"  {code} ({data['name']}): {data['price']}  ({data['change_pct']:+.2%})")

    print("\n=== Update price files ===")
    result = update_price_files(["RB", "J", "M", "AG", "SC"])
    for code, info in result.items():
        print(f"  {code}: {info.get('new', 0)} new, {info.get('total', 0)} total")
