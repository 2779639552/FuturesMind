# -*- coding: utf-8 -*-
"""变异自检：验证「观点截断修复」的护栏真能拦住回归。"""
import ast
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "web_app.py"
TPL = ROOT / "web_template.html"
PY = str(ROOT / "venv/Scripts/python.exe")
TESTS = "tests/test_views_no_truncation.py"

CR, LF = chr(13), chr(10)
EOL = CR + LF


def to_crlf(s: str) -> str:
    return s.replace(EOL, LF).replace(LF, EOL)


# (标签, 文件, 原串(LF 书写), 变异串(LF 书写))
MUTS = [
    ("M1 app: LLM 观点重新截 60 字", APP,
     '        view = (llm_views.get(c) or "").strip()',
     '        view = (llm_views.get(c) or "").strip()\n        if len(view) > 60:\n            view = view[:60] + "…"'),

    ("M2 app: 回退拼接重新截 40 字（每家机构）", APP,
     '                views_tmp.append(op if op else "—")',
     '                views_tmp.append(op[:40] if op else "—")'),

    ("M3 app: 观点提取重新收紧到 150", APP,
     "            opinion = _extract_key_opinion(seg, max_len=360)\n            if not opinion:",
     "            opinion = _extract_key_opinion(seg, max_len=150)\n            if not opinion:"),

    ("M4 app: 合并层 cap 重新卡到 200（显示又被截）", APP,
     "_merge_same_source_items(recs, flatten=True, text_cap=0, trade_cap=0)",
     "_merge_same_source_items(recs, flatten=True, text_cap=200, trade_cap=120)"),

    ("M5 app: prompt 材料重新静默硬切", APP,
     "    _payload_txt = _fit_prompt_payload(payload.split(\"\\n\\n\"))",
     "    _payload_txt = payload[:28000]"),

    # ⚠ 锚点要带上**后一行的唯一上下文**（ 单行在同函数里可能多次出现）
    ("M6 app: 预算装载改为「遇到装不下的就停」（挡住后面短条目）", APP,
     '            dropped.append(head or blk[:12])\n            continue\n        keep.append(blk)',
     '            dropped.append(head or blk[:12])\n            break\n        keep.append(blk)'),

    ("M7 app: 去掉被略过条目的显式标注（LLM 会以为没研报）", APP,
     '        out += ("\\n\\n（注：以下品种的材料因超出长度预算未列入，请勿据此下结论：%s）"\n                % "、".join(sorted(set(dropped))))',
     '        pass'),

    ("M8 app: 观点文本的换行清洗被删（表会被劈开）", APP,
     '                op = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")',
     '                op = str(x.get("opinion") or "").strip().replace("|", "/")'),

    ("M9 前端: clamp 改用几何判据（折叠容器内全量误判）", TPL,
     "if (txt.length <= 120) return;",
     "if (td.scrollHeight <= td.clientHeight) return;"),

    ("M10 前端: 去掉「展开全文」入口（用户以为还是被截的）", TPL,
     "btn.addEventListener('click', (e) => {",
     "btn.addEventListener('noop', (e) => {"),

    ("M11 前端: 去掉打印时放开 clamp（PDF 里仍被截）", TPL,
     "  .report-content table td.rv-long > .clamp3 { display:block !important; overflow:visible !important; -webkit-line-clamp:unset !important; }",
     "  /* 打印样式已移除 */"),

    ("M12 前端: 去掉 clamp 的幂等标记（反复叠加包裹）", TPL,
     "if (!td || td.dataset.clamped === '1') return;",
     "if (!td) return;"),
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
    bases = {APP: APP.read_bytes(), TPL: TPL.read_bytes()}
    base_md5 = {p: hashlib.md5(b).hexdigest() for p, b in bases.items()}
    texts = {p: b.decode("utf-8") for p, b in bases.items()}

    print("=" * 78)
    print("  变异自检 · 观点截断修复")
    print("=" * 78)
    red, out = run()
    print("  基线: %s" % (out.strip().splitlines()[-1] if out.strip() else "?"))
    if red:
        print("  ★ 基线就是红的 ⇒ 先修护栏")
        return 1

    killed = survived = skipped = 0
    for tag, path, old_lf, new_lf in MUTS:
        o, n = to_crlf(old_lf), to_crlf(new_lf)
        if texts[path].count(o) != 1:
            print("  ⚠ 跳过（锚点命中 %d）: %s" % (texts[path].count(o), tag))
            skipped += 1
            continue
        path.write_bytes(texts[path].replace(o, n, 1).encode("utf-8"))
        try:
            red2, _ = run()
        finally:
            path.write_bytes(bases[path])
            assert md5(path) == base_md5[path], "★ 未复原: " + tag
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
    for p in bases:
        assert md5(p) == base_md5[p], "源文件 md5 不一致: %s" % p
    print("  ✔ 两个源文件 md5 均已复原")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
