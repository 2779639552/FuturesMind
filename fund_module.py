"""fund_module.py — 基本面量化模块后端(Flask Blueprint)

【模块角色】(2026-09-29 新增) 与 `quant_module.py`(技术量化)并列的**基本面量化**支柱,
把顶级技能 `qihuo-duokong-logic`(《期货多空逻辑》仓颉蒸馏)中此前只落在研报问答文本块里、
或"有数据无前端"的能力, 收拢成 4 个可计算、可复算的子模块:

  ① 三指标策略系统   /api/fund/strategy        库存 + 基差 + 利润 → 单边/跨期四类策略
  ② 估值+驱动二维四象 /api/fund/value-drive     基差率(估值) × 库存分位(驱动) → 品种分类
                     /api/fund/value-drive/matrix  全池矩阵(截面, 供气泡图/筛选)
  ③ 加工/进口利润     (复用 /api/quant/profit)  多口径利润序列 + 历史分位
  ④ 虚实盘比与逼仓    /api/fund/virtual-real    (主力持仓÷2) ÷ 仓单折手数 + 两条安全边际

【方法论出处】`qihuo-duokong-logic` 能力卡:
  cap-inventory-basis-profit(三指标系统) / cap-value-drive(估值驱动四象) /
  cap-warrant-basis(虚实盘比逼仓) / cap-basis-calculation(基差口径与顺基差)。

【★ 基差符号口径 —— 全模块统一, 改动前必读】
  `dom_basis` = **现货 − 期货**(国君 basisValue 原样, 未做任何加工)。
  实测四品种钉死(2026-09-29): RU 18550−19010=−460 / CF 17252−15780=+1472 /
  TA 7350−6350=+1000 / M 3350−3359=−9, 四例均满足 basis == spot − futures。
  ⇒ **basis > 0 = 现货升水 = 期货贴水**(顺基差做多方向)
    **basis < 0 = 现货贴水 = 期货升水**(顺基差做空方向)
  ⚠️ 方法论中的"贴水/升水"主体一律是**期货**, 不是现货 —— 两者方向相反, 是历史事故点。
    (2026-09-29 已修正 quant_module._quant_signals / web_app._qa_quant_block 两处
     同源反向错误, 以及 quant_methodology.value_drive_matrix 的估值轴符号。)
  ⚠️ 输出文案必须带主体词("期货贴水"/"现货升水"), 禁止裸用"贴水/升水"。

【★ 仓单计量单位 —— 虚实盘比的地基, 错一个数量级不报错】
  仓单计数单位**各交易所不统一**(项目内权威表 warrant/history.py UNIT_MAIN):
    郑商所=张 / 大商所=手 / 广期所=手 / 上期所(含能源中心)=吨或桶。
  折合约手数必须按交易所换算, 实证依据:
    · 郑商所: 采集产物含**每张仓单实物吨数**(如棉花 spec「8手合约*5吨/手=40吨」)。
      ⇒ 折手系数 = 每张吨数 ÷ 合约乘数 ⇒ 棉花 = 40÷5 = **8**(1 张 = 8 手),
        红枣/PTA/对二甲苯 = 5÷5 = 1。若不换算, 棉花虚盘比会**高估 8 倍**。
    · 大商所/广期所: 仓单以「手」计, 1 手仓单 = 1 手合约标的 ⇒ 系数 1。
    · 上期所/能源中心: 以「吨」(原油为「桶」)计, 除以合约乘数。
      原油 1000 桶/手 ⇒ 系数 0.001, 若不换算虚盘比会**低估 1000 倍**。
    (审计脚本 probe/warrant_unit_audit.py, 实测 21/21 品种可解析。)

【设计约束】(与 quant_module 一致)
  - 纯读接口: 不写库、不写任何文件(除本模块的进程内 TTL 缓存, 及 ppi_basis 的落盘基差缓存)。
  - 单块失败只降级该块(ok:False + error), 绝不 500; 前端逐卡显示空态原因。
  - 取数一律**复用 quant_module 的同名函数**, 不另起一套, 防"同品种两套口径"漂移。
  - 品种池严格取 ACTIVE_VARIETIES(21 个), 未知品种 400。

【★ 现货/基差数据源 —— 两源回退(2026-09-30 新增)】
  源 1 国君 GTJA basisData.query   —— 覆盖 15/21。
  源 2 生意社 100ppi(ppi_basis.py) —— 补 **FU 燃料油 / PX 对二甲苯 / BZ 纯苯**, 仅当源 1 空时启用。
  ⇒ 现货覆盖从 15/21 提升到 **18/21**(全池 21 品种中 3 个仍无)。
  ★ 两源 dom_basis 均为 **现货 − 期货**(已互校), row 结构与 field 名完全同构 ⇒ 前端零改动。
  仍无现货指数: **SC 原油 / LU 低硫燃料油 / CJ 红枣**(两源皆无) ⇒ 其估值轴不可用,
    页面在品种下拉、#f-source-badge、矩阵气泡 tooltip、矩阵"未评级品种"表四处如实标注来源。
  ⚠️ 不要把"无现货指数"与"取数失败"混为一谈: 前者是数据源边界(不可修),
    后者是故障(可修)。页面用 basis_source='none' 区分二者。
  ⚠️ 100ppi 只能**逐日**抓(一天一页), 180 天串行 ~92s ⇒ ppi_basis 用 8 并发 + 落盘增量缓存,
    次日只补新日期, 实测首次 15~22s、二次 <0.1s。改动并发数前先看 _PARALLEL 注释。
"""

from __future__ import annotations

import glob  # 【调用包】郑商所规格文件枚举
import json  # 【调用包】读采集产物 JSON
import logging  # 【调用包】降级告警
import os  # 【调用包】路径拼接
import re  # 【调用包】合约月份/规格正则解析
import threading  # 【调用包】缓存加锁
import time  # 【调用包】TTL 缓存时间戳
from typing import Any  # 【调用包】类型标注

from flask import Blueprint, jsonify, request
import vote_semantics as _vs  # 【调用模块】方向票语义唯一权威来源

logger = logging.getLogger(__name__)

fund_bp = Blueprint("fund", __name__)  # 【变量】基本面量化蓝图(web_app 一行注册)

# ── 进程内 TTL 缓存 ──────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}  # 【变量】key → (写入时间, 值)
_CACHE_LOCK = threading.Lock()  # 【变量】缓存读写锁
_TTL_UNIT_SPEC = 3600  # 【变量】郑商所规格缓存 1h(每日采集后才有变化)
_TTL_MATRIX = 600  # 【变量】全池估值矩阵缓存 10 min(21 品种 × 2 源, 避免重复全量拉)
_MATRIX_PARALLEL = 6  # 【变量】矩阵品种并发数(与 quant_module._RANK_PARALLEL 同思路)

# ── 仓单单位约定(项目权威表, 与 warrant/history.py UNIT_MAIN 同源) ───────────
_UNIT_MAIN = {"ZCE": "张", "DCE": "手", "GFEX": "手", "SHFE": "吨", "INE": "桶"}
# 【变量】交易所代码 → 四所仓单采集目录前缀(能源中心并入上期所目录)
_EX_TO_LOCAL = {"ZCE": "CZCE", "DCE": "DCE", "GFEX": "GFEX", "SHFE": "SHFE", "INE": "SHFE"}
# 【变量】品种代码 → 交易所日报里的代码(已知不一致处, 实测补: 郑商所 PTA 写作 PTA)
_CODE_OVERRIDE = {"TA": "PTA"}

_SPEC_CACHE: dict[str, Any] = {"ts": 0.0, "specs": {}}  # 【变量】郑商所逐品种规格缓存

# ── 现货/基差数据源中文名(供页面如实标注"这个品种的数来自哪") ────────────────
_SRC_CN = {"gtja": "国君 GTJA", "100ppi": "生意社 100ppi", "akshare": "akshare"}

# 【变量】两源皆无现货指数的品种(页面需显式标注"无现货口径", 见文件头)
# 注: SC/LU/CJ 为 2026-09-30 实测结论 —— 国君返回 0 行, 生意社 54 品种清单亦不含。
_NO_SPOT = ("SC", "LU", "CJ")

# 【变量】无交易所注册仓单口径的品种。
# ★ 2026-09-30 实测: 全池 21 品种**全部**取到仓单(122 点), 故本集合为空。
#   注: 旧注释写"EG 无"系过时信息(实测 EG 的 inv.ok=True/count=122), 已纠正。
_NO_WARRANT: tuple[str, ...] = ()


# ===========================================================================
# 通用工具(取数一律委托 quant_module, 避免两套口径)
# ===========================================================================
def _q():
    """延迟导入 quant_module(避免与 web_app 循环导入, 也避免启动即加载 akshare)。"""
    import quant_module

    return quant_module


def _code(raw: str | None) -> str:
    """归一化并校验品种代码(不在池中 → 空串, 调用方据此回 400)。"""
    return _q()._norm_code(raw)


def _meta(code: str) -> dict:
    """品种元数据(name/exchange/unit/sector_cn 等)。"""
    return _q()._meta(code)


def _pool() -> list[str]:
    """活跃品种池(排序后)。"""
    return _q()._pool()


def _multiplier(code: str) -> float | None:
    """从元数据 unit(如 '10吨/手' / '1000桶/手')解析**合约乘数**。"""
    m = re.search(r"([\d.]+)\s*[^\d/]+/手", str(_meta(code).get("unit", "")))
    return float(m.group(1)) if m else None


