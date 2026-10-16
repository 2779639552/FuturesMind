# -*- coding: utf-8 -*-
"""Apple 化 · 第二步：组件规则重写。
用「选择器 -> 新规则体」的方式批量替换，比逐段复制原文更稳（不依赖上下文精确匹配）。
未命中的选择器只 WARN 不中断，最后统一报告。
"""
import re, pathlib, sys

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")

# ── 先补 --bg-tile 令牌（分区底：亮 #f5f5f7 / 暗 #1d1d1f，苹果式「用底色分层」）──
if "--bg-tile" not in src:
    src = src.replace("  --bg-hover: rgba(255,255,255,0.05);",
                      "  --bg-hover: rgba(255,255,255,0.05);\n  --bg-tile: #1d1d1f;", 1)
    src = src.replace("  --bg-hover: #f5f5f7;",
                      "  --bg-hover: #f5f5f7;\n  --bg-tile: #f5f5f7;", 1)

RULES = {
# ══ 顶栏：48px / 毛玻璃 / 零阴影 ══
"header": """{
  /* Apple：48px 精字顶栏 + 毛玻璃，底部分隔靠 1px 细线而非阴影 */
  background: var(--bg-glass);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--border-light);
  box-shadow: none;
  padding: 0 22px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  position: sticky;
  top: 0;
  z-index: 100;
  transition: background var(--transition);
}""",
"header h1": "{ font-size: 1.0625rem; font-weight: 600; letter-spacing: -0.02em; }",
"header h1 .brand-accent": "{ color: var(--text); text-shadow: none; }",
"header h1 .brand-plain": "{ color: var(--text-secondary); }",
".header-badge": """{
  font-size: 0.6875rem;
  padding: 2px 8px;
  border-radius: 980px;
  background: var(--bg-hover);
  color: var(--text-muted);
  font-weight: 400;
  letter-spacing: 0.02em;
}""",
".theme-toggle": """{
  background: var(--bg-hover);
  border: none;
  color: var(--text-secondary);
  padding: 6px 13px;
  border-radius: 980px;
  cursor: pointer;
  font-size: 0.75rem;
  font-family: inherit;
  transition: background var(--transition), color var(--transition);
  display: flex;
  align-items: center;
  gap: 5px;
}""",
".theme-toggle:hover": "{ background: var(--border-light); color: var(--text); }",

# ══ 导航：12px 精字，激活只加深字色 + 药丸淡底 ══
"nav": "{ display: flex; gap: 2px; }",
"nav button": """{
  background: transparent;
  border: none;
  color: var(--text-secondary);
  padding: 6px 12px;
  border-radius: 980px;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 400;
  letter-spacing: -0.005em;
  transition: background var(--transition), color var(--transition);
  white-space: nowrap;
  font-family: inherit;
}""",
"nav button:hover": "{ color: var(--text); background: var(--bg-hover); }",
"nav button.active": """{
  /* Apple：激活态只做「字色加深 + 淡底药丸」，去掉下划线/文字辉光/渐变三层叠加 */
  color: var(--text);
  font-weight: 500;
  background: var(--brand-dim);
  border-bottom: none;
  border-radius: 980px;
  text-shadow: none;
}""",

# ══ 主区：苹果式大留白，内容宽保留工具密度 ══
"main": "{ padding: 34px 22px 80px; max-width: 1520px; margin: 0 auto; }",

# ══ 卡片 -> 分区（去 AI 味的核心动作）══
".card": """{
  /* ★ Apple 化核心：卡片墙 -> 分区。
     单列堆叠时靠 1px 顶细线 + 大留白分层，不再用「边框+圆角+阴影」把页面切成一格格。
     并列（.grid 内）时见下方 .grid > .card 规则，用底色成块。 */
  background: transparent;
  border: none;
  border-top: 1px solid var(--border-light);
  border-radius: 0;
  padding: 28px 0 32px;
  box-shadow: none;
  transition: none;
}""",
".card:hover": "{ border-color: var(--border-light); box-shadow: none; }",
".card h3": """{
  font-size: 1.3125rem;
  font-weight: 600;
  letter-spacing: -0.022em;
  line-height: 1.15;
  margin-bottom: 18px;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 9px;
}""",
".card h3 .badge": "{ font-size: 0.6875rem; padding: 2px 9px; border-radius: 980px; font-weight: 400; }",

# ══ 徽标：药丸化 + 常规字重 ══
".badge-bull": "{ background: var(--green-bg); color: var(--green); border-radius: 980px; }",
".badge-bear": "{ background: var(--red-bg); color: var(--red); border-radius: 980px; }",
".badge-neutral": "{ background: var(--amber-bg); color: var(--amber); border-radius: 980px; }",
".badge-info": "{ background: var(--brand-dim); color: var(--brand); border-radius: 980px; }",

# ══ 信号横幅：去渐变去发光 ══
".signal-banner": """{
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius-lg);
  box-shadow: none;
  padding: 44px 32px;
  text-align: center;
  margin-bottom: 26px;
}""",
".signal-banner .signal-label": """{
  font-size: 0.75rem;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  text-transform: uppercase;
}""",
".signal-banner .signal-rating": """{
  font-size: 3.5rem;
  font-weight: 600;
  letter-spacing: -0.035em;
  line-height: 1.05;
  margin: 8px 0;
}""",
".signal-banner .signal-meta": "{ font-size: 0.9375rem; color: var(--text-secondary); }",

# ══ 指标：苹果 KPI（细分隔线 + 左对齐 + 大数字）══
".stat-row": """{
  /* Apple KPI 行：格子之间用 1px 竖线，不用卡片边框 */
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0;
  margin-bottom: 30px;
}""",
".stat-card": """{
  background: transparent;
  border: none;
  border-left: 1px solid var(--border-light);
  border-radius: 0;
  padding: 2px 26px;
  text-align: left;
  min-width: 0;
  transition: none;
}
.stat-card:first-child { border-left: none; padding-left: 0; }""",
".stat-card .stat-val": """{
  font-size: 2.25rem;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.03em;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}""",
".stat-card .stat-lbl": "{ font-size: 0.8125rem; color: var(--text-secondary); margin-top: 7px; }",

# ══ 表单：12px 圆角，聚焦环走苹果蓝 ══
"input, select, textarea": """{
  background: var(--bg-input);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 10px 13px;
  border-radius: var(--radius);
  font-size: 0.9375rem;
  font-family: inherit;
  transition: border-color .2s, box-shadow .2s;
  width: 100%;
}""",
"input:focus, select:focus, textarea:focus": """{
  outline: none;
  border-color: var(--brand);
  box-shadow: var(--input-focus-ring);
}""",
"label": "{ font-size: 0.75rem; color: var(--text-secondary); display: block; margin-bottom: 6px; }",

# ══ 按钮：980px 药丸，纯色，hover 只换底色 ══
".btn": """{
  /* Apple：radius 980px 药丸 + 纯色填充 + hover 只换底色（无渐变/无辉光/无位移） */
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 11px 22px;
  border-radius: 980px;
  font-size: 0.9375rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  cursor: pointer;
  border: none;
  transition: background .2s, color .2s, border-color .2s;
  font-family: inherit;
  white-space: nowrap;
}""",
".btn-primary": "{ background: var(--btn-primary-bg); color: var(--btn-primary-fg); box-shadow: none; }",
".btn-primary:hover": "{ background: var(--brand-hover); opacity: 1; transform: none; box-shadow: none; }",
".btn-primary:disabled": "{ opacity: 0.42; cursor: not-allowed; }",
".btn-green": "{ background: var(--green); color: #fff; }",
".btn-red": "{ background: var(--red); color: #fff; }",
".btn-outline": "{ background: transparent; border: 1px solid var(--brand); color: var(--brand); }",
".btn-outline:hover": "{ background: var(--brand); color: #fff; border-color: var(--brand); box-shadow: none; }",
".btn-sm": "{ padding: 7px 15px; font-size: 0.8125rem; }",

# ══ 流水线阶段：药丸 ══
".pipeline-stage": """{
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 13px;
  border-radius: 980px;
  font-size: 0.75rem;
  font-weight: 400;
  background: var(--bg-tile);
  border: 1px solid transparent;
  transition: background var(--transition), color var(--transition);
}""",
".pipeline-stage.done": "{ border-color: transparent; color: var(--green); background: var(--green-bg); }",
".pipeline-stage.active": "{ border-color: transparent; color: var(--brand); background: var(--brand-dim); animation: pulse 1.5s infinite; }",
".pipeline-stage.pending": "{ color: var(--text-muted); }",

# ══ 折叠条：苹果 FAQ 式细线 ══
".expander": "{ border: none; border-bottom: 1px solid var(--border-light); border-radius: 0; margin: 0; overflow: visible; }",
".expander-header": """{
  padding: 20px 0;
  background: transparent;
  cursor: pointer;
  font-weight: 500;
  font-size: 1.0625rem;
  letter-spacing: -0.015em;
  display: flex;
  align-items: center;
  justify-content: space-between;
  user-select: none;
  transition: color var(--transition);
}""",
".expander-header:hover": "{ background: transparent; color: var(--brand); }",
".expander-header .arrow": "{ transition: transform var(--transition); font-size: 0.7rem; color: var(--text-muted); }",
".expander-body": "{ display: none; padding: 0 0 26px; font-size: 0.9375rem; color: var(--text-secondary); line-height: 1.6; }",

# ══ 小模块索引：去卡片感 ══
".sec-index": """{
  border: none;
  border-bottom: 1px solid var(--border-light);
  border-radius: 0;
  background: transparent;
  padding: 0 0 18px;
  margin-bottom: 26px;
}""",
".sec-pill": """{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 13px;
  border-radius: 980px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--transition), border var(--transition), color var(--transition);
}""",
".sec-pill:hover": "{ border-color: var(--text-muted); color: var(--text); }",
".sec-pill.has-on": "{ border-color: transparent; color: var(--brand); background: var(--brand-dim); }",
".sec-index-item": """{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 980px;
  background: var(--bg-tile);
  border: 1px solid transparent;
  color: var(--text-secondary);
  font-size: 0.8125rem;
  cursor: pointer;
  user-select: none;
  transition: background var(--transition), border var(--transition),
              color var(--transition), transform 0.22s cubic-bezier(.22,1,.36,1);
}""",
".sec-index-item:hover": "{ background: var(--border-light); border-color: transparent; color: var(--text); }",
".sec-index-item.is-open": "{ border-color: transparent; color: var(--text); }",
".sec-index-item.is-on": """{
  border-color: transparent;
  color: var(--brand);
  background: var(--brand-dim);
  box-shadow: none;
}""",

# ══ 帖子卡 / 平台 chip ══
".post-card": """{
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius);
  padding: 14px 16px;
  margin: 6px 0;
  font-size: 0.875rem;
  transition: background var(--transition);
}""",
".post-card:hover": "{ background: var(--border-light); box-shadow: none; }",
".sent-plat-chip": """{
  font-size: 0.75rem;
  padding: 4px 12px;
  border-radius: 980px;
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  transition: border var(--transition), color var(--transition), background var(--transition);
}""",
".sent-plat-chip:hover": "{ border-color: var(--text-muted); color: var(--text); }",
".sent-plat-chip.active": "{ background: var(--brand-dim); border-color: transparent; color: var(--brand); font-weight: 400; }",

# ══ 仓单模块 ══
".wt-tab": """{
  padding: 7px 15px; border-radius: 980px; cursor: pointer;
  background: transparent; border: 1px solid var(--border);
  color: var(--text-secondary); font-size: 0.8125rem; transition: background var(--transition), color var(--transition), border-color var(--transition);
}""",
".wt-tab:hover": "{ border-color: var(--brand); color: var(--brand); box-shadow: none; }",
".wt-tab.on": "{ background: var(--brand); border-color: transparent; color: var(--brand-ink); font-weight: 400; box-shadow: none; }",
".wt-excard": """{
  border: none; border-left: none;
  border-radius: var(--radius); padding: 18px 20px; background: var(--bg-tile);
}""",
".wt-excard .v": "{ font-size: 1.5rem; font-weight: 600; color: var(--text); line-height: 1.2; letter-spacing: -0.025em; }",
".wt-kpi > div": """{
  flex: 1 1 120px; border: none; border-radius: var(--radius);
  padding: 14px 16px; background: var(--bg-tile);
}""",
".wt-kpi .v": "{ font-size: 1.125rem; font-weight: 600; margin-top: 3px; letter-spacing: -0.02em; }",

# ══ Toast：零阴影 ══
".toast": """{
  padding: 13px 19px;
  border-radius: var(--radius);
  font-size: 0.875rem;
  font-weight: 400;
  animation: slideIn 0.3s ease;
  box-shadow: none;
  max-width: 380px;
}""",

# ══ 日志：等宽但收敛 ══
".progress-log": """{
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius);
  padding: 14px 16px;
  max-height: 500px;
  overflow-y: auto;
  font-family: "SF Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: 0.8125rem;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
}""",

# ══ 辩论轮次 ══
".debate-round": """{
  background: var(--bg-tile);
  padding: 12px 16px;
  margin: 6px 0;
  border-radius: 0 var(--radius) var(--radius) 0;
  font-size: 0.875rem;
}""",
".debate-round .speaker": "{ font-weight: 500; font-size: 0.8125rem; margin-bottom: 5px; }",

# ══ 数据来源小标 ══
".delta-src": """{
  display: inline-block; font-size: 0.6875rem; padding: 2px 8px; border-radius: 980px;
  border: 1px solid var(--border-light); color: var(--text-secondary); white-space: nowrap;
}""",
".delta-src.scheduled": "{ border-color: transparent; background: var(--brand-dim); color: var(--brand); }",

# ══ 页级控制条：去紫底 ══
".page-tools": """{
  position: sticky;
  top: 48px;
  z-index: 45;
  margin-bottom: 22px;
  border-radius: 0;
  background: var(--bg);
  border-bottom: 1px solid var(--border-light);
  padding-bottom: 20px;
}""",
}

def replace_rule(text, selector, body, label=""):
    """把 `selector ` 后紧跟的规则体替换为 body（**保留选择器**）。
    ⚠️ 首次实现曾把整段（含选择器）一起替换成 body，导致 76 条规则变成裸 `{...}`、
    CSS 解析器整体丢弃 —— 这是必须保留选择器的原因，勿改成 subn(body)。"""
    pat = re.compile(r'(?m)^(' + re.escape(selector) + r')\s*\{[^{}]*\}')
    new, n = pat.subn(lambda m: m.group(1) + ' ' + body, text, count=1)
    return new, (n > 0)

warns = []
for sel, body in RULES.items():
    src, ok = replace_rule(src, sel, body)
    if not ok:
        warns.append(sel)

F.write_text(src, encoding="utf-8")
print(f"OK 组件规则替换完成：命中 {len(RULES)-len(warns)}/{len(RULES)}")
if warns:
    print("未命中（需人工确认）:")
    for w in warns:
        print("  -", w)
print("行数:", src.count("\n")+1, " 字节:", len(src.encode('utf-8')))
