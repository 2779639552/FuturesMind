"""FuturesMind Signal Analyzer — P1/P2/P3 analysis features.

Anomaly detection, bull-bear divergence, lead-lag analysis (Granger),
cross-platform divergence, author profiling, event extraction, simulated trading.

═══════════════════════════════════════════════════════════════════════
中文导读(面向零基础学习者)
═══════════════════════════════════════════════════════════════════════
【本文件在项目中的角色】
  这是 AgentSense 的【确定性回测引擎 / 信号计算核心】。它不调用任何 LLM,
  全部基于本地 JSON 数据(情绪/趋势/价格)做纯规则、纯数值计算,因此同一份
  数据永远得到同一份结果——可复现、可写单测、结果稳定。

【本文件包含哪些部分】
  1) 数据加载(_load_sentiment / _load_trends / _load_price 等)
     从磁盘读取"情绪、趋势、价格"三类 JSON;含中文文件名别名
     (_DATA_FILE_NAME_ALIASES,如 HC→热卷)与多级 fallback 顺序。
  2) 情绪统计指标(P1 系列)
     异常检测 detect_anomalies、牛熊分歧 compute_divergence、领先滞后分析
     analyze_lead_lag、跨平台分歧 analyze_cross_platform、作者画像
     get_top_authors、事件抽取 extract_events。
  3) 20+ 个回测策略
     · 旧策略:逆向情绪 run_contrarian_sentiment、自适应情绪 run_adaptive_sentiment、
       Donchian 通道 run_donchian_strategy、时间序列动量 run_momentum_strategy、
       动量+自适应 run_momentum_adaptive、情绪模拟交易 run_simulated_trading、
       情绪追踪离场 run_trailing_strategy、多策略对比 run_strategy_comparison。
     · 12 个技术策略(6 种指标 × 纯价格/情绪自适应两版):MA cross / MACD / RSI /
       Bollinger / Turtle / ATR。它们共用一套指标序列构建器(_build_tech_series)、
       统一信号判定(_tech_signal)、统一回测引擎(_run_technical_backtest),
       由 TECH_KEYS 注册,12 个薄包装(run_ma_cross_strategy 等)暴露给上层。
  4) 指标计算器 compute_advanced_metrics
     夏普/索提诺/卡玛/年化收益/最大回撤/盈亏因子等;无风险利率取
     中国 10 年期国债约 2.5%。
  5) 风控包装 apply_risk_management
     对任意策略产出的交易列表施加固定止损/移动止损/最长持仓限制。
  6) "今日操作"信号 latest_trading_signal 及辅助
     _build_forward_filled_sent_map(情绪前向填充)、_make_signal(信号 dict)。
  7) 品种横向对比 compare_varieties / get_all_variety_scores。

【与 web_app.py / web_template.html 如何协同】
  · web_app.py 是 Flask 后端,文件顶部 `from signal_analyzer import ...` 导入本文件
    的策略函数与 latest_trading_signal。每个"策略详情"路由先调用对应的 run_*_strategy
    得到回测 dict,再调 latest_trading_signal 得到"今日操作"信号,两者拼进同一个
    dict 返回给前端。
  · web_template.html 是前端模板,读取返回 dict 中的 total_trades / win_rate /
    total_pnl / advanced_metrics / curves / today_signal / today_signals 等字段,
    渲染成策略卡片、归一化强度条(strength_pct)与"今日操作"提示。
  · 因此本文件只负责"算数据、出结果",不负责 HTTP、渲染、数据库——职责单一,
    可脱离 Web 单独 import 与测试。
"""

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from path_utils import resolve_think2_dir

SENTIMENT_DIR = Path(os.path.expanduser("~/.tradingagents/external_data"))
THINK2_TRENDS = resolve_think2_dir() / "output" / "trends"

# 数据文件名的中文简称与 VARIETY_METADATA 全称不一致的品种(code -> 文件名用名)。
# 目前仅 HC:meta name「热轧卷板」,数据文件「热卷_price.json」/「热卷_sentiment.json」。
# 为什么需要它:不同数据流水线(思路2 采集 vs VARIETY_METADATA 元数据)命名口径
# 不一致,别名表把"品种代码"硬映射到"磁盘文件名用名",保证按代码也能读到文件。
# _load_trends / _load_price 在"直接代码名"和"meta 中文全称"都失败后,会回退到这里。
_DATA_FILE_NAME_ALIASES = {"HC": "热卷"}

# ── Helpers ────────────────────────────────────────────────────────


def _load_sentiment(variety: str) -> dict | None:
    """【功能】读取某品种的"情绪"JSON 文件(来自思路2 采集目录)。

    【参数】variety:品种代码,如 "RB"(螺纹钢)/ "HC"(热卷)。

    【返回】解析后的 dict;文件不存在时返回 None(调用方需自行兜底)。

    【关键逻辑】路径固定为 SENTIMENT_DIR / "<代码>_sentiment.json",不做任何
    别名/通配符 fallback——与 _load_trends 不同,这里只认"代码_文件名"。
    情绪文件是各策略判断"市场看多/看空"的主要数据源。
    """
    path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_trends(variety: str) -> dict | None:
    """Load trends sentiment JSON, trying multiple naming conventions.

    【功能】读取某品种的"趋势/情绪"JSON 文件(位于 THINK2_TRENDS 目录)。
    【参数】variety:品种代码,如 "RB" / "HC"。
    【返回】解析后的 dict;所有尝试都失败时返回 None。
    【关键逻辑】按 4 级 fallback 顺序依次尝试,直到第一个成功:
        ① 直接代码名  <code>_sentiment.json;
        ② meta 中文全称(如 HC→热轧卷板),来自 VARIETY_METADATA;
        ③ 显式别名表 _DATA_FILE_NAME_ALIASES(如 HC→热卷);
        ④ 通配符 glob 扫描,只要文件名里含代码或代码是文件名前缀即命中。
    为什么有这么多 fallback:同一品种在不同流水线里可能用"代码 / meta 中文全称 /
    采集简称"三种名字存文件,多级回退保证按代码尽量找到数据。
    """
    # Try direct code match first
    path = THINK2_TRENDS / f"{variety}_sentiment.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # Try looking up Chinese name from VARIETY_METADATA
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

        meta = VARIETY_METADATA.get(variety, {})
        chinese_name = meta.get("name", "")
        if chinese_name:
            path = THINK2_TRENDS / f"{chinese_name}_sentiment.json"
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
    except ImportError:
        # 元数据包不可用时静默跳过第②级,继续走别名/glob
        pass

    # Try explicit alias (meta 全称与文件名简称不一致,如 HC「热轧卷板」/「热卷」)
    alias = _DATA_FILE_NAME_ALIASES.get(variety)
    if alias:
        path = THINK2_TRENDS / f"{alias}_sentiment.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)

    # Try glob search
    for f in THINK2_TRENDS.glob("*_sentiment.json"):
        if variety in f.stem or f.stem.startswith(variety):
            with open(f, encoding="utf-8") as fp:
                return json.load(fp)

    return None


def _load_price(variety: str) -> dict | None:
    """Load price JSON, trying multiple naming conventions.

    【功能】读取某品种的"价格/OHLCV"JSON 文件(位于 THINK2_TRENDS 目录)。
    【参数】variety:品种代码,如 "RB" / "HC"。
    【返回】解析后的 dict;所有尝试都失败时返回 None。
    【关键逻辑】fallback 顺序与 _load_trends 完全同构,只是把文件名后缀从
    "_sentiment" 换成 "_price",仍是 4 级:①代码名 → ②meta 中文全称 →
    ③显式别名 → ④glob 扫描。三级(实际四级)兜底保证按代码尽量拿到行情。
    """
    path = THINK2_TRENDS / f"{variety}_price.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # Try Chinese name
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

        meta = VARIETY_METADATA.get(variety, {})
        chinese_name = meta.get("name", "")
        if chinese_name:
            path = THINK2_TRENDS / f"{chinese_name}_price.json"
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
    except ImportError:
        # 元数据包不可用时静默跳过,继续走别名/glob
        pass

    # Try explicit alias (meta 全称与文件名简称不一致,如 HC「热轧卷板」/「热卷」)
    alias = _DATA_FILE_NAME_ALIASES.get(variety)
    if alias:
        path = THINK2_TRENDS / f"{alias}_price.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)

    # Try glob
    for f in THINK2_TRENDS.glob("*_price.json"):
        if variety in f.stem or f.stem.startswith(variety):
            with open(f, encoding="utf-8") as fp:
                return json.load(fp)

    return None


def pearson_r(x, y):
    """【功能】计算两组序列的皮尔逊相关系数(衡量线性相关程度与方向)。

    【参数】x, y:等长的数值列表。
    【返回】相关系数 ∈ [-1, 1];样本数 < 3 或任一方标准差为 0 时返回 0.0
    (数据不足以判断相关性,干脆当作"无相关")。
    【关键逻辑】corr = cov(x,y) / (std(x)*std(y))。zip(..., strict=True) 要求
    两序列长度严格相等,长度不一时会抛 ValueError——调用方需保证对齐。
    """
    n = len(x)
    if n < 3:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=True))
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

    【功能】情绪异常检测:找出情绪评分相对其 7 日均值显著偏离的日子。
    【参数】variety:品种代码;threshold_std:z-score 阈值,默认 2.0(±2 个标准差)。
    【返回】异常日列表,每项含 date / score / ma_7d / z_score / direction / type;
    数据缺失或不足 14 天时返回空列表。
    【关键逻辑】z = (当日评分 - 前7日均值) / 前7日标准差。z>0 记 bullish(spike_up),
    z<0 记 bearish(spike_down)。注意窗口用"前 7 天"(不含当日),避免把当日混进
    自己的基准里。当 7 日波动为 0(异常平坦市)时跳过该日,避免除以 0。
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
        window = scores[i - 7 : i]
        ma = sum(window) / 7
        std = math.sqrt(sum((x - ma) ** 2 for x in window) / 7)
        if std == 0:
            continue
        z = (scores[i] - ma) / std
        if abs(z) > threshold_std:
            anomalies.append(
                {
                    "date": dates[i],
                    "score": round(scores[i], 3),
                    "ma_7d": round(ma, 3),
                    "z_score": round(z, 2),
                    "direction": "bullish" if z > 0 else "bearish",
                    "type": "spike_up" if z > 0 else "spike_down",
                }
            )

    return anomalies


# ═══════════════════════════════════════════════════════════════════
# P1-2: Bull-Bear Divergence Index
# ═══════════════════════════════════════════════════════════════════


