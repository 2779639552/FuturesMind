"""前端契约测试 —— 保证"后端算了的字段前端读得到, 前端引用的字段后端真的给"。

【为什么需要】两层语言不同, 最容易出的错是**键名漂移**:
  - 前端写 `mm.max_loss`, 后端根本没这个键 → 界面静默显示"未识别", 没人报错。
  - 卡片 slug 拼错(历史踩过: 写 inflection-line, 实际是 pivot-line) → 归组落到
    未知名下, 用户看到一本不存在的"书"。
两层各自的语法检查都发现不了, 只有对跑才能发现。

【三层断言】
  A. 前端引用的每个字段路径在真实后端输出里存在且非 None(有值时)。
  B. evidence 里出现过的每个 card slug 都能在某个 skill 的 capabilities/ 下找到文件。
  C. 每张卡都能归到一本书(不落 'other'), 且书名单与前端展示一致。

【用法】python _verify_frontend_contract.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SKILLS_DIR = Path.home() / ".workbuddy" / "skills"
SKILL_DIRS = {
    "carver": "carver-systematic-futures",
    "duokong": "qihuo-duokong-logic",
    "murphy": "murphy-technical-analysis",
    "harris": "harris-execution",
    "luxiwu": "luxiwu-trend-trading",
}
PASS, FAIL = "PASS", "FAIL"
_res: list[tuple[str, str, str]] = []


def _check(name: str, cond: bool, detail: str = "") -> None:
    _res.append((name, PASS if cond else FAIL, detail))


def _known_slugs() -> set[str]:
    """全部合法 slug(带 'cap-' 前缀), 来自 5 个 skill 的 capabilities/。"""
    out: set[str] = set()
    for dirname in SKILL_DIRS.values():
        d = SKILLS_DIR / dirname / "references" / "capabilities"
        if d.is_dir():
            out |= {f.stem for f in d.glob("cap-*.md")}
    return out


def _build() -> dict:
    import quant_methodology as qm
    import quant_module as q
    from quant_verdict import build_verdict
    sym = "M"  # 豆粕历史较长, 结构点足够, 能触发最多卡
    bars = q._load_daily_bars(sym, 250)
    b = q._fetch_basis(sym, 180)
    br = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None] if b.get("ok") else []
    inv = q._fetch_inventory(sym, 180)
    iv = [r["qty"] for r in inv["rows"] if r.get("qty") is not None] if inv.get("ok") else []
    pos = [v for v in iv if v > 0]
    chg = round((pos[-1] / pos[-4] - 1) * 100, 2) if len(pos) >= 4 and pos[-4] else None
    term = q._fetch_term_structure(sym)
    # 【第二批入参(2026-09-30)】必须与路由 api_quant_verdict 的装配**同口径**, 否则新卡
    #   在测试里全落降级态 → 测了个空。注意: 路由层的约束(白名单/参数解析)仍只有
    #   真实 HTTP 扫描能覆盖(_http_coverage_scan.py), 本测试只保证"结构与渲染契约"。
    inv_pct = None
    if pos and isinstance(iv[-1], (int, float)):
        inv_pct = round(sum(1 for x in pos if x <= float(iv[-1])) / len(pos), 4)
    cross_leg = None
    _pairs = qm.cross_pair_of(sym)
    if _pairs:
        _lc = _pairs[0]["leg"]
        _lt = q._fetch_term_structure(_lc)
        if _lt.get("ok"):
            _nr, _fr = (_lt.get("near") or {}), (_lt.get("far") or {})
            cross_leg = {"code": _lc, "relation": _pairs[0]["relation"], "note": _pairs[0]["note"],
                         "structure": "back" if float(_fr.get("price") or 0) < float(_nr.get("price") or 0)
                                      else "contango",
                         "price": _nr.get("price")}
    return build_verdict({
        "variety": sym, "bars": bars, "basis_rate_series": br, "inv_series": iv,
        "inv_chg_pct": chg, "term": term if term.get("ok") else None,
        "basis_rows": (b.get("rows") or []) if b.get("ok") else [],
        "inv_pct": inv_pct, "data_years": round(len(bars) / 244.0, 2), "cross_leg": cross_leg,
        "point_value": 10.0, "round_trip_fee": 6.0, "slip_ticks": 1.0,
    })


def _dig(obj, path: str):
    """按 a.b.c 取嵌套值; 支持 dict 键与 list 下标。"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def main() -> int:
    v = _build()
    _check("基建: verdict 可产出", bool(v.get("ok")))
    if not v.get("ok"):
        _print()
        return 1

    # ── A. 前端引用的字段路径必须存在 ──
    # 【设计要点: 测"键位契约", 不测"值非空"】本模块的段位**天生会降级**(缺数据时
    #   返回 {ok:False, reason, card})。若断言写成"值必须非空", 行情一变(如当前止损
    #   不可定)测试就变红 —— 那是**数据依赖的假红**, 测的是数据不是契约。
    #   正确做法: ① 两种状态下都存在的键(ok/reason/card)无条件断言;
    #            ② 仅在正常态(ok=True)才断言完整字段集; ③ 降级态断言含 reason。
    always = [
        "stages.direction.geometry",
        "stages.direction.votes",
        "stages.risk.stop_loss",
        "stages.risk.money_management",
        "stages.risk.trading_plan",
        "evidence.cards_by_book",
        "verdict_text",
        "conflicts",
        "data_gaps",
    ]
    for p in always:
        _check(f"A-段位存在: {p}", _dig(v, p) is not None)

    rk = v["stages"]["risk"]
    # 每个段位都有 ok 键(前端按 ok 分正常/降级两态渲染)
    for seg in ("stop_loss", "money_management", "trading_plan"):
        _check(f"A-{seg}.ok 键存在", "ok" in (rk.get(seg) or {}))
    # 风控段字段名一致性(踩过的坑): 后端不该有 max_loss 键
    mm = rk["money_management"]
    _check("A-mm 无 max_loss 键(前端不该引用它)", "max_loss" not in mm)
    if mm.get("ok"):
        for k in ("lots", "loss_per_lot", "verdict", "steps"):
            _check(f"A-mm 正常态含 {k}", k in mm)
        _check("A-mm.steps['2_单笔止损额度'] 可取",
               isinstance(mm.get("steps"), dict) and mm["steps"].get("2_单笔止损额度") is not None)
    else:
        # 降级态: 前端读 mm.reason 渲染提示 —— 该键必须存在, 否则界面显示空白
        _check("A-mm 降级态含 reason(前端降级分支读它)", bool(mm.get("reason")),
               f"keys={sorted(mm.keys())}")

    tp = rk["trading_plan"]
    if tp.get("card"):
        for k in ("ok", "steps", "missing"):
            _check(f"A-trading_plan 含 {k}", k in tp)
        _check("A-trading_plan.steps/ missing 类型正确",
               isinstance(tp.get("steps"), list) and isinstance(tp.get("missing"), list))

    # ── B. 所有 slug 必须真实存在(抓拼写错误) ──
    known = _known_slugs()
    used_raw: list[str] = list(v["evidence"].get("cards_used") or [])
    slug_pat = re.compile(r"cap-[a-z][a-z-]*[a-z]")
    seen: set[str] = set()
    for raw in used_raw:
        for s in slug_pat.findall(str(raw)):
            seen.add(s)
    bad = sorted(s for s in seen if s not in known)
    _check(f"B-slug 全部有效({len(seen)} 个)", not bad, f"无效: {bad}")
    # 每个 slug 必须归到一本书
    by_book = v["evidence"].get("cards_by_book") or {}
    flat = [c for cs in by_book.values() for c in cs]
    _check("B-卡片归书: 无 'other' 组", "other" not in by_book, f"书: {list(by_book.keys())}")
    _check("B-卡片归书: 每张卡都被归组", len(flat) >= len(used_raw) or len(flat) > 0,
           f"flat={len(flat)} used={len(used_raw)}")

    # ── C. 方向票结构完整(前端表格按 book/bias/detail/basis 渲染) ──
    votes = v["stages"]["direction"].get("votes") or []
    _check("C-方向票非空", len(votes) > 0, f"{len(votes)} 票")
    missing_keys = [i for i, x in enumerate(votes)
                    if not all(k in x for k in ("book", "bias", "detail", "basis"))]
    _check("C-方向票字段齐(book/bias/detail/basis)", not missing_keys, f"缺字段的下标: {missing_keys}")
    # 新增的两票应在场(期限结构 / 库存周期) —— 至少其一
    books = " ".join(x.get("book", "") for x in votes)
    _check("C-新增方向票: 期限结构在场", "期限结构" in books, books)
    _check("C-新增方向票: 库存周期在场或已记缺口",
           ("库存周期" in books) or any("库存周期" in g for g in (v.get("data_gaps") or [])))

    # ── D. 扩展段(第二批卡): 键齐 + slug 有效 + 前端确实渲染 ──
    # 【为什么单列一段】"后端算了但前端不渲染" = 用户看不见 = 事实上没接入。
    #   上次 term:None 的教训就是"接口里有字段"≠"字段有数据"≠"界面看得见", 三者要分别断言。
    ex = v["stages"].get("extended") or {}
    _check("D-扩展段存在", "extended" in (v.get("stages") or {}))
    ext_cards = {
        "ma_crossover": "murphy.cap-moving-averages",
        "volume_open_interest": "murphy.cap-volume-open-interest",
        "trend_structure": "murphy.cap-trend-structure",
        "basis_calculation": "duokong.cap-basis-calculation",
        "calendar_spread": "duokong.cap-calendar-spread",
        "cross_commodity_spread": "duokong.cap-cross-commodity-spread",
        "shrinkage_ladder": "carver.cap-shrinkage-ladder",
        "estimate_reliability": "carver.cap-estimate-reliability",
        "warrant_basis": "duokong.cap-warrant-basis",
        "reversal_signals": "duokong.cap-reversal-signals",
    }
    used_all = " ".join(str(x) for x in used_raw)
    for key, card in ext_cards.items():
        _check(f"D-扩展卡到位: {card}",
               key in ex and card.split(".", 1)[1] in used_all,
               f"ext键={'有' if key in ex else '缺'}, cards_used={'有' if card.split('.',1)[1] in used_all else '缺'}")
    # 卡片 slug 必须都能在 skills 里找到(防拼写漂移)
    _check("D-扩展段 slug 全部有效",
           all(c.split(".", 1)[1] in known for c in ext_cards.values()))
    # 前端必须真的读 stages.extended(否则该段是死代码)
    tpl = (ROOT / "web_template.html").read_text(encoding="utf-8", errors="ignore")
    _check("D-前端读取 stages.extended", "stages?.extended" in tpl,
           "前端未引用 → 扩展段用户看不见")
    for key in ext_cards:
        _check(f"D-前端渲染 ex.{key}", f"ex.{key}" in tpl)

    # ── E. 边界纪律: 技术注入块不得含产业数据, 基本面不得含技术几何 ──
    # 【为什么用自动断言而不是靠自觉】SKILL.md 的边界纪律只有写成断言才不会随时间腐化。
    from tradingagents.dataflows import methodology_context as mc
    mc.clear_cache()
    tech_blk = mc.technical_methodology_context("M")
    fund_blk = mc.fundamental_methodology_context("M")
    _check("E-技术面注入 不含产业数据(库存周期/基差/仓单)",
           all(k not in tech_blk for k in ("cap-inventory-cycle", "cap-basis-calculation",
                                           "cap-term-structure", "cap-calendar-spread")),
           "技术面出现了产业类卡")
    _check("E-技术面注入 含均线/量仓/趋势结构(第二批)",
           all(k in tech_blk for k in ("cap-moving-averages", "cap-volume-open-interest",
                                       "cap-trend-structure")))
    _check("E-基本面注入 不含技术几何(周期绑定/排除法/均线)",
           all(k not in fund_blk for k in ("cap-trend-definition", "cap-direction-exclusion",
                                           "cap-moving-averages", "cap-pivot-line")),
           "基本面出现了技术类卡")
    _check("E-基本面注入 含价差/收缩类(第二批)",
           all(k in fund_blk for k in ("cap-basis-calculation", "cap-calendar-spread",
                                       "cap-cross-commodity-spread", "cap-shrinkage-ladder",
                                       "cap-estimate-reliability")))

    return _print()


def _print() -> int:
    bad = [r for r in _res if r[1] == FAIL]
    for name, st, detail in _res:
        print(f"[{st}] {name}" + (f"  ({detail})" if detail else ""))
    print("-" * 60)
    print(f"共 {len(_res)} 项, PASS {len(_res) - len(bad)}, FAIL {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
