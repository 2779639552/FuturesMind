# -*- coding: utf-8 -*-
"""Apple 化 · 第十步（终轮）：复选框尺寸 + 小按钮高度 + 主题按钮。"""
import pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX6 = r'''
/* ── 点击目标收尾（Apple 层 · 第六批） ──────────────────────────
   实测剩余小目标全是可精确修的三类：
     ① 原生复选框 13x13（浏览器默认）—— 苹果控件是 16~18px
     ② 若干 .btn 被内联 padding 压到 20px 高
     ③ 主题切换 / 改密码按钮 28~30px
   ─────────────────────────────────────────────────────────── */

/* ① 复选框：抬到 18px（保留原生外观，只放大物理尺寸） */
input[type="checkbox"] {
  width: 18px;
  height: 18px;
  min-height: 18px;
  cursor: pointer;
  vertical-align: -3px;
  flex: none;
}
/* 已经自己做外观的（.chk / .sec-ck）不覆盖 */
.chk input[type="checkbox"] { width: 18px; height: 18px; }

/* 单选按钮同步放大 */
input[type="radio"] { width: 18px; height: 18px; cursor: pointer; vertical-align: -3px; }

/* ② 所有 .btn 家族稳住 32px 最小高度（内联 padding 也压不下去） */
.btn, .btn-sm, .depth-preset, .wt-tab, .sec-pill {
  min-height: 32px;
  align-items: center;
}
/* 模块勾选行里的原生 checkbox 行高 */
.depth-mod-row label { min-height: 32px; }

/* ③ 顶栏按钮 */
.theme-toggle { min-height: 32px; }
#change-pw-btn { min-height: 32px; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX6 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"OK 第十步：{n0} -> {len(src.encode('utf-8'))} 字节")