def _load_czce_specs() -> dict[str, str]:
    """读郑商所逐品种「仓单规格」(每张仓单实物吨数的唯一权威来源)。

    【为什么直读采集产物而不走 warrant.history】history.py 归一化 products 结构时
    只保留 qty/change, **丢弃了 spec 字段**; 而 spec(如「8手合约*5吨/手=40吨」)
    正是把「张」换算成「手」所必需。故此处直读 data/warrant/out/*_CZCE_product.json,
    按日期升序遍历、后写覆盖 ⇒ 等价于「取最新一次采集的非空规格」。
    【失败降级】目录不存在/文件损坏 → 返回已解析到的部分(可能为空), 不抛异常。
    """
    now = time.time()
    if now - float(_SPEC_CACHE["ts"]) < _TTL_UNIT_SPEC and _SPEC_CACHE["specs"]:
        return dict(_SPEC_CACHE["specs"])
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "warrant", "out")
    specs: dict[str, str] = {}
    for f in sorted(glob.glob(os.path.join(base, "*_CZCE_product.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                for row in json.load(fh):
                    c = str(row.get("product_code") or "").strip().upper()
                    if c and row.get("spec"):
                        specs[c] = str(row["spec"])
        except Exception as e:  # noqa: BLE001 - 单日文件异常不影响其它日
            logger.warning("郑商所规格读取失败(%s): %s", os.path.basename(f), e)
    if specs:
        _SPEC_CACHE["ts"], _SPEC_CACHE["specs"] = now, specs
    return dict(specs)


def _spec_ton(spec: str) -> float | None:
    """从郑商所规格串解析「每张仓单实物吨数」(如 '8手合约*5吨/手=40吨' → 40.0)。"""
    m = re.search(r"=\s*([\d.]+)", spec or "")
    return float(m.group(1)) if m else None


def _lot_factor(code: str) -> dict:
    """仓单数量 → **折合约手数**的换算系数(虚实盘比的地基)。

    【返回】{ok, factor, unit, exchange_cn, basis(依据说明)}
    【口径】factor = 仓单折手数 ÷ 仓单数量, 四类交易所分别处理:
      · 郑商所(张): factor = 每张吨数 ÷ 合约乘数  (棉花 40÷5 = 8)
      · 大商所/广期所(手): factor = 1             (1 手仓单即 1 手合约标的)
      · 上期所/能源中心(吨/桶): factor = 1 ÷ 合约乘数  (原油 1÷1000 = 0.001)
    【降级】郑商所规格缺失 / 合约乘数无法解析 → ok:False + 原因, 调用方如实展示而非猜。
    """
    meta = _meta(code)
    ex = str(meta.get("exchange") or "")
    unit = _UNIT_MAIN.get(ex, "?")
    mult = _multiplier(code)
    ex_cn = str(meta.get("exchange_cn") or ex)

    if ex == "ZCE":
        lcode = _CODE_OVERRIDE.get(code, code).upper()
        spec = _load_czce_specs().get(lcode, "")
        ton = _spec_ton(spec)
        if ton is None:
            return {"ok": False, "unit": unit, "exchange_cn": ex_cn, "mult": mult,
                    "error": f"郑商所规格缺失(未在采集产物中找到 {lcode} 的 spec), 无法把「张」折成手数"}
        if not mult:
            return {"ok": False, "unit": unit, "exchange_cn": ex_cn, "mult": None,
                    "error": "合约乘数缺失, 无法折算"}
        return {"ok": True, "factor": round(ton / mult, 8), "unit": unit, "exchange_cn": ex_cn,
                "mult": mult, "basis": f"郑商所规格「{spec}」→ 每张 {ton}吨 ÷ {mult}吨/手"}
    if ex in ("DCE", "GFEX"):
        return {"ok": True, "factor": 1.0, "unit": unit, "exchange_cn": ex_cn, "mult": mult,
                "basis": f"{ex_cn}仓单以「手」计, 1 手仓单 = 1 手合约标的"}
    if ex in ("SHFE", "INE"):
        if not mult:
            return {"ok": False, "unit": unit, "exchange_cn": ex_cn, "mult": None,
                    "error": "合约乘数缺失, 无法折算"}
        return {"ok": True, "factor": round(1.0 / mult, 8), "unit": unit, "exchange_cn": ex_cn,
                "mult": mult, "basis": f"{ex_cn}仓单以「{unit}」计 ÷ {mult}{unit}/手"}
    return {"ok": False, "unit": unit, "exchange_cn": ex_cn, "mult": mult,
            "error": f"未知交易所 {ex}, 无仓单单位约定"}


def _months_to_delivery(month: str) -> int | None:
    """合约月份串(如 '2701' / '2701C' / '2701P')距当前月份数(可为负=已过期)。

    【为什么要】虚盘比"无固定阈值, 需结合距交割远近"——临近交割时逼仓/交割压力
    才真正兑现; 远月合约的虚实比只是参考。故输出必须带这个距离。
    """
    m = re.match(r"^(\d{2})(\d{2})", str(month or ""))
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not (1 <= mm <= 12):
        return None
    from datetime import date

    today = date.today()
    return (2000 + yy - today.year) * 12 + (mm - today.month)


def _grade(rank: float | None, lo: float = 0.35, hi: float = 0.65) -> str:
    """把 0~1 分位映射成 低/中/高 三档(分位缺失 → '不明')。"""
    if rank is None:
        return "不明"
    return "低" if rank <= lo else ("高" if rank >= hi else "中")


def _pct_rank(series: list[float], cur: float) -> float | None:
    """cur 在 series 中的百分位(0~1);序列不足返回 None。"""
    vals = [v for v in series if v is not None]
    if len(vals) < 5 or cur is None:
        return None
    return round(sum(1 for v in vals if v <= cur) / len(vals), 4)


# ===========================================================================
# 模块元信息
# ===========================================================================
@fund_bp.route("/api/fund/meta")
def api_fund_meta():
    """基本面量化模块元信息(品种池 + 各子模块数据边界 + 逐品种现货源归属)。

    【返回】{ok, varieties:[{code,name,exchange,unit,sector,
                            spot_source,spot_source_cn}],
            sources:[{name,coverage,note}], modules:[{key,title,note}]}。
    【spot_source】该品种现货/基差的**实际来源**, 供页面如实标注:
      gtja=仅国君 / gtja+100ppi=国君为主+生意社兜底 / none=两源皆无现货指数。
    """
    try:
        import ppi_basis

        # ★ 标注用 FALLBACK_ACTIVE(兜底**真的在用**), 而非 supports(兜底**有能力**)。
        #   P 棕榈油在 100ppi 侧有冗余登记但国君正常 → 不能标成"生意社兜底"。
        ppi_support = set(getattr(ppi_basis, "FALLBACK_ACTIVE", []) or [])
        ppi_capable = {c for c in _pool() if ppi_basis.supports(c)}
    except Exception:  # noqa: BLE001 - 兜底源不可用时降级为"仅国君"
        ppi_support, ppi_capable = set(), set()

    items = []
    for c in _pool():
        m = _meta(c)
        if c in _NO_SPOT:
            src, src_cn = "none", "无现货指数(两源皆无)"
        elif c in ppi_support:
            src, src_cn = "gtja+100ppi", "国君 GTJA(主) / 生意社 100ppi(兜底)"
        elif c in ppi_capable:
            # 国君当前正常, 但生意社亦有覆盖(已登记冗余) → 如实说明"当前走国君"
            src, src_cn = "gtja", "国君 GTJA(生意社 100ppi 可兜底)"
        else:
            src, src_cn = "gtja", "国君 GTJA"
        items.append({"code": c, "name": m.get("name", c), "exchange": m.get("exchange_cn", ""),
                      "unit": m.get("unit", ""), "sector": m.get("sector_cn", ""),
                      "spot_source": src, "spot_source_cn": src_cn})
    # 【动态覆盖度】池扩容后 17/20 这类硬编码会失真, 改为按实际池与兜底集算。
    _n_pool = len(_pool())
    _n_spot = len([c for c in _pool() if c not in _NO_SPOT])
    _n_warrant = len([c for c in _pool() if c not in _NO_WARRANT])
    return jsonify({
        "ok": True,
        "varieties": items,
        "no_spot": list(_NO_SPOT),
        "sources": [
            {"name": "交易所注册仓单", "coverage": f"{_n_warrant}/{_n_pool}",
             "note": "国君 GTJA 优先, 四所官方兜底"},
            {"name": "基差/现货价", "coverage": f"{_n_spot}/{_n_pool}",
             "note": f"国君 GTJA 为主, 生意社 100ppi 兜底(补 {'/'.join(ppi_support)});"
                     f"{'/'.join(_NO_SPOT)} 两源皆无现货指数"},
            {"name": "加工/进口利润", "coverage": "按品种", "note": "国君 GTJA processProfitData"},
            {"name": "跨月合约持仓/报价", "coverage": f"{_n_pool}/{_n_pool}", "note": "akshare 全合约实时报价"},
            {"name": "主力连续日线", "coverage": f"{_n_pool}/{_n_pool}", "note": "akshare 主连 + 后复权"},
        ],
        "modules": [
            {"key": "strategy", "title": "① 三指标策略系统",
             "note": "库存 + 基差 + 利润 → 单边做多/做空 / 跨期正套/反套(指标矛盾转跨期)"},
            {"key": "value-drive", "title": "② 估值+驱动二维四象",
             "note": "基差率(估值) × 库存分位(驱动) → 品种分类与单边/对冲建议"},
            {"key": "profit", "title": "③ 加工/进口利润口径",
             "note": "多装置口径分别展示(不合并), 附历史分位"},
            {"key": "virtual-real", "title": "④ 虚实盘比与逼仓识别",
             "note": "(主力持仓÷2) ÷ 仓单折手数 + 两条安全边际铁律"},
        ],
    })


def _profit_summary(code: str) -> dict:
    """加工/进口利润的**汇总口径**(多装置口径 → 分位 → 三档; 口径互斥记"矛盾")。

    【为什么要抽出来】`/api/fund/strategy` 与 `/api/fund/profit` 都要"利润档位",
    若各写一遍, 同一品种在两处可能得到不同档位(实测风险: 一处取均值、一处取多数决)。
    故统一由本函数产出档位, 两接口共用。

    【返回】{ok, grade, grades:{口径:档}, pct:{口径:分位}, labels, latest, source, error?}
    【档位规则】各口径分位独立映射 低/中/高; 汇总时若"低"与"高"同时出现 → **矛盾**
      (不硬选一方, 因为不同装置口径确实可能给出相反信号); 否则取众数。
    """
    q = _q()
    out: dict[str, Any] = {"ok": False}
    try:
        pf = q._fetch_profit(code)
        if not (pf.get("ok") and pf.get("latest")):
            return {"ok": False, "error": pf.get("error") or "无加工/进口利润数据"}
        pcts = {k: v for k, v in (pf.get("pct") or {}).items() if v is not None}
        grades = {k: _grade(v) for k, v in pcts.items()}
        vals = list(grades.values())
        if not vals:
            return {"ok": False, "error": "利润无历史区间, 分位不可计算"}
        grade = "矛盾" if ("低" in vals and "高" in vals) else max(set(vals), key=vals.count)
        out = {"ok": True, "grade": grade, "grades": grades, "pct": pcts,
               "labels": pf.get("labels") or [], "latest": pf.get("latest") or {},
               "source": pf.get("source", ""), "count": pf.get("count")}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 利润汇总失败(%s): %s", code, e)
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


@fund_bp.route("/api/fund/profit")
def api_fund_profit():
    """③ 加工/进口利润(**前端友好**: 已按口径分组, 附档位)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, unit, grade, series:[{label,count,latest,date,pct,grade,
            points:[{date,value}]}], source, card}
    【为什么要包装】`/api/quant/profit` 返回的是长表(每行一条 {date,label,profit}),
    前端要自己分组; 本接口直接给出分组序列 + 每口径档位, 让前端只负责画线。
    两接口**同源**(都走 quant_module._fetch_profit 与 6h 缓存), 不存在口径差异。
    【口径提示】能化品种常有多装置口径(如 EG「石脑油制」/「合成气制」、M 现货/盘面压榨),
    视觉上**分色并列、不合并** —— 合并会掩盖装置间利润分化这个关键信息。
    """
    code = _code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    q = _q()
    meta = _meta(code)
    pf = q._fetch_profit(code)
    if not (pf.get("ok") and pf.get("rows")):
        return jsonify({"ok": True, "variety": code, "name": meta.get("name", code),
                        "unit": meta.get("unit", ""), "grade": "不明", "series": [],
                        "error": pf.get("error") or "无加工/进口利润数据",
                        "card": "qihuo-duokong-logic.cap-inventory-basis-profit"})

    # 按口径(label)分组, 保持后端返回的标签顺序
    by_label: dict[str, list[dict]] = {}
    for r in pf["rows"]:
        lb = str(r.get("label") or "利润")
        by_label.setdefault(lb, []).append({"date": r.get("date"), "value": r.get("profit")})

    summary = _profit_summary(code)
    grades = summary.get("grades") or {}
    pcts = summary.get("pct") or {}
    latest = pf.get("latest") or {}
    series = []
    for lb in (pf.get("labels") or sorted(by_label)):
        pts = by_label.get(lb) or []
        if not pts:
            continue
        series.append({
            "label": lb, "count": len(pts),
            "latest": (latest.get(lb) or {}).get("value"),
            "date": (latest.get(lb) or {}).get("date"),
            "pct": pcts.get(lb), "grade": grades.get(lb, "不明"),
            "points": pts,
        })
    return jsonify({
        "ok": True, "variety": code, "name": meta.get("name", code),
        "unit": meta.get("unit", ""), "grade": summary.get("grade", "不明"),
        "series": series, "source": pf.get("source", ""), "count": pf.get("count"),
        "grade_rule": "各口径分位独立映射 低/中/高; 低与高同时出现记「矛盾」(不硬选一方)。"
                      "低利润 = 亏损不可持续 → 偏多倾向; 高利润 = 刺激供给增量 → 偏空倾向。",
        "card": "qihuo-duokong-logic.cap-inventory-basis-profit",
    })


# ===========================================================================
# ① 三指标策略系统(库存 + 基差 + 利润)
# ===========================================================================
def _strategy_verdict(inv_pos: str, prof_pos: str, basis_side: str) -> dict:
    """三指标 → 四类策略(纯函数, 便于单测)。

    【输入】inv_pos: 低/中/高/矛盾/不明
            —— 2026-10-16 起由**双口径综合**给出(quant._inv_dual):
               低/中/高 = 社会库存与仓单同档或单口径;
               **矛盾 = 两口径背离(一低一高, 库存转移迹象)** ⇒ 下面 low_i/high_i
               两票都不成立, 自然**不会进单边判定**(这是刻意的, 勿加特例绕过)。
            prof_pos: 低/中/高/矛盾/不明(利润分位三档; 多口径口径冲突 → 矛盾)
            basis_side: 贴水/平水/升水/不明(**期货**口径)
    【映射】qihuo-duokong-logic / cap-inventory-basis-profit:
            低库存 + 低利润 + 深贴水 → 单边做多(高赔率 + 高胜率)
            高库存 + 高利润 + 高升水 → 单边做空
            低库存 + 高利润 + 深贴水 → 跨期正套(矛盾: 单边易频繁止损)
            高库存 + 低利润 + 高升水 → 跨期反套
    【返回】{ok, action, reason, bull, bear, partial(是否部分共振), degraded(缺哪维)}
    """
    low_i, high_i = inv_pos == "低", inv_pos == "高"
    low_p, high_p = prof_pos == "低", prof_pos == "高"
    disc, prem = basis_side == "贴水", basis_side == "升水"

    missing = []
    if inv_pos in ("不明", ""):
        missing.append("库存")
    if prof_pos in ("不明", ""):
        missing.append("利润")
    if basis_side in ("不明", ""):
        missing.append("基差")

    # 顺基差方向票(赔率) + 库存/利润票(胜率), 供"部分共振"计数
    bull = sum([low_i, low_p, disc])
    bear = sum([high_i, high_p, prem])

    if missing:
        return {"ok": False, "action": "降级", "bull": bull, "bear": bear,
                "reason": f"缺 {'、'.join(missing)} 数据, 三指标系统无法判定"
                          f"(该品种可能无现货指数或无利润口径)", "degraded": missing}

    if low_i and low_p and disc:
        return {"ok": True, "action": "单边做多", "bull": bull, "bear": bear, "partial": False,
                "reason": "三指标共振: 低库存 + 低利润(亏损不可持续) + 期货深贴水 → 高赔率 + 高胜率"}
    if high_i and high_p and prem:
        return {"ok": True, "action": "单边做空", "bull": bull, "bear": bear, "partial": False,
                "reason": "三指标共振: 高库存 + 高利润(刺激供给增量) + 期货高升水 → 向下修复压力"}
    if low_i and high_p and disc:
        return {"ok": True, "action": "跨期正套", "bull": bull, "bear": bear, "partial": False,
                "reason": "指标矛盾(低库存+贴水看多, 但高利润抑多) → 多近空远, 勿做单边"}
    if high_i and low_p and prem:
        return {"ok": True, "action": "跨期反套", "bull": bull, "bear": bear, "partial": False,
                "reason": "指标矛盾(高库存+升水看空, 但低利润托底) → 空近多远, 勿做单边"}

    n_same = max(bull, bear)
    if n_same >= 2:
        side = "偏多" if bull > bear else "偏空"
        return {"ok": True, "action": f"部分共振({n_same}/3 {side})", "bull": bull, "bear": bear,
                "partial": True,
                "reason": f"仅 {n_same}/3 项指向{side}, 无单边优势 → 轻仓试或等第三项确认"}
    return {"ok": True, "action": "未共振", "bull": bull, "bear": bear, "partial": True,
            "reason": "三指标未共振 → 无单边优势, 建议观望或降仓"}


@fund_bp.route("/api/fund/strategy")
def api_fund_strategy():
    """① 三指标策略系统(库存 + 基差 + 利润 → 四类策略)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, unit, inv:{...}, profit:{...}, basis:{...},
            verdict:{action, reason, ...}, card}
    【口径】三个指标各自独立取数、独立降级(缺哪个在 verdict.degraded 里点名)。
    """
    code = _code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    q = _q()
    meta = _meta(code)

    # ── 库存维度(★ 2026-10-16 双口径: 国君社会库存 + 交易所仓单 综合) ──
    # 【为什么不再只用仓单】仓单是**交割库口径**(可交割量), 社会库存是**产业链口径**;
    #   二者背离 = 库存向交割库转移。只用仓单会把"无注册仓单"错读成"库存极低"
    #   (实测 CJ 仓单归零 → 分位 0.042 → 判"低" ⇒ 三指标系统输出凭空偏多)。
    # 【★ 判定统一走 quant._inv_dual】两口径量纲不可比(实测 EG 社会库存 13 vs 仓单
    #   6226), 故**不在本文件另起一套比较逻辑** —— 只消费后端给的 grade/mode。
    inv_out: dict[str, Any] = {"ok": False}
    try:
        inv = q._fetch_inventory(code, 180)
        ck_rows = (inv.get("rows") or []) if inv.get("ok") else []
        dual = q._inv_dual(code, ck_rows, 180,
                           inv.get("source", "") if inv.get("ok") else "")
        ck = dual.get("ck") or {}
        soc = dual.get("social") or {}
        if dual.get("grade") != "不明":
            inv_out = {"ok": True,
                       "grade": dual["grade"], "mode": dual.get("mode", ""),
                       "dual_note": dual.get("note", ""), "warn": dual.get("warn"),
                       "available": dual.get("available", []),
                       # 下面几个键是**仓单口径**原值(口径与 ④ 卡一致), 保留是因为
                       # 既有消费端按它们取值; 判定请只看 grade/mode。
                       "latest": ck.get("latest"), "pct_rank": ck.get("pct_rank"),
                       "count": ck.get("count"), "source": ck.get("source", ""),
                       "ck": ck, "social": soc}
        else:
            inv_out = {"ok": False,
                       "error": ((ck.get("error") if not ck.get("ok") else "")
                                 or (soc.get("error") if not soc.get("ok") else "")
                                 or "库存双口径均无数据"),
                       "ck": ck, "social": soc, "warn": dual.get("warn")}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 库存取数失败(%s): %s", code, e)
        inv_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── 利润维度(多口径 → 分位 → 三档; 口径互相冲突记"矛盾") ──
    prof_out = _profit_summary(code)

    # ── 基差维度(★ 符号口径见文件头: >0 = 现货升水 = **期货贴水**) ──
    basis_out: dict[str, Any] = {"ok": False}
    try:
        b = q._fetch_basis(code, 60)
        if b.get("ok") and b.get("rows"):
            vals = [r["basis"] for r in b["rows"] if r.get("basis") is not None]
            rates = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None]
            if vals:
                cur = vals[-1]
                side = "贴水" if cur > 0 else ("升水" if cur < 0 else "平水")
                basis_out = {"ok": True, "latest": cur, "rate": (rates[-1] if rates else None),
                             "side": side, "side_cn": ("期货贴水(现货升水)" if cur > 0
                                                       else "期货升水(现货贴水)" if cur < 0 else "平水"),
                             "pct_rank": _pct_rank(vals, cur), "count": len(vals),
                             # 【来源透传】供页面标注"该品种基差来自哪一源"(见文件头两源回退链)
                             "source": b.get("source", "gtja"),
                             "source_cn": _SRC_CN.get(b.get("source", "gtja"), "国君 GTJA")}
            else:
                basis_out = {"ok": False, "error": "基差序列为空"}
        else:
            # 【如实透出】error 已含"已试 国君 → 生意社"; 另带诊断字段供页面区分
            # "两源无覆盖"(数据源边界) 与 "兜底源故障"(可修)。
            basis_out = {"ok": False,
                         "error": b.get("error") or "无基差数据(该品种无现货指数口径)",
                         "tried_sources": b.get("tried_sources", []),
                         "ppi_broken": bool(b.get("ppi_broken")),
                         "source": "none",
                         "source_cn": ("无现货指数(两源皆无)" if code in _NO_SPOT
                                       else "取数失败")}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 基差取数失败(%s): %s", code, e)
        basis_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    vd = _strategy_verdict(
        inv_out.get("grade", "不明") if inv_out.get("ok") else "不明",
        prof_out.get("grade", "不明") if prof_out.get("ok") else "不明",
        basis_out.get("side", "不明") if basis_out.get("ok") else "不明",
    )
    return jsonify({
        "ok": True, "variety": code, "name": meta.get("name", code),
        "unit": meta.get("unit", ""), "exchange": meta.get("exchange_cn", ""),
        "inv": inv_out, "profit": prof_out, "basis": basis_out, "verdict": vd,
        "card": "qihuo-duokong-logic.cap-inventory-basis-profit",
        "basis_sign_note": "基差 = 现货 − 期货: >0 为期货贴水(顺基差做多方向), <0 为期货升水",
    })


# ===========================================================================
# ② 估值 + 驱动 二维四象
# ===========================================================================
def _value_drive_one(code: str) -> dict:
    """单品种估值+驱动评分(供全池矩阵复用; 单品种与截面走同一实现防口径漂移)。"""
    q = _q()
    row: dict[str, Any] = {"variety": code, "name": _meta(code).get("name", code),
                           "sector": _meta(code).get("sector_cn", "")}
    try:
        b = q._fetch_basis(code, 180, batch=True)
        if b.get("ok") and b.get("rows"):
            rates = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None]
            row["basis_rate"] = rates[-1] if rates else None
            row["_rate_series"] = rates
            # 【来源标注】该品种基差实际来自哪一源(国君 / 生意社兜底), 供页面如实标注
            row["basis_source"] = b.get("source", "gtja")
            row["basis_source_cn"] = _SRC_CN.get(b.get("source", "gtja"), "国君 GTJA")
            row["basis_points"] = len(rates)
        else:
            row["basis_source"] = "none"
            row["basis_source_cn"] = "无现货指数" if code in _NO_SPOT else "取数失败"
            row["basis_err"] = b.get("error", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 矩阵-基差失败(%s): %s", code, e)
    try:
        inv = q._fetch_inventory(code, 180, batch=True)
        if inv.get("ok") and inv.get("rows"):
            qty = [r["qty"] for r in inv["rows"] if r.get("qty") is not None]
            row["inventory"] = qty[-1] if qty else None
            row["_inv_series"] = qty
            row["inventory_pct_rank"] = _pct_rank(qty, qty[-1]) if qty else None
            if len(qty) >= 4 and qty[-4]:
                row["inventory_chg_pct"] = round((qty[-1] / qty[-4] - 1) * 100, 2)
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 矩阵-库存失败(%s): %s", code, e)
    try:
        pf = q._fetch_profit(code)
        if pf.get("ok") and pf.get("latest"):
            pcts = [v for v in (pf.get("pct") or {}).values() if v is not None]
            if pcts:
                row["profit_pct"] = round(sum(pcts) / len(pcts), 4)  # 多口径等权均值(展示用)
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 矩阵-利润失败(%s): %s", code, e)

    # 二维四象(复用已修正估值轴符号的纯函数)
    try:
        import quant_methodology as qm

        vd = qm.value_drive_matrix(row.get("_rate_series") or [], row.get("_inv_series") or [],
                                   row.get("inventory_chg_pct"))
        row["value"] = vd.get("value")
        row["drive"] = vd.get("drive")
        row["quadrant"] = vd.get("quadrant")
        row["advice"] = vd.get("advice")
        row["vd_ok"] = bool(vd.get("ok"))
        if not vd.get("ok"):
            row["vd_reason"] = vd.get("reason")
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 矩阵-四象失败(%s): %s", code, e)
        row["vd_ok"] = False
        row["vd_reason"] = f"{type(e).__name__}: {e}"
    return row


@fund_bp.route("/api/fund/value-drive")
def api_fund_value_drive():
    """② 单品种估值+驱动二维四象(含两条轴的原始证据值)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, value, drive, quadrant, advice, evidence:{...}, note, card}
    """
    code = _code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    row = _value_drive_one(code)
    meta = _meta(code)
    ev = {"basis_rate": row.get("basis_rate"), "basis_rate_count": len(row.get("_rate_series") or []),
          "inventory": row.get("inventory"), "inventory_pct_rank": row.get("inventory_pct_rank"),
          "inventory_chg_pct": row.get("inventory_chg_pct"), "profit_pct": row.get("profit_pct"),
          "inventory_count": len(row.get("_inv_series") or [])}
    return jsonify({
        "ok": True, "variety": code, "name": meta.get("name", code),
        "sector": meta.get("sector_cn", ""), "unit": meta.get("unit", ""),
        "value": row.get("value"), "drive": row.get("drive"),
        "quadrant": row.get("quadrant"), "advice": row.get("advice"),
        "vd_ok": row.get("vd_ok"), "vd_reason": row.get("vd_reason"),
        "evidence": ev,
        "card": "qihuo-duokong-logic.cap-value-drive",
        "axis_note": "估值轴 = 基差率(越高=期货越便宜=估值越低, 越偏多); 驱动轴 = 库存分位反向"
                     "(库存越低=驱动向上)。二维四象是**品种分类工具, 不是择时工具**。",
    })


@fund_bp.route("/api/fund/value-drive/matrix")
def api_fund_value_drive_matrix():
    """② 全池估值+驱动矩阵(截面, 供二维气泡图与品种池筛选)。

    【参数】varieties=逗号分隔(默认全池); refresh=1 跳过缓存。
    【返回】{ok, count, cached, rows:[{variety,name,sector,value,drive,quadrant,advice,...}]}
    【性能】有界并发(6) + GTJA 短超时; 整体缓存 10 min。
    """
    raw = (request.args.get("varieties") or "").strip()
    pool = _pool()
    codes = [c for c in (x.strip().upper() for x in raw.split(",")) if c] if raw else pool
    codes = [c for c in codes if c in pool] or pool
    refresh = (request.args.get("refresh") or "").strip() in ("1", "true", "yes")

    key = "vdmatrix:" + ",".join(codes)
    cached = True
    if refresh:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        cached = False
    hit = _q()._cache_get(key, _TTL_MATRIX) if not refresh else None
    if hit is not None:
        rows = hit
    else:
        from concurrent.futures import ThreadPoolExecutor

        out: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=_MATRIX_PARALLEL) as ex:
            futs = {ex.submit(_value_drive_one, c): c for c in codes}
            for fut, c in futs.items():
                try:
                    out[c] = fut.result()
                except Exception as e:  # noqa: BLE001 - 单品种失败留空行
                    logger.warning("矩阵截面失败(%s): %s", c, e)
                    out[c] = {"variety": c, "name": _meta(c).get("name", c)}
        rows = [out[c] for c in codes if c in out]
        for r in rows:  # 内部序列不下发(体积大且前端不用), 但保留计数
            r.pop("_rate_series", None)
            r.pop("_inv_series", None)
        _q()._cache_set(key, rows)
    ok_n = sum(1 for r in rows if r.get("vd_ok"))
    return jsonify({"ok": True, "count": len(rows), "scored": ok_n, "cached": cached, "rows": rows})


# ===========================================================================
# ④ 虚实盘比与逼仓识别
# ===========================================================================
@fund_bp.route("/api/fund/virtual-real")
def api_fund_virtual_real():
    """④ 虚实盘比与逼仓识别(主力合约, 基于仓单 + 基差 + 持仓)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, ratio, ...}
    【口径】虚实盘比 =（**主力合约持仓量 ÷ 2**）÷ **仓单折手数**
            · 虚盘 = 单边持仓(手) = 主力持仓 ÷ 2(交易所公布持仓为双边)
            · 实盘 = 仓单折手数(手) = 仓单数量 × _lot_factor 的换算系数
            比值大 = 钱多货少 → 易涨(多逼空); 比值小 = 货多钱少 → 易跌(空逼多)。
    【三特征】多逼空: 期货贴水 + 仓单少(比值大) + 现货坚挺上涨
              空逼多: 期货升水 + 巨量仓单(比值小) + 现货疲软下跌
    【两条安全边际(作者铁律)】近月临近交割时:
              ① 基差维度: 期货升水不做多、期货贴水不做空;
              ② 虚实比维度: 虚实比大(可能多逼空)不做空、虚实比小(可能空逼多)不做多。
    【⚠️ 阈值说明】书中明确"无固定阈值, 需结合距交割远近"。本接口的 2.0/0.5 是
      经验档位, **仅用于把连续比值翻译成定性描述**, 不构成交易信号。
    """
    code = _code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    q = _q()
    meta = _meta(code)

    # ── 实盘: 仓单折手数 ──
    inv_out: dict[str, Any] = {"ok": False}
    try:
        inv = q._fetch_inventory(code, 180)
        if inv.get("ok") and inv.get("rows"):
            qty = [r["qty"] for r in inv["rows"] if r.get("qty") is not None]
            if qty:
                lf = _lot_factor(code)
                if lf.get("ok"):
                    lots = float(qty[-1]) * float(lf["factor"])
                    inv_out = {"ok": True, "qty": qty[-1], "unit": lf.get("unit"),
                               "lot_factor": lf["factor"], "lots": round(lots, 2),
                               "factor_basis": lf.get("basis", ""),
                               "source": inv.get("source", ""),
                               "pct_rank": _pct_rank(qty, qty[-1])}
                else:
                    inv_out = {"ok": False, "error": lf.get("error")}
            else:
                inv_out = {"ok": False, "error": "仓单序列为空"}
        else:
            inv_out = {"ok": False, "error": inv.get("error") or "无仓单数据"}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 虚实-仓单失败(%s): %s", code, e)
        inv_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── 虚盘: 主力合约持仓 ÷ 2 ──
    term_out: dict[str, Any] = {"ok": False}
    try:
        t = q._fetch_term_structure(code)
        if t.get("ok") and t.get("main"):
            main = t["main"]
            oi = main.get("oi")
            term_out = {"ok": True, "contract": main.get("contract"), "month": main.get("month"),
                        "oi": oi, "one_side_oi": (round(oi / 2, 2) if oi else None),
                        "price": main.get("price"), "shape": t.get("shape"),
                        "spread": t.get("spread"),
                        "months_to_delivery": _months_to_delivery(main.get("month"))}
        else:
            term_out = {"ok": False, "error": t.get("error") or "无跨月合约报价"}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 虚实-期限失败(%s): %s", code, e)
        term_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── 基差(判断升贴水 + 现货强弱) ──
    basis_out: dict[str, Any] = {"ok": False}
    try:
        b = q._fetch_basis(code, 60)
        if b.get("ok") and b.get("rows"):
            rows = b["rows"]
            vals = [r["basis"] for r in rows if r.get("basis") is not None]
            spots = [r["spot_price"] for r in rows if r.get("spot_price") is not None]
            if vals:
                cur = vals[-1]
                spot_trend = None
                if len(spots) >= 6 and spots[-6]:
                    spot_trend = "上涨" if spots[-1] > spots[-6] else ("下跌" if spots[-1] < spots[-6] else "走平")
                basis_out = {"ok": True, "latest": cur,
                             "side": "贴水" if cur > 0 else ("升水" if cur < 0 else "平水"),
                             "side_cn": ("期货贴水(现货升水)" if cur > 0
                                         else "期货升水(现货贴水)" if cur < 0 else "平水"),
                             "spot_trend": spot_trend,
                             "spot_latest": spots[-1] if spots else None}
            else:
                basis_out = {"ok": False, "error": "基差序列为空"}
        else:
            basis_out = {"ok": False, "error": b.get("error") or "无基差数据(该品种无现货指数口径)"}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 虚实-基差失败(%s): %s", code, e)
        basis_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── 合成 ──
    # 【2026-10-16】改调 _vr_ratio_of —— 让虚实盘比的算术**只有一处**(见其 docstring);
    #   ⑨ 截面表也要这个比值, 两处各写一遍迟早漂移。
    ratio = _vr_ratio_of(inv_out.get("lots") if inv_out.get("ok") else None,
                         term_out.get("one_side_oi") if term_out.get("ok") else None)

    # 三特征判定
    feats: list[dict] = []
    side = basis_out.get("side") if basis_out.get("ok") else None
    spot_tr = basis_out.get("spot_trend") if basis_out.get("ok") else None
    if ratio is not None:
        feats = [
            {"name": "期货贴水", "hit": side == "贴水", "detail": basis_out.get("side_cn") or "无基差数据",
             "note": "贴水是多逼空前提(期货低于现货, 有向上修复空间)"},
            {"name": "仓单少 / 虚实比大", "hit": ratio >= 2.0,
             "detail": f"虚实比 {ratio}" + (f" · 仓单窗口分位 {inv_out.get('pct_rank')}%" if inv_out.get("pct_rank") is not None else ""),
             "note": "钱多货少, 空头可交割货源不足(经验阈值 2.0)"},
            {"name": "现货坚挺上涨", "hit": spot_tr == "上涨",
             "detail": f"现货近期 {spot_tr or '不明'}", "note": "现货走强会压缩空头退路"},
        ]
        bear_feats = [
            {"name": "期货升水", "hit": side == "升水", "detail": basis_out.get("side_cn") or "无基差数据",
             "note": "升水是空逼多前提(期货高于现货)"},
            {"name": "巨量仓单 / 虚实比小", "hit": ratio <= 0.5,
             "detail": f"虚实比 {ratio}", "note": "货多钱少, 多头接货压力大(经验阈值 0.5)"},
            {"name": "现货疲软下跌", "hit": spot_tr == "下跌",
             "detail": f"现货近期 {spot_tr or '不明'}", "note": "现货走弱削弱多头信心"},
        ]
        bull_n = sum(1 for f in feats if f["hit"])
        bear_n = sum(1 for f in bear_feats if f["hit"])
        if bull_n >= 3:
            verdict, judge = "多逼空特征(三特征齐备)", "期货贴水 + 仓单少 + 现货坚挺 → 易涨, 勿做空"
        elif bear_n >= 3:
            verdict, judge = "空逼多特征(三特征齐备)", "期货升水 + 巨量仓单 + 现货疲软 → 易跌, 勿做多"
        elif bull_n == 2:
            verdict, judge = "偏多逼空(2/3 特征)", "指向多逼空但不齐备, 需再确认第三项"
        elif bear_n == 2:
            verdict, judge = "偏空逼多(2/3 特征)", "指向空逼多但不齐备, 需再确认第三项"
        else:
            verdict, judge = "无明显逼仓特征", "三特征均不齐备, 按常规供需逻辑判断"
    else:
        bull_n = bear_n = 0
        verdict, judge = "数据不足", "虚盘比需要同时具备仓单与主力合约持仓数据"

    # 两条安全边际(仅临近交割才具约束力)
    mtd = term_out.get("months_to_delivery") if term_out.get("ok") else None
    near = (mtd is not None and mtd <= 2)
    rules = [
        {"rule": "期货升水不做多", "hit": side == "升水", "dim": "基差",
         "note": "升水做多 = 逆基差, 拿到的是不利赔率"},
        {"rule": "期货贴水不做空", "hit": side == "贴水", "dim": "基差",
         "note": "贴水做空 = 逆基差, 交割时面临基差修复亏损"},
        {"rule": "虚实比大不做空", "hit": (ratio is not None and ratio >= 2.0), "dim": "虚实比",
         "note": "可能多逼空, 期现可不修复而直接升水交割(交不出货)"},
        {"rule": "虚实比小不做多", "hit": (ratio is not None and ratio <= 0.5), "dim": "虚实比",
         "note": "可能空逼多, 接货后转抛困难"},
    ]
    return jsonify({
        "ok": True, "variety": code, "name": meta.get("name", code),
        "unit": meta.get("unit", ""), "exchange": meta.get("exchange_cn", ""),
        "ratio": ratio, "inv": inv_out, "term": term_out, "basis": basis_out,
        "feats": feats, "bear_feats": (bear_feats if ratio is not None else []),
        "bull_hits": bull_n, "bear_hits": bear_n,
        "verdict": verdict, "judge": judge,
        "near_delivery": near, "months_to_delivery": mtd,
        "rules": rules,
        "threshold_note": "书中明确虚实盘比**无固定阈值**, 需结合距交割远近; 本页 2.0 / 0.5 "
                          "为经验档位, 仅用于把连续比值翻译成定性描述, 不构成交易信号。",
        "card": "qihuo-duokong-logic.cap-warrant-basis",
    })


def _fund_month_row(code: str, month: str) -> dict:
    """按**指定合约月**重算该品种的截面行（用户 2026-10-16 要求）。

    【为什么需要】截面表默认口径是"主力合约"：基差 = 现货 − **主力**价、虚实盘比用**主力**持仓。
      但**逼仓是月份特定的** —— 近月的虚实盘比常与主力差一个量级；顺基差/展期也要看具体月份。
      ⇒ 选中某月时, 凡**依赖合约**的读数必须按该月重算。

    【哪些随合约重算】
      · 基差 / 基差率 : 现货价 − **该月**期货价（现货价与合约无关, 只有期货腿换月）
      · 虚实盘比      : **该月**持仓÷2 ÷ 仓单折手数（仓单是总量, 与月份无关）
      · 综合结论      : 用重算后的基差侧 + 不变的库存/利润档位 → `_strategy_verdict`
      · 期限结构读数  : 另起 `term_anchor`（自该月往后的曲线形态）, **不覆盖原票**
    【哪些不动（它们本就不依赖合约）】
      库存双口径综合 / 仓单分位 / 仓单5日 / 社会库存分位 / 社会库存4期 / 利润档位 /
      库存周期票 / 利润票 / 期限结构票
    【★ 哪些**不可**按该月算 —— 如实置空并注明】
      基差分位 与 二维四象票：两者都需要**该月基差率的历史序列**, 而平台只提供
      「现货 − 主力」的历史(见 gtja_api 的 dom_basis)。**绝不用主力序列冒充**
      （用户 2026-10-16 明确选择"如实标 — 并注明"）。
    【口径纪律】本函数不新增判定口径：虚实比走 `_vr_ratio_of`、综合结论走 `_strategy_verdict`、
      四票走 `_fund_vote_inputs` → `_fund_skill_votes` → `build_verdict`。
    【返回】{ok, variety, name, month, contract, row:{只含被替换的字段}}
    """
    q = _q()
    meta = _meta(code)
    t = q._fetch_term_structure(code)
    if not t.get("ok"):
        return {"ok": False, "error": t.get("error") or "该品种无跨月合约报价"}
    rows = t.get("rows") or []
    m = (month or "").strip()
    sel = next((r for r in rows if str(r.get("month")) == m), None)
    if sel is None:
        avail = "/".join(str(r.get("month")) for r in rows if r.get("month"))
        return {"ok": False, "error": f"该品种没有 {m} 这个可交易月份（可用: {avail}）"}
    price = sel.get("price")
    oi = sel.get("oi")
    months = [r.get("month") for r in rows if r.get("month")]
    row: dict[str, Any] = {"month_mode": m, "vr_months": months}

    # ── 基差：现货 − 该月期货价（现货腿与合约无关，只有期货腿换月）──
    spot = None
    spot_date = None
    src = ""
    try:
        b = q._fetch_basis(code, 60, batch=True)
        if b.get("ok") and b.get("rows"):
            sp = [r["spot_price"] for r in b["rows"] if r.get("spot_price") is not None]
            spot = sp[-1] if sp else None
            spot_date = b["rows"][-1].get("date")
            src = b.get("source") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 月度-基差取数失败(%s): %s", code, e)
    row["basis_spot"] = spot
    row["basis_spot_date"] = spot_date
    if spot is not None and price:
        bs = round(float(spot) - float(price), 2)
        row["basis"] = bs
        row["basis_rate"] = round(bs / float(price), 4)
        row["basis_side"] = "贴水" if bs > 0 else ("升水" if bs < 0 else "平水")
        row["basis_src"] = f"{src} 现货 + {sel.get('contract')} 报价"
    else:
        row["basis"] = None
        row["basis_rate"] = None
        row["basis_side"] = None
        row["basis_src"] = src
    # ★ 分位不可算（缺该月历史序列）—— 如实置空 + 注明，绝不用主力序列冒充
    row["basis_pct"] = None
    row["basis_pct_note"] = (
        "无现货指数口径（该品种基差本就不可得）" if spot is None
        else f"「{m}」无基差历史序列（平台只提供「现货−主力」序列），分位不可算")

    # ── 虚实盘比：该月持仓÷2 ÷ 仓单折手数 ──
    row["vr_contract"] = sel.get("contract")
    row["vr_month"] = m
    row["vr_mtd"] = _months_to_delivery(m)
    row["vr_ratio"] = None
    try:
        inv = q._fetch_inventory(code, 180, batch=True)
        qty = [r["qty"] for r in (inv.get("rows") or []) if r.get("qty") is not None] \
            if inv.get("ok") else []
        lf = _lot_factor(code)
        if qty and lf.get("ok") and oi:
            lots = float(qty[-1]) * float(lf["factor"])
            row["vr_lots"] = round(lots, 2)
            row["vr_ratio"] = _vr_ratio_of(lots, round(float(oi) / 2, 2))
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 月度-虚实比失败(%s): %s", code, e)

    # ── 期限结构：**原票保留**，另起「自该月看」的读数（用户拍板，不覆盖原票）──
    try:
        import quant_methodology as qmth

        # 月份串是 'YYMM' 定长 ⇒ 字符串序 == 时间序（与 _fetch_term_structure 的排序同口径）
        sub = [r for r in rows
               if str(r.get("month") or "") >= m and r.get("price") is not None]
        tsr = qmth.term_structure_regime(sub)
        if tsr.get("ok"):
            row["term_anchor"] = {
                "from_month": m, "shape": tsr.get("shape"), "spread": tsr.get("spread"),
                "spread_pct": tsr.get("spread_pct"), "bias": tsr.get("bias"),
                "months_used": len(sub),
                "note": f"自 {m} 起（把 {m} 当新近月）的曲线形态与价差。"
                        f"⚠ 这只是**另起的一个读数**：表格「期限结构」那一票仍按"
                        f"**整条曲线**（近月→远月）口径, 未改变。",
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 月度-期限锚失败(%s): %s", code, e)

    # ── 4 票：库存周期/利润/期限 与合约无关（沿用 build_verdict 的真值）；
    #    二维四象因缺"该月基差率历史序列"而**不可重算** ⇒ 如实置空并注明 ──
    try:
        ins, _ierr = _fund_vote_inputs(code)
        ev = _fund_skill_votes(code, ins["bars"], ins["basis_rates"], ins["inv_series"],
                               ins["inv_chg"], ins["term"], ins["profit_rows"])
        if ev.get("ok"):
            row["skill_vd"] = None
            row["skill_vd_gap"] = (
                f"「{m}」口径下无法重算二维四象：它的估值轴需要**该月基差率的历史序列**，"
                f"平台只提供「现货−主力」序列（原值见默认口径）")
            row["skill_term"] = ev["votes"].get("term")
            row["skill_cyc"] = ev["votes"].get("cyc")
            row["skill_prof"] = ev["votes"].get("prof")
            # 净票/票数只统计**本行真正有效的票**（二维四象已置空 ⇒ 少一票）
            keys = ("term", "cyc", "prof")
            # 【2026-10-19】方向映射统一走 vote_semantics：原 dirmap 漏了「偏多/偏空」，
            #   而前端 _FRK_TEXT_RANK 一直把「偏多」当 +1 ⇒ 前后端口径不一致。
            row["skill_net"] = sum(_vs.sign(row.get("skill_" + k)) for k in keys)
            row["skill_n"] = sum(1 for k in keys if row.get("skill_" + k))
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 月度-四票失败(%s): %s", code, e)

    # ── 综合结论：用重算后的基差侧（库存/利润档位与合约无关，取自同一批既有函数）──
    try:
        inv_grade = "不明"
        prof_grade = "不明"
        try:
            inv2 = q._fetch_inventory(code, 180, batch=True)
            d2 = q._inv_dual(code, (inv2.get("rows") or []) if inv2.get("ok") else [], 180,
                             inv2.get("source", "") if inv2.get("ok") else "")
            inv_grade = d2.get("grade") or "不明"
        except Exception:  # noqa: BLE001
            pass
        try:
            p2 = _profit_summary(code)
            prof_grade = p2.get("grade") if p2.get("ok") else "不明"
        except Exception:  # noqa: BLE001
            pass
        v = _strategy_verdict(inv_grade, prof_grade, row.get("basis_side") or "不明")
        row["strategy"] = v.get("action")
        row["strategy_ok"] = bool(v.get("ok"))
        row["strategy_reason"] = v.get("reason")
        row["strategy_degraded"] = v.get("degraded")
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 月度-策略失败(%s): %s", code, e)

    return {"ok": True, "variety": code, "name": meta.get("name", code),
            "month": m, "contract": sel.get("contract"), "row": row}


@fund_bp.route("/api/fund/xsection")
def api_fund_xsection():
    """按**指定合约月**重算某品种的基本面截面行（供 ⑨ 表「合约」列切换时就地替换该行）。

    【参数】variety(必填); month=合约月(如 2701)。**month 为空/缺省 ⇒ 返回默认口径整行**
            （供"恢复默认"用, 与拉表同源, 保证与表格默认值一致）。
    【返回】{ok, variety, month, contract, row:{...}}；row 只含被替换的字段
            （库存族/利润/与合约无关的票**不在其中** —— 前端按字段合并, 不整行覆盖）。
    【口径】见 `_fund_month_row` 的注释（哪些重算 / 哪些不动 / 哪些不可算）。
    """
    code = _code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    month = (request.args.get("month") or "").strip()
    try:
        if not month:
            # 默认口径整行（与 ⑨ 表同一函数 ⇒ 数值必然一致）
            return jsonify(_q()._json_safe(
                {"ok": True, "variety": code, "month": "",
                 "row": _fund_xsection_one(code)}))
        res = _fund_month_row(code, month)
        return jsonify(_q()._json_safe(res))
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 月度截面失败(%s/%s): %s", code, month, e)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})

# ═══════════════════════════════════════════════════════════════════════════
# ⑨ 全品种基本面截面表（2026-10-16）—— 与技术面「全品种技术面总览」并列
# ═══════════════════════════════════════════════════════════════════════════
# 【为什么需要】技术面有「全品种总览」表(涨跌幅 / 日线 / 60分 / 15分 / 5分 / 状态 /
#   共振 / 结论)。基本面此前**只有单品种视图** ⇒ 无法一眼看出全池的库存/基差/
#   虚实比分布。本表把列换成基本面因子:
#     库存(双口径) / 基差 / 基差率 / 仓单 5 日变化 / 社会库存 4 期变化 / 虚实盘比 /
#     利润 + 《期货多空逻辑》系 **4 票**(二维四象 / 期限结构 / 库存周期 / 利润)
#     + 三指标综合结论。
#
# 【★★ 口径纪律: 本表不新增任何口径】同品种在本表与对应卡片上**必然一致**:
#   · 库存   → quant_module._inv_dual   (社会库存 + 仓单 双口径综合, 与 ① 卡同源)
#   · 虚实比 → fund_module._vr_ratio_of (与 ④ 卡的比值**同一处算术**)
#   · 利润   → fund_module._profit_summary (与 ①③ 卡同源)
#   · 4 票   → quant_verdict.build_verdict (与 ⑥ 多空逻辑体系研判同源), 只按 book 前缀筛
#   任何"本表自己再算一套"的改动都是错的 —— 那正是本项目已记录过的口径漂移事故型。
# 【变量】基本面截面整体缓存 15 min —— **与技术面 `quant_module._TTL_OVERVIEW` 对齐**
#   （用户 2026-10-16 要求"更新模式也按照技术量化的方法"）。前端 `_FRK_REFRESH_MS`
#   必须与这个值一致，否则要么永远命中旧缓存、要么每次都白重算。
_TTL_FUND_RANK = 900

# 4 票的 book 名 → 列键(与 quant_verdict.build_verdict 里的 book **字面量**一一对应)
_FUND_VOTE_BOOKS = (
    ("vd", "多空逻辑"),
    ("term", "多空逻辑(期限结构)"),
    ("cyc", "多空逻辑(库存周期)"),
    ("prof", "多空逻辑(利润)"),
)
# 缺口文案前缀 → 同一列(取自 build_verdict 的 data_gaps 措辞, 用于说明"该列为何空")
_FUND_GAP_PREFIX = {
    "vd": "多空逻辑二维四象:",
    "term": "多空逻辑期限结构:",
    "cyc": "多空逻辑库存周期:",
    "prof": "利润维度:",
}

# 库存档位 → 排序数值：低=+1(偏多) / 中=0 / 高=-1(偏空)。
# 【变量】为什么把档位翻译成数字】档位本身是**方向**不是量: 低库存→偏多。让它与
#   "多空票净额"(做多=+1)同向, 用户按"库存档位"降序排才不会看到方向反过来的表
#   (若把低写成 -1, 表里高库存排最前, 结论恰好反了 —— 排序看着没坏, 语义全错)。
# 【为什么在 _fund_xsection_one 里就写死】原写在路由里**事后改缓存对象** ⇒
#   直接调 _fund_rank_all(或读缓存)拿到的行**没有** inv_rank(验收实测抓到),
#   而且改缓存对象是隐式副作用。行生成时就带上, 缓存里存的就是"完整行"。
_INV_GRADE_RANK = {"低": 1, "中": 0, "高": -1}


def _vr_ratio_of(lots, one_side_oi):
    """虚实盘比 = 单边持仓(手) ÷ 仓单折手数 —— **本模块里比值的唯一定义处**。

    【为什么抽成函数】④ 卡(api_fund_virtual_real)与 ⑨ 截面表都要这个比值。
    若各写一遍 `one_side_oi / lots`, 将来一边改口径(如改成"总持仓"或另一折算系数)
    另一边不会跟着变 ⇒ 同一品种在两个页面出现**不同虚实比**。本项目已有同类教训
    (见 _profit_summary 的"为什么要抽出来")。
    【返回】比值(4 位小数); 分母缺失/为 0 → None(**不是 0** —— 0 会被当成"极端空逼多")
    """
    try:
        lots_f = float(lots)
        oi_f = float(one_side_oi)
    except (TypeError, ValueError):
        return None
    if lots_f <= 0 or oi_f < 0:
        return None
    return round(oi_f / lots_f, 4)


def _vr_ratio(code: str) -> dict:
    """单品种虚实盘比(截面表用; ④ 卡另有更丰富的三特征/安全边际输出)。

    【口径与 ④ 卡一致】实盘 = 仓单数量 × _lot_factor 的换算系数(各交易所单位不统一);
      虚盘 = 主力合约持仓 ÷ 2(交易所公布持仓为双边)。
    【返回】{ok, ratio, lots, one_side_oi, contract, month, qty, ck_pct, months_to_delivery}
    """
    q = _q()
    out: dict[str, Any] = {"ok": False}
    try:
        inv = q._fetch_inventory(code, 180)
        qty = [r["qty"] for r in (inv.get("rows") or []) if r.get("qty") is not None] \
            if inv.get("ok") else []
        lf = _lot_factor(code)
        if not qty or not lf.get("ok"):
            return {**out, "error": (lf.get("error") if not lf.get("ok")
                                     else (inv.get("error") or "无仓单数据"))}
        lots = float(qty[-1]) * float(lf["factor"])
        t = q._fetch_term_structure(code)
        if not (t.get("ok") and t.get("main")):
            return {**out, "error": t.get("error") or "无跨月合约报价(取不到主力持仓)"}
        main = t["main"]
        oi = main.get("oi")
        if not oi:
            return {**out, "error": "主力合约持仓量为空"}
        one_side = round(oi / 2, 2)
        ratio = _vr_ratio_of(lots, one_side)
        if ratio is None:
            return {**out, "error": "仓单折手数为 0, 比值无定义"}
        return {"ok": True, "ratio": ratio, "lots": round(lots, 2), "one_side_oi": one_side,
                "contract": main.get("contract"), "month": main.get("month"),
                "qty": qty[-1], "ck_pct": _pct_rank(qty, qty[-1]),
                "months_to_delivery": _months_to_delivery(main.get("month")),
                "shape": t.get("shape"),
                # 【供 ⑨ 表「合约」列的下拉直接使用】把该品种的合约月份带在行里,
                #   前端无需为 21 行各发一次 /api/fund/contracts(省 21 个请求);
                #   行内展开明细时才按需拉那一个品种。
                "months": [r.get("month") for r in (t.get("rows") or []) if r.get("month")]}
    except Exception as e:  # noqa: BLE001 - 单维度失败只降级该列
        logger.warning("fund 截面-虚实比失败(%s): %s", code, e)
        return {**out, "error": f"{type(e).__name__}: {e}"}


def _fund_vote_inputs(code: str, bars: list | None = None) -> tuple[dict, dict]:
    """装配 4 票所需的**取数输入**（纯取数，不含任何判定口径）。

    【为什么抽出来】「截面表默认口径」(`_fund_xsection_one`) 与「按指定合约月重算」
      (`_fund_month_row`) 都要这六样输入。若各写一遍, 哪天某处换了窗口(例如基差率从
      180 天改成 120 天), 另一处不会跟着变 ⇒ 同一品种在两个入口**四票不一致**。
      判定口径本身仍只在 `_fund_skill_votes` → `quant_verdict.build_verdict`。
    【返回】(inputs, errors)
      inputs = {bars, basis_rates, inv_series, inv_chg, term, profit_rows}
      errors = {term?: str, prof?: str, bars?: str}（取数失败的如实原因, 供上层标注）
    """
    q = _q()
    err: dict[str, str] = {}
    if bars is None:
        try:
            # 【2026-10-22 收口】走缓存层（内存 5min + 磁盘定稿缓存），与量化页同源同口径。
            #   下游只读 `bars[-1]/[-2]` 与遍历，不改容器 ⇒ 共享 list 安全。
            bars = q._daily_bars_cached(code, 200)
        except Exception as e:  # noqa: BLE001
            err["bars"] = f"{type(e).__name__}: {e}"[:60]
            bars = []
    basis_rates: list = []
    try:
        br = q._fetch_basis(code, 180, batch=True)
        basis_rates = [r["basis_rate"] for r in (br.get("rows") or [])
                       if r.get("basis_rate") is not None] if br.get("ok") else []
    except Exception as e:  # noqa: BLE001
        err["basis"] = f"{type(e).__name__}: {e}"[:60]
    inv_series: list = []
    try:
        iv = q._fetch_inventory(code, 180, batch=True)
        inv_series = [r["qty"] for r in (iv.get("rows") or [])
                      if r.get("qty") is not None] if iv.get("ok") else []
    except Exception as e:  # noqa: BLE001
        err["inv"] = f"{type(e).__name__}: {e}"[:60]
    inv_chg = None
    pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]
    if len(pos) >= 4 and pos[-4]:
        inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2)
    term: dict = {}
    try:
        t = q._fetch_term_structure(code)
        if t.get("ok"):
            term = t
    except Exception as e:  # noqa: BLE001
        err["term"] = f"{type(e).__name__}: {e}"[:60]
    profit_rows: list = []
    try:
        pf = q._fetch_profit(code)
        if pf.get("ok"):
            profit_rows = pf.get("rows") or []
    except Exception as e:  # noqa: BLE001
        err["prof"] = f"{type(e).__name__}: {e}"[:60]
    return ({"bars": bars, "basis_rates": basis_rates, "inv_series": inv_series,
             "inv_chg": inv_chg, "term": term, "profit_rows": profit_rows}, err)


