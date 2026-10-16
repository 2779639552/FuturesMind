# -*- coding: utf-8 -*-
"""补丁 #2：把「多空逻辑系方向票」从技术面 `_renderVerdict` 里过滤掉 + 留指向说明。

【为什么单独一个补丁】上一个补丁（_relocate_quant_fund.py）漏了这两处 ——
它把基本面侧的 `_renderFundVotes` 建好了、也把 5 个扩展块搬走了，但**没有**把
技术面 votes 表里的多空逻辑系行排除 ⇒ 同一本书的票会在两页重复出现（正是用户要消除的）。
本补丁只补这两处，是**幂等安全**的（有标记就跳过），因此可以独立重跑。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
NEEDLE = "const _movedVotes = _allVotes.filter("


def main() -> int:
    raw = TPL.read_bytes()
    assert raw.count(b"\r\n") > 0, "web_template.html 应为 CRLF"
    txt = raw.decode("utf-8")
    print("md5(前) =", hashlib.md5(raw).hexdigest())

    if NEEDLE in txt:
        print("= 已应用过（幂等跳过）")
        return 0

    OLD = " const votes = d.stages?.direction?.votes || [];"
    assert txt.count(OLD) == 1, "votes 锚点命中 %d" % txt.count(OLD)
    NEW = "\r\n".join([
        " // 【2026-10-14】多空逻辑系(《期货多空逻辑》口径)方向票已迁至「基本面量化 →",
        " // 多空逻辑体系研判」。本页只陈述**技术面三体系**(鹿希武/墨菲/威科夫)的票,",
        " // 避免同一本书在两页重复列票(用户要求: 技术面量化不再出现多空逻辑内容)。",
        " const _allVotes = d.stages?.direction?.votes || [];",
        " const _movedVotes = _allVotes.filter(v => String(v.book || '').startsWith('多空逻辑'));",
        " const votes = _allVotes.filter(v => !String(v.book || '').startsWith('多空逻辑'));",
    ])
    txt = txt.replace(OLD, NEW, 1)
    print("  ✔ votes 已过滤多空逻辑系")

    # 在 votes 表的 `}` 之后补一句指向说明（锚点：conflict_note 行 → 其后两行）
    lines = txt.split("\r\n")
    i_cf = next(i for i, l in enumerate(lines) if "conflict_note || '')}</div>" in l)
    j = i_cf + 2
    assert lines[j].strip() == "}", "期望 votes 表收尾 brace, 实际 %r" % lines[j]
    note = [
        " // 迁出说明: 只在确有票时提示, 避免无票品种白刷一行",
        " if (_movedVotes.length) parts.push(`<div style=\"font-size:0.75rem;color:var(--text-muted);margin:-8px 0 14px\">",
        "  多空逻辑系 ${_movedVotes.length} 票（${md(_movedVotes.map(v => v.book).join('、'))}）已迁至「基本面量化 → 多空逻辑体系研判」。</div>`);",
    ]
    lines[j + 1:j + 1] = note
    txt = "\r\n".join(lines)
    print("  ✔ 表后已加迁出指向说明")

    TPL.write_bytes(txt.encode("utf-8"))
    raw2 = TPL.read_bytes()
    assert raw2.count(b"\r\n") > 0 and (raw2.count(b"\n") - raw2.count(b"\r\n")) == 0, "EOL 被破坏"
    t = raw2.decode("utf-8")
    print()
    print("[自检]")
    for tag, v in [
        ("_movedVotes 已定义", "_movedVotes" in t),
        ("过滤出现两处", t.count("startsWith('多空逻辑')") == 3),
        ("指向说明在位", "已迁至「基本面量化 → 多空逻辑体系研判」" in t),
        ("基本面侧过滤仍在", "_renderFundVotes" in t and t.count("startsWith('多空逻辑')") >= 2),
    ]:
        print("    %-24s %s" % (tag, "✔" if v else "✘"))
    print()
    print("md5(后) =", hashlib.md5(raw2).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