def compute_divergence(variety: str) -> dict | None:
    """Compute bull-bear divergence: how split is market sentiment?

    Divergence = 1 - |bullish_ratio - bearish_ratio|
    High divergence (>0.7) = strong consensus; Low (<0.4) = split/uncertain.

    【功能】计算牛熊分歧度:衡量市场情绪是多空一致还是严重分裂。
    【参数】variety:品种代码。
    【返回】含 variety / date / bullish_ratio / bearish_ratio / neutral_ratio /
    divergence / label / signal / trend / total_notes 的 dict;无数据返回 None。
    【关键逻辑】divergence = 1 - |看多占比 - 看空占比|。值越接近 1 表示越一致
    (高度共识),越接近 0 表示越分裂(分歧)。label/signal 的中文档位是硬编码的
    业务解释:如 divergence<0.3 → "极度分歧 / 趋势可能反转"。trend 是把最近 7 天
    与更早 7 天的均分对比:改善(绝对值变小)/ diverging / steady。
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

    【功能】领先-滞后分析(简化版 Granger):判断是"情绪领先价格"还是"价格领先情绪"。
    【参数】variety:品种代码;max_lag:考察的最大滞后天数,默认 5 天。
    【返回】含 variety / data_points / sentiment_leads_price / price_leads_sentiment /
    conclusion / max_sent_corr / max_price_corr 的 dict;情绪或价格数据不足时返回 None。
    【关键逻辑】
      - 先按"两个日期集合的交集"对齐情绪与价格(价格只取 close,换算成日收益率)。
      - 对每个滞后 lag:算两组相关系数——情绪(t) vs 价格变化(t+lag),以及
        价格变化(t) vs 情绪(t+lag)。
      - 最后取"最大绝对相关"更大的一方为结论(sentiment_leads 或 price_leads)。
      注意这是简化 Granger:只是同步/滞后相关,不控制其他变量,【待确认】不保证因果。
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
    # 先把价格按日期建成查表,再遍历情绪序列,只保留"情绪与价格都有的交易日"——
    # 两列日期对齐后才能算相关,缺失日一律跳过。
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
            sent_leads[f"lag_{lag}d"] = round(
                pearson_r([x[0] for x in s_lead], [x[1] for x in s_lead]), 4
            )
            price_leads[f"lag_{lag}d"] = round(
                pearson_r([x[0] for x in p_lead], [x[1] for x in p_lead]), 4
            )

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

    【功能】跨平台情绪对比:比较微博(散户)/知乎(专业)/小红书(社交)等平台的
    情绪得分差异。跨平台分歧可能预示风险,如"散户看多 + 专业社区看空"。
    【参数】variety:品种代码。
    【返回】含 variety / date / platforms / spread / conflict_level /
    interpretation 的 dict;无趋势数据或数据不完整返回 None。
    【关键逻辑】取最新一天各平台的 avg_score,极差 spread = max - min;按 spread
    大小划分为 high(>0.5)/ moderate(>0.3)/ low 三档冲突等级;interpretation
    交由 _interpret_cross_platform 生成中文解释。
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
    """【功能】把跨平台分歧的量化结果翻译成一句中文投资提示。

    【参数】platforms:各平台 {platform: {avg_score, ...}};conflict:冲突等级
    (high / moderate / low / insufficient)。
    【返回】中文解释字符串。
    【关键逻辑】业务规则:若"微博(散户)>0.1 且 知乎(专业)<-0.1"→ 散户狂热而专业
    谨慎,提示"警惕情绪泡沫";反向则提示"抄底机会"。仅 high 档才会看平台间方向差,
    其余档返回通用提示。
    """
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
    """Get top authors ranked by influence (posts * engagement * log(fans)).

    【功能】作者影响力排行:从作者索引文件读取全部作者并按"影响力"降序取前 limit 名。
    【参数】limit:返回前多少名,默认 20。
    【返回】作者 dict 列表,每项含 name / uid / posts / fans / avg_engagement / influence;
    索引文件不存在返回空列表。
    【关键逻辑】影响力公式是经验加权:
      - 有粉丝:influence = log10(粉丝数+1)*0.70 + sqrt(发帖数)*0.10 + log(互动+1)*0.20。
        log10 使 1千→3、1万→4、10万→5、100万→6,把"数量级"变成可加的量纲;
        sqrt 对发帖数做开方压平(抑制刷帖);互动取 log 同理。
      - 无粉丝但有发帖:退化为 sqrt(发帖数)*0.5。
      排序用 -influence(降序),最后截断到 limit 条。
    """
    index_path = THINK2_TRENDS / "_author_index.json"
    if not index_path.exists():
        return []

    with open(index_path, encoding="utf-8") as f:
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

        result.append(
            {
                "name": name,
                "uid": uid,
                "posts": posts,
                "fans": fans,
                "avg_engagement": round(engagement, 1),
                "influence": influence,
            }
        )

    result.sort(key=lambda x: -x["influence"])
    return result[:limit]


# ═══════════════════════════════════════════════════════════════════
# P1-5: Event Extraction (keyword-based)
# ═══════════════════════════════════════════════════════════════════

# 事件类别 → 触发关键词。extract_events 用"标题/正文里是否出现关键词"做规则匹配,
# 命中即归入对应类别。这是纯关键词硬匹配,不涉及语义理解。
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
    """Extract key events from recent posts using keyword matching.

    【功能】从最近的社交媒体帖子里抽取"关键事件",按 EVENT_KEYWORDS 关键词归类。
    【参数】variety:若指定则只保留涉及该品种的帖子;days:只看最近 N 天,默认 7 天。
    【返回】去重后的事件 dict 列表(最多 50 条),每项含 date / platform / title /
    categories / url / varieties / sentiment / sentiment_score。
    【关键逻辑】数据源是批量 JSONL(batch_*.jsonl)而非数据库——注释写明"数据库可能
    未填充"。流程:①按 note_id 去重并只留最近 days 天、匹配 variety 的帖子(上限 500);
    ②标题+正文拼起来做关键词匹配,可命中多个类别;③varieties 字段可能是 JSON 字符串
    或列表,统一解析成名字;④按标题前 50 字去重(近似去重),截断到 50 条。
    """
    # Read directly from JSONL batch files (database may not be populated)
    import glob as _glob

    think2_output = resolve_think2_dir() / "output"
    if not think2_output.exists():
        return []

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    posts = []
    seen = set()
    for fpath in sorted(_glob.glob(str(think2_output / "batch_*.jsonl"))):
        try:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    nid = d.get("note_id", "")
                    if nid in seen:
                        continue
                    seen.add(nid)
                    t = (d.get("publish_time", "") or "")[:10]
                    if t < since:
                        continue
                    if variety and variety not in json.dumps(d.get("varieties", [])):
                        continue
                    posts.append(d)
                    if len(posts) >= 500:
                        break
        except Exception:
            pass
        if len(posts) >= 500:
            break

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
                try:
                    varieties_raw = json.loads(varieties_raw)
                except (json.JSONDecodeError, TypeError):
                    varieties_raw = []
            variety_names = (
                [v["name"] if isinstance(v, dict) else str(v) for v in varieties_raw]
                if isinstance(varieties_raw, list)
                else []
            )
            sentiment_label = post.get("sentiment", "neutral")
            sentiment_score = post.get("sentiment_score", 0)

            events.append(
                {
                    "date": (post.get("publish_time", "") or "")[:10],
                    "platform": post.get("platform", "?"),
                    "title": (post.get("title", "") or "")[:120],
                    "categories": matched_categories,
                    "url": post.get("url", ""),
                    "varieties": variety_names[:5],
                    "sentiment": sentiment_label,
                    "sentiment_score": round(sentiment_score, 2)
                    if isinstance(sentiment_score, (int, float))
                    else 0,
                }
            )

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
    end_date: str = "",
) -> dict:
    """Contrarian sentiment: trade AGAINST the trend when sentiment disagrees.

    Logic:
      - Price falling (N-day trend < 0) + sentiment bullish (score > 0) → LONG (bottom signal)
      - Price rising (N-day trend > 0) + sentiment bearish (score < 0) → SHORT (top signal)
      - Exit: fixed horizon or when sentiment aligns with trend again

    This exploits the finding that sentiment is a CONTRARIAN indicator (55.9% accurate
    when opposing the trend vs 50.0% when following it).

    【功能】逆向情绪策略:当情绪与价格趋势背离时,逆着情绪方向交易。
    【参数】variety:品种代码,空串表示遍历全部有数据品种;horizon:持仓天数(固定
    周期离场),默认 3;trend_window:N 日趋势窗口,默认 5;start_date/end_date:
    回测日期窗(过滤用,默认从 2025-01-01 开始,空串表示不限)。
    【返回】dict,含 strategy / contrarian / consensus / dates / curves / variety /
    horizon / trend_window;无信号时返回 {"total_trades": 0, "message": "No signals"}。
    【关键逻辑】同时跑两条路径做对比:
      - contrarian(逆情绪):跌+看多→做多(抄底),涨+看空→做空(逃顶)。
      - consensus/momentum(情绪共识,对照组):逻辑正好相反,顺势而为。
      两条路径共用同一份价格/情绪数据与同一套持仓状态机(pos: 0/1/-1),
      因此输出天然可比,用于验证"逆向"相对"顺向"是否真的更优。
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
        # 情绪日期与价格日期不一定每天对齐,这里把情绪"向前填充"(forward-fill):
        # 对每个价格日期 d,取"情绪日期 <= d 的最新一条"作为该日的情绪值。这模拟了
        # 当日开盘前能看到的"最新已知情绪",而不是未来数据——避免未来函数。
        sent_series = sent_data.get("data", {}).get("daily_series", sent_data.get("series", []))
        raw_sent = {s.get("date", ""): s.get("avg_score", 0) for s in sent_series}
        sd_sorted = sorted(raw_sent.keys())
        sent_map = {}
        last_s = 0
        si = 0
        for d in sorted(str(x["date"])[:10] for x in px):
            while si < len(sd_sorted) and sd_sorted[si] <= d:
                last_s = raw_sent[sd_sorted[si]]
                si += 1
            sent_map[d] = last_s

        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        # Positions: 0=flat, 1=long, -1=short. Tracks last entry for both strategies
        # 0=空仓, 1=持多, -1=持空。pos_c 是逆向路径,pos_m 是顺向(共识)路径。
        pos_c = 0
        entry_px_c = 0
        entry_d_c = ""  # contrarian
        pos_m = 0
        entry_px_m = 0
        entry_d_m = ""  # momentum

        for i in range(trend_window, n - horizon):
            d = dates[i]
            if d < start_date or (end_date and d > end_date):
                continue
            ss = sent_map.get(d, 0)
            px_now = closes[i]
            trend = (
                (closes[i] - closes[i - trend_window]) / closes[i - trend_window]
                if closes[i - trend_window]
                else 0
            )

            # --- Contrarian Entry ---
            # 趋势阈值 ±1%(0.01)、情绪阈值 ±0.1:数值太小当噪音、太大错过信号。
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
            # 固定周期离场:入场后第 horizon 天收盘离场;越界则用最后一根 bar。
            exit_idx = min(i + horizon, n - 1)

            # Process contrarian
            # 用 dates.index(entry_d_c) 反查入场下标来判断是否已持仓 horizon 天。
            if pos_c != 0 and entry_d_c and i >= dates.index(entry_d_c) + horizon:
                exit_px = closes[exit_idx]
                if pos_c == 1:
                    pnl = (exit_px - entry_px_c) / entry_px_c * 100
                else:
                    pnl = (entry_px_c - exit_px) / entry_px_c * 100
                all_contrarian.append(
                    {
                        "variety": var,
                        "entry": entry_d_c,
                        "exit": d,
                        "direction": "long" if pos_c == 1 else "short",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "contrarian",
                    }
                )
                pos_c = 0

            if pos_c == 0:
                if c_long:
                    pos_c = 1
                    entry_px_c = px_now
                    entry_d_c = d
                elif c_short:
                    pos_c = -1
                    entry_px_c = px_now
                    entry_d_c = d

            # Process momentum
            if pos_m != 0 and entry_d_m and i >= dates.index(entry_d_m) + horizon:
                exit_px_m = closes[exit_idx]
                if pos_m == 1:
                    pnl = (exit_px_m - entry_px_m) / entry_px_m * 100
                else:
                    pnl = (entry_px_m - exit_px_m) / entry_px_m * 100
                all_momentum.append(
                    {
                        "variety": var,
                        "entry": entry_d_m,
                        "exit": d,
                        "direction": "long" if pos_m == 1 else "short",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "momentum",
                    }
                )
                pos_m = 0

            if pos_m == 0:
                if m_long:
                    pos_m = 1
                    entry_px_m = px_now
                    entry_d_m = d
                elif m_short:
                    pos_m = -1
                    entry_px_m = px_now
                    entry_d_m = d

    if not all_contrarian and not all_momentum:
        return {"total_trades": 0, "message": "No signals"}

    # Build curves
    all_dates = sorted({t["entry"] for t in all_contrarian + all_momentum})
    c_map = {}
    m_map = {}
    for t in all_contrarian:
        c_map[t["entry"]] = c_map.get(t["entry"], 0) + t["pnl"]
    for t in all_momentum:
        m_map[t["entry"]] = m_map.get(t["entry"], 0) + t["pnl"]

    c_curve = []
    m_curve = []
    cum_c = 0
    cum_m = 0
    for d in all_dates:
        cum_c += c_map.get(d, 0)
        cum_m += m_map.get(d, 0)
        c_curve.append(round(cum_c, 2))
        m_curve.append(round(cum_m, 2))

    def wr(ts):
        """胜率:win 笔数 / 总笔数;空列表返回 0。"""
        return round(sum(1 for t in ts if t["outcome"] == "win") / len(ts), 3) if ts else 0

    # Compute advanced metrics per sub-strategy
    c_pnls = [t["pnl"] for t in all_contrarian]
    m_pnls = [t["pnl"] for t in all_momentum]
    all_entry_dates = sorted({t["entry"] for t in all_contrarian + all_momentum})
    td = (
        max(
            (
                datetime.strptime(all_entry_dates[-1], "%Y-%m-%d")
                - datetime.strptime(all_entry_dates[0], "%Y-%m-%d")
            ).days
            * 252
            // 365,
            20,
        )
        if len(all_entry_dates) >= 2
        else 252
    )
    adv_c = compute_advanced_metrics(trade_pnls=c_pnls, total_trading_days=td) if c_pnls else {}
    adv_m = compute_advanced_metrics(trade_pnls=m_pnls, total_trading_days=td) if m_pnls else {}

    return {
        "strategy": "contrarian_sentiment",
        "contrarian": {
            "trades": len(all_contrarian),
            "win_rate": wr(all_contrarian),
            "total_pnl": round(c_curve[-1], 2) if c_curve else 0,
            "label": "逆情绪(背离)",
            "advanced_metrics": adv_c,
        },
        "consensus": {
            "trades": len(all_momentum),
            "win_rate": wr(all_momentum),
            "total_pnl": round(m_curve[-1], 2) if m_curve else 0,
            "label": "情绪共识",
            "advanced_metrics": adv_m,
        },
        "dates": all_dates,
        "curves": {"contrarian": c_curve, "consensus": m_curve},
        "variety": variety or "all",
        "horizon": horizon,
        "trend_window": trend_window,
    }


# ═══════════════════════════════════════════════════════════════════
# Adaptive Sentiment Strategy (auto-choose contrarian vs momentum)
# ═══════════════════════════════════════════════════════════════════