def _fund_skill_votes(code: str, bars: list, basis_rates: list, inv_series: list,
                      inv_chg, term: dict, profit_rows: list) -> dict:
    """《期货多空逻辑》系 4 票 —— **调 quant_verdict.build_verdict**(绝不另写一套判定)。

    【为什么必须调它】4 票的判据都在 build_verdict 里: 二维四象的组合规则、
      期限结构的 regime 映射、库存周期四阶段、利润的 **0.3/0.7** 分位阈值
      (⚠ 注意利润票用的是 0.3/0.7, 而 fund_module._grade 用的是 0.35/0.65 ——
       两套阈值**确实不同**, 是不同方法论的口径, 不可"顺手统一")。
      自己再写一遍必然漂移。
    【返回】{ok, votes:{vd/term/cyc/prof: bias}, details:{同上: 说明}, gaps:{同上: 缺口}, net}
    """
    ev = {"ok": False, "votes": {}, "details": {}, "gaps": {}, "net": None}
    try:
        import quant_verdict as qv
    except Exception as e:  # noqa: BLE001
        logger.warning("量化研判模块不可用(%s): %s", code, e)
        return {**ev, "error": f"{type(e).__name__}: {e}"}
    # 【利润序列】取"样本最长"的那个口径(与 api_quant_verdict 同一规则: 能化多装置口径,
    #   长的通常是主工艺)
    profit_label, profit_series = "利润", []
    groups: dict[str, list] = {}
    for r in profit_rows or []:
        if r.get("profit") is not None:
            groups.setdefault(r.get("label") or "利润", []).append(r["profit"])
    if groups:
        profit_label = max(groups.items(), key=lambda kv: len(kv[1]))[0]
        profit_series = [float(v) for v in groups[profit_label]]
    payload = {
        "variety": code, "name": _meta(code).get("name", code),
        "unit": _meta(code).get("unit", ""), "bars": bars,
        "basis_rate_series": basis_rates, "inv_series": inv_series, "inv_chg_pct": inv_chg,
        "term": term, "profit_series": profit_series, "profit_label": profit_label,
    }
    try:
        res = qv.build_verdict(payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 截面-研判失败(%s): %s", code, e)
        return {**ev, "error": f"{type(e).__name__}: {e}"}
    if not res.get("ok"):
        return {**ev, "error": res.get("error") or "研判不可用"}
    votes = (((res.get("stages") or {}).get("direction") or {}).get("votes") or [])
    by_book = {}
    for v in votes:
        by_book.setdefault(v.get("book"), v)
    for key, book in _FUND_VOTE_BOOKS:
        v = by_book.get(book)
        if v:
            ev["votes"][key] = v.get("bias")
            ev["details"][key] = v.get("detail")
    # 缺口: 只认**该列自己的**那条, 避免把别列的原因贴到本列
    for g in (res.get("data_gaps") or []):
        for key, prefix in _FUND_GAP_PREFIX.items():
            if str(g).startswith(prefix) and key not in ev["gaps"]:
                ev["gaps"][key] = str(g)[len(prefix):].strip()
    # 【2026-10-19】统一走 vote_semantics（期限结构的「偏多/偏空」此前恒计 0）
    ev["net"] = sum(_vs.sign(ev["votes"].get(k)) for k, _ in _FUND_VOTE_BOOKS)
    ev["ok"] = True
    ev["n_voted"] = sum(1 for k, _ in _FUND_VOTE_BOOKS if k in ev["votes"])
    return ev


def _fund_xsection_one(code: str) -> dict:
    """单品种基本面截面行(供 ⑨ 表; 无数据的列留 None, 不猜)。

    【设计约束】每个维度**各自 try/except** —— 单一源失败只让该列空(并附原因),
      绝不让整行消失(与 quant_module._cross_section_one 同一纪律)。
    """
    q = _q()
    meta = _meta(code)
    _sector_cn = meta.get("sector_cn") or meta.get("sector") or ""
    row: dict[str, Any] = {"variety": code, "name": meta.get("name", code),
                           "sector": _sector_cn,
                           # 【2026-10-16 新增】粗板块 —— **复用技术面同一个归并函数**
                           #   (`quant_module._sector_bucket`: 精确表 + 前缀兜底)。
                           #   本表的 `sector` 是细分子板块(「能化(化工)」), 直接拿它做筛选会
                           #   得到与技术面**不同粒度**的选项(那边是「能化」) ⇒ 两页筛选不一致。
                           #   这里下发归一后的 `sector_bucket`, 前端只按它筛。
                           "sector_bucket": q._sector_bucket(_sector_cn)}
    err: dict[str, str] = {}

    # ── ① 基差族(基差 / 基差率 / 升贴水 / 分位) ──
    bracket_side = None
    try:
        b = q._fetch_basis(code, 60, batch=True)
        if b.get("ok") and b.get("rows"):
            vals = [r["basis"] for r in b["rows"] if r.get("basis") is not None]
            rates = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None]
            if vals:
                cur = vals[-1]
                row["basis"] = cur
                row["basis_rate"] = rates[-1] if rates else None
                bracket_side = "贴水" if cur > 0 else ("升水" if cur < 0 else "平水")
                row["basis_side"] = bracket_side
                # 【★ 量纲必须与本表其它分位列一致】本表所有**分位**列一律 **0~1 比率**
                #   (ck_pct / soc_pct 来自 _inv_pct/_pct_rank, 天生是 0~1), 前端统一 ×100 展示。
                #   此处原写成 `* 100` 的百分数 ⇒ **同一张表里两种量纲**, 前端再 ×100
                #   就显示成 "5790%"(护栏 test_ranking_field_contract 抓到)。
                row["basis_pct"] = round(sum(1 for v in vals if v <= cur) / len(vals), 4)
                row["basis_src"] = b.get("source")
            else:
                err["basis"] = "基差序列为空"
        else:
            err["basis"] = (b.get("error") or "无基差数据")[:60]
    except Exception as e:  # noqa: BLE001
        err["basis"] = f"{type(e).__name__}: {e}"[:60]

    # ── ② 库存双口径(综合档位 / 仓单分位 / 仓单 5 日 / 社会库存分位 / 4 期变化) ──
    try:
        inv = q._fetch_inventory(code, 180, batch=True)
        rows = (inv.get("rows") or []) if inv.get("ok") else []
        dual = q._inv_dual(code, rows, 180, inv.get("source", "") if inv.get("ok") else "")
        row["inv_grade"] = dual.get("grade")
        row["inv_rank"] = _INV_GRADE_RANK.get(row["inv_grade"])
        row["inv_mode"] = dual.get("mode")
        ck = dual.get("ck") or {}
        row["ck_pct"] = ck.get("pct_rank")
        row["ck_chg5"] = ck.get("chg_pct")
        row["ck_zero"] = bool(ck.get("warn"))
        row["ck_warn"] = ck.get("warn")
        soc = dual.get("social") or {}
        row["soc_ok"] = bool(soc.get("ok"))
        row["soc_pct"] = soc.get("pct_rank")
        row["soc_chg4"] = soc.get("chg_pct")
        row["soc_label"] = soc.get("label") or "社会库存"
        row["soc_date"] = soc.get("latest_date")
    except Exception as e:  # noqa: BLE001
        err["inv"] = f"{type(e).__name__}: {e}"[:60]

    # ── ③ 虚实盘比 ──
    try:
        vr = _vr_ratio(code)
        row["vr_ratio"] = vr.get("ratio") if vr.get("ok") else None
        row["vr_contract"] = vr.get("contract")
        row["vr_month"] = vr.get("month")
        row["vr_mtd"] = vr.get("months_to_delivery")
        row["vr_months"] = vr.get("months") or []
        row["vr_shape"] = vr.get("shape")
        if not vr.get("ok"):
            err["vr"] = (vr.get("error") or "无数据")[:60]
    except Exception as e:  # noqa: BLE001
        err["vr"] = f"{type(e).__name__}: {e}"[:60]

    # ── ④ 利润档位(与 ①③ 卡同源) ──
    try:
        p = _profit_summary(code)
        row["prof_ok"] = bool(p.get("ok"))
        row["prof_grade"] = p.get("grade") if p.get("ok") else None
        if not p.get("ok"):
            err["prof"] = (p.get("error") or "无利润数据")[:60]
    except Exception as e:  # noqa: BLE001
        err["prof"] = f"{type(e).__name__}: {e}"[:60]

    # ── ⑤ 4 票 SKILL 判断(需要日线 + 基差率 + 库存序列 + 期限 + 利润) ──
    bars: list = []
    try:
        bars = q._daily_bars_cached(code, 200)  # 【收口 2026-10-22】走缓存层
    except Exception as e:  # noqa: BLE001
        err["bars"] = f"{type(e).__name__}: {e}"[:60]
    try:
        if bars:
            row["close"] = bars[-1].get("close")
            prev = bars[-2].get("close") if len(bars) >= 2 else None
            if row["close"] is not None and prev:
                row["chg_pct"] = round((row["close"] / prev - 1) * 100, 2)
            row["date"] = bars[-1].get("date")
        # 【2026-10-16】六样取数输入抽到 _fund_vote_inputs, 与"按合约月重算"共用同一份
        #   （各写一遍的话窗口口径会各自漂移）。判定口径仍在 _fund_skill_votes。
        ins, ierr = _fund_vote_inputs(code, bars)
        for _k, _v in ierr.items():
            err.setdefault(_k, _v)
        ev = _fund_skill_votes(code, ins["bars"], ins["basis_rates"], ins["inv_series"],
                               ins["inv_chg"], ins["term"], ins["profit_rows"])
        row["skill_ok"] = bool(ev.get("ok"))
        for key, _ in _FUND_VOTE_BOOKS:
            row["skill_" + key] = ev["votes"].get(key)
            row["skill_" + key + "_gap"] = ev["gaps"].get(key) or ev.get("error")
        row["skill_net"] = ev.get("net")
        row["skill_n"] = ev.get("n_voted")
    except Exception as e:  # noqa: BLE001
        err["skill"] = f"{type(e).__name__}: {e}"[:60]

    # ── ⑥ 三指标综合结论(库存双口径 + 利润 + 基差 → 单边/跨期四类) ──
    try:
        v = _strategy_verdict(row.get("inv_grade") or "不明",
                              row.get("prof_grade") or "不明",
                              bracket_side or "不明")
        row["strategy"] = v.get("action")
        row["strategy_ok"] = bool(v.get("ok"))
        row["strategy_reason"] = v.get("reason")
        row["strategy_degraded"] = v.get("degraded")
    except Exception as e:  # noqa: BLE001
        err["strategy"] = f"{type(e).__name__}: {e}"[:60]

    if err:
        row["errors"] = err
    return row


