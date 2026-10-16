"""基本面截面表护栏的变异自检 —— 2026-10-16。

【为什么必须做】护栏"全绿"不等于"有效"。本脚本把每条护栏对应的**缺陷**植回源码,
  跑护栏必须**变红**; 仍全绿 ⇒ 那条是假护栏。
【安全】逐个变异 → 跑 → **立即从内存原字节还原** → md5 比对确认复原。
  ⚠ 运行期间**不得**与全量回归并发(会临时改写源文件)。
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TEST = "tests/test_fund_xsection.py"

QM = "quant_module.py"
FM = "fund_module.py"
TP = "web_template.html"

MUTATIONS = [
    # ── 口径不新增 ──
    ("M1 ④卡路由改回内联比值算术", FM,
     'ratio = _vr_ratio_of(inv_out.get("lots") if inv_out.get("ok") else None,\n'
     '                         term_out.get("one_side_oi") if term_out.get("ok") else None)',
     'ratio = (round(float(term_out.get("one_side_oi")) / float(inv_out["lots"]), 4)\n'
     '             if (inv_out.get("ok") and term_out.get("ok") and inv_out.get("lots")) else None)', 1),
    ("M2 4票不再调 build_verdict(自算一套)", FM,
     "        res = qv.build_verdict(payload)",
     "        res = {'ok': True, 'stages': {'direction': {'votes': []}}, 'data_gaps': []}", 1),
    ("M3 book 名改错一个字(列会静默变空)", FM,
     '    ("term", "多空逻辑(期限结构)"),',
     '    ("term", "多空逻辑(期限结构 )"),', 1),
    ("M4 缺口前缀改名(空白原因会丢失)", FM,
     '"prof": "利润维度:",',
     '"prof": "利润数据缺失:",', 1),
    ("M5 截面行自算档位(不走 _inv_dual)", FM,
     '        row["inv_grade"] = dual.get("grade")',
     '        row["inv_grade"] = _grade(row.get("ck_pct"))', 1),

    # ── 月差别名 ──
    ("M6 别再名字典里的关键品种", QM,
     '    "PX": ["对二甲苯", "二甲苯"],\n',
     '', 1),
    ("M7 退回直接传项目名", QM,
     "    df, name_used, tried = _term_realtime_df(ak, code, name)",
     "    df, name_used, tried = ak.futures_zh_realtime(symbol=name), name, [name]", 1),
    ("M8 空 DataFrame 不换候选(提前返回)", QM,
     "        if df is not None and not df.empty:\n            return df, cand, tried",
     "        if df is not None:\n            return df, cand, tried", 1),

    # ── 量纲 ──
    ("M9 basis_pct 退回百分数(与另两列不同量纲)", FM,
     'row["basis_pct"] = round(sum(1 for v in vals if v <= cur) / len(vals), 4)',
     'row["basis_pct"] = round(sum(1 for v in vals if v <= cur) / len(vals) * 100, 1)', 1),
    ("M10 库存档位方向序写反(低=-1)", FM,
     '_INV_GRADE_RANK = {"低": 1, "中": 0, "高": -1}',
     '_INV_GRADE_RANK = {"低": -1, "中": 0, "高": 1}', 1),
    ("M11 inv_rank 不在行内算(缓存行缺字段)", FM,
     '        row["inv_rank"] = _INV_GRADE_RANK.get(row["inv_grade"])\n',
     '', 1),
    ("M12 分位改成百分数(前端会显示 3100%)", FM,
     '        row["ck_pct"] = ck.get("pct_rank")',
     '        row["ck_pct"] = None if ck.get("pct_rank") is None else ck["pct_rank"] * 100', 1),

    # ── 前端结构/交互 ──
    ("M13 行不再绑 fOpenDetail", TP,
     """onclick="App.fOpenDetail('${r.variety}')" title="点击进入""",
     """onclick="" title="点击进入""", 1),
    ("M14 月份下拉不阻止冒泡", TP,
     'onclick="event.stopPropagation()" onchange="App._fExpandContracts(',
     'onchange="App._fExpandContracts(', 1),
    ("M15 明细视图不再隐藏(首屏叠加)", TP,
     '<div id="f-detail-view" style="display:none">',
     '<div id="f-detail-view">', 1),
    ("M16 switchTab 不再按视图分派", TP,
     "  if (fov && fov.style.display === 'none') this.loadFund();\n  else this.loadFundRanking();",
     "  this.loadFund();", 1),
    ("M17 meta 不采用 pending 品种(点行被顶掉)", TP,
     " sel.value = _fWant || ((pref && _fValid(pref.code)) ? pref.code : (d.varieties?.[0]?.code || ''));",
     " sel.value = ((pref && _fValid(pref.code)) ? pref.code : (d.varieties?.[0]?.code || ''));", 1),
    ("M18 版本号回落 27", TP,
     "_SEC_IDX_VER: '28',",
     "_SEC_IDX_VER: '27',", 1),
    ("M19 ①卡基差率退回不 ×100", TP,
     "(Number(bs.rate) * 100).toFixed(2) + '%'",
     "Number(bs.rate).toFixed(2) + '%'", 1),
    ("M20 返回按钮不再绑 fBackOverview", TP,
     'onclick="App.fBackOverview()"',
     'onclick=""', 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run() -> tuple:
    r = subprocess.run([str(PY), "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:warnings"],
                       cwd=str(ROOT), capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    tail = [ln for ln in out.splitlines()
            if "passed" in ln or "failed" in ln or "error" in ln]
    # ★ 判定不能只认 "failed" —— 变异若把文件改成**语法错**, pytest 报的是
    #   `errors during collection`(不含 "failed") ⇒ 会被误记成"存活"(实测踩到)。
    bad = ("failed" in out) or ("error" in out)
    return bad, (tail[-1] if tail else out.strip()[-110:])


def main() -> int:
    files = [QM, FM, TP]
    originals = {f: (ROOT / f).read_bytes() for f in files}
    before = {f: md5(b) for f, b in originals.items()}
    t_before = md5((ROOT / TEST).read_bytes())
    print("=== 基线 ===")
    failed, line = run()
    print("   %s" % line)
    if failed:
        print("★ 基线就是红的 —— 先修基线")
        return 2

    killed, alive, skipped = [], [], []
    print()
    print("=== 逐个变异 ===")
    for tag, fname, old, new, want in MUTATIONS:
        raw = originals[fname]
        crlf = raw.count(b"\r\n") > 0
        txt = raw.decode("utf-8").replace("\r\n", "\n")
        got = txt.count(old)
        if got != want:
            print("    %-42s ★ 锚点命中 %d (期望 %d) —— 跳过(变异脚本自身问题)" % (tag, got, want))
            skipped.append(tag)
            continue
        data = txt.replace(old, new, want)
        data = data.replace("\n", "\r\n").encode("utf-8") if crlf else data.encode("utf-8")
        try:
            (ROOT / fname).write_bytes(data)
            failed, line = run()
        finally:
            (ROOT / fname).write_bytes(raw)
        (killed if failed else alive).append(tag)
        print("    %-42s %s  %s" % (tag, "击杀 ✔" if failed else "★ 存活(假护栏!)", line[:52]))

    print()
    print("=== 源文件复原自检 ===")
    ok = True
    for f in files:
        same = md5((ROOT / f).read_bytes()) == before[f]
        ok &= same
        print("    %-22s %s" % (f, "md5 一致 ✔" if same else "★ md5 变了!"))
    same_t = md5((ROOT / TEST).read_bytes()) == t_before
    ok &= same_t
    print("    %-22s %s" % (TEST, "md5 一致 ✔" if same_t else "★ 测试文件被改!"))

    print()
    print("=" * 62)
    print("击杀 %d / 存活 %d / 跳过 %d" % (len(killed), len(alive), len(skipped)))
    if alive:
        print("存活(需补强护栏):")
        for a in alive:
            print("  ★ " + a)
    if skipped:
        print("跳过(锚点没对上, 需修变异脚本):")
        for a in skipped:
            print("  - " + a)
    if not ok:
        print("★★ 源文件未完全复原!")
    return 0 if (ok and not alive) else 1


if __name__ == "__main__":
    raise SystemExit(main())
