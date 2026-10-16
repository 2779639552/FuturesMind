"""user_signals.py — 自传数据信号提炼引擎(两级:通用分析器 + 领域提炼器,含自适应注册)

【模块角色】用户自传数据入库后,不裸灌原始列给 LLM,而是先做"信号提炼":
  ① 通用分析器(任何数据必出,纯确定性): A层确定性计算 + B层规则判定 + C层模板拼装;
  ② 领域提炼器(注册表命中才增强): 按 data_type/variety/列含义关键词匹配专属提炼器;
  ③ 自适应注册(未命中时): 按语义关键词自动判定新类别,套用模板提炼器并登记,
     下次同类型数据直接命中该类别。
【输出】统一 schema 的 signals dict + render_md(给 LLM 的注入块,由 signals 确定性渲染)。
【纪律】纯代码计算,零 LLM(LLM 不碰数值);数据不足自动降级(绝不硬算假信号)。
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from datetime import date, datetime

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "user_signal_v1"

# 通用层窗口参数(【变量】可调)
W_CHG20 = 20      # 20 日变化窗口
W_CHG60 = 60      # 60 日变化窗口
W_52W = 260       # 52 周窗口(日频)
MIN_TREND_N = 5   # 趋势判定最少期数
STALE_DAYS_LIMIT = 5  # 超过视为过期(注入标黄)


# ── 数据准备: 从 DB 行还原真实数据行 ───────────────────────────────────

def _load_rows(ds: dict) -> tuple[list[dict], dict]:
    """从数据集 DB 行解析出 (数据行, spec)。解析失败返回 ([], {})。"""
    try:
        spec = json.loads(ds.get("spec") or "{}")
        rows = json.loads(ds.get("data") or "[]")
    except (json.JSONDecodeError, TypeError):
        return [], {}
    if not isinstance(rows, list) or not isinstance(spec, dict):
        return [], {}
    return rows, spec


def _is_date_str(v) -> bool:
    """该值能否解析为日期字符串(YYYY-MM-DD 开头)。"""
    s = str(v or "").strip()
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", s))


def _date_key(v) -> str:
    """取日期键(前 10 位);解析失败返回 ''。"""
    s = str(v or "").strip()
    return s[:10] if _is_date_str(s) else ""


def _as_float(v) -> float | None:
    """值 → float;无法解析返回 None(不抛异常)。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _spec_cols(spec: dict) -> dict[str, dict]:
    """spec.columns → {列名: 列说明 dict}。"""
    out = {}
    for c in (spec.get("columns") or []):
        if isinstance(c, dict) and c.get("name"):
            out[str(c["name"])] = c
    return out


def _real_rows(rows: list[dict], spec: dict) -> list[dict]:
    """筛出真实数据行(含可解析日期;排除表头/单位/其他块的行)。"""
    date_col = (spec.get("date_column") or "").strip()
    cands = _DATE_CANDIDATES if (date_col not in _DATE_CANDIDATES) else _DATE_CANDIDATES
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if date_col and _is_date_str(r.get(date_col)):
            out.append(r)
            continue
        for cand in cands:
            if cand in r and _is_date_str(r.get(cand)):
                out.append(r)
                break
    return out


# 【变量】日期列候选(与 user_data._DATE_COL_CANDIDATES 同源,独立维护防循环依赖)
_DATE_CANDIDATES = ("Code", "Unnamed: 0", "AssessDate", "Date", "date",
                    "日期", "时间", "交易日", "DateTime", "Datetime")


def _sort_rows(rows: list[dict], spec: dict) -> list[dict]:
    """按日期升序(取日期键排序;无日期行排最后)。"""
    return sorted(rows, key=lambda r: _date_key(r.get((spec.get("date_column") or "").strip()))
                  or _date_key(next((r.get(c) for c in _DATE_CANDIDATES if c in r), "")))


def _series(rows: list[dict], col: str) -> list[float]:
    """列 → 数值序列(按行序,空值剔除)。"""
    return [_as_float(r.get(col)) for r in rows if _as_float(r.get(col)) is not None]


# ── 通用层 A: 确定性计算 ───────────────────────────────────────────────

def _calc_single(series: list[float]) -> dict:
    """单列数值序列 → A层信号(最新/环比/20·60日变化/极值/均值;数据不足自动降级)。"""
    out = {"latest": None, "mom": None, "mom_pct": None,
           "chg20": None, "chg60": None, "min": None, "max": None,
           "mean": None, "n": len(series)}
    if not series:
        return out
    latest = series[-1]
    out.update(latest=latest, min=min(series), max=max(series),
               mean=statistics.mean(series))
    if len(series) >= 2:
        prev = series[-2]
        if prev not in (None, 0):
            out["mom"] = latest - prev
            out["mom_pct"] = (latest - prev) / abs(prev) * 100
    if len(series) > W_CHG20:
        out["chg20"] = latest - series[-1 - W_CHG20]
    if len(series) > W_CHG60:
        out["chg60"] = latest - series[-1 - W_CHG60]
    return out


