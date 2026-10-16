# -*- coding: utf-8 -*-
"""把国君页 ④ 走势图 / ⑤ 明细数据 并入 ③ 指标总览（用户 2026-09-30 要求）。

【为什么】④⑤ 都由 ③ 的「查看」按钮驱动 —— 拆三张卡时点「查看」要等下面两块展开
（靠 _gtEnsureVisible 补丁），三块还各占一条小模块索引项。合并后
「选指标 → 看走势 → 看明细」在同一张卡里连贯完成，索引从 5 项减到 3 项。

⚠️ 三个容器 id（#gt-indicators / #gt-chart / #gt-table）与三个 badge **一律不改** ——
   tests/js/gtja_module_harness.mjs 按 id 断言渲染行为。
⚠️ 子标题用 <div> 不用 <h3>：_secTitleOf 取卡内**第一个** h3 作索引名，多塞 h3 会串。
⚠️ web_template.html 是 CRLF，必须二进制读写 + 显式 \\r\\n。
"""
P = r"D:\AgentSense\AgentSense\web_template.html"

raw = open(P, "rb").read()
assert b"\r\n" in raw and (raw.count(b"\n") - raw.count(b"\r\n")) == 0, "模板应为纯 CRLF"
txt = raw.decode("utf-8")

OLD = "\r\n".join([
    '  <!-- ③ 指标总览表 -->',
    '  <div class="card" style="margin-top:16px">',
    '    <h3>③ 指标总览 <span class="badge badge-info" id="gt-ind-badge"></span></h3>',
    '    <div id="gt-indicators" style="overflow-x:auto">',
    '      <span style="color:var(--text-muted)">查询后显示。</span>',
    '    </div>',
    '  </div>',
    '',
    '  <!-- ④ 走势图(点击上表某行 → 切换到这里) -->',
    '  <div class="card" style="margin-top:16px">',
    '    <h3>④ 走势图 <span class="badge badge-info" id="gt-chart-badge"></span></h3>',
    '    <div id="gt-chart" style="height:340px"></div>',
    '    <div id="gt-chart-note" style="font-size:0.82rem;color:var(--text-muted);margin-top:8px"></div>',
    '  </div>',
    '',
    '  <!-- ⑤ 明细表(选中指标的逐日数值) -->',
    '  <div class="card" style="margin-top:16px">',
    '    <h3>⑤ 明细数据 <span class="badge badge-info" id="gt-tbl-badge"></span></h3>',
    '    <div id="gt-table" style="max-height:420px;overflow:auto;font-size:0.84rem">',
    '      <span style="color:var(--text-muted)">点击上方指标的「查看」载入明细。</span>',
    '    </div>',
    '  </div>',
]) + "\r\n"

NEW = "\r\n".join([
    '  <!-- ③ 指标总览 + 走势图 + 明细数据（2026-09-30 三卡合并, 用户要求）',
    '       【为什么合并】④⑤ 原本各自成卡, 但两者都**只由本卡的「查看」按钮驱动** ——',
    '         拆开时点「查看」得等下面两块展开(靠 _gtEnsureVisible 补丁兜着), 且三块分散、',
    '         各占一条小模块索引项。合并后「选指标 → 看走势 → 看明细」在同一条视线内连贯完成,',
    '         本页索引由 5 项减为 3 项。',
    '       ⚠️ 三个容器 id(#gt-indicators/#gt-chart/#gt-table)与三个 badge 一律不改 ——',
    '         tests/js/gtja_module_harness.mjs 按 id 断言渲染行为。',
    '       ⚠️ 子标题用 <div> 不用 <h3>: _secTitleOf 取卡内**第一个** h3 作索引名, 多塞会串名。',
    '       ⚠️ 折叠靠 max-height + overflow:hidden(见 ⑨ 段), 不可对 #gt-chart 用 display:none',
    '         —— 那样 ECharts 会 init 出 0×0 空白画布。 -->',
    '  <div class="card" style="margin-top:16px">',
    '    <h3>③ 指标总览 / 走势图 / 明细 <span class="badge badge-info" id="gt-ind-badge"></span></h3>',
    '    <div id="gt-indicators" style="overflow-x:auto">',
    '      <span style="color:var(--text-muted)">查询后显示。</span>',
    '    </div>',
    '',
    '    <!-- 走势图(点击上表某行「查看」→ 就地渲染在此, 不再跳到别的卡) -->',
    '    <div style="margin-top:16px;padding-top:12px;border-top:1px dashed var(--border,#e5e7eb)">',
    '      <div style="font-size:0.88rem;font-weight:600;color:var(--text-secondary);margin-bottom:8px">',
    '        走势图 <span class="badge badge-info" id="gt-chart-badge"></span>',
    '      </div>',
    '      <div id="gt-chart" style="height:340px"></div>',
    '      <div id="gt-chart-note" style="font-size:0.82rem;color:var(--text-muted);margin-top:8px"></div>',
    '    </div>',
    '',
    '    <!-- 明细数据(选中指标的逐日数值) -->',
    '    <div style="margin-top:16px;padding-top:12px;border-top:1px dashed var(--border,#e5e7eb)">',
    '      <div style="font-size:0.88rem;font-weight:600;color:var(--text-secondary);margin-bottom:8px">',
    '        明细数据 <span class="badge badge-info" id="gt-tbl-badge"></span>',
    '      </div>',
    '      <div id="gt-table" style="max-height:420px;overflow:auto;font-size:0.84rem">',
    '        <span style="color:var(--text-muted)">点击上方指标的「查看」载入明细。</span>',
    '      </div>',
    '    </div>',
    '  </div>',
]) + "\r\n"

assert txt.count(OLD) == 1, "待合并区块未唯一命中: %d" % txt.count(OLD)
txt = txt.replace(OLD, NEW, 1)

out = txt.encode("utf-8")
assert (out.count(b"\n") - out.count(b"\r\n")) == 0, "落盘引入裸 LF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
