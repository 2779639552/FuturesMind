# -*- coding: utf-8 -*-
r"""隐藏「情绪数据」页分析工具下的 5 个子模块 + 数据更新里的「更新日志」。

用户要求（2026-10-13）：
  隐藏 情绪数据模块下「分析工具」中的 —— 作者 / 框架说明 / Agent验证 / Agent回测 / 图谱
  （"等小模块"），以及「更新日志」部分。

保留：分析工具下的「综合分析」「事件」两个子模块。

★ 为什么是「真删 HTML」而不是 display:none
  `_secScan`（小模块索引引擎）**按 DOM 结构扫描，不看可见性** —— 每个 `.analysis-sub`
  都会被收成一条「分析工具 · xxx」索引项。只设 display:none 会留下一堆点了没反应的
  空索引项（本项目 09-30 实测过同类症状："筛选后页面上仍挂着空标题"）。
  ⇒ 必须连同子导航按钮一起删掉（按钮文本是索引项名的来源）。

★ 为什么「更新日志」要留一个隐藏载体
  `runDataUpdate()` 直接 `log.textContent = ''` / `log.innerHTML += ...`（**非 null-safe**）。
  删掉 DOM 后 log 为 null ⇒ 抛 TypeError ⇒「一键更新」永久卡在禁用态。
  故保留一个不可见的 `#update-log`（裸 div，不含 .card ⇒ 不进小模块索引）。

用法: venv/Scripts/python.exe probe/_trim_sentiment_modules.py [--apply]
  不加 --apply 只做 dry-run（只校验锚点，不写文件）。
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMPL = ROOT / "web_template.html"
BAK = "backup/web_template_pre_sentiment_trim_20261013_1528.html"

APPLY = "--apply" in sys.argv

# ── 待删区块（用 start/end 锚点区间切割，比逐行删更抗漂移）────────────────
# 4 个面板一次性删：Authors / Framework / Agent Validation / Agent Backtest
PANELS_4_START = "  <!-- Authors -->\n"
PANELS_4_END = "  <!-- Events -->\n"
# Graph 面板
GRAPH_START = "  <!-- Graph & Sankey (非标数据可视化) -->\n"
GRAPH_END = "  <!-- ============ 数据更新"

NOTE_4 = """  <!-- 【2026-10-13 用户要求】分析工具原「作者影响力 / 框架说明 / Agent验证 / Agent回测」
       4 个子模块连同子导航按钮一并移除。连同下方的「图谱」，分析工具由 7 个子模块
       精简为 2 个：综合分析、事件。
       备份: backup/web_template_pre_sentiment_trim_20261013_1528.html -->
"""

NOTE_GRAPH = """  <!-- 【2026-10-13】原「图谱」子模块(品种-平台-板块关系图 + 平台→品种→情绪流量 Sankey)
       已移除，同属分析工具 7→2 的子模块精简。 -->
"""

# ── 逐行删除：5 个子导航按钮 ────────────────────────────────────────────
BTNS = [
    '    <button class="btn btn-outline btn-sm" data-asub="authors" onclick="App.switchAnalysisSub(\'authors\')">作者</button>\n',
    '    <button class="btn btn-outline btn-sm" data-asub="framework" onclick="App.switchAnalysisSub(\'framework\')">框架说明</button>\n',
    '    <button class="btn btn-outline btn-sm" data-asub="agentval" onclick="App.switchAnalysisSub(\'agentval\')" style="border-color:var(--green);font-weight:700">Agent验证</button>\n',
    '    <button class="btn btn-outline btn-sm" data-asub="agentbt" onclick="App.switchAnalysisSub(\'agentbt\')" style="border-color:var(--brand)">Agent回测</button>\n',
    '    <button class="btn btn-outline btn-sm" data-asub="graph" onclick="App.switchAnalysisSub(\'graph\')" style="border-color:var(--green)">图谱</button>\n',
]

# ── 更新日志卡 → 隐藏载体 ───────────────────────────────────────────────
LOG_OLD = ('  <div class="card" style="margin-top:16px"><h3>更新日志</h3>'
           '<div class="progress-log" id="update-log" style="min-height:280px"></div></div>\n')
LOG_NEW = """  <!-- 【2026-10-13 用户要求】「更新日志」UI 已隐藏。
       ⚠️ 必须保留下面的 #update-log 载体：runDataUpdate() 会 `log.textContent=''` 与
       `log.innerHTML += ...`（非 null-safe），删掉即抛 TypeError，导致「一键更新」
       永久卡在禁用态。此容器是裸 div（不含 .card）⇒ 不进小模块索引。
       恢复做法：把 style 去掉，外面包回 <div class="card"><h3>更新日志</h3>…。</div> -->
  <div class="progress-log" id="update-log" style="display:none"></div>
