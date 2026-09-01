"""
Commodity Futures Data Vendor for TradingAgents.
Provides free Chinese commodity futures data via AKShare.

Hybrid Mode: Supports external data injection for higher-quality sources.
  - Drop {variety}.json in ~/.tradingagents/external_data/
  - Each function checks external data FIRST before falling back to free API
  - All responses are annotated with data provenance (FREE_API vs EXTERNAL)
  - See external_data.py for the file format specification

Supported varieties:
    ZCE (郑州): TA(PTA), MA(甲醇), FG(玻璃), SA(纯碱), UR(尿素),
                PF(短纤), CF(棉花), SR(白糖), OI(菜油), RM(菜粕),
                AP(苹果), CJ(红枣), PK(花生), SM(锰硅), SF(硅铁), PX(对二甲苯)
    SHFE (上期): RB(螺纹钢), HC(热卷), FU(燃料油), BU(沥青), RU(橡胶),
                AG(白银), AU(黄金), AL(沪铝), AO(氧化铝), CU(沪铜),
                NI(沪镍), PB(沪铅), SN(沪锡), ZN(沪锌), WR(线材)
    INE (上期能源): SC(原油), LU(低硫燃料油), NR(20号胶)
    DCE (大商): I(铁矿石), JM(焦煤), J(焦炭), M(豆粕),
                EB(苯乙烯), V(PVC), PP(聚丙烯), L(塑料), EG(乙二醇),
                C(玉米), CS(玉米淀粉), JD(鸡蛋), LH(生猪),
                P(棕榈油), Y(豆油), SH(烧碱)
    GFEX (广期): LC(碳酸锂), SI(工业硅)
    (共52品种,与 VARIETY_METADATA 一致;2026-09-01 扩充 19 非金融品种)

Note on variety scopes:
    - 52 池 = 完整分析池: 价格/指标/基差/库存/新闻/供需/情绪全部可用。
    - 思路2 采集池(60 品种映射, generate_tradingagents_sentiment.py)仅提供
      情绪数据;52 池之外品种请求价格/供需等会经
      _validate_symbol 显式拒绝,不会静默降级。
    - 详见 worklog/2026-08-21: 分裂不是病,静默才是。

Data sources (via akshare):
    - futures_main_sina: daily OHLCV + open interest (Sina Finance)
    - futures_spot_price_daily: spot price + basis data
    - futures_inventory_em: warehouse inventory (East Money)
    - futures_news_shmet: SHMET commodity news
"""

# ===========================================================================
# 【本文件在数据流中的角色】
#   这是 TradingAgents 的"商品期货实时数据供应商(Data Vendor)"。
#   Agent(如期货分析师)需要行情/基差/库存/新闻/宏观等数据时,由本文件的
#   get_futures_* 系列函数实时联网获取(通过免费库 akshare 或公开 HTTP 接口),
#   再把结果整理成 CSV/文本返回给大模型,供其分析使用。
#
# 【与 signal_analyzer 回测引擎数据源的区别】
#   - 本文件:      实时 / 半实时联网拉取(AKShare、东方财富接口),返回的是
#                  当下最新的市场数据,适合 Agent 做实时分析与多空判断。
#   - signal_analyzer.py 回测引擎: 读取本地 JSON 文件
#                  (~/.tradingagents/external_data/ 与 think2/output/trends/ 下的
#                   *_price.json / *_sentiment.json),这些文件由"思路2"项目先采集
#                  落地,再做技术指标回测、情绪回测等。它不联网,依赖落盘数据。
#   - 一句话总结: 本文件是"取实时数据",signal_analyzer 是"读本地历史数据回测"。
#
# 【Hybrid Mode(混合模式)】
#   用户可以把自己付费买到的更高质量数据(Mysteel/Wind 等)写成 JSON 文件放到
#   ~/.tradingagents/external_data/{品种}.json。本文件在基差/库存/供需等函数里
#   会"先看外部 JSON,有且未过期就用它;没有或过期才退回免费 AKShare"。
#   具体格式与合并逻辑见 external_data.py。
# ===========================================================================

import json  # 【调用包】JSON 序列化(品种元信息输出)
import logging  # 【调用包】日志输出(拉取失败告警)
import pickle  # 【调用包】基差缓存磁盘持久化(重启不丢,序列化 DataFrame)
import random  # 【调用包】随机延时(降低免费接口请求频率)
import time  # 【调用包】缓存时间戳与 TTL 过期判断
import warnings  # 【调用包】过滤 AKShare "非交易日" 信息性警告
from datetime import timedelta  # 【调用包】日期加减(get_verified_quote 拉取窗口推算)
from pathlib import Path  # 【调用包】缓存目录路径(磁盘持久化)

import pandas as pd  # 【调用包】行情 DataFrame 处理/指标计算/CSV 输出
import requests  # 【调用包】HTTP 请求(东方财富 7x24 快讯接口)

# 从 external_data.py(外部数据注入层)导入 Hybrid Mode 需要的工具函数:
#   load_external_data      读取外部 JSON(没有/过期则返回 None)
#   get_external_source_label  生成来源标签(如 "[source: Mysteel ..., updated: ...]")
#   merge_basis_data        把外部现货价合并进基差结果
#   merge_inventory_data    把外部社会/钢厂库存合并进仓单库存结果
from tradingagents.dataflows.external_data import (  # 【调用包】外部数据注入层(Hybrid Mode:读外部 JSON、合并基差/库存)
    get_external_source_label,
    load_external_data,
    merge_basis_data,
    merge_inventory_data,
)

logger = logging.getLogger(__name__)

# Suppress AKShare "非交易日" warnings — they're informational
# (one per weekend/holiday in the date range) and flood the output.
warnings.filterwarnings("ignore", message=r".*非交易日.*", category=UserWarning)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# 【缓存机制说明】
# _response_cache: 内存字典,保存 AKShare 拉取过的行情 DataFrame。
#                  键形如 "price:RB0"(主力连续合约代码),值是一个元组
#                  (拉取时刻的时间戳, DataFrame)。
# _CACHE_TTL:      缓存有效期,单位秒,这里 300 秒 = 5 分钟。
#                  在 TTL 内再次请求同一品种会直接复用缓存,避免重复请求免费接口;
#                  超过 TTL 则视为过期,重新联网拉取。
_response_cache: dict[str, tuple[float, pd.DataFrame]] = {}  # 【变量】内存缓存:键 "price:{品种代码}",值 (拉取时间戳, DataFrame)
_CACHE_TTL = 300  # 5 minutes

# _basis_cache: 基差/现货价内存缓存(键 "basis:{品种代码}")。基差接口 futures_spot_price_daily 是
#   东财网页爬取,实测单次 13~175s(2026-09-01 实测 175s),是数据看板刷新慢的根因。现货/基差为
#   日频数据,当天多次刷新无需重爬 → TTL 取 6 小时(价格缓存 5 分钟,基差例外放宽到 6h)。
_basis_cache: dict[str, tuple[float, pd.DataFrame]] = {}  # 【变量】基差内存缓存:键 "basis:{品种代码}",值 (拉取时间戳, 归一化 DataFrame)
_BASIS_CACHE_TTL = 6 * 3600  # 【变量】基差缓存有效期 6 小时(日频数据,容忍当天迟滞)

# 【变量】基差缓存磁盘持久化目录 ~/.tradingagents/cache/basis。东财基差接口单次 13~175s,
#   若只存内存,服务每次重启缓存即清空 → 重启后首个看板请求仍要冷拉(2026-09-01 实测两次
#   重启后用户反馈"提速没生效")。落到磁盘后,同一品种 6h 内跨重启也直接命中;
#   pickle 损坏/过期一律视为未命中重拉,绝不阻断主流程。
_BASIS_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "basis"


def _basis_cache_path(code: str) -> Path:
    """基差缓存磁盘文件路径(每个品种一个 pickle 文件)。"""
    return _BASIS_CACHE_DIR / f"basis_{code}.pkl"


def _basis_cache_load(code: str):
    """从磁盘加载 {code} 的基差缓存;文件缺失/损坏/过期/非表 → None(视为未命中重拉)。"""
    p = _basis_cache_path(code)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            ts, df = pickle.load(f)
        if not isinstance(df, pd.DataFrame):
            if df is None and time.time() - ts < _BASIS_CACHE_TTL:
                return ts, None  # 【无数据结论缓存】该品种基差无数据,TTL 内直接复用
            return None
        if "date" not in df.columns or df.empty:
            return None
        if time.time() - ts >= _BASIS_CACHE_TTL:
            return None
        return ts, df
    except Exception:
        return None  # 损坏文件 → 保守重拉,下次成功覆盖