def _pctile_52w(series: list[float]) -> float | None:
    """52 周分位(0-100);不足 52 周返回 None。"""
    if len(series) < W_52W:
        return None
    win = series[-W_52W:]
    if not win:
        return None
    return statistics.median([100 * (x - min(win)) / (max(win) - min(win)) if max(win) > min(win) else 50.0 for x in win[-1:]])


# ── 通用层 B: 规则判定 ─────────────────────────────────────────────────

def _trend_dir(series: list[float], n: int = MIN_TREND_N) -> str:
    """趋势方向: 线性回归斜率符号(最近 n 期);期数不足返回 'insufficient'。"""
    if len(series) < n:
        return "insufficient"
    win = series[-n:]
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(win) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return "flat"
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, win)) / denom
    span = abs(mean_y) if mean_y else 1.0
    if slope / span > 0.01:
        return "up"
    if slope / span < -0.01:
        return "down"
    return "flat"


def _inventory_regime(series: list[float]) -> str | None:
    """库存周期判定(环比符号): 去库/累库;数据不足返回 None。"""
    if len(series) < 2:
        return None
    diff = series[-1] - series[-2]
    if diff < 0:
        return "destocking"   # 去库
    if diff > 0:
        return "restocking"   # 累库
    return "flat"


# ── 领域提炼器: Platts 原油价差 ────────────────────────────────────────

# 【变量】Platts 价差列的区域分组(以列含义关键词匹配,不依赖列名)
_PLATTS_GROUP_KEYWORDS = {
    "north_sea":   ("北海", "North Sea", "Forties", "Ekofisk", "Oseberg", "BNB", "Troll", "Johan"),
    "west_africa": ("西非", "WAF", "Bonny", "Qua Iboe", "Forcados", "Girassol"),
    "mid_east":    ("中东", "Urals", "Es Sider", "Saharan", "CPC", "Al-Shaheen", "Qatar", "Murban", "Zakum", "Oman", "Dubai"),
    "americas":    ("美洲", "WTI", "Mars", "LLS", "MEH", "WCS", "Castilla", "Tupi", "Liza", "Unity"),
    "russia_far":  ("俄", "ESPO", "Urals", "远东", "India"),
}


def _platts_group_of(meaning: str) -> str | None:
    """列含义命中哪个区域组;未命中返回 None。"""
    m = meaning or ""
    for g, kws in _PLATTS_GROUP_KEYWORDS.items():
        if any(k in m for k in kws):
            return g
    return None


# 【变量】价差/贴水列特征词(用于区分"绝对价"列 vs "贴水/价差"列)
_SPREAD_FEATURES = ("vs", "FOB", "Differential", "Diff", "CFD", "Strip", "折价", "升水", "贴水")


def _is_price_col(meaning: str) -> bool:
    """该列含义是**绝对价格**列吗(不含价差特征词)。"""
    m = meaning or ""
    return not any(k in m for k in _SPREAD_FEATURES)


