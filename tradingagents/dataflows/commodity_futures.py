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
                AP(苹果), CJ(红枣), PK(花生), SM(锰硅), SF(硅铁)
    SHFE (上期): RB(螺纹钢), HC(热卷), CU(铜), AU(黄金), AG(白银), RU(橡胶)
    DCE (大商): I(铁矿石), JM(焦煤), J(焦炭), M(豆粕)
    INE (上能): SC(原油)
    (共20品种)

Data sources (via akshare):
    - futures_main_sina: daily OHLCV + open interest (Sina Finance)
    - futures_spot_price_daily: spot price + basis data
    - futures_inventory_em: warehouse inventory (East Money)
    - futures_news_shmet: SHMET commodity news
"""

import json
import logging
import time
import random
import warnings
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from tradingagents.dataflows.external_data import (
    annotate_with_source,
    get_external_source_label,
    load_external_data,
    merge_basis_data,
    merge_inventory_data,
)

logger = logging.getLogger(__name__)

# Suppress AKShare "非交易日" warnings — they're informational
# (one per weekend/holiday in the date range) and flood the output.
warnings.filterwarnings(
    "ignore", message=r".*非交易日.*", category=UserWarning
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_response_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Variety metadata & symbol mapping
# ---------------------------------------------------------------------------

VARIETY_METADATA = {
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
            "海运运费(BDI)",
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
        "inv_code": "ta",
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
        "inv_code": "ma",
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
        "inv_code": "fg",
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
        "inv_code": "sa",
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
        "inv_code": "ur",
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
        "inv_code": "pf",
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
        "inv_code": "cf",
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
        "inv_code": "sr",
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
        "inv_code": "oi",
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
        "inv_code": "rm",
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
        "inv_code": "ap",
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
        "inv_code": "cj",
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
        "inv_code": "pk",
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
        "inv_code": "sm",
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
        "inv_code": "sf",
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
}


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

    supported = ", ".join(
        f"{k}({v['name']})" for k, v in VARIETY_METADATA.items()
    )
    raise ValueError(
        f"Unsupported variety: '{symbol}'. Supported: {supported}"
    )


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
    return json.dumps(meta, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public data functions (TradingAgents vendor interface)
# ---------------------------------------------------------------------------

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
        if now - cached_at < _CACHE_TTL:
            df = cached_df.copy()
        else:
            df = None
    else:
        df = None

    if df is None:
        try:
            from akshare import futures_main_sina

            time.sleep(random.uniform(0.1, 0.3))
            # Fetch with extended lookback for indicator calculation
            df = futures_main_sina(
                symbol=main_sym,
                start_date="20200101",
                end_date=end_date,
            )
            _response_cache[cache_key] = (now, df.copy())
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
    col_map = {
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
    if "date" not in df.columns:
        # If column names didn't match, try positional
        if len(df.columns) >= 7:
            df.columns = ["date", "open", "high", "low", "close", "volume", "open_interest"][:len(df.columns)]
            keep_cols = [c for c in ["date", "open", "high", "low", "close", "volume", "open_interest"] if c in df.columns]

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
    else:
        # Fetch fresh
        try:
            from akshare import futures_main_sina

            full_df = futures_main_sina(
                symbol=main_sym,
                start_date="20200101",
                end_date=end_date,
            )
            _response_cache[cache_key] = (time.time(), full_df.copy())
        except Exception as e:
            # Try using cached price data
            return f"NO_DATA_AVAILABLE: Cannot fetch data for indicators. Error: {e}"

    if full_df.empty:
        return f"NO_DATA_AVAILABLE: No data for {meta['name']}({code})."

    # Normalize columns
    col_map = {
        "日期": "date", "开盘价": "open", "最高价": "high",
        "最低价": "low", "收盘价": "close", "成交量": "volume",
        "持仓量": "open_interest",
    }
    full_df = full_df.rename(columns=col_map)
    if "date" not in full_df.columns and len(full_df.columns) >= 7:
        full_df.columns = ["date", "open", "high", "low", "close", "volume", "open_interest"][:len(full_df.columns)]

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
    full_df["pct_from_sma_20"] = (close - full_df["sma_20"]) / full_df["sma_20"].replace(0, float("nan")) * 100

    # Filter to requested date range
    end_dt = pd.to_datetime(end_date)
    start_dt = pd.to_datetime(start_date)
    full_df = full_df[full_df["date"] <= end_dt]
    result = full_df[full_df["date"] >= start_dt].copy()

    if result.empty:
        return (
            f"NO_DATA_AVAILABLE: Insufficient data for indicators on "
            f"{meta['name']}({code})."
        )

    # Round for readability
    float_cols = result.select_dtypes(include=["float64"]).columns
    result[float_cols] = result[float_cols].round(4)

    return result.to_csv(index=False)


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

    try:
        from akshare import futures_spot_price_daily

        time.sleep(random.uniform(0.1, 0.3))
        df = futures_spot_price_daily(
            start_day=start_date.replace("-", ""),
            end_day=end_date.replace("-", ""),
            vars_list=[spot_code],
        )
    except ImportError:
        return "DATA_ERROR: akshare is required."
    except Exception as e:
        logger.warning("Failed to fetch basis for %s: %s", code, e)
        return (
            f"DATA_UNAVAILABLE: Could not fetch basis data for "
            f"{meta['name']}({code}). Error: {e}"
        )

    if df.empty:
        return f"NO_DATA_AVAILABLE: No basis data for {meta['name']}({code})."

    # Normalize column names (akshare returns Chinese columns)
    col_map = {
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
    keep_cols = [c for c in ["date", "spot_price", "dominant_contract",
                              "dominant_contract_price", "dom_basis",
                              "dom_basis_rate", "near_contract",
                              "near_contract_price", "near_basis",
                              "near_basis_rate"]
                 if c in df.columns]
    result = df[keep_cols].copy()

    # Round numeric cols
    float_cols = result.select_dtypes(include=["float64"]).columns
    result[float_cols] = result[float_cols].round(4)

    # Add interpretation hints
    if "dom_basis" in result.columns:
        latest_basis = result["dom_basis"].iloc[-1]
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

    csv_out = result.to_csv(index=False)
    api_output = csv_out + basis_note

    # --- Hybrid injection: merge with external spot price if available ---
    merged, used_external = merge_basis_data(code, api_output)
    return merged


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
        from akshare import futures_inventory_em

        time.sleep(random.uniform(0.1, 0.3))
        df = futures_inventory_em(symbol=inv_code)
    except ImportError:
        return "DATA_ERROR: akshare is required."
    except Exception as e:
        logger.warning("Failed to fetch inventory for %s: %s", code, e)
        return (
            f"DATA_UNAVAILABLE: Could not fetch inventory for "
            f"{meta['name']}({code}). Error: {e}"
        )

    if df.empty:
        return f"NO_DATA_AVAILABLE: No inventory data for {meta['name']}({code})."

    # Normalize columns
    col_map = {
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
    merged, used_external = merge_inventory_data(code, api_output)
    return merged


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
    import uuid

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
        em_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kuaixun.eastmoney.com/",
        }
        r_em = requests.get(em_url, params=em_params, headers=em_headers, timeout=10)
        d_em = r_em.json()
        for item in d_em.get("data", {}).get("fastNewsList", []):
            title = item.get("title", "")
            summary = item.get("summary", "")[:300]
            pub_time = item.get("showTime", "")
            all_news.append({
                "title": title,
                "content": summary if summary else title,
                "time": pub_time,
                "source": "Eastmoney",
            })
    except Exception as e:
        logger.warning("Eastmoney news fetch failed: %s", e)

    # --- Source 3: SHMET (commodity-specific, fallback) ---
    try:
        from akshare import futures_news_shmet
        df = futures_news_shmet()
        if not df.empty:
            for _, row in df.head(15).iterrows():
                time_str = str(row.get("发布时间", ""))
                content = str(row.get("内容", ""))
                if content and content != "nan":
                    all_news.append({
                        "title": content[:100],
                        "content": content[:300],
                        "time": time_str[:16] if time_str else "",
                        "source": "SHMET",
                    })
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
    commodity_kw = [
        # Black metals
        "螺纹", "热卷", "铁矿石", "铁矿", "焦炭", "焦煤", "钢材", "钢铁",
        "钢厂", "高炉", "电炉", "铁水", "废钢", "钢价", "钢市", "钢坯",
        # Non-ferrous
        "铜", "铝", "锌", "镍", "黄金", "白银", "有色金属",
        # Energy
        "原油", "OPEC", "天然气", "煤矿", "煤炭",
        # Chemicals
        "PTA", "甲醇", "聚酯", "烯烃", "MTO", "PX", "纯碱", "玻璃", "尿素",
        "短纤", "涤纶", "涤短",
        # Agriculture
        "豆粕", "大豆", "USDA", "棕榈油", "生猪", "玉米", "棉花", "白糖",
        "菜油", "菜粕", "苹果", "红枣", "花生", "硅铁", "锰硅",
        # Production & policy
        "限产", "减产", "停产", "检修", "去产能", "环保限产", "供给侧",
        "反内卷", "产能置换", "碳达峰",
        # Real estate & infra
        "房地产", "地产", "新开工", "保交楼", "基建", "专项债", "固投", "保障房",
        # Construction
        "建材", "水泥", "开工率", "工地",
        # Supply chain
        "BHP", "FMG", "力拓", "必和必拓", "淡水河谷", "罢工", "台风", "封库",
        # Trade & macro
        "大宗商品", "黑色系", "商品期货", "现货", "出口退税", "反倾销",
        "关税", "贸易摩擦", "PMI", "GDP", "央行", "降准", "LPR",
        # Regions
        "唐山", "邯郸", "山西", "河北",
        # Inventory & cost
        "库存", "累库", "去库", "港口库存", "利润", "盈利率",
    ]

    symbol_specific = {
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
        "SM": ["锰硅", "硅锰", "锰矿"],
        "SF": ["硅铁", "硅石"],
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
        f"# NOTE: For industry-specific events (限产/罢工/检修), use external data JSON.",
        f"#       Free APIs provide macro/commodity context, not Mysteel-grade detail.",
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
    import akshare as ak

    parts = ["# CHINA MACROECONOMIC INDICATORS (for commodity futures analysis)",
             f"# Data_Source: FREE_API (AKShare / Eastmoney)",
             f"# Note: latest available data points shown. Some series lag 1-2 months.",
             ""]

    # --- GDP ---
    try:
        gdp = ak.macro_china_gdp()
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
            parts.append(f"  近四个季度趋势: {', '.join(str(x) for x in recent['国内生产总值-同比增长'].tail(4))}")
            parts.append("")
    except Exception as e:
        parts.append(f"## GDP: UNAVAILABLE ({e})")
        parts.append("")

    # --- PMI ---
    try:
        pmi = ak.macro_china_pmi()
        if not pmi.empty:
            latest = pmi.iloc[-1]
            parts.append("## PMI (制造业采购经理指数)")
            parts.append(f"  最新月份: {latest.get('月份', 'N/A')}")
            parts.append(f"  制造业PMI: {latest.get('制造业-指数', 'N/A')}")
            parts.append(f"  非制造业PMI: {latest.get('非制造业-指数', 'N/A')}")
            # Recent 3 months
            recent = pmi.tail(3)
            parts.append(f"  近3个月制造业PMI: {', '.join(str(x) for x in recent['制造业-指数'].tail(3))}")
            below_50 = float(latest.get('制造业-指数', 50)) < 50
            parts.append(f"  荣枯线判断: {'**低于50荣枯线，经济收缩**' if below_50 else '高于50荣枯线，经济扩张'}")
            parts.append("")
    except Exception as e:
        parts.append(f"## PMI: UNAVAILABLE ({e})")
        parts.append("")

    # --- Fixed Asset Investment ---
    try:
        fai = ak.macro_china_gdzctz()
        if not fai.empty:
            latest = fai.iloc[-1]
            parts.append("## 固定资产投资 (FAI)")
            parts.append(f"  最新月份: {latest.get('月份', 'N/A')}")
            parts.append(f"  当月值: {latest.get('当月', 'N/A')} 亿元")
            parts.append(f"  同比增长: {latest.get('同比增长', 'N/A')}%")
            parts.append(f"  累计值: {latest.get('累计值', 'N/A')} 亿元")
            # Recent trend
            recent = fai.tail(3)
            trend = [str(x) for x in recent['同比增长'].tail(3) if str(x) != 'nan']
            if trend:
                parts.append(f"  近3个月同比趋势: {', '.join(trend)}%")
            parts.append("")
    except Exception as e:
        parts.append(f"## FAI: UNAVAILABLE ({e})")
        parts.append("")

    # --- Real Estate ---
    try:
        re = ak.macro_china_real_estate()
        if not re.empty:
            latest = re.iloc[-1]
            col_date = next((c for c in re.columns if '日期' in str(c)), re.columns[0])
            col_val = next((c for c in re.columns if '指数值' in str(c) or '值' in str(c)), re.columns[1])
            col_chg = next((c for c in re.columns if '涨跌幅' in str(c) and '近' not in str(c)), None)
            parts.append("## 房地产景气指数")
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            real_val = latest.get(col_val, 'N/A')
            parts.append(f"  指数值: {real_val}")
            if col_chg:
                parts.append(f"  涨跌幅: {latest.get(col_chg, 'N/A')}%")
            recent = re.tail(6)
            recent_vals = [str(x) for x in recent[col_val].tail(6)]
            parts.append(f"  近6个月指数走势: {', '.join(recent_vals)}")
            parts.append(f"  **判断**: 指数持续低迷表明房地产行业仍在筑底，利空螺纹钢需求（房地产占螺纹钢需求约60%）。")
            parts.append("")
    except Exception as e:
        parts.append(f"## Real Estate: UNAVAILABLE ({e})")
        parts.append("")

    # --- Industrial Production ---
    try:
        ip = ak.macro_china_gyzjz()
        if not ip.empty:
            latest = ip.iloc[-1]
            parts.append("## 工业增加值")
            parts.append(f"  最新月份: {latest.get('月份', 'N/A')}")
            parts.append(f"  同比增长: {latest.get('同比增长', 'N/A')}%")
            parts.append(f"  累计增长: {latest.get('累计增长', 'N/A')}%")
            recent = ip.tail(3)
            trend = [str(x) for x in recent['同比增长'].tail(3)]
            parts.append(f"  近3个月同比趋势: {', '.join(trend)}%")
            parts.append("")
    except Exception as e:
        parts.append(f"## Industrial Production: UNAVAILABLE ({e})")
        parts.append("")

    # --- Construction Industry Index ---
    try:
        ci = ak.macro_china_construction_index()
        if not ci.empty:
            latest = ci.iloc[-1]
            col_date = next((c for c in ci.columns if '日期' in str(c)), ci.columns[0])
            col_val = next((c for c in ci.columns if '指数值' in str(c) or '值' in str(c)), ci.columns[1])
            parts.append("## 建筑业指数 (日度)")
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            parts.append(f"  指数值: {latest.get(col_val, 'N/A')}")
            # Weekly trend (last 5 trading days)
            recent = ci.tail(5)
            recent_vals = [str(x) for x in recent[col_val].tail(5)]
            parts.append(f"  近5个交易日: {', '.join(recent_vals)}")
            parts.append(f"  **与钢铁需求关系**: 建筑业是螺纹钢最大下游，指数走势直接反映建筑活动强弱。")
            parts.append("")
    except Exception as e:
        parts.append(f"## Construction Index: UNAVAILABLE ({e})")
        parts.append("")

    return "\n".join(parts)


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
        f"# Combines external data (Mysteel, Wind, etc.) with free API data",
        f"",
    ]

    # --- External Data Section ---
    ext = load_external_data(variety)
    if ext:
        data = ext.get("data", {})
        source_label = get_external_source_label(variety)
        parts.append(f"## External Data (来源: {source_label})")
        parts.append("")

        # Weekly production
        wp = data.get("weekly_production")
        if wp:
            parts.append("### 螺纹钢周度产量")
            parts.append(f"  产量: {wp.get('value', 'N/A')} {wp.get('unit', '万吨')}")
            parts.append(f"  环比: {wp.get('change_wow', 'N/A')} ({wp.get('change_wow_pct', 'N/A')}%)")
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
            parts.append(f"  高炉利润: {profit.get('bf_mill_profit', 'N/A')} {profit.get('unit', '元/吨')}")
            parts.append(f"  电炉利润: {profit.get('eaf_mill_profit', 'N/A')} {profit.get('unit', '元/吨')}")
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
            parts.append(f"  环比: {si.get('change_wow', 'N/A')} ({si.get('change_wow_pct', 'N/A')}%)")
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
        parts.append("  (No external data file found. Create ~/.tradingagents/external_data/"
                     f"{variety}.json to enable supply-demand indicators and key industry events.)")
        parts.append("  See RB.json.sample for the file format.")
        parts.append("")

    # --- Free API: Construction Index ---
    parts.append("## Construction Industry Index (FREE API)")
    try:
        import akshare as ak
        ci = ak.macro_china_construction_index()
        if not ci.empty:
            col_date = next((c for c in ci.columns if '日期' in str(c)), ci.columns[0])
            col_val = next((c for c in ci.columns if '指数值' in str(c) or '值' in str(c)), ci.columns[1])
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
        import akshare as ak
        re = ak.macro_china_real_estate()
        if not re.empty:
            col_date = next((c for c in re.columns if '日期' in str(c)), re.columns[0])
            col_val = next((c for c in re.columns if '指数值' in str(c) or '值' in str(c)), re.columns[1])
            latest = re.iloc[-1]
            parts.append(f"  最新日期: {latest.get(col_date, 'N/A')}")
            parts.append(f"  指数值: {latest.get(col_val, 'N/A')}")
            recent = re.tail(6)
            recent_vals = [str(x) for x in recent[col_val].tail(6)]
            parts.append(f"  近6月走势: {', '.join(recent_vals)}")
            parts.append(f"  **与钢铁需求关系**: 房地产是螺纹钢最大下游(约60%)，该指数持续低迷意味着螺纹钢需求端缺乏支撑。")
        else:
            parts.append("  No data available.")
    except Exception as e:
        parts.append(f"  UNAVAILABLE: {e}")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Verified Quote Snapshot — deterministic source of truth for price/indicator values
# ---------------------------------------------------------------------------

def get_verified_quote(symbol: str, date: str = "", start_date: str = "", end_date: str = "") -> str:
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
    from datetime import datetime as dt, timedelta
    try:
        target_dt = dt.strptime(date, "%Y-%m-%d")
    except ValueError:
        return f"VERIFIED_SNAPSHOT_ERROR: invalid date format '{date}', use YYYY-MM-DD."

    fetch_start = (target_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    fetch_end = (target_dt + timedelta(days=5)).strftime("%Y-%m-%d")

    price_result = get_futures_price(symbol, fetch_start, fetch_end)
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
    l = float(target_row[3]) if len(target_row) > 3 else 0.0
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
        f"Source: AKShare / Sina Finance | Status: TRUSTED — use for exact numeric claims",
        "=" * 50,
        "",
        f"Exchange: {meta['exchange_cn']} | Unit: {meta['unit']}",
        f"Price Limit: {meta['price_limit']} | Margin: {meta['margin_rate']}",
        "",
        "--- Exact OHLCV ---",
        f"Open:       {o:>12.2f}",
        f"High:       {h:>12.2f}",
        f"Low:        {l:>12.2f}",
        f"Close:      {c:>12.2f}",
        f"Volume:     {v:>12.0f}",
        f"Open Int:   {oi:>12.0f}",
        f"Day Change: {day_change:>+11.2f}%",
        "",
        "--- Key Levels ---",
        f"SMA(5):     {sma5:>12.2f}  (short-term trend)",
        f"SMA(20):    {sma20:>12.2f}  (medium-term trend)",
        f"Price vs SMA20: {'ABOVE' if c > sma20 else 'BELOW'} by {abs(c-sma20):.2f}",
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
    from tradingagents.dataflows.sentiment_data import get_futures_sentiment as _impl
    return _impl(symbol)


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
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
