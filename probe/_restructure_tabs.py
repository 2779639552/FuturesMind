# -*- coding: utf-8 -*-
"""结构调整：tab 层级重组（2026-10-14 用户拍板）

═══ 用户需求与拍板 ═══
① 「数据仓库」大模块下并入三模块：**卖方数据（原「国君数据」改名）** / 上传研报 / 自传数据 / 研报库
   —— 其中「研报库」「自传数据」**已在** warehouse 内；实际动作 = 把 `tab-gtja` 整块并入并改名。
   **顺序（用户拍板）**：卖方数据 → 上传研报 → 自传数据 → 研报库
② 「基本面量化」下并入「仓单分析」⇒ `tab-warrant` 整块并入 `tab-fund`，**追加到最后**
③ 「情绪数据」位置 → 移到「基本面量化」后、「外文研报」前
④ **并入方的控制条降为页内卡**（用户拍板）：去掉 `class="page-tools"`、保留 id
   —— 满足项目铁律「一个 tab 恰好 1 个 .page-tools」
   （注：`.page-tools` 已是 `position:static`，视觉几乎无差别）

新 nav 顺序：run → research → quant → fund → **sentiment** → foreign → warehouse（gtja/warrant 移除）

═══ 关键技术点（本次实测得出）═══
★ **div 配平必须剥掉注释**：注释里写了 `此处 <div id="tab-gtja">` 这类字面量，
  不剥注释会误报"缺一个 </div>"（本次首查就中了这个假警报）。
  剥注释后 `<main>` 与全部 10 个 tab 块**净 0**（结构本来是干净的）。
★ 容器边界用「剥注释后的 div 配平扫描」求，不用 `find('</div>')` 也不靠下一个 tab 容器
  （间隙里有大段前导注释）。
★ 保留全部 `gt-*` / `wt-*` 容器 id 与 App 方法：`tests/js/gtja_module_harness.mjs`
  等按 id 断言渲染行为，改名即假红。
★ 后端接口一律不动（项目防过度删除惯例）。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


# ══════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════
def strip_comments(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def container_end(h: str, i: int) -> int | None:
    """从容器开标签起点 i 找 div 配平结束位置（**跳过注释**，注释里可能有 <div 字面量）。"""
    d = 0
    k = i
    pat = re.compile(r"<div\b|</div>|<!--.*?-->", re.S)
    while k < len(h):
        m = pat.search(h, k)
        if not m:
            return None
        tok = m.group(0)
        if tok.startswith("<!--"):
            pass                      # 注释整体跳过，不计入深度
        elif tok == "<div":
            d += 1
        else:
            d -= 1
            if d == 0:
                return m.end()
        k = m.end()
    return None


def span_of(h: str, tab: str) -> tuple[int, int]:
    m = re.search(r'<div id="tab-%s" class="tab(?: active)?">' % tab, h)
    assert m, "未找到 tab-%s 容器" % tab
    e = container_end(h, m.start())
    assert e, "tab-%s 配平失败" % tab
    return m.start(), e


def prev_comment_chain(h: str, s: int) -> int:
    """向前跳过**连续**的注释块（本文件用两段式前导：分隔线注释 + 说明注释）。

    判据：两个注释之间**只有空白**才算"连续"；一旦中间夹了 HTML（如 `</div>` 之外还有卡片），
    说明那已是上一节的内容，停止回退。
    """
    c = s
    for _ in range(4):                      # 最多回退 4 段，防跑飞
        k = h.rfind("<!--", 0, c)
        if k == -1:
            break
        end = h.find("-->", k)
        if end == -1:
            break
        if h[end + 3: c].strip() == "":     # 中间只有空白 ⇒ 属于同一前导块
            c = k
        else:
            break
    return c


def cut_block(h: str, tab: str) -> tuple[int, int, str]:
    """返回 (块起点含**全部**前导注释, 块终点, 前导注释文本)。"""
    s, e = span_of(h, tab)
    c = prev_comment_chain(h, s)
    assert c != s, "未找到 tab-%s 的前导注释" % tab
    head = h[c:s]
    return c, e, head


def main() -> int:
    raw = TPL.read_bytes()
    crlf = raw.count(b"\r\n") > 0
    assert crlf and raw.count(b"\n") - raw.count(b"\r\n") == 0, "web_template.html 应为纯 CRLF"
    t = raw.decode("utf-8").replace("\r\n", "\n")     # ★ 在 LF 域内操作，写回时还原
    h0 = hashlib.md5(raw).hexdigest()
    if 'data-tab="gtja"' not in t:
        print("⚠️ 已是目标结构（nav 无 gtja），跳过")
        return 0

    # ── 1. 定位 ──────────────────────────────────────────────────
    print("[1] 定位")
    gs, ge, ghead = cut_block(t, "gtja")          # gtja 块（含前导注释）
    ws, we, whead = cut_block(t, "warrant")
    whs, whe = span_of(t, "warehouse")
    fs, fe = span_of(t, "fund")
    print("    gtja     块 [%6d, %6d)  含前导注释" % (gs, ge))
    print("    warrant  块 [%6d, %6d)  含前导注释" % (ws, we))
    print("    warehouse 容器 [%6d, %6d)" % (whs, whe))
    print("    fund      容器 [%6d, %6d)" % (fs, fe))
    for nm, s, e in (("gtja", gs, ge), ("warrant", ws, we), ("warehouse", whs, whe), ("fund", fs, fe)):
        net = len(re.findall(r"<div\b", strip_comments(t[s:e]))) - len(
            re.findall(r"</div>", strip_comments(t[s:e])))
        assert net == 0, "%s 剥注释后净 %+d（应 0）—— 边界不对" % (nm, net)
    print("    ✔ 四块剥注释后 div 均配平")

    # ── 2. 构造迁移内容 ──────────────────────────────────────────
    print("[2] 构造迁移内容")
    G_OPEN = '<div id="tab-gtja" class="tab">'
    W_OPEN = '<div id="tab-warrant" class="tab">'
    g_inner = t[gs + 0:ge]                        # 含前导注释，稍后整体替换掉
    g_body = t[t.index(G_OPEN, gs) + len(G_OPEN): ge - len("</div>")].lstrip("\n")
    w_body = t[t.index(W_OPEN, ws) + len(W_OPEN): we - len("</div>")].lstrip("\n")
    # 控制条降为页内卡：去掉 class="page-tools"（保留 id，harness 按 id 断言）
    g_body = g_body.replace('<div id="tab-gtja-tools" class="page-tools">',
                            '<div id="tab-gtja-tools">', 1)
    w_body = w_body.replace('<div id="tab-warrant-tools" class="page-tools">',
                            '<div id="tab-warrant-tools">', 1)
    assert 'class="page-tools"' not in g_body, "gtja 控制条降级失败"
    assert 'class="page-tools"' not in w_body, "warrant 控制条降级失败"
    # 改名（只改用户可见的那处标题；历史注释保留原词以便追溯）
    assert '<b>国君数据(国泰君安期货 VIP cloudApi)</b>' in g_body
    g_body = g_body.replace('<b>国君数据(国泰君安期货 VIP cloudApi)</b>',
                            '<b>卖方数据(国君数据 · 国泰君安期货 VIP cloudApi)</b>', 1)
    print("    ✔ 控制条已降级为页内卡；标题已改名「卖方数据」")

    # 迁移说明注释（替代原容器位置）
    G_NOTE = """<!-- 【2026-10-14 结构重组】原「国君数据」页已整块并入「数据仓库」页并**改名「卖方数据」**,
 位置: 数据仓库页内**第一块**（顺序 卖方数据 → 上传研报 → 自传数据 → 研报库）。
 为什么: 用户要求「在数据仓库大模块下并入卖方数据/研报库/自传数据」——
 后两者本就在数据仓库页内, 故只需把本页改名迁入。
 迁入时把控制条 `<div id="tab-gtja-tools">` 的 `class="page-tools"` **去掉**
 （用户拍板「并入方控制条降为页内卡」）: 一个 tab 只许一个 .page-tools。
 全部 `gt-*` 容器 id 与 App 方法（loadGtja/_renderGtja*）**原样保留** ——
 tests/js/gtja_module_harness.mjs 按 id 断言渲染行为。
 后端接口 /api/gtja/* **未动**（防过度删除惯例）。
 备份: backup/web_template_pre_tab_restructure_<ts>.html 与
       backup/__restructure_original.html（改动前的完整原文, 供整体回退）
 回退: 把本注释换回原 <div id="tab-gtja" class="tab">…</div> 整块即可。 -->"""

    W_NOTE = """<!-- 【2026-10-14 结构重组】原「仓单分析」页已整块并入「基本面量化」页,
 位置: 基本面量化页内**最后一块**（④ 虚实盘比与逼仓识别 之后）。
 为什么: 用户要求「在基本面量化大模块下并入仓单分析模块」。
 迁入时把控制条 `<div id="tab-warrant-tools">` 的 `class="page-tools"` **去掉**
 （用户拍板「并入方控制条降为页内卡」）: 一个 tab 只许一个 .page-tools。
 全部 `wt-*` 容器 id 与 App 方法（loadWarrant/_renderWarrant*）**原样保留**。
 后端接口 /api/warrant/* **未动**（防过度删除惯例）。
 备份见 backup/web_template_pre_tab_restructure_<ts>.html。
 回退: 把本注释换回原 <div id="tab-warrant" class="tab">…</div> 整块即可。 -->"""

    WA_NOTE = ("<!-- 【2026-10-14 结构重组】本页现含四个模块, 顺序: "
               "卖方数据(原「国君数据」页, 已整块并入并改名) → 上传研报 → 自传数据 → 研报库。"
               " 原独立 tab「国君数据」(tab-gtja) 已删除, 其控制条已降为页内卡; "
               "后端 /api/gtja/* 未动。详见原位置留下的迁移说明。 -->")
    F_NOTE = ("<!-- 【2026-10-14 结构重组】本页现含「仓单分析」模块(原独立 tab「仓单分析」"
              "已整块并入, **追加在 ④ 虚实盘比与逼仓识别 之后**), 其控制条已降为页内卡; "
              "后端 /api/warrant/* 未动。详见原位置留下的迁移说明。 -->")

    # ── 3. 应用替换（每处断言命中 1 次）─────────────────────────
    print("[3] 应用替换")
    edits: list[tuple[str, str, str, int]] = []

    # 3a. warehouse：开标签后插入 gtja 正文（作为第 1 块）+ 更新前导注释
    wh_open = '<div id="tab-warehouse" class="tab">'
    edits.append(("warehouse 插入卖方数据",
                  wh_open + "\n",
                  wh_open + "\n" + g_body.rstrip("\n") + "\n\n", 1))

    # 3b. fund：容器闭合前插入 warrant 正文（追加到最后）
    fund_tail = t[fe - 120:fe]                      # 唯一尾部锚点(取长一点保唯一)
    assert fund_tail.endswith("</div>")
    edits.append(("fund 追加仓单分析",
                  fund_tail,
                  fund_tail[:-len("</div>")] + w_body.rstrip("\n") + "\n</div>", 1))

    # 3c. 删 gtja 块（含前导注释）→ 迁移说明
    edits.append(("删 gtja 块并留说明", g_inner, G_NOTE, 1))
    # 3d. 删 warrant 块（含前导注释）→ 迁移说明
    edits.append(("删 warrant 块并留说明", t[ws:we], W_NOTE, 1))

    # 3e. nav 重排
    old_nav = ('<button class="active" data-tab="run">运行分析</button>\n'
               ' <button data-tab="research">研报</button>\n'
               ' <button data-tab="sentiment">情绪数据</button>\n'
               ' <button data-tab="quant">技术面量化</button>\n'
               ' <button data-tab="fund">基本面量化</button>\n'
               ' <button data-tab="warrant">仓单分析</button>\n'
               ' <button data-tab="foreign">外文研报</button>\n'
               ' <button data-tab="warehouse">数据仓库</button>\n'
               ' <button data-tab="gtja">国君数据</button>\n'
               ' <button data-tab="variety" style="display:none">品种数据库</button>')
    new_nav = ('<button class="active" data-tab="run">运行分析</button>\n'
               ' <button data-tab="research">研报</button>\n'
               ' <button data-tab="quant">技术面量化</button>\n'
               ' <button data-tab="fund">基本面量化</button>\n'
               ' <!-- 【2026-10-14】情绪数据移到基本面量化之后、外文研报之前(用户要求) -->\n'
               ' <button data-tab="sentiment">情绪数据</button>\n'
               ' <button data-tab="foreign">外文研报</button>\n'
               ' <button data-tab="warehouse">数据仓库</button>\n'
               ' <button data-tab="variety" style="display:none">品种数据库</button>')
    edits.append(("nav 重排", old_nav, new_nav, 1))

    # 3f. switchTab：删两处旧分支，改两处目标分支
    edits.append(("删 warrant 分支",
                  " if (t === 'warrant') this.loadWarrant(); // 仓单分析(2026-09-14 新增)\n", "", 1))
    edits.append(("删 gtja 分支",
                  " if (t === 'gtja') this.loadGtja(); // 国君数据(2026-09-29 新增): 四源统一快照\n", "", 1))
    edits.append(("warehouse 分支加 loadGtja",
                  " if (t === 'warehouse') this.loadWarehouse();",
                  " if (t === 'warehouse') { this.loadWarehouse(); this.loadGtja(); }"
                  " // 【2026-10-14】卖方数据已并入本页, 一并加载", 1))
    edits.append(("fund 分支加 loadWarrant",
                  " if (t === 'fund') this.loadFund();",
                  " if (t === 'fund') { this.loadFund(); this.loadWarrant(); }"
                  " // 【2026-10-14】仓单分析已并入本页, 一并加载", 1))

    # 3g. Ctrl+N 数组 + 上界
    edits.append(("Ctrl+N 数组",
                  "const tabs = ['run', 'research', 'sentiment', 'quant', 'fund', 'warrant', 'foreign', 'warehouse', 'gtja'];",
                  "const tabs = ['run', 'research', 'quant', 'fund', 'sentiment', 'foreign', 'warehouse'];",
                  1))
    edits.append(("Ctrl+N 上界 9→7",
                  "if (e.ctrlKey && e.key >= '1' && e.key <= '9') {",
                  "if (e.ctrlKey && e.key >= '1' && e.key <= '7') {", 1))

    # 3h. `_SEC_OPEN_BY_TAB`：'gtja' 键随页面迁到 warehouse
    edits.append(("默认展开配置键改 warehouse",
                  "'gtja': [0, 1],", "'warehouse': [0, 1],", 1))

    # 3i. 两个目标页的前导注释同步（写清现在含哪些模块）
    # 注：warehouse/fund 用的是**单段式** `<!-- ====== 标签页 N: xx ====== ... -->`，
    #     没有 `TAB: Warehouse` 分隔线（那是 quant/warrant 等页才有的两段式）。
    edits.append(("warehouse 前导注释",
                  "<!-- ============ 标签页 10: 数据仓库 ============",
                  WA_NOTE + "\n<!-- ============ 标签页 10: 数据仓库 ============", 1))
    edits.append(("fund 前导注释",
                  "<!-- ============ 标签页: 基本面量化 ============",
                  F_NOTE + "\n<!-- ============ 标签页: 基本面量化 ============", 1))

    out = t
    for tag, old, new, want in edits:
        got = out.count(old)
        assert got == want, "[%s] 锚点命中 %d（期望 %d）" % (tag, got, want)
        out = out.replace(old, new, 1)
        print("    ✔ %-26s (命中 %d)" % (tag, want))

    # ── 4. 写回（还原 CRLF）────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    bak = ROOT / "backup" / ("web_template_pre_tab_restructure_%s.html" % ts)
    shutil.copy2(TPL, bak)
    orig = ROOT / "backup" / ("__restructure_original_%s.html" % ts)
    shutil.copy2(TPL, orig)
    print("\n[4] 已备份 -> backup/%s / backup/%s" % (bak.name, orig.name))

    TPL.write_bytes(out.replace("\n", "\r\n").encode("utf-8"))
    nb = TPL.read_bytes()
    assert nb.count(b"\n") - nb.count(b"\r\n") == 0, "EOL 被破坏"

    # ── 5. 自检 ────────────────────────────────────────────────
    print("[5] 自检")
    nn = TPL.read_bytes().decode("utf-8").replace("\r\n", "\n")
    checks = [
        ("nav 无 gtja", 'data-tab="gtja"' not in nn),
        ("nav 无 warrant", 'data-tab="warrant"' not in nn),
        ("tab-gtja 容器已删", '<div id="tab-gtja" class="tab">' not in nn),
        ("tab-warrant 容器已删", '<div id="tab-warrant" class="tab">' not in nn),
        ("卖方数据标题在位", "卖方数据(国君数据 · 国泰君安期货 VIP cloudApi)" in nn),
        ("gt-* id 保留", all(k in nn for k in ("gt-variety", "gt-indicators", "gt-chart", "gt-table", "gt-sources"))),
        ("wt-* id 保留", all(k in nn for k in ("wt-product", "wt-scope", "wt-date"))),
        ("App 方法保留", all(k in nn for k in ("loadGtja", "loadWarrant", "_renderGtjaSources", "loadGtjaOverview"))),
        ("switchTab 已接", "this.loadWarehouse(); this.loadGtja();" in nn and "this.loadFund(); this.loadWarrant();" in nn),
        ("Ctrl+N 7 项", "'run', 'research', 'quant', 'fund', 'sentiment', 'foreign', 'warehouse'" in nn),
        ("上界已改 7", "e.key <= '7'" in nn),
    ]
    bad = 0
    for name, ok in checks:
        bad += 0 if ok else 1
        print("    %s %s" % ("✔" if ok else "✘", name))

    # tab 容器与 page-tools 计数
    tabs = re.findall(r'<div id="tab-([a-z]+)" class="tab(?: active)?">', nn)
    print("    留下的 tab 容器: %s" % " ".join(tabs))
    print("    各页 page-tools 数:")
    for tb in ("warehouse", "fund"):
        s, e = span_of(nn, tb)
        print("      %-10s %d" % (tb, nn[s:e].count('class="page-tools"')))
        assert nn[s:e].count('class="page-tools"') <= 1, "%s 出现多个 page-tools" % tb

    # 全局配平（剥注释）
    mi, me = nn.index("<main>"), nn.index("</main>")
    c = strip_comments(nn[mi:me])
    net = len(re.findall(r"<div\b", c)) - len(re.findall(r"</div>", c))
    print("    <main> 剥注释后 div 净 = %+d（应 0）" % net)
    bad += 0 if net == 0 else 1

    print("\n    md5 %s -> %s" % (h0[:8], hashlib.md5(TPL.read_bytes()).hexdigest()[:8]))
    if bad:
        print("\n✘ 自检 %d 项失败" % bad)
        return 2
    print("\n✔ 结构调整已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
