# -*- coding: utf-8 -*-
"""P1 收尾 + P2 统一品种状态层 + P3 清理健壮性。

字节级替换(保 CRLF, 见项目铁律 #8); 每项断言命中次数, 任一失败即整体不落盘。
所有中文注释内的引号一律用「」, 避免与 Python 字符串定界符冲突。
"""
import shutil
import sys
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_template.html")
data = P.read_bytes()
CRLF0, LF0, SIZE0 = data.count(b"\r\n"), data.count(b"\n"), len(data)
E = []


def add(old, new, n, note):
    E.append((old.encode("utf-8"), new.encode("utf-8"), n, note))


# ══════════════════════════════════════════════════════════════════
# A. P1 收尾: 把混进控制条的大块内容退回正文(吸顶会遮屏)
# ══════════════════════════════════════════════════════════════════

# A1 run: 「品种信息」卡从 grid 里拆出, 退回正文
add(
    '    </div>\r\n'
    '    <div class="card">\r\n'
    '      <h3>品种信息 <span class="badge badge-info" id="sentiment-badge"></span></h3>\r\n'
    '      <div id="variety-info" style="font-size:0.84rem;color:var(--text-secondary)">选择品种查看详情</div>\r\n'
    '    </div>\r\n'
    '  </div>\r\n',
    '    </div>\r\n'
    '  </div>\r\n'
    '  <!-- 品种信息卡(2026-09-30 由「分析配置」卡拆出): 它随选中品种异步刷新,\r\n'
    '       放进常驻控制条会顶着半屏。控制条只保留需要操作的东西。 -->\r\n'
    '  <div class="card" style="margin-bottom:16px">\r\n'
    '    <h3>品种信息 <span class="badge badge-info" id="sentiment-badge"></span></h3>\r\n'
    '    <div id="variety-info" style="font-size:0.84rem;color:var(--text-secondary)">选择品种查看详情</div>\r\n'
    '  </div>\r\n',
    1, "A1 run: 品种信息卡退回正文",
)

# A2 research: 问答历史区(含错题本 details)搬出控制条
add(
    '    <div id="res-ask-hist" style="display:flex;flex-direction:column;gap:12px;'
    'color:var(--text-muted);font-size:0.84rem;max-height:420px;overflow-y:auto;'
    'padding-right:4px">输入问题开始提问; 对话已按会话自动保存, 可从右上角下拉找回历史。</div>\r\n',
    '  </div>\r\n'
    '  <!-- 问答历史区 + 错题本/黑话词典(2026-09-30 由「研报问答」卡拆出):\r\n'
    '       结果区不当常驻控制条。 -->\r\n'
    '  <div class="card" style="margin-bottom:16px">\r\n'
    '    <div id="res-ask-hist" style="display:flex;flex-direction:column;gap:12px;'
    'color:var(--text-muted);font-size:0.84rem;max-height:420px;overflow-y:auto;'
    'padding-right:4px">输入问题开始提问; 对话已按会话自动保存, 可从右上角下拉找回历史。</div>\r\n',
    1, "A2 research: 问答历史区退回正文",
)

# A3 gtja: 覆盖矩阵大表搬出控制条(它可能有 21 行)
add(
    '    <div id="gt-matrix" style="overflow-x:auto;margin-top:10px">\r\n'
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\r\n'
    '    </div>\r\n'
    '  </div>\r\n',
    '  </div>\r\n'
    '  <!-- 覆盖矩阵(2026-09-30 由「① 查询与覆盖矩阵」卡拆出):\r\n'
    '       21 行表格, 不能跟着控制条吸顶。 -->\r\n'
    '  <div class="card" style="margin-bottom:16px">\r\n'
    '    <h3>覆盖矩阵</h3>\r\n'
    '    <div id="gt-matrix" style="overflow-x:auto;margin-top:10px">\r\n'
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\r\n'
    '    </div>\r\n'
    '  </div>\r\n',
    1, "A3 gtja: 覆盖矩阵退回正文",
)

