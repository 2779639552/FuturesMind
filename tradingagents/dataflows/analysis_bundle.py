"""analysis_bundle.py — 运行分析的「确定性数据快照」层（2026-10-19）。

【它解决什么】
  「数据仓库」页早就算好了国君四源（基差 / 交易所仓单 / 期货+社会库存 / 加工利润），
  但运行分析的四个分析师一次都没读过 —— 他们手里只有 `get_futures_basis` 这类
  走公开接口的工具。结果是：页面上有结论，报告里却像不知道这回事。

【为什么是"注入"而不是"再加一个 @tool"】
  见 skills/analyst-context-injection：**工具是否被调用由 LLM 自行决定，可能整轮一次都不调**。
  关键读数必须确定性拼进 system prompt；只有下钻（更长历史、其它口径）才交给工具。

【输出纪律（三条，缺一条就会误导）】
  1. **三态不可混**：`available=False` 是"取数失败"，`available=True & rows=0` 才是
     "确定无数据"。前者写"失败"，后者写"无覆盖"，**都不许写成 0**。
  2. **不补零 / 不插值 / 不用未来值**：序列里晚于今天的点直接丢弃（全量数据集可能含计划值），
     并在句尾注明被丢弃的个数。
  3. **静默降级**：任何异常都返回 ""（不注入），绝不让一个数据源把整次分析拖挂。

【性能】
  一次 `fetch_overview` 冷启动可能 6~18s（全量端点）+ 基差分片 ≈3s。
  ⇒ 进程内缓存 TTL 10 分钟；首次调用最多等 `WAIT_SECONDS`，
     超时就先返回 ""，同时留一个后台线程把结果灌进缓存（下次分析就命中）。
     同一品种同时只允许一个在途请求（单飞），避免并发分析把上游打爆。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ── 预算 ────────────────────────────────────────────────────────────────
MAX_CHARS = 1100          # 注入块上限（经验值：超过后模型开始跳读）
WAIT_SECONDS = 20.0       # 首次调用最多阻塞多久（超过就降级 + 后台补）
# 【2026-10-22 体检修正】原值 6.0 远低于 `fetch_overview` 冷启动（6~18s），
#   等于每次冷缓存都必然超时。基本面节点靠"排在技术分析师之后 60~120s"侥幸命中，
#   属于顺序巧合而非设计保证。取 20s 与 analyst_views 对齐。
CACHE_TTL = 600           # 进程内缓存 10 分钟（与 quant 的 overview 缓存同档）

_CACHE: dict[str, tuple[float, str]] = {}      # symbol -> (ts, text)
_EVENTS: dict[str, threading.Event] = {}       # symbol -> 在途完成事件
_LOCK = threading.Lock()

# 注入优先级：基差 > 仓单 > 库存 > 利润（截断时从后往前丢）
_SECTION_ORDER = ("basis", "warehouse", "inventory", "profit")


# ---------------------------------------------------------------------------
# 数值格式化（全部容错：拿不到就返回 None，由调用方决定写什么）
# ---------------------------------------------------------------------------
def _num(v: Any) -> str | None:
    """1234.5 → "1,234.5"；非数值 → None（不写 0，0 与"没数据"必须能区分）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    if abs(f) >= 1000:
        return f"{f:,.0f}" if abs(f) >= 10000 else f"{f:,.1f}".rstrip("0").rstrip(".")
    if abs(f) >= 10:
        return f"{f:.1f}".rstrip("0").rstrip(".")
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _pct(v: Any) -> str | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f"{f:.0f}%"