def _fund_rank_all(codes: list[str], force: bool = False) -> list[dict]:
    """并发拉全池基本面截面(21 品种 × 多源), 整体结果缓存 _TTL_FUND_RANK。

    【为什么并行】单品种要拉 日线/基差/仓单/合约报价/利润/社会库存 六类源; 串行时
      最慢的那个(日线走后复权 CSV, 冷启动可达 30s+)会把全表拖成分钟级。
      实测并发 6 下全池墙钟 ≈ 41s(首拉), 之后切排序维度直接对缓存重排、秒回。
    """
    key = "fund_rank:" + ",".join(codes)
    q = _q()
    if not force:
        hit = q._cache_get(key, _TTL_FUND_RANK)
        if hit is not None:
            return hit
    from concurrent.futures import ThreadPoolExecutor

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=q._RANK_PARALLEL) as ex:
        futs = {ex.submit(_fund_xsection_one, c): c for c in codes}
        for fut, c in futs.items():
            try:
                out[c] = fut.result()
            except Exception as e:  # noqa: BLE001 - 单品种失败留空行
                logger.warning("基本面截面失败(%s): %s", c, e)
                out[c] = {"variety": c, "name": _meta(c).get("name", c)}
    rows = [out[c] for c in codes if c in out]
    q._cache_set(key, rows)
    return rows