def run_adaptive_sentiment(
    variety: str = "",
    horizon: int = 3,
    trend_window: int = 5,
    start_date: str = "2025-01-01",
    end_date: str = "",
) -> dict:
    """Adaptive sentiment: auto-choose contrarian or momentum based on conditions.

    Decision rules (empirically validated):
      - Trend > 2% AND sentiment agrees → MOMENTUM (crowd is right in trends)
      - Trend < 1% → CONTRARIAN (sentiment as reversal signal in ranges)
      - Sentiment DISAGREES with trend → CONTRARIAN (divergence = reversal)
      - Otherwise → MOMENTUM (default follow)

    【功能】自适应情绪策略:按"趋势强弱 + 情绪是否同向"动态选择顺势(动量)或
    逆向(逆向情绪)交易,并统计每次选择的决策类别。
    【参数】variety / horizon / trend_window / start_date / end_date:含义与
    run_contrarian_sentiment 一致(horizon=持仓天数,trend_window=趋势窗口)。
    【返回】dict,含 strategy / adaptive / consensus / decisions / dates / curves /
    variety / horizon / trend_window;无信号时返回 {"total_trades": 0, ...}。
    【关键逻辑】决策树(经验验证,代码注释里写了准确率):
      1. 涨+看空(Type A)→ 高置信逆向做空(60% 准确率);
      2. 跌+看多(Type B)→ 若强跌(>3%)视为"接飞刀"跳过,否则视为抄底做多;
      3. 强趋势(>2%)→ 顺势(情绪跟着价格走);
      4. 弱趋势(<1%)→ 逆向(震荡市里情绪当反转信号);
      5. 其余 → 默认顺势。
      与 contrarian 一样,同时维护 adaptive 与纯 momentum 基线两条路径作对比。
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
        last_s = 0
        si = 0
        for d in sorted(str(x["date"])[:10] for x in px):
            while si < len(sd_sorted) and sd_sorted[si] <= d:
                last_s = raw_sent[sd_sorted[si]]
                si += 1
            sent_map[d] = last_s

        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        pos_a = 0
        entry_px_a = 0
        entry_d_a = ""  # adaptive
        pos_m = 0
        entry_px_m = 0
        entry_d_m = ""  # momentum baseline

        for i in range(trend_window, n - horizon):
            d = dates[i]
            if d < start_date or (end_date and d > end_date):
                continue
            ss = sent_map.get(d, 0)
            px_now = closes[i]
            trend = (
                (closes[i] - closes[i - trend_window]) / closes[i - trend_window]
                if closes[i - trend_window]
                else 0
            )
            trend_pct = abs(trend) * 100

            # Adaptive: choose contrarian or momentum based on conditions
            # Distinguish two types of divergence (empirically validated):
            #   Type A (涨+看空): 60% accuracy → HIGH confidence contrarian (top signal)
            #   Type B (跌+看多): 21% accuracy → SKIP (catching falling knife)
            diverge_bearish = trend > 0.01 and ss < -0.1  # price up + bearish = top signal
            diverge_bullish = trend < -0.01 and ss > 0.1  # price down + bullish = bottom fishing

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
                    a_long = False
                    a_short = False
                    decisions["skipped"] = decisions.get("skipped", 0) + 1
                else:
                    # Moderate dip + bullish = potential bottom → CONTRARIAN long
                    use_contrarian = True
                    a_long = True
                    a_short = False
            elif trend_pct > 2:
                # Strong trend → MOMENTUM (follow the crowd)
                use_contrarian = False
            elif trend_pct < 1:
                # Weak/ranging trend → CONTRARIAN (reversal signals)
                use_contrarian = True
            else:
                # Default: MOMENTUM
                use_contrarian = False

            # 决策计数:注意第一个分支是 `diverge_bearish or (diverge_bullish and
            # use_contrarian)`(and 优先级高于 or),即"看空背离"或"被采纳的看多背离"
            # 计入 contrarian;未被采纳的看多背离计入 skipped;其余按 use_contrarian
            # 决定计 contrarian 还是 momentum。计数仅用于回传展示,不改变交易行为。
            if diverge_bearish or diverge_bullish and use_contrarian:
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
                all_adaptive.append(
                    {
                        "variety": var,
                        "entry": entry_d_a,
                        "exit": d,
                        "direction": "long" if pos_a == 1 else "short",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "adaptive",
                    }
                )
                pos_a = 0

            if pos_a == 0:
                if a_long:
                    pos_a = 1
                    entry_px_a = px_now
                    entry_d_a = d
                elif a_short:
                    pos_a = -1
                    entry_px_a = px_now
                    entry_d_a = d

            # Process momentum
            if pos_m != 0 and entry_d_m and i >= dates.index(entry_d_m) + horizon:
                exit_px_m = closes[exit_idx]
                if pos_m == 1:
                    pnl = (exit_px_m - entry_px_m) / entry_px_m * 100
                else:
                    pnl = (entry_px_m - exit_px_m) / entry_px_m * 100
                all_momentum.append(
                    {
                        "variety": var,
                        "entry": entry_d_m,
                        "exit": d,
                        "direction": "long" if pos_m == 1 else "short",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "momentum",
                    }
                )
                pos_m = 0

            if pos_m == 0:
                if m_long:
                    pos_m = 1
                    entry_px_m = px_now
                    entry_d_m = d
                elif m_short:
                    pos_m = -1
                    entry_px_m = px_now
                    entry_d_m = d

    if not all_adaptive and not all_momentum:
        return {"total_trades": 0, "message": "No signals"}

    all_dates = sorted({t["entry"] for t in all_adaptive + all_momentum})
    a_map = {}
    m_map = {}
    for t in all_adaptive:
        a_map[t["entry"]] = a_map.get(t["entry"], 0) + t["pnl"]
    for t in all_momentum:
        m_map[t["entry"]] = m_map.get(t["entry"], 0) + t["pnl"]

    a_curve = []
    m_curve = []
    cum_a = 0
    cum_m = 0
    for d in all_dates:
        cum_a += a_map.get(d, 0)
        cum_m += m_map.get(d, 0)
        a_curve.append(round(cum_a, 2))
        m_curve.append(round(cum_m, 2))

    def wr(ts):
        """胜率:win 笔数 / 总笔数;空列表返回 0。"""
        return round(sum(1 for t in ts if t["outcome"] == "win") / len(ts), 3) if ts else 0

    # Compute advanced metrics per sub-strategy
    a_pnls = [t["pnl"] for t in all_adaptive]
    m_pnls = [t["pnl"] for t in all_momentum]
    all_entry_dates2 = sorted({t["entry"] for t in all_adaptive + all_momentum})
    td2 = (
        max(
            (
                datetime.strptime(all_entry_dates2[-1], "%Y-%m-%d")
                - datetime.strptime(all_entry_dates2[0], "%Y-%m-%d")
            ).days
            * 252
            // 365,
            20,
        )
        if len(all_entry_dates2) >= 2
        else 252
    )
    adv_a = compute_advanced_metrics(trade_pnls=a_pnls, total_trading_days=td2) if a_pnls else {}
    adv_m2 = compute_advanced_metrics(trade_pnls=m_pnls, total_trading_days=td2) if m_pnls else {}

    return {
        "strategy": "adaptive_sentiment",
        "adaptive": {
            "trades": len(all_adaptive),
            "win_rate": wr(all_adaptive),
            "total_pnl": round(a_curve[-1], 2) if a_curve else 0,
            "label": "自适应情绪",
            "advanced_metrics": adv_a,
        },
        "consensus": {
            "trades": len(all_momentum),
            "win_rate": wr(all_momentum),
            "total_pnl": round(m_curve[-1], 2) if m_curve else 0,
            "label": "情绪共识",
            "advanced_metrics": adv_m2,
        },
        "decisions": decisions,
        "dates": all_dates,
        "curves": {"adaptive": a_curve, "consensus": m_curve},
        "variety": variety or "all",
        "horizon": horizon,
        "trend_window": trend_window,
    }


# ═══════════════════════════════════════════════════════════════════
# Donchian Channel Breakout (Turtle-style)
# ═══════════════════════════════════════════════════════════════════


def run_donchian_strategy(variety="", period=20, start_date="2025-01-01", end_date=""):
    """Donchian Channel Breakout — Turtle Trading classic.
    Entry: price breaks above N-day high → LONG; breaks below N-day low → SHORT
    Exit: price crosses back below/below the opposite side

    【功能】唐奇安通道突破(海龟交易经典):价格突破 N 日最高做多,跌破 N 日最低做空;
    反向突破/跌穿另一侧通道时离场。
    【参数】variety:品种,空串取前 10 个有数据品种;period:通道天数 N,默认 20;
    start_date / end_date:回测日期窗。
    【返回】dict,含 strategy / total_trades / win_count / loss_count / win_rate /
    avg_pnl_pct / total_pnl / sharpe_like / max_drawdown_pct / long_trades /
    short_trades / period / advanced_metrics / recent_trades;无交易返回 {"total_trades": 0}。
    【关键逻辑】
      - 入场:收盘价 > 前 period 日最高(hh,不含当日)做多;收盘价 < 前 period 日
        最低(ll)做空。hh/ll 用 "i-period:i" 切片,即"不含当日"的滚动窗口。
      - 离场:持多时若收盘跌破 ll(反向突破)→ 平多;持空时若收盘涨破 hh → 平空。
      - 末尾强平:日期窗内最后一根 bar(last_i)仍持仓时,按该 bar 收盘价强平,避免
        把回测窗口之外的数据算进结果。
    """
    all_trades = []
    vlist = [variety] if variety else _get_all_varieties_with_data()[:10]
    for var in vlist:
        p = _load_price(var)
        if not p or len(p.get("prices", [])) < period + 5:
            continue
        px = p["prices"]
        closes = [float(x["close"]) for x in px]
        highs = [float(x["high"]) for x in px]
        lows = [float(x["low"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        pos = 0
        entry_px = 0
        entry_d = ""
        last_i = None  # 窗口内最后一根 bar;窗口末仍有持仓时按它强平
        for i in range(period, n):
            d = dates[i]
            if d < start_date or (end_date and d > end_date):
                continue
            last_i = i
            hh = max(highs[i - period : i])
            ll = min(lows[i - period : i])
            cu = closes[i] > hh  # Breakout up
            cd = closes[i] < ll  # Breakout down

            if pos == 1 and cd and entry_px:
                pnl = (closes[i] - entry_px) / entry_px * 100
                all_trades.append(
                    {
                        "variety": var,
                        "entry": entry_d,
                        "exit": d,
                        "direction": "long",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "donchian",
                    }
                )
                pos = 0
            elif pos == -1 and cu and entry_px:
                pnl = (entry_px - closes[i]) / entry_px * 100
                all_trades.append(
                    {
                        "variety": var,
                        "entry": entry_d,
                        "exit": d,
                        "direction": "short",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "donchian",
                    }
                )
                pos = 0

            if pos == 0:
                if cu:
                    pos = 1
                    entry_px = closes[i]
                    entry_d = d
                elif cd:
                    pos = -1
                    entry_px = closes[i]
                    entry_d = d

        if pos != 0 and entry_px and last_i is not None:
            final = closes[last_i]
            pnl = (
                (final - entry_px) / entry_px * 100
                if pos == 1
                else (entry_px - final) / entry_px * 100
            )
            all_trades.append(
                {
                    "variety": var,
                    "entry": entry_d,
                    "exit": dates[last_i],
                    "direction": "long" if pos == 1 else "short",
                    "pnl": round(pnl, 2),
                    "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                    "signal": "donchian",
                }
            )

    if not all_trades:
        return {"total_trades": 0}
    wins = [t for t in all_trades if t["outcome"] == "win"]
    pnls = [t["pnl"] for t in all_trades]
    avg = sum(pnls) / len(pnls)
    (sum((x - avg) ** 2 for x in pnls) / len(pnls)) ** 0.5 if len(pnls) > 1 else 1
    cum = 0
    peak = 0
    dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    # Estimate trading days
    entry_dates = sorted({t["entry"] for t in all_trades})
    td = (
        max(
            (
                datetime.strptime(entry_dates[-1], "%Y-%m-%d")
                - datetime.strptime(entry_dates[0], "%Y-%m-%d")
            ).days
            * 252
            // 365,
            len(pnls) * period,
        )
        if len(entry_dates) >= 2
        else len(pnls) * period
    )
    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=td)
    return {
        "strategy": "donchian",
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(all_trades) - len(wins),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg, 2),
        "total_pnl": round(sum(pnls), 2) if pnls else 0,
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "long_trades": len([t for t in all_trades if t["direction"] == "long"]),
        "short_trades": len([t for t in all_trades if t["direction"] == "short"]),
        "period": period,
        "advanced_metrics": advanced,
        "recent_trades": all_trades,
    }


# ═══════════════════════════════════════════════════════════════════
# Technical strategies (MA cross / MACD / RSI / Bollinger / Turtle / ATR)
# Shared indicator helpers + one uniform signal interface + one backtest
# engine power the 12 new strategies: 6 indicators × (pure-price + sentiment-adaptive).
# ═══════════════════════════════════════════════════════════════════


def _sma(vals: list[float], period: int) -> list[float | None]:
    """Simple moving average; None before the first `period` values.

    【功能】简单移动平均。
    【参数】vals:价格序列;period:窗口天数。
    【返回】与 vals 等长的列表,前 period-1 个为 None(数据不足),其余为均值。
    【关键逻辑】用"滑动和"实现 O(n):每步加新值、减掉 period 步之前的值,
    除以 period 即窗口均值,不必每次重算整个窗口。
    """
    out: list[float | None] = [None] * len(vals)
    run = 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= period:
            run -= vals[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _ema(vals: list[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first `period` values.

    【功能】指数移动平均,对近期价格赋予更高权重。
    【参数】vals:价格序列;period:窗口天数。
    【返回】与 vals 等长的列表;前 period-1 个为 None(seed 之前无效)。
    【关键逻辑】平滑系数 k = 2/(period+1),EMA 递推:ema = v*k + 上一EMA*(1-k)。
    初始值用前 period 个的 SMA 作为 seed,避免从 0 起步的偏置。
    """
    out: list[float | None] = [None] * len(vals)
    if len(vals) < period:
        return out
    prev = sum(vals[:period]) / period
    out[period - 1] = prev
    k = 2.0 / (period + 1)
    for i in range(period, len(vals)):
        prev = vals[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI; None before the seed window.

    【功能】相对强弱指标 RSI(Wilder 平滑版本),衡量近期涨跌动能。
    【参数】closes:收盘价序列;period:窗口,默认 14。
    【返回】与 closes 等长的列表;seed 之前为 None。
    【关键逻辑】RSI = 100 - 100/(1 + 平均涨幅/平均跌幅),范围 [0,100]。
    涨跌幅的平均用 Wilder 递推(avg = (前值*(period-1) + 当日值)/period),
    等效于变相 EMA。avg_loss 为 0(纯上涨)时 RSI=100。
    """
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        avg_gain += max(d, 0.0)
        avg_loss += max(-d, 0.0)
    avg_gain /= period
    avg_loss /= period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def _macd(closes, fast=12, slow=26, signal=9):
    """MACD 三序列 (dif, dea, hist);hist 从 index slow+signal-2 起有效。

    【功能】MACD 指标,输出三条序列。
    【参数】closes:收盘价;fast/slow/signal:快慢 EMA 与信号线周期,默认 12/26/9。
    【返回】(dif, dea, hist) 三个与 closes 等长的列表:
      - dif = EMA(fast) - EMA(slow),快慢线差值;
      - dea = dif 的 signal 期 EMA(信号线);
      - hist = dif - dea(柱状体,金叉/死叉看它是否穿越 0)。
    【关键逻辑】dif 只在两条 EMA 都有效后才有值;为了保证 dea 从对应位置起就连续,
    先把有效的 dif 抽成"密集段"做 EMA,再在开头补回同样数量的 None,与 closes 对齐。
    """
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [None] * len(closes)
    dense = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            v = ema_fast[i] - ema_slow[i]
            dif[i] = v
            dense.append(v)
    start = len(closes) - len(dense)
    dea_dense = _ema(dense, signal)
    dea = [None] * start + dea_dense
    hist = [
        (dif[i] - dea[i]) if dif[i] is not None and dea[i] is not None else None
        for i in range(len(closes))
    ]
    return dif, dea, hist


def _bollinger(closes, period=20, num_std=2.0):
    """布林带三序列 (mid, upper, lower)。

    【功能】布林带指标。
    【参数】closes:收盘价;period:中轨 SMA 周期,默认 20;num_std:带宽(标准差倍数)。
    【返回】(mid, upper, lower) 三个与 closes 等长的列表;period-1 之前为 None。
    【关键逻辑】mid = 20 日 SMA;上/下轨 = mid ± num_std * 窗口标准差(总体标准差,
    分母 period)。价格触上轨视为超买、触下轨视为超卖,突破轨线则是趋势信号。
    """
    mid = _sma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        m = mid[i]
        window = closes[i - period + 1 : i + 1]
        std = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        upper[i] = m + num_std * std
        lower[i] = m - num_std * std
    return mid, upper, lower


def _atr(highs, lows, closes, period=14):
    """Average True Range (Wilder smoothing); None before the seed bar.

    【功能】平均真实波幅 ATR:衡量市场波动幅度,常被用作止损距离。
    【参数】highs / lows / closes:最高/最低/收盘价;period:窗口,默认 14。
    【返回】与 closes 等长的列表;seed 之前为 None。
    【关键逻辑】真实波幅 TR = max(当日振幅, |当日高-昨收|, |当日低-昨收|),
    取三者最大,覆盖跳空缺口带来的波动。ATR 用 Wilder 平滑(递推加权平均),
    seed 为前 period 个 TR 的均值。海龟策略用它计算 ATR 止损距离。
    """
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period + 1, len(closes)):
        prev = (prev * (period - 1) + trs[i - 1]) / period
        out[i] = prev
    return out


def _build_tech_series(indicator, closes, highs, lows, P):
    """预计算指标序列;返回 dict(含 warmup=循环起始索引)。

    【功能】一次性预计算某个技术指标所需的全部序列,供回测/今日信号共用。
    【参数】indicator:ma_cross / macd / rsi / bollinger / turtle / atr;
    closes/highs/lows:行情序列;P:策略参数字典(键见各分支)。
    【返回】dict T,内含各指标序列 + warmup(循环可以从第几根 K 线开始,之前的
    数据不足)。warmup 由指标自己定义,回测和今日信号据此跳过数据不足的头部。
    【关键逻辑】为什么"先建序列再判定":指标计算开销大且重复,预计算一次后,
    回测循环里每根 K 线只做 O(1) 的查表/判定,避免每根都重算整个序列。
    turtle 特殊:入场用 e 日通道、离场用 x 日通道(海龟原版是 20 入 10 出),
    并额外算 ATR 供止损。
    """
    T = {}
    if indicator == "ma_cross":
        T["ma_fast"] = _sma(closes, P["fast"])
        T["ma_slow"] = _sma(closes, P["slow"])
        T["warmup"] = P["slow"]  # 慢线起算点 = 可用的最早位置
    elif indicator == "macd":
        _, _, T["macd_hist"] = _macd(closes, P["macd_fast"], P["macd_slow"], P["macd_signal"])
        T["warmup"] = P["macd_slow"] + P["macd_signal"]
    elif indicator == "rsi":
        T["rsi"] = _rsi(closes, P["rsi_period"])
        T["warmup"] = P["rsi_period"]
    elif indicator in ("bollinger", "atr"):
        if indicator == "bollinger":
            T["bb_mid"], T["bb_upper"], T["bb_lower"] = _bollinger(
                closes, P["bb_period"], P["num_std"]
            )
            T["warmup"] = P["bb_period"]
        else:
            # ATR 通道 = 肯特纳通道变体:mid = 周期 SMA,上下轨 = mid ± mult*ATR。
            T["bb_mid"] = _sma(closes, P["keltner_period"])
            T["atr"] = _atr(highs, lows, closes, P["keltner_period"])
            T["bb_upper"] = [None] * len(closes)
            T["bb_lower"] = [None] * len(closes)
            for i in range(P["keltner_period"], len(closes)):
                m = T["bb_mid"][i]
                a = T["atr"][i]
                if m is not None and a is not None:
                    T["bb_upper"][i] = m + P["keltner_mult"] * a
                    T["bb_lower"][i] = m - P["keltner_mult"] * a
            T["warmup"] = P["keltner_period"]
    elif indicator == "turtle":
        e = P["turtle_entry"]
        x = P["turtle_exit"]
        T["turtle_hh"] = [None] * len(closes)
        T["turtle_ll"] = [None] * len(closes)
        T["turtle_exit_hh"] = [None] * len(closes)
        T["turtle_exit_ll"] = [None] * len(closes)
        # 入场通道:前 e 日(不含当日)最高/最低
        for i in range(e, len(closes)):
            T["turtle_hh"][i] = max(highs[i - e : i])
            T["turtle_ll"][i] = min(lows[i - e : i])
        # 离场通道:前 x 日最高/最低,通常比入场通道窄 → 更快触发反向离场
        for i in range(x, len(closes)):
            T["turtle_exit_hh"][i] = max(highs[i - x : i])
            T["turtle_exit_ll"][i] = min(lows[i - x : i])
        T["atr"] = _atr(highs, lows, closes, P["atr_period"])
        T["warmup"] = max(e, P["atr_period"])
    return T


def _tech_signal(indicator, i, closes, highs, lows, T, P):
    """统一信号判定:返回 (enter_long, enter_short, exit_long, exit_short,
    strength, scale, reason)。

    【功能】对第 i 根 K 线做统一的技术信号判定,返回 4 个布尔 + 强度 + 标尺 + 原因。
    【参数】indicator:指标名;i:当前下标;closes/highs/lows:行情;T:指标序列 dict;
    P:策略参数。
    【返回】元组 (enter_long, enter_short, exit_long, exit_short, strength, scale, reason):
      - enter_*:是否触发"新开多/新开空";
      - exit_*:是否触发"平多/平空"(通常与反向入场条件重合,即"对向离场");
      - strength:信号强度(量纲随指标不同,如均线间隙比、RSI 偏离度、突破幅度);
      - scale:该指标 strength 的归一化标尺(供 _make_signal 把 strength 折算成
        [0,1] 的 strength_pct);
      - reason:中文信号原因,用于展示/日志。
    【关键逻辑】每个指标内部先用"数据不足 → 全 False + reason"做保护;随后按各指标
    的规则给出信号。数值口径(0.05 / 0.02 / 1.0 等 scale)是经验取值,含义见各分支。
    """
    if indicator == "ma_cross":
        f, s = T["ma_fast"][i], T["ma_slow"][i]
        f1, s1 = T["ma_fast"][i - 1], T["ma_slow"][i - 1]
        if None in (f, s, f1, s1):
            return False, False, False, False, 0.0, 0.05, "均线数据不足"
        gap = (f - s) / closes[i]  # 快慢线间距相对价格,作强度
        # 金叉(f>s 且昨日未金叉)开多;死叉开空;快线跌破慢线平多;反之平空
        return f > s and f1 <= s1, f < s and f1 >= s1, f < s, f > s, abs(gap), 0.05, "双均线交叉"
    if indicator == "macd":
        h, h1 = T["macd_hist"][i], T["macd_hist"][i - 1]
        if h is None or h1 is None:
            return False, False, False, False, 0.0, 0.02, "MACD 数据不足"
        # 金叉:hist 从 <=0 上穿 0 开多;死叉开空;hist<0 平多、hist>0 平空
        return (
            h > 0 and h1 <= 0,
            h < 0 and h1 >= 0,
            h < 0,
            h > 0,
            abs(h) / closes[i],
            0.02,
            "MACD 金叉死叉",
        )
    if indicator == "rsi":
        r = T["rsi"][i]
        if r is None:
            return False, False, False, False, 0.0, 1.0, "RSI 数据不足"
        # 均值回归:超卖(<oversold)开多、超买(>overbought)开空;回归 50 线平仓
        return (
            r < P["rsi_oversold"],
            r > P["rsi_overbought"],
            r > 50,
            r < 50,
            abs(r - 50) / 50.0,  # RSI 离 50 越远强度越大,50 为分界
            1.0,
            "RSI 超买超卖",
        )
    if indicator == "bollinger":
        m, u, lo = T["bb_mid"][i], T["bb_upper"][i], T["bb_lower"][i]
        if None in (m, u, lo):
            return False, False, False, False, 0.0, 1.0, "布林带数据不足"
        c = closes[i]
        z = abs(c - m) / (u - m) if u > m else 1.0  # 价格在带内的归一化位置,作强度
        # 突破上轨开多、跌破下轨开空;回归中轨(mid)平仓
        return c > u, c < lo, c < m, c > m, z, 1.0, "布林带突破"
    if indicator == "turtle":
        hh, ll = T["turtle_hh"][i], T["turtle_ll"][i]
        ex_hh, ex_ll = T["turtle_exit_hh"][i], T["turtle_exit_ll"][i]
        if None in (hh, ll, ex_hh, ex_ll):
            return False, False, False, False, 0.0, 0.05, "海龟通道数据不足"
        c = closes[i]
        # br = 突破超过通道的幅度(相对通道边界),突破越多强度越大
        br = (c - hh) / hh if c > hh else (ll - c) / ll if c < ll else 0.0
        # 入场:突破 e 日通道;离场:反向跌破 x 日离场通道(比入场通道窄,更快出场)
        return c > hh, c < ll, c < ex_ll, c > ex_hh, abs(br), 0.05, "海龟突破"
    if indicator == "atr":
        m, u, lo = T["bb_mid"][i], T["bb_upper"][i], T["bb_lower"][i]
        if None in (m, u, lo):
            return False, False, False, False, 0.0, 0.05, "ATR 通道数据不足"
        c = closes[i]
        br = (c - u) / c if c > u else (lo - c) / c if c < lo else 0.0
        # 与布林带同构:突破 ATR 通道上/下轨入场,回归 mid 离场
        return c > u, c < lo, c < m, c > m, abs(br), 0.05, "ATR 通道突破"
    return False, False, False, False, 0.0, 1.0, "未知指标"


def _adapt_sentiment_signal(tsig, tech_strength, tech_scale, trend, ss):
    """情绪自适应包装(镜像 adaptive_sent 的分歧/顺势/逆向哲学)。

    情绪作为**自适应因子**,不是原始方向门控:
      - tsig==0(无技术触发):弱趋势 + 极端情绪 → 逆向入场(镜像 adaptive ④)。
      - 技术信号与情绪同向(|ss|>0.1)→ 顺势确认。
      - 分歧:强趋势(>3%)信技术(动量市);弱趋势(<1%)逆情绪(逆向市);中等→信技术。
      - 情绪中性 → 跟随技术。
    返回 (action, strength, scale, reason)。
    """
    trend_pct = abs(trend) * 100
    # ── 无技术信号:弱趋势(<1%)+ 极端情绪 → 用情绪做逆向入场(镜像自适应④)──
    if tsig == 0:
        if trend_pct < 1 and ss < -0.1:
            return "buy", abs(ss), 1.0, "无技术信号 + 弱趋势看空 → 逆向买入"
        if trend_pct < 1 and ss > 0.1:
            return "sell", abs(ss), 1.0, "无技术信号 + 弱趋势看多 → 逆向卖出"
        return "hold", abs(ss), 1.0, "无技术信号"
    tech_dir = "buy" if tsig == 1 else "sell"
    # ── 技术信号与情绪同向(|ss|>0.1)→ 顺势确认;强度取两者中较大者 ──
    if (tsig == 1 and ss > 0.1) or (tsig == -1 and ss < -0.1):
        if tech_strength >= abs(ss):
            return tech_dir, tech_strength, tech_scale, "技术信号 + 情绪同向确认"
        return tech_dir, abs(ss), 1.0, "技术信号 + 情绪同向确认"
    # ── 技术信号与情绪分歧(背离)──
    if (tsig == 1 and ss < -0.1) or (tsig == -1 and ss > 0.1):
        if trend_pct > 3:
            # 强趋势下的分歧:动量市,信技术
            return tech_dir, tech_strength, tech_scale, f"强趋势 {trend_pct:.1f}% 分歧 → 跟随技术"
        if trend_pct < 1:
            # 弱趋势下的分歧:逆向市,逆情绪交易
            return (
                ("buy" if tsig == -1 else "sell"),
                abs(ss),
                1.0,
                f"弱趋势 {trend_pct:.1f}% 分歧 → 逆向情绪",
            )
        return tech_dir, tech_strength, tech_scale, f"中等趋势 {trend_pct:.1f}% 分歧 → 跟随技术"
    # 情绪中性 → 完全跟随技术信号
    return tech_dir, tech_strength, tech_scale, "技术信号(情绪中性)"


def _run_technical_backtest(
    variety="", indicator="ma_cross", sent_mode=False, start_date="2025-01-01", end_date="", **P
):
    """通用技术策略回测引擎,供 12 个薄包装调用。

    indicator: ma_cross / macd / rsi / bollinger / turtle / atr。
    sent_mode=True 时入场信号经 _adapt_sentiment_signal 用情绪自适应调整
    (离场保持纯技术);False 为纯价格版本。参数经 P 传入(trend_window 供情绪版)。
    返回与 donchian 相同的单策略 dict 形状。

    【功能】12 个技术策略共用的统一回测引擎。
    【参数】variety:品种(空串取前 10 个有数据品种);indicator:指标名;
    sent_mode:是否启用情绪自适应入场;start_date/end_date:回测日期窗;
    P:各指标参数(fast/slow/macd_*/rsi_*/bb_*/turtle_*/atr_*/keltner_*/trend_window)。
    【返回】单策略 dict:strategy / total_trades / win_count / loss_count / win_rate /
    avg_pnl_pct / total_pnl / sharpe_like / max_drawdown_pct / long_trades /
    short_trades / advanced_metrics / recent_trades;并把传入的参数原样回显
    (result[k] = P[k],方便前端展示用了哪些参数);无交易返回 {"total_trades": 0}。
    【关键逻辑】
      - 多空仓位循环:pos ∈ {0, 1, -1}(0 空仓 / 1 持多 / -1 持空),每根 K 线
        "先离场、后入场":先检查平仓条件,再在空仓状态下开新仓,避免同根 K 线
        先平后开导致的重复信号。
      - 对向离场:多数指标"平多条件"就是反向入场条件(如 MA 死叉平多),也就是
        _tech_signal 返回的 exit_long/exit_short。
      - turtle 额外叠加 ATR 止损:持仓时若价格跌破 entry - atr_mult*ATR(多)/涨破
        entry + atr_mult*ATR(空),强制平仓(覆盖 _tech_signal 的普通离场)。
      - sent_mode 时,用情绪自适应函数改写 el/es(只改入场,离场 xl/xs 不变)。
      - 末尾强平:日期窗内最后一根 bar(last_i)仍持仓则按该 bar 收盘强平,保证回测
        只用到窗口内数据。
    """
    all_trades = []
    vlist = [variety] if variety else _get_all_varieties_with_data()[:10]
    for var in vlist:
        p = _load_price(var)
        if not p or not p.get("prices"):
            continue
        px = p["prices"]
        closes = [float(x["close"]) for x in px]
        highs = [float(x["high"]) for x in px]
        lows = [float(x["low"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)
        T = _build_tech_series(indicator, closes, highs, lows, P)
        if n <= T["warmup"]:
            continue
        sent_map = {}
        if sent_mode:
            sent_data = _load_trends(var) or _load_sentiment(var)
            sent_map = _build_forward_filled_sent_map(sent_data, dates) if sent_data else {}

        pos = 0
        entry_px = 0.0
        entry_d = ""
        last_i = None  # 窗口内最后一根 bar;窗口末仍有持仓时按它强平(而非全数据最后一根)
        for i in range(T["warmup"], n):
            d = dates[i]
            if d < start_date or (end_date and d > end_date):
                continue
            last_i = i
            el, es, xl, xs, tstr, tscale, _ = _tech_signal(indicator, i, closes, highs, lows, T, P)
            if indicator == "turtle" and pos != 0 and entry_px:
                # 海龟 ATR 止损:距入场价超过 atr_mult*ATR 即离场,价格越烈越早止损
                atr = T["atr"][i]
                if atr and atr > 0:
                    stop = P["atr_mult"] * atr
                    if pos == 1 and closes[i] < entry_px - stop:
                        xl = True
                    if pos == -1 and closes[i] > entry_px + stop:
                        xs = True
            if sent_mode:
                tw = P.get("trend_window", 5)
                trend = (
                    (closes[i] - closes[i - tw]) / closes[i - tw]
                    if i >= tw and closes[i - tw]
                    else 0.0
                )
                # 用情绪自适应改写入场信号;action 返回 buy/sell/hold
                action, _, _, _ = _adapt_sentiment_signal(
                    1 if el else (-1 if es else 0), tstr, tscale, trend, sent_map.get(d, 0.0)
                )
                el, es = action == "buy", action == "sell"

            # ── 先离场:多单看 xl、空单看 xs,按对向信号平仓 ──
            if pos == 1 and xl and entry_px:
                pnl = (closes[i] - entry_px) / entry_px * 100
                all_trades.append(_trade(var, entry_d, d, "long", pnl, indicator, sent_mode))
                pos = 0
            elif pos == -1 and xs and entry_px:
                pnl = (entry_px - closes[i]) / entry_px * 100
                all_trades.append(_trade(var, entry_d, d, "short", pnl, indicator, sent_mode))
                pos = 0

            # ── 后入场:空仓时才开新仓,避免持仓期内反复开仓 ──
            if pos == 0:
                if el:
                    pos, entry_px, entry_d = 1, closes[i], d
                elif es:
                    pos, entry_px, entry_d = -1, closes[i], d

        if pos != 0 and entry_px and last_i is not None:
            # 末尾强平:窗口内最后一根 bar 仍持仓时,按该 bar 收盘价结算一笔交易。
            final = closes[last_i]
            pnl = (
                (final - entry_px) / entry_px * 100
                if pos == 1
                else (entry_px - final) / entry_px * 100
            )
            all_trades.append(
                _trade(
                    var,
                    entry_d,
                    dates[last_i],
                    "long" if pos == 1 else "short",
                    pnl,
                    indicator,
                    sent_mode,
                )
            )

    if not all_trades:
        return {"total_trades": 0}
    wins = [t for t in all_trades if t["outcome"] == "win"]
    pnls = [t["pnl"] for t in all_trades]
    avg = sum(pnls) / len(pnls)
    # 手算最大回撤:沿累计 PnL 走,peak 记历史最高,dd = peak - 当前累计,取最大
    cum = 0
    peak = 0
    dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    # 估计交易天数:用首末入场日的自然日天数 * 252/365 折算成交易日,
    # 但至少给 len(pnls)*trend_window 天,避免样本极少时年化指标被放大失真。
    entry_dates = sorted({t["entry"] for t in all_trades})
    td = (
        max(
            (
                datetime.strptime(entry_dates[-1], "%Y-%m-%d")
                - datetime.strptime(entry_dates[0], "%Y-%m-%d")
            ).days
            * 252
            // 365,
            len(pnls) * P.get("trend_window", 5),
        )
        if len(entry_dates) >= 2
        else len(pnls) * P.get("trend_window", 5)
    )
    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=td)
    result = {
        "strategy": f"{indicator}{'_sent' if sent_mode else ''}",
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(all_trades) - len(wins),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg, 2),
        "total_pnl": round(sum(pnls), 2) if pnls else 0,
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "long_trades": len([t for t in all_trades if t["direction"] == "long"]),
        "short_trades": len([t for t in all_trades if t["direction"] == "short"]),
        "advanced_metrics": advanced,
        "recent_trades": all_trades,
    }
    for k in (
        "fast",
        "slow",
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "rsi_period",
        "rsi_overbought",
        "rsi_oversold",
        "bb_period",
        "num_std",
        "turtle_entry",
        "turtle_exit",
        "atr_period",
        "atr_mult",
        "keltner_period",
        "keltner_mult",
        "trend_window",
    ):
        if k in P:
            result[k] = P[k]
    return result


def _trade(var, entry, exit, direction, pnl, indicator, sent_mode):
    """构造一条技术策略交易记录(与 donchian 的记录字段一致)。

    【功能】把一笔已结算交易格式化成统一 dict,供回测结果收集。
    【参数】var:品种;entry/exit:入场/离场日期;direction:long/short;pnl:盈亏%
    (已乘 100 的百分比);indicator:指标名;sent_mode:是否情绪版。
    【返回】交易 dict,含 variety/entry/exit/direction/pnl/outcome/signal。
    【关键逻辑】outcome 用 ±0.15% 分档:>0.15% 记 win,<-0.15% 记 loss,中间记
    breakeven(打平)。这个 0.15% 阈值是全文件统一的"赢/亏"判定口径,各策略共用,
    保证对比时标准一致。
    """
    return {
        "variety": var,
        "entry": entry,
        "exit": exit,
        "direction": direction,
        "pnl": round(pnl, 2),
        "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
        "signal": f"{indicator}{'_sent' if sent_mode else ''}",
    }


# 12 个策略键 → (indicator, sent_mode);today_signal 与回测共用。
# 键名即对外暴露的策略名(如 "ma_cross"/"ma_cross_sent"),值里的 indicator 是
# 统一的指标名,sent_mode 表示是否情绪自适应版。12 个 = 6 指标 × (纯价格 + 情绪)。
# _latest_technical_signal 与 web 前端都通过这个表把"策略名"翻译成"指标+模式",
# 避免在回测与今日信号两处各写一份映射导致口径分裂。
TECH_KEYS = {
    "ma_cross": ("ma_cross", False),
    "ma_cross_sent": ("ma_cross", True),
    "macd": ("macd", False),
    "macd_sent": ("macd", True),
    "rsi": ("rsi", False),
    "rsi_sent": ("rsi", True),
    "bollinger": ("bollinger", False),
    "bollinger_sent": ("bollinger", True),
    "turtle": ("turtle", False),
    "turtle_sent": ("turtle", True),
    "atr": ("atr", False),
    "atr_sent": ("atr", True),
}


def _latest_technical_signal(strategy, variety, closes, highs, lows, dates, today, i, n, **P):
    """12 个技术策略的今日信号(纯价格或情绪自适应版)。

    【功能】计算某一技术策略在"最新交易日"的今日操作信号(被 latest_trading_signal 调用)。
    【参数】strategy:TECH_KEYS 里的策略键;variety:品种;closes/highs/lows/dates:
    行情序列;today/i/n:最新日期、最新下标、K 线总数;P:指标参数。
    【返回】数据不足时返回 None;否则 {"today_signal": 信号dict, "today_signals": {}}。
    【关键逻辑】与回测共用同一套 _build_tech_series/_tech_signal 与 TECH_KEYS,保证
    "今日信号"和"回测信号"口径一致。sent_mode 时情绪前向填充到 today 并走
    _adapt_sentiment_signal;纯价格版直接把 el/es 映射成 buy/sell/hold。
    """
    indicator, sent_mode = TECH_KEYS[strategy]
    T = _build_tech_series(indicator, closes, highs, lows, P)
    if n <= T["warmup"]:
        return None
    if sent_mode:
        sent_data = _load_trends(variety) or _load_sentiment(variety)
        if not sent_data:
            return None
        sent_map = _build_forward_filled_sent_map(sent_data, dates)
        ss = sent_map.get(today, 0.0)
    else:
        ss = 0.0
    el, es, _, _, tstr, tscale, treason = _tech_signal(indicator, i, closes, highs, lows, T, P)
    if sent_mode:
        tw = P["trend_window"]
        trend = (closes[i] - closes[i - tw]) / closes[i - tw] if i >= tw and closes[i - tw] else 0.0
        action, strength, scale, reason = _adapt_sentiment_signal(
            1 if el else (-1 if es else 0), tstr, tscale, trend, ss
        )
    else:
        action = "buy" if el else ("sell" if es else "hold")
        strength, scale, reason = tstr, tscale, treason
    if action == "hold" and not reason:
        reason = "指标未触发"
    sig = _make_signal(variety, today, action, strength, reason, scale=scale)
    return {"today_signal": sig, "today_signals": {}}


def run_ma_cross_strategy(variety="", fast=10, slow=30, start_date="2025-01-01", end_date=""):
    """双均线交叉(纯价格):快线上穿慢线做多,下穿做空。

    【功能】纯价格双均线交叉策略的薄包装,只负责把参数透传给统一引擎。
    【参数】variety:品种;fast/slow:快/慢 SMA 周期,默认 10/30;start_date/end_date:回测日期窗。
    【返回】统一引擎 _run_technical_backtest 返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety, "ma_cross", False, start_date, end_date, fast=fast, slow=slow
    )


