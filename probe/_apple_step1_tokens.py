# -*- coding: utf-8 -*-
"""Apple 设计语言改造 · 第一步：设计令牌层重写。
替换 web_template.html 的 :root(暗) 与 [data-theme="light"](亮) 两套令牌。
原则：苹果色板 + 特效令牌全部归零（发光/渐变/星云/卡片阴影 -> 无）。
"""
import re, pathlib, sys, shutil

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
orig = src

DARK = r'''/* ============================================================
   DESIGN TOKENS — Apple Design Language
   ============================================================
   【2026-10-14 Apple 化改造】整套皮肤换成 apple.com.cn 的设计语言：
     · 分区代替卡片：底色/细线分层，不再用「边框+圆角+阴影」切格子
     · 零特效：发光、渐变、星云、内高光一律归零（阴影只留给功能性聚焦环）
     · 强调色 = 苹果系统蓝；亮色 #0071e3 / 暗色 #2997ff
     · 圆角药丸化：按钮 980px，容器 12/18px
   ⚠️ 特效类令牌被刻意置为 none/transparent —— 这是「去 AI 味」的核心动作，
      不是遗漏。凡引用它们的旧规则会自动退化为无特效，无需逐条改规则。
   ⚠️ 亮色主题必须逐条给值（不能继承暗色），漏一条就会在白底上继承暗色值。
   ── 强调色族(--brand-*) ────────────────────────────────────
      --brand      强调文字/描边/激活态
      --brand-ink  落在强调色底上的文字色
      --brand-dim  激活态淡底  --brand-line 激活态描边
   ── 涨跌语义色（中国习惯：红涨绿跌）**不动色相语义** ─────────
   ── 图表专用（ECharts 读不到 CSS 变量，由 JS 侧 __cssVar() 取值） ── */
:root {
  /* ── 强调色：苹果系统蓝（暗色模式 systemBlue） ── */
  --brand: #2997ff;
  --brand-hover: #51adff;
  --brand-light: #7cc4ff;
  --brand-ink: #ffffff;
  --brand-shadow: rgba(41,151,255,0.16);
  --brand-glow: transparent;
  --brand-dim: rgba(41,151,255,0.14);
  --brand-line: rgba(41,151,255,0.38);

  /* ── 分类色（苹果系统色，只用于装饰位，不用于涨跌语义位） ── */
  --candy-1: #2997ff;   /* 蓝 */
  --candy-2: #ff9f0a;   /* 橙 */
  --candy-3: #30d158;   /* 绿 */
  --candy-4: #bf5af2;   /* 紫 */

  /* ── 底色：纯黑 + 深灰面（苹果深色模式） ── */
  --bg: #000000;
  --bg-deep: #000000;
  --bg-elevated: #1d1d1f;
  --bg-card: #1d1d1f;
  --bg-input: #1d1d1f;
  --bg-glass: rgba(0,0,0,0.72);

  --border: #424245;
  --border-light: #2c2c2e;
  --border-glow: rgba(255,255,255,0.10);
  /* 复选框方框描边：苹果暗色下拉框的灰阶 */
  --ck-box: #86868b;
  --ck-mark: #ffffff;

  --text: #f5f5f7;
  --text-secondary: #a1a1a6;
  --text-muted: #6e6e73;

  /* ── 涨跌语义色（中国习惯：红涨绿跌）。色相语义保持不变 ── */
  --green: #30d158;
  --green-bg: rgba(48,209,88,0.15);
  --red: #ff453a;
  --red-bg: rgba(255,69,58,0.15);
  --amber: #ff9f0a;
  --amber-bg: rgba(255,159,10,0.15);
  --warn-ink: #ff9f0a;
  --blue: #2997ff;
  --link: #2997ff;   /* 裸 <a> 默认色 */
  --teal: #40c8e0;
  --purple: #bf5af2;

  /* ── 图表专用（JS 侧 __cssVar() 读取） ── */
  --chart-brand: #2997ff;
  --chart-brand-2: #7cc4ff;
  --chart-brand-glow: rgba(41,151,255,0.30);
  --chart-brand-a25: rgba(41,151,255,0.25);
  --chart-brand-a02: rgba(41,151,255,0.02);
  --chart-axis: #86868b;
  --chart-grid: #2c2c2e;
  --chart-line: #424245;

  /* ── 表面质感：全部归零 ── */
  --card-sheen: transparent;
  --card-shadow: none;
  --card-hover-shadow: none;
  --hover-glow: none;
  --thead-bg: var(--bg-elevated);
  --input-focus-ring: 0 0 0 3.5px rgba(41,151,255,0.24);
  --btn-primary-bg: #0071e3;
  --btn-primary-fg: #ffffff;
  --btn-primary-glow: none;
  --btn-primary-glow-h: none;
  --nebula: none;
  --header-shadow: none;

  --selection: rgba(41,151,255,0.30);

  --radius: 12px;
  --radius-lg: 18px;
  --radius-sm: 8px;
  --shadow: none;
  --transition: 0.2s ease;
  --bg-elev: var(--bg-elevated);
  --bg-subtle: var(--bg-input);

  /* ══ 字体栈：苹果栈（SF Pro / 苹方），全站统一，无衬线 ══════════
     2026-10-14：随 Apple 化改造，标题原用的宋体栈(--serif)退役 —— 苹果不用
     衬线标题。--serif 保留定义仅为兼容可能的历史引用，值已回落到无衬线。 */
  --sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
          "PingFang SC", "HarmonyOS Sans SC", "MiSans", "Microsoft YaHei",
          "Helvetica Neue", Arial, sans-serif;
  --serif: var(--sans);
  --num: var(--sans);

  /* ══ K 线图表令牌（canvas 读不到 CSS 变量，由 JS 侧 CHART.* getter 取值） ══ */
  --chart-k-up: #ff453a;
  --chart-k-down: #30d158;
  --chart-k-ma1: #ff9f0a;
  --chart-k-ma2: #2997ff;
  --chart-k-ma3: #bf5af2;
  --chart-k-ma4: #40c8e0;
  --chart-k-boll-mid: #2997ff;
  --chart-k-boll-side: #86868b;
  --chart-k-vol: rgba(41,151,255,0.45);
  --chart-k-axis: #86868b;
  --chart-k-grid: #2c2c2e;
  --chart-k-cross: #86868b;
  --chart-k-font: var(--sans);
  --chart-k-ylab: #7cc4ff;

  /* ══ 历史令牌补齐（被引用但曾未定义） ══ */
  --bg-hover: rgba(255,255,255,0.05);
  --color-background-primary: #ffffff;
  --color-background-secondary: #ffffff;
  --text-primary: #222222;
  --orange: #ff9f0a;
  --surface2: rgba(255,255,255,0.04);
}'''