def _refine_platts(rows: list[dict], spec: dict) -> dict:
    """Platts 原油价差表 → 领域信号(期限结构 / 区域贴水 / 事件价差)。"""
    cols = _spec_cols(spec)
    # 数值列 = spec 声明且可解析出数值的列(排除日期列)
    date_col = (spec.get("date_column") or "").strip()
    num_cols = []
    for name, c in cols.items():
        if name == date_col:
            continue
        if _series(rows, name):
            num_cols.append((name, c.get("meaning", ""), c.get("unit", "")))
    if not num_cols:
        return {}
    # 按列含义找基准列: 只认**绝对价列**(排除贴水/价差列),精确匹配优先
    def find_col(*kws):
        exact = [n for n, m, u in num_cols if any(k == m.strip() for k in kws)]
        if exact:
            return exact[0]
        for n, m, u in num_cols:
            if _is_price_col(m) and any(k in m for k in kws):
                return n
        return None
    brent_col = find_col("Dated Brent", "Brent") or num_cols[0][0]
    dubai_col = find_col("Dubai Mo01", "Dubai M1", "Dubai") or None
    # 期限结构: Dubai Mo01-Mo03 价差(找含义含 Mo01-Mo03 或列名含 Unnamed 的价差列)
    ts_col = None
    for name, meaning, unit in num_cols:
        if "Mo01" in meaning and "Mo03" in meaning:
            ts_col = name
            break
    domain: dict = {}
    brent = _calc_single(_series(rows, brent_col))
    domain["benchmarks"] = {"brent": brent}
    if dubai_col:
        domain["benchmarks"]["dubai_m1"] = _calc_single(_series(rows, dubai_col))
    if ts_col:
        ts = _calc_single(_series(rows, ts_col))
        domain["term_structure"] = {
            "spread": ts.get("latest"),
            "chg20": ts.get("chg20"),
            "regime": "BACK" if (ts.get("latest") or 0) > 0 else "CONTANGO",
            "bias": "bullish" if (ts.get("latest") or 0) > 0 else "bearish",
        }
    # 区域平均贴水: 只统计**价差列**(含义含 vs/Differential/FOB/折价/升水),
    # 排除绝对价格列(Dubai Mo01=113 这种),否则区域均值会被基准价污染
    def _is_spread_col(meaning: str) -> bool:
        m = meaning or ""
        return any(k in m for k in ("vs", "Differential", "FOB", "折价", "升水", "贴水", "价差", "差价"))
    reg = {}
    for g in _PLATTS_GROUP_KEYWORDS:
        vals = []
        for name, meaning, unit in num_cols:
            if _platts_group_of(meaning) == g and _is_spread_col(meaning):
                v = _as_float(rows[-1].get(name)) if rows else None
                if v is not None:
                    vals.append(v)
        if vals:
            reg[g] = {"avg": round(statistics.mean(vals), 2), "n": len(vals)}
    if reg:
        domain["regional_spreads"] = reg
    return domain


# ── 领域提炼器: 库存周期 ───────────────────────────────────────────────

def _refine_inventory(rows: list[dict], spec: dict) -> dict:
    """库存类数据 → 领域信号(库存水平 / 周期 / 分位)。"""
    cols = _spec_cols(spec)
    date_col = (spec.get("date_column") or "").strip()
    inv_col = None
    for name, c in cols.items():
        if name == date_col:
            continue
        meaning = c.get("meaning", "")
        if "库存" in meaning or "万吨" in c.get("unit", "") or "库存" in name:
            inv_col = name
            break
    if not inv_col:
        for name in cols:
            if name != date_col and _series(rows, name):
                inv_col = name
                break
    if not inv_col:
        return {}
    series = _series(rows, inv_col)
    calc = _calc_single(series)
    return {
        "inventory_level": calc.get("latest"),
        "mom": calc.get("mom"),
        "mom_pct": calc.get("mom_pct"),
        "regime": _inventory_regime(series),
        "trend": _trend_dir(series),
        "pctile_52w": _pctile_52w(series),
    }


# ── 注册表 + 自适应注册 ────────────────────────────────────────────────

# 显式注册的领域提炼器(【变量】新增类别时在此登记)
REFINER_REGISTRY = [
    {
        "id": "platts_crude",
        "label": "原油现货价差",
        # 匹配规则: 任一条件命中即算(多条件取最高分)
        "match_score": lambda spec, rows: (
            3 if ("原油" in (spec.get("data_type") or "")) or
                 any("FOB" in (c.get("meaning") or "") or "Strip" in (c.get("meaning") or "")
                     for c in (spec.get("columns") or [])) else 0
        ),
        "refiner": _refine_platts,
    },
    {
        "id": "inventory",
        "label": "库存周期",
        "match_score": lambda spec, rows: (
            2 if any("库存" in (c.get("meaning") or "") or "万吨" in (c.get("unit") or "")
                     for c in (spec.get("columns") or [])) else 0
        ),
        "refiner": _refine_inventory,
    },
]

# 自适应类别判定: 未命中注册表时按语义关键词归类(【变量】可扩)
AUTO_CATEGORIES = [
    {"cat": "inventory",   "label": "库存类",    "kws": ("库存", "万吨", "社会库存")},
    {"cat": "price",       "label": "价格类",    "kws": ("价格", "现货", "收盘", "结算")},
    {"cat": "spread",      "label": "价差/基差类", "kws": ("价差", "基差", "贴水", "升水")},
    {"cat": "profit",      "label": "利润类",    "kws": ("利润", "加工费", "盈亏")},
    {"cat": "rate",        "label": "开工/负荷类", "kws": ("开工", "负荷", "产能利用率")},
]

# 自动注册簿(内存 + 落盘 ~/.tradingagents/refiner_registry.json,避免重复判定)
_AUTO_REGISTRY: dict[str, str] = {}
_REGISTRY_FILE = __import__("pathlib").Path.home() / ".tradingagents" / "refiner_registry.json"


