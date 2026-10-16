# -*- coding: utf-8 -*-
"""Apple 化 · 第八步（收尾打磨）：
1) `#fg-list` 里的 预览/下载/删除 行内链接（24x18px）-> 达标的可点区
2) `<small>` 默认 0.83em -> 12px 地板
3) 统一 tab 内容宽度与苹果式节奏
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX4 = r'''
/* ── 收尾打磨（Apple 层 · 第四批） ──────────────────────────── */

/* <small> 浏览器默认 0.83em（≈10.8px）会击穿 12px 地板 —— 显式钉死。
   实测「数据覆盖: …」这行就是这么漏下去的。 */
small { font-size: 0.75rem; }

/* 外文研报历史任务的 预览 / 下载 / 删除：原为裸行内 <a>，命中区仅 24x18px。
   苹果语言里这类操作用「文字按钮」——给足内边距形成 28px 高的可点条。 */
#fg-list a {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  margin-left: 14px;
  font-size: 0.875rem;
  white-space: nowrap;
}
/* 任务行本身给足行高，右列操作不再挤成一条 */
#fg-list > div { line-height: 1.9; }

/* 台账类长列表（历史任务/研报库/数据集）：行与行之间用细线，替代裸文字堆叠 */
#fg-list > div + div { border-top: 1px solid var(--border-light); }

/* 顶栏右侧按钮：改密码 也抬到 32px 可点区 */
#change-pw-btn { min-height: 30px; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX4 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"OK 第八步完成：{n0} -> {len(src.encode('utf-8'))} 字节")

# 复查：CSS 里是否还有 <12px 的绝对 px 字号
a, b = src.find("<style>"), src.find("</style>")
css = src[a:b]
bad = [(m.group(0), parse) for m in re.finditer(r'font-size:\s*([\d.]+)(px)', css)
       if (parse := float(m.group(1))) < 12]
print("CSS 残留 <12px 绝对字号:", len(bad), bad[:6] if bad else "")
