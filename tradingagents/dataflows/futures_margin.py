"""盘面利润(以期货价格合成的产业链利润)计算模块。

【本文件在数据流中的角色】
  盘面利润 = 用主力连续合约收盘价按产业配比合成的"虚拟利润"序列,例如
  螺纹钢盘面利润 ≈ RB − 1.6×I − 0.5×J(1 吨螺纹消耗约 1.6 吨铁矿石 + 0.5 吨焦炭)。
  它是产业链估值的核心读数:"利润处于历史低分位 → 供给收缩概率上升"。

  两个消费入口:
  1. Agent 工具 get_futures_margin(commodity_futures_tools.py)→ 基本面分析师
     在成本传导/利润分配框架里取数(替代此前"LLM 拉几个品种价格自己心算");
  2. 数据看板 /api/dashboard 的 margin 区块(web_app._load_margin)→ 前端折线卡。

【口径与局限】(分析提示词与看板注释都会引用)
  - 主力连续合约价,未做换月平滑,利润序列在主力切换日可能有台阶;
  - 配比是行业惯例简化(未含合金/电极/加工费/增值税等),用于**趋势与分位**判断,
    不等于真实现金利润;
  - 依赖外盘/进口成本的配方(豆粕榨利、LM 炼厂利润等)不在此表——CBOT 与升贴水
    免费接口覆盖不稳,强行合成会输出误导性数值,宁可不做。
"""

from __future__ import annotations

import logging
from io import StringIO

import pandas as pd

logger = logging.getLogger(__name__)

# 【变量】盘面利润配方表:代码 → 配方定义。
#   legs: 品种代码 → 系数(1 吨产出消耗各原料吨数,产出品种系数 +1);
#   note: 给分析师/看板看的一句口径说明。
# 【为什么这些品种】黑三角(RB/HC/J)三个配方全部只用境内主力连续价,数据链路稳定;
#   涉及外盘的配方(豆粕榨利=CBOT+升贴水、炼厂利润=原油+运费)不做,避免编造精度。
MARGIN_FORMULAS: dict[str, dict] = {
    "RB": {
        "name": "螺纹钢盘面利润",
        "legs": {"RB": 1.0, "I": -1.6, "J": -0.5},
        "note": "1吨螺纹≈1.6吨铁矿石+0.5吨焦炭(长流程简化配比,未含合金/加工费)",
    },
    "HC": {
        "name": "热卷盘面利润",
        "legs": {"HC": 1.0, "I": -1.6, "J": -0.5},
        "note": "与螺纹同配比的板卷口径;HC−RB 价差另行看卷螺差",
    },
    "J": {
        "name": "焦化盘面利润",
        "legs": {"J": 1.0, "JM": -1.3},
        "note": "1吨焦炭≈1.3吨焦煤(焦比简化,未含焦炉煤气等化产收益)",
    },
}


def margin_formula_text(code: str) -> str | None:
    """返回品种的配方说明文本;无配方的品种返回 None。

    【参数】code: 品种代码(如 "RB")。
    【返回】如 "螺纹钢盘面利润 = RB - 1.6*I - 0.5*J(…口径说明)";无配方 → None。
    """
    f = MARGIN_FORMULAS.get((code or "").upper())
    if not f:
        return None
    parts: list[str] = []
    for i, (leg, coef) in enumerate(f["legs"].items()):
        mag = abs(coef)
        body = leg if mag == 1 else f"{mag:g}*{leg}"
        if i == 0:
            parts.append(f"-{body}" if coef < 0 else body)
        else:
            parts.append(f"- {body}" if coef < 0 else f"+ {body}")
    return f"{f['name']} = {' '.join(parts)}({f['note']})"


def _fetch_close_series(code: str, start_date: str, end_date: str) -> pd.Series | None:
    """取单品种主力连续日收盘序列(index=日期, value=close);失败返回 None。

    【关键逻辑】刻意走 get_futures_price 的 CSV 文本出口而非自建拉取:
    ①复用 "price:{main_sym}" 缓存(TTL+覆盖检查),与分析师看到的价格同源同口径;
    ②换月/主连选择逻辑只维护一份。CSV 首行是列头,直接 read_csv 解析。
    """
    from .commodity_futures import get_futures_price  # 【调用函数】懒导入避免环

    try:
        csv_text = get_futures_price(code, start_date, end_date)
    except Exception as e:
        logger.warning("margin leg %s fetch failed: %s", code, e)
        return None
    if not csv_text or csv_text.startswith(
        ("DATA_ERROR", "NO_DATA_AVAILABLE", "DATA_UNAVAILABLE")
    ):
        logger.warning("margin leg %s no data: %s", code, (csv_text or "")[:120])
        return None
    try:
        df = pd.read_csv(StringIO(csv_text))
        if "date" not in df.columns or "close" not in df.columns or df.empty:
            return None
        s = pd.Series(
            df["close"].astype(float).values,
            index=pd.to_datetime(df["date"]),
            name=code,
        )
        return s[~s.index.duplicated(keep="last")].sort_index()
    except Exception as e:
        logger.warning("margin leg %s parse failed: %s", code, e)
        return None