def _delta(series: list[list[Any]], back: int = 5) -> tuple[float | None, str | None]:
    """近 N 个点变化：返回 (百分比, 展示文本)。点数不足返回 (None, None)。

    ⚠ 用"点数"而不是"自然日"—— 库存/仓单有周末与假期缺口，按日历算会把
      3 天前当成 5 天前，变化率凭空放大。
    ⚠ 基数很小时百分比会失真（仓单 1,000 → 19,500 会显示 +1855%）：这种情况下
      改给**绝对变化量**，否则模型会把它当成"暴涨近 20 倍"的异常信号。
    """
    if not series or len(series) < back + 1:
        return None, None
    try:
        last = float(series[-1][1])
        prev = float(series[-1 - back][1])
    except (TypeError, ValueError, IndexError):
        return None, None
    diff = last - prev
    if prev == 0:
        return None, f"{diff:+,.0f}（基数为 0，不适用百分比）"
    p = diff / prev * 100
    if abs(p) > 300:
        return p, f"{diff:+,.0f}（{p:+.0f}%，基数小）"
    return p, f"{p:+.1f}%"


def _clean_series(series: list[Any], today: date) -> tuple[list[list[Any]], int]:
    """丢弃未来日期点（全量数据集可能含计划/预测值），返回 (清洗后序列, 丢弃个数)。"""
    kept: list[list[Any]] = []
    dropped = 0
    for p in series or []:
        try:
            d = str(p[0])[:10]
            if date.fromisoformat(d) > today:
                dropped += 1
                continue
            float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        kept.append([d, p[1]])
    kept.sort(key=lambda x: x[0])
    return kept, dropped


def _by_id(inds: list[dict[str, Any]], iid: str) -> dict[str, Any] | None:
    for i in inds:
        if i.get("id") == iid:
            return i
    return None