def _basis_cache_save(code: str, df) -> None:
    """把 {code} 的基差缓存写盘;df 可为 None(无数据结论标记);写失败只记日志,不阻断主流程。"""
    try:
        _BASIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_basis_cache_path(code), "wb") as f:
            pickle.dump((time.time(), df), f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        logger.warning("Failed to persist basis cache for %s: %s", code, e)


def _basis_cache_merge_widest(code: str, new_df: pd.DataFrame) -> pd.DataFrame:
    """把新拉取的基差 DataFrame 与内存旧缓存合并,取【最宽】日期覆盖。

    【背景】数据看板有 60/120/180/365 四个回看档位。若缓存只有窄窗口,切到更宽档位
      必然冷拉;反过来先把缓存做宽,再切窄档位则全部命中。合并规则:新旧拼接、按日期
      去重(新数据在前 → 重叠日期以新为准)、按日期升序,最终覆盖 = 两者日期并集。
      这样同一品种通常只需一次冷拉(首次最宽档位),此后任意档位都命中缓存。
    【安全】旧缓存缺失/列不一致/合并异常 → 直接返回新拉数据,绝不用脏数据。
    """
    cached = _basis_cache.get(f"basis:{code}")
    if cached is None:
        return new_df
    _, old_df = cached
    if old_df is None or old_df.empty or "date" not in old_df.columns:
        return new_df
    try:
        new = new_df.copy()
        new["date"] = new["date"].astype(str)
        old = old_df.copy()
        old["date"] = old["date"].astype(str)
        common_cols = [c for c in new.columns if c in old.columns]
        combined = (
            pd.concat([new[common_cols], old[common_cols]])
            .drop_duplicates(subset=["date"], keep="first")
            .sort_values("date")
        )
        return combined if not combined.empty else new_df
    except Exception as e:
        logger.warning("Basis cache merge failed for %s: %s", code, e)
        return new_df


# ---------------------------------------------------------------------------
# Variety metadata & symbol mapping
# ---------------------------------------------------------------------------
# 【VARIETY_METADATA——品种元信息字典】
# 这是本文件的数据"字典"。当前收录 33 个品种键:RB/HC/I/JM/J/M/TA/MA/FG/SA/UR/PF/
# CF/SR/OI/RM/AP/CJ/PK/SM/SF(原21)+ SC/LU/FU/BU/RU/NR/EB/V/PP/L/EG/PX
# (2026-09-01 扩充能化整组 12 品种)。
# 每个品种的键是大写代码(如 "RB" 螺纹钢),值是一个小字典,包含:
#   name              中文名称
#   name_en           英文名称
#   exchange          交易所英文缩写(ZCE 郑商所 / SHFE 上期所 / DCE 大商所 / INE 上能所)
#   exchange_cn       交易所中文全称
#   main_contract     主力连续合约代码(用于 futures_main_sina,如 "RB0")
#   spot_code         现货报价代码(用于 futures_spot_price_daily,如 "RB")
#   inv_code          库存接口代码(用于 futures_inventory_em;非 ZCE 用小写如 "rb",
#                       ZCE 品种用大写如 "TA" —— akshare 表内 ZCE 仅大写,2026-09-01 修)
#   unit              合约单位(如 "10吨/手")
#   price_limit       涨跌停幅度
#   margin_rate       保证金比例
#   trading_hours     交易时间段
#   sector_cn         所属板块(黑色系/能化/农产品等)
#   description       基本面一句话介绍
#   key_factors       影响价格的关键因素列表
#   related_varieties 产业链相关品种代码列表
# 作用:把"不同接口各自不同的品种代码"统一成一份映射,让后面的行情/基差/库存
# 等函数都能通过同一个 symbol 找到各自需要的接口代码,实现"一处配置、处处使用"。

VARIETY_METADATA = {  # 【变量】33个商品期货品种的元信息字典(接口代码/合约规格/板块/关键因素)
    "RB": {
        "name": "螺纹钢",
        "name_en": "Rebar",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "RB0",
        "spot_code": "RB",
        "inv_code": "rb",
        "unit": "10吨/手",
        "price_limit": "±6%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "黑色系",
        "description": "建筑钢材，下游为房地产和基建，与铁矿石/焦炭构成黑色产业链",
        "key_factors": [
            "房地产新开工面积",
            "基建投资增速",
            "铁矿石/焦炭成本",
            "钢厂高炉开工率",
            "钢材社会库存",
            "环保限产政策",
        ],
        "related_varieties": ["HC", "I", "J", "JM"],
    },
    "HC": {
        "name": "热轧卷板",
        "name_en": "Hot-rolled Coil",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "HC0",
        "spot_code": "HC",
        "inv_code": "hc",
        "unit": "10吨/手",
        "price_limit": "±6%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "黑色系",
        "description": "板材，下游为制造业、汽车、家电，与螺纹钢构成成材端",
        "key_factors": [
            "制造业PMI",
            "汽车/家电产量",
            "出口订单",
            "钢厂热卷库存",
            "冷轧-热轧价差",
        ],
        "related_varieties": ["RB", "I"],
    },
    "I": {
        "name": "铁矿石",
        "name_en": "Iron Ore",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "I0",
        "spot_code": "I",
        "inv_code": "i",
        "unit": "100吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "黑色系",
        "description": "炼钢原料，进口依赖度高（澳洲/巴西），与螺纹钢/热卷构成黑色产业链上游",
        "key_factors": [
            "澳洲/巴西发货量",
            "港口铁矿石库存",
            "钢厂铁矿石库存天数",
            "高炉开工率",
            "海运运费(BDI)(仅新闻定性,系统无定量数据)",
            "矿山季报/年报",
        ],
        "related_varieties": ["RB", "HC", "J"],
    },
    "JM": {
        "name": "焦煤",
        "name_en": "Coking Coal",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "JM0",
        "spot_code": "JM",
        "inv_code": "jm",
        "unit": "60吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "黑色系",
        "description": "炼焦原料，用于高炉炼铁，受煤矿安全监管影响大",
        "key_factors": [
            "煤矿安全检查/停产",
            "焦化厂焦煤库存",
            "进口焦煤(蒙古/澳洲)",
            "焦化利润",
            "动力煤替代效应",
        ],
        "related_varieties": ["J", "I", "RB"],
    },
    "J": {
        "name": "焦炭",
        "name_en": "Coke",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "J0",
        "spot_code": "J",
        "inv_code": "j",
        "unit": "100吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "黑色系",
        "description": "高炉炼铁还原剂，焦煤加工品，产能过剩",
        "key_factors": [
            "焦化利润",
            "焦化厂开工率",
            "钢厂焦炭库存",
            "焦煤成本传导",
            "出口关税政策",
        ],
        "related_varieties": ["JM", "I", "RB"],
    },
    "M": {
        "name": "豆粕",
        "name_en": "Soybean Meal",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "M0",
        "spot_code": "M",
        "inv_code": "m",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品",
        "description": "饲料蛋白源，压榨大豆副产品，与CBOT大豆联动强",
        "key_factors": [
            "CBOT大豆价格",
            "进口大豆到港量",
            "油厂压榨利润",
            "饲料需求(生猪存栏)",
            "USDA月度供需报告",
            "天气(南美/北美产区)",
        ],
        "related_varieties": [],
    },
    "TA": {
        "name": "PTA",
        "name_en": "Purified Terephthalic Acid",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "TA0",
        "spot_code": "TA",
        "inv_code": "TA",
        "unit": "5吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化",
        "description": "聚酯原料，下游为纺织服装，上游为PX(对二甲苯)",
        "key_factors": [
            "原油价格(PX成本)",
            "PTA社会库存",
            "聚酯开工率",
            "纺织服装出口",
            "PX-PTA加工费",
        ],
        "related_varieties": ["SC", "MA"],
    },
    "MA": {
        "name": "甲醇",
        "name_en": "Methanol",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "MA0",
        "spot_code": "MA",
        "inv_code": "MA",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化",
        "description": "化工原料，煤制/气制/焦炉气制，下游MTO(甲醇制烯烃)",
        "key_factors": [
            "煤炭价格(煤制成本)",
            "天然气价格(气制成本)",
            "MTO/MTP开工率",
            "甲醇港口库存",
            "伊朗/中东进口",
        ],
        "related_varieties": ["TA"],
    },
    "FG": {
        "name": "玻璃",
        "name_en": "Flat Glass",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "FG0",
        "spot_code": "FG",
        "inv_code": "FG",
        "unit": "20吨/手",
        "price_limit": "±8%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(建材)",
        "description": "平板玻璃，下游为房地产和汽车，受竣工周期和产能出清影响大",
        "key_factors": [
            "房地产竣工面积",
            "汽车产量",
            "玻璃生产线开工率",
            "玻璃企业库存",
            "纯碱/天然气成本",
            "环保限产政策",
        ],
        "related_varieties": ["SA", "RB"],
    },
    "SA": {
        "name": "纯碱",
        "name_en": "Soda Ash",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "SA0",
        "spot_code": "SA",
        "inv_code": "SA",
        "unit": "20吨/手",
        "price_limit": "±8%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "纯碱（碳酸钠），玻璃生产核心原料，也用于光伏玻璃和洗涤剂，产能投放周期影响大",
        "key_factors": [
            "浮法玻璃开工率",
            "光伏玻璃投产进度",
            "纯碱企业库存",
            "氨碱/联碱法成本",
            "原盐/合成氨价格",
            "出口政策",
        ],
        "related_varieties": ["FG"],
    },
    "UR": {
        "name": "尿素",
        "name_en": "Urea",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "UR0",
        "spot_code": "UR",
        "inv_code": "UR",
        "unit": "20吨/手",
        "price_limit": "±6%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "能化(农化)",
        "description": "氮肥，农业需求为主（70%），工业需求为辅（板材/三聚氰胺），季节性明显",
        "key_factors": [
            "农业施肥季(春耕/夏播)",
            "印度招标(出口)",
            "煤炭/天然气成本",
            "尿素企业开工率",
            "企业库存/港口库存",
            "出口法检政策",
        ],
        "related_varieties": ["MA"],
    },
    "PF": {
        "name": "短纤",
        "name_en": "Polyester Short Fiber",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "PF0",
        "spot_code": "PF",
        "inv_code": "PF",
        "unit": "5吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(纺织)",
        "description": "涤纶短纤，纺织原料，下游为纱线/面料，与PTA和乙二醇构成聚酯产业链",
        "key_factors": [
            "纺织服装内外需",
            "PTA/乙二醇成本传导",
            "涤短-棉花替代价差",
            "短纤企业库存",
            "聚酯开工率",
            "出口订单",
        ],
        "related_varieties": ["TA", "CF"],
    },
    "CF": {
        "name": "棉花",
        "name_en": "Cotton",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "CF0",
        "spot_code": "CF",
        "inv_code": "CF",
        "unit": "5吨/手",
        "price_limit": "±6%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(软商品)",
        "description": "棉花，纺织核心原料，新疆主产区，受种植面积、天气和下游纺织需求影响",
        "key_factors": [
            "新疆种植面积/天气",
            "USDA全球供需报告",
            "国储棉抛储/轮入政策",
            "纺织企业开工率",
            "棉纱-棉花价差",
            "ICE美棉联动",
        ],
        "related_varieties": ["PF", "SR"],
    },
    "SR": {
        "name": "白糖",
        "name_en": "Sugar",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "SR0",
        "spot_code": "SR",
        "inv_code": "SR",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(软商品)",
        "description": "白砂糖，广西/云南主产区，受甘蔗种植面积、天气和进口政策影响大",
        "key_factors": [
            "广西/云南甘蔗种植面积",
            "巴西/印度产区天气",
            "ICE原糖联动",
            "进口糖浆/预拌粉政策",
            "食糖进口配额",
            "季节性(榨季/消费旺季)",
        ],
        "related_varieties": ["CF"],
    },
    "OI": {
        "name": "菜油",
        "name_en": "Rapeseed Oil",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "OI0",
        "spot_code": "OI",
        "inv_code": "OI",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(油脂)",
        "description": "菜籽油，食用油和生物柴油原料，与豆油/棕榈油构成油脂三巨头",
        "key_factors": [
            "国内油菜籽种植面积",
            "加拿大/澳大利亚菜籽产量",
            "豆油/棕榈油价差",
            "生物柴油政策",
            "油脂库存",
            "进口菜籽到港量",
        ],
        "related_varieties": ["RM", "Y", "P"],
    },
    "RM": {
        "name": "菜粕",
        "name_en": "Rapeseed Meal",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "RM0",
        "spot_code": "RM",
        "inv_code": "RM",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(饲料)",
        "description": "菜籽粕，水产饲料蛋白源，与豆粕有替代关系，季节性明显",
        "key_factors": [
            "水产养殖旺季(5-10月)",
            "豆粕-菜粕价差",
            "加拿大菜籽产量",
            "进口菜籽到港量",
            "饲料配方调整",
            "压榨利润",
        ],
        "related_varieties": ["OI", "M"],
    },
    "AP": {
        "name": "苹果",
        "name_en": "Apple",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "AP0",
        "spot_code": "AP",
        "inv_code": "AP",
        "unit": "10吨/手",
        "price_limit": "±6%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "农产品(生鲜)",
        "description": "鲜苹果，陕西/山东/甘肃主产区，受天气(倒春寒/冰雹)和季节性影响极大",
        "key_factors": [
            "产区天气(倒春寒/冰雹/干旱)",
            "套袋/坐果数据",
            "冷库库存",
            "时令水果替代",
            "出口量",
            "交割品级标准",
        ],
        "related_varieties": ["CJ"],
    },
    "CJ": {
        "name": "红枣",
        "name_en": "Red Dates",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "CJ0",
        "spot_code": "CJ",
        "inv_code": "CJ",
        "unit": "5吨/手",
        "price_limit": "±6%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "农产品(生鲜)",
        "description": "红枣，新疆主产区（占全国50%+），消费季节性明显（冬季旺季），耐储存",
        "key_factors": [
            "新疆产区天气",
            "坐果率/产量预估",
            "贸易商库存",
            "节日消费(春节/中秋)",
            "替代干果价格",
            "托市收购政策",
        ],
        "related_varieties": ["AP"],
    },
    "PK": {
        "name": "花生",
        "name_en": "Peanut",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "PK0",
        "spot_code": "PK",
        "inv_code": "PK",
        "unit": "5吨/手",
        "price_limit": "±5%",
        "margin_rate": "6%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "农产品(油料)",
        "description": "花生仁，油料兼食品，河南/山东主产区，与花生油/花生粕联动",
        "key_factors": [
            "河南/山东种植面积",
            "产区天气",
            "花生油加工利润",
            "进口花生(塞内加尔/苏丹)",
            "食品加工需求",
            "豆油/菜油替代",
        ],
        "related_varieties": ["OI", "M"],
    },
    "SM": {
        "name": "锰硅",
        "name_en": "Manganese Silicon",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "SM0",
        "spot_code": "SM",
        "inv_code": "SM",
        "unit": "5吨/手",
        "price_limit": "±8%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "黑色系(合金)",
        "description": "锰硅合金，炼钢脱氧剂和合金添加剂，锰矿进口依赖度高（南非/澳大利亚）",
        "key_factors": [
            "锰矿进口价格(南非/澳洲)",
            "钢厂招标价",
            "合金厂开工率",
            "螺纹钢需求联动",
            "电力成本",
            "锰矿港口库存",
        ],
        "related_varieties": ["SF", "RB"],
    },
    "SF": {
        "name": "硅铁",
        "name_en": "Silicon Iron",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "SF0",
        "spot_code": "SF",
        "inv_code": "SF",
        "unit": "5吨/手",
        "price_limit": "±8%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "黑色系(合金)",
        "description": "硅铁合金，炼钢脱氧剂，高耗电行业，宁夏/内蒙古主产区",
        "key_factors": [
            "电力成本(高耗电)",
            "钢厂招标价",
            "硅石/兰炭成本",
            "合金厂开工率",
            "螺纹钢需求联动",
            "出口关税政策",
        ],
        "related_varieties": ["SM", "RB"],
    },
    # ---- 2026-09-01 扩充:能化整组 12 品种(SC/LU/FU/BU/RU/NR/EB/V/PP/L/EG/PX) ----
    "SC": {
        "name": "原油",
        "name_en": "Crude Oil",
        "exchange": "INE",
        "exchange_cn": "上海国际能源交易中心",
        "main_contract": "SC0",
        "spot_code": "SC",
        "inv_code": "sc",
        "unit": "1000桶/手",
        "price_limit": "±8%",
        "margin_rate": "15%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-02:30",
        "sector_cn": "能化(能源)",
        "description": "上海国际能源交易中心中质含硫原油，国产+进口(中东为主)定价锚，全能化产业链成本源头",
        "key_factors": [
            "OPEC+产量政策(仅新闻定性,系统无定量数据)",
            "美国EIA/API库存(仅新闻定性,系统无库存数据)",
            "中东地缘局势(霍尔木兹海峡)(仅新闻定性)",
            "美元指数/美联储利率(仅新闻定性,系统无宏观数据)",
            "全球炼厂开工率(仅新闻定性)",
            "国产原油产量/进口到港",
        ],
        "related_varieties": ["LU", "FU", "BU", "TA"],
    },
    "LU": {
        "name": "低硫燃料油",
        "name_en": "Low-sulphur Fuel Oil",
        "exchange": "INE",
        "exchange_cn": "上海国际能源交易中心",
        "main_contract": "LU0",
        "spot_code": "LU",
        "inv_code": "lu",
        "unit": "10吨/手",
        "price_limit": "±10%",
        "margin_rate": "15%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-02:30",
        "sector_cn": "能化(燃料)",
        "description": "船用低硫燃料油(IMO 2020 硫限催生的主力船燃)，与SC原油/高硫燃料油联动的能源系品种",
        "key_factors": [
            "原油(SC/Brent)成本",
            "IMO 2020 硫含量新规",
            "新加坡高/低硫价差(仅新闻定性,系统无定量数据)",
            "航运BDI运价(仅新闻定性,系统无定量数据)",
            "船燃加注需求",
            "高硫-低硫转换溢价",
        ],
        "related_varieties": ["SC", "FU", "BU"],
    },
    "FU": {
        "name": "燃料油",
        "name_en": "Fuel Oil",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "FU0",
        "spot_code": "FU",
        "inv_code": "fu",
        "unit": "10吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(燃料)",
        "description": "高硫燃料油，原油下游馏分，电厂/船燃/炼厂原料，与SC原油和低硫燃料油联动",
        "key_factors": [
            "原油成本传导",
            "新加坡/中东高硫供需(仅新闻定性,系统无定量数据)",
            "电力/船燃/炼厂需求",
            "高硫-低硫价差",
            "库存(新加坡/富查伊拉)(仅新闻定性,系统无定量数据)",
            "季节性(取暖/发电旺季)",
        ],
        "related_varieties": ["SC", "LU", "BU"],
    },
    "BU": {
        "name": "沥青",
        "name_en": "Bitumen",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "BU0",
        "spot_code": "BU",
        "inv_code": "bu",
        "unit": "10吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(建材)",
        "description": "道路沥青，原油蒸馏减压渣油，下游为公路基建和防水卷材，季节性与基建强相关",
        "key_factors": [
            "原油成本传导",
            "公路基建投资",
            "防水卷材需求",
            "沥青厂开工率",
            "社会库存(华东/山东)",
            "季节规律(春冬淡季/夏秋旺季)",
        ],
        "related_varieties": ["SC", "FU"],
    },
    "RU": {
        "name": "橡胶",
        "name_en": "Natural Rubber",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "RU0",
        "spot_code": "RU",
        "inv_code": "ru",
        "unit": "10吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(橡胶)",
        "description": "天然橡胶，东南亚主产，下游为轮胎和汽车产业链，天气(割胶季)与库存主导",
        "key_factors": [
            "东南亚割胶季/天气(厄尔尼诺)",
            "泰/马/印尼产胶量",
            "轮胎/汽车产销",
            "青岛保税区/交易所库存",
            "RU-NR价差",
            "合成橡胶替代",
        ],
        "related_varieties": ["NR", "V"],
    },
    "NR": {
        "name": "20号胶",
        "name_en": "No.20 Rubber",
        "exchange": "INE",
        "exchange_cn": "上海国际能源交易中心",
        "main_contract": "NR0",
        "spot_code": "NR",
        "inv_code": "nr",
        "unit": "10吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-02:30",
        "sector_cn": "能化(橡胶)",
        "description": "20号标准胶，进口依赖度高的轮胎核心原料，与沪胶RU同属天然橡胶链、互为定价锚",
        "key_factors": [
            "东南亚产胶量(印尼/泰国)",
            "轮胎出口与汽车产销",
            "青岛保税区库存",
            "RU-NR价差",
            "进口船期/到港",
            "升贴水与交割品级",
        ],
        "related_varieties": ["RU"],
    },
    "EB": {
        "name": "苯乙烯",
        "name_en": "Styrene",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "EB0",
        "spot_code": "EB",
        "inv_code": "eb",
        "unit": "5吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "苯乙烯(EB)，苯+乙烯为原料，下游EPS/PS/ABS，受原油与纯苯价差和家电汽车需求影响",
        "key_factors": [
            "原油/纯苯/乙烯成本",
            "非一体化装置利润",
            "华东港口库存",
            "家电/汽车(ABS/PS)需求",
            "EPS开工率",
            "进口量(韩国/中东)",
        ],
        "related_varieties": ["SC", "TA", "PP"],
    },
    "V": {
        "name": "PVC",
        "name_en": "Polyvinyl Chloride",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "V0",
        "spot_code": "V",
        "inv_code": "v",
        "unit": "5吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "聚氯乙烯(PVC)，电石/乙烯双工艺，下游为管材型材和房地产竣工，地产链化工品种",
        "key_factors": [
            "电石/乙烯成本",
            "房地产竣工与基建",
            "PVC企业开工率",
            "华东/华南社会库存",
            "出口量(印度/东南亚)",
            "烧碱-氯平衡",
        ],
        "related_varieties": ["L", "PP", "EG", "RB"],
    },
    "PP": {
        "name": "聚丙烯",
        "name_en": "Polypropylene",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "PP0",
        "spot_code": "PP",
        "inv_code": "pp",
        "unit": "5吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "聚丙烯(PP)，丙烯聚合，下游为塑编袋/汽车改性/家电，与聚乙烯L构成通用塑料双雄",
        "key_factors": [
            "原油/丙烷/丙烯成本",
            "煤制/油制/PDH开工率",
            "塑编/汽车/家电需求",
            "PP-L价差",
            "社会库存",
            "新产能投放节奏",
        ],
        "related_varieties": ["L", "V", "EG"],
    },
    "L": {
        "name": "塑料",
        "name_en": "Linear Low-density Polyethylene",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "L0",
        "spot_code": "L",
        "inv_code": "l",
        "unit": "5吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "线型低密度聚乙烯(LLDPE)，乙烯聚合，下游为农膜/包装膜，与PP同为通用塑料",
        "key_factors": [
            "原油/乙烯成本",
            "农膜/包装膜需求(春耕)",
            "LLDPE-PP价差",
            "石化库存/港口库存",
            "进口量(中东/北美)",
            "新产能投放",
        ],
        "related_varieties": ["PP", "V"],
    },
    "EG": {
        "name": "乙二醇",
        "name_en": "Ethylene Glycol",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "EG0",
        "spot_code": "EG",
        "inv_code": "eg",
        "unit": "10吨/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "乙二醇(MEG)，煤制/油制双路线，与PTA并列为聚酯双原料，下游涤纶长丝",
        "key_factors": [
            "原油/煤/乙烯成本",
            "港口库存(华东主港)",
            "聚酯开工率",
            "煤制装置利润/开工",
            "进口量(沙特/韩国)",
            "TA-EG价差",
        ],
        "related_varieties": ["TA", "PF"],
    },
    "PX": {
        "name": "对二甲苯",
        "name_en": "Paraxylene",
        "exchange": "ZCE",
        "exchange_cn": "郑州商品交易所",
        "main_contract": "PX0",
        "spot_code": "PX",
        "inv_code": "PX",
        "unit": "5吨/手",
        "price_limit": "±10%",
        "margin_rate": "15%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(化工)",
        "description": "对二甲苯(PX)，芳烃链中间品，PTA直接原料，被原油-石脑油-重整成本与聚酯需求双重驱动",
        "key_factors": [
            "原油/石脑油成本",
            "PX-石脑油价差",
            "PTA装置开工率",
            "亚洲PX检修/投产",
            "芳烃调油(汽油旺季)",
            "港口库存",
        ],
        "related_varieties": ["TA", "SC", "EG"],
    },
    "AG": {
        "name": "白银",
        "name_en": "Silver",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "AG0",
        "spot_code": "AG",
        "inv_code": "ag",
        "unit": "15千克/手",
        "price_limit": "±9%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-02:30",
        "sector_cn": "有色(贵金属)",
        "description": "白银，贵金属与工业金属双重属性，金融属性受美元/美债利率与避险情绪驱动，光伏需求提供工业增量",
        "key_factors": [
            "美联储降息预期(仅新闻定性,系统无定量数据)",
            "美元指数(仅新闻定性,系统无定量数据)",
            "实际利率/美债收益率(仅新闻定性,系统无定量数据)",
            "光伏/工业需求",
            "金银比价",
            "交易所库存",
        ],
        "related_varieties": ["AU"],
    },
    "AU": {
        "name": "黄金",
        "name_en": "Gold",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "AU0",
        "spot_code": "AU",
        "inv_code": "au",
        "unit": "1000克/手",
        "price_limit": "±8%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-02:30",
        "sector_cn": "有色(贵金属)",
        "description": "黄金，避险与抗通胀资产，美元实际利率与央行购金为长期主线，人民币金价兼具汇率定价",
        "key_factors": [
            "美联储降息预期(仅新闻定性,系统无定量数据)",
            "美元指数(仅新闻定性,系统无定量数据)",
            "实际利率/美债收益率(仅新闻定性,系统无定量数据)",
            "央行购金",
            "地缘政治避险(仅新闻定性,系统无定量数据)",
            "人民币金价溢价",
        ],
        "related_varieties": ["AG"],
    },
    "AL": {
        "name": "沪铝",
        "name_en": "Aluminium",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "AL0",
        "spot_code": "AL",
        "inv_code": "al",
        "unit": "5吨/手",
        "price_limit": "±7%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "电解铝，供给端受产能天花板与云南电力约束，需求端看地产/新能源/汽车，现货与期货价差反映基本面",
        "key_factors": [
            "云南电力/限电",
            "电解铝产能天花板(约4500万吨)",
            "氧化铝成本",
            "铝锭社会库存",
            "新能源需求(光伏边框/汽车轻量化)",
            "铝材出口",
        ],
        "related_varieties": ["AO", "CU", "ZN"],
    },
    "AO": {
        "name": "氧化铝",
        "name_en": "Alumina",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "AO0",
        "spot_code": "AO",
        "inv_code": "ao",
        "unit": "20吨/手",
        "price_limit": "±10%",
        "margin_rate": "12%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "氧化铝，电解铝直接原料，国内产能总体充裕，几内亚/澳大利亚铝土矿供给与环保限产为价格变量",
        "key_factors": [
            "铝土矿价格(几内亚/澳大利亚)",
            "电解铝开工率/需求",
            "氧化铝现货价(山西/河南)",
            "环保限产(采暖季)",
            "港口/厂内库存",
        ],
        "related_varieties": ["AL", "SH"],
    },
    "CU": {
        "name": "沪铜",
        "name_en": "Copper",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "CU0",
        "spot_code": "CU",
        "inv_code": "cu",
        "unit": "5吨/手",
        "price_limit": "±7%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "精炼铜，宏观属性与工业属性兼具，电网/新能源为需求增量，矿端TC加工费与库存周期主导",
        "key_factors": [
            "全球铜矿供给/TC加工费",
            "LME库存(仅新闻定性,系统无定量数据)",
            "电网/基建投资",
            "新能源用铜(光伏/风电/新能源车)",
            "铜精矿港口库存",
            "人民币汇率(仅新闻定性,系统无定量数据)",
        ],
        "related_varieties": ["AL", "ZN", "NI"],
    },
    "NI": {
        "name": "沪镍",
        "name_en": "Nickel",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "NI0",
        "spot_code": "NI",
        "inv_code": "ni",
        "unit": "1吨/手",
        "price_limit": "±10%",
        "margin_rate": "12%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "镍，不锈钢主原料，印尼镍矿/镍铁供给为价格主线，新能源(硫酸镍)提供需求增量",
        "key_factors": [
            "印尼镍矿政策/出口配额",
            "镍铁/不锈钢价格",
            "LME镍库存(仅新闻定性,系统无定量数据)",
            "硫酸镍-电镍价差",
            "新能源电池需求",
        ],
        "related_varieties": ["CU", "ZN", "LC"],
    },
    "PB": {
        "name": "沪铅",
        "name_en": "Lead",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "PB0",
        "spot_code": "PB",
        "inv_code": "pb",
        "unit": "5吨/手",
        "price_limit": "±7%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "铅，铅蓄电池为主需求，再生铅占比高，环保政策与废电瓶价格影响供给弹性",
        "key_factors": [
            "再生铅开工率",
            "铅蓄电池企业开工率",
            "电动自行车/汽车产销",
            "环保限产",
            "废电瓶价格",
        ],
        "related_varieties": ["ZN"],
    },
    "SN": {
        "name": "沪锡",
        "name_en": "Tin",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "SN0",
        "spot_code": "SN",
        "inv_code": "sn",
        "unit": "1吨/手",
        "price_limit": "±9%",
        "margin_rate": "11%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "锡，半导体焊料主要需求，缅甸佤邦矿端供给扰动为关键变量，电子景气度决定需求",
        "key_factors": [
            "缅甸佤邦锡矿复产进度",
            "印尼出口政策",
            "半导体/电子行业景气",
            "锡锭库存",
            "光伏焊带需求",
        ],
        "related_varieties": ["CU", "NI"],
    },
    "ZN": {
        "name": "沪锌",
        "name_en": "Zinc",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "ZN0",
        "spot_code": "ZN",
        "inv_code": "zn",
        "unit": "5吨/手",
        "price_limit": "±7%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-01:00",
        "sector_cn": "有色(工业金属)",
        "description": "锌，镀锌/锌合金为主需求，矿端加工费(TC)与库存周期主导，基建/地产为终端驱动",
        "key_factors": [
            "锌精矿加工费(TC)",
            "国内/海外矿山复产",
            "镀锌企业开工率",
            "基建/地产需求",
            "锌锭库存",
        ],
        "related_varieties": ["CU", "AL", "PB"],
    },
    "WR": {
        "name": "线材",
        "name_en": "Wire Rod",
        "exchange": "SHFE",
        "exchange_cn": "上海期货交易所",
        "main_contract": "WR0",
        "spot_code": "WR",
        "inv_code": "wr",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "7%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "黑色系",
        "description": "线材(盘条)，建筑钢材细分，与螺纹钢同属长材，需求端看基建/地产，京津冀供给集中",
        "key_factors": [
            "基建投资增速",
            "房地产新开工面积",
            "钢厂线材库存",
            "京津冀环保限产",
            "螺线价差",
        ],
        "related_varieties": ["RB", "HC"],
    },
    "C": {
        "name": "玉米",
        "name_en": "Corn",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "C0",
        "spot_code": "C",
        "inv_code": "c",
        "unit": "10吨/手",
        "price_limit": "±6%",
        "margin_rate": "8%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(玉米链)",
        "description": "玉米，饲用/深加工双需求，新季售粮节奏与进口(美/乌/巴西)影响供应，政策收储托底",
        "key_factors": [
            "新季玉米上市/售粮进度",
            "深加工(淀粉/酒精)开工率",
            "生猪存栏(饲用需求)",
            "进口量(美国/乌克兰/巴西)",
            "收储/拍卖政策",
        ],
        "related_varieties": ["CS", "LH", "JD", "M"],
    },
    "CS": {
        "name": "玉米淀粉",
        "name_en": "Corn Starch",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "CS0",
        "spot_code": "CS",
        "inv_code": "cs",
        "unit": "10吨/手",
        "price_limit": "±5%",
        "margin_rate": "8%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(玉米链)",
        "description": "玉米淀粉，玉米深加工下游，与玉米的价差(盘面加工利润)为核心交易逻辑",
        "key_factors": [
            "玉米价格",
            "淀粉-玉米价差(盘面利润)",
            "淀粉企业开工率/库存",
            "淀粉糖/造纸需求",
        ],
        "related_varieties": ["C"],
    },
    "JD": {
        "name": "鸡蛋",
        "name_en": "Egg",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "JD0",
        "spot_code": "JD",
        "inv_code": "jd",
        "unit": "5吨/手",
        "price_limit": "±5%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "农产品(畜牧)",
        "description": "鲜鸡蛋，在产蛋鸡存栏与养殖利润主导，节假日(端午/中秋/春节)需求脉冲明显",
        "key_factors": [
            "在产蛋鸡存栏量",
            "淘汰鸡价格/淘鸡节奏",
            "蛋鸡养殖利润",
            "季节消费(端午/中秋/春节)",
            "饲料成本(玉米/豆粕)",
        ],
        "related_varieties": ["LH", "C", "M"],
    },
    "LH": {
        "name": "生猪",
        "name_en": "Live Hog",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "LH0",
        "spot_code": "LH",
        "inv_code": "lh",
        "unit": "16吨/手",
        "price_limit": "±8%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "农产品(畜牧)",
        "description": "生猪，猪周期核心品种，能繁母猪存栏/出栏节奏与养殖利润(猪粮比)驱动价格",
        "key_factors": [
            "能繁母猪存栏量",
            "生猪出栏量/出栏体重",
            "养殖利润(猪粮比)",
            "二次育肥/压栏情绪",
            "冻品库存",
            "饲料成本(玉米/豆粕)",
        ],
        "related_varieties": ["JD", "C", "M"],
    },
    "P": {
        "name": "棕榈油",
        "name_en": "Palm Oil",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "P0",
        "spot_code": "P",
        "inv_code": "p",
        "unit": "10吨/手",
        "price_limit": "±7%",
        "margin_rate": "10%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(油脂)",
        "description": "棕榈油，全球最大植物油品种，马来西亚/印尼产量与库存周期主导，生物柴油政策提供弹性",
        "key_factors": [
            "马来西亚/印尼产量与库存(仅新闻定性,系统无定量数据)",
            "产地生物柴油政策(印尼B35/B40)(仅新闻定性,系统无定量数据)",
            "季节性(斋月备货)(仅新闻定性,系统无定量数据)",
            "产地出口税/关税(仅新闻定性,系统无定量数据)",
            "豆棕价差",
        ],
        "related_varieties": ["Y", "OI"],
    },
    "Y": {
        "name": "豆油",
        "name_en": "Soybean Oil",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "Y0",
        "spot_code": "Y",
        "inv_code": "y",
        "unit": "10吨/手",
        "price_limit": "±6%",
        "margin_rate": "8%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "农产品(油脂)",
        "description": "豆油，大豆压榨副产品，跟随美豆与国内压榨利润，油脂板块核心品种",
        "key_factors": [
            "美豆走势/USDA报告(仅新闻定性,系统无定量数据)",
            "国内大豆压榨量/开机率",
            "豆油库存(港口/油厂)",
            "豆棕价差",
            "生物柴油需求(仅新闻定性,系统无定量数据)",
        ],
        "related_varieties": ["P", "OI", "M", "RM"],
    },
    "SH": {
        "name": "烧碱",
        "name_en": "Caustic Soda",
        "exchange": "DCE",
        "exchange_cn": "大连商品交易所",
        "main_contract": "SH0",
        "spot_code": "SH",
        "inv_code": "SH",
        "unit": "30吨/手",
        "price_limit": "±7%",
        "margin_rate": "9%",
        "trading_hours": "9:00-11:30, 13:30-15:00, 21:00-23:00",
        "sector_cn": "能化(氯碱)",
        "description": "烧碱(氢氧化钠)，氯碱产业链，氧化铝/造纸/纺织为主要下游，与液氯联产相互制约",
        "key_factors": [
            "氧化铝开工率(主下游)",
            "氯碱开工率/负荷",
            "液碱现货价格(山东/江苏)",
            "企业库存",
            "液氯配套/环保政策",
        ],
        "related_varieties": ["AO", "AL", "V"],
    },
    "LC": {
        "name": "碳酸锂",
        "name_en": "Lithium Carbonate",
        "exchange": "GFEX",
        "exchange_cn": "广州期货交易所",
        "main_contract": "LC0",
        "spot_code": "LC",
        "inv_code": "lc",
        "unit": "1吨/手",
        "price_limit": "±10%",
        "margin_rate": "12%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "有色(新能源)",
        "description": "碳酸锂，锂电核心原料，供需两端新能源属性强，非洲/澳洲锂矿与盐湖供给为增量来源",
        "key_factors": [
            "锂矿(澳洲/非洲)与盐湖供给",
            "电池厂/正极材料排产",
            "新能源车销量",
            "锂盐库存(港口/冶炼厂)",
            "期货-现货价差",
        ],
        "related_varieties": ["NI", "SI"],
    },
    "SI": {
        "name": "工业硅",
        "name_en": "Industrial Silicon",
        "exchange": "GFEX",
        "exchange_cn": "广州期货交易所",
        "main_contract": "SI0",
        "spot_code": "SI",
        "inv_code": "si",
        "unit": "5吨/手",
        "price_limit": "±10%",
        "margin_rate": "12%",
        "trading_hours": "9:00-11:30, 13:30-15:00",
        "sector_cn": "有色(新能源)",
        "description": "工业硅，光伏(多晶硅)/有机硅/铝合金原料，云南枯水期电价与光伏产能周期主导",
        "key_factors": [
            "云南/四川丰枯水期电价",
            "光伏(多晶硅)需求",
            "有机硅/铝合金需求",
            "工业硅开工率/库存",
            "新增产能投放",
        ],
        "related_varieties": ["LC", "AL"],
    },
}


# 【功能】校验并规范化品种代码:统一转大写;支持中文名(如"螺纹钢")转代码。
# 【参数】symbol: 用户传入的品种代码,如 "RB"、"rb"、"螺纹钢"。
# 【返回】规范化后的大写代码(如 "RB")。
# 【关键逻辑】先在 VARIETY_METADATA 里按大写代码匹配;匹配不到再用中文名反查;
#           都不支持则抛 ValueError,异常信息里列出全部支持的品种。
def _validate_symbol(symbol: str) -> str:
    """Validate and normalize commodity variety code.

    Args:
        symbol: Variety code like "RB", "rb", "螺纹钢"

    Returns:
        Normalized uppercase code.

    Raises:
        ValueError: If variety is not supported.
    """
    upper = symbol.upper().strip()
    if upper in VARIETY_METADATA:
        return upper

    # Try Chinese name match
    name_map = {v["name"]: k for k, v in VARIETY_METADATA.items()}
    if symbol.strip() in name_map:
        return name_map[symbol.strip()]

    supported = ", ".join(f"{k}({v['name']})" for k, v in VARIETY_METADATA.items())
    raise ValueError(f"Unsupported variety: '{symbol}'. Supported: {supported}")


# 【功能】返回单个品种的元信息(品种介绍、合约规格、关键影响因素、相关品种等)。
# 【参数】symbol: 品种代码(如 "RB")。
# 【返回】JSON 字符串;若品种不受支持则直接返回错误信息字符串。
# 【关键逻辑】先 _validate_symbol 校验,再取 VARIETY_METADATA 的副本,
#          用 json.dumps 序列化成带缩进的、保留中文的 JSON。
def get_variety_info(symbol: str) -> str:
    """Get metadata for a commodity variety.

    Args:
        symbol: Variety code (e.g. "RB")

    Returns:
        JSON string with variety metadata.
    """
    try:
        code = _validate_symbol(symbol)
    except ValueError as e:
        return str(e)

    meta = VARIETY_METADATA[code].copy()
    return json.dumps(meta, ensure_ascii=False, indent=2)  # 【调用函数】序列化为带缩进 JSON(ensure_ascii=False 保留中文)


# ---------------------------------------------------------------------------
# Public data functions (TradingAgents vendor interface)
# ---------------------------------------------------------------------------


# 【功能】获取商品期货的日线行情(开高低收 OHLCV + 成交量 + 持仓量)。
# 【参数】symbol: 品种代码(如 "RB");start_date/end_date: 起止日期 "YYYY-MM-DD"。
# 【返回】CSV 字符串(列为 date, open, high, low, close, volume, open_interest);
#        失败时返回以 "DATA_ERROR:" 或 "NO_DATA_AVAILABLE:" 开头的错误提示。
# 【关键逻辑】1) 使用"主力连续合约"(main_contract,如 RB0)避免换月跳空;
#           2) 调用 AKShare 的 futures_main_sina(新浪财经接口),固定从 "20200101"
#              拉全量历史(便于后续指标计算),再按日期范围过滤;
#           3) 结果带 5 分钟缓存(_response_cache),命中缓存则跳过联网;
#           4) 把中文列名重命名为英文标准列名,最后转成 CSV。
#           ★ 注意:此函数是纯免费 API 拉取,不走 Hybrid Mode 的外部 JSON。


def _cached_covers_end(cached_df, end_date) -> bool:
    """缓存行情是否已覆盖到请求的 end_date(防跨 end_date 静默截断)。

    【背景】_response_cache 的键是 "price:{main_sym}",不含请求的起止日期。
          5 分钟 TTL 窗口内,若先后请求两个不同的 end_date,后到的请求命中旧缓存
          会静默返回只到旧日期为止的数据(旧缓存末行 < 新请求的 end_date)。
    【逻辑】缓存末行日期 >= 请求 end_date → 覆盖充分,可直接用;否则返回 False,
          调用方应视为缓存未命中重新拉取。
    【注意】缓存里存的是 AKShare 原始中文列名("日期"),此处两者都兼容。
    """
    date_col = "日期" if "日期" in cached_df.columns else "date"
    if date_col not in cached_df.columns or cached_df[date_col].empty:
        return False  # 无法判断 → 视为不覆盖,重拉
    max_d = pd.to_datetime(cached_df[date_col]).max()
    try:
        return bool(max_d >= pd.to_datetime(end_date))
    except (ValueError, TypeError):
        return False  # 解析失败 → 保守重拉,绝不静默返回旧数据


def _cached_covers_range(cached_df, start_date: str, end_date: str, tolerance_days: int = 7) -> bool:
    """缓存是否覆盖请求的窗口(防静默截断),用 `_BASIS_CACHE_TTL` 管新鲜度。

    【背景】基差缓存键 "basis:{品种代码}" 不含起止日期,同一品种一次拉取要能服务
           days=60/120/180/365 全部回看档位。若缓存是窄窗口(如 30 天)而请求更宽,
           直接复用会静默返回被截断的数据。
    【关键】两端都不严格判覆盖(容差 7 天):①终点——现货价是日频、收盘后才发布,
           请求 end_date=today 时源里往往只有昨天,严格 max>=end_date 永远不满足;
           ②起点——start_date 常落在非交易日,源返回下一个交易日 → 首行日期会略晚于
           start_date,严格 min<=start_date 同样永远不满足。两个"差一天"都会让缓存
           形同虚设、每次刷新白爬一次(基差接口实测 13~175s)。容差 7 天覆盖周末/节假日,
           新鲜度交给 6h TTL 兜底;宽窗口请求(如 365 天对 60 天缓存)仍会正确判不覆盖重拉。
    【逻辑】非空 且 min<=start+7天 且 max>=end-7天 → 覆盖充分;否则视为未命中重拉。
    """
    date_col = "日期" if "日期" in cached_df.columns else "date"
    if date_col not in cached_df.columns or cached_df[date_col].empty:
        return False
    dts = pd.to_datetime(cached_df[date_col])
    try:
        start_ceil = pd.to_datetime(start_date) + timedelta(days=tolerance_days)
        end_floor = pd.to_datetime(end_date) - timedelta(days=tolerance_days)
        return bool(dts.min() <= start_ceil and dts.max() >= end_floor)
    except (ValueError, TypeError):
        return False  # 解析失败 → 保守重拉,绝不静默返回旧数据


def get_futures_price(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Get daily OHLCV + open interest data for a commodity futures contract.

    Uses the main (continuous) contract to avoid rollover gaps.

    Args:
        symbol: Variety code (e.g. "RB" for rebar)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        CSV-formatted string with columns: date, open, high, low, close, volume, open_interest
    """
    try:
        code = _validate_symbol(symbol)
    except ValueError as e:
        return f"DATA_ERROR: {e}"

    meta = VARIETY_METADATA[code]
    main_sym = meta["main_contract"]

    # Check cache
    cache_key = f"price:{main_sym}"
    now = time.time()
    if cache_key in _response_cache:
        cached_at, cached_df = _response_cache[cache_key]
        # TTL 内命中且缓存已覆盖请求的 end_date → 直接用;否则视为未命中重拉,
        # 避免"缓存键不含日期 → 5 分钟窗口内跨 end_date 请求被静默截断"。
        df = (
            cached_df.copy()
            if (now - cached_at < _CACHE_TTL and _cached_covers_end(cached_df, end_date))
            else None
        )
    else:
        df = None

    if df is None:
        try:
            from akshare import futures_main_sina  # 【调用包】AKShare 新浪主力连续行情接口

            time.sleep(random.uniform(0.1, 0.3))
            # Fetch with extended lookback for indicator calculation
            df = futures_main_sina(
                symbol=main_sym,
                start_date="20200101",
                end_date=end_date,
            )  # 【调用函数】新浪主力连续行情(从 2020 拉全量历史,便于后续指标计算)
            _response_cache[cache_key] = (now, df.copy())  # 【变量】写入缓存:拉取时间戳 + DataFrame 副本
        except ImportError:
            return (
                "DATA_ERROR: akshare is required for futures data. "
                "Install with: pip install akshare"
            )
        except Exception as e:
            logger.warning("Failed to fetch futures price for %s: %s", main_sym, e)
            return (
                f"NO_DATA_AVAILABLE: Could not fetch price data for "
                f"{meta['name']}({code}). Error: {e}"
            )

    if df.empty:
        return f"NO_DATA_AVAILABLE: No price data for {meta['name']}({code})."

    # Rename columns to English standard
    col_map = {  # 【变量】AKShare 中文列名 → 英文标准列名映射(行情接口,含动态结算价)
        "日期": "date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交量": "volume",
        "持仓量": "open_interest",
        "动态结算": "settlement",
    }
    df = df.rename(columns=col_map)

    # Keep only standard columns
    keep_cols = [c for c in col_map.values() if c in df.columns]
    # If column names didn't match, try positional
    if "date" not in df.columns and len(df.columns) >= 7:
        df.columns = ["date", "open", "high", "low", "close", "volume", "open_interest"][
            : len(df.columns)
        ]
        keep_cols = [
            c
            for c in ["date", "open", "high", "low", "close", "volume", "open_interest"]
            if c in df.columns
        ]

    df = df[keep_cols]

    # Filter date range
    df["date"] = pd.to_datetime(df["date"])
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    mask = (df["date"] >= start_dt) & (df["date"] <= end_dt)
    result = df[mask]

    if result.empty:
        return (
            f"NO_DATA_AVAILABLE: No price data for {meta['name']}({code}) "
            f"in {start_date} to {end_date}."
        )

    return result.to_csv(index=False)


# 【功能】计算商品期货的技术指标(均线/EMA/MACD/RSI/布林带/ATR/量能/持仓变化)。
# 【参数】symbol: 品种代码;start_date/end_date: 起止日期 "YYYY-MM-DD"。
# 【返回】CSV 字符串,在原行情列基础上附加指标列;数据不足时返回 NO_DATA_AVAILABLE。
# 【关键逻辑】1) 复用 get_futures_price 相同的缓存键 "price:{main_sym}" 取全量历史,
#              保证与价格接口口径一致且不重复联网;
#           2) 用 pandas 的 rolling/ewm 计算各指标:
#              SMA5/10/20/50、EMA12/26、MACD(含信号线与柱)、RSI(14)、
#              布林带(20,2)、ATR(14)、量比、持仓量变化率、偏离20日线幅度;
#           3) 最后只保留 [start_date, end_date] 区间,浮点列四舍五入到 4 位小数。
def get_futures_indicators(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Calculate technical indicators for a commodity futures contract.

    Computes: SMA(5/10/20/50), EMA(12/26), MACD, RSI(14), Bollinger Bands(20,2),
    ATR(14), volume SMA(5/20), open interest change rate.

    Args:
        symbol: Variety code (e.g. "RB")
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        CSV-formatted string with price data + indicators.
    """
    try:
        code = _validate_symbol(symbol)
    except ValueError as e:
        return f"DATA_ERROR: {e}"

    meta = VARIETY_METADATA[code]
    main_sym = meta["main_contract"]

    # Get full history for accurate indicator calculation
    cache_key = f"price:{main_sym}"
    if cache_key in _response_cache:
        _, full_df = _response_cache[cache_key]
        full_df = full_df.copy()
        # 与 get_futures_price 同一守卫:缓存末行日期必须覆盖请求的 end_date,
        # 否则视为未命中重拉(指标同样存在跨 end_date 静默截断问题)。
        if not _cached_covers_end(full_df, end_date):
            full_df = None
    else:
        full_df = None

    if full_df is None:
        # Fetch fresh
        try:
            from akshare import futures_main_sina  # 【调用包】AKShare 新浪主力连续行情接口

            full_df = futures_main_sina(
                symbol=main_sym,
                start_date="20200101",
                end_date=end_date,
            )  # 【调用函数】拉全量历史(2020 至今),保证指标窗口足够长
            _response_cache[cache_key] = (time.time(), full_df.copy())  # 【变量】写缓存,与 get_futures_price 共享同一缓存键
        except Exception as e:
            # Try using cached price data
            return f"NO_DATA_AVAILABLE: Cannot fetch data for indicators. Error: {e}"

    if full_df.empty:
        return f"NO_DATA_AVAILABLE: No data for {meta['name']}({code})."

    # Normalize columns
    col_map = {  # 【变量】AKShare 中文列名 → 英文标准列名映射(技术指标接口)
        "日期": "date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交量": "volume",
        "持仓量": "open_interest",
    }
    full_df = full_df.rename(columns=col_map)
    if "date" not in full_df.columns and len(full_df.columns) >= 7:
        full_df.columns = ["date", "open", "high", "low", "close", "volume", "open_interest"][
            : len(full_df.columns)
        ]

    full_df["date"] = pd.to_datetime(full_df["date"])
    full_df = full_df.sort_values("date")

    close = full_df["close"]
    vol = full_df["volume"]

    # --- Moving Averages ---
    full_df["sma_5"] = close.rolling(5).mean()
    full_df["sma_10"] = close.rolling(10).mean()
    full_df["sma_20"] = close.rolling(20).mean()
    full_df["sma_50"] = close.rolling(50).mean()
    full_df["ema_12"] = close.ewm(span=12, adjust=False).mean()
    full_df["ema_26"] = close.ewm(span=26, adjust=False).mean()

    # --- MACD ---
    full_df["macd"] = full_df["ema_12"] - full_df["ema_26"]
    full_df["macd_signal"] = full_df["macd"].ewm(span=9, adjust=False).mean()
    full_df["macd_histogram"] = full_df["macd"] - full_df["macd_signal"]

    # --- RSI (14) ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    full_df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # --- Bollinger Bands (20,2) ---
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    full_df["boll_mid"] = bb_mid
    full_df["boll_upper"] = bb_mid + 2 * bb_std
    full_df["boll_lower"] = bb_mid - 2 * bb_std

    # --- ATR (14) ---
    high = full_df["high"]
    low = full_df["low"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    full_df["atr_14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # --- Volume indicators ---
    full_df["volume_sma_5"] = vol.rolling(5).mean()
    full_df["volume_sma_20"] = vol.rolling(20).mean()
    full_df["volume_ratio"] = vol / full_df["volume_sma_20"].replace(0, float("nan"))

    # --- Open Interest ---
    if "open_interest" in full_df.columns:
        oi = full_df["open_interest"]
        full_df["oi_change"] = oi.diff()
        full_df["oi_change_pct"] = oi.pct_change() * 100
        full_df["oi_sma_5"] = oi.rolling(5).mean()

    # --- Price position within range ---
    full_df["pct_from_sma_20"] = (
        (close - full_df["sma_20"]) / full_df["sma_20"].replace(0, float("nan")) * 100
    )

    # Filter to requested date range
    end_dt = pd.to_datetime(end_date)
    start_dt = pd.to_datetime(start_date)
    full_df = full_df[full_df["date"] <= end_dt]
    result = full_df[full_df["date"] >= start_dt].copy()

    if result.empty:
        return f"NO_DATA_AVAILABLE: Insufficient data for indicators on {meta['name']}({code})."

    # Round for readability
    float_cols = result.select_dtypes(include=["float64"]).columns
    result[float_cols] = result[float_cols].round(4)

    return result.to_csv(index=False)


# 【功能】获取商品现货与期货的基差数据(基差 = 现货价格 - 期货价格)。
# 【参数】symbol: 品种代码;start_date/end_date: 起止日期 "YYYY-MM-DD"。
# 【返回】CSV 字符串(含现货价、主力/近月合约价、基差、基差率),末尾追加一行
#        对最新基差的解读(Backwardation 现货升水 / Contango 期货升水)。
# 【关键逻辑】1) 调用 AKShare 的 futures_spot_price_daily(东财现货+基差接口),
#              vars_list 传品种的 spot_code,日期去掉横杠转成 YYYYMMDD;
#           2) 中文列名重命名为英文;只保留主力/近月相关列;
#           3) ★ Hybrid Mode:函数末尾调用 merge_basis_data(code, api_output),
#              即"外部 JSON 里有现货价就追加一条外部数据说明;没有则原样返回"。
#              这是本文件"外部数据优先、免费接口兜底"机制的一部分。
def _build_basis_output(code: str, result: pd.DataFrame) -> str:
    """【功能】把归一化后的基差 DataFrame 构建为最终输出(四舍五入 + 解读注释 + 外部现货价合并)。

    【关键】缓存命中也走这里重建输出——round/注释/merge_basis_data 全部幂等,缓存里只存 DataFrame,
          避免把可变的字符串输出放进缓存。
    """
    out = result.copy()
    float_cols = out.select_dtypes(include=["float64"]).columns
    out[float_cols] = out[float_cols].round(4)

    # Add interpretation hints
    if "dom_basis" in out.columns:
        latest_basis = out["dom_basis"].iloc[-1]
        if latest_basis > 0:
            structure = "BACKWARDATION (现货升水：期货贴水，近强远弱，通常反映现货紧张)"
        elif latest_basis < 0:
            structure = "CONTANGO (期货升水：现货贴水，近弱远强，通常反映远期乐观预期)"
        else:
            structure = "FLAT (基差为零)"
        basis_note = (
            f"\n# Latest basis: {latest_basis:.2f} — {structure}\n"
            f"# Interpretation: positive basis = spot premium (tight supply); "
            f"negative basis = futures premium (carry/expectation driven)\n"
        )
    else:
        basis_note = ""

    api_output = out.to_csv(index=False) + basis_note

    # --- Hybrid injection: merge with external spot price if available ---
    # 【Hybrid Mode 回退逻辑(基差)】
    # merge_basis_data 会先尝试读取 ~/.tradingagents/external_data/{code}.json:
    #   有外部现货价且未过期 -> 在结果前追加一行外部现货价说明(视为更可信);
    #   没有/过期              -> 原样返回免费接口的 CSV,并打上 FREE_API 来源标记。
    merged, _used_external = merge_basis_data(code, api_output)  # 【调用函数】合并外部现货价(无外部数据则原样返回并标注 FREE_API)
    return merged


def get_futures_basis(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Get spot-futures basis (基差) data for a commodity.

    Basis = spot_price - futures_price (positive = backwardation, negative = contango)
    Also returns the basis rate (basis / spot_price).

    Args:
        symbol: Variety code (e.g. "RB")
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        CSV-formatted string with basis data.
    """
    try:
        code = _validate_symbol(symbol)
    except ValueError as e:
        return f"DATA_ERROR: {e}"

    meta = VARIETY_METADATA[code]
    spot_code = meta["spot_code"]

    # 【关键】缓存命中(6h TTL 且已覆盖请求起止区间)→ 直接复用,避免每次刷新都爬东财。
    #   背景: futures_spot_price_daily 是东财网页爬取接口,实测单次 13~175s(2026-09-01 实测 175s),
    #   是数据看板刷新慢的根因;现货/基差为日频数据,6h 内复用完全合理。
    #   内存没有时回落磁盘(服务重启后内存缓存清空,靠磁盘续命)。
    cache_key = f"basis:{code}"
    cached = _basis_cache.get(cache_key)
    if cached is None:
        cached = _basis_cache_load(code)
        if cached is not None:
            _basis_cache[cache_key] = cached
    if cached is not None:
        cached_at, cached_df = cached
        if time.time() - cached_at < _BASIS_CACHE_TTL:
            if cached_df is None:
                # 【关键】缓存"该品种基差无数据"结论:生意社 100ppi 无此品种数据时,单次探测
                #   要逐日扫完全区间(实测 AP ~200s),必须缓存,否则每次看板加载都重付。
                logger.info("basis no-data cache hit for %s", code)
                return f"NO_DATA_AVAILABLE: No basis data for {meta['name']}({code})."
            if _cached_covers_range(cached_df, start_date, end_date):
                logger.info("basis cache hit for %s", code)
                return _build_basis_output(code, cached_df)

    try:
        from akshare import futures_spot_price_daily  # 【调用包】AKShare 现货+基差接口(东方财富)

        time.sleep(random.uniform(0.1, 0.3))
        df = futures_spot_price_daily(
            start_day=start_date.replace("-", ""),
            end_day=end_date.replace("-", ""),
            vars_list=[spot_code],
        )  # 【调用函数】东财现货价+基差接口(日期去横杠,按品种 spot_code 查询)
    except ImportError:
        return "DATA_ERROR: akshare is required."
    except Exception as e:
        logger.warning("Failed to fetch basis for %s: %s", code, e)
        return (
            f"DATA_UNAVAILABLE: Could not fetch basis data for {meta['name']}({code}). Error: {e}"
        )

    if df.empty:
        # 【关键】缓存"无数据"结论:生意社无此品种现货价时逐日扫全区间实测 ~200s,
        #   不缓存则每次看板都重付;已有数据缓存则保留(可能只是源站瞬时抖动)。
        if _basis_cache.get(cache_key) is None:
            _basis_cache[cache_key] = (time.time(), None)
            _basis_cache_save(code, None)
        return f"NO_DATA_AVAILABLE: No basis data for {meta['name']}({code})."

    # Normalize column names (akshare returns Chinese columns)
    col_map = {  # 【变量】AKShare 中文列名 → 英文标准列名映射(现货/基差接口)
        "日期": "date",
        "品种": "symbol",
        "现货价格": "spot_price",
        "近月合约": "near_contract",
        "近月合约价格": "near_contract_price",
        "近月基差": "near_basis",
        "近月基差率": "near_basis_rate",
        "主力合约": "dominant_contract",
        "主力合约价格": "dominant_contract_price",
        "主力基差": "dom_basis",
        "主力基差率": "dom_basis_rate",
    }
    df = df.rename(columns=col_map)

    # Build output: focus on dominant contract basis
    keep_cols = [
        c
        for c in [
            "date",
            "spot_price",
            "dominant_contract",
            "dominant_contract_price",
            "dom_basis",
            "dom_basis_rate",
            "near_contract",
            "near_contract_price",
            "near_basis",
            "near_basis_rate",
        ]
        if c in df.columns
    ]
    result = df[keep_cols].copy()

    # 【关键】缓存成功拉取、已归一化的 DataFrame,并【合并到最宽覆盖】:老缓存可能更宽
    #   (如先拉 180 天),新拉可能更窄(如切回 60 天)——拼接去重保住最宽窗口,使后续任意
    #   ≤ 该宽度的档位请求都命中(切档位不再冷拉)。键不含起止日期,靠 _cached_covers_range
    #   判覆盖;同时写磁盘,服务重启后 6h 内仍可命中。
    result = _basis_cache_merge_widest(code, result)
    _basis_cache[cache_key] = (time.time(), result.copy())
    _basis_cache_save(code, result)
    return _build_basis_output(code, result)


# 【功能】获取商品期货的库存数据(交易所仓单库存)。
# 【参数】symbol: 品种代码;_start_date/_end_date: 形参保留但实际未用
#        (该接口一次返回全部历史,由函数内部只取最近 60 条)。
# 【返回】CSV 字符串(日期、库存、变化),末尾追加趋势解读注释(累库/去库/平稳)。
# 【关键逻辑】1) 调用 AKShare 的 futures_inventory_em(东方财富库存接口),
#              symbol 传品种的 inv_code(小写);
#           2) 只取最后 60 条以便阅读,并做趋势分析:
#              最近5日均值 vs 更早期均值的百分比变化,>+3% 记为 BUILDING 累库,
#              <-3% 记为 DRAINING 去库,否则 STABLE 平稳;
#           3) 明确提示:这是"仓单库存",不等于 35 城社会库存;
#           4) ★ Hybrid Mode:函数末尾调用 merge_inventory_data(code, api_output),
#              外部 JSON 里有社会库存/钢厂库存就合并成"Part1 仓单 + Part2 社会库存"
#              两部分一并返回给大模型。
def get_futures_inventory(
    symbol: str,
    _start_date: str = "",
    _end_date: str = "",
) -> str:
    """Get warehouse inventory data for a commodity.

    Inventory levels are a key supply-side indicator for commodity analysis.
    Rising inventory typically signals oversupply (bearish), while declining
    inventory signals tightening (bullish).

    Args:
        symbol: Variety code (e.g. "RB")
        _start_date: Start date (unused, data source returns full available history)
        _end_date: End date (unused, data source returns full available history)

    Returns:
        CSV-formatted string with inventory data.
    """
    try:
        code = _validate_symbol(symbol)
    except ValueError as e:
        return f"DATA_ERROR: {e}"

    meta = VARIETY_METADATA[code]
    inv_code = meta["inv_code"]

    try:
        from akshare import futures_inventory_em  # 【调用包】AKShare 仓单库存接口(东方财富)

        time.sleep(random.uniform(0.1, 0.3))
        df = futures_inventory_em(symbol=inv_code)  # 【调用函数】东财仓单库存(一次返回全部历史)
    except ImportError:
        return "DATA_ERROR: akshare is required."
    except Exception as e:
        logger.warning("Failed to fetch inventory for %s: %s", code, e)
        return f"DATA_UNAVAILABLE: Could not fetch inventory for {meta['name']}({code}). Error: {e}"

    if df.empty:
        return f"NO_DATA_AVAILABLE: No inventory data for {meta['name']}({code})."

    # Normalize columns
    col_map = {  # 【变量】AKShare 中文列名 → 英文标准列名映射(库存接口)
        "日期": "date",
        "库存": "inventory",
        "变化": "change",
    }
    df = df.rename(columns=col_map)

    # Keep only last 60 entries for readability
    result = df.tail(60).copy()
    result["date"] = pd.to_datetime(result["date"])

    # Add trend analysis
    if "inventory" in result.columns and len(result) >= 5:
        inv = result["inventory"]
        recent_avg = inv.tail(5).mean()
        earlier_avg = inv.head(min(20, len(result) // 2)).mean()
        if earlier_avg > 0:
            pct_change = (recent_avg - earlier_avg) / earlier_avg * 100
            trend = "BUILDING" if pct_change > 3 else ("DRAINING" if pct_change < -3 else "STABLE")
            trend_note = (
                f"\n# Warehouse receipt trend: {trend} ({pct_change:+.1f}% vs earlier period)\n"
                f"# Recent 5-day avg: {recent_avg:.0f}, Earlier avg: {earlier_avg:.0f}\n"
                f"# NOTE: This is WAREHOUSE RECEIPTS (仓单库存), NOT social inventory.\n"
                f"# Social inventory covers 35-city trader holdings and may show a different picture.\n"
            )
        else:
            trend_note = ""
    else:
        trend_note = ""

    api_output = result.to_csv(index=False) + trend_note

    # --- Hybrid injection: merge with external social/mill inventory if available ---
    # 【Hybrid Mode 回退逻辑(库存)】
    # merge_inventory_data 先读外部 JSON:
    #   有社会库存/钢厂库存 -> 输出"仓单(免费API) + 社会/钢厂库存(外部)"合并报告,
    #                           并附给大模型的解读指引(两部分同向/反向的含义);
    #   没有                 -> 仅返回免费 API 仓单 CSV,标注 FREE_API 来源。
    merged, used_external = merge_inventory_data(code, api_output)  # 【调用函数】合并外部社会/钢厂库存(无外部数据则原样返回并标注 FREE_API)
    return merged


# 【功能】抓取与商品期货相关的新闻,并按关键词过滤后返回文本。
# 【参数】symbol: 品种代码(用于追加品种专属关键词,如 RB 追加"螺纹/地产"等);
#        _start_date/_end_date: 形参保留,当前未使用。
# 【返回】格式化的新闻文本(标题+时间+来源);全部来源失败时返回 NO_DATA_AVAILABLE。
# 【关键逻辑】1) 数据源按优先级:
#              - Eastmoney 7x24 快讯(公开 HTTP 接口 np-weblist.eastmoney.com),
#                抓 30 条后用 commodity_kw(通用商品关键词)+ symbol_specific
#                (品种专属关键词)过滤,命中才保留;
#              - SHMET 商品新闻(AKShare 的 futures_news_shmet)作为兜底,
#                商品属性强,直接全部保留(取前 15 条);
#              财联社 CLS 源已停用(旧接口 404),代码被注释保留待恢复;
#           2) 两源合并后按"标题前 35 字符"去重,最多输出 20 条;
#           3) 文档明确提醒:免费接口没有 Mysteel/SMM 级别的行业细节,
#              事件类数据请走外部 JSON 机制。
def get_futures_news(
    symbol: str = "",
    _start_date: str = "",
    _end_date: str = "",
) -> str:
    """Get commodity-relevant news from multiple sources with keyword filtering.

    Sources (in order of priority):
      1. CLS (财联社) — real-time Chinese financial/policy news wire
      2. Eastmoney 7x24 — global market news (filtered for commodity relevance)
      3. SHMET — commodity-specific news (fallback)

    Keyword filtering ensures the LLM receives only articles relevant to:
      steel, iron ore, coke, coal, real estate, infrastructure, production cuts,
      environmental controls, trade policy, supply disruptions (strikes, typhoons).

    Args:
        symbol: Variety code for context (e.g. RB adds steel-specific keywords)

    Returns:
        Formatted news text with headlines, timestamps, and source labels.
    """
    import uuid  # 【调用包】生成请求跟踪号(req_trace)

    all_news: list[dict] = []

    # --- Source 1: CLS (财联社快讯) — disabled, endpoint changed ---
    # CLS now returns 404 on the old telegraphList endpoint.
    # Keeping code commented for future re-enablement when a working endpoint is found.

    # --- Source 2: Eastmoney 7x24 Global ---
    try:
        em_url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        em_params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": "30",
            "req_trace": str(uuid.uuid4()),
        }
        em_headers = {  # 【变量】请求头(User-Agent/Referer,模拟浏览器访问)
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kuaixun.eastmoney.com/",
        }
        r_em = requests.get(em_url, params=em_params, headers=em_headers, timeout=10)  # 【调用函数】抓东方财富 7x24 快讯(30条)
        d_em = r_em.json()  # 【调用函数】解析响应 JSON(取 fastNewsList 列表)
        for item in d_em.get("data", {}).get("fastNewsList", []):
            title = item.get("title", "")
            summary = item.get("summary", "")[:300]
            pub_time = item.get("showTime", "")
            all_news.append(
                {
                    "title": title,
                    "content": summary if summary else title,
                    "time": pub_time,
                    "source": "Eastmoney",
                }
            )
    except Exception as e:
        logger.warning("Eastmoney news fetch failed: %s", e)

    # --- Source 3: SHMET (commodity-specific, fallback) ---
    try:
        from akshare import futures_news_shmet  # 【调用包】AKShare SHMET 商品新闻接口

        df = futures_news_shmet()  # 【调用函数】SHMET 商品新闻(商品属性强,兜底源)
        if not df.empty:
            for _, row in df.head(15).iterrows():
                time_str = str(row.get("发布时间", ""))
                content = str(row.get("内容", ""))
                if content and content != "nan":
                    all_news.append(
                        {
                            "title": content[:100],
                            "content": content[:300],
                            "time": time_str[:16] if time_str else "",
                            "source": "SHMET",
                        }
                    )
    except Exception as e:
        logger.warning("SHMET news fetch failed: %s", e)

    if not all_news:
        return "NO_DATA_AVAILABLE: No news from any source."

    # --- Two-tier news filtering ---
    # SHMET: commodity/macro news, always relevant context
    # Eastmoney 7x24: general financial news, filtered for commodity relevance
    #
    # IMPORTANT LIMITATION: Free APIs do not provide Mysteel/SMM-grade industry news
    # (production cuts, mill maintenance, trade flows, strike details).
    # For event-specific data, use the external data JSON mechanism:
    #   ~/.tradingagents/external_data/{variety}.json

    # Broader keyword set: catch general commodity, macro, and policy news
    # that could affect ANY commodity futures (not just steel-specific)
    commodity_kw = [  # 【变量】通用商品/宏观/政策新闻过滤关键词(东财快讯命中任一即保留)
        # Black metals
        "螺纹",
        "热卷",
        "铁矿石",
        "铁矿",
        "焦炭",
        "焦煤",
        "钢材",
        "钢铁",
        "钢厂",
        "高炉",
        "电炉",
        "铁水",
        "废钢",
        "钢价",
        "钢市",
        "钢坯",
        # Non-ferrous
        "铜",
        "铝",
        "锌",
        "镍",
        "黄金",
        "白银",
        "有色金属",
        # Energy
        "原油",
        "OPEC",
        "天然气",
        "煤矿",
        "煤炭",
        # Chemicals
        "PTA",
        "甲醇",
        "聚酯",
        "烯烃",
        "MTO",
        "PX",
        "纯碱",
        "玻璃",
        "尿素",
        "短纤",
        "涤纶",
        "涤短",
        # Energy-chemicals expanded (2026-09-01, 12 能化品种)
        "燃料油",
        "低硫燃料油",
        "低硫",
        "高硫",
        "沥青",
        "橡胶",
        "天然橡胶",
        "天胶",
        "20号胶",
        "苯乙烯",
        "乙二醇",
        "PVC",
        "聚丙烯",
        "聚乙烯",
        "对二甲苯",
        "芳烃",
        "石脑油",
        "聚氯乙烯",
        # Non-ferrous/agri expanded (2026-09-01, 19 非金融品种: 有色/农产品/氯碱/新能源金属)
        "氧化铝",
        "铝土矿",
        "锡",
        "铅",
        "线材",
        "盘条",
        "碳酸锂",
        "锂矿",
        "盐湖",
        "工业硅",
        "多晶硅",
        "有机硅",
        "烧碱",
        "液碱",
        "氯碱",
        "淀粉",
        "玉米淀粉",
        "豆油",
        "鸡蛋",
        "蛋鸡",
        "淘汰鸡",
        "猪价",
        "能繁母猪",
        "仔猪",
        # Agriculture
        "豆粕",
        "大豆",
        "USDA",
        "棕榈油",
        "生猪",
        "玉米",
        "棉花",
        "白糖",
        "菜油",
        "菜粕",
        "苹果",
        "红枣",
        "花生",
        "硅铁",
        "锰硅",
        # Production & policy
        "限产",
        "减产",
        "停产",
        "检修",
        "去产能",
        "环保限产",
        "供给侧",
        "反内卷",
        "产能置换",
        "碳达峰",
        # Real estate & infra
        "房地产",
        "地产",
        "新开工",
        "保交楼",
        "基建",
        "专项债",
        "固投",
        "保障房",
        # Construction
        "建材",
        "水泥",
        "开工率",
        "工地",
        # Supply chain
        "BHP",
        "FMG",
        "力拓",
        "必和必拓",
        "淡水河谷",
        "罢工",
        "台风",
        "封库",
        # Trade & macro
        "大宗商品",
        "黑色系",
        "商品期货",
        "现货",
        "出口退税",
        "反倾销",
        "关税",
        "贸易摩擦",
        "PMI",
        "GDP",
        "央行",
        "降准",
        "LPR",
        # Regions
        "唐山",
        "邯郸",
        "山西",
        "河北",
        # Inventory & cost
        "库存",
        "累库",
        "去库",
        "港口库存",
        "利润",
        "盈利率",
    ]

    symbol_specific = {  # 【变量】品种专属新闻过滤关键词(在通用关键词基础上追加,按品种代码索引)
        "RB": ["螺纹", "钢材", "地产", "基建", "唐山", "铁水", "废钢", "钢厂", "限产", "新开工"],
        "HC": ["热卷", "钢材", "汽车", "家电", "出口", "制造业"],
        "I": ["铁矿石", "铁矿", "BHP", "FMG", "力拓", "淡水河谷", "港口", "罢工", "到港"],
        "JM": ["焦煤", "煤矿", "安检", "蒙古", "口岸"],
        "J": ["焦炭", "焦化", "提涨", "提降", "环保", "产能"],
        "M": ["豆粕", "大豆", "USDA", "压榨", "饲料", "生猪"],
        "TA": ["PTA", "聚酯", "原油", "PX", "涤纶"],
        "MA": ["甲醇", "煤化工", "烯烃", "MTO"],
        "FG": ["玻璃", "浮法", "竣工", "深加工", "LOW-E", "光伏玻璃"],
        "SA": ["纯碱", "碳酸钠", "轻碱", "重碱", "氨碱", "联碱"],
        "UR": ["尿素", "氮肥", "农业", "印度招标", "法检"],
        "CF": ["棉花", "郑棉", "美棉", "新疆棉", "抛储", "轮入"],
        "SR": ["白糖", "甘蔗", "糖价", "糖厂", "进口糖", "糖浆"],
        "OI": ["菜油", "菜籽油", "油菜籽", "生物柴油"],
        "RM": ["菜粕", "菜籽粕", "水产", "饲料"],
        "PF": ["短纤", "涤短", "涤纶", "纺织"],
        "AP": ["苹果", "冷库", "套袋", "优果率", "交割果", "早熟", "晚熟"],
        "CJ": ["红枣", "灰枣", "骏枣", "阿克苏", "若羌", "托市"],
        "PK": ["花生", "油料米", "通货米", "花生粕", "筛选厂", "进口米"],
        "SM": ["锰硅", "硅锰", "锰矿"],
        "SF": ["硅铁", "硅石"],
        # 2026-09-01 扩充:能化整组 12 品种(与 VARIETY_METADATA 同步)
        "SC": ["原油", "OPEC", "OPEC+", "EIA", "页岩油", "布伦特", "WTI", "上海原油", "炼厂", "浮仓"],
        "LU": ["低硫燃料油", "低硫油", "低硫", "船燃", "LSFO", "舟山"],
        "FU": ["燃料油", "燃油", "高硫燃料油", "高硫", "船用油"],
        "BU": ["沥青", "道路沥青", "防水卷材", "炼厂"],
        "RU": ["橡胶", "天然橡胶", "天胶", "割胶", "胶水", "轮胎"],
        "NR": ["20号胶", "20号橡胶", "印尼胶", "泰标", "烟片胶"],
        "EB": ["苯乙烯", "EPS", "PS", "ABS"],
        "V": ["PVC", "电石", "聚氯乙烯", "烧碱"],
        "PP": ["聚丙烯", "PP", "塑编", "改性"],
        "L": ["塑料", "聚乙烯", "LLDPE", "农膜", "包装膜"],
        "EG": ["乙二醇", "MEG", "聚酯", "煤制乙二醇"],
        "PX": ["对二甲苯", "PX", "芳烃", "石脑油"],
        # 2026-09-01 扩充:19 非金融品种(有色/农产品/氯碱/新能源金属,与 VARIETY_METADATA 同步)
        "AG": ["白银", "沪银", "银价", "金银比", "全球央行购银"],
        "AU": ["黄金", "沪金", "金价", "避险", "央行购金", "非农"],
        "AL": ["沪铝", "电解铝", "铝锭", "铝棒", "云南限电", "氧化铝"],
        "AO": ["氧化铝", "铝土矿", "几内亚", "澳大利亚铝矿", "山西铝业", "河南氧化铝"],
        "CU": ["沪铜", "精炼铜", "铜价", "TC加工费", "LME铜", "智利", "刚果金", "铜库存"],
        "NI": ["沪镍", "电解镍", "镍铁", "印尼镍矿", "不锈钢", "硫酸镍", "高冰镍"],
        "PB": ["沪铅", "再生铅", "铅酸电池", "蓄电池", "废电瓶", "铅冶炼"],
        "SN": ["沪锡", "锡价", "缅甸佤邦", "半导体", "焊料", "印尼锡"],
        "ZN": ["沪锌", "锌锭", "镀锌", "锌精矿", "加工费", "锌矿"],
        "WR": ["线材", "盘条", "高线", "建筑钢材", "线螺价差"],
        "C": ["玉米", "玉米价格", "售粮", "玉米拍卖", "进口玉米", "玉米库存"],
        "CS": ["玉米淀粉", "淀粉", "淀粉糖", "淀粉企业"],
        "JD": ["鸡蛋", "蛋价", "蛋鸡", "淘汰鸡", "鸡苗", "禽蛋"],
        "LH": ["生猪", "猪价", "能繁母猪", "仔猪", "猪肉", "猪粮比", "二次育肥"],
        "P": ["棕榈油", "马棕", "印尼棕榈油", "MPOB", "斋月", "生物柴油"],
        "Y": ["豆油", "美豆", "大豆压榨", "豆油库存", "港口大豆", "豆棕价差"],
        "SH": ["烧碱", "氢氧化钠", "液碱", "氯碱", "片碱"],
        "LC": ["碳酸锂", "锂", "锂矿", "盐湖", "电碳", "工碳", "氢氧化锂", "锂电"],
        "SI": ["工业硅", "多晶硅", "有机硅", "枯水期", "云南硅", "硅石"],
    }
    extra_kw = symbol_specific.get(symbol.upper(), [])
    all_kw = commodity_kw + extra_kw

    # SHMET: always include (it's commodities-focused by design)
    shmet_news = [n for n in all_news if n["source"] == "SHMET"]
    # Eastmoney: filter for commodity/policy relevance
    em_news = [n for n in all_news if n["source"] == "Eastmoney"]
    em_filtered = []
    for n in em_news:
        text = n["title"] + " " + n["content"]
        for kw in all_kw:
            if kw in text:
                em_filtered.append(n)
                break

    combined = shmet_news + em_filtered

    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for n in combined:
        prefix = n["title"][:35]
        if prefix not in seen:
            seen.add(prefix)
            unique.append(n)

    lines = [
        "# COMMODITY & MACRO NEWS (multi-source)",
        f"# SHMET: {len(shmet_news)} articles | Eastmoney keyword-filtered: {len(em_filtered)}/{len(em_news)}",
        "# NOTE: For industry-specific events (限产/罢工/检修), use external data JSON.",
        "#       Free APIs provide macro/commodity context, not Mysteel-grade detail.",
        "",
    ]

    for n in unique[:20]:
        source_tag = f"[{n['source']}]"
        time_tag = n["time"] if n["time"] else "??"
        title = n["title"][:150]
        content = n["content"][:200] if n["content"] != title else ""
        line = f"{time_tag} {source_tag} {title}"
        if content and content[:100] != title[:100]:
            line += f"\n  {content}"
        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Macro & Industry Data (P0 enhancement)
# ---------------------------------------------------------------------------


# 【功能】抓取影响商品期货的中国宏观指标,汇总成文本报告。
# 【参数】start_date/end_date: 形参保留,当前未使用(接口默认返回全量最新)。
# 【返回】格式化文本:GDP(季度同比)、制造业PMI(含荣枯线判断)、固定资产投资、
#        房地产景气指数、工业增加值、建筑业指数(日度)。
# 【关键逻辑】1) 全部来自 akshare 的 macro_* 系列免费接口:
#              macro_china_gdp / macro_china_pmi / macro_china_gdzctz /
#              macro_china_real_estate / macro_china_gyzjz / macro_china_construction_index;
#           2) 每个指标各自 try/except,单个接口失败不影响其他指标,
#              失败处输出 "UNAVAILABLE (异常信息)";
#           3) 在房地产/建筑业部分给出与螺纹钢需求的联动解读(供大模型参考)。
def get_futures_macro(start_date: str = "", end_date: str = "") -> str:
    """Fetch key China macroeconomic indicators for commodity analysis.

    Data sources (via akshare / Eastmoney):
      - GDP (quarterly, YoY%)
      - PMI (manufacturing, monthly)
      - Fixed Asset Investment (monthly, YoY%)
      - Real Estate Climate Index (monthly)
      - Industrial Production / Value-Added (monthly, YoY%)
      - Construction Industry Index (daily)

    Returns a formatted text report suitable for LLM consumption.
    """
    import akshare as ak  # 【调用包】AKShare 宏观指标系列接口(macro_*)

    parts = [
        "# CHINA MACROECONOMIC INDICATORS (for commodity futures analysis)",
        "# Data_Source: FREE_API (AKShare / Eastmoney)",
        "# Note: latest available data points shown. Some series lag 1-2 months.",
        "",
    ]

    # --- GDP ---
    try:
        gdp = ak.macro_china_gdp()  # 【调用函数】GDP 季度数据(含三产业同比)
        if not gdp.empty:
            latest = gdp.iloc[-1]
            parts.append("## GDP (季度)")
            parts.append(f"  最新季度: {latest.get('日期', 'N/A')}")
            parts.append(f"  GDP 绝对值: {latest.get('国内生产总值-绝对值', 'N/A')} 亿元")
            parts.append(f"  GDP 同比: {latest.get('国内生产总值-同比增长', 'N/A')}%")
            parts.append(f"  第一产业同比: {latest.get('第一产业-同比增长', 'N/A')}%")
            parts.append(f"  第二产业同比: {latest.get('第二产业-同比增长', 'N/A')}%")
            parts.append(f"  第三产业同比: {latest.get('第三产业-同比增长', 'N/A')}%")
            # Recent trend
            recent = gdp.tail(4)
            parts.append(
                f"  近四个季度趋势: {', '.join(str(x) for x in recent['国内生产总值-同比增长'].tail(4))}"
            )
            parts.append("")
    except Exception as e:
        parts.append(f"## GDP: UNAVAILABLE ({e})")
        parts.append("")

    # --- PMI ---
    try:
        pmi = ak.macro_china_pmi()  # 【调用函数】制造业/非制造业 PMI(附荣枯线判断)
        if not pmi.empty:
            latest = pmi.iloc[-1]
            parts.append("## PMI (制造业采购经理指数)")
            parts.append(f"  最新月份: {latest.get('月份', 'N/A')}")
            parts.append(f"  制造业PMI: {latest.get('制造业-指数', 'N/A')}")
            parts.append(f"  非制造业PMI: {latest.get('非制造业-指数', 'N/A')}")
            # Recent 3 months
            recent = pmi.tail(3)
            parts.append(
                f"  近3个月制造业PMI: {', '.join(str(x) for x in recent['制造业-指数'].tail(3))}"
            )
            below_50 = float(latest.get("制造业-指数", 50)) < 50
            parts.append(
                f"  荣枯线判断: {'**低于50荣枯线，经济收缩**' if below_50 else '高于50荣枯线，经济扩张'}"
            )
            parts.append("")
    except Exception as e:
        parts.append(f"## PMI: UNAVAILABLE ({e})")
        parts.append("")

    # --- Fixed Asset Investment ---
    try:
        fai = ak.macro_china_gdzctz()  # 【调用函数】固定资产投资(FAI,当月/累计同比)
        if not fai.empty:
            latest = fai.iloc[-1]
            parts.append("## 固定资产投资 (FAI)")
            parts.append(f"  最新月份: {latest.get('月份', 'N/A')}")
            parts.append(f"  当月值: {latest.get('当月', 'N/A')} 亿元")
            parts.append(f"  同比增长: {latest.get('同比增长', 'N/A')}%")
            parts.append(f"  累计值: {latest.get('累计值', 'N/A')} 亿元")
            # Recent trend
            recent = fai.tail(3)
            trend = [str(x) for x in recent["同比增长"].tail(3) if str(x) != "nan"]
            if trend:
                parts.append(f"  近3个月同比趋势: {', '.join(trend)}%")
            parts.append("")
    except Exception as e:
        parts.append(f"## FAI: UNAVAILABLE ({e})")
        parts.append("")

    # --- Real Estate ---
    try:
        re = ak.macro_china_real_estate()  # 【调用函数】房地产景气指数(与螺纹钢需求强相关)
        if not re.empty:
            latest = re.iloc[-1]
            col_date = next((c for c in re.columns if "日期" in str(c)), re.columns[0])
            col_val = next(
                (c for c in re.columns if "指数值" in str(c) or "值" in str(c)), re.columns[1]
            )
            col_chg = next(
                (c for c in re.columns if "涨跌幅" in str(c) and "近" not in str(c)), None
            )
            parts.append("## 房地产景气指数")
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            real_val = latest.get(col_val, "N/A")
            parts.append(f"  指数值: {real_val}")
            if col_chg:
                parts.append(f"  涨跌幅: {latest.get(col_chg, 'N/A')}%")
            recent = re.tail(6)
            recent_vals = [str(x) for x in recent[col_val].tail(6)]
            parts.append(f"  近6个月指数走势: {', '.join(recent_vals)}")
            parts.append(
                "  **判断**: 指数持续低迷表明房地产行业仍在筑底，利空螺纹钢需求（房地产占螺纹钢需求约60%）。"
            )
            parts.append("")
    except Exception as e:
        parts.append(f"## Real Estate: UNAVAILABLE ({e})")
        parts.append("")

    # --- Industrial Production ---
    try:
        ip = ak.macro_china_gyzjz()  # 【调用函数】工业增加值(月度同比/累计增长)
        if not ip.empty:
            latest = ip.iloc[-1]
            parts.append("## 工业增加值")
            parts.append(f"  最新月份: {latest.get('月份', 'N/A')}")
            parts.append(f"  同比增长: {latest.get('同比增长', 'N/A')}%")
            parts.append(f"  累计增长: {latest.get('累计增长', 'N/A')}%")
            recent = ip.tail(3)
            trend = [str(x) for x in recent["同比增长"].tail(3)]
            parts.append(f"  近3个月同比趋势: {', '.join(trend)}%")
            parts.append("")
    except Exception as e:
        parts.append(f"## Industrial Production: UNAVAILABLE ({e})")
        parts.append("")

    # --- Construction Industry Index ---
    try:
        ci = ak.macro_china_construction_index()  # 【调用函数】建筑业指数(日度,建筑活动强弱直接反映)
        if not ci.empty:
            latest = ci.iloc[-1]
            col_date = next((c for c in ci.columns if "日期" in str(c)), ci.columns[0])
            col_val = next(
                (c for c in ci.columns if "指数值" in str(c) or "值" in str(c)), ci.columns[1]
            )
            parts.append("## 建筑业指数 (日度)")
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            parts.append(f"  指数值: {latest.get(col_val, 'N/A')}")
            # Weekly trend (last 5 trading days)
            recent = ci.tail(5)
            recent_vals = [str(x) for x in recent[col_val].tail(5)]
            parts.append(f"  近5个交易日: {', '.join(recent_vals)}")
            parts.append(
                "  **与钢铁需求关系**: 建筑业是螺纹钢最大下游，指数走势直接反映建筑活动强弱。"
            )
            parts.append("")
    except Exception as e:
        parts.append(f"## Construction Index: UNAVAILABLE ({e})")
        parts.append("")

    return "\n".join(parts)


# 【功能】汇总一个品种的供需两侧指标(产量、成交、开工率、利润、库存、事件等)。
# 【参数】variety: 品种代码(如 "RB");start_date/end_date: 形参保留,当前未使用。
# 【返回】格式化供需报告文本;无外部数据时也会给出免费 API 的建筑业/地产指数部分。
# 【关键逻辑】1) ★ Hybrid Mode 核心体现:先 load_external_data(variety) 读外部 JSON,
#              有则把周度产量/铁水产量/开工率/钢厂利润/建材成交/社会库存/
#              铁矿港口库存/关键事件 逐项输出,并标注来源(get_external_source_label);
#              没有外部文件则提示如何创建(见 RB.json.sample);
#           2) 后半部分始终用免费 API(建筑业指数、房地产景气指数)补充;
#           3) 外部数据字段缺失时用 .get(key, 'N/A') 兜底,不会崩。
def get_futures_supply_demand(variety: str, start_date: str = "", end_date: str = "") -> str:
    """Fetch supply-side and demand-side indicators for a commodity variety.

    Combines:
      1. External data (Mysteel/social inventory, production data, transaction volume)
      2. Free API data (construction index, industry indicators)

    External data file (~/.tradingagents/external_data/{variety}.json) can include:
      - weekly_production: rebar weekly output (万吨)
      - daily_transaction: national building materials transaction volume (万吨)
      - hot_metal_output: daily hot metal / pig iron output (万吨)
      - bf_operating_rate: blast furnace operating rate (%)
      - eaf_operating_rate: EAF operating rate (%)
      - mill_profit: mill profit margin (元/吨)

    Args:
        variety: Variety code, e.g. "RB"

    Returns:
        Formatted supply-demand report string.
    """
    parts = [
        f"# SUPPLY-DEMAND INDICATORS for {variety}",
        "# Combines external data (Mysteel, Wind, etc.) with free API data",
        "",
    ]

    # --- External Data Section ---
    ext = load_external_data(variety)  # 【调用函数】读外部 JSON 供需数据(无/过期返回 None)
    if ext:
        data = ext.get("data", {})
        source_label = get_external_source_label(variety)  # 【调用函数】生成外部数据来源标签(标注在报告头)
        parts.append(f"## External Data (来源: {source_label})")
        parts.append("")

        # Weekly production
        wp = data.get("weekly_production")
        if wp:
            parts.append("### 螺纹钢周度产量")
            parts.append(f"  产量: {wp.get('value', 'N/A')} {wp.get('unit', '万吨')}")
            parts.append(
                f"  环比: {wp.get('change_wow', 'N/A')} ({wp.get('change_wow_pct', 'N/A')}%)"
            )
            parts.append(f"  备注: {wp.get('note', 'N/A')}")
            parts.append("")

        # Hot metal output
        hm = data.get("hot_metal_output")
        if hm:
            parts.append("### 日均铁水产量")
            parts.append(f"  产量: {hm.get('value', 'N/A')} {hm.get('unit', '万吨')}")
            parts.append(f"  环比: {hm.get('change_wow', 'N/A')}")
            parts.append(f"  备注: {hm.get('note', 'N/A')}")
            parts.append("")

        # Capacity utilization (already partially covered by existing data)
        cap = data.get("capacity_utilization")
        if cap:
            parts.append("### 开工率")
            parts.append(f"  高炉开工率: {cap.get('bf_operating_rate', 'N/A')}%")
            parts.append(f"  电炉开工率: {cap.get('eaf_operating_rate', 'N/A')}%")
            parts.append(f"  备注: {cap.get('note', 'N/A')}")
            parts.append("")

        # Mill profit
        profit = data.get("profit_margin")
        if profit:
            parts.append("### 钢厂利润")
            parts.append(
                f"  高炉利润: {profit.get('bf_mill_profit', 'N/A')} {profit.get('unit', '元/吨')}"
            )
            parts.append(
                f"  电炉利润: {profit.get('eaf_mill_profit', 'N/A')} {profit.get('unit', '元/吨')}"
            )
            parts.append(f"  备注: {profit.get('note', 'N/A')}")
            parts.append("")

        # Daily building materials transaction volume
        dt = data.get("daily_transaction")
        if dt:
            parts.append("### 建材日成交量 (全国主流贸易商)")
            parts.append(f"  成交量: {dt.get('value', 'N/A')} {dt.get('unit', '万吨')}")
            parts.append(f"  5日均值: {dt.get('avg_5d', 'N/A')} 万吨")
            parts.append(f"  20日均值: {dt.get('avg_20d', 'N/A')} 万吨")
            parts.append(f"  趋势: {dt.get('trend', 'N/A')}")
            parts.append(f"  备注: {dt.get('note', 'N/A')}")
            parts.append("")

        # Social inventory (already covered but may have updates)
        si = data.get("social_inventory")
        if si:
            parts.append("### 社会库存 (35城)")
            parts.append(f"  库存: {si.get('value', 'N/A')} {si.get('unit', '万吨')}")
            parts.append(
                f"  环比: {si.get('change_wow', 'N/A')} ({si.get('change_wow_pct', 'N/A')}%)"
            )
            parts.append(f"  趋势: {si.get('trend', 'N/A')}")
            parts.append("")

        # Iron ore port inventory
        io = data.get("iron_ore_port_inventory")
        if io:
            parts.append("### 铁矿石港口库存")
            parts.append(f"  库存: {io.get('value', 'N/A')} {io.get('unit', '万吨')}")
            parts.append(f"  环比: {io.get('change_wow', 'N/A')}")
            parts.append("")

        # Key events
        ke = data.get("key_events")
        if ke:
            parts.append("### 关键行业事件 (Key Industry Events)")
            parts.append(f"  更新日期: {ke.get('updated', 'N/A')}")
            for item in ke.get("items", []):
                parts.append(f"  - **{item.get('event', '?')}**: {item.get('detail', '')}")
                parts.append(f"    影响: {item.get('impact', '')} (来源: {item.get('source', '')})")
            parts.append("")

    else:
        parts.append("## External Data: NOT AVAILABLE")
        parts.append(
            "  (No external data file found. Create ~/.tradingagents/external_data/"
            f"{variety}.json to enable supply-demand indicators and key industry events.)"
        )
        parts.append("  See RB.json.sample for the file format.")
        parts.append("")

    # --- Free API: Construction Index ---
    parts.append("## Construction Industry Index (FREE API)")
    try:
        import akshare as ak  # 【调用包】AKShare 免费接口(建筑业指数)

        ci = ak.macro_china_construction_index()  # 【调用函数】建筑业指数(日度,免费兜底补充)
        if not ci.empty:
            col_date = next((c for c in ci.columns if "日期" in str(c)), ci.columns[0])
            col_val = next(
                (c for c in ci.columns if "指数值" in str(c) or "值" in str(c)), ci.columns[1]
            )
            latest = ci.iloc[-1]
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            parts.append(f"  指数值: {latest.get(col_val, 'N/A')}")
            recent = ci.tail(5)
            recent_vals = [str(x) for x in recent[col_val].tail(5)]
            parts.append(f"  近5日走势: {', '.join(recent_vals)}")
        else:
            parts.append("  No data available.")
    except Exception as e:
        parts.append(f"  UNAVAILABLE: {e}")
    parts.append("")

    # --- Free API: Real Estate Climate Index ---
    parts.append("## Real Estate Climate Index (FREE API)")
    try:
        import akshare as ak  # 【调用包】AKShare 免费接口(房地产景气指数)

        re = ak.macro_china_real_estate()  # 【调用函数】房地产景气指数(免费兜底补充,关联螺纹钢需求)
        if not re.empty:
            col_date = next((c for c in re.columns if "日期" in str(c)), re.columns[0])
            col_val = next(
                (c for c in re.columns if "指数值" in str(c) or "值" in str(c)), re.columns[1]
            )
            latest = re.iloc[-1]
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            parts.append(f"  指数值: {latest.get(col_val, 'N/A')}")
            recent = re.tail(6)
            recent_vals = [str(x) for x in recent[col_val].tail(6)]
            parts.append(f"  近6月走势: {', '.join(recent_vals)}")
            parts.append(
                "  **与钢铁需求关系**: 房地产是螺纹钢最大下游(约60%)，该指数持续低迷意味着螺纹钢需求端缺乏支撑。"
            )
        else:
            parts.append("  No data available.")
    except Exception as e:
        parts.append(f"  UNAVAILABLE: {e}")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Verified Quote Snapshot — deterministic source of truth for price/indicator values
# ---------------------------------------------------------------------------


# 【功能】返回某个目标日期"确定性的、经过核验"的行情快照(开高低收+量+仓+关键指标)。
#        设计为数值主张的唯一真相来源:所有分析师做"精确数字"论断时必须用这里。
# 【参数】symbol: 品种代码;date: 目标日期 "YYYY-MM-DD";
#        start_date/end_date: 形参保留(兼容调用方),内部由 date 自动推算。
# 【返回】VERIFIED_SNAPSHOT 格式文本(含精确 OHLCV、日涨跌%、SMA5/SMA20、
#        价格相对20日线的位置);出错时返回 VERIFIED_SNAPSHOT_ERROR/UNAVAILABLE。
# 【关键逻辑】1) 以 date 为基准,拉取 [date-30天, date+5天] 的价格窗口
#              (复用 get_futures_price);
#           2) 手工解析 CSV 找目标日期那一行;若目标日是非交易日,则退而取
#              区间内最新一天并注明;
#           3) 由全部收盘价序列计算日涨跌%、SMA5、SMA20;
#           4) 输出中强调"冲突要上报,不要自己编一个调和数字"。
def get_verified_quote(
    symbol: str, date: str = "", start_date: str = "", end_date: str = ""
) -> str:
    """Get a deterministic, verified OHLCV + key indicator snapshot for a specific date.

    This is the SINGLE SOURCE OF TRUTH for numeric price/indicator claims.
    All analysts MUST prefer this over self-retrieved price data when making
    exact numeric claims. If another tool's output conflicts with this snapshot,
    flag the discrepancy rather than inventing a reconciled number.

    Args:
        symbol: Variety code, e.g. "RB", "I", "JM".
        date: Target analysis date in YYYY-MM-DD format.

    Returns:
        Structured text with VERIFIED_SNAPSHOT header, OHLCV, volume, OI,
        and key computed indicators for the exact date.
    """
    try:
        code = _validate_symbol(symbol)
    except ValueError as e:
        return f"VERIFIED_SNAPSHOT_ERROR: {e}"

    meta = VARIETY_METADATA[code]

    # Step 1: Get price data covering the target date
    if not date:
        return "VERIFIED_SNAPSHOT_ERROR: date parameter is required."

    # Fetch a window around the target date (30 days before, 5 days after)
    from datetime import datetime as dt  # 【调用包】日期字符串解析(YYYY-MM-DD)

    try:
        target_dt = dt.strptime(date, "%Y-%m-%d")
    except ValueError:
        return f"VERIFIED_SNAPSHOT_ERROR: invalid date format '{date}', use YYYY-MM-DD."

    fetch_start = (target_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    fetch_end = (target_dt + timedelta(days=5)).strftime("%Y-%m-%d")

    price_result = get_futures_price(symbol, fetch_start, fetch_end)  # 【调用函数】复用价格接口拉目标日前后窗口行情(30天前~5天后)
    if price_result.startswith("NO_DATA") or price_result.startswith("DATA_ERROR"):
        return f"VERIFIED_SNAPSHOT_UNAVAILABLE: {price_result}"

    # Step 2: Parse the CSV to find the exact date row
    lines = price_result.strip().split("\n")
    target_row = None
    latest_row = None
    for line in lines:
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        row_date = parts[0].strip()
        if row_date == date:
            target_row = parts
        latest_row = parts  # Keep track of latest available

    if target_row is None and latest_row is not None:
        # Target date may be non-trading day — use latest available
        target_row = latest_row
        date_note = f"(target date {date} is non-trading, using latest: {latest_row[0]})"
    elif target_row is None:
        return f"VERIFIED_SNAPSHOT_UNAVAILABLE: No price data found near {date}."
    else:
        date_note = ""

    # Step 3: Compute key indicators
    o = float(target_row[1]) if len(target_row) > 1 else 0.0
    h = float(target_row[2]) if len(target_row) > 2 else 0.0
    low = float(target_row[3]) if len(target_row) > 3 else 0.0
    c = float(target_row[4]) if len(target_row) > 4 else 0.0
    v = float(target_row[5]) if len(target_row) > 5 else 0.0
    oi = float(target_row[6]) if len(target_row) > 6 else 0.0

    # Compute daily change from all price data
    all_closes = []
    for line in lines:
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        parts2 = line.split(",")
        if len(parts2) >= 5:
            all_closes.append(float(parts2[4]))

    prev_close = all_closes[-2] if len(all_closes) >= 2 else c
    day_change = (c - prev_close) / prev_close * 100 if prev_close != 0 else 0.0

    # 5-day SMA
    sma5_closes = all_closes[-5:] if len(all_closes) >= 5 else all_closes
    sma5 = sum(sma5_closes) / len(sma5_closes)

    # 20-day SMA
    sma20_closes = all_closes[-20:] if len(all_closes) >= 20 else all_closes
    sma20 = sum(sma20_closes) / len(sma20_closes)

    lines_out = [
        "=" * 50,
        f"VERIFIED_SNAPSHOT | {meta['name']}({code}) | {target_row[0]} {date_note}",
        "Source: AKShare / Sina Finance | Status: TRUSTED — use for exact numeric claims",
        "=" * 50,
        "",
        f"Exchange: {meta['exchange_cn']} | Unit: {meta['unit']}",
        f"Price Limit: {meta['price_limit']} | Margin: {meta['margin_rate']}",
        "",
        "--- Exact OHLCV ---",
        f"Open:       {o:>12.2f}",
        f"High:       {h:>12.2f}",
        f"Low:        {low:>12.2f}",
        f"Close:      {c:>12.2f}",
        f"Volume:     {v:>12.0f}",
        f"Open Int:   {oi:>12.0f}",
        f"Day Change: {day_change:>+11.2f}%",
        "",
        "--- Key Levels ---",
        f"SMA(5):     {sma5:>12.2f}  (short-term trend)",
        f"SMA(20):    {sma20:>12.2f}  (medium-term trend)",
        f"Price vs SMA20: {'ABOVE' if c > sma20 else 'BELOW'} by {abs(c - sma20):.2f}",
        "",
        "--- Guidelines ---",
        "1. Use ONLY the values above for any numeric claims about price/levels.",
        "2. If your self-retrieved data shows different numbers, FLAG the discrepancy.",
        "3. Do NOT reconcile conflicting numbers — report the conflict.",
        "4. For trend/multi-day analysis, call get_futures_price or get_futures_indicators.",
    ]

    return "\n".join(lines_out)


# ---------------------------------------------------------------------------
# Sentiment data (social media — from 思路2 project)
# ---------------------------------------------------------------------------


# 【功能】获取商品品种的社交媒体情绪数据(来自"思路2"项目采集的微博/知乎/小红书)。
# 【参数】symbol: 品种代码(如 "RB");start_date/end_date: 形参保留,当前未使用。
# 【返回】格式化情绪报告文本;无数据时返回对应的"无数据"提示。
# 【关键逻辑】本函数只是薄封装:真正实现转发给 sentiment_data 模块里的同名函数,
#          由它去读 ~/.tradingagents/external_data/{symbol}_sentiment.json。
def get_futures_sentiment(symbol: str, start_date: str = "", end_date: str = "") -> str:
    """Get social media sentiment data for a commodity variety.

    Loads sentiment JSON from ~/.tradingagents/external_data/{symbol}_sentiment.json
    (generated by 思路2's generate_tradingagents_sentiment.py) and formats it as
    a structured prompt for the Sentiment Analyst LLM.

    Args:
        symbol: Variety code, e.g. "RB", "I", "JM".

    Returns:
        Formatted sentiment report text or "no data" message.
    """
    from tradingagents.dataflows.sentiment_data import (
        get_futures_sentiment as _impl,  # 【调用包】情绪数据模块(读 *_sentiment.json 并格式化)
    )

    return _impl(symbol)  # 【调用函数】转发给 sentiment_data 模块的同名实现


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
# 直接运行本文件(python tradingagents/dataflows/commodity_futures.py)时,
# 会用螺纹钢 RB 依次自测:品种信息 / 行情 / 技术指标 / 基差 / 库存 / 新闻。
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== Testing Commodity Futures Data Layer for Rebar (RB) ===\n")

    # Test variety info
    print("--- Variety Info ---")
    print(get_variety_info("RB"))

    # Test price data
    print("\n--- Price Data (last 30 days) ---")
    price_data = get_futures_price("RB", "2026-06-15", "2026-07-14")
    print(price_data[:800] if len(price_data) > 800 else price_data)

    # Test indicators
    print("\n--- Indicators ---")
    ind_data = get_futures_indicators("RB", "2026-06-15", "2026-07-14")
    # Only show last 5 rows to keep output readable
    lines = ind_data.split("\n")
    if len(lines) > 8:
        print("\n".join(lines[:2] + lines[-6:]))
    else:
        print(ind_data[:800])

    # Test basis
    print("\n--- Basis ---")
    basis = get_futures_basis("RB", "2026-06-15", "2026-07-14")
    print(basis[:800] if len(basis) > 800 else basis)

    # Test inventory
    print("\n--- Inventory ---")
    inv = get_futures_inventory("RB")
    print(inv[:800] if len(inv) > 800 else inv)

    # Test news
    print("\n--- News (first 5 headlines) ---")
    news = get_futures_news()
    lines = news.split("\n")[:5]
    print("\n".join(lines))

    print("\n[SUCCESS] Commodity futures data layer test complete.")
