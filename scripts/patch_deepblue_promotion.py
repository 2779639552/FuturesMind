# -*- coding: utf-8 -*-
r"""深蓝金推广补丁（2026-10-13）：把 [data-theme="light"] #tab-quant 作用域的
「冷白底 + 深蓝(navy) + 金(gold)」风格推广到全站亮色主题。

任务编号：#144 图表令牌 / #145 CSS 令牌 / #146 字体规则 / #147 遗留作用域收敛 /
         #149 切主题图表重染（#148 CHART 字符串字面量已由并行会话修完，本脚本只验证）。

安全约定（本项目"同文件多处编辑静默丢改"踩过多次）：
  1) 先备份到 backup/；
  2) 每处替换前断言锚点命中次数，任何一处不符 → 整体作废，不写盘；
  3) EOL 纪律：web_template.html 为 CRLF。读时统一转 \n 处理，写回转 \r\n，
     写完自检 bareLF == 0（ lone \n 会污染 diff 且违反项目约定）。
"""
import re
import shutil
import sys
import datetime
from pathlib import Path

ROOT = Path(r"D:\AgentSense\AgentSense")
PATH = ROOT / "web_template.html"
BACKUP_DIR = ROOT / "backup"


def die(msg: str) -> None:
    print(f"[ABORT] {msg}")
    sys.exit(1)


def apply(text: str, old: str, new: str, expect: int, tag: str) -> str:
    """带命中数断言的替换；expect=None 表示不校验次数（调用方已单独断言）。"""
    n = text.count(old)
    if expect is not None and n != expect:
        die(f"{tag}: 锚点命中 {n} 次，预期 {expect} 次 —— 拒绝写盘")
    if n == 0:
        die(f"{tag}: 锚点未命中 —— 拒绝写盘")
    return text.replace(old, new)


