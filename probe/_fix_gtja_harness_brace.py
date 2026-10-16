# -*- coding: utf-8 -*-
"""把 `tests/js/gtja_module_harness.mjs` 的 bodyLines 改为**花括号配平**（2026-10-14 二次修正）

═══ 两次修正的原因（重要，别退回缩进判据）═══
本文件原用 `^  },?$`（**恰好 2 空格**）当"方法结束"判据。它**靠缩进层级**区分
「对象方法结束(2 空格)」与「方法内 if/for 块的结束(4 空格)」。

模板 `web_template.html` 已被**全局压平为 1 空格/层** ⇒
① 沿用 `^  ` ⇒ 命中 0，`exit(2)`（症状：`找不到方法定义`，看着像方法被删）；
② 改 `^[ \\t]+` ⇒ **更糟**：方法定义行与内层 `if` 块缩进**都是 1 空格**，
   缩进信息彻底丢失 ⇒ 在内层 `if (...) { ... }` 的 `}` 处**提前截断**，
   静默产出半截方法体（实测 `_renderGtjaIndicators` 只取到 12 行、丢掉后半段），
   随后 `eval` 报 `Unexpected token ','` —— **比原问题更隐蔽**。

⇒ 正解：**语义配平**（数 `{`/`}` 回到 0），与缩进无关。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F = ROOT / "tests" / "js" / "gtja_module_harness.mjs"

# 用行区间替换整段（比长字符串锚点稳）——先定位 function bodyLines 的起止行
NEW_FUNC = '''/** 取一个方法的完整方法体 —— **按花括号配平**，不看缩进。
 *
 * 【为什么不能用缩进判据(2026-10-14 两次修正)】
 *   本文件原用 `^  },?$`（恰好 2 空格）当"方法结束"。它靠缩进层级区分
 *   「对象方法结束(2 空格)」与「方法内 if/for 块的结束(4 空格)」。
 *   但模板 `web_template.html` 已被**全局压平为 1 空格/层** ⇒ 两类行都变成 1 空格
 *   ⇒ 缩进信息**彻底丢失**。
 *   ① 沿用 `^  ` ⇒ 命中 0，直接 exit(2)（症状：像"方法被删了"）；
 *   ② 改 `^[ \\t]+` ⇒ **更糟**：会在内层 `if (...) { ... }` 的 `}` 处**提前截断**，
 *      静默产出半截方法体（`_renderGtjaIndicators` 实测只取到 12 行、缺后半段）。
 *   ⇒ 正确做法是**语义配平**：数 `{` / `}`，回到 0 才是方法结束。
 *   ⚠️ 前提：方法体内字符串/模板字面量里的花括号必须**自身配对**
 *     （`${...}` 天然配对；实测这些方法里无不成对的字面量花括号）。
 */
function bodyLines(startRe) {
  const s = LINES.findIndex(l => startRe.test(l));
  if (s < 0) { console.error('找不到方法定义: ' + startRe); process.exit(2); }
  let depth = 0, started = false;
  for (let i = s; i < LINES.length; i++) {
    for (const ch of LINES[i]) {
      if (ch === '{') { depth++; started = true; }
      else if (ch === '}') { depth--; }
    }
    if (started && depth === 0) {
      // 结束后末行应是 `}` 或 `},`；否则说明配平被字符串里的花括号带偏了
      const last = LINES[i].trim();
      if (last !== '}' && last !== '},') {
        console.error('方法配平可疑(末行非 }, ): ' + startRe + ' -> ' + JSON.stringify(last));
        process.exit(2);
      }
      return LINES.slice(s, i + 1);
    }
  }
  console.error('方法未配平: ' + startRe);
  process.exit(2);
}'''


def main() -> int:
    raw = F.read_bytes()
    assert raw.count(b"\r\n") == 0, "该 harness 应为 LF"
    lines = raw.decode("utf-8").split("\n")
    h0 = hashlib.md5(raw).hexdigest()
    if "按花括号配平" in "\n".join(lines):
        print("⚠️ 已改为配平法，跳过")
        return 0

    # 定位：`/** 取一行起、到同级缩进` 起始 → 该函数结束（第一个单独 `}` 行）
    s = next(i for i, l in enumerate(lines) if l.startswith("/** 取一行起"))
    e = next(i for i in range(s, len(lines)) if lines[i].rstrip() == "}")
    print("[定位] 替换第 %d~%d 行（原 %d 行）" % (s + 1, e + 1, e - s + 1))

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    bak = ROOT / "backup" / ("gtja_module_harness_pre_brace_balance_%s.mjs" % ts)
    shutil.copy2(F, bak)
    print("[备份] backup/%s" % bak.name)

    new_lines = lines[:s] + NEW_FUNC.split("\n") + lines[e + 1:]
    # 顶部注释同步（原来说"统一用 ^[ \t]+"，现在改成配平）
    txt = "\n".join(new_lines)
    old_note = ('// 【★ 缩进必须无关(2026-10-14 修)】模板已被全局压平为 **1 空格/层**(原 2 空格)\n'
                '//   ⇒ 一切写死 `^  `(恰 2 空格)的正则命中 0，本 harness 会 exit(2) 而非报失败，\n'
                '//   极易被误读成"方法被删了"。故本文件统一用 `^[ \\t]+`。')
    new_note = ('// 【★ 方法体提取必须按花括号配平, 不能用缩进(2026-10-14 二次修正)】\n'
                '//   模板已被全局压平为 **1 空格/层**(原 2 空格) ⇒ 方法定义行与内层 if 块的\n'
                '//   缩进**完全相同**, 缩进无法再区分层级: 写死 `^  ` 会命中 0(exit 2,\n'
                '//   看着像"方法被删"); 改 `^[ \\t]+` 更糟 —— 会在内层 `}` 处**提前截断**、\n'
                '//   静默产出半截方法体。故统一走 bodyLines() 的花括号配平。')
    assert txt.count(old_note) == 1, "顶部注释锚点命中 %d" % txt.count(old_note)
    txt = txt.replace(old_note, new_note, 1)
    # 方法锚点保持 `^[ \t]+`（缩进无关是对的；错的只是"结束判据"）
    for m in ("_renderGtjaSources", "_renderGtjaIndicators", "_renderGtjaChart",
              "_renderGtjaTable", "showGtjaIndicator", "_gtEnsureVisible"):
        assert ("/^[ \\t]+" + m) in txt, "方法锚点 %s 丢了" % m
    print("[自检] 6 个方法锚点仍为缩进无关形式 ✔")

    F.write_text(txt, encoding="utf-8", newline="")
    b = F.read_bytes()
    assert b.count(b"\r\n") == 0, "EOL 被破坏"
    print("[自检] LF 保持 ✔  md5 %s -> %s" % (h0[:8], hashlib.md5(b).hexdigest()[:8]))
    print("\n✔ 已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
