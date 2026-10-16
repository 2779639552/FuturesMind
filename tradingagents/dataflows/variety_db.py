"""variety_db.py — 品种数据库(本地导入的产业数据库)读取、检索与分析师注入

【模块角色】
  用户在 web「品种数据库」页导入的产业数据库(xlsx → `data/variety/<slug>/` 逐表 JSON),
  是一手产业数据:库存/仓单/开工/产量/炼厂排产/裂差利润/基差月差/道路需求/平衡表/气温...
  本模块把这套数据接进"运行分析"链路,分两条通道:

    1) **确定性注入** `render_variety_db_context(symbol)`
       基本面分析师系统提示前置注入(与 user_data / research_macro 同模式)。
       为什么不做成工具:工具调用由 LLM 自主决策、不保证发生,而库存/开工/裂差这类
       关键读数必须 100% 到位且不占工具轮数。
    2) **工具下钻** `query_variety_db(...)`
       供 @tool `get_variety_db` 使用:按关键词/分组/区间检索,把"想看的模块"取回来。
       58 个模块不可能全塞进提示,所以注入块只放关键读数,细节让 LLM 自己下钻。

【权重口径】本地导入库是同品种同指标的一手数据,在其覆盖范围内优先于公开接口
  (akshare/免费 API)与研报转述;注入块内写明该规则,冲突时以本块为准。

【数据来源】`data/variety/<slug>/{meta,index,sNN}.json`(由 variety_convert.py 生成)
【目录查找】env `AGENTSENSE_VARIETY_DIR` > 项目根 `data/variety`
【加载策略】进程内缓存,按"文件集(名, mtime, size)"签名失效 —— 重新导入数据库后
  自动重建,不需要重启 web 服务。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# 【常量】注入块规模上限:模块数与模块内序列数(提示词体积与信息量的折中)
MAX_INJECT_MODULES = 6
MAX_INJECT_SERIES = 3
INJECT_WINDOW = 250  # 【常量】分位/区间统计的窗口期数(日频≈一年, 周频≈五年)
QUERY_WINDOW_MAX = 2500  # 【常量】工具查询单序列最多回看的点数(防超长历史撑爆上下文)
QUERY_MAX_SERIES = 12  # 【常量】工具查询单次返回的序列上限(调用方可调小)
QUERY_POINTS_SHOWN = 6  # 【常量】工具查询每条序列展示的最近点数

# 【常量】分组优先级: 基本面前几组最关键, 决定注入块先讲什么
GROUP_ORDER = ("库存仓单", "供需", "炼厂排产", "成本利润", "基差价差", "行情价格",
               "需求", "平衡表", "宏观天气", "报告快照", "其他")

# 【常量】注入块里"每分组最多取几个模块"(避免某一组霸占全部名额)
MAX_MODULES_PER_GROUP = 2

# 【变量】进程内缓存: (root, slug) → {"sig": 文件集签名, "index": [...], "sheets": {...}}
_CACHE: dict[tuple[str, str], dict] = {}


# ── 目录与清单 ──────────────────────────────────────────────────────────────

def variety_root() -> Path:
    """品种数据库根目录: env `AGENTSENSE_VARIETY_DIR` 优先, 否则项目根 data/variety。"""
    env = os.environ.get("AGENTSENSE_VARIETY_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "variety"


def list_varieties(root: Path | None = None) -> list[dict]:
    """品种清单: 优先读 varieties.json; 缺失/损坏时按子目录扫 index.json 重建。"""
    root = Path(root) if root else variety_root()
    p = root / "varieties.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:  # noqa: BLE001  清单损坏不影响分析主线
            pass
    out = []
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "index.json").exists():
                m = _read_json(d / "meta.json") or {}
                out.append({"slug": d.name, "name": m.get("name", d.name),
                            "symbols": m.get("symbols") or []})
    return out


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _symbols_of(meta: dict) -> list[str]:
    """品种元信息 → 期货代码列表(兼容字符串写法), 统一大写去重。"""
    raw = meta.get("symbols") or []
    if isinstance(raw, str):
        raw = [c for c in raw.replace("，", ",").split(",") if c.strip()]
    out: list[str] = []
    for c in raw:
        c = str(c).strip().upper()
        if c and c not in out:
            out.append(c)
    return out


def _code_to_name(code: str) -> str:
    """期货代码 → 品种中文名(复用主项目元数据; 导入失败返回空串, 不影响主流程)。"""
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

        meta = VARIETY_METADATA.get(code.upper())
        if isinstance(meta, dict):
            return str(meta.get("name") or "")
        return str(meta or "")
    except Exception:  # noqa: BLE001
        return ""


def _code_by_name(name: str) -> str:
    """品种中文名 → 期货代码(项目元数据反查; 失败返回空串)。"""
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

        for code, meta in VARIETY_METADATA.items():
            nm = meta.get("name") if isinstance(meta, dict) else meta
            if str(nm or "").strip() == name:
                return str(code).upper()
    except Exception:  # noqa: BLE001
        return ""
    return ""


def resolve_variety(symbol: str, root: Path | None = None) -> dict | None:
    """期货品种代码/中文名 → 品种库元信息 dict(slug/name/...); 找不到返回 None。

    【匹配顺序】
      1. meta.symbols 显式代码(导入时写入, 权威; 如 BU → bitumen);
      2. 传入值本身就是品种中文名(如 "沥青");
      3. 中文名/代码 → 项目元数据反查(新导入品种没写 symbols 时的兜底);
      4. slug 与传入值同名(如 pvc → PVC)。
    【关键】只做确定性字符串匹配, 不做模糊/拼音猜测(猜错会把别的品种数据喂给分析师)。
    """
    if not symbol:
        return None
    sym = str(symbol).strip().upper()
    root = Path(root) if root else variety_root()
    items = list_varieties(root)
    # ① 显式代码匹配
    for it in items:
        if sym in [str(c).upper() for c in (it.get("symbols") or [])]:
            return _meta(root, it)
    # ② 传入的就是品种中文名(如 "沥青")
    for it in items:
        if str(it.get("name") or "").strip() == str(symbol).strip():
            return _meta(root, it)
    # ③ 中文名 → 期货代码反查(如 "沥青" 在项目元数据里对应 BU; 再按代码匹配清单)
    cn = _code_to_name(sym) or _code_by_name(str(symbol).strip())
    if cn:
        for it in items:
            if str(it.get("name") or "").strip() == cn:
                return _meta(root, it)
    # ④ slug 同名
    low = str(symbol).strip().lower()
    for it in items:
        if str(it.get("slug") or "").lower() == low:
            return _meta(root, it)
    return None


def _meta(root: Path, item: dict) -> dict:
    """把清单项补全为完整 meta(合并目录内 meta.json, 清单项优先缺失字段)。"""
    slug = item.get("slug")
    disk = _read_json(Path(root) / str(slug) / "meta.json") or {}
    merged = {**disk, **{k: v for k, v in item.items() if v not in (None, [], "")}}
    merged.setdefault("slug", slug)
    merged.setdefault("name", slug)
    merged["symbols"] = _symbols_of(merged)
    return merged


# ── 表数据加载(带签名失效缓存) ────────────────────────────────────────────

def _signature(slug_dir: Path) -> tuple:
    """文件集签名: index.json + 各 sNN.json 的 (名, mtime_ns, size)。"""
    sig = []
    for f in sorted(slug_dir.glob("*.json")):
        try:
            st = f.stat()
            sig.append((f.name, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(sig)


def _bundle(slug: str, root: Path | None = None) -> dict:
    """取品种库的 index + 已加载表(按签名失效, 重新导入后自动重建)。"""
    root = Path(root) if root else variety_root()
    key = (str(root), slug)
    slug_dir = root / slug
    sig = _signature(slug_dir)
    ent = _CACHE.get(key)
    if ent and ent["sig"] == sig:
        return ent
    ent = {"sig": sig, "dir": slug_dir,
           "index": _read_json(slug_dir / "index.json") or [], "sheets": {}}
    _CACHE[key] = ent
    return ent


def _sheet(slug: str, sheet_slug: str, root: Path | None = None) -> dict | None:
    """取单张表(懒加载 + 缓存); 失败返回 None(调用方降级, 不抛)。"""
    ent = _bundle(slug, root)
    if sheet_slug in ent["sheets"]:
        return ent["sheets"][sheet_slug]
    obj = _read_json(ent["dir"] / f"{sheet_slug}.json")
    ent["sheets"][sheet_slug] = obj
    return obj


# ── 序列统计 ────────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def series_points(obj: dict, name: str) -> list[tuple[str, float]]:
    """从表对象里取某条 series 的点列 [(date, value)]; 非 series 表返回 []。"""
    for s in obj.get("series") or []:
        if s.get("name") == name:
            out = []
            for p in s.get("points") or []:
                if not isinstance(p, (list, tuple)) or len(p) < 2:
                    continue
                v = _num(p[1])
                if v is None:
                    continue
                out.append((str(p[0]), v))
            return out
    return []


def series_stats(points: list[tuple[str, float]], window: int = INJECT_WINDOW,
                 today: str | None = None) -> dict | None:
    """点列 → 读数统计: 最新值/日期、环比(上一期)、近窗口变化、分位、区间。

    【关键】只统计**非空**点: 某日缺数在源数据里是键缺省, 不补 0 也不插值
    (补 0 会把"没数据"读成"库存归零", 是实质性误导)。
    【关键】日期晚于今天的点(平衡表/排产类源表会把当年剩余月份填成计划值)不计入
    读数, 只记 future_n 个数并提示"预测/计划值" —— 否则会把计划值当既成事实引用。
    """
    if not points:
        return None
    today = today or date.today().isoformat()
    obs = [p for p in points if p[0] <= today]
    future_n = len(points) - len(obs)
    if obs:
        points = obs
    last_date, last = points[-1]
    prev = points[-2][1] if len(points) >= 2 else None
    win = [v for _, v in points[-window:]] if window > 0 else [v for _, v in points]
    lookback = points[-5] if len(points) >= 5 else (points[0] if points else None)
    out = {
        "date": last_date, "latest": last, "prev": prev,
        "change": (last - prev) if prev is not None else None,
        "pct": ((last - prev) / abs(prev) * 100) if prev not in (None, 0) else None,
        "n": len(points), "first_date": points[0][0],
        "window_n": len(win), "window_min": min(win) if win else None,
        "window_max": max(win) if win else None,
        "change_4": (last - lookback[1]) if lookback is not None and lookback[1] != last else None,
        "future_n": future_n,
    }
    if win:
        out["pct_rank"] = sum(1 for x in win if x <= last) / len(win) * 100
    else:
        out["pct_rank"] = None
    return out


def fmt_num(v) -> str:
    """数值格式化: 千分位 + 最多两位小数(整数不带小数点)。"""
    if v is None:
        return "-"
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        v = round(v)
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"


def _fmt_stat(st: dict) -> str:
    """统计 → 中文读数句(最新 + 环比 + 近4期 + 分位/区间)。"""
    bits = [f"最新 {fmt_num(st['latest'])} ({st['date']})"]
    if st.get("change") is not None:
        sgn = "+" if st["change"] >= 0 else ""
        pct = f", {sgn}{st['pct']:.2f}%" if st.get("pct") is not None else ""
        bits.append(f"环比 {sgn}{fmt_num(st['change'])}{pct}")
    if st.get("change_4") is not None:
        bits.append(f"近4期 {'+' if st['change_4'] >= 0 else ''}{fmt_num(st['change_4'])}")
    if st.get("pct_rank") is not None:
        bits.append(f"区间分位 {st['pct_rank']:.0f}%")
    if st.get("window_min") is not None:
        bits.append(f"区间 [{fmt_num(st['window_min'])}, {fmt_num(st['window_max'])}]")
    if st.get("future_n"):
        bits.append(f"另有 {st['future_n']} 个未来日期点(源表计划/预测值, 未计入读数)")
    return ", ".join(bits)


# ── 模块/序列选择 ───────────────────────────────────────────────────────────

def _group_rank(group: str) -> int:
    try:
        return GROUP_ORDER.index(group)
    except ValueError:
        return len(GROUP_ORDER)


class _Desc(str):
    """降序包装: 同组内"数据越新越靠前", 而无需反转整个排序键。"""

    __slots__ = ()

    def __lt__(self, other):
        return str.__gt__(self, other)

    def __gt__(self, other):
        return str.__lt__(self, other)


def _sort_modules(index: list[dict]) -> list[dict]:
    """模块排序: 分组优先级 → 数据新鲜度(date_max 降序; 无日期轴排最后) → 序列数降序。"""
    return sorted(index, key=lambda m: (_group_rank(m.get("group") or "其他"),
                                        _Desc(str(m.get("date_max") or "")),
                                        -(m.get("n_series") or 0)))


def pick_modules(index: list[dict], max_modules: int = MAX_INJECT_MODULES,
                 per_group: int = MAX_MODULES_PER_GROUP) -> list[dict]:
    """挑注入用模块: 按分组优先级 + 新鲜度, 每组最多 per_group 个, 总数 max_modules。

    【为什么按组配额】全按新鲜度排会被"行情价格(每日更新)"整组占满, 而库存/开工
    这类周度数据才是基本面分析的主驱动。
    """
    out, used = [], {}
    for m in _sort_modules(index):
        if m.get("n_series") in (0, None) and m.get("type") != "entity":
            continue  # 空表不注入
        g = m.get("group") or "其他"
        if used.get(g, 0) >= per_group:
            continue
        out.append(m)
        used[g] = used.get(g, 0) + 1
        if len(out) >= max_modules:
            break
    return out


def _top_series(obj: dict, limit: int = MAX_INJECT_SERIES) -> list[dict]:
    """表内挑最有信息量的序列: 按"最新数据日"降序, 同日后按点数降序。"""
    items = []
    for s in obj.get("series") or []:
        pts = series_points(obj, s.get("name"))
        if not pts:
            continue
        st = series_stats(pts)
        items.append({"name": s.get("name"), "unit": s.get("unit") or "", "stats": st})
    items.sort(key=lambda it: (it["stats"]["date"], it["stats"]["n"]), reverse=True)
    return items[:limit]


def _entity_lines(obj: dict, limit: int = 4) -> list[str]:
    """实体表(分炼厂排产/日产/远期结构/报告快照) → 最新期间的主要实体读数。"""
    axis = obj.get("axis") or []
    ents = obj.get("entities") or []
    if not axis or not ents:
        return []
    # 取最后一个有数值的期间列
    idx = None
    for i in range(len(axis) - 1, -1, -1):
        if any(_num((e.get("values") or [None] * len(axis))[i]) is not None
               for e in ents if i < len(e.get("values") or [])):
            idx = i
            break
    if idx is None:
        return []
    label = (axis[idx] or {}).get("label") or (axis[idx] or {}).get("date") or str(idx)
    rows = []
    for e in ents:
        vals = e.get("values") or []
        v = _num(vals[idx]) if idx < len(vals) else None
        if v is None:
            continue
        rows.append((e.get("name") or "", v))
    rows.sort(key=lambda kv: kv[1], reverse=True)
    lines = [f"最新期间 {label}: " + "; ".join(f"{n} {fmt_num(v)}" for n, v in rows[:limit])]
    if len(rows) > limit:
        lines.append(f"(共 {len(rows)} 个实体有数, 只列前 {limit}; 全部明细请调 get_variety_db)")
    return lines


# ── 注入块渲染(确定性, 供分析师系统提示前置) ─────────────────────────────

def render_variety_db_context(symbol: str, max_modules: int = MAX_INJECT_MODULES,
                              per_module: int = MAX_INJECT_SERIES,
                              root: Path | None = None) -> str:
    """品种库关键读数 → 分析师提示注入块; 该品种未导入数据库时返回 ""(不注入)。"""
    try:
        meta = resolve_variety(symbol, root)
        if not meta:
            return ""
        slug = str(meta.get("slug"))
        ent = _bundle(slug, root)
        index = ent["index"]
        if not index:
            return ""
        lines = [
            f"【品种数据库 · {meta.get('name')} {symbol}】本地导入的一手产业数据库快照。",
            "权重规则: 本库与公开接口(akshare/免费API)、研报转述覆盖同一指标同一时段时，"
            "以本库为准(用户私有导入数据, 可信度最高)。",
            f"库信息: {meta.get('name')}({slug}) | 源 {meta.get('source') or '-'} | "
            f"导入 {meta.get('imported_at') or '-'} | {meta.get('n_sheets') or len(index)} 模块 / "
            f"{meta.get('n_series') or '-'} 序列",
            "覆盖分组: " + "、".join(str(g) for g in (meta.get("groups") or [])) or "-",
        ]
        seen: set[str] = set()  # 【变量】已展示序列名: 跨模块去重(源表有同名镜像模块)
        for m in pick_modules(index, max_modules):
            obj = _sheet(slug, str(m.get("slug")), root)
            if not obj:
                continue
            rng = f"{m.get('date_min')} ~ {m.get('date_max')}" if m.get("date_max") else "无日期轴"
            head = f"── {m.get('group')} · {m.get('name')}"
            if obj.get("type") == "entity":
                lines.append("")
                lines.append(f"{head} [实体×周期, {m.get('n_series')} 个实体, {rng}]")
                lines.extend("   " + ln for ln in _entity_lines(obj))
                continue
            tops = [t for t in _top_series(obj, max(per_module * 3, per_module))
                    if t["name"] not in seen][:per_module]
            if not tops:
                continue  # 本模块没有新序列(与前面模块重复) → 整块跳过
            seen.update(t["name"] for t in tops)
            units = "、".join(sorted({t["unit"] for t in tops if t["unit"]})) or "无单位标注"
            lines.append("")
            lines.append(f"{head} [单位:{units}, {rng}]")
            for t in tops:
                lines.append(f"   · {t['name']}: {_fmt_stat(t['stats'])}")
        lines.append("")
        lines.append("【如何取更多】本块只是关键读数摘要。需要其它模块/指标/更长历史时调用 "
                     "`get_variety_db`(可传 keyword 关键词、group 分组、days 回看天数);"
                     "数据库缺数日不补零, 不要用插值或估计值替代。")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001  注入失败绝不影响分析主线
        return ""


# ── 工具检索(供 @tool get_variety_db 调用) ─────────────────────────────────

def _match_series(index: list[dict], slug: str, keyword: str, group: str,
                  module: str, root: Path | None) -> list[dict]:
    """在库内检索序列: 模块名或序列名命中关键词即入选; 返回带统计的条目列表。"""
    kw = (keyword or "").strip()
    gsel = (group or "").strip()
    msel = (module or "").strip().lower()
    hits: list[dict] = []
    for m in index:
        if gsel and gsel not in str(m.get("group") or ""):
            continue
        if msel and msel not in (str(m.get("name") or "").lower() + str(m.get("slug") or "").lower()):
            continue
        obj = _sheet(slug, str(m.get("slug")), root)
        if not obj:
            continue
        mod_hit = bool(kw) and kw in str(m.get("name") or "")
        if obj.get("type") == "entity":
            if mod_hit or (kw and any(kw in str(e.get("name") or "") for e in obj.get("entities") or [])):
                hits.append({"module": m, "entity": True, "obj": obj})
            continue
        for s in obj.get("series") or []:
            nm = str(s.get("name") or "")
            if kw and not (mod_hit or kw in nm):
                continue
            pts = series_points(obj, s.get("name"))
            if not pts:
                continue
            hits.append({"module": m, "name": nm, "unit": s.get("unit") or "",
                         "points": pts, "stats": series_stats(pts)})
    return hits


def query_variety_db(symbol: str, keyword: str = "", group: str = "", module: str = "",
                     days: int = 180, max_series: int = QUERY_MAX_SERIES,
                     root: Path | None = None) -> str:
    """按品种/关键词/分组/模块检索本地品种数据库, 返回可直接阅读的文本。

    【参数】symbol 期货代码(如 BU); keyword 指标关键词(如 库存/开工/裂差);
            group 分组名(如 库存仓单/炼厂排产); module 模块名或编号(如 s07);
            days 回看天数(0/None = 全部历史); max_series 返回序列数上限。
    【返回】文本读数; 未导入该品种 → `VARIETY_DB_NO_DATA: ...`;
            检索无命中 → `VARIETY_DB_NO_MATCH: ...`(附可用分组/模块清单)。
    【关键】只回真实数值, 缺数不补零; 天数裁剪只影响展示窗口, 分位/区间按窗口内点算。
    """
    try:
        meta = resolve_variety(symbol, root)
    except Exception as e:  # noqa: BLE001
        return f"VARIETY_DB_ERROR: {e}"
    if not meta:
        have = "、".join(f"{v.get('name')}({v.get('slug')})" for v in list_varieties(root)) or "空"
        return (f"VARIETY_DB_NO_DATA: 品种 {symbol} 未导入本地品种数据库(已导入: {have})。"
                "请勿编造该品种的库存/开工等产业数据。")
    slug = str(meta.get("slug"))
    ent = _bundle(slug, root)
    index = ent["index"]
    hits = _match_series(index, slug, keyword, group, module, root)
    if not hits:
        groups = "、".join(sorted({str(m.get("group")) for m in index}))
        mods = "; ".join(f"{m.get('slug')}={m.get('name')}({m.get('group')})" for m in index[:40])
        return (f"VARIETY_DB_NO_MATCH: {symbol} 库内没有匹配 keyword={keyword!r} group={group!r} "
                f"module={module!r} 的数据。可用分组: {groups}。可用模块(节选): {mods}")

    cutoff = None
    if days:
        try:
            cutoff = (date.today() - timedelta(days=int(days))).isoformat()
        except (TypeError, ValueError):
            cutoff = None

    lines = [f"【品种数据库查询】{meta.get('name')}({slug}) symbol={symbol} "
             f"keyword={keyword or '-'} group={group or '-'} "
             f"days={days or '全部'} 命中 {len(hits)} 条序列"
             f"{'(仅显示前 %d 条)' % max_series if len(hits) > max_series else ''}",
             f"源 {meta.get('source') or '-'} | 导入 {meta.get('imported_at') or '-'} | "
             f"缺数日不补零, 未显示的序列请调整 keyword/group 再查"]

    shown, seen_names = 0, set()
    for h in hits:
        if shown >= max(1, int(max_series or QUERY_MAX_SERIES)):
            break
        m = h["module"]
        if not h.get("entity") and h.get("name") in seen_names:
            continue  # 同名序列(源表镜像模块)只回一次
        if not h.get("entity"):
            seen_names.add(h["name"])
        if h.get("entity"):
            lines.append(f"\n── {m.get('group')} · {m.get('name')} [{m.get('slug')}, 实体×周期]")
            lines.extend("   " + ln for ln in _entity_lines(h["obj"], limit=8))
            shown += 1
            continue
        pts = h["points"]
        if cutoff:
            pts = [(d, v) for d, v in pts if d >= cutoff] or pts[-QUERY_POINTS_SHOWN:]
        pts = pts[-QUERY_WINDOW_MAX:]
        st = series_stats(pts) or h["stats"]
        unit = f" {h['unit']}" if h.get("unit") else ""
        lines.append(f"\n── {m.get('group')} · {m.get('name')} [{m.get('slug')}]{unit} · {h['name']}")
        lines.append("   " + _fmt_stat(st))
        recent = ", ".join(f"{d} {fmt_num(v)}" for d, v in pts[-QUERY_POINTS_SHOWN:])
        lines.append(f"   近{min(QUERY_POINTS_SHOWN, len(pts))}期: {recent}")
        shown += 1
    return "\n".join(lines)


# ── web 看板结构化快照(运行分析页"分析输入数据"卡) ───────────────────────

def variety_db_snapshot(symbol: str, max_modules: int = MAX_INJECT_MODULES,
                        per_module: int = MAX_INJECT_SERIES,
                        root: Path | None = None) -> dict:
    """品种库 → 结构化快照(web 看板渲染用): {available, note, name, modules:[...]}。

    【关键】任何异常都降级为 available=False + note, 绝不 500。
    """
    empty = {"available": False, "note": "", "slug": None, "name": None, "source": None,
             "imported_at": None, "symbols": [], "n_sheets": 0, "n_series": 0, "modules": []}
    try:
        meta = resolve_variety(symbol, root)
    except Exception as e:  # noqa: BLE001
        return {**empty, "note": f"DATA_ERROR: {e}"}
    if not meta:
        return {**empty, "note": f"NO_DATA_AVAILABLE: {symbol} 未导入本地品种数据库"}
    try:
        slug = str(meta.get("slug"))
        index = _bundle(slug, root)["index"]
        modules = []
        for m in pick_modules(index, max_modules):
            obj = _sheet(slug, str(m.get("slug")), root)
            if not obj:
                continue
            rows = []
            for t in _top_series(obj, per_module):
                st = t["stats"]
                rows.append({"name": t["name"], "unit": t["unit"], "date": st["date"],
                             "latest": st["latest"], "change": st.get("change"),
                             "pct": st.get("pct"), "pct_rank": st.get("pct_rank")})
            modules.append({"slug": m.get("slug"), "name": m.get("name"),
                            "group": m.get("group"), "type": m.get("type"),
                            "date_max": m.get("date_max"), "n_series": m.get("n_series"),
                            "rows": rows})
        return {**empty, "available": True,
                "slug": slug, "name": meta.get("name"), "source": meta.get("source"),
                "imported_at": meta.get("imported_at"), "symbols": meta.get("symbols") or [],
                "n_sheets": meta.get("n_sheets") or len(index),
                "n_series": meta.get("n_series") or 0, "modules": modules}
    except Exception as e:  # noqa: BLE001
        return {**empty, "note": f"DATA_ERROR: {e}", "slug": meta.get("slug"),
                "name": meta.get("name")}


def snapshot_text(symbol: str, root: Path | None = None) -> str:
    """诊断用: 品种库接入状态一行摘要(供脚本/测试快速核对)。"""
    meta = resolve_variety(symbol, root)
    if not meta:
        return f"{symbol}: NOT_IMPORTED"
    ctx = render_variety_db_context(symbol, root=root)
    return (f"{symbol}: {meta.get('slug')} / {meta.get('name')} / "
            f"modules={meta.get('n_sheets')} series={meta.get('n_series')} / "
            f"injected_chars={len(ctx)}")