# 【功能】⑨ 基本面截面表的**后台计算 + stale-while-revalidate**（2026-10-16）
#
# 【为什么需要】此前 `/api/fund/ranking` 是**同步**跑 `_fund_rank_all` ⇒
#   ① 首次进页面要**干等约 40 秒**（21 品种 × 六类源，并发 6），中间页面看着像卡死；
#   ② 缓存 TTL 一到就把结果丢掉重算 ⇒ 用户每次回来(距上次 > TTL)都得再等一整轮；
#   ③ 结果只存在进程内缓存，**进程重启后第一次访问同样要等 40 秒**。
#   ⇒ 与技术面总览(`_ov_start/_ov_worker/_ov_snapshot`)对齐，做成同一套：
#       后台线程逐品种发布 + 保留旧结果就地刷新 + 前端短轮询看进度。
#
# 【状态字段（与 quant 总览同形，便于前端复用同一套判据）】
#   rows(按 code 的 dict) / done(有可用结果) / refreshing(后台正在算) /
#   stale(当前 rows 是上一轮的) / refreshed(本轮已刷新品种数) / ts / err
#
# 【与 `_fund_rank_all` 的关系】那个**同步版保留不动**（供直接调用它的脚本/验收），
#   本套只服务界面。两者共用同一个缓存 key 前缀，但**状态互不读写**：
#   本套额外做的事是"整轮成功后把结果写回 `q._cache_set`"，让**进程重启后也能秒开**
#   （第一次请求时把持久缓存里的旧行当 carry 带上表）。
_FRK_STATE: dict[str, dict] = {}    # 【变量】key → 状态（同 _OV_STATE 的形状）
_FRK_LOCK = threading.Lock()        # 【变量】状态读写锁


