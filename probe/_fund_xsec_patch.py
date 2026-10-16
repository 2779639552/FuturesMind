"""基本面截面表 —— 后端一次性补丁（2026-10-16）。

【改动面】
  quant_module.py
    Q1 新增 _SINA_NAME_ALIASES + _term_realtime_df（逐候选名重试）
    Q2 _fetch_term_structure 改用它（修 FU/P/PG/PX 月差/合约报价取数失败）
    Q3 修正模块头 coverage 里"跨月合约报价 21/21"的错误留档
  fund_module.py
    F1 ④ 卡的比值算术改调 _vr_ratio_of（合并到唯一定义处）
    F2 文件末尾追加 ⑨ 截面表全套（helpers 文件原样）

【★ EOL】quant_module / fund_module 均为 LF；本脚本统一按 LF 读写并断言。
【★ 不幂等】"末尾追加"重跑会重复追加 ⇒ 开头检测标记即明确拒绝重跑。

用法: python probe/_fund_xsec_patch.py [--write]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = (ROOT / "probe" / "_fund_xsec_helpers.py.txt").read_text(encoding="utf-8")

MARKERS = {"quant_module.py": "_SINA_NAME_ALIASES", "fund_module.py": "_TTL_FUND_RANK"}

# ── quant_module: 别名表 + 重试取数 ─────────────────────────────────────────
Q_ANCHOR = '''def _fetch_term_structure(code: str) -> dict:'''

Q_ALIAS_BLOCK = '''# ── 新浪命名别名表(2026-10-16) ────────────────────────────────────────────
# 【为什么需要】`ak.futures_zh_realtime(symbol=...)` 内部查的是**新浪的品种命名表**
#   (`ak.futures_symbol_mark()`), 该表的品种名与项目内 `VARIETY_METADATA.name`
#   **并不同源**: 表里用的是俗称/别名。实测 2026-10-16:
#       项目名            新浪表名
#       对二甲苯(PX)  →   二甲苯
#       燃料油(FU)    →   燃油
#       棕榈油(P)     →   棕榈
#       LPG(PG)       →   液化石油气
#   直接传项目名 ⇒ KeyError ⇒ 这些品种的**期限结构 / 虚实盘比 / 月差全为空**,
#   而在界面上表现为"无跨月合约报价"—— 看着像**数据源无覆盖**, 实为**取名失败**。
#   (这正是本项目"三态不可混"的原型: 确定无数据 vs 取数失败必须区分。)
# 【变量】顺序 = 候选优先级: 先项目名(多数品种命中), 再逐个试别名。
_SINA_NAME_ALIASES: dict[str, list[str]] = {
    "PX": ["对二甲苯", "二甲苯"],
    "FU": ["燃料油", "燃油"],
    "P": ["棕榈油", "棕榈"],
    "PG": ["LPG", "液化石油气"],
    "LU": ["低硫燃料油"],
}


def _term_realtime_df(ak, code: str, name: str):
    """逐个候选名试 `ak.futures_zh_realtime`, 返回 (df, 命中的名字, 试过的名字列表)。

    【★ 为什么要逐名重试而不是加一张"覆盖表"】别名表是**外部命名表的知识**, 会随
      数据源改版而失效; 用"逐个试直到成功"能把**新增**的命名差异自动吸收, 表只负责
      提高命中优先级。全部失败时把"试过哪些"如实带回 error(区分"无覆盖"与"取名失败")。
    """
    cands = list(_SINA_NAME_ALIASES.get(code, [])) or [name]
    if name not in cands:
        cands = [name] + cands
    tried: list[str] = []
    for cand in cands:
        if not cand:
            continue
        tried.append(cand)
        try:
            df = ak.futures_zh_realtime(symbol=cand)
        except Exception as e:  # noqa: BLE001 - 换下一个候选名继续试
            logger.debug("term 候选名 %s 失败(%s): %s", cand, code, e)
            continue
        if df is not None and not df.empty:
            return df, cand, tried
    return None, None, tried


'''

Q_OLD = '''    meta = _meta(code)
    name = meta.get("name") or code
    try:
        df = ak.futures_zh_realtime(symbol=name)
    except Exception as e:  # noqa: BLE001 - 任一次失败降级为空
        res = {"ok": False, "error": f"合约报价接口异常: {e}", "rows": []}
        _cache_set(key, res)
        return res'''

Q_NEW = '''    meta = _meta(code)
    name = meta.get("name") or code
    # 【2026-10-16 修】新浪命名表与项目名不同源 ⇒ 逐候选名重试(见 _SINA_NAME_ALIASES)。
    df, name_used, tried = _term_realtime_df(ak, code, name)
    if df is None:
        res = {"ok": False, "rows": [],
               "error": f"合约报价取名失败: 试过 {'/'.join(tried)} 均不可用"
                        f"(新浪命名表与项目名称不同源)"}
        _cache_set(key, res)
        return res'''

# 模块头 coverage 留档里的错误数字 → 修正(并注明实测日)
Q_DOC_OLD = '''  - 跨月合约报价:       akshare futures_zh_realtime(全合约) —— 21/21'''
Q_DOC_NEW = '''  - 跨月合约报价:       akshare futures_zh_realtime(全合约) —— 21/21
                        ⚠ 2026-10-16 实测复核: 原"21/21"是**错的**, 实际 17/21 ——
                        FU/P/PG/PX 因新浪命名表用别名(燃油/棕榈/液化石油气/二甲苯)
                        而 KeyError。已加 _SINA_NAME_ALIASES 逐候选名重试修复。'''

# ── fund_module: 比值算术合并 ───────────────────────────────────────────────
F_OLD = '''    ratio = None
    if inv_out.get("ok") and term_out.get("ok") and inv_out.get("lots"):
        if inv_out["lots"] and float(inv_out["lots"]) > 0:
            ratio = round(float(term_out["one_side_oi"]) / float(inv_out["lots"]), 4)'''

F_NEW = '''    # 【2026-10-16】改调 _vr_ratio_of —— 让虚实盘比的算术**只有一处**(见其 docstring);
    #   ⑨ 截面表也要这个比值, 两处各写一遍迟早漂移。
    ratio = _vr_ratio_of(inv_out.get("lots") if inv_out.get("ok") else None,
                         term_out.get("one_side_oi") if term_out.get("ok") else None)'''

# 【追加锚点】fund_module 末尾那段唯一文本(用它当锚, 因为 "\n" 会命中 800+ 次)
F_TAIL = '''        "card": "qihuo-duokong-logic.cap-warrant-basis",
    })'''


def patch(name: str, ops: list, write: bool) -> bool:
    p = ROOT / name
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    bare = raw.count(b"\n") - crlf
    assert crlf == 0 or bare == 0, "%s EOL 混合, 拒绝改动" % name
    eol = "\r\n" if crlf else "\n"
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    ok = True
    print("  ── %s (%s) ──" % (name, "CRLF" if crlf else "LF"))
    for old, new, want, why in ops:
        got = txt.count(old)
        print("    %s %-40s 命中 %d (期望 %d)" % ("OK " if got == want else "★  ", why, got, want))
        if got != want:
            ok = False
            continue
        txt = txt.replace(old, new, want)
    if not ok:
        print("    ⇒ 有锚点未命中, **本文件未写入**")
        return False
    if write:
        p.write_bytes(txt.replace("\n", eol).encode("utf-8"))
        chk = p.read_bytes()
        c2, b2 = chk.count(b"\r\n"), chk.count(b"\n") - chk.count(b"\r\n")
        assert (c2 > 0 and b2 == 0) if crlf else (c2 == 0), "写回后 EOL 破坏"
        print("    ✔ 已写入 (%d 字节, EOL 保持)" % len(chk))
    else:
        print("    ✔ 空跑通过")
    return True


def main() -> int:
    write = "--write" in sys.argv
    for name, mark in MARKERS.items():
        if mark in (ROOT / name).read_bytes().decode("utf-8", "ignore"):
            print("★ %s 已含标记 %r —— 本脚本不幂等, 请勿重跑(从 backup 还原再跑)。" % (name, mark))
            return 2
    print("=== %s ===" % ("写入模式" if write else "空跑模式(加 --write 落盘)"))
    ok = True
    ok &= patch("quant_module.py", [
        (Q_ANCHOR, Q_ALIAS_BLOCK + Q_ANCHOR, 1, "Q1 别名表 + _term_realtime_df"),
        (Q_OLD, Q_NEW, 1, "Q2 _fetch_term_structure 逐候选名重试"),
        (Q_DOC_OLD, Q_DOC_NEW, 1, "Q3 修正 coverage 留档的错误数字"),
    ], write)
    ok &= patch("fund_module.py", [
        (F_OLD, F_NEW, 1, "F1 ④卡比值改调 _vr_ratio_of"),
        # 【★ 不能用 "\\n" 当追加锚点】它命中 868 次。改用文件**末尾那段唯一文本**。
        (F_TAIL, F_TAIL + "\n\n" + HELPERS.rstrip("\n") + "\n", 1, "F2 追加 ⑨ 截面表全套"),
    ], write)
    print()
    print("=== %s ===" % ("全部通过" if ok else "★ 有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