def run_ma_cross_sent_strategy(
    variety="", fast=10, slow=30, trend_window=5, start_date="2025-01-01", end_date=""
):
    """双均线交叉(情绪确认):入场经情绪自适应调整。

    【功能】双均线交叉的情绪自适应版薄包装:sent_mode=True,入场信号再经
    _adapt_sentiment_signal 用情绪修正,离场保持纯技术。
    【参数】比纯价格版多一个 trend_window(趋势窗口,默认 5),供情绪自适应判定用。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "ma_cross",
        True,
        start_date,
        end_date,
        fast=fast,
        slow=slow,
        trend_window=trend_window,
    )


def run_macd_strategy(
    variety="", macd_fast=12, macd_slow=26, macd_signal=9, start_date="2025-01-01", end_date=""
):
    """MACD(纯价格):hist 上穿 0 做多,下穿做空。

    【功能】纯价格 MACD 策略薄包装。
    【参数】variety:品种;macd_fast/macd_slow/macd_signal:快慢 EMA 与信号线周期,
    默认 12/26/9;start_date/end_date:回测日期窗。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "macd",
        False,
        start_date,
        end_date,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
    )


def run_macd_sent_strategy(
    variety="",
    macd_fast=12,
    macd_slow=26,
    macd_signal=9,
    trend_window=5,
    start_date="2025-01-01",
    end_date="",
):
    """MACD(情绪确认):入场经情绪自适应调整。

    【功能】MACD 的情绪自适应版薄包装。
    【参数】比纯价格版多 trend_window(默认 5)。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "macd",
        True,
        start_date,
        end_date,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        trend_window=trend_window,
    )


def run_rsi_strategy(
    variety="",
    rsi_period=14,
    rsi_overbought=70,
    rsi_oversold=30,
    start_date="2025-01-01",
    end_date="",
):
    """RSI 均值回归(纯价格):超卖做多、超买卖出,回归 50 离场。

    【功能】纯价格 RSI 均值回归策略薄包装。
    【参数】variety:品种;rsi_period:RSI 周期(默认 14);rsi_overbought/rsi_oversold:
    超买/超卖阈值(默认 70/30);start_date/end_date:回测日期窗。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "rsi",
        False,
        start_date,
        end_date,
        rsi_period=rsi_period,
        rsi_overbought=rsi_overbought,
        rsi_oversold=rsi_oversold,
    )