def compute_margin_series(
    code: str, start_date: str, end_date: str
) -> dict | None:
    """计算某品种的盘面利润序列与统计摘要;无配方或任一腿缺数据 → None。

    【参数】code: 品种代码;start_date/end_date: "YYYY-MM-DD"(两端含)。
    【返回】{
        "code", "name", "note",
        "points": [{"date": "YYYY-MM-DD", "value": float}, …](升序),
        "latest": float, "latest_date": str,
        "pct_rank": float | None,  # 最新值在窗口内的分位 0~1(≤最新值的历史点占比)
        "mean", "min", "max": float, "wow": float | None,  # 最新 vs 5 个交易日前
        "legs": {腿代码: 最新收盘价},
    }
    【关键逻辑】各腿收盘序列按日期 inner-join(任一腿当天缺价则该日不参与,
    保证利润值口径自洽);配比向量化一次算出,不做逐日循环。
    """
    f = MARGIN_FORMULAS.get((code or "").upper())
    if not f:
        return None
    code = code.upper()
    series_map: dict[str, pd.Series] = {}
    for leg in f["legs"]:
        s = _fetch_close_series(leg, start_date, end_date)
        if s is None or s.empty:
            return None  # 任一腿缺数据 → 宁可不给,不给半截利润
        series_map[leg] = s
    # 日期对齐:inner join 后按系数加权求和
    aligned = pd.DataFrame(series_map).dropna()
    if aligned.empty:
        return None
    margin = pd.Series(0.0, index=aligned.index, name="margin")
    for leg, coef in f["legs"].items():
        margin = margin + coef * aligned[leg]
    margin = margin.round(2)

    if margin.empty:
        return None
    latest_date = margin.index[-1]
    latest = float(margin.iloc[-1])
    pct_rank = round(float((margin <= latest).mean()), 4)
    wow = (
        round(latest - float(margin.iloc[-6]), 2)
        if len(margin) >= 6
        else None
    )
    return {
        "code": code,
        "name": f["name"],
        "note": f["note"],
        "points": [
            {"date": d.strftime("%Y-%m-%d"), "value": float(v)}
            for d, v in margin.items()
        ],
        "latest": latest,
        "latest_date": latest_date.strftime("%Y-%m-%d"),
        "pct_rank": pct_rank,
        "mean": round(float(margin.mean()), 2),
        "min": round(float(margin.min()), 2),
        "max": round(float(margin.max()), 2),
        "wow": wow,
        "legs": {leg: float(aligned[leg].iloc[-1]) for leg in f["legs"]},
    }


def get_futures_margin(
    symbol: str, start_date: str = "", end_date: str = ""
) -> str:
    """盘面利润读数(interface.VENDOR_METHODS 注册的供应商实现,与 get_futures_price 同族)。

    【参数】symbol: 品种代码(如 "RB");start_date/end_date: 可选窗口(缺省回看 1 年算分位)。
    【返回】格式化文本块(见 format_margin_text);无配方/缺数据为确定性哨兵文本。
    """
    return format_margin_text(symbol, start_date, end_date)


def format_margin_text(
    code: str, start_date: str = "", end_date: str = "", lookback_days: int = 365
) -> str:
    """Agent 工具出口:格式化盘面利润读数为文本块;无配方/缺数据返回哨兵文本。

    【参数】code: 品种代码;lookback_days: 统计分位用的回看窗口(自然日)。
    【返回】格式化文本;无配方 → "MARGIN_NO_FORMULA: …";数据缺失 →
            "NO_DATA_AVAILABLE: …"(确定性哨兵,不允许分析师编造)。
    """
    code = (code or "").upper()
    if code not in MARGIN_FORMULAS:
        formulas = ", ".join(sorted(MARGIN_FORMULAS))
        return (
            f"MARGIN_NO_FORMULA: {code} 无盘面利润配方。当前支持: {formulas}。"
            "可用 get_futures_price 自行拉取相关品种价格做定性成本比较。"
        )
    from datetime import datetime, timedelta

    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=lookback_days)).strftime(
        "%Y-%m-%d"
    )
    result = compute_margin_series(code, start, end)
    if result is None:
        return (
            f"NO_DATA_AVAILABLE: 无法计算 {code} 盘面利润(配方腿主力合约价格缺失, "
            f"窗口 {start}~{end})。"
        )
    pct = result["pct_rank"]
    pct_desc = (
        "极低(<10%)"
        if pct < 0.10
        else "偏低(<25%)"
        if pct < 0.25
        else "中性(25%~75%)"
        if pct <= 0.75
        else "偏高(>75%)"
        if pct <= 0.90
        else "极高(>90%)"
    )
    wow = (
        f", 近5交易日变动 {result['wow']:+.2f}" if result["wow"] is not None else ""
    )
    legs = ", ".join(f"{k}={v:.0f}" for k, v in result["legs"].items())
    return (
        f"# FUTURES MARGIN({result['name']})\n"
        f"公式口径: {result['note']}\n"
        f"最新值: {result['latest']:.2f} 元/吨 ({result['latest_date']}){wow}\n"
        f"腿最新收盘: {legs}\n"
        f"窗口统计({start}~{end}): 均值 {result['mean']:.2f} / "
        f"最小 {result['min']:.2f} / 最大 {result['max']:.2f}\n"
        f"历史分位: {pct:.0%} ({pct_desc})\n"
        "解读提示: 分位是相对窗口的估值读数;配比为行业简化,用于趋势与分位判断,"
        "不等于真实现金利润;主力换月日可能有台阶。"
    )
