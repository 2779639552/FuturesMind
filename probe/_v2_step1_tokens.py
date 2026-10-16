# -*- coding: utf-8 -*-
"""重做视觉层 · 第一步：设计令牌层（v2）
与前一轮的差别：
  · 新增**间距阶**(--sp-*) 与**字号阶**(--fs-*)，让布局和排版有唯一真源
  · 字体栈改为「Mac 用苹果原字体 / Windows 用本地 Inter + Noto Sans SC」——
    上一轮的 -apple-system/PingFang SC 在 Windows 上全部落空，实际回退到了微软雅黑
  · @font-face 引入已下载到 static/fonts 的 4 个 Inter 字重（本地化，不依赖 CDN）
  · 新增 --bg-tile（色块卡片底）与 --bg-tile-nested（卡片内嵌套卡片底）
  · 特效令牌一律归零
"""
import re, pathlib, sys

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")

DARK = r'''/* ══════════════════════════════════════════════════════════════════
   DESIGN TOKENS — Apple Design Language (v2)
   ============================================================
   【2026-10-14 v2 重做】上一轮直接在旧 CSS 上打补丁，导致间距/字号被旧规则
   与内联样式顶回去、紫色残留。本版改为：令牌层 → 组件层 → 单一设计层 → 内联收敛，
   四层各司其职，层内不允许出现"临时补丁"。

   ── 唯一真源（改这两组就改全站节奏）────────────────────────────
      --sp-*  间距阶：所有 gap / padding / margin 只能取这些值
      --fs-*  字号阶：所有 font-size 只能取这些值（地板 12px）

   ── 字号阶（8 档，rem，基准 html=16px）──────────────────────────
      --fs-xs 12px  标签/徽标/表头/元信息    --fs-sm 13px  次级文字
      --fs-base 15px 正文/输入/按钮/表格     --fs-md 17px  强调正文
      --fs-lg 21px  卡片标题                --fs-xl 28px  区块标题/KPI
      --fs-2xl 40px 主指标

   ── 间距阶（9 档）───────────────────────────────────────────────
      --sp-1 4 / --sp-2 8 / --sp-3 12 / --sp-4 16 / --sp-5 20
      --sp-6 24 / --sp-7 32 / --sp-8 40 / --sp-9 56

   ── 字体 ────────────────────────────────────────────────────────
      Mac   → SF Pro Text/Display（-apple-system）
      Win   → Inter（拉丁，本地 woff2）+ Noto Sans SC（中文，系统已装）
      ⚠️ 上一版把 -apple-system/PingFang SC 写在最前，Windows 上全部落空、
         回退微软雅黑 —— 故本版把 Inter 提到 PingFang 之前，保证 Windows 也命中现代无衬线。

   ── 特效 ────────────────────────────────────────────────────────
      发光/渐变/星云/卡片阴影全部归零。阴影只留给功能性聚焦环。
   ────────────────────────────────────────────────────────────── */
:root {
  /* 强调色：苹果系统蓝（暗色模式 systemBlue） */
  --brand: #2997ff;
  --brand-hover: #51adff;
  --brand-light: #7cc4ff;
  --brand-ink: #ffffff;
  --brand-shadow: rgba(41,151,255,0.16);
  --brand-glow: transparent;
  --brand-dim: rgba(41,151,255,0.14);
  --brand-line: rgba(41,151,255,0.38);

  /* 分类色（苹果系统色；仅装饰位，禁用于涨跌语义位） */
  --candy-1: #2997ff;
  --candy-2: #ff9f0a;
  --candy-3: #30d158;
  --candy-4: #bf5af2;

  /* 底色：纯黑 + 深灰面（苹果深色模式） */
  --bg: #000000;
  --bg-deep: #000000;
  --bg-elevated: #1d1d1f;
  --bg-card: #1d1d1f;
  --bg-input: #1d1d1f;
  --bg-glass: rgba(0,0,0,0.72);
  --bg-tile: #1d1d1f;          /* 色块卡片底（亮色主题换 #f5f5f7） */
  --bg-tile-nested: #2c2c2e;   /* 卡片内嵌套卡片底 */

  --border: #424245;
  --border-light: #2c2c2e;
  --border-glow: rgba(255,255,255,0.10);
  --ck-box: #86868b;
  --ck-mark: #ffffff;

  --text: #f5f5f7;
  --text-secondary: #a1a1a6;
  --text-muted: #86868b;

  /* 涨跌语义色（中国习惯：红涨绿跌）——色相语义刻意不变 */
  --green: #30d158;
  --green-bg: rgba(48,209,88,0.16);
  --red: #ff453a;
  --red-bg: rgba(255,69,58,0.16);
  --amber: #ff9f0a;
  --amber-bg: rgba(255,159,10,0.16);
  --warn-ink: #ff9f0a;
  --blue: #2997ff;
  --link: #2997ff;
  --teal: #40c8e0;
  --purple: #bf5af2;

  /* 图表（ECharts 读不到 CSS 变量，由 JS 侧 __cssVar() 取值） */
  --chart-brand: #2997ff;
  --chart-brand-2: #7cc4ff;
  --chart-brand-glow: rgba(41,151,255,0.30);
  --chart-brand-a25: rgba(41,151,255,0.25);
  --chart-brand-a02: rgba(41,151,255,0.02);
  --chart-axis: #86868b;
  --chart-grid: #2c2c2e;
  --chart-line: #424245;

  /* 间距阶 —— 全站唯一真源 */
  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px; --sp-4: 16px;
  --sp-5: 20px; --sp-6: 24px; --sp-7: 32px; --sp-8: 40px; --sp-9: 56px;

  /* 字号阶 —— 全站唯一真源（地板 12px） */
  --fs-xs: 0.75rem;
  --fs-sm: 0.8125rem;
  --fs-base: 0.9375rem;
  --fs-md: 1.0625rem;
  --fs-lg: 1.3125rem;
  --fs-xl: 1.75rem;
  --fs-2xl: 2.5rem;

  /* 字体栈：Mac 走苹果原字体；Windows 走本地 Inter + Noto Sans SC */
  --sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
          "Inter", "PingFang SC", "Noto Sans SC", "Source Han Sans SC",
          "Microsoft YaHei", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --serif: var(--sans);
  --num: var(--sans);

  /* 表面质感：全部归零 */
  --card-sheen: transparent;
  --card-shadow: none;
  --card-hover-shadow: none;
  --hover-glow: none;
  --thead-bg: transparent;
  --input-focus-ring: 0 0 0 3px rgba(41,151,255,0.30);
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
  --bg-hover: rgba(255,255,255,0.06);

  /* K 线图表令牌 */
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

  /* 历史令牌补齐（被引用但曾未定义；值取原兜底值，外观零变化） */
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
  --bg-card: #f5f5f7;
  --bg-input: #ffffff;
  --bg-glass: rgba(255,255,255,0.72);
  --bg-tile: #f5f5f7;
  --bg-tile-nested: #ffffff;

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
  --thead-bg: transparent;
  --input-focus-ring: 0 0 0 3px rgba(0,113,227,0.22);
  --btn-primary-bg: #0071e3;
  --btn-primary-fg: #ffffff;
  --btn-primary-glow: none;
  --btn-primary-glow-h: none;
  --nebula: none;
  --header-shadow: none;
  --selection: rgba(0,113,227,0.20);

  --chart-brand: #0071e3;
  --chart-brand-2: #1d1d1f;
  --chart-brand-glow: rgba(0,113,227,0.28);
  --chart-brand-a25: rgba(0,113,227,0.25);
  --chart-brand-a02: rgba(0,113,227,0.02);
  --chart-axis: #86868b;
  --chart-grid: #e8e8ed;
  --chart-line: #d2d2d7;

  --shadow: none;
  --bg-hover: #f5f5f7;

  /* 保留旧令牌名以免历史引用断链；值改为苹果灰阶 */
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

  /* 涨跌语义色（亮色）：白底 AA 合规 */
  --red: #d70015;
  --red-bg: rgba(215,0,21,0.10);
  --green: #00875a;
  --green-bg: rgba(0,135,90,0.10);

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

  --color-background-primary: #ffffff;
  --color-background-secondary: #ffffff;
  --text-primary: #222222;
  --orange: #b25000;
  --surface2: #ffffff;
}'''

