# -*- coding: utf-8 -*-
"""重做视觉层 · 第十三步（终）：可见但零散的区块收尾。

问题（实测）：
  · 「讨论进化」是个 32px 裸 div 挂在 #tab-run 下，带内联 margin-top:12px，
    与设计层的 20px 节奏不一致；且它是个**次要操作**，独占一行显得零散。
  · `#run-input-card`（分析输入数据）在未运行时只有一行"加载中..." + 大留白。
处理原则（苹果的渐进披露）：
  · 次要操作不独占成行 → 收进该区块末尾并缩小尺寸
  · 未运行时为空的分析输入卡 → 折叠收起，不占半屏
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX = r'''
/* ── 零散区块收尾（Apple 层 · 终批）───────────────────────────── */

/* ①「讨论进化」这类次要操作：不独占一行成"孤儿按钮"。
   改成靠左的紧凑小按钮，并与上方内容保持与全站一致的上边距。 */
#tab-run > div:has(> .btn:only-child) {
  margin-top: var(--sp-4) !important;
  display: flex;
  justify-content: flex-start;
}
#tab-run > div:has(> .btn:only-child) > .btn {
  min-height: 32px;
  padding: 0 var(--sp-4);
  font-size: var(--fs-sm);
}

/* ② 空区块不占位：未运行时「分析输入数据」卡只有一行占位文字，
   却撑出一整块底色 —— 空状态应当克制。 */
#run-input-card:has(#run-input-body:empty) { padding-bottom: var(--sp-5); }
#run-input-body:empty::after { content: '暂无数据'; color: var(--text-muted); }

/* ③ 隐藏区块（display:none）不参与间距计算 —— 避免出现"看不见但有高度"的间隙。
   注：本规则只兜底 .tab 直属的隐藏块，不动任何可见元素。 */
.tab > [style*="display:none"],
.tab > [style*="display: none"] { margin-top: 0 !important; }

/* ④ 区块之间的最后一处节奏统一：卡片内的最后一个子元素不留多余底距 */
.card > *:last-child { margin-bottom: 0; }
.card > *:first-child { margin-top: 0; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"OK 收尾层已追加: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
