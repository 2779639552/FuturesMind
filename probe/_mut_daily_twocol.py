# -*- coding: utf-8 -*-
"""变异自检：把 _layoutDailyTwoCol 的关键行逐个改坏，harness 必须变红。

纪律：
  * 只在临时副本上变异（harness 支持传模板路径），原文件字节不动，跑完 md5 校验；
  * 每项变异是一组 (old, new) 替换，每处必须命中恰好 1 次（多命中说明锚点选得太泛）；
  * "崩溃/抛异常"也算击杀——但会打印出来，避免把"用例压根没跑"当成护栏生效。
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
HARNESS = ROOT / "tests" / "js" / "daily_twocol_harness.mjs"
NODE = r"C:\Users\lenovo\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"

MUTS = [
    ("M1 第四节停止条件不再放行自身（本次实跑踩到的原 bug）", [
        ("if (n !== h4 && n.tagName === 'H2') break;", "if (n.tagName === 'H2') break;"),
    ]),
    ("M2 容器改用 appendChild（不再落回第三节原位置）", [
        ("host.insertBefore(wrap, h3);", "host.appendChild(wrap);"),
    ]),
    ("M3 幂等闸门退回 root.dataset（切日期后两列静默失效）", [
        ("if (root.querySelector && root.querySelector('.rv-twocol')) return;",
         "if (root.dataset.rvTwoCol === '1') return;"),
        ("wrap.dataset.rvTwoCol = '1';", "root.dataset.rvTwoCol = '1';"),
    ]),
    ("M4 左右两列对调", [
        ("wrap.appendChild(mkCol(nodes3, 'rv-twocol-col rv-twocol-left'));",
         "wrap.appendChild(mkCol(nodes4, 'rv-twocol-col rv-twocol-left'));"),
    ]),
    ("M5 去掉「两节都得找到」的闸门（缺四节也要并排）", [
        ("if (i3 < 0 || i4 < 0) return;", "if (i3 < 0) return;"),
    ]),
    ("M6 不再按标题文本定位（写死下标 2/3）", [
        ("const i3 = hs.findIndex(h => /^[三3]、/.test(norm(h)) && norm(h).indexOf('可信程度') >= 0);",
         "const i3 = 2;"),
    ]),
    ("M7 去掉 min-width:0（长表格撑破列宽）", [
        ("c.style.cssText = 'min-width:0';", "c.style.cssText = '';"),
    ]),
]


def run(tpl_path):
    r = subprocess.run([NODE, str(HARNESS), str(tpl_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    raw = io.open(str(TPL), "rb").read()
    md5_before = hashlib.md5(raw).hexdigest()
    base = raw.decode("utf-8")

    code, out = run(TPL)
    print("基线: exit=%d" % code)
    if code != 0:
        print(out[-2000:])
        sys.exit("基线不绿，先修基线再变异")

    tmpdir = Path(tempfile.mkdtemp(prefix="twocol_mut_"))
    killed = 0
    try:
        for name, pairs in MUTS:
            body, ok = base, True
            for old, new in pairs:
                n = body.count(old)
                if n != 1:
                    print("[SKIP] %s —— 锚点命中 %d 次: %r" % (name, n, old[:50]))
                    ok = False
                    break
                body = body.replace(old, new, 1)
            if not ok:
                continue
            p = tmpdir / ("mut_%d.html" % killed)
            io.open(str(p), "w", encoding="utf-8", newline="").write(body)
            code, out = run(p)
            fails = len(re.findall(r"--- .* --- FAIL", out))
            if code == 0:
                print("[存活] %s —— harness 仍全绿（护栏没咬住）" % name)
            elif "no tests ran" in out:
                print("[无效] %s —— 用例没跑起来" % name)
            elif fails == 0:
                # 断言没红但退出码非 0：抛异常中断（同样是回归，但更粗暴）
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
