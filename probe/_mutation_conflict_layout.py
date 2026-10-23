# -*- coding: utf-8 -*-
"""变异自检：第二节「列宽份额 + 每家一行」护栏（2026-10-19）。

【纪律】改 → 测 → 复原（finally）→ 末尾核对 md5；**与全量回归串行**，绝不并发。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
TPL = ROOT / "web_template.html"
TARGET = "tests/test_conflict_table_layout.py"


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def write(p: Path, text: str) -> None:
    p.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run() -> bool:
    r = subprocess.run(
        [PY, "-m", "pytest", TARGET, "-q", "--no-header", "-p", "no:warnings", "--tb=no"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return r.returncode == 0


H0 = md5(TPL)

MUTS = [
    # ── 列宽份额 ───────────────────────────────────────────────────────────
    ("M1 分歧状态改回 120px（放大后仍是第二宽列）", TPL,
     "  width: 76px; text-align: center; white-space: nowrap;",
     "  width: 120px; text-align: center; white-space: nowrap;"),
    # ★ 最关键的一条：把唯一 auto 列改回定宽 ⇒ 所有列都有定宽 ⇒ 浏览器等比放大
    #   ⇒ 分歧状态 76px 实际渲染成 141px（正是用户二次反馈的现象）
    ("M2 观点列 auto 改回定宽（全表定宽 ⇒ 等比放大，分歧状态又变 141px）", TPL,
     "  width: auto;                                               /* ★ 唯一 auto 列：吃剩余空间 */",
     "  width: 360px;                                              /* ★ 唯一 auto 列：吃剩余空间 */"),
    ("M2b 多出一个 auto 列（剩余空间被平分，观点列拿不到最大份额）", TPL,
     ".report-content table.rv-conflict-tbl th:nth-child(4),\n.report-content table.rv-conflict-tbl td:nth-child(4) { width: 20%; }     /* 分歧焦点 */",
     ".report-content table.rv-conflict-tbl th:nth-child(4),\n.report-content table.rv-conflict-tbl td:nth-child(4) { width: auto; }     /* 分歧焦点 */"),
    ("M3 去掉第二节专属作用域（波及总表）", TPL,
     ".report-content table.rv-conflict-tbl th:nth-child(2),",
     ".report-content table th:nth-child(2),"),
    # ── 接线 / 定位 ────────────────────────────────────────────────────────
    ("M4 断开接线（方法写了但页面不生效）", TPL,
     " this._decorateConflictTable(el);\n } catch (e) {",
     " this._decorateConflictTableX(el);\n } catch (e) {"),
    ("M5 定位改按列序号（列序一变就切错列）", TPL,
     "const iState = ths.findIndex(th => (th.textContent || '').trim() === '分歧状态');",
     "const iState = 1;"),
    ("M6 去掉双表头守卫（误伤其它表格）", TPL,
     "if (iState < 0 || iLogic < 0) return;        // 只认第二节这张表，不误伤其它表格",
     "if (iState < 0 && iLogic < 0) return;        // 只认第二节这张表，不误伤其它表格"),
    # ── 拆行 ──────────────────────────────────────────────────────────────
    ("M7 只认未转义的 <br>（真实页面切不开）", TPL,
     "const BR = /(?:&lt;|<)br\\s*\\/?(?:&gt;|>)/gi;",
     "const BR = /<br\\s*\\/?(?:>)/gi;"),
    # ★ 用户实际反馈的那条：判据退化成「按 <br> 段数」，单家内部多段会被当多家拆开
    ("M8 判据退化为按 <br> 段数（单家内部多段 ⇒ 被拆成三行 + 两条虚线）", TPL,
     "if (heads < 2) {\n// 只有一家（或结构不符合\"多家\"）⇒ 不拆段、**不加分隔线**；",
     "if (parts.length < 2) {\n// 只有一家（或结构不符合\"多家\"）⇒ 不拆段、**不加分隔线**；"),
    ("M8b 单家分支里偷偷生成 cf-row（一家被拆成多段）", TPL,
     "td.innerHTML = parts.join('<br>');",
     "td.innerHTML = parts.map(x => '<div class=\"cf-row\">' + x + '</div>').join('');"),
    # ★ 用户第四次反馈那条：漏写 <br> 时两家粘连
    ("M8c 去掉「每段内部二次切分」（漏写 <br> 时第三家粘在第二家尾部）", TPL,
     "parts = parts.flatMap(x => x.split(SPLITTER)",
     "parts = parts.slice(); parts.forEach(x => {}); parts = parts.concat([].concat(x => x.split(SPLITTER)"),
    ("M8d 切分锚点改用模糊通配（会把「国泰君安」切成「泰君安/君安」）", TPL,
     "const SPLITTER = new RegExp('(?=(?:' + SRC + ')",
     "const SPLITTER = new RegExp('(?=[\\u4e00-\\u9fa5]{2,8}\\s*(?:' + DIR + ')"),
    ("M8e 去掉前缀归并（带序号的多家切出独立的「①」段）", TPL,
     "if (prefixOnly.test(seg)) { carry += seg + ' '; continue; }",
     "if (false) { carry += seg + ' '; continue; }"),
    ("M9 去掉单元格级幂等（重复调用层层包裹）", TPL,
     "if (!td || td.dataset.cfSplit === '1') return;",
     "if (!td) return;"),
    ("M10 去掉表级幂等（切日期反复处理）", TPL,
     "if (tbl.dataset && tbl.dataset.cfDone === '1') return;",
     "if (false) return;"),
    ("M11 段间分隔线丢掉（读起来仍是一整块）", TPL,
     "margin-top: 5px; padding-top: 5px; border-top: 1px dashed var(--border-light);",
     "margin-top: 0px; padding-top: 0px;"),
    ("M12 不生成 cf-row 包裹（换行没实现）", TPL,
     "td.innerHTML = parts.map(x => '<div class=\"cf-row\">' + x + '</div>').join('');",
     "td.innerHTML = parts.join('<br>');"),
]

print("=" * 78)
print("  变异自检：%d 个变异" % len(MUTS))
print("=" * 78)
killed, survived, skipped = 0, [], []
for name, path, old, new in MUTS:
    t = read(path)
    c = t.count(old)
    if c != 1:
        skipped.append(name)
        print("  ⚠ 跳过  %s（锚点命中 %d 次）" % (name, c))
        continue
    write(path, t.replace(old, new, 1))
    try:
        passed = run()
    finally:
        write(path, t)
    if passed:
        survived.append(name)
        print("  ✗ 存活  %s  ← 护栏有洞" % name)
    else:
        killed += 1
        print("  ✓ 击杀  %s" % name)

print()
print("  结果: %d 击杀 / %d 存活 / %d 跳过" % (killed, len(survived), len(skipped)))
print("  复原校验: template %s" % ("OK" if md5(TPL) == H0 else "★不一致"))
sys.exit(0 if not survived and not skipped and md5(TPL) == H0 else 1)