add('    <h3>① 查询与覆盖矩阵 <span class="badge badge-info" id="gt-matrix-badge"></span></h3>',
    '    <h3>① 查询控制 <span class="badge badge-info" id="gt-matrix-badge"></span></h3>',
    1, "A4 gtja: 控制卡标题改名(名实相符)",
)

# A5 warrant: 把「品种」控制格从「② 品种仓单细则」卡提出, 放到控制条最前
add(
    '<div id="tab-warrant-tools" class="page-tools">\r\n'
    '  <!-- 数据状态条 -->\r\n',
    '<div id="tab-warrant-tools" class="page-tools">\r\n'
    '  <!-- 品种仓单细则的「品种」选择(2026-09-30 从「② 品种仓单细则」卡提出):\r\n'
    '       本页 4 张细则图全依赖这个品种取值, 而它原先是第二栏内部的第一个控件 ——\r\n'
    '       折叠/小模块筛选后用户就找不到品种入口了。控制条必须持有本页的品种入口。 -->\r\n'
    '  <div class="card" style="margin-bottom:16px">\r\n'
    '    <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap">\r\n'
    '      <div style="flex:1;min-width:220px">\r\n'
    '        <label>品种</label>\r\n'
    '        <select id="wt-product" onchange="App.loadWarrantProduct()"></select>\r\n'
    '      </div>\r\n'
    '      <span style="font-size:0.78rem;color:var(--text-muted);padding-bottom:8px" id="wt-product-tip"></span>\r\n'
    '    </div>\r\n'
    '  </div>\r\n'
    '  <!-- 数据状态条 -->\r\n',
    1, "A5 warrant: 控制条新增品种格",
)

add(
    '    <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:14px">\r\n'
    '      <div style="flex:1;min-width:220px">\r\n'
    '        <label>品种</label>\r\n'
    '        <select id="wt-product" onchange="App.loadWarrantProduct()"></select>\r\n'
    '      </div>\r\n'
    '      <span style="font-size:0.78rem;color:var(--text-muted);padding-bottom:8px" id="wt-product-tip"></span>\r\n'
    '    </div>\r\n',
    '',
    1, "A6 warrant: 移除第二栏里被搬走的原控制格",
)