"""

# ── 注释同步 ────────────────────────────────────────────────────────────
CMT_BLOCK_OLD = """  <!-- ============ 分析工具 (2026-09-07 由独立 tab-analysis 并入本页) ============
       做什么: 情绪数据衍生分析工具, 子导航切换 7 个子模块:
       综合分析(异常检测/分歧度/领先滞后/排名/对比)、作者影响力、事件提取、
       图谱、框架说明(架构介绍)、Agent验证、Agent回测。 -->
"""
CMT_BLOCK_NEW = """  <!-- ============ 分析工具 (2026-09-07 由独立 tab-analysis 并入本页) ============
       做什么: 情绪数据衍生分析工具, 子导航切换 **2 个**子模块:
       综合分析(异常检测/分歧度/领先滞后/排名/对比)、事件提取。
       【2026-10-13 用户要求精简】原 7 个: 另有「作者影响力 / 框架说明 / 图谱 /
       Agent验证 / Agent回测」5 个, 已连同子导航按钮整块移除(见下方面板位置的原位说明)。 -->
"""

CMT_AUTHOR_LINE_OLD = "      <b>作者</b>：影响力 = 发帖量×0.3 + log(粉丝)×0.3 + log(互动)×0.4。<br>\n"
CMT_AUTHOR_LINE_NEW = ""

CMT_SWITCH_OLD = """  // 【功能】分析工具子区的子导航切换(综合分析/作者/事件/框架说明/Agent验证/Agent回测/图谱),
  //         切换后按需触发对应子模块的数据加载
  // 【输入】sub: 子模块标识, 如 'comprehensive' / 'agentval'
  // 【渲染】显示对应 .analysis-sub 面板并高亮子导航按钮
  // 【后端】视子模块而定 (comprehensive→5 个分析接口; authors→/api/analysis/authors; events→/api/analysis/events)
"""
CMT_SWITCH_NEW = """  // 【功能】分析工具子区的子导航切换(综合分析/事件 —— 2026-10-13 由 7 个精简为 2 个),
  //         切换后按需触发对应子模块的数据加载
  // 【输入】sub: 子模块标识, 'comprehensive' / 'events'
  // 【渲染】显示对应 .analysis-sub 面板并高亮子导航按钮
  // 【后端】comprehensive→5 个分析接口; events→/api/analysis/events
  //   ⚠️ 已移除子模块的 load 调用(authors/graph)一并删除, 避免留下永远打不到的死分支。
"""

DEAD_1 = "    if (sub === 'authors') this.loadAuthors();\n"
DEAD_2 = "    if (sub === 'graph') { this.loadGraphChart(); this.loadSankeyChart(); }\n"

HEADER_OLD = """         tab-sentiment 情绪数据   —— 情绪得分/价格走势叠加图 + 采集帖子 + 情绪信号回测 +
                                    分析工具(2026-09-07 并入) + 数据更新(2026-09-08 并入)
"""
HEADER_NEW = """         tab-sentiment 情绪数据   —— 情绪得分/价格走势叠加图 + 采集帖子 + 情绪信号回测 +
                                    分析工具(2026-09-07 并入; 2026-10-13 子模块 7→2) +
                                    数据更新(2026-09-08 并入; 更新日志 2026-10-13 隐藏)
"""

VER_OLD = "  _SEC_IDX_VER: '23',\n"
VER_NEW = """  //   v24 (2026-10-13): 情绪页分析工具由 7 个子模块精简为 2 个(删除 作者/框架说明/
  //                     Agent验证/Agent回测/图谱 的按钮+面板), 并隐藏数据更新页的「更新日志」。
  //                     索引条目数变化 ⇒ 必须重建, 否则旧浏览器残留已删模块的僵尸索引项。
  _SEC_IDX_VER: '24',
