# -*- coding: utf-8 -*-
"""补丁 #3：修正 SCORE_JS 的落点 —— 从方法闭合 `},` 之后移到 **try 块内**。

【为什么必须修（一个真 bug）】
上一个补丁把 9 行计分写入语句插在 `async loadQuantSignals()` 的闭合 ` },` **之后**，
于是它们成了 App 对象字面量的**顶层语句** ⇒ `node --check` 直接报
`SyntaxError: Unexpected identifier '_sc'`，整页 JS 全废。
而且即使挪到方法体内、但放在 `try/catch` **之后**也不行：`d` 是 `const d = await r.json();`
在 `try` 块内声明的**块级变量**，块外引用会 ReferenceError。
⇒ 正解：插在 `if (badge) { ... }` 之后、` } catch (e) {` **之前**（仍在 try 内）。

本补丁幂等安全（有标记即跳过）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
HEAD = "  // 【2026-10-14】同一份 signals 也写入「① 三指标策略系统」卡的一行(基本面口径计分)。"
N = 9          # SCORE_JS 的行数


def main() -> int:
    raw = TPL.read_bytes()
    assert raw.count(b"\r\n") > 0, "应为 CRLF"
    txt = raw.decode("utf-8")
    print("md5(前) =", hashlib.md5(raw).hexdigest())
    lines = txt.split("\r\n")

    # ① 取出错位的那 9 行
    i = next((k for k, l in enumerate(lines) if l == HEAD), None)
    assert i is not None, "找不到 SCORE_JS 头部（可能已修好或未应用）"
    assert lines[i + N - 1].strip() == "}", "第 %d 行不是收尾 brace: %r" % (i + N, lines[i + N - 1])
    # 校验它前面确实是方法闭合 ` },`
    assert lines[i - 1].strip() == "},", "错位块之前不是方法闭合: %r" % lines[i - 1]
    blk = lines[i:i + N]
    del lines[i:i + N]
    print("  ✔ 取出错位的 %d 行（原在方法闭合之后）" % len(blk))

    # ② 插到 loadQuantSignals 的 ` } catch (e) {` 之前（仍在 try 内）
    a = next(k for k, l in enumerate(lines) if "async loadQuantSignals() {" in l)
    c = next(k for k in range(a, len(lines)) if lines[k].strip() == "} catch (e) {")
    lines[c:c] = blk
    print("  ✔ 已插入 try 块内（`if (badge)` 之后、`catch` 之前）@%d" % c)

    TPL.write_bytes("\r\n".join(lines).encode("utf-8"))
    raw2 = TPL.read_bytes()
    assert raw2.count(b"\r\n") > 0 and (raw2.count(b"\n") - raw2.count(b"\r\n")) == 0, "EOL 被破坏"
    print()
    print("md5(后) =", hashlib.md5(raw2).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