def _by_group(inds: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    return [i for i in inds if i.get("group") == group]


def _as_of_line(inds: list[dict[str, Any]]) -> str:
    """各源最新日期一行（MM-DD）。四源更新频率不同，不写日期会让模型误以为都是今天。"""
    inv = _by_group(inds, "库存")
    prf = _by_group(inds, "利润")
    pairs = (
        ("基差", _by_id(inds, "basis.value") or _by_id(inds, "basis.spot_price")),
        ("仓单", _by_id(inds, "warehouse.on_warrant")),
        ("库存", inv[0] if inv else None),
        ("利润", prf[0] if prf else None),
    )
    parts = []
    for name, ind in pairs:
        d = str((ind or {}).get("as_of") or "")[:10]
        if len(d) == 10:
            parts.append(f"{name} {d[5:]}")
    return ("数据截至：" + " · ".join(parts)) if parts else ""


def _three_state(sources: dict[str, Any]) -> str:
    """四源三态一行：ok / 无覆盖 / 失败。**失败与无覆盖必须分开写**。"""
    names = {"basis": "基差", "warehouse": "仓单", "inventory": "库存", "profit": "利润"}
    parts = []
    for k in ("basis", "warehouse", "inventory", "profit"):
        s = sources.get(k) or {}
        if not s.get("available"):
            parts.append(f"{names[k]} 失败")
        elif int(s.get("rows") or 0) <= 0:
            parts.append(f"{names[k]} 无覆盖")
        else:
            parts.append(f"{names[k]} ok")
    ok = sum(1 for p in parts if p.endswith("ok"))
    return f"三态：{' · '.join(parts)}（{ok}/4 可用）"


# ---------------------------------------------------------------------------
# 分节构建
# ---------------------------------------------------------------------------
def _basis_lines(inds: list[dict[str, Any]], today: date) -> list[str]:
    spot = _by_id(inds, "basis.spot_price")
    fut = _by_id(inds, "basis.contract_price")
    basis = _by_id(inds, "basis.value")
    if not (spot or basis):
        return []
    seg = []
    if spot and _num(spot.get("current")) is not None:
        seg.append(f"现货 {_num(spot.get('current'))}")
    if fut and _num(fut.get("current")) is not None:
        seg.append(f"期货 {_num(fut.get('current'))}")
    if basis and _num(basis.get("current")) is not None:
        cur = float(basis["current"])
        chg = _delta(_clean_series(basis.get("series") or [], today)[0], 1)[0]
        s = f"基差 {_num(cur)}"
        # 基差率自算：spot 为 0/缺失时不写 —— 接口的 basisPremiumRate 口径不明确
        # （TA 实测返回 0.08，而 530/7000≈7.6% ⇒ 它是小数，直接当 % 会差 100 倍）。
        try:
            spot_v = float((spot or {}).get("current"))
            if spot_v:
                s += f"（{cur / spot_v * 100:.2f}%）"
        except (TypeError, ValueError):
            pass
        if chg is not None:
            s += f"，较前值 {chg:+.1f}%"
        seg.append(s)
    line = "◆ 基差 " + " / ".join(seg) if seg else ""
    if not line:
        return []
    unit = (spot or basis or {}).get("unit") or ""
    pct = _pct((basis or spot or {}).get("pct"))
    tail = []
    if pct:
        tail.append(f"分位 {pct}")
    if unit:
        tail.append(f"单位 {unit}")
    if pct or unit:
        line += " | " + " | ".join(tail)
    # 方向提示（只解释符号，不替模型下结论）
    try:
        bv = float((basis or {}).get("current"))
        line += "（正=现货升水/期货贴水）" if bv > 0 else (
            "（负=现货贴水/期货升水）" if bv < 0 else "（平水）")
    except (TypeError, ValueError):
        pass
    return [line]


def _warehouse_lines(inds: list[dict[str, Any]], today: date) -> list[str]:
    wh = _by_id(inds, "warehouse.on_warrant")
    if not wh:
        return []
    ser, dropped = _clean_series(wh.get("series") or [], today)
    val = _num(wh.get("current"))
    if val is None:
        return []
    parts = [f"◆ 仓单 {val}"]
    _, chg5 = _delta(ser, 5)
    if chg5:
        parts.append(f"5日 {chg5}")
    pct = _pct(wh.get("pct"))
    if pct:
        parts.append(f"分位 {pct}")
    line = " | ".join(parts)
    if dropped:
        line += f"（另有 {dropped} 个未来日期点，未计入）"
    return [line]


def _infer_inventory_unit(ind: dict[str, Any]) -> str:
    """国君库存数据集的 `unit` 字段是空的，只能按**历史峰值**推断量级。

    【为什么用 hi 而不是 current】单点值会随周期大幅波动（螺纹钢社库 400~2,200），
      拿当前值判断容易在低位把它当成"吨"。历史峰值稳定得多。
    【判据】峰值 < 10000 → 万吨级（螺纹钢社库峰值约 2,200 万吨）；
      否则 → 吨（TA/LC 社库都是几十万吨量级，写成 1,385,241 吨）。
    ⚠ 这是**推断**，调用方必须在 note 里写上"单位按历史峰值推断"，
      不能让它看起来像数据源给的确定信息。
    """
    try:
        hi = float(ind.get("hi"))
    except (TypeError, ValueError):
        return ""
    return "万吨" if hi < 10000 else "吨"


def _inventory_lines(inds: list[dict[str, Any]], today: date) -> list[str]:
    items = _by_group(inds, "库存")
    if not items:
        return []
    lines: list[str] = []
    changes: dict[str, float] = {}
    for ind in items[:3]:
        ser, dropped = _clean_series(ind.get("series") or [], today)
        val = _num(ind.get("current"))
        if val is None:
            continue
        label = ind.get("label") or "库存"
        # 【2026-10-23】库存原本不带单位，模型只能看到「1,385,241」这种裸数字；
        #   而各品种量级差一个数量级（RB 522 万吨 vs TA 138 万吨 = 1,385,241 吨），
        #   不给单位等于让模型自己猜。单位推断见 `_infer_inventory_unit`。
        unit_i = ind.get("unit") or _infer_inventory_unit(ind)
        seg = [f"{label} {val}" + (f" {unit_i}" if unit_i else "")]
        p, desc = _delta(ser, 5)
        if desc:
            seg.append(f"5日 {desc}")
            changes[label] = p
        pct = _pct(ind.get("pct"))
        if pct:
            seg.append(f"分位 {pct}")
        line = "◆ " + " | ".join(seg)
        if dropped:
            line += f"（另有 {dropped} 个未来日期点，未计入）"
        lines.append(line)
    # 双口径背离：期货库存（交割库）与社会库存（产业链）方向相反
    fut_key = next((k for k in changes if "期货" in k), None)
    soc_key = next((k for k in changes if "社会" in k or "工厂" in k), None)
    if fut_key and soc_key and changes[fut_key] * changes[soc_key] < 0:
        if changes[fut_key] < 0:
            hint = "库存向交割库转移（现货偏紧）"
        else:
            hint = "库存从交割库回流产业链（现货转松）"
        lines.append(f"  ⚠ 双口径背离：{fut_key} {changes[fut_key]:+.1f}% vs "
                     f"{soc_key} {changes[soc_key]:+.1f}% → {hint}")
    return lines


def _profit_lines(inds: list[dict[str, Any]], today: date) -> list[str]:
    items = _by_group(inds, "利润")
    if not items:
        return []
    lines = []
    for ind in items[:2]:
        val = _num(ind.get("current"))
        if val is None:
            continue
        label = ind.get("label") or "利润"
        seg = [f"{label} {val}"]
        pct = _pct(ind.get("pct"))
        if pct:
            seg.append(f"分位 {pct}")
        lines.append("◆ " + " | ".join(seg))
    return lines


# ---------------------------------------------------------------------------
# 数据卡：把读数从「文本」升级为「带口径的结构化事实」（2026-10-22）
# ---------------------------------------------------------------------------
# 【为什么需要】
#   文本块能让模型读懂一个数，却没告诉它**两个数能不能相减**。
#   2026-10-22 实测（TA）：模型在报告里自己写下了
#     「注入块 7.89%（以现货价为分母） vs API 8.56%（以期货价为分母）——
#       同一绝对值、分母口径不同，不可混比」
#   它推对了，但**是靠猜**。系统侧没有任何字段告诉它这件事。
#   数据卡把「来源 / 时点 / 算法 / 可与谁比 / 不可与谁比 / 怎么换算」写成字段，
#   模型不必再猜，研究内容检查节点也能据此判断"结论引用的口径对不对"。
#
# 【和文本块的关系】
#   同一份 overview、同一套取数，只是多一层结构化投影。文本块照旧注入提示词
#   （模型读文本最自然），数据卡供前端展示与机器校验。**两者绝不各算一套**。
_CARDS: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # symbol -> (ts, cards)

# 库存/利润的 id 归一：中文 label → 稳定英文后缀（报告/前端按 id 引用）
_INV_SLUGS = (("社会", "social"), ("工厂", "factory"), ("期货", "futures"),
              ("交易所", "futures"), ("港口", "port"))
_PROFIT_SLUGS = (("加工", "processing"), ("盘面", "board"), ("现货", "spot"))


def _slug(label: str, table: tuple[tuple[str, str], ...], idx: int) -> str:
    for key, slug in table:
        if key in label:
            return slug
    return f"other{idx}"


def _card(
    cid: str,
    label: str,
    value: float | None,
    *,
    unit: str = "",
    source: str = "GTJA",
    as_of: str = "",
    method: str = "",
    pct: Any = None,
    grade: str = "A",
    kind: str = "",
    not_comparable_with: tuple[str, ...] = (),
    convertible_to: dict[str, str] | None = None,
    note: str = "",
) -> dict[str, Any] | None:
    """构造一张数据卡；数值缺失返回 None（调用方跳过，**不写 0 占位**）。"""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    card: dict[str, Any] = {
        "id": cid,
        "label": label,
        "value": v,
        "display": _num(v),
        "unit": unit,
        "source": source,
        "as_of": str(as_of or "")[:10],
        "method": method,
        "grade": grade,          # A=交易所/国君原始  B=第三方或外部常用口径
        "kind": kind,            # price / price_diff / pct / qty
    }
    # 【时效】数据日期距今天数。库存/利润是周频，卡片上的 as_of 可能比价格落后十几天，
    #   不写出来模型会用"今天的库存"口吻描述 12 天前的数（本轮实测：社会库存 10-09，
    #   基差 10-21）。这条与提示词里的「频率纪律」配合使用。
    try:
        age = (date.today() - date.fromisoformat(card["as_of"])).days
        if age >= 0:
            card["age_days"] = age
    except ValueError:
        pass
    try:
        p = float(pct)
        if p == p:
            card["pct"] = p
    except (TypeError, ValueError):
        pass
    # 【自指过滤】`not_comparable_with` 是手写清单，按 id 前缀共用；必须剔除自身，
    #   否则会出现「社会库存不可与自身比较」这种噪音。
    peers = [x for x in not_comparable_with if x != cid]
    if peers:
        card["not_comparable_with"] = peers
    if convertible_to:
        card["convertible_to"] = convertible_to
    if note:
        card["note"] = note
    return card


def _series_meta(ind: dict[str, Any] | None, today: date) -> tuple[str, Any]:
    """从指标里取 (as_of, 干净序列)。"""
    ind = ind or {}
    ser, _dropped = _clean_series(ind.get("series") or [], today)
    as_of = ser[-1][0] if ser and isinstance(ser[-1], (list, tuple)) else (ind.get("as_of") or "")
    return str(as_of or "")[:10], ser


def build_cards(symbol: str) -> list[dict[str, Any]]:
    """四源读数 → 结构化数据卡（与 `build_block` 同源同口径，绝不各算一套）。

    【口径纪律】同一物理量的不同表达（绝对值 / 现货分母基差率 / 期货分母基差率）
      必须**并列成不同卡**并互相标注 `not_comparable_with`，不允许只给一个值
      然后让模型自己猜分母。
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    now = time.time()
    hit = _CARDS.get(sym)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    payload = _overview(sym)
    if not payload or payload.get("ok") is not True:
        return []
    inds = payload.get("indicators") or []
    today = date.today()
    cards: list[dict[str, Any]] = []

    # ── 基差族 ─────────────────────────────────────────────────────────
    spot = _by_id(inds, "basis.spot_price")
    fut = _by_id(inds, "basis.contract_price")
    basis = _by_id(inds, "basis.value")
    unit = (spot or basis or {}).get("unit") or ""
    as_of_spot, _ = _series_meta(spot, today)
    as_of_fut, _ = _series_meta(fut, today)
    as_of_basis, _ = _series_meta(basis, today)

    spot_v = (spot or {}).get("current")
    fut_v = (fut or {}).get("current")
    basis_v = (basis or {}).get("current")

    for card in (
        _card("gtja.basis.spot", "现货价", spot_v, unit=unit, as_of=as_of_spot,
              method="国君 basisData 现货价",
              not_comparable_with=("gtja.basis.abs", "gtja.basis.rate_spot"),
              note="价格本身；与基差（价差）量纲不同，不可互相替代"),
        _card("gtja.basis.fut", "期货价（参考合约）", fut_v, unit=unit, as_of=as_of_fut,
              method="国君 basisData 参考合约价",
              not_comparable_with=("gtja.basis.abs", "gtja.basis.rate_fut")),
        _card("gtja.basis.abs", "基差（绝对值）", basis_v, unit=unit, as_of=as_of_basis,
              method="现货价 − 期货价（正=现货升水/期货贴水）", pct=(basis or {}).get("pct"),
              kind="price_diff",
              not_comparable_with=("gtja.basis.spot", "gtja.basis.rate_spot", "gtja.basis.rate_fut"),
              note="价差有单位（元/吨），不能与百分比直接比较"),
    ):
        if card:
            cards.append(card)

    # 基差率：**两个分母并列** —— 这是本项目最容易出错的一处
    try:
        s = float(spot_v)  # type: ignore[arg-type]
        b = float(basis_v)  # type: ignore[arg-type]
        if s:
            cards.append(_card(
                "gtja.basis.rate_spot", "基差率（现货分母）", b / s * 100.0,
                unit="%", as_of=as_of_basis, method="基差 ÷ 现货价 × 100",
                kind="pct", grade="A",
                not_comparable_with=("gtja.basis.abs", "gtja.basis.rate_fut"),
                convertible_to={"gtja.basis.rate_fut": "value × 现货价 ÷ 期货价"},
                note="**本系统注入块采用此口径**；与期货分母口径同绝对值但分母不同，不可混比",
            ))
    except (TypeError, ValueError):
        pass
    try:
        f = float(fut_v)  # type: ignore[arg-type]
        b = float(basis_v)  # type: ignore[arg-type]
        if f:
            cards.append(_card(
                "gtja.basis.rate_fut", "基差率（期货分母）", b / f * 100.0,
                unit="%", as_of=as_of_basis, method="基差 ÷ 期货价 × 100",
                kind="pct", grade="B", source="外部/研报常用",
                not_comparable_with=("gtja.basis.abs", "gtja.basis.rate_spot"),
                convertible_to={"gtja.basis.rate_spot": "value × 期货价 ÷ 现货价"},
                note="研报与部分接口用此口径；本系统不自算用于结论，仅登记以便识别外部引用",
            ))
    except (TypeError, ValueError):
        pass

    # ── 仓单 ───────────────────────────────────────────────────────────
    wh = _by_id(inds, "warehouse.on_warrant")
    if wh:
        as_of_wh, wh_ser = _series_meta(wh, today)
        _p, chg5 = _delta(wh_ser, 5)
        cards.append(_card(
            "gtja.warehouse.qty", "交易所注册仓单", wh.get("current"),
            unit=wh.get("unit") or "吨",   # 仓单是吨/手，绝不能继承基差的「元/吨」
            as_of=as_of_wh, method="国君 fut.warehouseStock 注册仓单量",
            pct=wh.get("pct"), kind="qty",
            not_comparable_with=("gtja.inventory.futures",),
            note=("5日变化 " + chg5) if chg5 else "",
        ))

    # ── 库存（多口径并列，不可相加） ──────────────────────────────────
    for i, ind in enumerate(_by_group(inds, "库存")[:3]):
        label = str(ind.get("label") or "库存")
        slug = _slug(label, _INV_SLUGS, i)
        as_of_i, ser_i = _series_meta(ind, today)
        _p, chg5 = _delta(ser_i, 5)
        # 单位：指标自带优先；国君数据集的 unit 是空的 ⇒ 按历史峰值推断，
        # 并在 note 里标明「推断」——不能让它看起来像数据源给的确定信息。
        unit_i = ind.get("unit") or _infer_inventory_unit(ind)
        unit_note = "" if ind.get("unit") else "（单位按历史峰值推断）"
        cards.append(_card(
            f"gtja.inventory.{slug}", label, ind.get("current"),
            unit=unit_i,
            as_of=as_of_i,
            method=f"国君周度库存（{label}）",
            pct=ind.get("pct"), kind="qty",
            grade="B" if slug == "social" else "A",
            not_comparable_with=("gtja.inventory.social", "gtja.inventory.futures", "gtja.warehouse.qty")
            if slug in ("social", "futures") else (),
            note=(("5日变化 " + chg5) if chg5 else "") + unit_note,
        ))

    # ── 利润 ───────────────────────────────────────────────────────────
    for i, ind in enumerate(_by_group(inds, "利润")[:2]):
        label = str(ind.get("label") or "利润")
        slug = _slug(label, _PROFIT_SLUGS, i)
        as_of_p, _ = _series_meta(ind, today)
        cards.append(_card(
            f"gtja.profit.{slug}", label, ind.get("current"),
            unit=ind.get("unit") or "元/吨", as_of=as_of_p,
            method=f"国君加工利润（{label}）", pct=ind.get("pct"), kind="qty",
            note="利润是慢变量，只作价差结构校验，不单独构成方向",
        ))

    final = [c for c in cards if c]
    _CARDS[sym] = (time.time(), final)
    return final


# ---------------------------------------------------------------------------
# 对外：构建 + 缓存 + 单飞
# ---------------------------------------------------------------------------
_PAYLOAD: dict[str, tuple[float, dict[str, Any]]] = {}  # symbol -> (ts, overview)


def _overview(symbol: str) -> dict[str, Any] | None:
    """取国君全量 overview（进程内 5min 缓存）。

    【为什么要单独一层】`build_block`（文本注入块）与 `build_cards`（结构化数据卡）
      必须消费**同一份**读数 —— 各自调一次 `fetch_overview` 不仅白费一次磁盘缓存读取，
      更危险的是两次结果若在窗口边缘取到不同快照，文本与卡片会对不上号。
    """
    now = time.time()
    hit = _PAYLOAD.get(symbol)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        import gtja_module as G  # 【调用包】聚合层（延迟导入，避免拖慢主模块）

        payload = G.fetch_overview(symbol)
    except Exception as exc:  # noqa: BLE001 - 数据源异常绝不影响分析
        logger.warning("GTJA overview 取数失败(%s): %s", symbol, exc)
        return None
    _PAYLOAD[symbol] = (time.time(), payload)
    return payload


def _caliber_hint(cards: list[dict[str, Any]]) -> str:
    """把数据卡里最要紧的两条口径压成**一行**，追加到注入块末尾。

    【为什么不把卡片整段塞进提示词】块预算 1100 字，整表 8 张卡约 600 字，会挤掉读数。
      所以只摘要两条最容易出错的：
        ① 基差率有两个分母（现货/期货）—— 2026-10-22 实测模型靠猜才发现不可混比；
        ② 周频指标的数据日期 —— 社会库存可能比价格落后十几天，不写会被当成"今天的库存"。
    """
    if not cards:
        return ""
    by_id = {c["id"]: c for c in cards}
    bits: list[str] = []
    spot = by_id.get("gtja.basis.rate_spot")
    fut = by_id.get("gtja.basis.rate_fut")
    if spot and fut:
        bits.append(
            f"基差率有两个分母：现货分母 {spot['display']}% / 期货分母 {fut['display']}%，"
            f"引用时必须注明分母（两者不可直接比较，可按 现货价/期货价 换算）"
        )
    # 年龄最大的**数量型**卡（库存/仓单/利润才是周频；价格是日频，不能标周频）
    stale = max(
        (c for c in cards
         if c.get("kind") == "qty" and (c.get("age_days") or 0) >= 3),
        key=lambda c: c["age_days"],
        default=None,
    )
    if stale:
        bits.append(
            f"{stale['label']}截至 {stale['as_of']}（滞后 {stale['age_days']} 天，周频），"
            f"不可当作当日数据"
        )
    if not bits:
        return ""
    return "◆ 口径提示：" + "；".join(bits)


def build_block(symbol: str) -> str:
    """同步构建注入块（异常/无数据 → ""）。**不要直接调用**，用 `gtja_context`。"""
    sym = (symbol or "").strip().upper()
    if not sym:
        return ""
    payload = _overview(sym)
    if not payload or payload.get("ok") is not True:
        return ""

    inds = payload.get("indicators") or []
    sources = payload.get("sources") or {}
    today = date.today()

    sections: dict[str, list[str]] = {
        "basis": _basis_lines(inds, today),
        "warehouse": _warehouse_lines(inds, today),
        "inventory": _inventory_lines(inds, today),
        "profit": _profit_lines(inds, today),
    }
    if not any(sections.values()):
        return ""  # 四源全空：不注入（与品种库未导入同一处理）

    head = [
        f"【国君四源 · {today.isoformat()}（确定性注入，零工具成本）】",
        "权重：与公开接口/研报同口径冲突时，以本块为准（交易所/国君原始口径）；"
        "指标不同名则不可直接比较。",
        _three_state(sources),
    ]
    as_of = _as_of_line(inds)
    if as_of:
        head.append(as_of)
    body: list[str] = []
    for key in _SECTION_ORDER:
        body.extend(sections[key])

    text = "\n".join(head + body)
    # 超长：从最不重要的分节开始丢（利润 → 库存 → 仓单），最后才硬截断
    for key in ("profit", "inventory", "warehouse"):
        if len(text) <= MAX_CHARS:
            break
        if sections[key]:
            body = [ln for ln in body if ln not in sections[key]]
            sections[key] = []
            text = "\n".join(head + body)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip() + "\n…（已截断）"
    # 【口径提示】放在截断之后追加 —— 它是"怎么用这个数"的说明，被截掉就等于没写。
    #   与数据卡同源（`build_cards` 复用同一份 `_overview`，不会二次取数）。
    hint = _caliber_hint(build_cards(sym))
    if hint:
        text = f"{text}\n{hint}"
    return text


def _timeout_block(symbol: str) -> str:
    """超时降级块：明确写「本次缺席」，避免模型把「没取到」当成「没有这回事」。"""
    return "\n".join([
        f"【国君四源 · {date.today().isoformat()}（确定性注入，零工具成本）】",
        f"◆ ⚠ 本次取数超时（等待 {WAIT_SECONDS:.0f}s 未算完），四源读数**未纳入**。",
        "  这不是「无数据」，也不是「数据为零」——是本该有的这一节缺席。",
        "  请在报告里如实写「本次无四源读数」，不要用 0 或中性顶替；"
        "需要时可调 get_futures_basis / get_futures_inventory 自取。",
    ])


def gtja_context(symbol: str) -> str:
    """基本面分析师的国君四源注入块；失败/未配置/超时 → ""（静默降级）。

    【为什么要单飞 + 后台补】一次 `fetch_overview` 冷启动 6~18s，而这是**构造提示**时
      同步调用 —— 不能让用户的一次点击卡在这里。故：首次最多等 WAIT_SECONDS，
      超时先返回 ""（本次不注入），后台线程把结果写进缓存，下一次分析直接命中。
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return ""

    now = time.time()
    hit = _CACHE.get(sym)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    with _LOCK:
        ev = _EVENTS.get(sym)
        owner = ev is None
        if owner:
            ev = threading.Event()
            _EVENTS[sym] = ev

    if not owner:
        # 已有一次在途：等它，但别等太久
        finished = ev.wait(WAIT_SECONDS)
        hit = _CACHE.get(sym)
        if hit:
            return hit[1]
        return "" if finished else _timeout_block(sym)

    def _work() -> None:
        try:
            text = build_block(sym)
            _CACHE[sym] = (time.time(), text)
        except Exception as exc:  # noqa: BLE001
            # 【旧版缺这一步】异常时事件会 set、缓存却不写 ⇒ 之后每次调用都重新起线程，
            # "记住失败"退化成"每次重试"。失败也写缓存（空串 = 本次确定拿不到）。
            logger.warning("GTJA 注入块构建异常(%s): %s", sym, exc)
            _CACHE[sym] = (time.time(), "")
        finally:
            with _LOCK:
                _EVENTS.pop(sym, None)
            ev.set()

    threading.Thread(target=_work, name=f"gtja-ctx-{sym}", daemon=True).start()
    finished = ev.wait(WAIT_SECONDS)
    hit = _CACHE.get(sym)
    if hit:
        return hit[1]
    if finished:
        return ""  # 算完但无内容 / 构建异常 = 确定不注入
    # 还在算：必须写明"缺席"，不能静默返回空串（三态纪律）
    logger.warning("GTJA 注入块首调超时(%s): 等待 %.1fs 未算完，本次不注入", sym, WAIT_SECONDS)
    return _timeout_block(sym)


def clear_cache() -> None:
    """清空进程内缓存（测试/排查用）。三层一起清，否则测试间会串味。"""
    with _LOCK:
        _CACHE.clear()
        _EVENTS.clear()
        _PAYLOAD.clear()
        _CARDS.clear()