# ── 1) 替换两个令牌块 ────────────────────────────────
m = re.search(r':root \{.*?\n\}', src, re.S)
if not m:
    sys.exit("FAIL: 未找到 :root 块")
src = src[:m.start()] + DARK + src[m.end():]

m = re.search(r'\[data-theme="light"\] \{.*?\n\}', src, re.S)
if not m:
    sys.exit("FAIL: 未找到亮色令牌块")
src = src[:m.start()] + LIGHT + src[m.end():]

# ── 2) @font-face 注入（本地 Inter，4 个字重）──────────
FONTFACE = r'''
/* ── 本地字体：Inter（拉丁字形）────────────────────────────────────
   为什么本地化而不是走 CDN：项目是局域网服务(10.220.6.48)，访问者机器未必能出外网；
   放在 static/fonts 可保证任何访问者看到的都是同一套字形。
   只取拉丁子集（中文交给系统已装的 Noto Sans SC），4 个字重合计 96KB。 */
@font-face { font-family: 'Inter'; font-style: normal; font-weight: 400; font-display: swap;
  src: url('/static/fonts/inter-latin-400.woff2') format('woff2'); }
@font-face { font-family: 'Inter'; font-style: normal; font-weight: 500; font-display: swap;
  src: url('/static/fonts/inter-latin-500.woff2') format('woff2'); }
@font-face { font-family: 'Inter'; font-style: normal; font-weight: 600; font-display: swap;
  src: url('/static/fonts/inter-latin-600.woff2') format('woff2'); }
@font-face { font-family: 'Inter'; font-style: normal; font-weight: 700; font-display: swap;
  src: url('/static/fonts/inter-latin-700.woff2') format('woff2'); }
'''
anchor = "/* ══════════════════════════════════════════════════════════════════\n   DESIGN TOKENS"
i = src.find(anchor)
if i < 0:
    sys.exit("FAIL: 未找到令牌层锚点")
