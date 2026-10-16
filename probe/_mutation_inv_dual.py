"""库存双口径护栏的变异自检 —— 2026-10-16。

【为什么必须做】护栏"全绿"不等于"有效"。本脚本把每一条护栏对应的**缺陷**人为植回
  源码, 跑护栏时必须**变红**; 若仍全绿 ⇒ 那条护栏是假护栏(本轮实测抓到过 1 条:
  只断言 `startsWith(...)` 出现, 而同方法里另一行本身就含该子串 ⇒ 删掉判据也能过)。

【安全】逐个变异 → 跑 → **立即从内存原字节还原** → 比对 md5 确认复原。
  不依赖"我以为恢复了"; 任何一步异常都会在 finally 里还原。
  运行期间**不得**与全量回归并发(会临时改写源文件)。
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "venv" / "Scripts" / "python.exe" if (ROOT / "venv" / "Scripts" / "python.exe").exists() else sys.executable
TEST = "tests/test_inventory_dual.py"

QM = "quant_module.py"
FM = "fund_module.py"
TP = "web_template.html"

# (说明, 文件, 旧串, 新串, 期望命中数)
MUTATIONS = [
    ("M1 矛盾分支被删(一低一高落到'中')", QM,
     '    if set([ck_g, so_g]) == set(["低", "高"]):\n        return dict(out, grade="矛盾"',
     '    if False:\n        return dict(out, grade="矛盾"', 1),
    ("M2 仓单 0 值被剔除(违反'保留原值')", QM,
     '    qty = [r.get("qty") for r in rows if r.get("qty") is not None]\n    if not qty:\n        return {"ok": False, "error": "无仓单数据"',
     '    qty = [r.get("qty") for r in rows if r.get("qty") is not None and r.get("qty") > 0]\n    if not qty:\n        return {"ok": False, "error": "无仓单数据"', 1),
    ("M3 0 值警示措辞改成武断断言", QM,
     '"0 可能表示无注册仓单或集中注销, 不必然等于库存极低 —— 请结合社会库存判断"',
     '"该品种仓单数据源疑似断供"', 1),
    ("M4 _inv_grade 阈值与 fund 脱钩", QM,
     '    return "低" if rank <= lo else ("高" if rank >= hi else "中")',
     '    return "低" if rank <= 0.2 else ("高" if rank >= 0.8 else "中")', 1),
    ("M5 available 少算一侧", QM,
     '"available": [n for n, g in (("社会库存", so_g), ("仓单", ck_g)) if g != "不明"]}',
     '"available": [n for n, g in (("社会库存", so_g),) if g != "不明"]}', 1),
    ("M6 _inv_chg 不再跳过 0 占位", QM,
     '    pos = [v for v in qty if v is not None and v > 0]',
     '    pos = [v for v in qty if v is not None]', 1),
    ("M7 单侧口径不点名(退化为笼统 mode)", QM,
     '                    note="该品种无国君社会库存数据, 库存档位只由仓单(%s)给出" % ck_g)',
     '                    note="库存档位已给出")', 1),
    ("M8 前端去掉交易所仓单行", TP,
     " const invSub = dualLine('社会库存', soc) + dualLine('交易所仓单', ckv) + ckWarn;",
     " const invSub = dualLine('社会库存', soc) + ckWarn;", 1),
    ("M9 前端不再渲染 0 值警示", TP,
     " const ckWarn = ckv.warn\n ? `<div style=\"font-size:0.75rem;color:var(--amber);line-height:1.7\">${ckv.warn}</div>` : '';",
     " const ckWarn = '';", 1),
    ("M10 四象限不再插双口径两行", TP,
     " ${socLine}${crossLine}",
     "", 1),
    ("M11 旧单口径文案被写回", TP,
     " 库存(<b>社会库存 + 交易所仓单</b> 双口径综合分位) + 利润",
     " 库存(仓单分位) + 利润", 1),
    ("M12 档位→方向词映射被删", TP,
     " const invWord = inv.grade === '低' ? '偏多' : (inv.grade === '高' ? '偏空' : '中性');",
     " const invWord = '中性';", 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_tests() -> tuple:
    r = subprocess.run([str(PY), "-m", "pytest", TEST, "-q", "--no-header",
                        "-p", "no:warnings"], cwd=str(ROOT),
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
    failed = "failed" in out
    return failed, (tail[-1] if tail else out.strip()[-120:])


def main() -> int:
    files = [QM, FM, TP]
    originals = {f: (ROOT / f).read_bytes() for f in files}
    before = {f: md5(b) for f, b in originals.items()}
    test_before = md5((ROOT / TEST).read_bytes())
    print("=== 基线(无变异) ===")
    failed, line = run_tests()
    print("   %s" % line)
    if failed:
        print("★ 基线就是红的 —— 先修基线再跑变异")
        return 2

    killed, alive = [], []
    print()
    print("=== 逐个变异 ===")
    for tag, fname, old, new, want in MUTATIONS:
        raw = originals[fname]
        crlf = raw.count(b"\r\n") > 0
        txt = raw.decode("utf-8").replace("\r\n", "\n")
        got = txt.count(old)
        if got != want:
            print("    %-34s ★ 锚点命中 %d (期望 %d) —— 跳过(变异脚本自身问题)" % (tag, got, want))
            continue
        mutated = txt.replace(old, new, want)
        data = mutated.replace("\n", "\r\n").encode("utf-8") if crlf else mutated.encode("utf-8")
        try:
            (ROOT / fname).write_bytes(data)
            failed, line = run_tests()
        finally:
            (ROOT / fname).write_bytes(raw)          # 立即还原
        status = "击杀 ✔" if failed else "★ 存活(假护栏!)"
        print("    %-34s %s   %s" % (tag, status, line.replace("in ", "in ")[:60]))
        (killed if failed else alive).append(tag)

    print()
    print("=== 源文件复原自检 ===")
    ok = True
    for f in files:
        now = md5((ROOT / f).read_bytes())
        same = now == before[f]
        ok &= same
        print("    %-22s %s" % (f, "md5 一致 ✔" if same else "★ md5 变了! 需要手工还原"))
    tnow = md5((ROOT / TEST).read_bytes())
    ok &= tnow == test_before
    print("    %-22s %s" % (TEST, "md5 一致 ✔" if tnow == test_before else "★ 测试文件被改动!"))

    print()
    print("=" * 62)
    print("击杀 %d / 存活 %d / 跳过 %d" % (len(killed), len(alive), len(MUTATIONS) - len(killed) - len(alive)))
    if alive:
        print("存活(需补强护栏):")
        for a in alive:
            print("  ★ " + a)
    if not ok:
        print("★★ 源文件未完全复原, 请立即检查!")
    return 0 if (ok and not alive) else 1


if __name__ == "__main__":
    raise SystemExit(main())