# ══════════════════════════════════════════════════════════════════
# B. P2 统一品种状态层
# ══════════════════════════════════════════════════════════════════
add(
    "  // 【品种池】默认品种兜底(2026-09-09):旧兜底 'RB' 已随 21 品种池收缩出池,\r\n"
    "  // 统一回退到下拉第一个池内品种(数据为空时再兜 'SC' 防未加载)。\r\n"
    "  _defaultCode() {\r\n"
    "    return (this._varieties && this._varieties[0] && this._varieties[0].code) || 'SC';\r\n"
    "  },\r\n",
    "  // ═══════════════════════════════════════════════════════════\r\n"
    "  // 【品种状态层 2026-09-30】\r\n"
    "  // 解决什么问题: 此前各函数直接 getElementById('sym').value 取值 —— 品种选择\r\n"
    "  //   被绑死在某一个小模块的 <select> 上, 其他模块想用只能去读它。一旦折叠 /\r\n"
    "  //   小模块筛选把那个 select 藏起来, 或它所在的卡根本没渲染, 取值就落空;\r\n"
    "  //   而 _defaultCode() 又只会回退到池内第一项, 于是别的模块无法独立完成\r\n"
    "  //   —— 即用户反馈的「品种选择被困在某个小模块里」。\r\n"
    "  // 做法: 统一入口 _curVariety(scope)。元素在 → 读它当前值(永远最新, 不缓存旧值);\r\n"
    "  //   元素被折叠/筛选隐藏(display:none / max-height:0) → select.value 仍可读;\r\n"
    "  //   元素整个不存在 → 回退到「最后已知值」, 再回退池内首项, 而不是静默取错品种。\r\n"
    "  // ⚠️ 语义例外: 以「全部品种」(空串)为合法取值的控件(#event-variety /\r\n"
    "  //   #res-ask-variety / #res-filter-variety) 不接入本层 —— 空串是它们的正常语义,\r\n"
    "  //   回退成池首会把「全部」悄悄变成「某一个品种」。\r\n"
    "  // ═══════════════════════════════════════════════════════════\r\n"
    "  _tabVariety: {},   // 【状态】scope(控件 id) → 最后已知品种码\r\n"
    "\r\n"
    "  // 【功能】读取某页/某模块当前的品种(唯一入口)\r\n"
    "  // 【输入】scope: 品种控件的 id(如 'sym' / 'comp-variety' / 'overlay-sym')\r\n"
    "  // 【返回】品种代码(保证非空)\r\n"
    "  _curVariety(scope) {\r\n"
    "    const el = document.getElementById(scope);\r\n"
    "    const cur = (el && el.value) ? el.value : '';\r\n"
    "    this._tabVariety = this._tabVariety || {};\r\n"
    "    if (cur) {\r\n"
    "      this._tabVariety[scope] = cur;   // 记下最后已知值: 元素被移除时兜底\r\n"
    "      return cur;\r\n"
    "    }\r\n"
    "    return this._tabVariety[scope] || this._defaultCode();\r\n"
    "  },\r\n"
    "\r\n"
    "  // 【品种池】默认品种兜底(2026-09-09):旧兜底 'RB' 已随 21 品种池收缩出池。\r\n"
    "  // 【2026-09-30】本方法只作最后兜底, 正常路径一律走 _curVariety(scope) ——\r\n"
    "  //   此前它被当成「当前品种」使用, 才会出现「模块单独打开时拿的是池内第一项」。\r\n"
    "  _defaultCode() {\r\n"
    "    return (this._varieties && this._varieties[0] && this._varieties[0].code) || 'SC';\r\n"
    "  },\r\n",
    1, "B1 P2: 新增 _curVariety 状态层",
)

add("const sym = document.getElementById('sym').value;",
    "const sym = this._curVariety('sym');", 5, "B2 P2: #sym 直读 ×5 → _curVariety")

add("    const v = document.getElementById('comp-variety').value;",
    "    const v = this._curVariety('comp-variety');", 1, "B3 P2: loadComprehensive 取品种")

add("const v = forceV || document.getElementById('comp-variety')?.value || this._defaultCode();",
    "const v = forceV || this._curVariety('comp-variety');", 3, "B4 P2: comp-variety 兜底链 ×3")

add("    const sym = (_oSel && _oSel.value) || document.getElementById('sym')?.value || '';",
    "    const sym = (_oSel && _oSel.value) || this._curVariety('overlay-sym');",
    1, "B5 P2: overlay 去跨页回退")

add(
    "      this._varieties.forEach(v => {\r\n"
    "        const o = document.createElement('option');\r\n"
    "        o.value = v.code; o.textContent = `${v.code} - ${v.name}`;\r\n"
    "        sel.appendChild(o);\r\n"
    "      });\r\n"
    "    });\r\n"
    "  },\r\n",
    "      this._varieties.forEach(v => {\r\n"
    "        const o = document.createElement('option');\r\n"
    "        o.value = v.code; o.textContent = `${v.code} - ${v.name}`;\r\n"
    "        sel.appendChild(o);\r\n"
    "      });\r\n"
    "      // 【2026-09-30】填充后补默认选中: 此前只填不选, 单独打开某模块时下拉会停在\r\n"
    "      //   空白首项, 与状态层记录不一致 ⇒ 该模块看起来像没选品种。统一以状态层为准。\r\n"
    "      if (!sel.value) sel.value = this._curVariety(id);\r\n"
    "    });\r\n"
    "  },\r\n",
    1, "B6 P2: 下拉填充后补默认选中",
)

