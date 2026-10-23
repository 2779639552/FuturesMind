# -*- coding: utf-8 -*-
"""变异自检：把 _navBackToOverview 的关键行逐个改坏，harness 必须变红。

注意：`dv.style.display = 'none'` 这类语句在 qBackOverview / fBackOverview 里也有，
所以替换必须**限定在 _navBackToOverview 方法体内**，否则锚点会命中 3 次。
只在临时副本上变异，原文件 md5 校验不变。
"""
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\AgentSense\AgentSense")
TPL = ROOT / "web_template.html"
HARNESS = ROOT / "tests" / "js" / "nav_back_overview_harness.mjs"
NODE = r"C:\Users\lenovo\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
METHOD = "_navBackToOverview"

MUTS = [
    ("M1 明细不隐藏（只显示总览）", "dv.style.display = 'none';", "dv.style.display = '';"),
    ("M2 总览写成 block（不交还样式表）", 'ov.style.display = \'\';', "ov.style.display = 'block';"),
    ("M3 隐藏判据误用空串（永远不进回退分支）",
     "if (!dv || !ov || dv.style.display === 'none') return false;",
     "if (!dv || !ov || dv.style.display === '') return false;"),
    ("M4 技术面两视图 id 顺序写反",
     "quant: ['q-detail-view', 'q-overview-view'],",
     "quant: ['q-overview-view', 'q-detail-view'],"),
    ("M5 返回值反转（本就该 false 的也报 true）",
     "if (!dv || !ov || dv.style.display === 'none') return false;",
     "if (!dv || !ov || dv.style.display === 'none') return true;"),
    ("M6 去掉未知 tab 的兜底", "if (!pair) return false;", "if (!pair) pair = [];"),
]


def method_span(s: str):
    m = re.search(r"\n\s*" + METHOD + r"\(t\)\s*\{", s)
    if not m:
        raise SystemExit("找不到方法定义")
    i = s.index("{", m.start())
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return m.start(), j + 1
    raise SystemExit("方法未闭合")


def run(tpl_path):
    r = subprocess.run([NODE, str(HARNESS), str(tpl_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    raw = io.open(str(TPL), "rb").read()
    md5_before = hashlib.md5(raw).hexdigest()
    base = raw.decode("utf-8")
    a, b = method_span(base)

    code, out = run(TPL)
    print("基线: exit=%d" % code)
    if code != 0:
        print(out[-2000:])
        sys.exit("基线不绿，先修基线再变异")

    tmpdir = Path(tempfile.mkdtemp(prefix="navback_mut_"))
    killed = 0
    try:
        for name, old, new in MUTS:
            body = base[a:b]
            n = body.count(old)
            if n != 1:
                print("[SKIP] %s —— 方法体内锚点命中 %d 次" % (name, n))
                continue
            mutated = base[:a] + body.replace(old, new, 1) + base[b:]
            p = tmpdir / ("mut_%d.html" % killed)
            io.open(str(p), "w", encoding="utf-8", newline="").write(mutated)
            code, out = run(p)
            fails = len(re.findall(r"--- .* --- FAIL", out))
            if code == 0:
                print("[存活] %s —— harness 仍全绿（护栏没咬住）" % name)
            elif "no tests ran" in out:
                print("[无效] %s —— 用例没跑起来" % name)
            elif fails == 0:
                last = [l for l in out.strip().splitlines() if l.strip()][-1][:90]
                print("[击杀] %s —— 未捕获异常中断（%s）" % (name, last))
                killed += 1
            else:
                print("[击杀] %s —— %d 条断言变红" % (name, fails))
                killed += 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    md5_after = hashlib.md5(io.open(str(TPL), "rb").read()).hexdigest()
    print("\n击杀 %d/%d，原文件 md5 %s" % (killed, len(MUTS), "未变" if md5_before == md5_after else "已变(!)"))
    if md5_before != md5_after:
        sys.exit("原文件被污染")
    if killed != len(MUTS):
        sys.exit("有变异存活")


if __name__ == "__main__":
    main()