def _load_auto_registry() -> dict[str, str]:
    """加载落盘的自适应注册(类别 → 标签);文件缺失/损坏返回 {}。"""
    global _AUTO_REGISTRY
    if _AUTO_REGISTRY:
        return _AUTO_REGISTRY
    try:
        if _REGISTRY_FILE.exists():
            data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _AUTO_REGISTRY = {k: v.get("label", k) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return _AUTO_REGISTRY


def _save_auto_registry() -> None:
    """把自适应注册结果落盘(失败仅记日志,不影响主流程)。"""
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: {"label": v} for k, v in _AUTO_REGISTRY.items()}
        _REGISTRY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning("自适应注册落盘失败: %s", e)


def _match_refiner(spec: dict) -> tuple[str, str, object, bool]:
    """注册表匹配 → (id, label, refiner_fn, auto)。未命中走自适应,返回 auto 类别。"""
    best_id, best_label, best_fn, best_score = None, None, None, 0
    for r in REFINER_REGISTRY:
        try:
            score = r["match_score"](spec, [])
        except Exception:
            score = 0
        if score > best_score:
            best_id, best_label, best_fn, best_score = r["id"], r["label"], r["refiner"], score
    if best_id:
        return best_id, best_label, best_fn, False
    # 自适应: 判定类别 → 查自动注册簿 → 未注册则登记
    cat, label = None, None
    for c in AUTO_CATEGORIES:
        if any(k in (spec.get("data_type") or "") for k in c["kws"]):
            cat, label = c["cat"], c["label"]
            break
    if cat is None:  # 连语义关键词也没有 → 走列含义兜底
        for c in AUTO_CATEGORIES:
            if any(any(k in (col.get("meaning") or "") or k in (col.get("unit") or "")
                       for k in c["kws"]) for col in (spec.get("columns") or [])):
                cat, label = c["cat"], c["label"]
                break
    if cat:
        reg = _load_auto_registry()
        if cat not in reg:
            reg[cat] = label
            _AUTO_REGISTRY[cat] = label
            _save_auto_registry()
        return f"auto:{cat}", label, None, True
    return "generic", "通用", None, False


# ── 统一入口 ───────────────────────────────────────────────────────────

def build_signals(ds: dict, as_of_date: date | None = None) -> dict:
    """数据集 DB 行 → 统一 signals dict(含 common/domain/readiness/render_md)。

    【参数】ds: user_datasets 表的一行(dict,须含 spec/data);as_of_date: 当前日期,
            默认今天(计算 stale_days 用,便于测试注入固定日期)。
    【返回】见模块 docstring schema;任何异常都不外抛,失败返回最小可用结构。
    """
    try:
        rows, spec = _load_rows(ds)
        if not rows:
            return {"schema_version": SCHEMA_VERSION, "dataset_id": ds.get("id"),
                    "refiner": "generic", "refiner_label": "通用", "auto_registered": False,
                    "common": {}, "domain": {}, "readiness": {"usable": False},
                    "render_md": ""}
        real = _sort_rows(_real_rows(rows, spec), spec)
        if not real:
            return {"schema_version": SCHEMA_VERSION, "dataset_id": ds.get("id"),
                    "refiner": "generic", "refiner_label": "通用", "auto_registered": False,
                    "common": {}, "domain": {}, "readiness": {"usable": False},
                    "render_md": ""}

        # 主数值列(通用层用): 优先选"基准/价格"语义列(Brent/Dubai/价格/结算),
        # 再兜底第一个有数值的列 —— 否则宽表会选到某个价差列当主指标
        cols = _spec_cols(spec)
        date_col = (spec.get("date_column") or "").strip()
        main_col = None
        _MAIN_COL_KWS = ("Brent", "Dated", "Dubai", "价格", "基准", "结算", "收盘", "现货")
        for name, c in cols.items():
            if name == date_col:
                continue
            m = c.get("meaning") or ""
            if _is_price_col(m) and any(k in m for k in _MAIN_COL_KWS) and _series(real, name):
                main_col = name
                break
        if main_col is None:
            for name in cols:
                if name != date_col and _series(real, name):
                    main_col = name
                    break
        common: dict = {}
        if main_col:
            series = _series(real, main_col)
            calc = _calc_single(series)
            common = {
                "col": main_col,
                "unit": cols[main_col].get("unit", "") if main_col in cols else "",
                "latest": calc.get("latest"), "mom": calc.get("mom"),
                "mom_pct": calc.get("mom_pct"), "chg20": calc.get("chg20"),
                "chg60": calc.get("chg60"), "min": calc.get("min"),
                "max": calc.get("max"), "mean": calc.get("mean"),
                "trend": _trend_dir(series), "pctile_52w": _pctile_52w(series),
                "n": len(series), "coverage_pct": round(len(series) / len(real) * 100, 1),
            }

        # 领域层
        refiner_id, refiner_label, refiner_fn, auto = _match_refiner(spec)
        domain: dict = {}
        if refiner_fn:
            try:
                domain = refiner_fn(real, spec) or {}
            except Exception as e:
                logger.warning("领域提炼器 %s 执行失败: %s", refiner_id, e)

        # 时效
        as_of = _date_key(real[-1].get(date_col)) if date_col else ""
        if not as_of:
            for cand in _DATE_CANDIDATES:
                if cand in real[-1]:
                    as_of = _date_key(real[-1].get(cand))
                    if as_of:
                        break
        stale_days = 0
        if as_of:
            try:
                stale_days = (as_of_date or date.today()) - date.fromisoformat(as_of)
                stale_days = max(0, stale_days.days)
            except ValueError:
                stale_days = 0

        readiness = {
            "coverage_ok": bool(common.get("coverage_pct", 0) >= 60),
            "stale": stale_days > STALE_DAYS_LIMIT,
            "usable": bool(common.get("n", 0) >= 1),
        }

        out = {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": ds.get("id"),
            "variety": ds.get("variety", ""),
            "as_of": as_of, "stale_days": stale_days,
            "refiner": refiner_id, "refiner_label": refiner_label,
            "auto_registered": auto,
            "common": common, "domain": domain,
            "readiness": readiness,
        }
        out["render_md"] = _render_md(out, ds)
        return out
    except Exception as e:
        logger.warning("build_signals 异常(dataset=%s): %s", ds.get("id"), e)
        return {"schema_version": SCHEMA_VERSION, "dataset_id": ds.get("id"),
                "refiner": "generic", "refiner_label": "通用", "auto_registered": False,
                "common": {}, "domain": {}, "readiness": {"usable": False},
                "render_md": ""}


