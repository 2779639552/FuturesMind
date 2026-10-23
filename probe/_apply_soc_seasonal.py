# -*- coding: utf-8 -*-
"""基本面量化：社会库存 → 季节性图（2026-10-21 用户要求「够多年的图改成季节性」）。

【实测依据】`/api/quant/inventory`
  · 社会库存 2023-10-20 ~ 2026-10-16，约 154 点（3 年）⇒ 够画多年叠加
  · 交易所仓单 2026-04-24 ~ 2026-10-20，118 点（半年）⇒ 只有 1 条线，不参与
  · 基差 259 点/13 个月、研报指标 2 个月、仓单历史 25 交易日 ⇒ 都不够
∴ 本次只改「社会库存」这一处，复刻数据仓库的季节性口径（`_seasonalFromSeries`
  + `_drawGtjaSeasonal`：366 天月份轴 / 6 色年份映射 / 当年粗红线 / 最新点标注）。

【结构变化】
  原：`#chart-f-inv` 一张图 = 上下两栏（上=双口径水平阶梯线，下=双口径变化柱）
  新：`#chart-f-soc`（上，300px）= 社会库存季节性；`#chart-f-inv`（下，240px）= 仓单日频走势
      社会库存不足 2 年（如 BZ/CJ）时：隐藏 `#chart-f-soc`，`#chart-f-inv` 退回原双口径对照。

⚠ CRLF：本文件必须保持 CRLF，写回用 newline=""。
"""
import io

P = r"D:\AgentSense\AgentSense\web_template.html"
CRLF = "\r\n"

src = io.open(P, "r", encoding="utf-8", newline="").read()
before = src


def crlf(s: str) -> str:
    return s.replace("\n", CRLF)


# ── ① 容器：新增 #chart-f-soc，原 #chart-f-inv 降为仓单图 ──────────────────
OLD1 = crlf(
    ' <div style="font-size:0.8125rem;font-weight:500;margin-bottom:8px">\n'
    ' 仓单库存 · 社会库存双口径 <span class="badge badge-info" id="f-inv-badge"></span>\n'
    ' </div>\n'
    ' <div class="chart-container" id="chart-f-inv" style="height:360px"></div>\n')
NEW1 = crlf(
    ' <div style="font-size:0.8125rem;font-weight:500;margin-bottom:8px">\n'
    ' 库存：社会库存季节性 · 交易所仓单走势 <span class="badge badge-info" id="f-inv-badge"></span>\n'
    ' </div>\n'
    ' <!-- 【2026-10-21 用户要求「够多年的图改成季节性图」】\n'
    '      社会库存 2023-10 起约 154 点（3 年）⇒ 季节性图；交易所仓单只有半年 ⇒ 保留日频走势。\n'
    '      不足 2 个年份的品种隐藏上面那张、下面退回原双口径对照（见 _renderFundInventory）。 -->\n'
    ' <div class="chart-container" id="chart-f-soc" style="height:300px"></div>\n'
    ' <div id="f-inv-ck-title" style="font-size:0.75rem;color:var(--text-muted);margin:10px 0 6px">交易所仓单 · 日频走势</div>\n'
    ' <div class="chart-container" id="chart-f-inv" style="height:240px"></div>\n')
assert src.count(OLD1) == 1, "① 容器锚点命中 %d" % src.count(OLD1)
src = src.replace(OLD1, NEW1, 1)

# ── ② 方法头注释补「⓪ 季节性」这一形态 ─────────────────────────────────
OLD2 = crlf(
    '// 【渲染】#chart-f-inv。三种形态（**数据齐就画齐，缺一侧就如实降级，绝不硬凑**）：\n')
NEW2 = crlf(
    '// 【渲染】#chart-f-soc（社会库存季节性，≥2 个年份时）+ #chart-f-inv（仓单/双口径）。\n'
    '//   ⓪ 社会库存 ≥2 个年份 → 上方画**季节性图**（多年叠加），下方只画仓单日频走势；\n')
assert src.count(OLD2) == 1, "② 注释锚点命中 %d" % src.count(OLD2)
src = src.replace(OLD2, NEW2, 1)

# ── ③ 空数据时也要把季节性图清干净 ─────────────────────────────────────
OLD3 = crlf(
    "  c.setOption({ backgroundColor: 'transparent',\n"
    "   title: { text: (s && s.error) || (so && so.error) || '无双口径数据', left: 'center', top: 'middle',\n"
    "    textStyle: { color: textColor, fontSize: 12, fontWeight: 'normal' } } }, true);\n"
    "  return;\n"
    " }\n")
NEW3 = crlf(
    "  c.setOption({ backgroundColor: 'transparent',\n"
    "   title: { text: (s && s.error) || (so && so.error) || '无双口径数据', left: 'center', top: 'middle',\n"
    "    textStyle: { color: textColor, fontSize: 12, fontWeight: 'normal' } } }, true);\n"
    "  const _cs0 = this._initChart('chart-f-soc');\n"
    "  if (_cs0) { _cs0.clear(); _cs0.setOption({ backgroundColor: 'transparent',\n"
    "   title: { text: '无社会库存序列', left: 'center', top: 'middle',\n"
    "    textStyle: { color: textColor, fontSize: 12, fontWeight: 'normal' } } }, true); }\n"
    "  return;\n"
    " }\n")