def run_rsi_sent_strategy(
    variety="",
    rsi_period=14,
    rsi_overbought=70,
    rsi_oversold=30,
    trend_window=5,
    start_date="2025-01-01",
    end_date="",
):
    """RSI 均值回归(情绪确认):入场经情绪自适应调整。

    【功能】RSI 均值回归的情绪自适应版薄包装。
    【参数】比纯价格版多 trend_window(默认 5)。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "rsi",
        True,
        start_date,
        end_date,
        rsi_period=rsi_period,
        rsi_overbought=rsi_overbought,
        rsi_oversold=rsi_oversold,
        trend_window=trend_window,
    )


def run_bollinger_strategy(
    variety="", bb_period=20, num_std=2.0, start_date="2025-01-01", end_date=""
):
    """布林带突破(纯价格):突破上轨做多、下轨做空,回归中轨离场。

    【功能】纯价格布林带突破策略薄包装。
    【参数】variety:品种;bb_period:中轨 SMA 周期(默认 20);num_std:带宽倍数
    (默认 2.0);start_date/end_date:回测日期窗。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety, "bollinger", False, start_date, end_date, bb_period=bb_period, num_std=num_std
    )


def run_bollinger_sent_strategy(
    variety="", bb_period=20, num_std=2.0, trend_window=5, start_date="2025-01-01", end_date=""
):
    """布林带突破(情绪确认):入场经情绪自适应调整。

    【功能】布林带突破的情绪自适应版薄包装。
    【参数】比纯价格版多 trend_window(默认 5)。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "bollinger",
        True,
        start_date,
        end_date,
        bb_period=bb_period,
        num_std=num_std,
        trend_window=trend_window,
    )


def run_turtle_strategy(
    variety="",
    turtle_entry=20,
    turtle_exit=10,
    atr_period=14,
    atr_mult=2.0,
    start_date="2025-01-01",
    end_date="",
):
    """海龟交易法(纯价格):entry 日通道突破入场,exit 日通道或 ATR 止损离场。

    【功能】纯价格海龟(唐奇安突破)策略薄包装。
    【参数】variety:品种;turtle_entry:入场通道天数(默认 20);turtle_exit:离场
    通道天数(默认 10);atr_period:ATR 周期(默认 14);atr_mult:ATR 止损倍数
    (默认 2.0);start_date/end_date:回测日期窗。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "turtle",
        False,
        start_date,
        end_date,
        turtle_entry=turtle_entry,
        turtle_exit=turtle_exit,
        atr_period=atr_period,
        atr_mult=atr_mult,
    )


def run_turtle_sent_strategy(
    variety="",
    turtle_entry=20,
    turtle_exit=10,
    atr_period=14,
    atr_mult=2.0,
    trend_window=5,
    start_date="2025-01-01",
    end_date="",
):
    """海龟交易法(情绪确认):入场经情绪自适应调整。

    【功能】海龟突破的情绪自适应版薄包装。
    【参数】比纯价格版多 trend_window(默认 5)。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "turtle",
        True,
        start_date,
        end_date,
        turtle_entry=turtle_entry,
        turtle_exit=turtle_exit,
        atr_period=atr_period,
        atr_mult=atr_mult,
        trend_window=trend_window,
    )


def run_atr_strategy(
    variety="", keltner_period=20, keltner_mult=2.0, start_date="2025-01-01", end_date=""
):
    """ATR 通道突破(纯价格):收盘突破 mid±k·ATR 做多/做空,回归 mid 离场。

    【功能】纯价格 ATR(肯特纳)通道突破策略薄包装。
    【参数】variety:品种;keltner_period:通道 SMA 周期(默认 20);keltner_mult:
    通道宽度倍数 k(默认 2.0);start_date/end_date:回测日期窗。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "atr",
        False,
        start_date,
        end_date,
        keltner_period=keltner_period,
        keltner_mult=keltner_mult,
    )


def run_atr_sent_strategy(
    variety="",
    keltner_period=20,
    keltner_mult=2.0,
    trend_window=5,
    start_date="2025-01-01",
    end_date="",
):
    """ATR 通道突破(情绪确认):入场经情绪自适应调整。

    【功能】ATR 通道突破的情绪自适应版薄包装。
    【参数】比纯价格版多 trend_window(默认 5)。
    【返回】统一引擎返回的单策略 dict。
    """
    return _run_technical_backtest(
        variety,
        "atr",
        True,
        start_date,
        end_date,
        keltner_period=keltner_period,
        keltner_mult=keltner_mult,
        trend_window=trend_window,
    )


# ═══════════════════════════════════════════════════════════════════
# Time-Series Momentum (Moskowitz et al. 2012)
# ═══════════════════════════════════════════════════════════════════


def run_momentum_strategy(variety="", lookback=5, hold=3, start_date="2025-01-01", end_date=""):
    """Time-series momentum: go long if N-day return > 0, short if < 0, hold M days.
    Re-evaluates every day — much higher frequency than crossover strategies.

    【功能】时间序列动量策略:前 lookback 日收益为正做多、为负做空,持仓 hold 天。
    【参数】variety:品种(空串取前 10 个有数据品种);lookback:回看天数(默认 5);
    hold:持仓天数(默认 3);start_date/end_date:回测日期窗。
    【返回】单策略 dict(strategy="momentum"),含 total_trades/win_rate/total_pnl/
    sharpe_like/max_drawdown_pct/lookback/hold/advanced_metrics/recent_trades 等;
    无交易返回 {"total_trades": 0}。
    【关键逻辑】每天重估信号(频率远高于均线交叉类):收益 > 0.5% 开多、<-0.5% 开空、
    中间视为无信号。持仓按固定 hold 天离场;窗口末仍有持仓按最后一根 bar 强平。
    """
    all_trades = []
    vlist = [variety] if variety else _get_all_varieties_with_data()[:10]
    for var in vlist:
        p = _load_price(var)
        if not p or len(p.get("prices", [])) < lookback + hold + 5:
            continue
        px = p["prices"]
        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        pos = 0
        entry_px = 0
        entry_d = ""
        entry_i = 0
        last_i = None  # 窗口内最后一根 bar;窗口末仍有持仓时按它强平
        for i in range(lookback, n):
            d = dates[i]
            if d < start_date or (end_date and d > end_date):
                continue
            last_i = i
            ret = (
                (closes[i] - closes[i - lookback]) / closes[i - lookback]
                if closes[i - lookback]
                else 0
            )
            # 0.5% 阈值:超过才视为有动量,避免把微小噪音当信号
            sig = 1 if ret > 0.005 else (-1 if ret < -0.005 else 0)

            # Exit after holding `hold` days
            if pos != 0 and entry_i > 0 and i - entry_i >= hold:
                exit_px = closes[i]
                pnl = (
                    (exit_px - entry_px) / entry_px * 100
                    if pos == 1
                    else (entry_px - exit_px) / entry_px * 100
                )
                all_trades.append(
                    {
                        "variety": var,
                        "entry": entry_d,
                        "exit": d,
                        "direction": "long" if pos == 1 else "short",
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": "momentum",
                    }
                )
                pos = 0

            # Entry
            if pos == 0 and sig != 0:
                pos = sig
                entry_px = closes[i]
                entry_d = d
                entry_i = i

        if pos != 0 and entry_px and last_i is not None:
            # 末尾强平:窗口内最后一根 bar 仍持仓则按它收盘结算(与 donchian 同款逻辑)。
            final = closes[last_i]
            pnl = (
                (final - entry_px) / entry_px * 100
                if pos == 1
                else (entry_px - final) / entry_px * 100
            )
            all_trades.append(
                {
                    "variety": var,
                    "entry": entry_d,
                    "exit": dates[last_i],
                    "direction": "long" if pos == 1 else "short",
                    "pnl": round(pnl, 2),
                    "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                    "signal": "momentum",
                }
            )

    if not all_trades:
        return {"total_trades": 0}
    wins = [t for t in all_trades if t["outcome"] == "win"]
    pnls = [t["pnl"] for t in all_trades]
    avg = sum(pnls) / len(pnls)
    # 这行是遗留代码:计算了标准差但结果被丢弃(未赋值给任何变量),可视为冗余。
    (sum((x - avg) ** 2 for x in pnls) / len(pnls)) ** 0.5 if len(pnls) > 1 else 1
    cum = 0
    peak = 0
    dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    entry_dates = sorted({t["entry"] for t in all_trades})
    td = (
        max(
            (
                datetime.strptime(entry_dates[-1], "%Y-%m-%d")
                - datetime.strptime(entry_dates[0], "%Y-%m-%d")
            ).days
            * 252
            // 365,
            len(pnls) * hold,
        )
        if len(entry_dates) >= 2
        else len(pnls) * hold
    )
    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=td)
    return {
        "strategy": "momentum",
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(all_trades) - len(wins),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg, 2),
        "total_pnl": round(sum(pnls), 2) if pnls else 0,
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "lookback": lookback,
        "hold": hold,
        "advanced_metrics": advanced,
        "recent_trades": all_trades,
    }


# ═══════════════════════════════════════════════════════════════════
# Momentum + Adaptive Sentiment Fusion
# ═══════════════════════════════════════════════════════════════════