src = src[:i] + FONTFACE + "\n" + src[i:]

# ── 3) html 基准字号 15px -> 16px（字号阶按 16px 基准设计）──
old = "html { font-size: 15px; scroll-behavior: smooth; }"
new = "html { font-size: 16px; scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }"
if old in src:
    src = src.replace(old, new, 1)
else:
    src = re.sub(r'html \{ font-size: 15px;', 'html { font-size: 16px;', src, count=1)

# ── 4) 标题/表格退出宋体栈与 Georgia ─────────────────
old_font = """.card h3,
.expander-header,
.report-content h1, .report-content h2, .report-content h3 { font-family: var(--serif); }

/* 数字：Georgia 数字栈（表格数值 / 指标值对齐更稳）。th 一并进 —— 表头与单元格同族，
   否则同一张表"头是无衬线、体是 Georgia"打架。 */
table th, table td { font-family: var(--num); }"""
new_font = """/* 2026-10-14：标题/数字原走的宋体栈/Georgia 退役 —— 苹果的标题与数字同为无衬线。
   只保留「数字等宽对齐」这条功能性意图。 */
.card h3,
.expander-header,
.report-content h1, .report-content h2, .report-content h3 { font-family: var(--sans); }

table th, table td { font-family: var(--sans); font-variant-numeric: tabular-nums; }"""
if old_font in src:
    src = src.replace(old_font, new_font, 1)
else:
    print("WARN: 字体块未精确命中，走宽松匹配")
    src = re.sub(r'\.card h3,\s*\n\.expander-header,\s*\n\.report-content h1.*?table th, table td \{[^}]*\}',
                 new_font, src, count=1, flags=re.S)

F.write_text(src, encoding="utf-8")
print("OK 令牌层 v2 完成")
print("  行数:", src.count("\n") + 1, " 字节:", len(src.encode('utf-8')))
for probe in ["--sp-5: 20px", "--fs-xl: 1.75rem", "--bg-tile: #f5f5f7", "--bg-tile-nested: #2c2c2e",
              "inter-latin-400.woff2", "html { font-size: 16px", '"Inter", "PingFang SC"']:
    print(f"  校验 {probe!r}: {'OK' if probe in src else 'MISSING'}")