# ══════════════════════════════════════════════════════════════════
# C. P3 清理与健壮性
# ══════════════════════════════════════════════════════════════════
add("    this._populateVarietySelects(['comp-variety', 'trade-variety', 'event-variety']);",
    "    // 【2026-09-30】去掉 'trade-variety': 该 id 在 DOM 中不存在(交易回测页已下线),\r\n"
    "    //   此前每进一次分析工具页都白填一次, 属死引用。\r\n"
    "    this._populateVarietySelects(['comp-variety', 'event-variety']);",
    1, "C1 P3: 摘除幽灵 id trade-variety")

add(
    "          if (el.id === 'analysis-subnav') return false;\r\n"
    "          const txt = (el.textContent || '').trim();\r\n"
    "          return !!txt && el.offsetHeight <= HEAD_MAX_H;\r\n",
    "          if (el.id === 'analysis-subnav') return false;\r\n"
    "          // 【2026-09-30 修正】两类元素永不视为段头:\r\n"
    "          //   ① 常驻控制条(.page-tools) —— 它不是任何模块的标题;\r\n"
    "          //   ② 真·不可见的元素 —— opacity:0 的元素 offsetHeight=0, 会被下面\r\n"
    "          //      「矮」的判据误判成段头而放行, 于是筛选后页面上仍挂着看不见\r\n"
    "          //      却占位的空壳(实测同类隐患)。\r\n"
    "          if (el.classList.contains('page-tools')) return false;\r\n"
    "          const _st = window.getComputedStyle(el);\r\n"
    "          if (_st.display === 'none' || parseFloat(_st.opacity || '1') === 0) return false;\r\n"
    "          const txt = (el.textContent || '').trim();\r\n"
    "          return !!txt && el.offsetHeight <= HEAD_MAX_H;\r\n",
    1, "C2 P3: isHeader 不误放行控制条/不可见元素",
)

# ══════════════════════════════════════════════════════════════════
# D. 折叠体系的两处核心排除(★ 少了这两处, 控制条会被再折叠/再隐藏)
# ══════════════════════════════════════════════════════════════════
add(
    "      if (cls.contains('sec-index') || cls.contains('expander') || cls.contains('sec-block')) return;",
    "      // ★【2026-09-30】控制条(.page-tools)永不参与折叠 —— 否则它会被当成本 tab 的\r\n"
    "      //   一个小模块再包一层外壳, 于是「常驻」失效: 默认只展开第一个有内容的模块,\r\n"
    "      //   控制条(高度~30px)会落在收起态, 用户还是看不见品种入口。\r\n"
    "      if (cls.contains('page-tools') || cls.contains('sec-index')\r\n"
    "          || cls.contains('expander') || cls.contains('sec-block')) return;",
    1, "D1 P3: _secScan 排除 .page-tools",
)

add(
    "        kids.forEach(el => { if (el !== idx) el.classList.add('sec-hidden'); });",
    "        // ★【2026-09-30】清场时放行控制条: 它是操作入口, 不属于任何被筛的小模块。\r\n"
    "        //   否则勾选任一小模块后, 品种入口会连同未选中的模块一起消失。\r\n"
    "        kids.forEach(el => {\r\n"
    "          if (el === idx || el.classList.contains('page-tools')) return;\r\n"
    "          el.classList.add('sec-hidden');\r\n"
    "        });",
    1, "D2 P3: _applySecFilter 清场放行 .page-tools",
)