def run_momentum_adaptive(
    variety: str = "",
    lookback: int = 5,
    hold: int = 3,
    trend_window: int = 5,
    start_date: str = "2025-01-01",
    end_date: str = "",
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

    【功能】纯动量 + 情绪自适应的融合策略,并带"基线质量门控"。
    【参数】variety:品种(空串取前 10 个有数据品种);lookback:动量回看天数(默认 5);
    hold:持仓天数(默认 3);trend_window:趋势窗口(默认 5);start_date/end_date:回测日期窗。
    【返回】dict,含 strategy / adaptive / momentum_baseline / decisions / dates /
    curves / variety / lookback / hold / trend_window;无信号时返回 {"total_trades": 0,...}。
    【关键逻辑】
      1. 两条路径并行:adaptive(动量+自适应覆盖)与 momentum_baseline(纯动量基线,
         等价于 run_momentum_strategy),输出天然可比。
      2. 决策树(逐日):
         - 涨+看空(Type A):恒逆向做空(经验 60% 准确);
         - 跌+看多(Type B):若基线盈利则跟随动量(quality_skip),若强跌>3% 跳过(接飞刀),
           否则逆向做多;
         - 强趋势(>2%):纯动量;弱/中趋势:基线健康就动量,否则逆向(逆情绪)。
      3. 质量门控:维护最近 10 笔已平仓基线交易(recent_total>0 且胜率≥45%)才算
         "基线健康",只有健康时才允许在分歧/弱趋势时信任动量。
    """
    all_adaptive = []  # Momentum + adaptive overlay
    all_baseline = []  # Pure momentum baseline
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
        last_s = 0
        si = 0
        for d in sorted(str(x["date"])[:10] for x in px):
            while si < len(sd_sorted) and sd_sorted[si] <= d:
                last_s = raw_sent[sd_sorted[si]]
                si += 1
            sent_map[d] = last_s

        closes = [float(x["close"]) for x in px]
        dates = [str(x["date"])[:10] for x in px]
        n = len(closes)

        pos_a = 0
        entry_px_a = 0
        entry_d_a = ""
        entry_i_a = 0  # adaptive
        pos_b = 0
        entry_px_b = 0
        entry_d_b = ""
        entry_i_b = 0  # baseline

        # Rolling baseline quality tracker (last N closed trades)
        baseline_recent_pnls = []  # stores PnL of last 10 closed baseline trades
        BASELINE_WINDOW = 10
        last_i = None  # 窗口内最后一根 bar;窗口末仍有持仓时按它强平

        for i in range(max(lookback, trend_window), n):
            d = dates[i]
            if d < start_date or (end_date and d > end_date):
                continue
            last_i = i
            ss = sent_map.get(d, 0)
            px_now = closes[i]

            # Pure momentum signal (lookback return)
            mom_ret = (
                (closes[i] - closes[i - lookback]) / closes[i - lookback]
                if closes[i - lookback]
                else 0
            )
            mom_sig = 1 if mom_ret > 0.005 else (-1 if mom_ret < -0.005 else 0)

            # Trend and divergence
            trend = (
                (closes[i] - closes[i - trend_window]) / closes[i - trend_window]
                if closes[i - trend_window]
                else 0
            )
            trend_pct = abs(trend) * 100

            diverge_bearish = trend > 0.01 and ss < -0.1  # price up + bearish = top signal
            diverge_bullish = trend < -0.01 and ss > 0.1  # price down + bullish = bottom signal

            # ── Baseline quality check ──
            # 基线质量门控:只有最近 5 笔以上基线交易的"累计盈亏>0 且胜率≥45%"才视为
            # 健康。健康时更愿意跟随动量(因为动量最近在赚钱);不健康时更容易逆情绪。
            baseline_is_good = False
            if len(baseline_recent_pnls) >= 5:
                recent_wr = sum(1 for p in baseline_recent_pnls if p > 0.15) / len(
                    baseline_recent_pnls
                )
                recent_total = sum(baseline_recent_pnls)
                baseline_is_good = recent_total > 0 and recent_wr >= 0.45

            # ── Adaptive decision (quality-gated) ──
            if diverge_bearish:
                # Type A (涨+看空): always contrarian — empirically 60% accurate
                a_long = False
                a_short = True
                decisions["contrarian"] += 1
            elif diverge_bullish:
                if baseline_is_good:
                    # Baseline is profitable → trust it, don't fade
                    a_long = mom_sig == 1
                    a_short = mom_sig == -1
                    decisions["quality_skip"] += 1
                elif trend_pct > 3:
                    # Strong crash + bullish + baseline losing = denial → skip
                    a_long = False
                    a_short = False
                    decisions["skipped"] += 1
                else:
                    # Moderate dip + bullish + baseline losing → contrarian LONG
                    a_long = True
                    a_short = False
                    decisions["contrarian"] += 1
            elif trend_pct > 2:
                # Strong trend: always pure momentum
                a_long = mom_sig == 1
                a_short = mom_sig == -1
                decisions["momentum"] += 1
            elif baseline_is_good:
                # Weak/moderate trend + baseline healthy → stick to momentum
                a_long = mom_sig == 1
                a_short = mom_sig == -1
                decisions["momentum"] += 1
            else:
                # Weak/moderate trend + baseline struggling → contrarian (fade sentiment)
                a_long = ss < -0.1
                a_short = ss > 0.1
                decisions["contrarian"] += 1

            # ── Baseline: pure momentum only ──
            b_long = mom_sig == 1
            b_short = mom_sig == -1

            # Exit after holding `hold` days
            exit_idx = min(i + hold, n - 1)
            exit_px = closes[exit_idx]

            def _close(
                pos,
                entry_px,
                entry_d,
                entry_i,
                px_now,
                d,
                direction,
                trades_list,
                label,
                variety=var,  # 用默认参把 var 固化,避免闭包在循环结束时拿到最后的品种(B023 修复)
            ):
                """按当前价格 px_now 平掉一笔持仓,并把交易记录追加到 trades_list。

                多单盈亏 = (平仓-入场)/入场,空单反向;outcome 按 ±0.15% 分档。
                """
                if pos == 1:
                    pnl = (px_now - entry_px) / entry_px * 100
                else:
                    pnl = (entry_px - px_now) / entry_px * 100
                trades_list.append(
                    {
                        "variety": variety,
                        "entry": entry_d,
                        "exit": d,
                        "direction": direction,
                        "pnl": round(pnl, 2),
                        "outcome": "win"
                        if pnl > 0.15
                        else ("loss" if pnl < -0.15 else "breakeven"),
                        "signal": label,
                    }
                )

            # Process adaptive
            if pos_a != 0 and entry_d_a and i - entry_i_a >= hold:
                _close(
                    pos_a,
                    entry_px_a,
                    entry_d_a,
                    entry_i_a,
                    exit_px,
                    d,
                    "long" if pos_a == 1 else "short",
                    all_adaptive,
                    "mom_ad",
                )
                pos_a = 0
            if pos_a == 0:
                if a_long:
                    pos_a = 1
                    entry_px_a = px_now
                    entry_d_a = d
                    entry_i_a = i
                elif a_short:
                    pos_a = -1
                    entry_px_a = px_now
                    entry_d_a = d
                    entry_i_a = i

            # Process baseline — also track PnL for quality gating
            if pos_b != 0 and entry_d_b and i - entry_i_b >= hold:
                if pos_b == 1:
                    bl_pnl = (exit_px - entry_px_b) / entry_px_b * 100
                else:
                    bl_pnl = (entry_px_b - exit_px) / entry_px_b * 100
                all_baseline.append(
                    {
                        "variety": var,
                        "entry": entry_d_b,
                        "exit": d,
                        "direction": "long" if pos_b == 1 else "short",
                        "pnl": round(bl_pnl, 2),
                        "outcome": "win"
                        if bl_pnl > 0.15
                        else ("loss" if bl_pnl < -0.15 else "breakeven"),
                        "signal": "momentum",
                    }
                )
                # Track for quality gating
                baseline_recent_pnls.append(bl_pnl)
                if len(baseline_recent_pnls) > BASELINE_WINDOW:
                    baseline_recent_pnls.pop(0)
                pos_b = 0
            if pos_b == 0:
                if b_long:
                    pos_b = 1
                    entry_px_b = px_now
                    entry_d_b = d
                    entry_i_b = i
                elif b_short:
                    pos_b = -1
                    entry_px_b = px_now
                    entry_d_b = d
                    entry_i_b = i

        # Close final positions
        final_px = closes[last_i] if last_i is not None else None
        if pos_a != 0 and entry_px_a and final_px is not None:
            pnl = (
                (final_px - entry_px_a) / entry_px_a * 100
                if pos_a == 1
                else (entry_px_a - final_px) / entry_px_a * 100
            )
            all_adaptive.append(
                {
                    "variety": var,
                    "entry": entry_d_a,
                    "exit": dates[last_i],
                    "direction": "long" if pos_a == 1 else "short",
                    "pnl": round(pnl, 2),
                    "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                    "signal": "mom_ad",
                }
            )
        if pos_b != 0 and entry_px_b and final_px is not None:
            pnl = (
                (final_px - entry_px_b) / entry_px_b * 100
                if pos_b == 1
                else (entry_px_b - final_px) / entry_px_b * 100
            )
            all_baseline.append(
                {
                    "variety": var,
                    "entry": entry_d_b,
                    "exit": dates[last_i],
                    "direction": "long" if pos_b == 1 else "short",
                    "pnl": round(pnl, 2),
                    "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
                    "signal": "momentum",
                }
            )

    if not all_adaptive and not all_baseline:
        return {"total_trades": 0, "message": "No signals"}

    # Build cumulative curves
    all_dates = sorted({t["entry"] for t in all_adaptive + all_baseline})
    a_map = {}
    b_map = {}
    for t in all_adaptive:
        a_map[t["entry"]] = a_map.get(t["entry"], 0) + t["pnl"]
    for t in all_baseline:
        b_map[t["entry"]] = b_map.get(t["entry"], 0) + t["pnl"]

    a_curve = []
    b_curve = []
    cum_a = 0
    cum_b = 0
    for d in all_dates:
        cum_a += a_map.get(d, 0)
        cum_b += b_map.get(d, 0)
        a_curve.append(round(cum_a, 2))
        b_curve.append(round(cum_b, 2))

    def wr(ts):
        """胜率:win 笔数 / 总笔数;空列表返回 0。"""
        return round(sum(1 for t in ts if t["outcome"] == "win") / len(ts), 3) if ts else 0

    # Compute advanced metrics per sub-strategy
    a_pnls = [t["pnl"] for t in all_adaptive]
    b_pnls = [t["pnl"] for t in all_baseline]
    all_eds = sorted({t["entry"] for t in all_adaptive + all_baseline})
    td_ma = (
        max(
            (
                datetime.strptime(all_eds[-1], "%Y-%m-%d")
                - datetime.strptime(all_eds[0], "%Y-%m-%d")
            ).days
            * 252
            // 365,
            20,
        )
        if len(all_eds) >= 2
        else 252
    )
    adv_a = compute_advanced_metrics(trade_pnls=a_pnls, total_trading_days=td_ma) if a_pnls else {}
    adv_b = compute_advanced_metrics(trade_pnls=b_pnls, total_trading_days=td_ma) if b_pnls else {}

    return {
        "strategy": "momentum_adaptive",
        "adaptive": {
            "trades": len(all_adaptive),
            "win_rate": wr(all_adaptive),
            "total_pnl": round(a_curve[-1], 2) if a_curve else 0,
            "label": "动量+自适应",
            "advanced_metrics": adv_a,
        },
        "momentum_baseline": {
            "trades": len(all_baseline),
            "win_rate": wr(all_baseline),
            "total_pnl": round(b_curve[-1], 2) if b_curve else 0,
            "label": "纯动量(基线)",
            "advanced_metrics": adv_b,
        },
        "decisions": decisions,
        "dates": all_dates,
        "curves": {"adaptive": a_curve, "momentum_baseline": b_curve},
        "variety": variety or "all",
        "lookback": lookback,
        "hold": hold,
        "trend_window": trend_window,
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

    【功能】对任意策略产出的交易列表施加风控:固定止损、移动止损、最长持仓限制。
    【参数】trades:交易 dict 列表(需含 entry/exit/direction 键);prices:OHLCV 行情
    (用于逐日检查是否触发止损);stop_loss_pct:固定止损%(0=关闭);trailing_stop_pct:
    移动止损%(0=关闭);max_holding:最长持仓天数(20)。
    【返回】新的交易列表。被止损的交易会生成 dict 副本,改写 pnl/exit/outcome,并加
    stopped_out=True;未触发的原样保留。
    【关键逻辑】
      - 先按日期把 close 建成查表,再对每笔交易在 [entry, exit] 区间逐日检查。
      - 多单:价格跌破 entry*(1-止损%)→ 止损;否则更新峰值,跌破 peak*(1-移动%)
        → 止损;移动止损下沿不低于 entry(保护不亏本)。
      - 空单逻辑完全镜像(上下颠倒)。
      - ⚠️ 被止损的订单用"触发止损那天的价格"(stop_px)重算 pnl,而不是沿用原始
        离场价格——这是风控路径与原始路径口径统一的关键,避免盈亏失真。
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
        price_map.get(exit_d, entry_px)  # 遗留无效果行:取值后未使用,可忽略
        # 只看 [入场日, 原离场日] 之间的交易日,确认止损是否在原始离场前触发
        dates = sorted([d for d in price_map if entry_d <= d <= exit_d])
        peak_px = entry_px
        stopped_out = False
        stop_px = 0

        for d in dates:
            px = price_map[d]
            if direction == "long":
                # Fixed stop: exit if price drops below entry * (1 - stop_loss%)
                if stop_loss_pct and px <= entry_px * (1 - stop_loss_pct / 100):
                    stopped_out = True
                    stop_px = px
                    exit_d = d
                    break
                # Trailing stop: update peak, exit if falls below peak * (1 - trailing%)
                if px > peak_px:
                    peak_px = px
                trail_level = peak_px * (1 - trailing_stop_pct / 100)
                # Never let trailing stop go below entry (protect breakeven)
                trail_level = max(trail_level, entry_px)
                if trailing_stop_pct and px <= trail_level:
                    stopped_out = True
                    stop_px = px
                    exit_d = d
                    break
            else:  # short
                if stop_loss_pct and px >= entry_px * (1 + stop_loss_pct / 100):
                    stopped_out = True
                    stop_px = px
                    exit_d = d
                    break
                if px < peak_px:
                    peak_px = px
                trail_level = peak_px * (1 + trailing_stop_pct / 100)
                # Never let trailing stop go above entry (protect breakeven)
                trail_level = min(trail_level, entry_px)
                if trailing_stop_pct and px >= trail_level:
                    stopped_out = True
                    stop_px = px
                    exit_d = d
                    break

        if stopped_out:
            # ⚠️ 用触发止损的价格(stop_px)重算盈亏,而非原离场价;这样风控后的
            # 每笔交易 PnL 口径与原始路径一致,都是"实际平仓价 - 入场价"。
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
class TradeRecord:
    """一条模拟交易记录的定长数据类(dataclass 自动生成 __init__/__repr__ 等)。

    【功能】用结构化字段描述一笔"已完成的模拟交易",供 run_simulated_trading 与
    run_trailing_strategy 收集结果。
    【字段】variety:品种;entry_date/exit_date:入场/离场日期;direction:long/short;
    signal_value:触发信号的情绪得分;entry_price/exit_price:入场/离场价格;
    pnl_pct:盈亏百分比;outcome:win/loss/breakeven;horizon:实际持仓天数。
    【边界】纯数据容器,无方法;调用方直接构造实例后放入列表。
    """
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

    【功能】从交易盈亏序列计算全套回测指标,是所有策略统计指标的统一出口。
    【参数】trade_pnls:每笔交易的盈亏百分比列表;total_trading_days:回测期估算
    交易日(252≈1 年);daily_returns:可选,精确的日收益序列(用于精确年化波动率)。
    【返回】dict,含 sharpe_ratio/sortino_ratio/calmar_ratio/annualized_return/
    annualized_volatility/volatility_pct/max_drawdown_pct/max_drawdown_duration/
    profit_factor/avg_win_pct/avg_loss_pct/win_loss_ratio/sharpe_like。
    【关键逻辑】
      - 无风险利率 RISK_FREE_RATE=2.5,即中国 10 年期国债年化约 2.5%(2025 年上下
        的经验常数),夏普/索提诺都从年化收益里扣它。
      - 赢/亏分档阈值是 ±0.1%(与交易记录的 ±0.15% 不同!):>0.1 记赢、<-0.1 记亏、
        中间记打平。注意这里的分档比 _trade 的 ±0.15% 更窄,两者口径不一致,是历史
        遗留,【待确认】可能影响 win_loss_ratio 等统计口径。
      - 年化:把总收益按 (1+总收益)^(252/交易日)-1 年化;无日收益时用
        std_pnl * sqrt(每年交易次数) 估算年化波动。
      - 夏普 = (年化收益-无风险)/年化波动;波动为 0 时给 9.99(收益>无风险)或 0。
      - 最大回撤/持续期:沿累计盈亏序列扫描,peak 记历史最高,回撤长度计入
        max_drawdown_duration。
      - sharpe_like 是旧版兼容字段 = 平均盈亏/盈亏标准差,各策略对外展示用。
    """
    n = len(trade_pnls)
    RISK_FREE_RATE = 2.5  # China 10Y government bond ≈ 2.5% annual

    if n == 0:
        return {
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
            "annualized_return": 0.0,
            "annualized_volatility": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_duration": 0,
            "profit_factor": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "win_loss_ratio": 0.0,
            "volatility_pct": 0.0,
            "sharpe_like": 0.0,
        }

    # ── Basic stats ──
    wins = [p for p in trade_pnls if p > 0.1]
    losses = [p for p in trade_pnls if p < -0.1]
    [p for p in trade_pnls if -0.1 <= p <= 0.1]  # 遗留:计算了打平组但结果被丢弃

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    # 盈亏比:平均赢/平均亏;无亏(avg_loss==0)时按是否盈利给 99 或 0(避免除零)
    win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else (99.0 if avg_win > 0 else 0.0)

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (
        round(gross_profit / gross_loss, 2)
        if gross_loss > 0
        else (99.0 if gross_profit > 0 else 0.0)
    )

    total_return_pct = sum(trade_pnls)
    avg_pnl = total_return_pct / n
    std_pnl = math.sqrt(sum((p - avg_pnl) ** 2 for p in trade_pnls) / n) if n > 1 else 0.0

    # ── Annualization ──
    total_trading_days = max(total_trading_days, 1)
    total_return_decimal = total_return_pct / 100.0

    # 把回测期总收益年化到 252 个交易日;总收益若已亏到 -100% 以下(不可能但防御),
    # 直接记 -100%(全损)。
    if total_return_decimal > -1.0:
        annualized_return = ((1 + total_return_decimal) ** (252.0 / total_trading_days) - 1) * 100
    else:
        annualized_return = -100.0  # total loss

    # Trades per year for scaling trade-level std → annual
    trades_per_year = n * 252.0 / total_trading_days if total_trading_days > 0 else n

    # Volatility: use daily returns if available, otherwise scale from trade PnLs
    if daily_returns and len(daily_returns) > 1:
        daily_std = math.sqrt(
            sum((r - sum(daily_returns) / len(daily_returns)) ** 2 for r in daily_returns)
            / len(daily_returns)
        )
        annualized_vol = daily_std * math.sqrt(252) * 100  # convert to %
    else:
        annualized_vol = std_pnl * math.sqrt(trades_per_year) if trades_per_year > 0 else 0.0

    # ── Sharpe Ratio ──
    sharpe = (
        (annualized_return - RISK_FREE_RATE) / annualized_vol
        if annualized_vol > 0
        else (9.99 if annualized_return > RISK_FREE_RATE else 0.0)
    )

    # ── Sortino Ratio (downside deviation only) ──
    downside_returns = [p for p in trade_pnls if p < 0]
    if downside_returns:
        downside_var = sum(p**2 for p in downside_returns) / len(trade_pnls)
        downside_std_trade = math.sqrt(downside_var)
        downside_std_annual = (
            downside_std_trade * math.sqrt(trades_per_year) if trades_per_year > 0 else 0.0
        )
        sortino = (
            (annualized_return - RISK_FREE_RATE) / downside_std_annual
            if downside_std_annual > 0
            else (9.99 if annualized_return > RISK_FREE_RATE else 0.0)
        )
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
    calmar = annualized_return / max_dd if max_dd > 0 else (99.0 if annualized_return > 0 else 0.0)

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
    end_date: str = "",
) -> dict:
    """Simulated trading based on sentiment signals.

    Rules:
    - If avg_score > +threshold at day T → go LONG at T+1 open, exit at T+horizon close
    - If avg_score < -threshold at day T → go SHORT at T+1 open, exit at T+horizon close
    - Track all trades and compute win rate, avg return, Sharpe, max drawdown.

    【功能】基于纯情绪得分的模拟交易回测(固定周期持仓)。
    【参数】variety:品种(空串遍历全部有数据品种);horizon:持仓天数(默认 3);
    signal_threshold:情绪触发阈值(默认 0.2);start_date/end_date:回测日期窗。
    【返回】dict,含 total_trades/win_count/loss_count/win_rate/avg_pnl_pct/
    total_pnl/sharpe_like/max_drawdown_pct/long_trades/short_trades/
    long_win_rate/short_win_rate/horizon/signal_threshold/by_variety/
    advanced_metrics/recent_trades;无交易返回 {"total_trades": 0, ...}。
    【关键逻辑】在"情绪与价格都有的交易日"上:avg_score > +阈值 当日信号做多、
    < -阈值 做空,horizon 天后按那天的收盘价平仓(PnL 按入场/离场收盘价算)。
    注意这里"入场日=信号日",与 docstring 说的 T+1 略有出入,实际按当日价格成交
    【待确认】;每条交易存成 TradeRecord 收集,最后按品种统计 win_rate。
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
            if d in price_map and d >= start_date and (not end_date or d <= end_date):
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

            all_trades.append(
                TradeRecord(
                    variety=var,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    direction=direction,
                    signal_value=round(signal, 3),
                    entry_price=round(entry_px, 2),
                    exit_price=round(exit_px, 2),
                    pnl_pct=round(pnl, 2),
                    outcome=outcome,
                    horizon=(dates.index(exit_date) - dates.index(entry_date)),
                )
            )

    if not all_trades:
        return {"total_trades": 0, "message": "No trades generated"}

    # Compute statistics
    wins = [t for t in all_trades if t.outcome == "win"]
    losses = [t for t in all_trades if t.outcome == "loss"]
    long_trades = [t for t in all_trades if t.direction == "long"]
    short_trades = [t for t in all_trades if t.direction == "short"]

    pnls = [t.pnl_pct for t in all_trades]

    # Estimate trading days from trade date range
    # 用首末入场日的自然日天数 × 252/365 折算成交易日;样本过少时给至少
    # len(pnls)*horizon 天(或 20 天),避免年化指标被极端放大。
    all_entry_dates = sorted({t.entry_date for t in all_trades})
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
        by_variety[v]["win_rate"] = (
            round(by_variety[v]["wins"] / by_variety[v]["trades"], 3)
            if by_variety[v]["trades"]
            else 0
        )

    return {
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "total_pnl": round(sum(pnls), 2) if pnls else 0,
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(
            len([t for t in long_trades if t.outcome == "win"]) / len(long_trades), 3
        )
        if long_trades
        else 0,
        "short_win_rate": round(
            len([t for t in short_trades if t.outcome == "win"]) / len(short_trades), 3
        )
        if short_trades
        else 0,
        "horizon": horizon,
        "signal_threshold": signal_threshold,
        "by_variety": dict(by_variety),
        "advanced_metrics": advanced,
        "recent_trades": [
            {
                "variety": t.variety,
                "entry": t.entry_date,
                "exit": t.exit_date,
                "dir": t.direction,
                "pnl": t.pnl_pct,
                "outcome": t.outcome,
            }
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

    【功能】从 OHLCV 行情计算"基本面/技术"因子序列(实际是纯价格+量能因子)。
    【参数】prices:OHLCV dict 列表(需含 close,可含 volume)。
    【返回】dict,各键为与 prices 等长的列表:ma_5/ma_20/ma_signal/momentum_5/
    vol_ratio/vol_signal/fund_score。
    【关键逻辑】
      - 数据不足窗口时用当日 close 顶上(ma_5/ma_20),保证数组等长、不出现 None。
      - vol_ratio 无成交量为 1(中性);vol_signal 按 1.2/0.8 分活跃/冷清。
      - fund_score = 0.6*ma_signal + 0.25*momentum(归一化到 [-1,1],5% 收益记 1)
        + 0.15*vol_signal,合成在 [-1,1],供 run_strategy_comparison 用阈值开仓。
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
            ma_5.append(sum(closes[i - 4 : i + 1]) / 5)
        else:
            ma_5.append(closes[i])
        if i >= 19:
            ma_20.append(sum(closes[i - 19 : i + 1]) / 20)
        else:
            ma_20.append(closes[i])

        # MA signal
        ma_signal.append(1.0 if closes[i] > ma_20[i] else -1.0)

        # Momentum
        if i >= 5:
            momentum_5.append((closes[i] - closes[i - 5]) / closes[i - 5] if closes[i - 5] else 0)
        else:
            momentum_5.append(0.0)

        # Volume
        if volumes[i] > 0:
            if i >= 20:
                avg_vol = sum(volumes[i - 19 : i + 1]) / 20
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
        "ma_5": ma_5,
        "ma_20": ma_20,
        "ma_signal": ma_signal,
        "momentum_5": momentum_5,
        "vol_ratio": vol_ratio,
        "vol_signal": vol_signal,
        "fund_score": fund_score,
    }


# ═══════════════════════════════════════════════════════════════════
# P3-C: Multi-Strategy Comparison (Fundamental / Sentiment / Combined)
# ═══════════════════════════════════════════════════════════════════


def run_strategy_comparison(
    variety: str = "RB",
    horizon: int = 5,
    signal_threshold: float = 0.2,
    fund_threshold: float = 0.3,
    start_date: str = "2025-01-01",
    end_date: str = "",
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

    【功能】多策略对比:同时跑"纯基本面"与"基本面+情绪"两个策略并叠加基准(价格曲线)。
    【参数】variety:品种(默认 "RB");horizon:持仓天数(默认 5);signal_threshold:
    情绪阈值(默认 0.2);fund_threshold:基本面得分阈值(默认 0.3);start_date/end_date:
    回测日期窗。
    【返回】dict,含 variety/horizon/fund_threshold/sent_threshold/dates/curves/
    stats/recent_trades_fund/recent_trades_combo;数据不足返回 {"error": ...}。
    【关键逻辑】
      - 因子来自 _compute_fundamental_factors;情绪用 _load_trends 的 avg_score。
      - 日期只取"价格与情绪都有的重叠日",避免缺数据导致信号失真。
      - 策略1(纯基本面):|fund_score|>阈值 就开仓;策略2(基本面+情绪):两者方向
        必须一致才开仓;两者都用固定 horizon 天离场。
      - 曲线部分:build_pnl_curve 里有一段 while...pass 的死代码(基本无效),实际
        用的是下面"date-indexed cumulative PnL"的 map 累加方式构建 fund/combo 曲线,
        price 曲线是买入持有基准。
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
        if d in sent_map and d >= start_date and (not end_date or d <= end_date):
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
        all_trades_fund.append(
            {
                "date": dates[i],
                "direction": direction,
                "pnl": round(pnl, 2),
                "outcome": outcome,
            }
        )

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
        all_trades_combo.append(
            {
                "date": dates[i],
                "direction": direction,
                "pnl": round(pnl, 2),
                "outcome": outcome,
            }
        )

    # --- Build PnL curves (cumulative) ---
    def build_pnl_curve(trades: list, n_days: int, start_idx: int) -> list[float]:
        """Map trades to daily cumulative PnL curve."""
        # 注意:这个函数实际并未被调用(下方用 date-indexed map 构建曲线)。
        # 开头 while...pass 循环是死代码(不产生任何副作用),真正生效的是
        # 后面"simpler approach"按 trade 序列累加的 cur_curve。保留原因【待确认】,
        # 可能是历史遗留,阅读时可直接跳过 while 部分。
        [0.0] * n_days
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
        """计算某策略交易列表的胜率;空列表返回 0。"""
        if not trades:
            return 0
        return round(sum(1 for t in trades if t["outcome"] == "win") / len(trades), 3)

    # Compute advanced metrics per strategy
    sc_td = max(len(dates) * 252 // 365, 30) if dates else 252
    fund_pnls = [t["pnl"] for t in all_trades_fund]
    combo_pnls = [t["pnl"] for t in all_trades_combo]
    adv_fund = (
        compute_advanced_metrics(trade_pnls=fund_pnls, total_trading_days=sc_td)
        if fund_pnls
        else {}
    )
    adv_combo = (
        compute_advanced_metrics(trade_pnls=combo_pnls, total_trading_days=sc_td)
        if combo_pnls
        else {}
    )

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
    end_date: str = "",
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

    【功能】情绪追踪离场策略:入场规则与固定周期版相同,但离场改为"情绪反转就离场,
    否则最长持有 max_holding 天"——模拟"顺着情绪波浪走,大众转向才离场"。
    【参数】variety:品种(空串遍历全部有数据品种);signal_threshold:情绪触发阈值
    (默认 0.2);max_holding:最长持仓天数(默认 10);start_date/end_date:回测日期窗。
    【返回】dict,含 strategy/total_trades/win_count/loss_count/win_rate/avg_pnl_pct/
    total_pnl/sharpe_like/max_drawdown_pct/long_trades/short_trades/long_win_rate/
    short_win_rate/signal_threshold/max_holding/avg_holding_days/flip_exits/
    max_holding_exits/by_variety/advanced_metrics/recent_trades。
    【关键逻辑】逐日推进,情绪超阈值则开仓,然后在之后最多 max_holding 天里找
    "情绪反向翻转"的日期作为离场点(多单看情绪转负、空单看转正);没翻转就持有到
    max_holding 天。离场后跳到最后离场日的下一天,保证交易不重叠。flip_exits 统计
    因情绪翻转而离场的笔数(horizon<max_holding 近似)。
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
            if d in price_map and d >= start_date and (not end_date or d <= end_date):
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

            # 在之后最多 max_holding 天里找"情绪反转"日:多单等情绪转负、空单等转正。
            for j in range(i + 1, min(i + max_holding + 1, len(dates))):
                later_score = scores[j]
                later_date = dates[j]

                # Check sentiment reversal
                if (
                    direction == "long"
                    and later_score < 0
                    or direction == "short"
                    and later_score > 0
                ):
                    exit_date = later_date
                    break

            # If no flip within max_holding, exit at max_holding day
            if exit_date is None:
                exit_idx = min(i + max_holding, len(dates) - 1)
                exit_date = dates[exit_idx]

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

            all_trades.append(
                TradeRecord(
                    variety=var,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    direction=direction,
                    signal_value=round(signal, 3),
                    entry_price=round(entry_px, 2),
                    exit_price=round(exit_px, 2),
                    pnl_pct=round(pnl, 2),
                    outcome=outcome,
                    horizon=holding_days,
                )
            )

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
    # 与 run_simulated_trading 同款估算:自然日天数折算成交易日;样本过少时用
    # len(pnls)*max_holding 兜底,保证年化指标分母不会太小。
    all_entry_dates = sorted({t.entry_date for t in all_trades})
    if len(all_entry_dates) >= 2:
        d0 = datetime.strptime(all_entry_dates[0], "%Y-%m-%d")
        d1 = datetime.strptime(all_entry_dates[-1], "%Y-%m-%d")
        trading_days = max((d1 - d0).days * 252 // 365, len(pnls))
    else:
        trading_days = len(pnls) * max_holding

    advanced = compute_advanced_metrics(trade_pnls=pnls, total_trading_days=trading_days)
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0

    # Exit reason stats
    flip_exits = [t for t in all_trades if getattr(t, "horizon", 0) < max_holding]
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
    avg_holding = (
        round(sum(t.horizon for t in all_trades) / len(all_trades), 1) if all_trades else 0
    )

    return {
        "strategy": "trailing_sentiment",
        "total_trades": len(all_trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "total_pnl": round(sum(pnls), 2) if pnls else 0,
        "sharpe_like": advanced["sharpe_like"],
        "max_drawdown_pct": advanced["max_drawdown_pct"],
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(
            len([t for t in long_trades if t.outcome == "win"]) / len(long_trades), 3
        )
        if long_trades
        else 0,
        "short_win_rate": round(
            len([t for t in short_trades if t.outcome == "win"]) / len(short_trades), 3
        )
        if short_trades
        else 0,
        "signal_threshold": signal_threshold,
        "max_holding": max_holding,
        "avg_holding_days": avg_holding,
        "flip_exits": flip_count,
        "max_holding_exits": len(all_trades) - flip_count,
        "by_variety": dict(by_variety),
        "advanced_metrics": advanced,
        "recent_trades": [
            {
                "variety": t.variety,
                "entry": t.entry_date,
                "exit": t.exit_date,
                "dir": t.direction,
                "pnl": t.pnl_pct,
                "outcome": t.outcome,
                "days": t.horizon,
            }
            for t in all_trades
        ],
    }


# ── "今日操作"信号 ──────────────────────────────────────────────────────────


def _build_forward_filled_sent_map(
    sent_data: dict | None, price_dates: list[str]
) -> dict[str, float]:
    """按价格日期向前填充情绪评分(forward-fill)。

    镜像各回测函数(run_contrarian_sentiment / run_adaptive_sentiment /
    run_momentum_adaptive 中 "Build forward-filled sentiment" 段)的填充逻辑:
    取 <= 当日的最新情绪评分作为该日情绪。该工具函数把这段重复逻辑收敛到一处,
    供最新信号与回测共用,保证口径一致。
    """
    if not sent_data:
        return {}
    series = sent_data.get("data", {}).get("daily_series", sent_data.get("series", []))
    raw = {str(s.get("date", ""))[:10]: float(s.get("avg_score", 0)) for s in series}
    sd_sorted = sorted(raw)
    out: dict[str, float] = {}
    # 双指针:sd_sorted 已升序,对每个价格日期 d 把 <=d 的最新情绪逐步推进给 last_s,
    # 得到"当日能看到的最近一次情绪",即前向填充(不偷看未来)。
    last_s = 0.0
    si = 0
    for d in price_dates:
        while si < len(sd_sorted) and sd_sorted[si] <= d:
            last_s = raw[sd_sorted[si]]
            si += 1
        out[d] = last_s
    return out


def _make_signal(
    variety: str, date: str, action: str, strength: float, reason: str, scale: float = 1.0
) -> dict:
    """构建"今日操作"信号 dict。action 取值:buy(买入)/sell(卖出)/hold(不动)。

    ``strength`` 是各策略的原生信号幅度(情绪得分 / 收益率小数 / 突破幅度),
    量纲各不相同,不可跨策略比较。``strength_pct`` 将其归一化到 [0,1]:
    情绪/基本面得分 scale=1.0(得分本身 ≈ [-1,1]);收益率/突破幅度 scale=0.05
    (回看累计 5% 视为极强)。截断到 1.0,保证跨策略同一标尺。
    """
    return {
        "date": date,
        "variety": variety,
        "action": action,
        "strength": round(strength, 3),
        # strength_pct = min(|strength|/scale, 1.0):把各策略不同量纲的强度归一化到 [0,1],
        # 跨策略可比,前端据此画强度条。
        "strength_pct": round(min(abs(strength) / scale, 1.0), 3),
        "reason": reason,
    }


def latest_trading_signal(
    strategy: str,
    variety: str = "",
    horizon: int = 3,
    signal_threshold: float = 0.2,
    trend_window: int = 5,
    lookback: int = 5,
    hold: int = 3,
    period: int = 20,
    fund_threshold: float = 0.3,
    max_holding: int = 10,
    fast: int = 10,
    slow: int = 30,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    rsi_period: int = 14,
    rsi_overbought: float = 70,
    rsi_oversold: float = 30,
    bb_period: int = 20,
    num_std: float = 2.0,
    turtle_entry: int = 20,
    turtle_exit: int = 10,
    atr_period: int = 14,
    atr_mult: float = 2.0,
    keltner_period: int = 20,
    keltner_mult: float = 2.0,
) -> dict | None:
    """计算策略在数据最新交易日(而非用户选择的 end_date)的买入/卖出/不动信号。

    "今天" = 该品种价格数据最后一条记录的日期(prices[-1]["date"]),与回测的
    start_date/end_date 无关——回看窗需要 today 之前的完整历史。

    返回 None:variety 为空、无价格数据、或数据不足(少于该策略所需回看窗)。
    有信号时返回 {"today_signal": <主信号>, "today_signals": {子键: 信号, ...}}:
      - 单策略(fixed/trailing/momentum/donchian):today_signals 为空 dict。
      - 多子策略:today_signals 键与前端 subs 遍历键一致
        (contrarian/adaptive_sent → {主, consensus};momentum_ad → {adaptive, momentum_baseline};
         compare → {fund_only, fund_plus_sentiment});today_signal = today_signals[主键]。
    每个信号含 strength(策略原生幅度,不可跨策略比较)与 strength_pct(归一化到 [0,1],
    跨策略可比):情绪/基本面得分按 [-1,1] 计,收益率/突破幅度按 5% 封顶。

    注意:fixed/trailing/compare 的回测对情绪做"精确日期对齐"(只取价格与情绪都有的
    交易日),而本函数的 today 信号将情绪向前填充到 today——这是有意行为,以获得
    "今天"可执行的答案。
    """
    if not variety:
        return None
    price_data = _load_price(variety)
    if not price_data or not price_data.get("prices"):
        return None
    px = price_data["prices"]
    closes = [float(x["close"]) for x in px]
    highs = [float(x["high"]) for x in px]
    lows = [float(x["low"]) for x in px]
    dates = [str(x["date"])[:10] for x in px]
    n = len(closes)
    if n == 0:
        return None
    # "今天" = 该品种价格数据最后一条的日期。注意这里不看用户传入的 end_date,
    # 因为"今日操作"要回答的是当前最新交易日,而不是某个历史回测窗口的最后一天。
    today = dates[-1]
    i = n - 1

    # 情绪加载(按各策略原样选择)并向前填充到 today
    if strategy in ("fixed", "trailing"):
        sent_data = _load_sentiment(variety)
    elif strategy in ("contrarian", "adaptive_sent", "momentum_ad"):
        sent_data = _load_trends(variety) or _load_sentiment(variety)
    elif strategy == "compare":
        sent_data = _load_trends(variety)
    else:
        sent_data = None
    sent_map = _build_forward_filled_sent_map(sent_data, dates)
    ss = sent_map.get(today, 0.0)

    # ── 技术策略(MA cross / MACD / RSI / Bollinger / Turtle / ATR)──
    if strategy in TECH_KEYS:
        return _latest_technical_signal(
            strategy,
            variety,
            closes,
            highs,
            lows,
            dates,
            today,
            i,
            n,
            fast=fast,
            slow=slow,
            macd_fast=macd_fast,
            macd_slow=macd_slow,
            macd_signal=macd_signal,
            rsi_period=rsi_period,
            rsi_overbought=rsi_overbought,
            rsi_oversold=rsi_oversold,
            bb_period=bb_period,
            num_std=num_std,
            turtle_entry=turtle_entry,
            turtle_exit=turtle_exit,
            atr_period=atr_period,
            atr_mult=atr_mult,
            keltner_period=keltner_period,
            keltner_mult=keltner_mult,
            trend_window=trend_window,
        )

    # ── fixed / trailing:纯情绪阈值 ──
    if strategy in ("fixed", "trailing"):
        if n < 20 or not sent_data:
            return None
        if ss > signal_threshold:
            sig = _make_signal(
                variety, today, "buy", abs(ss), f"情绪评分 {ss:.2f} > 阈值 {signal_threshold:.2f}"
            )
        elif ss < -signal_threshold:
            sig = _make_signal(
                variety, today, "sell", abs(ss), f"情绪评分 {ss:.2f} < -阈值 {signal_threshold:.2f}"
            )
        else:
            sig = _make_signal(
                variety,
                today,
                "hold",
                abs(ss),
                f"情绪评分 {ss:.2f} 在阈值 ±{signal_threshold:.2f} 内",
            )
        return {"today_signal": sig, "today_signals": {}}

    # ── momentum:回看收益 ──
    if strategy == "momentum":
        if n < lookback + 1:
            return None
        mom_ret = (
            (closes[i] - closes[i - lookback]) / closes[i - lookback] if closes[i - lookback] else 0
        )
        pct = mom_ret * 100
        if mom_ret > 0.005:
            sig = _make_signal(
                variety,
                today,
                "buy",
                abs(mom_ret),
                f"回看{lookback}日收益 {pct:.2f}% > 0.5%",
                scale=0.05,
            )
        elif mom_ret < -0.005:
            sig = _make_signal(
                variety,
                today,
                "sell",
                abs(mom_ret),
                f"回看{lookback}日收益 {pct:.2f}% < -0.5%",
                scale=0.05,
            )
        else:
            sig = _make_signal(
                variety,
                today,
                "hold",
                abs(mom_ret),
                f"回看{lookback}日收益 {pct:.2f}%,动量不足",
                scale=0.05,
            )
        return {"today_signal": sig, "today_signals": {}}

    # ── donchian:通道突破 ──
    if strategy == "donchian":
        if n < period + 1:
            return None
        hh = max(highs[i - period : i])
        ll = min(lows[i - period : i])
        if closes[i] > hh:
            sig = _make_signal(
                variety,
                today,
                "buy",
                (closes[i] - hh) / hh,
                f"突破{period}日高点 {hh:.2f}",
                scale=0.05,
            )
        elif closes[i] < ll:
            sig = _make_signal(
                variety,
                today,
                "sell",
                (ll - closes[i]) / ll,
                f"跌破{period}日低点 {ll:.2f}",
                scale=0.05,
            )
        else:
            sig = _make_signal(variety, today, "hold", 0.0, f"价格在{period}日通道内")
        return {"today_signal": sig, "today_signals": {}}

    # ── contrarian:主=逆情绪,子=情绪共识(顺势) ──
    if strategy == "contrarian":
        if n < trend_window + 1:
            return None
        trend = (
            (closes[i] - closes[i - trend_window]) / closes[i - trend_window]
            if closes[i - trend_window]
            else 0
        )
        if trend < -0.01 and ss > 0.1:
            main = _make_signal(
                variety,
                today,
                "buy",
                abs(ss),
                f"{trend_window}日趋势 {trend * 100:.2f}% 下跌 + 情绪看多 → 逆向买入",
            )
        elif trend > 0.01 and ss < -0.1:
            main = _make_signal(
                variety,
                today,
                "sell",
                abs(ss),
                f"{trend_window}日趋势 {trend * 100:.2f}% 上涨 + 情绪看空 → 逆向卖出",
            )
        else:
            main = _make_signal(variety, today, "hold", abs(ss), "趋势与情绪未背离,观望")
        if trend > 0.01 and ss > 0.1:
            sub = _make_signal(variety, today, "buy", abs(ss), "趋势与情绪同向看多")
        elif trend < -0.01 and ss < -0.1:
            sub = _make_signal(variety, today, "sell", abs(ss), "趋势与情绪同向看空")
        else:
            sub = _make_signal(variety, today, "hold", abs(ss), "趋势与情绪未同向")
        return {"today_signal": main, "today_signals": {"contrarian": main, "consensus": sub}}

    # ── adaptive_sent:自适应选择顺势/逆势 ──
    if strategy == "adaptive_sent":
        if n < trend_window + 1:
            return None
        trend = (
            (closes[i] - closes[i - trend_window]) / closes[i - trend_window]
            if closes[i - trend_window]
            else 0
        )
        trend_pct = abs(trend) * 100
        diverge_bearish = trend > 0.01 and ss < -0.1
        diverge_bullish = trend < -0.01 and ss > 0.1
        if diverge_bearish:
            main = _make_signal(variety, today, "sell", abs(ss), "价格上涨 + 情绪看空(背离)→ 卖出")
        elif diverge_bullish:
            if trend_pct > 3:
                main = _make_signal(
                    variety,
                    today,
                    "hold",
                    abs(ss),
                    f"强跌 {trend_pct:.1f}% + 情绪看多(接飞刀)→ 观望",
                )
            else:
                main = _make_signal(variety, today, "buy", abs(ss), "回调 + 情绪看多 → 逆向买入")
        elif trend_pct > 2:
            if trend > 0.01 and ss > 0.1:
                main = _make_signal(
                    variety, today, "buy", abs(ss), f"强趋势 {trend_pct:.1f}% + 看多 → 顺势买入"
                )
            elif trend < -0.01 and ss < -0.1:
                main = _make_signal(
                    variety, today, "sell", abs(ss), f"强趋势 {trend_pct:.1f}% + 看空 → 顺势卖出"
                )
            else:
                main = _make_signal(
                    variety, today, "hold", abs(ss), f"强趋势 {trend_pct:.1f}% 但情绪中性"
                )
        elif trend_pct < 1:
            if trend < -0.01 and ss > 0.1:
                main = _make_signal(variety, today, "buy", abs(ss), "弱趋势 + 情绪看多 → 逆向买入")
            elif trend > 0.01 and ss < -0.1:
                main = _make_signal(variety, today, "sell", abs(ss), "弱趋势 + 情绪看空 → 逆向卖出")
            else:
                main = _make_signal(variety, today, "hold", abs(ss), "弱趋势 + 情绪中性")
        else:
            if trend > 0.01 and ss > 0.1:
                main = _make_signal(variety, today, "buy", abs(ss), "中等趋势 + 看多 → 顺势买入")
            elif trend < -0.01 and ss < -0.1:
                main = _make_signal(variety, today, "sell", abs(ss), "中等趋势 + 看空 → 顺势卖出")
            else:
                main = _make_signal(variety, today, "hold", abs(ss), "中等趋势但情绪中性")
        if trend > 0.01 and ss > 0.1:
            sub = _make_signal(variety, today, "buy", abs(ss), "趋势与情绪同向看多")
        elif trend < -0.01 and ss < -0.1:
            sub = _make_signal(variety, today, "sell", abs(ss), "趋势与情绪同向看空")
        else:
            sub = _make_signal(variety, today, "hold", abs(ss), "趋势与情绪未同向")
        return {"today_signal": main, "today_signals": {"adaptive": main, "consensus": sub}}

    # ── momentum_ad:质量门控自适应决策 + 基线重放 ──
    if strategy == "momentum_ad":
        if n < max(lookback, trend_window) + 1:
            return None
        # O(n) 纯动量基线重放:复刻 run_momentum_adaptive 里 baseline_is_good 的
        # 运行态(依赖最近 10 笔已平仓基线动量交易的盈亏)。重放窗口按默认回测
        # start_date="2025-01-01"(本函数不接收回测日期,聚焦"今天"视角)。
        baseline_recent_pnls: list[float] = []
        pos_b = 0
        entry_px_b = 0.0
        entry_d_b = ""
        entry_i_b = 0
        for j in range(max(lookback, trend_window), n - 1):
            dj = dates[j]
            if dj < "2025-01-01":
                continue
            b_ret = (
                (closes[j] - closes[j - lookback]) / closes[j - lookback]
                if closes[j - lookback]
                else 0
            )
            b_sig = 1 if b_ret > 0.005 else (-1 if b_ret < -0.005 else 0)
            b_exit_px = closes[min(j + hold, n - 1)]
            if pos_b != 0 and entry_d_b and j - entry_i_b >= hold:
                if pos_b == 1:
                    bl_pnl = (b_exit_px - entry_px_b) / entry_px_b * 100
                else:
                    bl_pnl = (entry_px_b - b_exit_px) / entry_px_b * 100
                baseline_recent_pnls.append(bl_pnl)
                if len(baseline_recent_pnls) > 10:
                    baseline_recent_pnls.pop(0)
                pos_b = 0
            if pos_b == 0:
                if b_sig == 1:
                    pos_b = 1
                    entry_px_b = closes[j]
                    entry_d_b = dj
                    entry_i_b = j
                elif b_sig == -1:
                    pos_b = -1
                    entry_px_b = closes[j]
                    entry_d_b = dj
                    entry_i_b = j
        baseline_is_good = False
        if len(baseline_recent_pnls) >= 5:
            recent_wr = sum(1 for p in baseline_recent_pnls if p > 0.15) / len(baseline_recent_pnls)
            recent_total = sum(baseline_recent_pnls)
            baseline_is_good = recent_total > 0 and recent_wr >= 0.45

        # 今日决策(镜像 run_momentum_adaptive 主循环里的自适应决策树)
        mom_ret = (
            (closes[i] - closes[i - lookback]) / closes[i - lookback] if closes[i - lookback] else 0
        )
        mom_sig = 1 if mom_ret > 0.005 else (-1 if mom_ret < -0.005 else 0)
        trend = (
            (closes[i] - closes[i - trend_window]) / closes[i - trend_window]
            if closes[i - trend_window]
            else 0
        )
        trend_pct = abs(trend) * 100
        diverge_bearish = trend > 0.01 and ss < -0.1
        diverge_bullish = trend < -0.01 and ss > 0.1

        strength_scale = 1.0
        if diverge_bearish:
            action, strength, reason = "sell", abs(ss), "价格上涨 + 情绪看空(背离)→ 卖出"
        elif diverge_bullish:
            if baseline_is_good:
                action = "buy" if mom_sig == 1 else ("sell" if mom_sig == -1 else "hold")
                strength = abs(mom_ret)
                strength_scale = 0.05
                reason = "跌 + 看多但基线盈利 → 跟随动量"
            elif trend_pct > 3:
                action, strength, reason = (
                    "hold",
                    abs(ss),
                    f"强跌 {trend_pct:.1f}% + 看多(接飞刀)→ 观望",
                )
            else:
                action, strength, reason = "buy", abs(ss), "回调 + 情绪看多 → 逆向买入"
        elif trend_pct > 2:
            action = "buy" if mom_sig == 1 else ("sell" if mom_sig == -1 else "hold")
            strength = abs(mom_ret)
            strength_scale = 0.05
            reason = f"强趋势 {trend_pct:.1f}% → 跟随动量"
        elif baseline_is_good:
            action = "buy" if mom_sig == 1 else ("sell" if mom_sig == -1 else "hold")
            strength = abs(mom_ret)
            strength_scale = 0.05
            reason = "弱趋势 + 基线盈利 → 跟随动量"
        else:
            action = "buy" if ss < -0.1 else ("sell" if ss > 0.1 else "hold")
            strength = abs(ss)
            reason = "弱趋势 + 基线亏损 → 逆情绪"
        main = _make_signal(variety, today, action, strength, reason, scale=strength_scale)
        if mom_sig == 1:
            sub = _make_signal(variety, today, "buy", abs(mom_ret), "纯动量基线看多", scale=0.05)
        elif mom_sig == -1:
            sub = _make_signal(variety, today, "sell", abs(mom_ret), "纯动量基线看空", scale=0.05)
        else:
            sub = _make_signal(variety, today, "hold", abs(mom_ret), "纯动量基线无信号", scale=0.05)
        return {"today_signal": main, "today_signals": {"adaptive": main, "momentum_baseline": sub}}

    # ── compare:基本面 / 基本面+情绪 ──
    if strategy == "compare":
        if n < 21 or not sent_data:
            return None
        factors = _compute_fundamental_factors(px)
        fs = factors["fund_score"][i]
        if fs > fund_threshold:
            fund_sig = _make_signal(
                variety, today, "buy", abs(fs), f"基本面得分 {fs:.3f} > 阈值 {fund_threshold:.2f}"
            )
        elif fs < -fund_threshold:
            fund_sig = _make_signal(
                variety, today, "sell", abs(fs), f"基本面得分 {fs:.3f} < -阈值 {fund_threshold:.2f}"
            )
        else:
            fund_sig = _make_signal(
                variety,
                today,
                "hold",
                abs(fs),
                f"基本面得分 {fs:.3f} 在阈值 ±{fund_threshold:.2f} 内",
            )
        if fs > fund_threshold and ss > 0:
            combo_sig = _make_signal(
                variety, today, "buy", abs(fs), f"基本面 {fs:.3f} + 情绪 {ss:.2f} 同向看多"
            )
        elif fs < -fund_threshold and ss < 0:
            combo_sig = _make_signal(
                variety, today, "sell", abs(fs), f"基本面 {fs:.3f} + 情绪 {ss:.2f} 同向看空"
            )
        else:
            combo_sig = _make_signal(
                variety, today, "hold", abs(fs), f"基本面 {fs:.3f} 与情绪 {ss:.2f} 未同向确认"
            )
        return {
            "today_signal": combo_sig,
            "today_signals": {"fund_only": fund_sig, "fund_plus_sentiment": combo_sig},
        }

    return None


def _get_all_varieties_with_data() -> list[str]:
    """Get all varieties that have both sentiment and price data.

    【功能】枚举"至少有一份情绪文件"的全部品种代码(策略循环遍历用)。
    【返回】升序排列的品种代码列表。
    【关键逻辑】只扫 SENTIMENT_DIR 下 *_sentiment.json,用文件名反推代码
    (去掉 _sentiment 后缀)。docstring 说"both sentiment and price",但实现只检查
    情绪文件,是否真有价格文件由各策略后续 _load_price 自行兜底,【待确认】。
    """
    varieties = set()
    for f in SENTIMENT_DIR.glob("*_sentiment.json"):
        varieties.add(f.stem.replace("_sentiment", ""))
    return sorted(varieties)


# ═══════════════════════════════════════════════════════════════════
# Variety Comparison (multi-variety radar)
# ═══════════════════════════════════════════════════════════════════


def compare_varieties(varieties: list[str]) -> list[dict]:
    """Compare sentiment metrics across multiple varieties.

    【功能】多品种情绪对比:为每个品种汇总情绪得分/多空占比/7 日趋势等指标。
    【参数】varieties:待对比的品种代码列表。
    【返回】每品种一个 dict 的列表(含 variety/name/sector/score/label/bullish_ratio/
    bearish_ratio/total_posts/trend_7d/trend_label),按 |score| 降序;无情绪数据的
    品种被跳过。
    【关键逻辑】score 优先取 social_sentiment.overall_score;若为 0 且有 bull/bear
    占比则退回 bull-bear(看多占比-看空占比)。label 为空或 neutral 时按 score 的
    ±0.1 阈值给"偏多/偏空"。
    """
    result = []
    for var in varieties:
        sent = _load_sentiment(var)
        if not sent:
            continue
        ss = sent.get("data", {}).get("social_sentiment", {})
        series = sent.get("data", {}).get("daily_series", [])
        series[-1] if series else {}  # 遗留:取了最新一天但结果未使用

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

        result.append(
            {
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
            }
        )

    result.sort(key=lambda x: -abs(x["score"]))
    return result


def get_all_variety_scores() -> list[dict]:
    """Get sentiment scores for ALL varieties (for ranking dashboard).

    【功能】全部品种情绪得分(排行榜/仪表盘用),无需传品种列表,自动扫文件。
    【返回】每品种一个 dict(含 code/name/sector/score/label/bullish/bearish/posts/
    date),按 |score| 降序。
    【关键逻辑】与 compare_varieties 的 score/label 口径一致(overall_score 为 0 时
    退回 bull-bear),区别只是这里 label 阈值用 ±0.15(更宽),且 bull/bear 转成
    百分比整数(bull*100)供前端画占比条。
    """
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
                label = "偏多"
            elif raw_score < -0.15:
                label = "偏空"

        varieties.append(
            {
                "code": var,
                "name": sent.get("variety_name", var),
                "sector": sent.get("sector", ""),
                "score": raw_score,
                "label": label,
                "bullish": round(bull * 100),
                "bearish": round(bear * 100),
                "posts": ss.get("total_posts_analyzed", 0),
                "date": latest.get("date", ""),
            }
        )
    return sorted(varieties, key=lambda x: -abs(x["score"]))
