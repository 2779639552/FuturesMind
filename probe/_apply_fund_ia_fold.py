# -*- coding: utf-8 -*-
"""A 区整区折叠（2026-10-21，补齐效果图里的「整区折叠」）。

【为什么现在能安全地做】模板里 `_SEC_FLAT_TABS_ALL = true`（全站折叠引擎关闭）⇒
  `_secScan` 不再扫描 `.card`，用 `display:none` 藏卡片**不会**留下"点不开的僵尸索引项"。
  （若将来把该开关改回 false，这里必须改成 max-height 或同步索引重建。）

【坑】重新展开后必须 `_resizeAllCharts()` —— 隐藏期间 ECharts 容器宽高为 0，
  再次显示时画布不会自动复位（本项目 2026-10-21 半宽改造时踩过同款）。
"""
import io

P = r"D:\AgentSense\AgentSense\web_template.html"
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s

# ⚠ 只在 #f-detail-view 范围内改：`expander` / `tab-warrant-tools` 这类 class 全站有多处
import re  # noqa: E402


def close_of(x: str, i: int) -> int:
    depth, started = 0, False
    for m in re.finditer(r"<div\b|</div>", x[i:]):
        if m.group(0).startswith("<div"):
            depth += 1
            started = True
        else:
            depth -= 1
        if started and depth == 0:
            return i + m.end()
    return -1


_v0 = s.index('<div id="f-detail-view"')
_v1 = close_of(s, _v0)
assert _v1 > 0
VIEW = s[_v0:_v1]

# ── ① 给 A 区各块打标记 ─────────────────────────────────────────────
subs = [
    ('<div class="card"><h3>A1 ', '<div class="card f-a-block"><h3>A1 '),
    ('<div class="card"><h3>A2 ', '<div class="card f-a-block"><h3>A2 '),
    ('<div class="card"><h3>A3 ', '<div class="card f-a-block"><h3>A3 '),
    ('<div class="card" id="f-profit-card">', '<div class="card f-a-block" id="f-profit-card">'),
    ('<div class="card" id="f-vr-card">', '<div class="card f-a-block" id="f-vr-card">'),
    ('<div class="card"><h3>A8 ', '<div class="card f-a-block"><h3>A8 '),
    ('<div id="f-research"></div>', '<div id="f-research" class="f-a-block"></div>'),
    # ⚠ 该标记在注释里也出现过一次（带反引号），带上行尾换行才唯一
    ('<div id="tab-warrant-tools">' + CRLF, '<div id="tab-warrant-tools" class="f-a-block">' + CRLF),
    ('<div class="expander" style="margin-bottom:14px">',
     '<div class="expander f-a-block" style="margin-bottom:14px">'),
    ('<div class="f-zone sub" id="f-zone-a6">', '<div class="f-zone sub f-a-block" id="f-zone-a6">'),
]
for old, new in subs:
    assert VIEW.count(old) == 1, "① 锚点命中 %d: %s" % (VIEW.count(old), old[:40])
    VIEW = VIEW.replace(old, new, 1)

# 仓单三卡：<div class="card" > 紧挨着 <h3>A6.x
for tag in ("A6.1", "A6.2", "A6.3"):
    pat = re.compile(r'<div class="card" >(\r\n\s*<h3>%s)' % re.escape(tag))
    assert len(pat.findall(VIEW)) == 1, "① 仓单卡锚点异常: %s" % tag
    VIEW = pat.sub(r'<div class="card f-a-block" >\1', VIEW, count=1)

# 作用域内的改动合并回全文
s = s[:_v0] + VIEW + s[_v1:]

# ── ② 分区条上加按钮 ────────────────────────────────────────────────
old_btn = (">跳到结论 ↓</div></div>")
assert s.count(old_btn) == 1, "② 按钮锚点命中 %d" % s.count(old_btn)
new_btn = (
    '>跳到结论 ↓</div>'
    '<button type="button" id="f-fold-a" class="f-fold-btn">收起数据区</button>'
    '</div>')
s = s.replace(old_btn, new_btn, 1)

# ── ③ CSS ──────────────────────────────────────────────────────────
css_anchor = ".f-subnav{display:inline-flex;"
assert s.count(css_anchor) == 1
css = (
    ".f-fold-btn{border:1px solid var(--border,#d2d2d7);background:var(--bg-card,#f5f5f7);\n"
    " color:var(--text-secondary);font:inherit;font-size:0.75rem;padding:4px 10px;border-radius:7px;\n"
    " cursor:pointer;white-space:nowrap}\n"
    ".f-fold-btn:hover{color:var(--text)}\n")
s = s.replace(css_anchor, css + css_anchor, 1)

# ── ④ JS 方法 ──────────────────────────────────────────────────────
anchor = "// 【功能】A/B 分区跳转（2026-10-21 信息架构重构）"
assert s.count(anchor) == 1
meth = (
    "// 【功能】A 区整区折叠（效果图里的「整区折叠」）\n"
    "// 【为什么用 display:none 安全】全站折叠引擎已关闭(_SEC_FLAT_TABS_ALL=true)，\n"
    "//   没有按 .card 扫描的小模块索引 ⇒ 不会留僵尸条目。\n"
    "// 【坑】展开后必须 resize：隐藏期间容器宽高为 0，ECharts 不会自己复位。\n"
    "fFoldA(force) {\n"
    " const blocks = document.querySelectorAll('#f-detail-view .f-a-block');\n"
    " if (!blocks.length) return;\n"
    " const fold = (typeof force === 'boolean')\n"
    "  ? force : !blocks[0].classList.contains('f-a-hidden');\n"
    " blocks.forEach(b => {\n"
    "  b.classList.toggle('f-a-hidden', fold);\n"
    "  b.style.display = fold ? 'none' : '';\n"
    " });\n"
    " const btn = document.getElementById('f-fold-a');\n"
    " if (btn) btn.textContent = fold ? '展开数据区' : '收起数据区';\n"
    " if (!fold) setTimeout(() => { if (this._resizeAllCharts) this._resizeAllCharts(); }, 30);\n"
    "},\n\n")
s = s.replace(anchor, meth + anchor, 1)

# ── ⑤ 绑定（同一个文档级委托里加一条） ─────────────────────────────
bind_anchor = ("  const t = e.target.closest && e.target.closest('.f-jump, #f-subnav button');")
assert s.count(bind_anchor) == 1
new_bind = (
    "  const fb = e.target.closest && e.target.closest('#f-fold-a');\n"
    "  if (fb) {\n"
    "   e.preventDefault();\n"
    "   if (typeof App !== 'undefined' && App.fFoldA) App.fFoldA();\n"
    "   return;\n"
    "  }\n"
    + bind_anchor)
s = s.replace(bind_anchor, new_bind, 1)

# 注入片段用 \n 书写 ⇒ 归一为 CRLF
s = s.replace("\r\n", "\n").replace("\n", CRLF)
assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("fold patched ok")