assert src.count(OLD3) == 1, "③ 空数据锚点命中 %d" % src.count(OLD3)
src = src.replace(OLD3, NEW3, 1)

# ── ④ 插入季节性渲染块 + socForDual ────────────────────────────────────
OLD4 = crlf(
    " const CK_C = CHART.brand;      // 仓单 = 主序列色\n")
NEW4 = crlf(
    " // ── ⓪ 社会库存季节性（2026-10-21 用户要求：够多年的图改成季节性）──────────────\n"
    " //   实测社会库存 2023-10 起 ≈154 点（3 年），够画多年叠加；交易所仓单只有半年 ⇒ 不参与。\n"
    " //   ⚠ 不足 2 个年份就不画：1 条线的\"季节性图\"与普通折线没有区别，反而多占 300px。\n"
    " //   ⚠ 复刻数据仓库同一套口径（366 天月份轴 / 6 色年份映射 / 当年粗红线 / 最新点红框）。\n"
    " const socSeason = this._seasonalFromSeries(socRows.map(r => [r.date, r.qty]));\n"
    " const socYearN = Object.keys(socSeason.years || {}).length;\n"
    " const invSeason = socYearN >= 2;\n"
    " const socEl = document.getElementById('chart-f-soc');\n"
    " if (invSeason) {\n"
    "  if (socEl) socEl.style.display = '';\n"
    "  const cs = this._initChart('chart-f-soc');\n"
    "  if (cs) this._drawGtjaSeasonal(cs, { label: (so && so.label) || '社会库存',\n"
    "   unit: (so && so.unit) || '' }, socSeason);\n"
    " } else if (socEl) {\n"
    "  socEl.style.display = 'none';\n"
    " }\n"
    " // 小标题随形态变：有季节性时下面只画仓单；没有时下面退回双口径对照\n"
    " const _ckTitle = document.getElementById('f-inv-ck-title');\n"
    " if (_ckTitle) _ckTitle.textContent = invSeason\n"
    "  ? '交易所仓单 · 日频走势'\n"
    "  : '仓单库存 · 社会库存双口径（不足 2 年，按走势对照）';\n"
    " // 社会库存已用季节性表达 ⇒ 下方的图不再画它（避免同一序列两种画法并存）\n"
    " const socForDual = invSeason ? [] : socRows;\n"
    " const CK_C = CHART.brand;      // 仓单 = 主序列色\n")
assert src.count(OLD4) == 1, "④ 插入锚点命中 %d" % src.count(OLD4)
src = src.replace(OLD4, NEW4, 1)

# ── ⑤ 分支②判据改用 socForDual ────────────────────────────────────────
OLD5 = " if (ckRows.length && !socRows.length) {"
NEW5 = " if (ckRows.length && !socForDual.length) {"
assert src.count(OLD5) == 1, "⑤ 分支②锚点命中 %d" % src.count(OLD5)
src = src.replace(OLD5, NEW5, 1)

# ── ⑥ 分支③：已有季节性时只提示"无仓单"，不再画社会库存阶梯线 ─────────
OLD6 = crlf(
    " if (!ckRows.length) {\n"
    "  const dates = socRows.map(r => r.date);\n")
NEW6 = crlf(
    " if (!ckRows.length) {\n"
    "  if (invSeason) {\n"
    "   c.clear();\n"
    "   c.setOption({ backgroundColor: 'transparent',\n"
    "    title: { text: '该品种无交易所仓单（社会库存见上方季节性图）', left: 'center', top: 'middle',\n"
    "     textStyle: { color: textColor, fontSize: 12, fontWeight: 'normal' } } }, true);\n"
    "   return;\n"
    "  }\n"
    "  const dates = socRows.map(r => r.date);\n")
assert src.count(OLD6) == 1, "⑥ 分支③锚点命中 %d" % src.count(OLD6)
src = src.replace(OLD6, NEW6, 1)

# ── ⑦ 分支①（不足 2 年时的双口径对照）用 socForDual ────────────────────
OLD7 = crlf(
    "  while (k < socRows.length && String(socRows[k].date) <= String(dates[i])) { last = socRows[k][key]; k++; }\n")
NEW7 = crlf(
    "  while (k < socForDual.length && String(socForDual[k].date) <= String(dates[i])) { last = socForDual[k][key]; k++; }\n")
assert src.count(OLD7) == 1, "⑦ 阶梯锚点命中 %d" % src.count(OLD7)
src = src.replace(OLD7, NEW7, 1)

OLD8 = crlf(" socRows.forEach(r => {\n  if (r.change === null || r.change === undefined) return;\n")
NEW8 = crlf(" socForDual.forEach(r => {\n  if (r.change === null || r.change === undefined) return;\n")
assert src.count(OLD8) == 1, "⑧ 变化柱锚点命中 %d" % src.count(OLD8)
src = src.replace(OLD8, NEW8, 1)

assert src != before, "没有任何改动"
assert src.count("\n") == src.count(CRLF), "CRLF 被破坏: LF=%d CRLF=%d" % (
    src.count("\n"), src.count(CRLF))
io.open(P, "w", encoding="utf-8", newline="").write(src)
print("patched ok")
