# -*- coding: utf-8 -*-
"""2026-10-14 用户三项前端改动（一次落盘）。

① 数据仓库(tab-warehouse)：删除「② 四源取数状态」卡；原「③ 指标总览」升为「②」。
② 基本面量化(tab-fund)：删除「价格 vs 仓单库存」小标题 + 说明 + #chart-f-pi 容器。
③ 基本面量化：把「研报基本面指标」七类口径由「七张 .card 平铺」整合成**一个折叠块**
   （默认收起）—— 改 App._renderResearchSection + 新增 App.toggleResearchSection。

【实现纪律（都是本项目踩过的坑）】
· 行级定位而非硬编码行号：模板 CRLF + 1 空格缩进，锚点取"含唯一关键字的那一行"
  再按相对偏移操作，避免手工转录长文本出错。
· 多行锚点**必须**用 \\r\\n 连接（`Path.read_text()` 会做换行转换 ⇒ 用 read_bytes）。
· div 配平/残留检查**必须剥注释**（注释里会照抄 `<div id="tab-gtja">` 这类字面量）。
· `_SEC_IDX_VER` **必须 +1**（tab 内卡片有增删）。
· 折叠只用 `max-height` + `overflow:hidden`，**不用 display:none** ——
  否则里面的 ECharts init 出 0×0 空白画布（模板 ③ 的注释里已记着同一条教训）。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


class Edit:
    """基于"唯一关键字行 + 相对偏移"的行级编辑器。"""

    def __init__(self, lines: list):
        self.lines = lines
        self.log: list = []

    def find(self, key: str, nth: int = 1) -> int:
        hits = [i for i, l in enumerate(self.lines) if key in l]
        assert len(hits) >= nth, "关键字 %r 只命中 %d 处（需第 %d 处）" % (key, len(hits), nth)
        return hits[nth - 1]

    def replace(self, tag: str, key: str, new_lines: list, nth: int = 1) -> None:
        i = self.find(key, nth)
        old = self.lines[i]
        self.lines[i:i + 1] = new_lines
        self.log.append(("替换", tag, old.strip()[:60], len(new_lines)))

    def delete(self, tag: str, key: str, back: int, fwd: int, nth: int = 1) -> None:
        """删 [i-back, i+fwd]（含端点）。"""
        i = self.find(key, nth)
        a, b = i - back, i + fwd
        assert a >= 0 and b < len(self.lines), "范围越界"
        removed = self.lines[a:b + 1]
        del self.lines[a:b + 1]
        self.log.append(("删除", tag, "%d 行: %s" % (len(removed), removed[0].strip()[:50]), 0))

    def insert_after(self, tag: str, key: str, new_lines: list, nth: int = 1) -> None:
        i = self.find(key, nth)
        self.lines[i + 1:i + 1] = new_lines
        self.log.append(("插入", tag, "after: " + self.lines[i].strip()[:50], len(new_lines)))

    def replace_range(self, tag: str, start_key: str, end_key: str, new_lines: list,
                      nth: int = 1) -> None:
        """替换 [含 start_key 的行, 其后首个含 end_key 的行]。"""
        a = self.find(start_key, nth)
        b = next(i for i in range(a, len(self.lines)) if end_key in self.lines[i])
        old_n = b - a + 1
        self.lines[a:b + 1] = new_lines
        self.log.append(("替换区间", tag, "%d 行 -> %d 行" % (old_n, len(new_lines)), len(new_lines)))

    def method_end(self, def_key: str) -> int:
        """按**花括号配平**找方法结束行（不用缩进判据 —— 模板已压平成 1 空格/层）。"""
        s = self.find(def_key)
        depth, started = 0, False
        for i in range(s, len(self.lines)):
            for ch in self.lines[i]:
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
            if started and depth == 0:
                last = self.lines[i].strip()
                assert last in ("}", "},"), "方法配平可疑(末行 %r)" % last
                return i
        raise AssertionError("方法未配平: %s" % def_key)


# ══════════════════════════════════════════════════════════════════
# 新增：折叠块生成代码（替换原 host.innerHTML = keys.map(...) 段）
# ══════════════════════════════════════════════════════════════════
NEW_RENDER = ''' // 【2026-10-14 用户要求】七类口径由「七张 .card 平铺」整合成**一个折叠块**(默认收起)。
 // ⚠ 折叠只用 max-height + overflow:hidden, **不用 display:none** —— 后者会让 ECharts
 //   init 出 0×0 空白画布(展开后仍是白的)。本页 ③ 的注释里记着同一条教训。
 // ⚠ 子标题必须用 <div> 不用 <h3>: 合并卡内多个 h3 会让 _secTitleOf 串名(索引名取第一个)。
 // ⚠ 折叠状态**跨渲染保持**(this._rsOpen): 切品种后重新渲染时若强制收回, 用户每次都要再点一遍。
 const totalPts = keys.reduce((s, k) => s + (standalone[k] || []).length, 0);
 const blocks = keys.map((k, i) => {
 const pts = standalone[k];
 // 单位取点里最常见的非空值(同指标通常同单位)
 const unitCount = {};
 pts.forEach(p => { if (p.unit) unitCount[p.unit] = (unitCount[p.unit] || 0) + 1; });
 const unit = Object.entries(unitCount).sort((a, b) => b[1] - a[1])[0]?.[0] || '';
 return `<div style="margin-top:${i ? 16 : 4}px">`
 + `<div style="font-size:0.8125rem;font-weight:500;margin-bottom:8px">研报${labels[k] || k}${unit ? `(${unit})` : ''} <span class="badge badge-info">${pts.length} 点</span></div>`
 + `<div class="chart-container" id="chart-${P}-r-${k.replace(/_/g, '-')}" style="height:240px"></div>`
 + `</div>`;
 }).join('');
 const wasOpen = !!(this._rsOpen && this._rsOpen[P]); // 折叠状态跨渲染保持
 host.innerHTML = `
<div class="card" style="margin-top:16px">
<div id="${P}-research-head" title="${wasOpen ? '点击收起' : '点击展开'}" style="display:flex;align-items:center;justify-content:space-between;gap:8px;cursor:pointer;user-select:none">
<h3 style="margin:0">研报基本面指标 <span class="badge badge-info">${keys.length} 类 · ${totalPts} 点</span></h3>
<span id="${P}-research-caret" style="font-size:0.8125rem;color:var(--text-muted);white-space:nowrap">${wasOpen ? '收起 ▴' : '展开 ▾'}</span>
</div>
<div style="font-size:0.75rem;color:var(--text-muted);margin-top:4px;line-height:1.7">
七类口径均为<b>研报披露值</b>(非交易所/行情源), 可与上方行情口径交叉验证。
</div>
<div id="${P}-research-body" data-open="${wasOpen ? '1' : '0'}" style="max-height:${wasOpen ? 'none' : '0'};overflow:hidden;transition:max-height .28s ease">
${blocks}
</div>
</div>`;
 // 绑定折叠: 用 addEventListener 而非内联 onclick —— 不依赖全局作用域(与 _qovBuildPills 同风格)
 const head = document.getElementById(`${P}-research-head`);
 if (head) head.onclick = () => this.toggleResearchSection(P);
'''

NEW_TOGGLE = '''
 // 【功能】折叠 / 展开「研报基本面指标」块(2026-10-14 用户要求, 默认收起)。
 // 【为什么用 max-height 而不是 display:none】本块内含 ECharts 折线图 —— display:none 会让
 //   容器尺寸变 0, 图表 init/渲染成 0×0 空白(展开后仍是白的)。max-height:0 + overflow:hidden
 //   只**裁剪显示**, 容器宽高照常 ⇒ 图表一直渲染完好, 展开即见。
 // 【状态跨渲染保持】写 this._rsOpen[P] —— 否则切品种重渲染后又被收回, 每次都要再点。
 // 【参数】pfx: 前缀('f' = 基本面量化页)
 toggleResearchSection(pfx) {
 const P = pfx || 'f';
 const body = document.getElementById(`${P}-research-body`);
 const head = document.getElementById(`${P}-research-head`);
 const caret = document.getElementById(`${P}-research-caret`);
 if (!body) return;
 this._rsOpen = this._rsOpen || {};
 const opened = body.dataset.open === '1';
 if (opened) {
 // 先从"当前实际高度"起步再收到 0, 否则 max-height 从 none 直跳 0 没有过渡动画
 body.style.maxHeight = body.scrollHeight + 'px';
 requestAnimationFrame(() => { body.style.maxHeight = '0px'; });
 body.dataset.open = '0';
 this._rsOpen[P] = false;
 if (caret) caret.textContent = '展开 ▾';
 if (head) head.title = '点击展开';
 } else {
 body.style.maxHeight = body.scrollHeight + 'px';
 body.dataset.open = '1';
 this._rsOpen[P] = true;
 if (caret) caret.textContent = '收起 ▴';
 if (head) head.title = '点击收起';
 // 过渡结束后放开高度上限: 之后内容变化(主题切换/窗口缩放使图变高)不会被裁掉
 setTimeout(() => { if (body.dataset.open === '1') body.style.maxHeight = 'none'; }, 300);
 // 图表在收起时是"被裁剪"而非"未渲染", 尺寸本应正确; 这里再 resize 一次作兜底
 this._resizeAllCharts();
 }
 },
'''


def main() -> int:
    raw = TPL.read_bytes()
    assert raw.count(b"\r\n") > 0, "web_template.html 应为 CRLF"
    h0 = hashlib.md5(raw).hexdigest()
    lines = raw.decode("utf-8").split("\r\n")
    e = Edit(lines)
    print("md5(前) =", h0)
    print()

    # ── ① 数据仓库：删「② 四源取数状态」卡 ────────────────────────────
    print("[1] 数据仓库：删 ② 四源取数状态")
    e.delete("删四源卡", " <h3>② 四源取数状态</h3>", back=2, fwd=4)
    e.replace("①注释③→②",
              "<!-- ③ 指标总览 + 走势图 + 明细数据（2026-09-30 三卡合并, 用户要求）",
              ["<!-- 【2026-10-14 删除】此处原为「② 四源取数状态」卡(容器 #gt-sources, 三态分色)。",
               " 取数状态在下方提示条里已有一句汇总(loadGtja 写入 #gt-note: “四源 N/4 可用（失败 M 个）”),",
               " 信息不丢。",
               " ⚠ App._renderGtjaSources() 与全部 #gt-sources 取值点**保留**(有 `if (!el) return` 守卫,",
               "   零风险) —— 防过度删除惯例, 也便于回退: 恢复本卡即可复原。",
               " ⚠ 原「③ 指标总览」已升为「② 指标总览」; _SEC_IDX_VER 已 +1。 -->",
               " ",
               " <!-- ② 指标总览 + 走势图 + 明细数据（2026-09-30 三卡合并, 用户要求）"])
    e.replace("h3 ③→②", "<h3>③ 指标总览 / 走势图 / 明细",
              [" <h3>② 指标总览 / 走势图 / 明细 <span class=\"badge badge-info\" id=\"gt-ind-badge\"></span></h3>"])

    # ── ② 基本面量化：删「价格 vs 仓单库存」 ──────────────────────────
    print("[2] 基本面量化：删 价格 vs 仓单库存")
    e.delete("删双轴图", ' 价格 vs 仓单库存 <span class="badge badge-info" id="f-pi-badge">',
             back=1, fwd=5)
    i_pi = e.find("价格-库存关联分析 <span class=\"badge badge-info\" id=\"f-analysis-badge\">")
    e.lines[i_pi - 1:i_pi - 1] = [
        " <!-- 【2026-10-14 用户要求删除】此处原为「价格 vs 仓单库存」双轴图",
        " (容器 #chart-f-pi + 徽标 #f-pi-badge)。同数据已在 ④ 仓单块与「价格-库存关联分析」",
        " (Pearson R) 中体现 ⇒ 不重复展示。",
        " ⚠ loadFundAnalysis 与 _renderDashCharts 的取值点均**保留**(piBadge 有守卫;",
        "   _renderDashCharts 按 opts 显式指定容器, 不传就不画) —— 零风险、便于回退。 -->",
    ]
    e.log.append(("插入", "补删图说明", "before: 价格-库存关联分析", 5))

    # ── ③ 折叠块 ─────────────────────────────────────────────────────
    print("[3] 研报基本面指标 → 折叠块")
    e.replace_range("host.innerHTML 改折叠块",
                    " host.innerHTML = keys.map(k => {", " }).join('');",
                    NEW_RENDER.rstrip("\n").split("\n"))
    me = e.method_end(" _renderResearchSection(d, pfx) {")
    e.lines[me + 1:me + 1] = NEW_TOGGLE.rstrip("\n").split("\n")
    e.log.append(("插入", "toggleResearchSection", "after: _renderResearchSection 结束行", 0))

    # ── ④ 注释与配置同步 ─────────────────────────────────────────────
    print("[4] 注释 / 配置 / 版本号同步")
    e.replace("f-research 容器注释",
              "本组先**平铺**于此",
              [" 【2026-10-14】用户要求: 七类整合成**一个折叠块**(默认收起) —— "
               "见 _renderResearchSection / toggleResearchSection。 -->"])
    e.replace("_SEC_ID_NAMES 注释",
              "'f-research': '研报基本面指标',",
              [" 'f-research': '研报基本面指标', // 基本面页: 研报抽取的基差/仓单/开工率等指标"
               " (2026-10-14 起整合为一个**折叠块**, 默认收起)"])
    e.replace("loadFundAnalysis【渲染】注释",
              "// 【渲染】#f-analysis-body / #f-analysis-badge / #f-pi-badge / #chart-f-pi / #f-research",
              [" // 【渲染】#f-analysis-body / #f-analysis-badge / #f-research",
               " // 【2026-10-14】#f-pi-badge / #chart-f-pi 已随「价格 vs 仓单库存」删除(用户要求)。"])
    e.replace("_renderDashCharts 调用",
              "this._renderDashCharts(d, { pi: 'chart-f-pi' });",
              [" this._renderDashCharts(d, {}); // 【2026-10-14】原传 { pi: 'chart-f-pi' } —— "
               "该图已删;",
               " //   本函数按 opts 显式指定容器, 不传就不画(保留调用点便于将来加图)。"])
    e.replace("清空筛选注释", '就会把刚刚看到的四源状态又收回去',
              [" // 就会把刚刚看到的模块又收回去(用户会以为“清空筛选”把页面弄坏了)。"])
    e.replace("_renderGtjaSources 注释", "// 【功能】渲染四源状态块(三态分色",
              [" // 【功能】渲染四源状态块(三态分色) —— ⚠ 【2026-10-14】其容器 #gt-sources 已随卡片",
               " // 删除, 本函数现为**保留的死代码**(首行 `if (!el) return` 守卫, 零风险):",
               " // 防过度删除 + 便于回退(恢复卡片即复原)。"])
    e.replace("loadGtja 渲染注释", "// 【输入】无 【渲染】#gt-variety/#gt-sources/#gt-indicators",
              [" // 【输入】无 【渲染】#gt-variety/#gt-sources(已删, 见下)/#gt-indicators"])
    e.replace("loadGtja 渲染注释2", "// 【渲染】#gt-note / #gt-sources / #gt-indicators",
              [" // 【渲染】#gt-note / #gt-sources(#gt-sources 已删, 取值点保留且守卫)/#gt-indicators"])
    # _SEC_OPEN_BY_TAB：实测 warehouse 页 .card 序列 = [①查询控制, ②四源, ③指标总览, 上传研报, ...]
    #   ⇒ 删掉 ②四源 后 [0,1] 恰好 = ①查询控制 + ②指标总览 —— 正是「卖方数据块的两张卡」,
    #   故**配置值保持不变**, 只刷新注释(否则注释与真实下标不符, 下次改的人会误判)。
    e.replace("_SEC_OPEN_BY_TAB 注释", "// 国君数据：2026-10-08 覆盖矩阵已删除, 剩余两张卡",
              [" // 国君数据(卖方数据块)：2026-10-08 覆盖矩阵已删除。",
               " // 【2026-10-14】② 四源取数状态 卡已按用户要求删除 ⇒ 下标含义刷新为",
               " // 0 = ① 查询控制, 1 = ② 指标总览(原③) —— 仍是「卖方数据块的两张卡」, 故 [0,1] 不变。",
               " // ⚠ 全局平铺(_SEC_FLAT_TABS_ALL)下本配置不生效, 仅在回退到折叠模式时起作用。",
               " // 平铺 —— 都默认展开。此前只默认开矩阵+四源(指标总览收起), 现矩阵没了, 用户要求"])
    e.replace("v20 注释补充", "// v20 (2026-10-08): 国君页「覆盖矩阵」整个删除(用户要求), 剩余 ② 四源状态 / ③ 指标总览",
              [" // v20 (2026-10-08): 国君页「覆盖矩阵」整个删除(用户要求), 剩余 ② 四源状态 / ③ 指标总览",
               " // (⚠ 后 ② 四源状态 亦于 2026-10-14 删除, 见 v26)。"])
    # 版本号 +1（铁律：tab 内卡片有增删）
    e.replace("_SEC_IDX_VER +1", " _SEC_IDX_VER: '25',",
              [" // v26 (2026-10-14): 数据仓库删除「② 四源取数状态」卡 ⇒ 该 tab 索引条目减 1",
               " // (原 ③ 指标总览升为 ②)。基本面页「研报基本面指标」由七张卡整合为一个折叠块",
               " // (卡数减少, 但卡内图 id 不变)。按铁律 +1 触发旧浏览器重建索引。",
               " _SEC_IDX_VER: '26',"])

    # ── 写盘 ─────────────────────────────────────────────────────────
    print()
    print("[5] 改动明细")
    for kind, tag, detail, n in e.log:
        print("    %-6s %-22s %s" % (kind, tag, detail))

    out = "\r\n".join(e.lines)
    TPL.write_bytes(out.encode("utf-8"))
    raw2 = TPL.read_bytes()
    assert raw2.count(b"\r\n") > 0 and (raw2.count(b"\n") - raw2.count(b"\r\n")) == 0, "EOL 被破坏"
    print()
    print("[6] 自检")
    t = raw2.decode("utf-8")
    clean = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    checks = [
        ("四源卡已删(剥注释)", "<h3>② 四源取数状态</h3>" not in clean),
        ("gt-sources 容器已删", 'id="gt-sources"' not in clean),
        ("指标总览升为②", "<h3>② 指标总览 / 走势图 / 明细" in clean),
        ("旧③标题已无", "<h3>③ 指标总览" not in clean),
        ("价格vs仓单库存已删", "价格 vs 仓单库存" not in clean),
        ("chart-f-pi 容器已删", 'id="chart-f-pi"' not in clean),
        ("toggleResearchSection 已定义", t.count(" toggleResearchSection(pfx) {") == 1),
        ("折叠块 head/body id 已生成", "${P}-research-head" in t and "${P}-research-body" in t),
        ("用 max-height 而非 display:none", "max-height:" in t and "rsOpen" in t),
        ("_SEC_IDX_VER = 26", "_SEC_IDX_VER: '26'," in t),
        ("warehouse 配置保持 [0,1]", "'warehouse': [0, 1]," in t),
    ]
    ok = True
    for tag, v in checks:
        print("    %-30s %s" % (tag, "✔" if v else "✘"))
        ok = ok and v
    print()
    print("md5(后) =", hashlib.md5(raw2).hexdigest())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
