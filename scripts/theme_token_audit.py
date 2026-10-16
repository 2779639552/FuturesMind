# -*- coding: utf-8 -*-
"""主题令牌审计 —— 守「亮色主题不被深色皮肤污染」这条契约。

背景(2026-09-15 皮肤重构):本页用 CSS 变量做主题,`[data-theme="light"]` 只覆盖
一部分令牌。**只要在 :root 里新增一个颜色令牌而忘了在亮色块里给值,亮色主题就会
继承深色值** —— 典型症状:--brand 变成冰蓝白,白底上的白字让整站强调色"消失"。
这种故障在深色主题下完全看不出来,只有切到亮色才暴露,靠人工点检很容易漏。
本脚本把这条契约变成可自动检查的规则。

三组检查:
  ① 漏定义 —— 被 var() 或 __cssVar() 引用了、但两个块都没定义的令牌。
     后果:该条 CSS 声明整条失效(不是回退成别色,而是直接作废)。
  ② 亮色契约 —— :root 有、[data-theme="light"] 没有的令牌。逐条列出以便人工确认
     是不是"刻意的主题无关令牌"(语义色/圆角/过渡时长/指向别的令牌的别名)。
  ③ 死令牌 —— 定义了但全站无人引用的令牌(代码腐化信号)。

用法:  venv/Scripts/python.exe scripts/theme_token_audit.py
退出码: 0 = 无"漏定义";1 = 存在"漏定义"(必须修)。②③ 只报告不拦。
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 页面模板路径:默认取项目根下的 web_template.html,可用环境变量覆盖(便于测试)
PATH = os.environ.get(
    "THEME_TEMPLATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html"),
)

# 刻意主题无关的令牌白名单:亮色块不给值是正确的,不算违约。
#   - 平台/提醒色(--amber/--blue/--teal/--purple):两主题共用同一套装饰色
#   - 几何与动效令牌:圆角/过渡时长与主题无关
#   - --bg-elev/--bg-subtle:是指向别的令牌的别名,本身就会跟着主题解析
#   - 字体栈(--serif/--sans/--num):字族与主题无关,深浅共用同一套
# 【2026-10-12 变更】--red/--green/--red-bg/--green-bg 已**移出**本白名单:
#   深蓝金改造采纳了新涨跌色(#c8102e/#0a6e4a,白底 AA 合规),这两个色
#   现在**两主题各有值**(深色沿用旧亮色),不再是"主题无关";
#   同时 JS 侧 51 处硬编码已收敛为 CHART.up/CHART.down 读令牌。
THEME_AGNOSTIC_OK = {
    "--amber", "--amber-bg",
    "--blue", "--teal", "--purple",
    "--radius", "--radius-sm", "--radius-lg", "--transition",
    "--bg-elev", "--bg-subtle",
    "--serif", "--sans", "--num",
}


def css_block(css: str, pattern: str) -> str:
    """取出 pattern 命中的规则块内容(按花括号配平,支持嵌套的多行值)。"""
    m = re.search(pattern, css, re.M)
    if not m:
        return ""
    i = css.index("{", m.start())
    depth = j = 0
    j = i
    while j < len(css):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i + 1: j]
        j += 1
    return ""


def main() -> int:
    src = io.open(PATH, encoding="utf-8").read()
    css = src[src.index("<style>") + 7: src.index("</style>")]

    dark = css_block(css, r"^:root\s*\{")
    # 亮色块必须锚定在"紧跟在 [data-theme=light] 后的那个 {",否则会误取
    # [data-theme="light"] .signal-banner 这类后代规则
    light = css_block(css, r'\[data-theme="light"\]\s*\{[^}]*\}')

    def_dark = set(re.findall(r"(--[\w-]+)\s*:", dark))
    def_light = set(re.findall(r"(--[\w-]+)\s*:", light))

    used = {}
    for m in re.finditer(r"var\((--[\w-]+)", src):          # CSS 侧
        used[m.group(1)] = used.get(m.group(1), 0) + 1
    for m in re.finditer(r"__cssVar\(\s*'(--[\w-]+)'", src):  # JS 侧(图表)
        used[m.group(1)] = used.get(m.group(1), 0) + 1

    print(f"模板: {PATH}")
    print(f"深色 :root 定义 {len(def_dark)} 个令牌 | 亮色块定义 {len(def_light)} 个 | "
          f"被引用 {len(used)} 个\n")

    undefined = [t for t in sorted(used) if t not in def_dark and t not in def_light]
    missing = [t for t in sorted(def_dark) if t not in def_light]
    dead = [t for t in sorted(def_dark) if t not in used]

    print("①[漏定义] 被引用但两个块都没定义:")
    if undefined:
        for t in undefined:
            print(f"   ！ {t} (×{used[t]})")
    else:
        print("   无")

    print('\n②[亮色契约] :root 有、[data-theme="light"] 没有 → 亮色会继承深色值:')
    suspicious = []
    for t in missing:
        v = re.search(re.escape(t) + r"\s*:\s*([^;]+);", dark)
        val = v.group(1).strip()[:58] if v else "?"
        if t in THEME_AGNOSTIC_OK:
            print(f"   ok(刻意主题无关) {t:<16} = {val}")
        else:
            print(f"   ！ 需确认        {t:<16} = {val}")
            suspicious.append(t)

    print("\n③[死令牌] 定义了但全站无人引用:")
    print("   " + (", ".join(dead) if dead else "无"))

    print(f"\n结论: 漏定义 {len(undefined)} 个 | 亮色待确认 {len(suspicious)} 个 | 死令牌 {len(dead)} 个")
    return 1 if undefined else 0


if __name__ == "__main__":
    sys.exit(main())
