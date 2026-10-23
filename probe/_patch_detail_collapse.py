# -*- coding: utf-8 -*-
"""数据仓库「明细数据」改为可折叠(默认收起, 点「查看」才下拉) —— 2026-10-19

改动(web_template.html, CRLF):
 1. 明细数据块 → .expander 结构(#gt-detail-expander, 默认不带 open)
 2. 新增 App.toggleGtjaDetail(force)
 3. showGtjaIndicator(i) → showGtjaIndicator(i, expandDetail); 只有 true 才展开明细
 4. 指标行「查看」按钮传 true; 进页自动渲染处保持不传 ⇒ 默认收起
"""
import io

P = "web_template.html"
src = io.open(P, encoding="utf-8", newline="").read()
assert "\r\n" in src and "\n" not in src.replace("\r\n", ""), "模板不是纯 CRLF, 中止"
before = len(src)


def once(old, new, tag):
    global src
    n = src.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次(应为 1)" % (tag, n)
    src = src.replace(old, new, 1)


# ── 1) HTML: 明细数据 → 折叠栏 ────────────────────────────────────
OLD_HTML = ''' <!-- 明细数据(选中指标的逐日数值) -->
 <div style="margin-top:16px;padding-top:12px;border-top:1px dashed var(--border,#e5e7eb)">
 <div style="font-size:0.9375rem;font-weight:600;color:var(--text-secondary);margin-bottom:8px">
 明细数据 <span class="badge badge-info" id="gt-tbl-badge"></span>
 </div>
 <div id="gt-table" style="max-height:420px;overflow:auto;font-size:0.8125rem">
 <span style="color:var(--text-muted)">点击上方指标的「查看」载入明细。</span>
 </div>
 </div>'''.replace("\n", "\r\n")

NEW_HTML = ''' <!-- 明细数据(选中指标的逐日数值)
 【2026-10-19 用户要求】默认**折叠**, 只有点指标行的「查看」才下拉展开 ——
 原状是一进页面就铺满 115 行(最高 420px), 把下面的研报上传/研报库全挤出首屏。
 ⚠ 结构必须保留 #gt-table(测试与 _renderGtjaTable 都按 id 取), 折叠只靠 .expander
   的 display:none, 不能删 DOM; 也不要用 display:none 直接写在 #gt-table 上
   (那样「展开」得逐个改内联样式, 与全站折叠组件两套机制)。
 ⚠ 默认不能带 open 类: 进页会自动渲染第一个指标(showGtjaIndicator(0)), 若那时也展开,
   就等于"默认展开", 与用户诉求相反 ⇒ 自动渲染不传 expandDetail。 -->
 <div class="expander" id="gt-detail-expander" style="margin-top:16px;border-top:1px dashed var(--border,#e5e7eb);border-bottom:none">
 <div class="expander-header" style="padding:8px 0" onclick="App.toggleGtjaDetail()">
 明细数据 <span class="badge badge-info" id="gt-tbl-badge"></span>
 <span style="font-size:0.75rem;color:var(--text-muted)">点击展开</span><span class="arrow"></span>
 </div>
 <div class="expander-body" style="padding-bottom:12px">
 <div id="gt-table" style="max-height:420px;overflow:auto;font-size:0.8125rem">
 <span style="color:var(--text-muted)">点击上方指标的「查看」载入明细。</span>
 </div>
 </div>
 </div>'''.replace("\n", "\r\n")

once(OLD_HTML, NEW_HTML, "明细数据 HTML")

# ── 2) 指标行「查看」按钮 → 带 expandDetail ────────────────────────
once('<td style="padding:5px 8px"><button class="btn btn-outline btn-sm" onclick="App.showGtjaIndicator(${i})">查看</button></td>',
     '<td style="padding:5px 8px"><button class="btn btn-outline btn-sm" onclick="App.showGtjaIndicator(${i}, true)">查看</button></td>',
     "查看按钮")

# ── 3) showGtjaIndicator 增加参数 + 展开明细 ─────────────────────
once(" // 【功能】把某个指标画成走势图 + 明细表(点击指标行的「查看」)\r\n"
     " // 【输入】i: 指标在 d.indicators 里的下标 【渲染】#gt-chart/#gt-table 【后端】无\r\n",
     " // 【功能】把某个指标画成季节性图 + 明细表(点击指标行的「查看」)\r\n"
     " // 【输入】i: 指标下标; expandDetail: true=同时下拉展开「明细数据」\r\n"
     " // (2026-10-19: 明细默认折叠, 只有用户**主动点「查看」**才展开 —— 进页自动渲染不传此参)\r\n"
     " // 【渲染】#gt-chart/#gt-table/#gt-detail-expander 【后端】无\r\n",
     "showGtjaIndicator 注释")

once(" showGtjaIndicator(i) {\r\n", " showGtjaIndicator(i, expandDetail) {\r\n", "showGtjaIndicator 签名")

once(" this._gtState.activeInd = ind;\r\n this._renderGtjaChart(ind);",
     " this._gtState.activeInd = ind;\r\n"
     " // 主动点「查看」才展开明细(自动渲染不传 ⇒ 保持收起)\r\n"
     " if (expandDetail) this.toggleGtjaDetail(true);\r\n"
     " this._renderGtjaChart(ind);",
     "showGtjaIndicator 展开")

# ── 4) 新增 toggleGtjaDetail(放在 showGtjaIndicator 之后) ─────────
ANCHOR = (" setTimeout(() => this._resizeAllCharts(), 90);\r\n"
          " },\r\n"
          "\r\n"
          " // 【功能】渲染单指标「季节性图」")
once(ANCHOR,
     " setTimeout(() => this._resizeAllCharts(), 90);\r\n"
     " },\r\n"
     "\r\n"
     " // 【功能】展开/收起「明细数据」折叠栏(2026-10-19)\r\n"
     " // 【输入】force: true=展开 / false=收起 / 省略=切换 【渲染】#gt-detail-expander\r\n"
     " // 【返回】展开后的状态(容器不存在 → false, 不抛)\r\n"
     " // 【为什么单独一个方法】表头点击与「查看」按钮两条入口要共用同一状态机;\r\n"
     " // 各写一份 el.classList.toggle 迟早出现\"点了表头状态与按钮不同步\"。\r\n"
     " toggleGtjaDetail(force) {\r\n"
     " const el = document.getElementById('gt-detail-expander');\r\n"
     " if (!el) return false;\r\n"
     " const open = (force === undefined) ? !el.classList.contains('open') : !!force;\r\n"
     " el.classList.toggle('open', open);\r\n"
     " return open;\r\n"
     " },\r\n"
     "\r\n"
     " // 【功能】渲染单指标「季节性图」",
     "toggleGtjaDetail")

io.open(P, "w", encoding="utf-8", newline="").write(src)
print("OK", P, before, "->", len(src))
