# -*- coding: utf-8 -*-
"""变异自检：验证本轮护栏（纵向对齐 + 观点口径）真的咬得住。

【纪律】每个变异：改文件 → 跑测试 → 期望**失败** → 复原 → md5 复核。
        任一变异"存活"（测试仍绿）说明护栏有洞，必须先补护栏再继续。
【EOL】两个源文件都是 CRLF，变异时统一 normalize 成 LF 再写回 CRLF。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
TPL = ROOT / "web_template.html"
APP = ROOT / "web_app.py"
TARGET = "tests/test_views_vstack_opinion.py"


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def write(p: Path, text: str) -> None:
    p.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run() -> bool:
    """返回 True = 测试通过（变异存活）。"""
    r = subprocess.run(
        [PY, "-m", "pytest", TARGET, "-q", "--no-header", "-p", "no:warnings", "--tb=no"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return r.returncode == 0


H0, A0 = md5(TPL), md5(APP)

MUTS = [
    # ── 对齐机制 ────────────────────────────────────────────────────────────
    ("M1 tpl: .src-item 高度 20px → auto（高度随内容变 ⇒ 逐项错位）", TPL,
     ".src-item { display:flex; align-items:center; height:20px; box-sizing:border-box;",
     ".src-item { display:flex; align-items:center; height:auto; box-sizing:border-box;"),
    ("M2 tpl: 去掉 .dir-chip 的 box-sizing（padding 撑破 20px）", TPL,
     ".dir-chips > .dir-chip { display:inline-flex; align-items:center; height:20px; box-sizing:border-box; }",
     ".dir-chips > .dir-chip { display:inline-flex; align-items:center; height:20px; }"),
    ("M3 tpl: 置信度列 gap 5px → 4px（三列间距不一致）", TPL,
     ".conf-bars { display:flex; flex-direction:column; gap:5px; align-items:flex-start; }",
     ".conf-bars { display:flex; flex-direction:column; gap:4px; align-items:flex-start; }"),
    ("M4 tpl: td 顶部对齐 → middle（内容不等的格中心对齐 ⇒ 首项错开）", TPL,
     ".report-content table td.rv-vstack { vertical-align:top; }",
     ".report-content table td.rv-vstack { vertical-align:middle; }"),
    ("M5 tpl: 占位 visibility:hidden → display:none（高度塌陷）", TPL,
     ".src-item.ghost, .dir-chip.ghost, .conf-bar.ghost { visibility:hidden; }",
     ".src-item.ghost, .dir-chip.ghost, .conf-bar.ghost { display:none; }"),
    # ── 前端接线 ────────────────────────────────────────────────────────────
    ("M6 tpl: 方向列不再接收发行方家数（少一项就错行）", TPL,
     "if (tds[iDir]) this._vizDirCell(tds[iDir], n);",
     "if (tds[iDir]) this._vizDirCell(tds[iDir]);"),
    ("M7 tpl: 不打总表标记（列宽覆盖失效 / 波及全表）", TPL,
     "if (tbl.classList) tbl.classList.add('rv-views-tbl');",
     "if (tbl.classList) tbl.classList.add('rv-views-tbl-x');"),
    # ── 后端解析 ────────────────────────────────────────────────────────────
    ("M8 app: 去掉「二、」分节截断（第二节分歧根源冒充观点）", APP,
     '    seg = body[:_m2.start()] if _m2 else body',
     '    seg = body'),
    ("M9 app: 方向校验恢复 len(x)<=6（多机构行全被挡）", APP,
     '        if not x or any(ch in x for ch in "，。；,;"):\n            return False',
     '        if not x or any(ch in x for ch in "，。；,;"):\n            return False\n        if len(x) > 6:\n            return False'),
    ("M10 app: 方向词上限 8 → 4（「中性偏震荡」被挡）", APP,
     "            if len(p) > 8:",
     "            if len(p) > 4:"),
    ("M11 app: 观点改回 cells[-2]（列数一变就错位）", APP,
     "        opinion = cells[4] if len(cells) >= 5 else \"\"",
     "        opinion = cells[-2]"),
    # ── prompt ──────────────────────────────────────────────────────────────
    ("M12 app: 观点口径 60~100 → 60~120（超 4 行容量）", APP,
     '"(60~100 字, 2~3 个分句),',
     '"(60~120 字, 2~3 个分句),'),
    ("M13 app: prompt 不再插入品种清单（LLM 漏品种 ⇒ 回退 700 字原文）", APP,
     'f"本次材料共 {len(_variety_codes)} 个品种(',
     'f"本次材料共 0 个品种('),
("M14 app: 在观点段落塞回「方向(数字%)」反例（LLM 照抄成逐家罗列）", APP,
     '        "整段**只谈逻辑与结论**: 不出现任何机构名称',
     '        "如「永安 看多(58%)—配额紧」。"\n        "整段**只谈逻辑与结论**: 不出现任何机构名称'),
]

print("=" * 78)
print("  变异自检：%d 个变异" % len(MUTS))
print("=" * 78)
killed = 0
skipped = []
survived = []
for name, path, old, new in MUTS:
    t = read(path)
    if t.count(old) != 1:
        skipped.append((name, "锚点命中 %d 次" % t.count(old)))
        print("  ⚠ 跳过  %s（锚点命中 %d 次）" % (name, t.count(old)))
        continue
    write(path, t.replace(old, new, 1))
    try:
        passed = run()
    finally:
        write(path, t)                      # 无论测试结果如何都复原
    if passed:
        survived.append(name)
        print("  ✗ 存活  %s  ← 护栏有洞" % name)
    else:
        killed += 1
        print("  ✓ 击杀  %s" % name)

print()
print("  结果: %d 击杀 / %d 存活 / %d 跳过" % (killed, len(survived), len(skipped)))
print("  复原校验: template %s / app %s" % (
    "OK" if md5(TPL) == H0 else "★不一致",
    "OK" if md5(APP) == A0 else "★不一致"))
sys.exit(0 if (not survived and not skipped and md5(TPL) == H0 and md5(APP) == A0) else 1)