LIGHT = r'''[data-theme="light"] {
  /* ── Apple 亮色：纯白底 + 近黑字 + 系统蓝（apple.com.cn 主用配色） ── */
  --brand: #0071e3;
  --brand-hover: #0077ed;
  --brand-light: #e8f2fe;
  --brand-ink: #ffffff;
  --brand-shadow: rgba(0,113,227,0.16);
  --brand-glow: transparent;
  --brand-dim: rgba(0,113,227,0.09);
  --brand-line: rgba(0,113,227,0.34);

  --candy-1: #0071e3;
  --candy-2: #f56300;
  --candy-3: #00875a;
  --candy-4: #8944ab;

  --warn-ink: var(--amber-d);
  --link: #0066cc;

  --bg: #ffffff;
  --bg-deep: #000000;   /* 刻意两主题同值：加载遮罩永远是深底 */
  --bg-elevated: #ffffff;
  --bg-card: #ffffff;
  --bg-input: #ffffff;
  --bg-glass: rgba(255,255,255,0.72);

  --border: #d2d2d7;
  --border-light: #e8e8ed;
  --border-glow: rgba(0,0,0,0.08);
  --ck-box: #86868b;
  --ck-mark: #ffffff;

  --text: var(--ink);
  --text-secondary: var(--muted);
  --text-muted: #86868b;

  /* 质感：亮色下彻底平掉 */
  --card-sheen: transparent;
  --card-shadow: none;
  --card-hover-shadow: none;
  --hover-glow: none;
  --thead-bg: var(--bg-elevated);
  --input-focus-ring: 0 0 0 3.5px rgba(0,113,227,0.16);
  --btn-primary-bg: #0071e3;
  --btn-primary-fg: #ffffff;
  --btn-primary-glow: none;
  --btn-primary-glow-h: none;
  --nebula: none;
  --header-shadow: none;
  --selection: rgba(0,113,227,0.20);

  --chart-brand: #0071e3;
  --chart-brand-2: #f56300;
  --chart-brand-glow: rgba(0,113,227,0.28);
  --chart-brand-a25: rgba(0,113,227,0.25);
  --chart-brand-a02: rgba(0,113,227,0.02);
  --chart-axis: #86868b;
  --chart-grid: #e8e8ed;
  --chart-line: #d2d2d7;

  --shadow: none;

  /* ══ Apple 亮色补充色板 ══════════════════════════════════════
     保留原令牌名（--navy/--gold 等）以免历史引用断链，但值改为苹果灰阶，
     不再作为主色使用。 */
  --navy: #1d1d1f;
  --navy2: #424245;
  --gold: #d2d2d7;
  --gold-d: #6e6e73;
  --gold-l: #86868b;
  --gold-w: #f5f5f7;
  --ink: #1d1d1f;
  --muted: #6e6e73;
  --line: #d2d2d7;
  --soft: #f5f5f7;
  --amber-d: #b25000;

  /* ── 涨跌语义色（亮色）：白底 AA 合规 ── */
  --red: #d70015;
  --red-bg: rgba(215,0,21,0.10);
  --green: #00875a;
  --green-bg: rgba(0,135,90,0.10);

  /* ── K 线图表令牌（亮色） ── */
  --chart-k-up: #d70015;
  --chart-k-down: #00875a;
  --chart-k-ma1: #f56300;
  --chart-k-ma2: #0071e3;
  --chart-k-ma3: #8944ab;
  --chart-k-ma4: #007d8a;
  --chart-k-boll-mid: #0071e3;
  --chart-k-boll-side: #86868b;
  --chart-k-vol: rgba(0,113,227,0.42);
  --chart-k-axis: #86868b;
  --chart-k-grid: #e8e8ed;
  --chart-k-cross: #86868b;
  --chart-k-font: var(--sans);
  --chart-k-ylab: #0071e3;

  /* ── 历史令牌补齐 ── */
  --bg-hover: #f5f5f7;
  --color-background-primary: #ffffff;
  --color-background-secondary: #ffffff;
  --text-primary: #222222;
  --orange: #b25000;
  --surface2: #f5f5f7;
}'''

