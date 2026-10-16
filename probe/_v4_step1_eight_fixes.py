# -*- coding: utf-8 -*-
"""v4 · 用户反馈八项修复
────────────────────────────────────────────────────────────────
① 页级控制条吸顶 → 去掉（用户：滑动时仍固定在上方，给我改掉）
② 版本号 v2.5 → v2.9（FUTURESMIND_CHANGELOG 最新为 2.9.0）
③ 灰底灰字对比度不足 → 提高文字色对比度
④ 卡片内 <details> 带边框 → 卡中套框，改细线分隔
⑤ 研报每日总结右侧控件被挤成竖排 → 标题吃余量、控件组不收缩
⑥ 全站按钮统一为苹果式（分段控件 + 黑/灰药丸）
⑦ 历史编译任务列表 → 两列排版
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)
log = []

# ══════════════════════════════════════════════════════════════
# ① 去掉吸顶（.page-tools 与 #analysis-subnav）
# ══════════════════════════════════════════════════════════════
pat = re.compile(r'\.page-tools \{\s*\n(\s*)position: sticky;\s*\n\s*top: \d+px;\s*\n\s*z-index: \d+;')
new, n = pat.subn(lambda m: ('.page-tools {\n' + m.group(1) +
    '/* ★【2026-10-14 v4】sticky 已移除 —— 用户反馈"滑动页面时仍固定在上方"，\n' +
    m.group(1) + '   控制条改为随页面正常滚动。移除此行即恢复吸顶。 */\n' +
    m.group(1) + 'position: static;'), src, count=1)
log.append(("① .page-tools 去吸顶", n)); src = new

pat = re.compile(r'#analysis-subnav \{\s*\n(\s*)position: sticky;\s*\n\s*top: \d+px;\s*\n\s*z-index: \d+;')
new, n = pat.subn(lambda m: ('#analysis-subnav {\n' + m.group(1) +
    '/* ★【2026-10-14 v4】同 .page-tools，去吸顶 */\n' +
    m.group(1) + 'position: static;'), src, count=1)
log.append(("① #analysis-subnav 去吸顶", n)); src = new

# ══════════════════════════════════════════════════════════════
# ② 版本号
# ══════════════════════════════════════════════════════════════
old_badge = '<span class="header-badge">v2.5</span>'
new_badge = '<span class="header-badge" title="版本号取自 FUTURESMIND_CHANGELOG.md 最新条目">v2.9</span>'
if old_badge in src:
    src = src.replace(old_badge, new_badge, 1)
    log.append(("② 版本号 v2.5 → v2.9", 1))
else:
    m = re.search(r'<span class="header-badge"[^>]*>([^<]*)</span>', src)
    log.append(("② 版本号", 0)); print("   WARN 当前值:", m.group(1) if m else "未找到")

# ══════════════════════════════════════════════════════════════
# ③ 亮色主题文字对比度
#    原: --muted #6e6e73 / --text-muted #86868b
#    #f5f5f7 上的对比度: #6e6e73≈5.0:1(勉强AA)  #86868b≈3.5:1(不达AA 4.5:1)
#    改为: --muted #494950(≈8.3:1)  --text-muted #6e6e73(≈5.0:1)
# ══════════════════════════════════════════════════════════════
lm = re.search(r'\[data-theme="light"\]\s*\{', src)
ls, d, i = lm.end(), 1, lm.end()
while d > 0 and i < len(src):
    if src[i] == '{': d += 1
    elif src[i] == '}': d -= 1
    i += 1
light_blk = src[ls:i]
lt_new, c = light_blk, 0
for old, new in [(" --muted: #6e6e73;", " --muted: #494950;"),
                 (" --text-muted: #86868b;", " --text-muted: #6e6e73;")]:
    if old in lt_new:
        lt_new = lt_new.replace(old, new, 1); c += 1
if c:
    src = src[:ls] + lt_new + src[i:]
log.append(("③ 亮色文字对比度", c))

# 暗色主题: --text-muted #86868b → #98989d（在 #1d1d1f 上 5.3:1 → 6.4:1）
rm = re.search(r':root\s*\{', src)
rs, d, i = rm.end(), 1, rm.end()
while d > 0 and i < len(src):
    if src[i] == '{': d += 1
    elif src[i] == '}': d -= 1
    i += 1
root_blk = src[rs:i]
rt_new = root_blk.replace(" --text-muted: #86868b;", " --text-muted: #98989d;", 1)
c2 = 1 if rt_new != root_blk else 0
if c2:
    src = src[:rs] + rt_new + src[i:]
log.append(("③ 暗色文字对比度", c2))

# ══════════════════════════════════════════════════════════════
# ④ 卡片内 <details> 去边框（"框中开框"）
# ══════════════════════════════════════════════════════════════
old_det = ('html += `<details style="margin-top:8px;border:1px solid var(--border-light);'
           'border-radius:var(--radius-sm);padding:0 10px">'
           '<summary style="cursor:pointer;font-size:0.8125rem;font-weight:600;padding:6px 0">'
           '${sec.title}</summary><div style="padding-bottom:8px">${sec.html}</div></details>`;')
new_det = ('// ★【2026-10-14 v4】原为 1px 边框 + 圆角，而它本身已在 .card 内 ⇒ 卡中套框（用户反馈"框中开框很丑"）。\n'
           ' // 改为「顶细线分隔 + 无边框」：与全站 .expander 的分隔语言一致，视觉上不再形成第二层框。\n'
           ' html += `<details style="margin-top:0;border:none;border-top:1px solid var(--border-light);'
           'border-radius:0;padding:0">'
           '<summary style="cursor:pointer;font-size:0.8125rem;font-weight:500;padding:7px 0;'
           'color:var(--text-secondary)">${sec.title}</summary>'
           '<div style="padding:0 0 8px;color:var(--text)">${sec.html}</div></details>`;')
if old_det in src:
    src = src.replace(old_det, new_det, 1)
    log.append(("④ <details> 去边框", 1))
else:
    log.append(("④ <details> 去边框", 0)); print("   WARN details 模板未精确命中")

# ══════════════════════════════════════════════════════════════
# ⑥ 分析深度 → 苹果分段控件（用 span 包裹三个按钮；JS 按 class 取，安全）
# ══════════════════════════════════════════════════════════════
seg_old = ('<span class="lbl">分析深度</span>\n'
           ' <button type="button" class="btn btn-outline btn-sm depth-preset active" data-preset="full" '
           'onclick="App._setPreset(\'full\')" title="全部模块,现行为">完整</button>\n'
           ' <button type="button" class="btn btn-outline btn-sm depth-preset" data-preset="light" '
           'onclick="App._setPreset(\'light\')" title="跳过辩论对抗与情景分析,保留综合研判(省时约 73%)">轻量</button>\n'
           ' <button type="button" class="btn btn-outline btn-sm depth-preset" data-preset="minimal" '
           'onclick="App._setPreset(\'minimal\')" title="仅基础报告,无最终评级">极简</button>')
seg_new = ('<span class="lbl">分析深度</span>\n'
           ' <!-- ★【2026-10-14 v4】三个预设档参照苹果分段控件：外层一个灰底胶囊，'
           '选中项为深色药丸。\n'
           '      包一层 .seg 只为提供"组容器"背景；JS（_setPreset/_syncDepthUI）按 '
           '.depth-preset class 取值，不依赖 DOM 层级，故加壳安全。 -->\n'
           ' <span class="seg" role="group" aria-label="分析深度">\n'
           ' <button type="button" class="depth-preset active" data-preset="full" '
           'onclick="App._setPreset(\'full\')" title="全部模块,现行为">完整</button>\n'
           ' <button type="button" class="depth-preset" data-preset="light" '
           'onclick="App._setPreset(\'light\')" title="跳过辩论对抗与情景分析,保留综合研判(省时约 73%)">轻量</button>\n'
           ' <button type="button" class="depth-preset" data-preset="minimal" '
           'onclick="App._setPreset(\'minimal\')" title="仅基础报告,无最终评级">极简</button>\n'
           ' </span>')
if seg_old in src:
    src = src.replace(seg_old, seg_new, 1)
    log.append(("⑥ 分析深度 → 分段控件", 1))
else:
    log.append(("⑥ 分析深度 → 分段控件", 0)); print("   WARN 分段控件 HTML 未精确命中")

# ══════════════════════════════════════════════════════════════
# CSS 追加：⑤ 控件横排 · ⑥ 分段控件外观 · ⑦ 历史任务两列 · 按钮统一
# ══════════════════════════════════════════════════════════════
CSS = r'''
/* ══════════════════════════════════════════════════════════════════════
   v4 修正层（2026-10-14）
   ══════════════════════════════════════════════════════════════════════ */

/* ── ⑤ 卡片标题行：标题吃余量、控件组不收缩 ─────────────────────────
   问题：h3 与控件组是同一 flex 行的两个子项，控件组被 flex-shrink 压窄后
   内部换行，竖排成三行（用户截图：日期/生成/重新生成 三行竖排）。
   修法：标题 flex:1 吃掉余量，控件组 flex:none 保持自然宽度 ⇒ 一行容纳。 */
.card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > h3 {
  flex: 1 1 auto;
  min-width: 0;
}
.card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > div:nth-child(2) {
  flex: 0 0 auto;
  margin-left: auto;
}
/* 控件组内部：优先横排，宽度不足才换 */
.card > div:has(> h3) > div:nth-child(2) > select { flex: 0 0 auto; min-width: 0; }
.card > div:has(> h3) > div:nth-child(2) > .btn { flex: 0 0 auto; }

/* ── ⑥ 按钮体系统一（参照苹果官网）─────────────────────────────────
   主操作 = 深色药丸（沿用 --btn-primary-bg）
   次操作 = 浅灰药丸
   选项组 = 分段控件（灰底胶囊 + 深色药丸选中） */
.btn {
  border-radius: 980px;
  min-height: 32px;
  padding: 0 18px;
  font-size: var(--fs-sm);
}
.btn-sm { min-height: 28px; padding: 0 14px; font-size: var(--fs-xs); }
/* 次要按钮：浅灰实底，去掉描边（描边+灰底同时存在会显得脏） */
.btn-outline {
  background: var(--bg-hover);
  border: 1px solid transparent;
  color: var(--text);
}
.btn-outline:hover {
  background: var(--border-light);
  border-color: transparent;
  color: var(--text);
}
[data-theme="light"] .btn-outline { background: #f0f0f2; }
[data-theme="light"] .btn-outline:hover { background: #e6e6e9; }

/* 分段控件（苹果 segmented control）*/
.seg {
  display: inline-flex;
  align-items: center;
  background: var(--bg-hover);
  border-radius: 980px;
  padding: 2px;
  gap: 0;
}
[data-theme="light"] .seg { background: #ededf0; }
.seg > .depth-preset {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: var(--fs-xs);
  font-weight: 500;
  padding: 5px 14px;
  min-height: 26px;
  border-radius: 980px;
  cursor: pointer;
  transition: background .18s ease, color .18s ease;
  white-space: nowrap;
}
.seg > .depth-preset:hover { color: var(--text); }
.seg > .depth-preset.active {
  background: var(--text);
  color: var(--bg);
  font-weight: 600;
}
[data-theme="dark"] .seg > .depth-preset.active { background: var(--text); color: #1d1d1f; }
/* 旧的三档描边按钮样式不再使用，但保留 .depth-preset 的其它属性以兼容 */
.depth-preset { cursor: pointer; }

/* ── ⑦ 历史编译任务列表 → 两列 ─────────────────────────────────────
   用户：「最右边的三行横排一下不是又能节省空间吗」+「改成两列排版，
   一列太占空间、空白有点大」。
   两列布局把纵向空间减半；每行内部改为紧凑布局（标题吃余量、右侧操作靠右）。 */
#fg-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 20px;
  row-gap: 0;
  align-items: start;
}
/* 每行：不再换行，压成单行；标题省略号 */
#fg-list > div {
  display: flex !important;
  flex-wrap: nowrap !important;
  align-items: center;
  gap: 8px !important;
  padding: 6px 0 !important;
  min-width: 0;
  overflow: hidden;
}
#fg-list > div > span:first-child { min-width: 42px !important; flex: none; }
/* 标题列：吃余量 + 省略号 */
#fg-list > div > span:nth-child(2) {
  flex: 1 1 auto !important;
  min-width: 0 !important;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
#fg-list > div > span:nth-child(2) b { font-weight: 500; }
/* 日期与操作：定宽不收缩 */
#fg-list > div > span:nth-child(n+3) { flex: none; }
#fg-list a { margin-left: 0 !important; padding: 3px 4px !important; min-height: 24px !important; }
#fg-list > div + div { border-top: 1px solid var(--border-light); }

/* ── ③ 灰底上的正文：确保足够对比 ──────────────────────────────── */
.card, .card * { text-shadow: none; }
/* 卡片正文（非标签）用主文字色，避免"灰底灰字" */
.card > div[style*="color:var(--text-secondary)"],
.card #variety-info,
.card #run-input-body { color: var(--text) !important; }

/* ── ① 去吸顶后：控制条仍需与下方内容有间距 ─────────────────────── */
.page-tools { padding-bottom: 12px; }

/* ── 移动端：两列回退为一列（窄屏两列会挤爆）───────────────────── */
@media (max-width: 900px) {
  #fg-list { grid-template-columns: minmax(0, 1fr); }
  .card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > div:nth-child(2) {
    flex: 0 1 auto;
    margin-left: 0;
    width: 100%;
  }
}
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
if "v4 修正层（2026-10-14）" not in src:
    src = src[:i] + CSS + "\n" + src[i:]
    log.append(("CSS v4 修正层", 1))
else:
    log.append(("CSS v4 修正层", 0)); print("   已存在，跳过")

F.write_text(src, encoding="utf-8")
print("=== v4 修复结果 ===")
for name, n in log:
    print(f"  [{'OK' if n else '未命中'}] {name}")
print()
print(f"{n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("script 配平:", src.count("<script") - src.count("</script>"))
print("残留 sticky（应仅 header/表头）:", len(re.findall(r'position:\s*sticky', src)))