def _fmt(v, nd=2) -> str:
    """数值格式化: None → '—';保留 nd 位小数。"""
    if v is None:
        return "—"
    return f"{v:.{nd}f}"


def _render_md(sig: dict, ds: dict) -> str:
    """signals dict → 给 LLM 的 markdown 注入块(确定性模板拼装,零 LLM)。"""
    c, d = sig.get("common", {}), sig.get("domain", {})
    lines = [
        f"## 用户自传数据 · {sig.get('variety') or ds.get('variety') or '未知品种'} · {sig.get('refiner_label')}信号"
        f"(截至 {sig.get('as_of') or '未知'}{'，已过期 ' + str(sig['stale_days']) + ' 天' if sig.get('stale_days', 0) > 0 else ''})",
    ]
    if c:
        lines.append(f"- 主指标 {c.get('col')}: 最新 {_fmt(c.get('latest'))}{c.get('unit') or ''}"
                     f"(环比 {_fmt(c.get('mom_pct'))}% / 20日 {_fmt(c.get('chg20'))}{c.get('unit') or ''})")
        lines.append(f"- 趋势: {c.get('trend')} | 52周分位: {_fmt(c.get('pctile_52w'), 0)}%"
                     f"(数据 {c.get('n')} 期, 覆盖 {c.get('coverage_pct')}%)")
    if "term_structure" in d:
        ts = d["term_structure"]
        lines.append(f"- 期限结构: Dubai Mo01-Mo03 {_fmt(ts.get('spread'))} {ts.get('regime')}"
                     f"(20日 {_fmt(ts.get('chg20'))}) → {ts.get('bias')}")
    if "regional_spreads" in d:
        r = d["regional_spreads"]
        lines.append("- 区域贴水: " + " ".join(f"{k} {_fmt(v.get('avg'))}" for k, v in r.items()))
    if "inventory_level" in d:
        lines.append(f"- 库存: {_fmt(d.get('inventory_level'))}(环比 {_fmt(d.get('mom_pct'))}%) → "
                     f"{'去库' if d.get('regime') == 'destocking' else '累库' if d.get('regime') == 'restocking' else '持平'}"
                     f" | 52周分位 {_fmt(d.get('pctile_52w'), 0)}%")
    if sig.get("auto_registered"):
        lines.append(f"- (本数据自动归入「{sig.get('refiner_label')}」类别并已注册,后续同类型直接识别)")
    if not sig.get("readiness", {}).get("usable"):
        lines.append("- 数据无法识别有效数值行,建议检查上传文件格式")
    return "\n".join(lines)