def main() -> None:
    raw = PATH.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        die(f"UTF-8 解码失败: {e}")

    # ── EOL 规范化：CRLF → \n（处理空间统一用 \n） ──
    if text.count("\r") != text.count("\r\n"):
        die("文件含孤立 \\r，EOL 异常 —— 拒绝处理")
    bare_lf_before = len(re.findall(r"(?<!\r)\n", text))
    if text.count("\r\n") == 0:
        die("文件不是 CRLF，与项目约定不符 —— 拒绝处理")
    text = text.replace("\r\n", "\n")

    # ── 0. 锚点预检（写盘前最后一道闸） ──
    pre = {
        'light 全局块': ('[data-theme="light"] {\n', 1),
        'quant 作用域块': ('[data-theme="light"] #tab-quant {\n', 1),
        'const isDark': ("const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';", 16),
        'isDark axis 三元': ("isDark ? CHART.axis : '#666'", 16),
        'isDark line 三元': ("isDark ? CHART.line : '#ddd'", 2),
        'isDark 666/999 三元': ("isDark ? '#666' : '#999'", 2),
        'isDark grid 三元': ("isDark ? CHART.grid : '#eee'", 12),
        'isDark aaa 三元': ("isDark ? '#aaa' : '#666'", 1),
        'signal-banner 亮色规则': ('[data-theme="light"] .signal-banner {\n', 1),
        '旧信号横幅渐变': ('background: linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%);', 1),
        'chartInstances 状态字段': ('_chartInstances: {}', 1),
        'lastKlineData': ('this._lastKlineData', 3),
        # #148 并行会话修复成果的验证（应为 0）
        "CHART.up 字面量": ("'CHART.up'", 0),
        "CHART.down 字面量": ("'CHART.down'", 0),
        "CHART.brand 字面量": ("'CHART.brand'", 0),
    }
    for tag, (needle, expect) in pre.items():
        n = text.count(needle)
        if n != expect:
            die(f"预检失败 {tag}: 命中 {n}，预期 {expect}")
    boiler = re.findall(
        r"^[ \t]*const isDark = \(document\.documentElement\.getAttribute\('data-theme'\) \|\| 'dark'\) === 'dark';\n"
        r"([ \t]*)const textColor = isDark \? CHART\.axis : '#666';\n"
        r"[ \t]*const gridColor = isDark \? CHART\.grid : '#eee';",
        text, re.M)
    if len(boiler) != 10:
        die(f"预检失败 三行样板: 命中 {len(boiler)}，预期 10")
    print(f"[OK] 锚点预检全部通过（isDark 常量 16 处 / 三行样板 10 处 / CHART 字面量 0 处）")

    # ── 1. 备份 ──
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"web_template_before_deepblue_promotion_{stamp}.html"
    shutil.copy2(PATH, backup)
    print(f"[OK] 备份 → {backup.name}")

    # ════════════════════════════════════════════════════════════════
    # #145 CSS 令牌：糖果紫 → 深蓝金（全局亮色块）
    # ════════════════════════════════════════════════════════════════

    # E1 亮色块头注
    text = apply(text,
        '/* Light Theme\n'
        '   【2026-09-15 皮肤重构契约】亮色主题外观必须保持与改造前一致。做法:把 :root 里\n'
        '   改写成糖果紫/糖果四色的令牌,在这里**逐条给出亮色专用值**(同样的色相,换一档明度)。\n'
        '   凡是 :root 新增的令牌,这里必须给出对应值,否则会继承到深色版(典型故障:亮色下\n'
        '   --brand 变成 #c7c7ff → 白底上的浅紫字,整站强调色"消失")。 */',
        '/* Light Theme\n'
        '   【2026-09-15 皮肤重构契约】亮色主题必须逐条给出专用值(不能继承深色版);\n'
        '   【2026-10-13 深蓝金推广】原"糖果紫"亮色令牌整体换成「冷白底 + 深蓝(navy) + 金(gold)」\n'
        '   色板(源自用户给定量化页配色, 现推广到全站亮色主题; 暗色主题不动)。\n'
        '   凡是 :root 新增的令牌,这里必须给出对应值,否则会继承到深色版(典型故障:亮色下\n'
        '   --brand 变成 #c7c7ff → 白底上的浅紫字,整站强调色"消失")。 */',
        1, 'E1 亮色块头注')

    # E2 品牌族
    text = apply(text,
        '  /* 品牌色:糖果紫。亮底上取深一档(#5b5bd6),否则 #c7c7ff 在白底上读不出来 */\n'
        '  --brand: #5b5bd6;\n'
        '  --brand-hover: #4a4ac0;\n'
        '  --brand-light: #9595ff;\n'
        '  --brand-ink: #ffffff;\n'
        '  --brand-shadow: rgba(91,91,214,0.28);\n'
        '  --brand-glow: rgba(91,91,214,0.30);\n'
        '  --brand-dim: rgba(149,149,255,0.12);\n'
        '  --brand-line: rgba(91,91,214,0.32);',
        '  /* 品牌色:深蓝金。强调文字用 --gold-d(5.01:1 浅底小字); hover/描边走深蓝;\n'
        '     --brand-light 取 --gold-l 只作大字/装饰; --brand-line 的 --gold(2.85:1) 只做边框。 */\n'
        '  --brand: var(--gold-d);\n'
        '  --brand-hover: var(--navy);\n'
        '  --brand-light: var(--gold-l);\n'
        '  --brand-ink: #ffffff;\n'
        '  --brand-shadow: rgba(184,147,90,0.22);\n'
        '  --brand-glow: rgba(184,147,90,0.26);\n'
        '  --brand-dim: rgba(184,147,90,0.10);\n'
        '  --brand-line: var(--gold);',
        1, 'E2 品牌族')

    # E3 降级提示 + 链接
    text = apply(text,
        '  /* 降级提示文字:亮底上取深琥珀(amber-700)。#fbbf24 在白底上读不出来 */\n'
        '  --warn-ink: #b45309;\n'
        "  --link: #2563eb;   /* 亮色主题链接色: 沿用暗色的 #60a5fa 在白底上对比度仅 ~2.4:1, 换 blue-600 */",
        '  /* 降级提示文字:亮底上取深琥珀(amber-700)。#fbbf24 在白底上读不出来 */\n'
        '  --warn-ink: var(--amber-d);\n'
        '  --link: var(--navy2);   /* 亮色主题链接色: 深蓝 #1b4571(白底 7.4:1); 原 #2563eb 属糖果蓝系, 随糖果紫一并退场 */',
        1, 'E3 链接/降级')

    # E4 底色层级
    text = apply(text,
        '  --bg: #f5f5f5;\n'
        '  --bg-deep: #050308;   /* 刻意两主题同值:加载遮罩永远是深底(黑底+白环组件靠它衬托),不随主题变浅 */\n'
        '  --bg-elevated: #ffffff;\n'
        '  --bg-card: #ffffff;\n'
        '  --bg-input: #f0f0f0;\n'
        '  --bg-glass: #ffffff;',
        '  --bg: #f7f9fb;\n'
        '  --bg-deep: #050308;   /* 刻意两主题同值:加载遮罩永远是深底(黑底+白环组件靠它衬托),不随主题变浅 */\n'
        '  --bg-elevated: #ffffff;\n'
        '  --bg-card: #ffffff;\n'
        '  --bg-input: #ffffff;\n'
        '  --bg-glass: #ffffff;',
        1, 'E4 底色层级')

    # E5 边框族
    text = apply(text,
        '  --border: #e0e0e0;\n'
        '  --border-light: #d0d0d0;\n'
        '  --border-glow: rgba(91,91,214,0.16);',
        '  --border: var(--line);\n'
        '  --border-light: #e8edf3;\n'
        '  --border-glow: rgba(184,147,90,0.18);',
        1, 'E5 边框族')

    # E6 文字族
    text = apply(text,
        '  --text: #1a1a1a;\n'
        '  --text-secondary: #555;\n'
        '  --text-muted: #999;',
        '  --text: var(--ink);\n'
        '  --text-secondary: var(--muted);\n'
        '  --text-muted: #999;',
        1, 'E6 文字族')

    # E7 构件质感
    text = apply(text,
        '  /* 质感:亮色下保持克制(不加辉光/星云,只保留糖果紫的一层淡底) */\n'
        '  --card-sheen: rgba(255,255,255,0);\n'
        '  --card-shadow: none;\n'
        '  --card-hover-shadow: 0 2px 8px rgba(0,0,0,0.08);\n'
        '  --hover-glow: 0 0 0 0 rgba(0,0,0,0);\n'
        '  --thead-bg: linear-gradient(180deg, rgba(149,149,255,0.10), rgba(149,149,255,0.03)), var(--bg-input);\n'
        '  --input-focus-ring: 0 0 0 3px var(--brand-shadow);\n'
        '  --btn-primary-bg: linear-gradient(135deg, #6d6af0, #9595ff);\n'
        '  --btn-primary-fg: #ffffff;\n'
        '  --btn-primary-glow: 0 4px 15px rgba(91,91,214,0.30);\n'
        '  --btn-primary-glow-h: 0 6px 20px rgba(91,91,214,0.34);\n'
        '  --nebula: none;\n'
        '  --header-shadow: none;\n'
        '  --selection: rgba(149,149,255,0.22);',
        '  /* 质感:亮色下保持克制(不加辉光/星云,只保留深蓝金的一层极淡底) */\n'
        '  --card-sheen: rgba(255,255,255,0);\n'
        '  --card-shadow: none;\n'
        '  --card-hover-shadow: 0 2px 10px rgba(14,42,71,0.10);\n'
        '  --hover-glow: 0 0 0 0 rgba(0,0,0,0);\n'
        '  --thead-bg: linear-gradient(180deg, rgba(14,42,71,0.055), rgba(14,42,71,0.018)), var(--bg-input);\n'
        '  --input-focus-ring: 0 0 0 3px var(--brand-shadow);\n'
        '  --btn-primary-bg: var(--navy);\n'
        '  --btn-primary-fg: var(--gold-w);   /* 深蓝底 + 金字:色板自带的 6.89:1 组合 */\n'
        '  --btn-primary-glow: 0 4px 14px rgba(14,42,71,0.22);\n'
        '  --btn-primary-glow-h: 0 6px 18px rgba(14,42,71,0.28);\n'
        '  --nebula: none;\n'
        '  --header-shadow: none;\n'
        '  --selection: rgba(184,147,90,0.22);',
        1, 'E7 构件质感')

    # E8 图表令牌（#144）
    text = apply(text,
        '  /* 图表:亮色分支用糖果紫 + 浅灰轴 */\n'
        '  --chart-brand: #5b5bd6;\n'
        '  --chart-brand-2: #9595ff;\n'
        '  --chart-brand-glow: rgba(91,91,214,0.35);\n'
        '  --chart-brand-a25: rgba(91,91,214,0.25);\n'
        '  --chart-brand-a02: rgba(91,91,214,0.02);\n'
        '  --chart-axis: #666666;\n'
        '  --chart-grid: #eeeeee;\n'
        '  --chart-line: #dddddd;',
        '  /* 图表:亮色分支用深蓝金(主序列 navy2, 次序列 gold; 轴/网格走 muted/line) */\n'
        '  --chart-brand: #1b4571;              /* navy2 —— 主序列线/柱 */\n'
        '  --chart-brand-2: #b8935a;            /* gold  —— 次序列/强调节点 */\n'
        '  --chart-brand-glow: rgba(27,69,113,0.30);\n'
        '  --chart-brand-a25: rgba(27,69,113,0.25);\n'
        '  --chart-brand-a02: rgba(27,69,113,0.02);\n'
        '  --chart-axis: #5a6472;               /* --muted */\n'
        '  --chart-grid: #dde3ea;               /* --line */\n'
        '  --chart-line: #dde3ea;               /* --line */',
        1, 'E8 图表令牌')

    # E9 投影
    text = apply(text,
        '  --shadow: 0 2px 8px rgba(0,0,0,0.08);',
        '  --shadow: 0 2px 8px rgba(14,42,71,0.08);',
        1, 'E9 投影')

    # E10 调色板注释（去掉"只在 quant 作用域"的过时表述）
    text = apply(text,
        '  /* ══ 深蓝金调色板（2026-10-12） ═══════════════════════════════════════\n'
        '     来源 = 用户给定色板。**只在 [data-theme="light"] #tab-quant 作用域内使用**\n'
        '     （量化分析页 × 亮色主题）；暗色主题与其余页签不引用它们。\n'
        '     对比度按 spec 自带比值选档，勿混：',
        '  /* ══ 深蓝金调色板（2026-10-12 定稿, 2026-10-13 推广到全站亮色） ══════════\n'
        '     来源 = 用户给定色板。亮色主题全局使用; 暗色主题不引用它们(走 :root 原值)。\n'
        '     对比度按 spec 自带比值选档，勿混：',
        1, 'E10 调色板注释')

    # E11 K 线令牌注释
    text = apply(text,
        '  /* ── K 线图表令牌（亮色 = 深蓝金；深色主题走 :root 的原值） ── */',
        '  /* ── K 线图表令牌（亮色 = 深蓝金；深色主题走 :root 的原值；全站图表通用令牌见上方 --chart-*） ── */',
        1, 'E11 K线注释')

    # E12 --bg-hover 跟随即将全局化的 quant 作用域值（锚点须带上文注释: :root 里有一处同值）
    text = apply(text,
        '  /* ── 补齐漏定义令牌（亮色值 = 原兜底值，外观零变化） ── */\n'
        '  --bg-hover: rgba(127,127,127,.08);',
        '  /* ── 补齐漏定义令牌（亮色值 = 原兜底值，外观零变化） ── */\n'
        '  --bg-hover: var(--soft);   /* 表格行 hover 底: 冷白(深蓝金底色族), 替代中性灰 */',
        1, 'E12 bg-hover')

    # ════════════════════════════════════════════════════════════════
    # #145/#146/#147 删除 #tab-quant 作用域块，三条规则 + 体字体上移为全局
    # ════════════════════════════════════════════════════════════════
    quant_scope_old = (
        '/* ============================================================\n'
        '   量化分析页 · 深蓝金（2026-10-12，仅亮色主题）\n'
        '   ============================================================\n'
        '   作用域刻意收窄到 [data-theme="light"] #tab-quant：\n'
        '     · 暗色主题完全不进这个块 → 量化页在暗色下仍是糖果紫（"暗色不动"）\n'
        '     · 其余 7 个标签页不受影响（选择器一律带 #tab-quant 限定）\n'
        '   做法沿用本项目 2026-09-15 皮肤重构的既有契约：不动布局 / 信息密度 / 涨跌语义，\n'
        '   只把通用令牌在本作用域内重映射成「冷白底 + 深蓝(navy) + 金(gold)」。\n'
        '   ⚠️ 选色纪律（来自色板自带对比度）：\n'
        '     · 强调**文字**用 --gold-d(5.01:1)；--gold(2.85:1) 只准做线条/边框，绝不作文字\n'
        '     · 表头刻意走"极淡深蓝底"而不是实心深蓝底 —— 因为 .delta-tbl thead th 的文字色是\n'
        '       --brand(=gold-d)，压在实心 navy 上只有约 2.8:1，不达标\n'
        '     · 主按钮才用「实心深蓝底 + --gold-w 金字」这组 6.89:1 的官方搭配\n'
        '   ============================================================ */\n'
        '[data-theme="light"] #tab-quant {\n'
        '  /* ── 底色层级：中性灰 → 冷白 ── */\n'
        '  --bg: #f7f9fb;\n'
        '  --bg-elevated: #ffffff;\n'
        '  --bg-card: #ffffff;\n'
        '  --bg-input: #ffffff;\n'
        '  --bg-hover: var(--soft);\n'
        '  --border: var(--line);\n'
        '  --border-light: #e8edf3;\n'
        '  --border-glow: rgba(184,147,90,0.18);\n'
        '\n'
        '  /* ── 文字：深墨蓝替代中性黑 ── */\n'
        '  --text: var(--ink);\n'
        '  --text-secondary: var(--muted);\n'
        '\n'
        '  /* ── 强调色：糖果紫 → 金(文字) + 深蓝(hover/描边)，--gold 只做边框 ── */\n'
        '  --brand: var(--gold-d);\n'
        '  --brand-hover: var(--navy);\n'
        '  --brand-light: var(--gold-l);\n'
        '  --brand-ink: #ffffff;\n'
        '  --brand-shadow: rgba(184,147,90,0.22);\n'
        '  --brand-glow: rgba(184,147,90,0.26);\n'
        '  --brand-dim: rgba(184,147,90,0.10);\n'
        '  --brand-line: var(--gold);\n'
        '  --link: var(--navy2);\n'
        '  --warn-ink: var(--amber-d);\n'
        '\n'
        '  /* ── 构件质感：表头极淡深蓝底（金字仍达 5:1）、卡片顶边金线 ── */\n'
        '  --thead-bg: linear-gradient(180deg, rgba(14,42,71,0.055), rgba(14,42,71,0.018)), var(--bg-input);\n'
        '  --card-sheen: rgba(255,255,255,0);\n'
        '  --card-shadow: none;\n'
        '  --card-hover-shadow: 0 2px 10px rgba(14,42,71,0.10);\n'
        '  --hover-glow: 0 0 0 0 rgba(0,0,0,0);\n'
        '  --input-focus-ring: 0 0 0 3px var(--brand-shadow);\n'
        '  --btn-primary-bg: var(--navy);\n'
        '  --btn-primary-fg: var(--gold-w);   /* 深蓝底 + 金字：色板自带的 6.89:1 组合 */\n'
        '  --btn-primary-glow: 0 4px 14px rgba(14,42,71,0.22);\n'
        '  --btn-primary-glow-h: 0 6px 18px rgba(14,42,71,0.28);\n'
        '  --selection: rgba(184,147,90,0.22);\n'
        '  --shadow: 0 2px 8px rgba(14,42,71,0.08);\n'
        '\n'
        '  font-family: var(--sans);\n'
        '}\n'
        '\n'
        '/* 标题：宋体体系（机构感）。只换字族，不动字号/字重/间距 —— 避免版面位移。 */\n'
        '[data-theme="light"] #tab-quant .card h3,\n'
        '[data-theme="light"] #tab-quant .expander-header { font-family: var(--serif); }\n'
        '\n'
        '/* 数字：Georgia 数字栈（表格数值 / 指标值对齐更稳） */\n'
        '[data-theme="light"] #tab-quant table td { font-family: var(--num); }\n'
        '\n'
        '/* badge-info：亮蓝(白底仅 2.4:1) → 深蓝族，与色板「--navy2 用于标签文字」一致 */\n'
        '[data-theme="light"] #tab-quant .badge-info { background: rgba(27,69,113,0.10); color: var(--navy2); }'
    )
    quant_scope_new = (
        '/* ============================================================\n'
        '   深蓝金 · 全局规则（2026-10-13 由 #tab-quant 作用域推广到全站亮色）\n'
        '   ============================================================\n'
        '   2026-10-12 深蓝金只作用于 [data-theme="light"] #tab-quant；10-13 按用户要求\n'
        '   推广到全部页签 × 亮色主题。原 #tab-quant 令牌作用域块已删除 —— 里面的通用令牌\n'
        '   重映射（--brand/--bg/--border/--btn-primary-*/--thead-bg 等约 40 条）全部上移进\n'
        '   上面的 [data-theme="light"] 全局块；这里只保留"选择器表达不了令牌"的几条规则。\n'
        '   暗色主题仍完全不进这些规则（选择器一律带 [data-theme="light"] 限定）。\n'
        '   选色纪律（来自色板自带对比度）：\n'
        '     · 强调**文字**用 --gold-d(5.01:1)；--gold(2.85:1) 只准做线条/边框，绝不作文字\n'
        '     · 主按钮用「实心深蓝底 + --gold-w 金字」这组 6.89:1 的官方搭配\n'
        '   ============================================================ */\n'
        '\n'
        '/* 标题：宋体体系（机构感）。只换字族，不动字号/字重/间距 —— 避免版面位移。 */\n'
        '[data-theme="light"] .card h3,\n'
        '[data-theme="light"] .expander-header { font-family: var(--serif); }\n'
        '\n'
        '/* 数字：Georgia 数字栈（表格数值 / 指标值对齐更稳） */\n'
        '[data-theme="light"] table td { font-family: var(--num); }\n'
        '\n'
        '/* 正文：无衬线栈（原 #tab-quant 内的 font-family: var(--sans) 上移为全局亮色） */\n'
        '[data-theme="light"] body { font-family: var(--sans); }\n'
        '\n'
        '/* badge-info：亮蓝(白底仅 2.4:1) → 深蓝族，与色板「--navy2 用于标签文字」一致 */\n'
        '[data-theme="light"] .badge-info { background: rgba(27,69,113,0.10); color: var(--navy2); }'
    )
    text = apply(text, quant_scope_old, quant_scope_new, 1, 'E13 quant 作用域块')

    # ════════════════════════════════════════════════════════════════
    # #147 signal-banner 亮色遗留蓝渐变 → 深蓝金冷白
    # ════════════════════════════════════════════════════════════════
    text = apply(text,
        '[data-theme="light"] .signal-banner {\n'
        '  background: linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%);\n'
        '  box-shadow: none;   /* 亮色主题不加内发光,保持改造前观感 */\n'
        '}',
        '[data-theme="light"] .signal-banner {\n'
        '  background: linear-gradient(135deg, #f5f7fa 0%, #eef2f7 100%);   /* 冷白 → 极淡蓝灰(深蓝金底色族) */\n'
        '  box-shadow: none;   /* 亮色主题不加内发光,保持改造前观感 */\n'
        '}',
        1, 'E14 signal-banner')

    # ════════════════════════════════════════════════════════════════
    # #144 _baseChartOptions 去 isDark 分支
    # ════════════════════════════════════════════════════════════════
    text = apply(text,
        '  _baseChartOptions(title) {\n'
        "    const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';\n"
        '    return {\n'
        "      backgroundColor: 'transparent',\n"
        '      // 标题置顶居中(top:4);legend 显式下移到标题下方(top:30, 居中)。\n'
        '      // 若不显式指定 legend.top,ECharts 会把它放在与标题同一行(y:0)且居中,与标题文字重叠。\n'
        '      // grid.top 预留 56px 容纳「标题 + 图例」两行,避免绘图区与文字标识重叠。\n'
        "      title: { text: title, left: 'center', top: 4, textStyle: { color: isDark ? CHART.axis : '#666', fontSize: 12 } },\n"
        "      tooltip: { trigger: 'axis' },\n"
        '      grid: { left: 50, right: 20, top: 56, bottom: 30 },\n'
        "      xAxis: { type: 'category', axisLine: { lineStyle: { color: isDark ? CHART.line : '#ddd' } }, axisLabel: { color: isDark ? '#666' : '#999' } },\n"
        "      yAxis: { type: 'value', axisLine: { lineStyle: { color: isDark ? CHART.line : '#ddd' } }, axisLabel: { color: isDark ? '#666' : '#999' }, splitLine: { lineStyle: { color: isDark ? CHART.grid : '#eee' } } },\n"
        "      legend: { top: 30, left: 'center', textStyle: { color: isDark ? CHART.axis : '#666' } }\n"
        '    };\n'
        '  },',
        '  _baseChartOptions(title) {\n'
        '    // 【2026-10-13】去掉 isDark 分支: 图表配色统一读 CHART getter(渲染那一刻取当前\n'
        '    //   主题的 --chart-* 令牌), 深蓝金/糖果紫都能正确进图, 不再按主题硬编码兜底色。\n'
        '    return {\n'
        "      backgroundColor: 'transparent',\n"
        '      // 标题置顶居中(top:4);legend 显式下移到标题下方(top:30, 居中)。\n'
        '      // 若不显式指定 legend.top,ECharts 会把它放在与标题同一行(y:0)且居中,与标题文字重叠。\n'
        '      // grid.top 预留 56px 容纳「标题 + 图例」两行,避免绘图区与文字标识重叠。\n'
        "      title: { text: title, left: 'center', top: 4, textStyle: { color: CHART.axis, fontSize: 12 } },\n"
        "      tooltip: { trigger: 'axis' },\n"
        '      grid: { left: 50, right: 20, top: 56, bottom: 30 },\n'
        "      xAxis: { type: 'category', axisLine: { lineStyle: { color: CHART.line } }, axisLabel: { color: CHART.axis } },\n"
        "      yAxis: { type: 'value', axisLine: { lineStyle: { color: CHART.line } }, axisLabel: { color: CHART.axis }, splitLine: { lineStyle: { color: CHART.grid } } },\n"
        "      legend: { top: 30, left: 'center', textStyle: { color: CHART.axis } }\n"
        '    };\n'
        '  },',
        1, 'E15 _baseChartOptions')

    # ════════════════════════════════════════════════════════════════
    # #144 十处三行样板 → 直接读 CHART getter
    # ════════════════════════════════════════════════════════════════
    text, n_sub = re.subn(
        r"^[ \t]*const isDark = \(document\.documentElement\.getAttribute\('data-theme'\) \|\| 'dark'\) === 'dark';\n"
        r"([ \t]*)const textColor = isDark \? CHART\.axis : '#666';\n"
        r"[ \t]*const gridColor = isDark \? CHART\.grid : '#eee';",
        lambda m: f"{m.group(1)}const textColor = CHART.axis;\n{m.group(1)}const gridColor = CHART.grid;",
        text, flags=re.M)
    if n_sub != 10:
        die(f"E16 三行样板替换 {n_sub} 处，预期 10 —— 拒绝写盘")

    # E17 _overlayChartError
    text = apply(text,
        "    const c = this._initChart('chart-overlay');\n"
        "    const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';\n"
        '    if (c) c.setOption({',
        "    const c = this._initChart('chart-overlay');\n"
        '    if (c) c.setOption({',
        1, 'E17a overlay 去 isDark')
    text = apply(text,
        "          fill: isDark ? CHART.axis : '#666',",
        '          fill: CHART.axis,',
        1, 'E17b overlay fill')

    # E18 _renderFundCycle（isDark 声明后未使用，直接删）
    text = apply(text,
        "    if (!cyc.ok) return this._emptyState(cyc.error || '无法判定库存周期(数据不足)');\n"
        "    const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';\n"
        '    // 阶段语义色',
        "    if (!cyc.ok) return this._emptyState(cyc.error || '无法判定库存周期(数据不足)');\n"
        '    // 阶段语义色',
        1, 'E18 fundCycle 去 isDark')

    # E19 关系图
    text = apply(text,
        "      const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';\n"
        "      const TYPE_COLOR = { platform: CHART.brand2, variety: '#4a9eff', sector: '#59c285' };",
        "      const TYPE_COLOR = { platform: CHART.brand2, variety: '#4a9eff', sector: '#59c285' };",
        1, 'E19a 关系图去 isDark')

    # E19b 桑基图
    text = apply(text,
        "      const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';\n"
        "      const DIR_COLOR = { '看多': '#59c285', '看空': '#e05252', '中性': '#8a8a8a' };",
        "      const DIR_COLOR = { '看多': '#59c285', '看空': '#e05252', '中性': '#8a8a8a' };",
        1, 'E19b 桑基图去 isDark')

    # E20 两处标题 textStyle 三元（E15 已清掉 _baseChartOptions 里的两处）
    text = apply(text,
        "textStyle: { color: isDark ? CHART.axis : '#666', fontSize: 12 } },",
        'textStyle: { color: CHART.axis, fontSize: 12 } },',
        2, 'E20 标题 textStyle')
    text = apply(text,
        "legend: { top: 30, left: 'center', textStyle: { color: isDark ? CHART.axis : '#666' } },",
        "legend: { top: 30, left: 'center', textStyle: { color: CHART.axis } },",
        1, 'E20b 图例 textStyle')

    # E21 桑基 label 三元
    text = apply(text,
        "          label: { color: isDark ? '#aaa' : '#666', fontSize: 11 },",
        '          label: { color: CHART.axis, fontSize: 11 },',
        1, 'E21 桑基 label')

    # E22 利润图 gridColor
    text = apply(text,
        "        const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';\n"
        "        const gridColor = isDark ? CHART.grid : '#eee';",
        '        const gridColor = CHART.grid;',
        1, 'E22 利润图 gridColor')

    # ════════════════════════════════════════════════════════════════
    # #149 切主题图表重染（通用，不重取数）
    # ════════════════════════════════════════════════════════════════

    # E23 CHART 色键清单（模块级，紧跟 CHART 对象）
    text = apply(text,
        "  get up()        { return __cssVar('--red', '#ef4444'); },              // 涨 / 正向\n"
        "  get down()      { return __cssVar('--green', '#22c55e'); },            // 跌 / 负向\n"
        '};',
        "  get up()        { return __cssVar('--red', '#ef4444'); },              // 涨 / 正向\n"
        "  get down()      { return __cssVar('--green', '#22c55e'); },            // 跌 / 负向\n"
        '};\n'
        '\n'
        '// _repaintCharts 用到的图表色键(与 CHART getter 一一对应; kFont 是字体栈, 也参与新旧对照)\n'
        "const CHART_COLOR_KEYS = ['brand','brand2','brandGlow','brandA25','brandA02','axis','grid','line',\n"
        "  'candy1','candy2','candy3','candy4',\n"
        "  'kUp','kDown','kMa1','kMa2','kMa3','kMa4','kBollMid','kBollSide','kVol','kAxis','kGrid','kCross','kYlab','kFont',\n"
        "  'up','down'];",
        1, 'E23 CHART_COLOR_KEYS')

    # E24 状态字段
    text = apply(text,
        '  _chartInstances: {},  // 【变量】ECharts 实例注册表 {容器id: 实例}, 供统一 resize/销毁',
        '  _chartInstances: {},  // 【变量】ECharts 实例注册表 {容器id: 实例}, 供统一 resize/销毁\n'
        '  _chartOpts: {},       // 【变量】每图表最后一次 setOption 的 option 快照 {容器id: {option, notMerge}}, 供切主题重染',
        1, 'E24 _chartOpts 状态字段')

    # E25 _initChart 包 setOption 记录快照
    text = apply(text,
        '  _initChart(containerId) {\n'
        '    const el = document.getElementById(containerId);\n'
        "    if (!el) { console.warn('Chart container not found:', containerId); return null; }\n"
        '    // Ensure container has dimensions\n'
        "    if (el.clientHeight === 0) { el.style.height = '320px'; }\n"
        '    if (this._chartInstances[containerId]) {\n'
        "      try { this._chartInstances[containerId].dispose(); } catch(e) {}\n"
        '      delete this._chartInstances[containerId];\n'
        '    }\n'
        '    const chart = echarts.init(el);  // 【调用函数】ECharts: 创建实例并登记到 _chartInstances(供 resize/销毁)\n'
        '    this._chartInstances[containerId] = chart;\n'
        '    return chart;\n'
        '  },',
        '  _initChart(containerId) {\n'
        '    const el = document.getElementById(containerId);\n'
        "    if (!el) { console.warn('Chart container not found:', containerId); return null; }\n"
        '    // Ensure container has dimensions\n'
        "    if (el.clientHeight === 0) { el.style.height = '320px'; }\n"
        '    if (this._chartInstances[containerId]) {\n'
        "      try { this._chartInstances[containerId].dispose(); } catch(e) {}\n"
        '      delete this._chartInstances[containerId];\n'
        '      delete this._chartOpts[containerId];\n'
        '    }\n'
        '    const chart = echarts.init(el);  // 【调用函数】ECharts: 创建实例并登记到 _chartInstances(供 resize/销毁)\n'
        '    // 【2026-10-13】包一层 setOption: 记录本图最后一次 option 快照, 供切主题重染(_repaintCharts)。\n'
        '    //   为什么不在 toggleTheme 里重跑各 loadXxx(): 那会重新请求后端, 弱网/接口失败时\n'
        '    //   切主题反而把图表打空; 重染只换色值, 不动数据。\n'
        '    const rec = { option: null, notMerge: false };\n'
        '    const rawSetOption = chart.setOption.bind(chart);\n'
        '    chart.setOption = function(option, notMerge, lazyUpdate) {\n'
        '      rec.option = option; rec.notMerge = !!notMerge;\n'
        '      return rawSetOption(option, notMerge, lazyUpdate);\n'
        '    };\n'
        '    this._chartOpts[containerId] = rec;\n'
        '    this._chartInstances[containerId] = chart;\n'
        '    return chart;\n'
        '  },',
        1, 'E25 _initChart 记录快照')

    # E26 _resizeAllCharts 后追加 _snapshotChartColors / _repaintCharts
    text = apply(text,
        '  // 【功能】遍历所有已创建的图表实例调用 resize(), 使图表在容器尺寸变化(窗口缩放/主题切换/切页)时自适应\n'
        '  // 【输入】无 【渲染】所有活动图表 【后端】无\n'
        '  _resizeAllCharts() {\n'
        "    Object.values(this._chartInstances).forEach(c => { try { c.resize(); } catch(e){} });\n"
        '  },',
        '  // 【功能】遍历所有已创建的图表实例调用 resize(), 使图表在容器尺寸变化(窗口缩放/主题切换/切页)时自适应\n'
        '  // 【输入】无 【渲染】所有活动图表 【后端】无\n'
        '  _resizeAllCharts() {\n'
        "    Object.values(this._chartInstances).forEach(c => { try { c.resize(); } catch(e){} });\n"
        '  },\n'
        '\n'
        '  // 【功能】快照当前主题下 CHART 的各色值(切主题**前**调用: 切后 getter 读到的是新主题值, 旧值就取不到了)\n'
        '  // 【输入】无 【渲染】无 【后端】无\n'
        '  _snapshotChartColors() {\n'
        '    const snap = {};\n'
        '    CHART_COLOR_KEYS.forEach(k => { snap[k] = CHART[k]; });\n'
        '    return snap;\n'
        '  },\n'
        '\n'
        '  // 【功能】切主题后重染所有已渲染图表: 把 _initChart 记录的 option 里"旧主题色值"替换为\n'
        '  //        "新主题色值"再重放 setOption —— 不重新请求后端数据。\n'
        '  // 【为什么】ECharts 画在 canvas 上, 读不到 CSS 变量, 颜色在渲染那一刻就固化了;\n'
        '  //   只 resize 不换色(2026-10-12 踩过: 切到亮色 K 线仍是暗色红绿, 当时只特判了 K 线)。\n'
        '  // 【输入】before: 切主题前的 CHART 色快照\n'
        '  // 【做法】深遍历 option(跳过函数/防环), 只对**严格相等**的字符串替换 —— 业务数据\n'
        '  //   (品种名/数值/日期)不可能恰好等于这些色值, 绝不误伤。\n'
        '  // 【边界】只记录"最后一次 setOption"; 若某图靠多次局部 setOption 叠加而成, 重染可能\n'
        '  //   只覆盖局部(降级可接受: 下次完整渲染即恢复)。绕开 _initChart 自建的图\n'
        '  //   (如 chart-trading-pnl)不登记快照, 不在重染范围。\n'
        '  _repaintCharts(before) {\n'
        '    const map = {};\n'
        '    let changed = 0;\n'
        '    Object.keys(before || {}).forEach(k => {\n'
        '      const b = before[k], a = CHART[k];\n'
        '      if (typeof b === \'string\' && b && b !== a) { map[b] = a; changed++; }\n'
        '    });\n'
        '    if (!changed) return;\n'
        '    const swap = v => (Object.prototype.hasOwnProperty.call(map, v) ? map[v] : v);\n'
        '    const seen = new WeakSet();\n'
        '    const walk = node => {\n'
        "      if (!node || typeof node !== 'object' || seen.has(node)) return node;\n"
        '      seen.add(node);\n'
        '      if (Array.isArray(node)) {\n'
        '        for (let i = 0; i < node.length; i++) {\n'
        '          const v = node[i];\n'
        "          if (typeof v === 'function') continue;\n"
        "          if (typeof v === 'string') node[i] = swap(v);\n"
        '          else walk(v);\n'
        '        }\n'
        '        return node;\n'
        '      }\n'
        '      Object.keys(node).forEach(key => {\n'
        '        const v = node[key];\n'
        "        if (typeof v === 'function') return;   // 格式化器/回调原样保留\n"
        "        if (typeof v === 'string') node[key] = swap(v);\n"
        "        else if (v && typeof v === 'object') walk(v);\n"
        '      });\n'
        '      return node;\n'
        '    };\n'
        "    Object.keys(this._chartOpts || {}).forEach(id => {\n"
        '      const rec = this._chartOpts[id];\n'
        '      const chart = this._chartInstances[id];\n'
        '      if (!rec || !rec.option || !chart) return;\n'
        '      try {\n'
        '        walk(rec.option);\n'
        '        chart.setOption(rec.option, rec.notMerge);\n'
        "      } catch (e) { /* 重染失败不影响使用(下次完整渲染自然恢复) */ }\n"
        '    });\n'
        '  },',
        1, 'E26 重染方法')

    # E27 toggleTheme: 特判 K 线 → 通用重染
    text = apply(text,
        '  toggleTheme() {\n'
        "    const current = document.documentElement.getAttribute('data-theme') || 'dark';\n"
        "    const next = current === 'dark' ? 'light' : 'dark';\n"
        '    document.documentElement.setAttribute(\'data-theme\', next);\n'
        "    localStorage.setItem('agentsense-theme', next);\n"
        '    this._updateThemeUI(next);\n'
        '    this._resizeAllCharts();\n'
        '    // 【2026-10-12】图表走 canvas，读不到 CSS 变量，颜色在"渲染那一刻"就固化了 ——\n'
        '    //   只 resize 不会重新取色。用缓存的上次响应重渲染 K 线，让深蓝金/糖果紫即时切换。\n'
        "    if (this._currentTab === 'quant' && this._lastKlineData) {\n"
        '      try { this._renderQuantKline(this._lastKlineData); } catch (e) { /* 失败不影响切主题 */ }\n'
        '    }\n'
        '  },',
        '  toggleTheme() {\n'
        "    const current = document.documentElement.getAttribute('data-theme') || 'dark';\n"
        "    const next = current === 'dark' ? 'light' : 'dark';\n"
        '    // 【2026-10-13】切之前先快照图表色: 切之后 CHART getter 读到的是新主题值, 旧值取不到\n'
        '    const before = this._snapshotChartColors();\n'
        '    document.documentElement.setAttribute(\'data-theme\', next);\n'
        "    localStorage.setItem('agentsense-theme', next);\n"
        '    this._updateThemeUI(next);\n'
        '    this._resizeAllCharts();\n'
        '    // 【2026-10-13】图表走 canvas，读不到 CSS 变量，颜色在"渲染那一刻"就固化了 ——\n'
        '    //   只 resize 不会重新取色。用 _initChart 记录的 option 快照做"旧色→新色"重染，\n'
        '    //   全站图表(不再只有 K 线)即时换肤，且不重新请求后端。\n'
        '    this._repaintCharts(before);\n'
        '  },',
        1, 'E27 toggleTheme')

    # ════════════════════════════════════════════════════════════════
    # 收尾断言
    # ════════════════════════════════════════════════════════════════
    post = {
        '无 const isDark': ('const isDark', 0),
        '无 isDark 三元': ('isDark ?', 0),
        'quant 作用域已删': ('[data-theme="light"] #tab-quant {', 0),
        '无 CHART 字面量': ("'CHART.up'", 0),
        '糖果紫品牌色已退场': ('--brand: #5b5bd6;', 0),
        '旧信号横幅已退场': ('#f0f4ff', 0),
        '重染钩子已挂': ('this._repaintCharts(before);', 1),
        '快照调用点已挂': ('this._snapshotChartColors();', 1),
        '快照方法已定义': ('_snapshotChartColors() {', 1),
    }
    for tag, (needle, expect) in post.items():
        n = text.count(needle)
        if n != expect:
            die(f"收尾断言失败 {tag}: 命中 {n}，预期 {expect}")
    print('[OK] 收尾断言全部通过')

    # ── 写盘：\n → \r\n，自检 bareLF == 0 ──
    out = text.replace('\n', '\r\n')
    bare_after = len(re.findall(r"(?<!\r)\n", out))
    if bare_after != 0:
        die(f"写盘前自检失败: bareLF={bare_after}")
    PATH.write_bytes(out.encode('utf-8'))
    print(f"[OK] 已写盘 {PATH}（{len(out)} 字节, bareLF=0, CRLF={out.count(chr(13)+chr(10))}）")
    print('[DONE] 深蓝金推广补丁应用完成')


if __name__ == '__main__':
    main()
