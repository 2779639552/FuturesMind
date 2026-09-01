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

import json  # 【调用包】JSON 读写(价格文件存取)
import logging  # 【调用包】日志记录(拉取失败/刷新状态)
from datetime import datetime  # 【调用包】时间戳生成(行情/更新时间)

from path_utils import resolve_think2_dir  # 【调用包】路径集中管理(定位趋势数据目录)

logger = logging.getLogger("price_fetcher")  # 【变量】模块日志器(带模块名前缀)

# Cache directory for price data
PRICE_DIR = resolve_think2_dir() / "output" / "trends"  # 【变量】价格数据文件目录(output/trends)

# AKShare 中文品种名 → TradingAgents 内部品种代码 的映射表(约 50 个品种)。
# 覆盖黑色系(螺纹钢/铁矿/焦炭...)、有色贵金属(铜/铝/金/银...)、
# 能化(原油/PTA/甲醇/纯碱...)、农产品(豆粕/豆油/棕榈/白糖/棉花...)
# 以及股指国债(上证50/沪深300/中证500/五年国债...)。
# 用途:实时行情按中文名调用 AKShare,返回结果用代码表示;历史数据保存时也要靠它翻译。
# AKShare variety name → TradingAgents code mapping
NAME_TO_CODE = {  # 【变量】中文品种名→内部代码映射表(实时行情/存盘翻译用)
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
    "燃料油": "FU",
    "低硫燃料油": "LU",
    "20号胶": "NR",
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
    "对二甲苯": "PX",
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
DEFAULT_LIVE_VARIETIES = [  # 【变量】默认实时展示的 24 个主力品种清单(首屏只取前 8 个提速)
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
    code_to_name = {v: k for k, v in NAME_TO_CODE.items()}  # 【变量】code_to_name:代码→中文名反向映射(把代码翻译成接口参数)
    target_codes = set(varieties) if varieties else set(DEFAULT_LIVE_VARIETIES)  # 【变量】target_codes:本次要抓取的品种集合

    result = {}

    # Fetch all real-time futures data at once
    try:
        df = ak.futures_zh_realtime(symbol="螺纹钢")  # any symbol triggers full fetch  # 【调用函数】AKShare 实时行情接口(尝试一次性抓全)
        # Actually, this returns only 螺纹钢. Let's try a broader approach.
    except Exception:
        df = None

    # Fetch one-by-one for requested varieties
    for code in target_codes:
        name = code_to_name.get(code)
        if not name:
            continue
        try:
            df = ak.futures_zh_realtime(symbol=name)  # 【调用函数】AKShare 实时行情接口(按中文名拉单品种 DataFrame)
            if df is None or df.empty:
                continue
            # Get the main contract row (index 0)
            row = df.iloc[0]  # 【变量】row:主连合约第一行(视为该品种主力行情)
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


# ═══════════════════════════════════════════════════════════════════════
# 换月跳空处理(后复权)
# ═══════════════════════════════════════════════════════════════════════
# 新浪主力连续(如 RB0)是"简单拼接、未复权"的序列:当主力合约从 A 月切到
# B 月时,序列会硬跳一段价差(换月跳空),这不是真实可交易的盈亏——若持仓恰好
# 跨过换月日,回测会被虚构一段 PnL。后复权(backward adjustment)在换月点把
# 历史价格按比例缩放,使序列连续、消除伪跳空。最近一根 bar 因子恒为 1(当前
# 价格保持真实,今日信号/当前价不受影响),历史段为"复权价"(收益率真实,
# 绝对价位不代表当时成交价)。
# 【换月点来源】优先用"真实换月日历"(scripts/build_rollover_calendar.py 生成,
# 逐合约日线逐日判主力切换,查证而非猜测),彻底消除 8% 启发式的两个盲区:
#   漏检(小换月 <8%,如螺纹钢/PTA/鸡蛋)+ 误判(把真实大涨当换月,如原油外盘联动)。
#   品种不在日历时,才回退到 8% 收盘缺口启发式(见 _detect_rollover_dates)。
ROLLOVER_GAP_THRESHOLD_PCT = 8.0  # 【变量】换月跳空判定阈值(%):无真实日历回退时,收盘缺口超过它即视为换月

# 真实换月日历(scripts/build_rollover_calendar.py 生成)。
# 结构: {品种名: {"main_per_day": {date: "JD2607"}, "rollover_dates": [{date, from, to}, ...]}}
# 换月日期是"查证"出来的(逐合约日线逐日判主力切换),不是启发式猜测;
# 有日历的品种优先用日历,避免 8% 启发式漏检小换月 / 误判真实大涨。
_rollover_calendar_cache: dict | None = None  # 【变量】换月日历内存缓存(懒加载,None=未加载)


def _load_rollover_calendar() -> dict:
    """加载真实换月日历(懒加载+缓存)。失败(无文件/解析错误)返回空 dict。

    【返回】{品种名: {"rollover_dates": [{"date", "from", "to"}, ...], ...}}
    """
    global _rollover_calendar_cache  # 【变量】模块级缓存
    if _rollover_calendar_cache is not None:
        return _rollover_calendar_cache
    cal_path = PRICE_DIR / "_rollover_calendar.json"  # 【变量】日历文件路径(与价格文件同目录)
    try:
        if cal_path.exists():
            with open(cal_path, encoding="utf-8") as f:
                _rollover_calendar_cache = json.load(f)  # 【调用函数】读取日历文件
        else:
            _rollover_calendar_cache = {}
    except Exception as e:
        logger.warning(f"加载换月日历失败({cal_path}): {e}")
        _rollover_calendar_cache = {}
    return _rollover_calendar_cache


def _detect_rollover_dates(
    prices: list[dict], threshold_pct: float = ROLLOVER_GAP_THRESHOLD_PCT
) -> list[int]:
    """识别主力连续序列中的换月点(返回 bar 下标列表)。

    【功能】用"收盘到收盘缺口" |close_i / close_{i-1} - 1| 判定换月,超过
    阈值(默认 8%)视为换月日。
    【关键逻辑】为何用收盘缺口而非隔夜缺口:实测发现新浪主连的拼接除了"开盘
    跳空"还有"盘中拼接"——换月当天 open 仍是旧合约价、close 已是新合约价,
    伪跳空藏在日内(可超涨跌停)。回测结算用的是收盘价,收盘缺口才是虚构盈亏
    的直接来源;且无论拼接发生在开盘还是盘中,收盘缺口都等于"新合约 vs 前日
    旧合约"的水平差,检测最自洽。
    阈值取 8%(超过绝大多数商品期货涨跌停幅度)的取舍:真实行情几乎不可能
    收盘缺口超 8%,因此误判率极低;代价是会漏检 8% 以下的换月(如螺纹钢等
    两月合约价差小的品种),但残留的伪缺口小、影响有限——宁漏勿误,误判会
    主动扭曲历史价格,漏判只是没清理干净。检测点写入 rollover_dates 元数据,
    可人工核对/调整阈值。
    """
    roll = []  # 【变量】换月点 bar 下标列表
    for i in range(1, len(prices)):
        prev_close = float(prices[i - 1]["close"])  # 【变量】前一日收盘价
        if prev_close <= 0:
            continue
        gap = (float(prices[i]["close"]) / prev_close - 1.0) * 100.0  # 【变量】收盘到收盘缺口(%)
        if abs(gap) >= threshold_pct:
            roll.append(i)
    return roll


def _backward_adjust(
    prices: list[dict],
    threshold_pct: float = ROLLOVER_GAP_THRESHOLD_PCT,
    calendar_dates: set[str] | None = None,
) -> tuple[list[dict], list[int]]:
    """对主力连续价格序列做后复权,消除换月跳空(原地修改 prices)。

    【功能】在换月点 i 处,历史段(下标 < i)乘以因子 close_i / close_{i-1},
    使前一日复权收盘 = 换月日复权收盘,序列连续无跳空。
    【参数】prices: 价格 dict 列表(含 open/high/low/close/volume)。
            calendar_dates: 真实换月日期集合(date 字符串);传入则**只用这些日期**
            作为换月点(换月点已查证),不再依赖 8% 阈值。为 None 时回退到
            8% 收盘缺口启发式(见 _detect_rollover_dates)。
    【返回】(prices, roll_idx):复权后的列表与换月点下标列表。
    【关键逻辑】累计因子从后往前递推 adj[i]=adj[i+1];当 i+1 是换月点时
    adj[i]=adj[i+1]×(close_{i+1}/close_i)。最近 bar 因子=1 → 当前价不变,
    历史价被缩放。OHLC 乘同一因子保持日内相对结构,volume 不变;change_pct
    按复权后相邻收盘重算(消除伪跳空,第一根记 0)。
    """
    if calendar_dates is not None:
        # 真实日历模式:只认日历里的换月日,不猜缺口。
        roll_idx = {i for i, p in enumerate(prices) if p["date"] in calendar_dates}
    else:
        roll_idx = set(_detect_rollover_dates(prices, threshold_pct))  # 【变量】换月点集合(8% 启发式回退)
    n = len(prices)  # 【变量】K 线根数
    adj = [1.0] * n  # 【变量】每根 bar 的累计复权因子(最近一根恒 1)
    for i in range(n - 2, -1, -1):
        if (i + 1) in roll_idx:
            prev_close = float(prices[i]["close"])  # 【变量】换月前一日收盘(旧合约)
            cur_close = float(prices[i + 1]["close"])  # 【变量】换月日收盘(新合约)
            adj[i] = adj[i + 1] * (cur_close / prev_close) if prev_close > 0 else adj[i + 1]
        else:
            adj[i] = adj[i + 1]

    for i in range(n):
        a = adj[i]  # 【变量】本 bar 复权因子
        prices[i]["open"] = round(float(prices[i]["open"]) * a, 2)
        prices[i]["high"] = round(float(prices[i]["high"]) * a, 2)
        prices[i]["low"] = round(float(prices[i]["low"]) * a, 2)
        prices[i]["close"] = round(float(prices[i]["close"]) * a, 2)
        if i > 0:  # 【关键】change_pct 用复权后相邻收盘重算(跳空日不再显示伪涨跌)
            prev_close = prices[i - 1]["close"]
            prices[i]["change_pct"] = (
                round((prices[i]["close"] / prev_close - 1.0) * 100.0, 2)
                if prev_close
                else 0
            )
        else:
            prices[i]["change_pct"] = 0
    return prices, sorted(roll_idx)


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
    code_to_name = {v: k for k, v in NAME_TO_CODE.items()}  # 【变量】code_to_name:代码→中文名反向映射(把代码翻译成接口参数)
    target = varieties if varieties else list(code_to_name.keys())  # 【变量】target:本次要更新价格的品种列表

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
    price_path = PRICE_DIR / f"{name}_price.json"  # 【变量】price_path:该品种的价格 JSON 文件路径
    existing = {}
    if price_path.exists():
        with open(price_path, encoding="utf-8") as f:
            existing = json.load(f)  # 【调用函数】json.load:反序列化已有价格文件(无文件时为空)

    existing_prices = existing.get("prices", [])  # 【变量】existing_prices:已有日线列表(增量追加的基础)
    existing_dates = {p["date"] for p in existing_prices}  # 【变量】existing_dates:已有日期集合(用于去重)

    # Fetch latest daily data
    new_count = 0  # 【变量】new_count:本次新增的日期数
    try:
        # Use Sina daily data
        df = ak.futures_zh_daily_sina(symbol=f"{code}0")  # 【调用函数】AKShare 新浪期货日线接口(主连, code0)
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

        # 换月跳空后复权(原地修改):消除主力连续序列的伪跳空。
        # 最近一根 bar 因子=1 → 当前价不变;历史段为复权价,收益率真实。
        # 优先用真实换月日历(查证的主力切换日);品种不在日历(或日历缺失)时
        # 回退到 8% 收盘缺口启发式。
        cal_dates = {r["date"] for r in _load_rollover_calendar().get(name, {}).get("rollover_dates", [])}  # 【变量】该品种真实换月日
        _, roll_idx = _backward_adjust(existing_prices, calendar_dates=cal_dates or None)  # 【调用函数】后复权(就地修改价格并返回换月点)
        roll_dates = [existing_prices[i]["date"] for i in roll_idx]  # 【变量】换月点日期(元数据,供人工核对)

        # Save
        existing["prices"] = existing_prices
        existing["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing["variety_code"] = code
        existing["variety_name"] = name
        existing["adjusted"] = True  # 【变量】后复权标记(前端/上层可据此知道价格是复权价)
        existing["adjust_method"] = "backward"  # 【变量】复权方式
        existing["rollover_dates"] = roll_dates  # 【变量】换月日期清单(真实日历查证 or 8% 启发式)
        if cal_dates:
            existing["rollover_method"] = "calendar"  # 【变量】换月来源:真实日历查证
        else:
            existing["rollover_method"] = "heuristic"  # 【变量】换月来源:8% 收盘缺口启发式(回退)
            existing["rollover_threshold_pct"] = ROLLOVER_GAP_THRESHOLD_PCT  # 【变量】换月判定阈值(仅启发式)

        PRICE_DIR.mkdir(parents=True, exist_ok=True)  # 【调用函数】确保价格目录存在(不存在则递归创建)
        with open(price_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)  # 【调用函数】json.dump:写回价格文件(ensure_ascii=False 保留中文)

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
_live_cache: dict = {}  # 【变量】内存缓存:品种代码→行情字典(避免频繁请求 AKShare)
_last_fetch: float = 0  # 【变量】上一次刷新缓存的 Unix 时间戳(秒)
CACHE_TTL = 60  # seconds  # 【变量】缓存有效期(秒),60 秒内重复请求直接读缓存


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
    now = time.time()  # 【变量】now:当前 Unix 时间戳(判断缓存是否过期)
    if not _live_cache or (now - _last_fetch) > CACHE_TTL:
        try:
            # Fetch only top 8 for first load speed
            to_fetch = DEFAULT_LIVE_VARIETIES[:8] if not _live_cache else DEFAULT_LIVE_VARIETIES  # 【变量】to_fetch:本次抓取的品种(首屏只抓前 8 个提速)
            result = fetch_realtime_prices(to_fetch)  # 【调用函数】拉取实时行情并合并进缓存(首次只取 8 个,刷新补全 24 个)
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