def _frk_state_key(codes: list[str]) -> str:
    """状态/缓存 key —— 按品种集合区分（与技术面 `_ov_state_key` 同一思路）。"""
    return "fund_rank:" + ",".join(codes)


def _frk_worker(key: str, codes: list[str], carry: dict | None = None) -> None:
    """后台线程主体：并发拉全池，**每完成一个品种就发布一次**。

    【为什么逐品种发布】首拉全池 ≈ 40s。逐品种发布 ⇒ 前端每 3s 轮询都能看到
      "已完成 n/21"，且**算好的行立刻出现**，不必等 21 个全好。
    【失败不致命】单品种异常写入兜底行(带 `error` 字段)，整轮永远收敛到 done=True，
      否则前端会无限轮询。
    【★ carry = 上一轮结果（stale-while-revalidate）】TTL 到期重算时把旧 rows 带进来 ⇒
      ① 前端**秒开旧表**（而不是回到"加载中…"干等一整轮 40s）；
      ② 逐品种覆盖 ⇒ 每行算好一个就换新的，用户看到的是"就地刷新"。
      ⚠ 有旧结果时 `done` 必须**全程保持 True** —— 否则前端会把已显示的表换成占位行，
      又把"秒开"毁掉（技术面那轮踩过，是修复的核心）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: dict[str, dict] = dict(carry or {})   # 【变量】带上旧结果, 逐品种覆盖
    had_old = bool(carry)                       # 【变量】有旧结果 ⇒ 全程 done=True
    prog = {"n": 0}                             # 【变量】本轮已刷新的品种数

    def _publish(done: bool = False, err: str = "") -> None:
        with _FRK_LOCK:
            st = _FRK_STATE.get(key)
            if st is None:          # 被显式清掉 ⇒ 放弃本轮
                return
            st["rows"] = dict(rows)
            st["done"] = bool(done or had_old)
            st["refreshing"] = not done
            st["stale"] = bool(had_old and not done)
            st["refreshed"] = prog["n"]
            st["ts"] = time.time()
            if err:
                st["err"] = err

    try:
        q = _q()
        with ThreadPoolExecutor(max_workers=q._RANK_PARALLEL) as ex:
            futs = {ex.submit(_fund_xsection_one, c): c for c in codes}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    rows[c] = fut.result()
                except Exception as e:  # noqa: BLE001 - 单品种失败留空行
                    logger.warning("基本面截面失败(%s): %s", c, e)
                    rows[c] = {"variety": c, "name": _meta(c).get("name", c)}
                prog["n"] += 1
                _publish()
    except Exception as e:  # noqa: BLE001 - 兜底: 整轮异常也必须收敛到 done
        logger.warning("基本面截面后台计算异常: %s", e)
        _publish(done=True, err=str(e)[:200])
        return
    _publish(done=True)
    # 【整轮成功才写持久缓存】留给"进程重启后的第一次访问"当 carry（见 _frk_start）。
    #   ⚠ 不能边算边写: 半张表写进缓存, 重启后会以"半张表"秒开且看不出缺行。
    try:
        _q()._cache_set(key, [rows[c] for c in codes if c in rows])
    except Exception as e:  # noqa: BLE001
        logger.warning("基本面截面写缓存失败: %s", e)


def _frk_start(codes: list[str], force: bool = False) -> str:
    """确保后台计算已启动（幂等）。返回 state key。

    【★ stale-while-revalidate】TTL 到期需要重算时**不再清空**旧结果：
      · 已有旧结果 ⇒ 保留它并在后台重算（全程 done=True + refreshing=True）
        ⇒ 前端秒开旧表，算好的品种逐个替换；
      · 从无结果（首次）才走空表 + 渐进轮询。
    【旧结果的三个来源，按优先级】① 进程内 state；② **持久缓存**（`q._cache_get`）
      —— 这一条专治"进程重启后第一次访问又要等 40s"；都没有才是真的首次。
    """
    key = _frk_state_key(codes)
    with _FRK_LOCK:
        st = _FRK_STATE.get(key) or {}
        refreshing = bool(st.get("refreshing"))
        done = bool(st.get("done"))
        # 新鲜 = 算完过、当前不在刷新、不是旧数据、且未过期
        fresh = (done and not refreshing and not st.get("stale")
                 and time.time() - st.get("ts", 0) < _TTL_FUND_RANK)
        if refreshing:
            # 已有后台刷新在跑（显式 force 也不再起一个 —— 那一轮马上就会给出新数据）
            return key
        if fresh and not force:
            return key                  # 结果还新鲜, 命中
        carry = dict(st["rows"]) if st.get("rows") else None
        if carry is None:
            # 进程内无旧结果 ⇒ 试试持久缓存（进程重启后仍能秒开）
            hit = _q()._cache_get(key, _TTL_FUND_RANK)
            if hit:
                carry = {r["variety"]: r for r in hit
                         if isinstance(r, dict) and r.get("variety")}
        _FRK_STATE[key] = {"rows": dict(carry or {}), "done": bool(carry),
                           "refreshing": True, "stale": bool(carry), "refreshed": 0,
                           "err": "", "ts": st.get("ts", 0)}
    threading.Thread(target=_frk_worker, args=(key, list(codes), carry),
                     name="fund-ranking", daemon=True).start()
    return key


def _frk_snapshot(key: str, codes: list[str]) -> tuple[list[dict], bool, str, dict]:
    """取当前快照（可能是部分结果）。返回 (rows_按 codes 序, done, err, meta)。

    【meta】{refreshing, stale, refreshed, ts} —— 供前端区分**三态**：
      · 首次计算中(done=False)         → 显示进度条（还没有东西可看）
      · 有旧数据 + 后台刷新中(refreshing) → **旧表继续显示** + 短轮询等新数据落地
      · 完成且不再刷新                  → 排"每 N 分钟自动刷新"的定时器
    """
    with _FRK_LOCK:
        st = _FRK_STATE.get(key) or {}
        rows_map = st.get("rows") or {}
        done = bool(st.get("done"))
        err = st.get("err") or ""
        meta = {"refreshing": bool(st.get("refreshing")),
                "stale": bool(st.get("stale")),
                "refreshed": int(st.get("refreshed") or 0),
                "ts": float(st.get("ts") or 0)}
    return ([rows_map[c] for c in codes if c in rows_map], done, err, meta)


@fund_bp.route("/api/fund/ranking")
def api_fund_ranking():
    """⑨ 全品种基本面截面表(与技术面「多品种量化截面排行」同形, 列换成基本面因子)。

    【参数】varieties=逗号分隔(默认全池 21); metric=排序维度
            (net 多空票 / basis / basis_rate / basis_pct / ck_pct / ck_chg5 /
             soc_pct / soc_chg4 / vr_ratio / inv_rank / chg_pct, 默认 net);
            order=desc/asc(默认 desc); refresh=1 跳缓存重拉。
    【返回】{ok, metric, order, count, cached, rows:[{...}]}。
    【口径】所有列都复用既有函数(见 _fund_xsection_one 的注释), 本接口**不含新口径**。
    """
    raw = (request.args.get("varieties") or "").strip()
    pool = _pool()
    codes = [c for c in (x.strip().upper() for x in raw.split(",")) if c] if raw else pool
    codes = [c for c in codes if c in pool] or pool
    metric = (request.args.get("metric") or "net").strip()
    order = (request.args.get("order") or "desc").strip().lower()
    refresh = (request.args.get("refresh") or "").strip() in ("1", "true", "yes")
    # ── 后台计算 + stale-while-revalidate（2026-10-16）──────────────────────
    # 原为同步 `_fund_rank_all(codes, force=refresh)` ⇒ 首次干等约 40s，且 TTL 一到
    # 就把结果丢掉重算（用户每次回来都得再等一轮）。现改为：
    #   起后台线程（幂等）+ 立刻返回当前快照 ⇒ 前端秒开（有旧结果时）或显示进度。
    skey = _frk_start(codes, force=refresh)
    rows, done, err, meta = _frk_snapshot(skey, codes)
    # 板块清单按**池内全部品种**派生(扩池自动跟随, 不硬编码) —— 与技术面 `pools` 同做法。
    # ⚠ 用 codes 而非 rows: 渐进阶段或个别源失败时 rows 可能不全, 用 rows 会让
    #   可选的板块**越筛越少**(技术面 api_quant_overview 同一处已有此纪律)。
    #   顺序用 sorted 固定 —— 不随请求顺序抖动, 否则胶囊位置会跳。
    _pools = sorted({_q()._sector_bucket(_meta(c).get("sector_cn") or "") for c in codes})
    # 【防御性重算, 不是主逻辑】inv_rank 已在 _fund_xsection_one 里算好(缓存里就是完整行);
    #   这里再兜一次是因为**缓存可能是旧进程写的**(本次改动前缓存的行没有该字段) ——
    #   若只依赖行内计算, 改完不重启就会让"库存档位"排序整体失效(静默)。
    #   ⚠ 唯一的档位序定义仍在 _INV_GRADE_RANK, 此处不得写第二套映射。
    for r in rows:
        r["inv_rank"] = _INV_GRADE_RANK.get(r.get("inv_grade"))

    def sort_key(r):
        v = r.get(metric)
        ok = isinstance(v, (int, float))
        return (not ok, -(v if ok else 0) if order == "desc" else (v if ok else 0))

    rows = sorted(rows, key=sort_key)
    return jsonify(_q()._json_safe({
        "ok": True, "metric": metric, "order": order,
        # ready/total/done 供前端做**渐进渲染**（同技术面总览的字段名, 前端判据可复用）
        "count": len(rows), "ready": len(rows), "total": len(codes), "done": done,
        # 【★ cached 语义修正】原为 `not refresh` —— 只要不是显式刷新就报"命中缓存",
        #   哪怕就是**刚刚现算出来**的(实测第一轮就为真, 是假的)。
        #   现如实反映"本次拿到的是一份**没在刷新**的新鲜结果"。
        "cached": bool(done and not meta["refreshing"] and not meta["stale"] and not err),
        "refreshing": meta["refreshing"], "stale": meta["stale"],
        "refreshed": meta["refreshed"], "ts": meta["ts"], "err": err,
        "pools": ["全部品种"] + _pools, "rows": rows}))


@fund_bp.route("/api/fund/contracts")
def api_fund_contracts():
    """某品种的**跨月合约清单**(供 ⑨ 表每行的「月份下拉」行内展开)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, shape, spread, months:[{month, contract, price, oi,
            volume, months_to_delivery, is_main}], error?}。
    【口径】_fetch_term_structure 原样(含各交易所单位差异, 不做归一)。
    【★ 名字别名】新浪的品种命名表与项目内名称不一致(如 PX 在表内叫"二甲苯"),
      此前直接传项目名 ⇒ KeyError ⇒ 该品种期限结构/虚实比**全为空**。
      现由 _fetch_term_structure 内部逐候选名重试(见 _SINA_NAME_ALIASES)。
    """
    code = _code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    q = _q()
    meta = _meta(code)
    try:
        t = q._fetch_term_structure(code)
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 合约清单失败(%s): %s", code, e)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})
    if not t.get("ok"):
        return jsonify(_q()._json_safe({"ok": False, "variety": code,
                                   "name": meta.get("name", code),
                                   "error": t.get("error") or "该品种无跨月合约报价"}))
    main_contract = ((t.get("main") or {}).get("contract"))
    months = []
    for r in (t.get("rows") or []):
        months.append({"month": r.get("month"), "contract": r.get("contract"),
                       "price": r.get("price"), "oi": r.get("oi"), "volume": r.get("volume"),
                       "months_to_delivery": _months_to_delivery(r.get("month")),
                       "is_main": r.get("contract") == main_contract})
    return jsonify(_q()._json_safe({"ok": True, "variety": code, "name": meta.get("name", code),
                               "unit": meta.get("unit", ""),
                               "shape": t.get("shape"), "spread": t.get("spread"),
                               "main": t.get("main"), "count": len(months),
                               "months": months}))

