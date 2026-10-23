# -*- coding: utf-8 -*-
"""变异自检：验证「方向/置信度可视化」那批护栏真的能拦住回归。

逐条把生产代码改成**错误写法**，期望护栏变红（= 被击杀）；跑完立即恢复并校验 md5。
"""
import ast
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
PY = str(ROOT / "venv/Scripts/python.exe")
TESTS = "tests/test_research_dir_conf_viz.py"

CR = chr(13)
LF = chr(10)
EOL = CR + LF


def to_crlf(s: str) -> str:
    return s.replace(EOL, LF).replace(LF, EOL)


# (标签, 原串(LF 书写), 变异串(LF 书写))
MUTS = [
    ("M1 方向 chip: 看多改回绿（配色反了 —— 项目原来的错误写法）",
     ".dir-chip.up { background:var(--red-bg); color:var(--red); }",
     ".dir-chip.up { background:var(--green-bg); color:var(--green); }"),

    ("M2 方向 chip: 看空改红",
     ".dir-chip.down { background:var(--green-bg); color:var(--green); }",
     ".dir-chip.down { background:var(--red-bg); color:var(--red); }"),

    ("M3 中性丢掉（与看多同色 ⇒ 三态混成两态）",
     ".dir-chip.flat { background:var(--amber-bg); color:var(--amber); }",
     ".dir-chip.flat { background:var(--red-bg); color:var(--red); }"),

    ("M4 总览: 看多底色改回绿（两处不一致，只改一处）",
     "const chipBg = x => x === '看多' ? 'var(--red-bg)'",
     "const chipBg = x => x === '看多' ? 'var(--green-bg)'"),

    ("M5 置信度: 高置信改绿（强度色反了）",
     "const col = c >= 0.7 ? 'var(--red)' : c >= 0.4 ? 'var(--amber)' : 'var(--green)';",
     "const col = c >= 0.7 ? 'var(--green)' : c >= 0.4 ? 'var(--amber)' : 'var(--red)';"),

    ("M6 置信度: 总表分档阈值改掉（两处不一致）",
     "const col = v >= 70 ? 'var(--red)' : (v >= 40 ? 'var(--amber)' : 'var(--green)');",
     "const col = v >= 60 ? 'var(--red)' : (v >= 30 ? 'var(--amber)' : 'var(--green)');"),

    # ⚠ M7 的锚点「不是行情胜率」在总表/总览**各有一份** ⇒ 必须带唯一上下文,
    #   否则命中 2 被跳过（第一版就是这么被跳过的）。
    ("M7a 总表: 丢掉「不是行情胜率」的语义说明",
     "+ v + '%（观点表述越明确越高，不是行情胜率）",
     "+ v + '%（观点表述越明确越高）"),

    ("M7b 总览: 丢掉「不是行情胜率」的语义说明",
     "研报观点识别置信度 ${pct}%（观点表述越明确越高，不是行情胜率）",
     "研报观点识别置信度 ${pct}%（观点表述越明确越高）"),

    ("M8 后处理改按列序号定位（列序一变就错位，且不报错）",
     "const iDir = ths.findIndex(th => (th.textContent || '').trim() === '方向');",
     "const iDir = 3;"),

    ("M9 去掉「两列表头都命中才处理」的守卫（误伤其它表）",
     "if (iDir < 0 || iConf < 0) return;",
     "if (iDir < 0 && iConf < 0) return;"),

    ("M10 去掉幂等标记（切换日期反复叠加）",
     "if (tbl.dataset && tbl.dataset.vizDone === '1') return;",
     "if (false) return;"),

    # ⚠ 该串在两个方法各有一份 ⇒ 带后一行限定到 _vizConfCell
    ("M11 置信度列: 去掉「—」的早退",
     # ⚠ 前缀在 _vizDirCell 里也有一份（命中 2 次）⇒ 必须带上置信度列独有的 `const bars`
     "if (!raw || raw === '—') return;\nconst parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);\nif (!parts.length) return;\ntd.classList.add('rv-vstack');\nconst bars = parts.map",
     "if (!raw) return;\nconst parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);\nif (!parts.length) return;\ntd.classList.add('rv-vstack');\nconst bars = parts.map"),

    ("M11b ★ 解析不出百分比时**假造成 0% 空条**（把「没给」读成「给了 0 分」）",
     "if (!m) return '<span style=\"color:var(--text-muted)\">' + this._escHtml(p) + '</span>';",
     "if (!m) m = ['', '0'];"),

    # ⚠ 补丁插入的方法是**顶格**的 ⇒ 锚点不能带 2 空格缩进（第一版命中 0）
    ("M12 方向列不拆多家机构（分歧被抹成一个颜色）",
     "const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);\nif (!parts.length) return;\ntd.classList.add('rv-vstack');\nconst chips = parts.map",
     "const parts = [raw];\nif (!parts.length) return;\ntd.classList.add('rv-vstack');\nconst chips = parts.map"),

    ("M13 断开接线（方法写了但页面不生效）",
     " this._decorateViewTable(el);",
     " // 接线已移除"),

    ("M14 进度条宽度写死（退化成装饰）",
     "'<span class=\"track\"><span class=\"fill\" style=\"width:' + v + '%;background:' + col + '\"></span></span>'",
     "'<span class=\"track\"><span class=\"fill\" style=\"width:50%;background:' + col + '\"></span></span>'"),
]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run() -> tuple[bool, str]:
    r = subprocess.run([PY, "-m", "pytest", TESTS, "-q", "--no-header", "-p", "no:warnings", "--tb=no"],
                       cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return (("failed" in out) or r.returncode != 0), out


def main() -> int:
    ast.parse(Path(__file__).read_text(encoding="utf-8"))
    base = TPL.read_bytes()
    base_md5 = hashlib.md5(base).hexdigest()
    text = base.decode("utf-8")
    print("=" * 78)
    print("  变异自检 · 研报方向/置信度可视化")
    print("=" * 78)

    red, out = run()
    print("  基线护栏: %s" % (out.strip().splitlines()[-1] if out.strip() else "?"))
    if red:
        print("  ★ 基线就是红的 ⇒ 先修护栏")
        return 1

    killed = survived = skipped = 0
    for tag, old_lf, new_lf in MUTS:
        o = to_crlf(old_lf)
        n = to_crlf(new_lf)
        if text.count(o) != 1:
            print("  ⚠ 跳过（锚点命中 %d）: %s" % (text.count(o), tag))
            skipped += 1
            continue
        TPL.write_bytes(text.replace(o, n, 1).encode("utf-8"))
        try:
            red2, out2 = run()
        finally:
            TPL.write_bytes(base)
            assert md5(TPL) == base_md5, "★ 未复原: " + tag
        if red2:
            killed += 1
            print("  ✔ 击杀  %s" % tag)
        else:
            survived += 1
            print("  ★ 存活  %s   ← 护栏没拦住！" % tag)

    print()
    print("=" * 78)
    print("  结果: 击杀 %d / 存活 %d / 跳过 %d  （共 %d 项）" % (killed, survived, skipped, len(MUTS)))
    print("=" * 78)
    assert md5(TPL) == base_md5, "源文件最终 md5 与基线不一致"
    print("  ✔ web_template.html md5 已复原: %s" % base_md5[:12])
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