"""


def cut(src: str, start: str, end: str, repl: str, tag: str, problems: list):
    """用 start/end 锚点切出区间并替换；断言两端锚点各命中 1 次、且 start 在 end 之前。"""
    n_s, n_e = src.count(start), src.count(end)
    if n_s != 1 or n_e != 1:
        problems.append(f"{tag}: 锚点命中 start={n_s} end={n_e} (各需 1)")
        return src
    i, j = src.index(start), src.index(end)
    if i >= j:
        problems.append(f"{tag}: start 在 end 之后")
        return src
    seg = src[i:j]
    # 校验区间自身 div 配平（删的是完整块）
    if seg.count("<div") != seg.count("</div>"):
        problems.append(f"{tag}: 区间内 div 不配平 <div={seg.count('<div')} </div={seg.count('</div>')}")
        return src
    return src[:i] + repl + src[j:]


def rep(src: str, old: str, new: str, tag: str, problems: list, expect: int = 1):
    n = src.count(old)
    if n != expect:
        problems.append(f"{tag}: 命中 {n} 次 (预期 {expect})")
        return src
    return src.replace(old, new)


def main() -> int:
    raw = TMPL.read_bytes()
    if raw.count(b"\r\n") == 0:
        print("✘ 文件不是 CRLF，中止（避免写坏行尾）")
        return 2
    text = raw.decode("utf-8")
    src = text.replace("\r\n", "\n")

    problems: list = []
    n_div_before = src.count("<div")
    n_cdiv_before = src.count("</div>")

    # 1) 4 个面板
    src = cut(src, PANELS_4_START, PANELS_4_END, NOTE_4, "D1 面板×4", problems)
    # 2) graph 面板
    src = cut(src, GRAPH_START, GRAPH_END, NOTE_GRAPH, "D2 图谱面板", problems)
    # 3) 5 个按钮
    for k, b in enumerate(BTNS, 1):
        src = rep(src, b, "", f"B{k} 按钮", problems)
    # 4) 更新日志 → 隐藏载体
    src = rep(src, LOG_OLD, LOG_NEW, "D3 更新日志", problems)
    # 5) 注释同步
    src = rep(src, CMT_BLOCK_OLD, CMT_BLOCK_NEW, "C1 块注释", problems)
    src = rep(src, CMT_AUTHOR_LINE_OLD, CMT_AUTHOR_LINE_NEW, "C2 说明里的作者行", problems)
    src = rep(src, CMT_SWITCH_OLD, CMT_SWITCH_NEW, "C3 切换函数注释", problems)
    src = rep(src, DEAD_1, "", "C4 死分支 authors", problems)
    src = rep(src, DEAD_2, "", "C5 死分支 graph", problems)
    src = rep(src, HEADER_OLD, HEADER_NEW, "C6 文件头", problems)
    src = rep(src, VER_OLD, VER_NEW, "C7 版本号", problems)

    n_div_after = src.count("<div")
    n_cdiv_after = src.count("</div>")

    print(f"div 计数: 前 <div={n_div_before} </div={n_cdiv_before}  |  后 <div={n_div_after} </div={n_cdiv_after}")
    print(f"div 减少: 开 {n_div_before - n_div_after} 个 / 闭 {n_cdiv_before - n_cdiv_after} 个（必须相等）")
    if (n_div_before - n_div_after) != (n_cdiv_before - n_cdiv_after):
        problems.append("div 开闭减少数不相等 ⇒ 删块切坏了结构")

    # 残留检查
    for pat in ("asub-authors", "asub-framework", "asub-agentval", "asub-agentbt", "asub-graph"):
        if pat in src:
            problems.append(f"残留 {pat}")
    for pat in ('data-asub="authors"', 'data-asub="framework"', 'data-asub="agentval"',
                'data-asub="agentbt"', 'data-asub="graph"'):
        if pat in src:
            problems.append(f"残留按钮 {pat}")

    if problems:
        print("\n✘ 校验未通过，未写文件：")
        for p in problems:
            print("   ·", p)
        return 1

    out = src.replace("\n", "\r\n")
    bare = out.count("\n") - out.count("\r\n")
    if bare != 0:
        print(f"✘ 写回前自检: bareLF={bare}（应为 0）")
        return 2

    if not APPLY:
        print("\n✔ dry-run 全部校验通过（未写文件）。加 --apply 落盘。")
        return 0

    shutil.copy2(TMPL, ROOT / BAK)          # 二次保险
    TMPL.write_bytes(out.encode("utf-8"))
    chk = TMPL.read_bytes()
    _crlf = chk.count(b"\r\n")
    _bare = chk.count(b"\n") - _crlf
    print("\n✔ 已写入。字节数 %d → %d（-%d）" % (len(raw), len(chk), len(raw) - len(chk)))
    print("   行尾自检: CRLF=%d bareLF=%d" % (_crlf, _bare))
    return 0


if __name__ == "__main__":
    sys.exit(main())
