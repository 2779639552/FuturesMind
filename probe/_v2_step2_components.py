# -*- coding: utf-8 -*-
"""重做视觉层 · 第二步：组件规则重写（v2）
用「选择器 -> 新规则体」批量替换（保留选择器，上一轮踩过把选择器替换掉的坑）。
所有尺寸只取 --sp-* / --fs-* 两组令牌，不允许出现游离数值。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")

R = {
# ══ 顶栏 ══
"header": """{
  background: var(--bg-glass);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--border-light);
  box-shadow: none;
  padding: 0 var(--sp-6);
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  position: sticky;
  top: 0;
  z-index: 100;
  transition: background var(--transition);
}""",
"header h1": "{ font-size: var(--fs-md); font-weight: 600; letter-spacing: -0.02em; }",
"header h1 .brand-accent": "{ color: var(--text); text-shadow: none; }",
"header h1 .brand-plain": "{ color: var(--text-secondary); }",
".header-badge": """{
  font-size: var(--fs-xs);
  padding: 2px var(--sp-2);
  border-radius: 980px;
  background: var(--bg-hover);
  color: var(--text-muted);
  font-weight: 400;
  letter-spacing: 0.01em;
}""",
".theme-toggle": """{
  background: var(--bg-hover);
  border: none;
  color: var(--text-secondary);
  padding: 0 var(--sp-3);
  min-height: 32px;
  border-radius: 980px;
  cursor: pointer;
  font-size: var(--fs-xs);
  font-family: inherit;
  transition: background var(--transition), color var(--transition);
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}""",
".theme-toggle:hover": "{ background: var(--border-light); color: var(--text); }",

# ══ 导航：12px 精字，激活只加深字色 + 药丸淡底 ══
"nav": "{ display: flex; gap: 2px; align-items: center; }",
"nav button": """{
  background: transparent;
  border: none;
  color: var(--text-secondary);
  padding: 0 var(--sp-3);
  min-height: 34px;
  border-radius: 980px;
  cursor: pointer;
  font-size: var(--fs-xs);
  font-weight: 400;
  letter-spacing: -0.005em;
  transition: background var(--transition), color var(--transition);
  white-space: nowrap;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
}""",
"nav button:hover": "{ color: var(--text); background: var(--bg-hover); }",
"nav button.active": """{
  color: var(--text);
  font-weight: 500;
  background: var(--brand-dim);
  border: none;
  border-radius: 980px;
  text-shadow: none;
}""",

# ══ 主区：**全宽铺满**（上一版 max-width:1600px 导致宽屏两侧留白）══
"main": "{ padding: var(--sp-7) var(--sp-8) var(--sp-9); max-width: none; margin: 0; }",
".tab": "{ display: none; }",
".tab.active": "{ display: block; animation: fadeIn 0.25s ease; }",

# ══ 栅格 ══
".grid": "{ display: grid; gap: var(--sp-5); }",
".grid-2": "{ grid-template-columns: repeat(2, minmax(0, 1fr)); }",
".grid-3": "{ grid-template-columns: repeat(3, minmax(0, 1fr)); }",
".grid-4": "{ grid-template-columns: repeat(4, minmax(0, 1fr)); }",

# ══ 卡片：色块（间距由设计层的容器 gap 统一接管）══
".card": """{
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius-lg);
  padding: var(--sp-6) var(--sp-7);
  box-shadow: none;
  transition: background var(--transition);
}""",
".card:hover": "{ background: var(--bg-tile); box-shadow: none; }",
".card h3": """{
  font-size: var(--fs-lg);
  font-weight: 600;
  letter-spacing: -0.022em;
  line-height: 1.2;
  margin-bottom: var(--sp-4);
  color: var(--text);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}""",
".card h3 .badge": "{ font-size: var(--fs-xs); padding: 2px var(--sp-2); border-radius: 980px; font-weight: 400; }",

# ══ 徽标 ══
".badge-bull": "{ background: var(--green-bg); color: var(--green); border-radius: 980px; }",
".badge-bear": "{ background: var(--red-bg); color: var(--red); border-radius: 980px; }",
".badge-neutral": "{ background: var(--amber-bg); color: var(--amber); border-radius: 980px; }",
".badge-info": "{ background: var(--brand-dim); color: var(--brand); border-radius: 980px; }",

# ══ 信号横幅 ══
".signal-banner": """{
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius-lg);
  box-shadow: none;
  padding: var(--sp-8) var(--sp-7);
  text-align: center;
  margin-bottom: 0;
}""",
".signal-banner .signal-label": "{ font-size: var(--fs-xs); letter-spacing: 0.06em; color: var(--text-muted); text-transform: uppercase; }",
".signal-banner .signal-rating": "{ font-size: var(--fs-2xl); font-weight: 600; letter-spacing: -0.035em; line-height: 1.05; margin: var(--sp-2) 0; }",
".signal-banner .signal-meta": "{ font-size: var(--fs-base); color: var(--text-secondary); }",

# ══ KPI ══
".stat-row": """{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--sp-5);
  margin-bottom: 0;
}""",
".stat-card": """{
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius-lg);
  padding: var(--sp-5) var(--sp-6);
  text-align: left;
  min-width: 0;
  transition: none;
}""",
".stat-card .stat-val": """{
  font-size: var(--fs-xl);
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.03em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}""",
".stat-card .stat-lbl": "{ font-size: var(--fs-sm); color: var(--text-secondary); margin-top: var(--sp-1); }",

# ══ 表单：白底 + 清晰描边（避免白底上边界不可见而"糊成一片"）══
"input, select, textarea": """{
  background: var(--bg-input);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 0 var(--sp-3);
  min-height: 40px;
  border-radius: var(--radius);
  font-size: var(--fs-base);
  font-family: inherit;
  transition: border-color .2s, box-shadow .2s;
  width: 100%;
  box-sizing: border-box;
}""",
"input:focus, select:focus, textarea:focus": "{ outline: none; border-color: var(--brand); box-shadow: var(--input-focus-ring); }",
"textarea": "{ padding: var(--sp-2) var(--sp-3); min-height: 96px; line-height: 1.5; }",
"select": "{ cursor: pointer; appearance: none; background-image: none; padding-right: var(--sp-3); }",
"label": "{ font-size: var(--fs-xs); color: var(--text-secondary); display: block; margin-bottom: var(--sp-2); }",

# ══ 按钮：统一 40px 高。**这是上一轮"按钮高度不统一"的修法**：
#     高度由 min-height 定，内容用 align-items:center 居中 ——
#     于是"文字按钮"与"图标按钮"内容盒不同也不影响总高。══
".btn": """{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  min-height: 40px;
  padding: 0 var(--sp-6);
  border-radius: 980px;
  font-size: var(--fs-base);
  font-weight: 400;
  letter-spacing: -0.01em;
  cursor: pointer;
  border: none;
  transition: background .2s, color .2s, border-color .2s;
  font-family: inherit;
  white-space: nowrap;
  box-sizing: border-box;
}""",
".btn-primary": "{ background: var(--btn-primary-bg); color: var(--btn-primary-fg); box-shadow: none; }",
".btn-primary:hover": "{ background: var(--brand-hover); opacity: 1; transform: none; box-shadow: none; }",
".btn-primary:disabled": "{ opacity: 0.42; cursor: not-allowed; }",
".btn-green": "{ background: var(--green); color: #fff; }",
".btn-red": "{ background: var(--red); color: #fff; }",
".btn-outline": "{ background: transparent; border: 1px solid var(--brand); color: var(--brand); }",
".btn-outline:hover": "{ background: var(--brand); color: #fff; border-color: var(--brand); box-shadow: none; }",
".btn-sm": "{ min-height: 32px; padding: 0 var(--sp-4); font-size: var(--fs-sm); }",
".depth-preset": "{ cursor: pointer; }",
".depth-preset.active": "{ border-color: transparent; color: var(--brand-ink); background: var(--brand); }",
".depth-mod-row": "{ margin-top: var(--sp-3); display: flex; gap: var(--sp-4); align-items: center; flex-wrap: wrap; font-size: var(--fs-sm); color: var(--text-secondary); }",
".depth-mod-row .lbl": "{ font-weight: 500; }",
".depth-mod-row .sep": "{ width: 1px; height: 16px; background: var(--border); margin: 0; }",
".depth-mod-row label": "{ display: inline-flex; align-items: center; gap: var(--sp-2); cursor: pointer; user-select: none; white-space: nowrap; margin-bottom: 0; }",
".depth-mod-row label input": "{ accent-color: var(--brand); margin: 0; }",
".depth-mod-row label.disabled": "{ opacity: 0.4; pointer-events: none; }",

# ══ 流水线 ══
".pipeline": "{ display: flex; flex-wrap: wrap; gap: var(--sp-2); margin: var(--sp-3) 0; }",
".pipeline-stage": """{
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 6px var(--sp-3);
  border-radius: 980px;
  font-size: var(--fs-xs);
  font-weight: 400;
  background: var(--bg-tile-nested);
  border: none;
  transition: background var(--transition), color var(--transition);
}""",
".pipeline-stage.done": "{ color: var(--green); background: var(--green-bg); }",
".pipeline-stage.active": "{ color: var(--brand); background: var(--brand-dim); animation: pulse 1.5s infinite; }",
".pipeline-stage.pending": "{ color: var(--text-muted); }",

# ══ 日志 ══
".progress-log": """{
  background: var(--bg-tile-nested);
  border: none;
  border-radius: var(--radius);
  padding: var(--sp-4);
  max-height: 500px;
  overflow-y: auto;
  font-family: "SF Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: var(--fs-sm);
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
}""",
".progress-log .node": "{ color: var(--brand); font-weight: 500; }",

# ══ 研报正文 ══
".report-content": "{ line-height: 1.6; font-size: var(--fs-base); max-height: none; overflow-y: visible; }",
".report-content h1, .report-content h2, .report-content h3": "{ color: var(--text); margin: var(--sp-5) 0 var(--sp-2); letter-spacing: -0.02em; }",
".report-content h2": "{ font-size: var(--fs-lg); }",
".report-content h3": "{ font-size: var(--fs-md); }",
".report-content table": "{ width: 100%; border-collapse: collapse; font-size: var(--fs-sm); margin: var(--sp-3) 0; }",
".report-content th": "{ background: transparent; color: var(--text-muted); font-weight: 400; padding: var(--sp-2) var(--sp-3); text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }",
".report-content td": "{ padding: var(--sp-3); border-bottom: 1px solid var(--border-light); }",
".report-content ul, .report-content ol": "{ margin: var(--sp-3) 0; padding-left: var(--sp-6); }",
".report-content li": "{ margin: 2px 0; }",
".report-content code": "{ background: var(--bg-tile-nested); padding: 2px 6px; border-radius: 6px; font-size: 0.9em; }",
".report-content pre": "{ background: var(--bg-tile-nested); padding: var(--sp-4); border-radius: var(--radius); overflow-x: auto; font-size: var(--fs-sm); }",
".report-content blockquote": "{ border-left: 3px solid var(--brand); padding: var(--sp-2) var(--sp-4); margin: var(--sp-3) 0; background: var(--bg-tile-nested); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }",
".report-content hr": "{ border: none; border-top: 1px solid var(--border-light); margin: var(--sp-5) 0; }",
"a": "{ color: var(--link); text-decoration: none; }",
"a:hover": "{ text-decoration: underline; }",
".report-content a": "{ color: var(--link); text-decoration: none; }",
".report-content a:hover": "{ text-decoration: underline; }",

# ══ 增量表 ══
".delta-tbl": "{ width: 100%; min-width: 640px; }",
".delta-tbl th, .delta-tbl td": "{ padding: var(--sp-2) var(--sp-3); border-bottom: 1px solid var(--border-light); text-align: center; white-space: nowrap; }",
".delta-tbl th:first-child, .delta-tbl td:first-child": "{ text-align: left; }",
".delta-tbl thead th": "{ background: transparent; color: var(--text-muted); font-weight: 400; }",
".delta-tbl tbody tr:hover": "{ background: var(--bg-hover); }",
".delta-src": "{ display: inline-block; font-size: var(--fs-xs); padding: 2px var(--sp-2); border-radius: 980px; border: 1px solid var(--border-light); color: var(--text-secondary); white-space: nowrap; }",
".delta-src.scheduled": "{ border-color: transparent; background: var(--brand-dim); color: var(--brand); }",

# ══ 辩论 ══
".debate-round": "{ background: var(--bg-tile-nested); padding: var(--sp-4); margin: var(--sp-2) 0; border-radius: 0 var(--radius) var(--radius) 0; font-size: var(--fs-base); }",
".debate-round .speaker": "{ font-weight: 500; font-size: var(--fs-sm); margin-bottom: var(--sp-1); }",

# ══ 帖子 / 平台 ══
".post-card": """{
  background: var(--bg-tile-nested);
  border: none;
  border-radius: var(--radius);
  padding: var(--sp-4);
  margin: var(--sp-2) 0;
  font-size: var(--fs-base);
  transition: background var(--transition);
}""",
".post-card:hover": "{ background: var(--border-light); box-shadow: none; }",
".post-card .platform-tag": "{ font-size: var(--fs-xs); padding: 2px var(--sp-2); border-radius: 980px; margin-right: var(--sp-2); font-weight: 400; }",
".sent-plat-filter": "{ display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; margin: 0 0 var(--sp-4); }",
".sent-plat-chip": """{
  font-size: var(--fs-xs);
  padding: 5px var(--sp-3);
  border-radius: 980px;
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  transition: border-color var(--transition), color var(--transition), background var(--transition);
}""",
".sent-plat-chip:hover": "{ border-color: var(--text-muted); color: var(--text); }",
".sent-plat-chip.active": "{ background: var(--brand-dim); border-color: transparent; color: var(--brand); font-weight: 400; }",

# ══ 折叠条 ══
".expander": "{ border: none; border-bottom: 1px solid var(--border-light); border-radius: 0; margin: 0; overflow: visible; }",
".expander-header": """{
  padding: var(--sp-5) 0;
  background: transparent;
  cursor: pointer;
  font-weight: 500;
  font-size: var(--fs-md);
  letter-spacing: -0.015em;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  user-select: none;
  transition: color var(--transition);
}""",
".expander-header:hover": "{ background: transparent; color: var(--brand); }",
".expander-header .arrow": "{ transition: transform var(--transition); font-size: var(--fs-xs); color: var(--text-muted); }",
".expander-body": "{ display: none; padding: 0 0 var(--sp-6); font-size: var(--fs-base); color: var(--text-secondary); line-height: 1.6; }",

# ══ Toast ══
".toast-container": "{ position: fixed; top: 64px; right: var(--sp-5); z-index: 1000; display: flex; flex-direction: column; gap: var(--sp-2); }",
".toast": """{
  padding: var(--sp-3) var(--sp-5);
  border-radius: var(--radius);
  font-size: var(--fs-base);
  font-weight: 400;
  animation: slideIn 0.3s ease;
  box-shadow: none;
  max-width: 380px;
}""",

# ══ 图表 ══
".chart-container": "{ width: 100%; height: 320px; }",

# ══ 仓单 ══
".wt-tabs": "{ display: flex; gap: var(--sp-2); flex-wrap: wrap; margin-bottom: var(--sp-4); }",
".wt-tab": """{
  padding: 7px var(--sp-4); border-radius: 980px; cursor: pointer;
  background: transparent; border: 1px solid var(--border);
  color: var(--text-secondary); font-size: var(--fs-sm);
  transition: background var(--transition), color var(--transition), border-color var(--transition);
}""",
".wt-tab:hover": "{ border-color: var(--brand); color: var(--brand); box-shadow: none; }",
".wt-tab.on": "{ background: var(--brand); border-color: transparent; color: var(--brand-ink); font-weight: 400; box-shadow: none; }",
".wt-excards": "{ display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-5); margin-bottom: 0; }",
".wt-excard": "{ border: none; border-radius: var(--radius-lg); padding: var(--sp-5) var(--sp-6); background: var(--bg-tile); }",
".wt-excard .n": "{ font-size: var(--fs-sm); color: var(--text-secondary); margin-bottom: var(--sp-1); }",
".wt-excard .v": "{ font-size: var(--fs-lg); font-weight: 600; color: var(--text); line-height: 1.2; letter-spacing: -0.025em; }",
".wt-excard .u": "{ font-size: var(--fs-xs); color: var(--text-muted); font-weight: 400; }",
".wt-excard .s": "{ font-size: var(--fs-xs); color: var(--text-muted); margin-top: var(--sp-1); line-height: 1.5; }",
".wt-empty": "{ display: flex; align-items: center; justify-content: center; height: 100%; min-height: 180px; padding: var(--sp-5); text-align: center; color: var(--text-muted); font-size: var(--fs-base); line-height: 1.6; }",
".wt-kpi": "{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: var(--sp-4); margin-bottom: 0; }",
".wt-kpi > div": "{ border: none; border-radius: var(--radius); padding: var(--sp-4); background: var(--bg-tile); }",
".wt-kpi .k": "{ font-size: var(--fs-xs); color: var(--text-secondary); }",
".wt-kpi .v": "{ font-size: var(--fs-md); font-weight: 600; margin-top: 2px; letter-spacing: -0.02em; }",

# ══ 小模块索引（扁平化：去紫底）══
".sec-index": "{ border: none; border-bottom: 1px solid var(--border-light); border-radius: 0; background: transparent; padding: 0 0 var(--sp-4); margin-bottom: 0; }",
".sec-index-head": "{ display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; margin-bottom: var(--sp-3); }",
".sec-index-title": "{ font-size: var(--fs-base); font-weight: 500; color: var(--text); display: flex; align-items: center; gap: var(--sp-2); }",
".sec-index-meta": "{ font-size: var(--fs-xs); color: var(--text-secondary); font-weight: 400; }",
".sec-index-list": "{ display: flex; flex-wrap: wrap; gap: var(--sp-2); }",
".sec-pill": """{
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: 5px var(--sp-3);
  border-radius: 980px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--transition), border-color var(--transition), color var(--transition);
}""",
".sec-pill:hover": "{ border-color: var(--text-muted); color: var(--text); }",
".sec-pill.has-on": "{ border-color: transparent; color: var(--brand); background: var(--brand-dim); }",
".sec-index-item": """{
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: 6px var(--sp-3);
  border-radius: 980px;
  background: var(--bg-tile);
  border: 1px solid transparent;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition), border-color var(--transition), color var(--transition);
}""",
".sec-index-item:hover": "{ background: var(--border-light); border-color: transparent; color: var(--text); }",
".sec-index-item.is-open": "{ border-color: transparent; color: var(--text); }",
".sec-index-item.is-on": "{ border-color: transparent; color: var(--brand); background: var(--brand-dim); box-shadow: none; }",
".sec-index-item .sec-ck": """{
  display: inline-flex; align-items: center; justify-content: center;
  width: 14px; height: 14px; flex: 0 0 auto; box-sizing: border-box;
  border: 2px solid var(--ck-box); border-radius: 4px; background: transparent;
  font-size: 10px; line-height: 1; font-weight: 700; color: transparent;
  overflow: hidden;
  transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}""",
".sec-index-item.is-on .sec-ck": "{ background: var(--brand); border-color: var(--brand); color: var(--ck-mark); transform: none; }",

# ══ 页级控制条 ══
".page-tools": """{
  position: sticky;
  top: 48px;
  z-index: 45;
  margin-bottom: 0;
  border-radius: 0;
  background: var(--bg);
  padding-bottom: var(--sp-5);
}""",

# ══ 拖拽区 ══
".dz": """{
  position: relative;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-tile-nested);
  padding: var(--sp-5);
  transition: border-color .15s, background .15s;
  cursor: pointer;
  min-width: 0;
  text-align: center;
}""",
".dz:hover": "{ border-color: var(--brand-line); }",
".dz.is-drag": "{ border-color: var(--brand); background: var(--brand-dim); box-shadow: none; }",
".dz.is-bad": "{ border-color: var(--red); background: var(--red-bg); }",
".dz-hint": "{ display: flex; align-items: center; justify-content: center; gap: var(--sp-2); font-size: var(--fs-base); color: var(--text-secondary); line-height: 1.5; }",
".dz-hint b": "{ color: var(--brand); font-weight: 500; }",
".dz-file": "{ display: none; margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--border-light); font-size: var(--fs-sm); color: var(--text); word-break: break-all; line-height: 1.5; }",
".dz-list": "{ display: none; margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--border-light); max-height: 132px; overflow: auto; font-size: var(--fs-sm); color: var(--text-secondary); line-height: 1.6; }",
".dz-err": "{ display: none; margin-top: var(--sp-2); font-size: var(--fs-sm); color: var(--red); }",
}

def replace_rule(text, selector, body):
    """替换 `selector ` 后的规则体，**保留选择器**（上一轮踩过删掉选择器的坑）。"""
    pat = re.compile(r'(?m)^(' + re.escape(selector) + r')\s*\{[^{}]*\}')
    new, n = pat.subn(lambda m: m.group(1) + ' ' + body, text, count=1)
    return new, n > 0

warns = []
for sel, body in R.items():
    src, ok = replace_rule(src, sel, body)
    if not ok:
        warns.append(sel)

F.write_text(src, encoding="utf-8")
print(f"OK 组件层 v2：命中 {len(R)-len(warns)}/{len(R)}")
if warns:
    print("未命中（需确认）:")
    for w in warns:
        print("   -", w)
print("行数:", src.count("\n")+1, " 字节:", len(src.encode('utf-8')))
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