# ── 1) 替换 :root 令牌块 ──────────────────────────────
m = re.search(r':root \{.*?\n\}', src, re.S)
if not m:
    sys.exit("FAIL: 未找到 :root 块")
src = src[:m.start()] + DARK + src[m.end():]

# ── 2) 替换 [data-theme="light"] 令牌块 ───────────────
m = re.search(r'\[data-theme="light"\] \{.*?\n\}', src, re.S)
if not m:
    sys.exit("FAIL: 未找到亮色令牌块")
src = src[:m.start()] + LIGHT + src[m.end():]

# ── 3) 深蓝金全局规则的字体部分：标题退出宋体 ──────────
old_font_block = """.card h3,
.expander-header,
.report-content h1, .report-content h2, .report-content h3 { font-family: var(--serif); }

/* 数字：Georgia 数字栈（表格数值 / 指标值对齐更稳）。th 一并进 —— 表头与单元格同族，
   否则同一张表"头是无衬线、体是 Georgia"打架。 */
table th, table td { font-family: var(--num); }"""
new_font_block = """/* 2026-10-14 Apple 化：标题/数字原走的宋体栈(--serif)/Georgia 栈退役 ——
   苹果的标题与数字同为无衬线 SF Pro。此处仅保留「数字用 tabular-nums 对齐」这条
   功能性意图，字族交回全局 --sans。 */
.card h3,
.expander-header,
.report-content h1, .report-content h2, .report-content h3 { font-family: var(--sans); }

table th, table td { font-family: var(--sans); font-variant-numeric: tabular-nums; }"""
if old_font_block in src:
    src = src.replace(old_font_block, new_font_block, 1)
else:
    print("WARN: 字体块未精确命中，尝试宽松匹配")
    src = re.sub(r'\.card h3,\s*\n\.expander-header,\s*\n\.report-content h1.*?table th, table td \{[^}]*\}',
                 new_font_block, src, count=1, flags=re.S)

# ── 4) html 基准字号 15px -> 16px（苹果正文 17px 需正基准） ──
src = src.replace("html { font-size: 15px; scroll-behavior: smooth; }",
                  "html { font-size: 16px; scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }", 1)

F.write_text(src, encoding="utf-8")
print("OK 令牌层已替换")
print("  文件行数:", src.count("\n") + 1)
print("  字节:", len(src.encode('utf-8')))
for probe in ["--brand: #2997ff", "--brand: #0071e3", "--nebula: none", "--card-shadow: none",
              "html { font-size: 16px", "--btn-primary-bg: #0071e3"]:
    print(f"  校验 {probe!r}: {'OK' if probe in src else 'MISSING'}")
