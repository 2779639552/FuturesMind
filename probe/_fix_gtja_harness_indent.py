# -*- coding: utf-8 -*-
"""修 `tests/js/gtja_module_harness.mjs` 的**缩进写死**问题（2026-10-14）

═══ 症状 ═══
`node tests/js/gtja_module_harness.mjs` 直接 `找不到方法定义: /^  _renderGtjaSources\\(d\\) \\{$/`
并以 exit(2) 退出 —— 看起来像"方法被删了"，其实方法都在。
（**已用改动前备份验证：改动前也报同样的错 ⇒ 既有问题，非本次结构重组引入。**）

═══ 根因 ═══
模板 `web_template.html` 已被全局压平为 **1 空格/层**（原 2 空格，见 skill 记录），
而本 harness 的 6 个方法锚点与 1 个"结束行"判据都写死了 `^  `（恰好 2 空格）
⇒ 命中 0。**这与 `tests/test_upload_dropzone.py::_method_body` 是同一类缺陷。**

═══ 修法与纪律 ═══
- 方法锚点：`^  xxx(` → `^[ \t]+xxx(`
- 结束行：`^  },?$`   → `^[ \t]+},?$`
- ★ 用 `str.replace` 而不是正则去改正则字面量，避免多层转义互相吃掉
  （本轮先用 heredoc 写就因转义失配而失败）。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F = ROOT / "tests" / "js" / "gtja_module_harness.mjs"

METHODS = [
    "_renderGtjaSources",
    "_renderGtjaIndicators",
    "_renderGtjaChart",
    "_renderGtjaTable",
    "showGtjaIndicator",
    "_gtEnsureVisible",
]


def main() -> int:
    raw = F.read_bytes()
    assert raw.count(b"\r\n") == 0, "该 harness 应为 LF"
    s = raw.decode("utf-8")
    h0 = hashlib.md5(raw).hexdigest()
    if "^[ \\t]+_renderGtjaSources" in s:
        print("⚠️ 已修过，跳过")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    bak = ROOT / "backup" / ("gtja_module_harness_pre_indent_%s.mjs" % ts)
    shutil.copy2(F, bak)
    print("[备份] backup/%s" % bak.name)

    # ① 六个方法定义的锚点前缀：'  /^  ' → '  /^[ \t]+'
    n = 0
    for m in METHODS:
        old = "  /^  " + m
        new = "  /^[ \\t]+" + m
        c = s.count(old)
        assert c == 1, "锚点 %s 命中 %d（期望 1）" % (m, c)
        s = s.replace(old, new, 1)
        n += 1
    print("    ✔ 方法锚点改缩进无关 x%d" % n)

    # ② 结束行判据
    old2 = "    if (/^  \\},?$/.test(LINES[i])) return LINES.slice(s, i + 1);"
    new2 = ("    // 【2026-10-14 修】原写死 2 空格(`^  \\},?$`)，而模板已全局压平为 1 空格/层\n"
            "    //   ⇒ 命中 0，harness 会 exit(2)「找不到方法结束」（看着像方法被删）。改缩进无关。\n"
            "    if (/^[ \\t]+\\},?$/.test(LINES[i])) return LINES.slice(s, i + 1);")
    c = s.count(old2)
    assert c == 1, "结束行判据命中 %d" % c
    s = s.replace(old2, new2, 1)
    print("    ✔ 结束行判据改缩进无关")

    # ③ 顶部注释同步
    old3 = "// 【必剥 \\r】模板是 CRLF; 不剥的话下面所有 `^  xxx(` 锚点都会因行尾 \\r 失配。"
    new3 = ("// 【必剥 \\r】模板是 CRLF; 不剥的话下面所有方法锚点都会因行尾 \\r 失配。\n"
            "// 【★ 缩进必须无关(2026-10-14 修)】模板已被全局压平为 **1 空格/层**(原 2 空格)\n"
            "//   ⇒ 一切写死 `^  `(恰 2 空格)的正则命中 0，本 harness 会 exit(2) 而非报失败，\n"
            "//   极易被误读成\"方法被删了\"。故本文件统一用 `^[ \\t]+`。")
    if s.count(old3) == 1:
        s = s.replace(old3, new3, 1)
        print("    ✔ 顶部注释已说明")

    F.write_text(s, encoding="utf-8", newline="")
    b = F.read_bytes()
    assert b.count(b"\r\n") == 0, "EOL 被破坏"
    print("    md5 %s -> %s" % (h0[:8], hashlib.md5(b).hexdigest()[:8]))
    print("\n✔ 已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