# ══════════════════════════════════════════════════════════════════
# E. CSS: .page-tools 常驻吸顶 + #analysis-subnav 吸顶
# ══════════════════════════════════════════════════════════════════
add(
    "/* 小模块（卡片）外壳: 折叠时收起内容, 展开时撑开 */\r\n",
    "/* ══════════════════════════════════════════════════════════════════\r\n"
    "   ★ 页级控制条 .page-tools (2026-09-30)\r\n"
    "   ------------------------------------------------------------\r\n"
    "   解决什么问题: 品种/区间这类操作入口原先塞在某一个小模块卡片里 —— 折叠起来\r\n"
    "   就看不见, 小模块筛选时还会被隐藏, 而其他模块仍要向它取品种值 ⇒ 用户在页面上\r\n"
    "   找不到品种入口, 单开一个模块也跑不出数。\r\n"
    "   做法: 把控制条提到页签第一个子元素, 独立成条 + 吸顶常驻; JS 侧见 _secScan /\r\n"
    "   _applySecFilter 的排除规则(永不参与折叠、永不被筛选隐藏)。\r\n"
    "   ⚠️ top 必须避开顶部导航栏(header 自身 sticky top:0, 高 56px), 否则吸顶时被导航\r\n"
    "   栏盖住。移动端导航栏移到底部(见 <=768px 断点), 该 56px 仍适用。\r\n"
    "   ⚠️ z-index 必须低于 header(100), 高于正文, 否则吸顶时压住导航栏。\r\n"
    "   ══════════════════════════════════════════════════════════════════ */\r\n"
    ".page-tools {\r\n"
    "  position: sticky;\r\n"
    "  top: 56px;\r\n"
    "  z-index: 45;\r\n"
    "  margin-bottom: 16px;\r\n"
    "  border-radius: var(--radius, 10px);\r\n"
    "  /* 吸顶必须有实色底, 否则下方内容透出来糊成一片。\r\n"
    "     --bg-card 在暗色/亮色下都是实色(非 --bg-glass 那种半透明), 故用它。 */\r\n"
    "  background: var(--bg-card);\r\n"
    "}\r\n"
    ".page-tools > .card { margin-bottom: 0; }\r\n"
    ".page-tools > .card + .card { margin-top: 12px; }   /* 控制条内多张卡之间的间距 */\r\n"
    ".page-tools .grid { margin-bottom: 0 !important; }\r\n"
    ".page-tools .grid > .card { margin-bottom: 0; }\r\n"
    "/* 控制条内硬上限兜底: 万一后续有人往控制条里塞了大块内容, 内部滚动即可,\r\n"
    "   不至于把吸顶条撑成遮住半屏。 */\r\n"
    ".page-tools .page-tools-scroll {\r\n"
    "  max-height: 40vh;\r\n"
    "  overflow-y: auto;\r\n"
    "}\r\n"
    "/* 「分析工具」子导航条吸顶: 它是裸 flex 容器(内部无 .card, 进不了小模块索引),\r\n"
    "   切到作者/事件等子模块后要滚回页顶才能再换页 —— 这里让它自己吸住。 */\r\n"
    "#analysis-subnav {\r\n"
    "  position: sticky;\r\n"
    "  top: 56px;\r\n"
    "  z-index: 44;\r\n"
    "  background: var(--bg);\r\n"
    "  padding: 6px 0;\r\n"
    "}\r\n"
    "\r\n"
    "/* 小模块（卡片）外壳: 折叠时收起内容, 展开时撑开 */\r\n",
    1, "E CSS: .page-tools / #analysis-subnav 吸顶",
)

# ── 执行 ──────────────────────────────────────────────────────────
report, ok = [], True
for old, new, n, note in E:
    c = data.count(old)
    if c != n:
        report.append("FAIL 命中 %d 次(期望 %d): %s" % (c, n, note))
        ok = False
        continue
    data = data.replace(old, new)
    report.append("OK   %-44s (替换 %d 次)" % (note, n))

print("\n".join(report))
if not ok:
    print("\n!! 有失败项, 未写入任何内容")
    sys.exit(1)

crlf1, lf1 = data.count(b"\r\n"), data.count(b"\n")
if lf1 - crlf1 != 0:
    print("!! 行尾符被破坏: bareLF=%d" % (lf1 - crlf1))
    sys.exit(1)

shutil.copy2(P, str(P) + ".bak_p2p3_20260930")
P.write_bytes(data)
print("-" * 66)
print("size %d -> %d (%+d)   CRLF %d -> %d   bareLF=%d"
      % (SIZE0, len(data), len(data) - SIZE0, CRLF0, crlf1, lf1 - crlf1))
