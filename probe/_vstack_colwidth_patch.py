# -*- coding: utf-8 -*-
"""总表列宽重排：纵向排列省下的宽度还给「一句话核心观点」（2026-10-19）。

【为什么】实测观点列 441px ≈ 每行 28 字 ⇒ 4 行仅 112 字，而目标口径是 60~120 字
        （120 字会刚好触发"展开"，等于白放宽）。纵向排列后「发行方/方向」只需一个 chip 宽。
【★ 必须限定到总表】这些 nth-child 规则原是**全局**的（.report-content table td:nth-child(n)），
        直接改会把第二节「各家观点与逻辑」等**列序完全不同**的表一起压窄 ⇒ 静默破坏别的表。
        故给总表动态加 .rv-views-tbl，用更高特异性覆盖。
【EOL】web_template.html 为 CRLF。
"""
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 非纯 CRLF"
t = raw.decode("utf-8").replace("\r\n", "\n")

applied = []


def sub(old, new, label):
    global t
    n = t.count(old)
    assert n == 1, "★ [%s] 锚点命中 %d 次" % (label, n)
    t = t.replace(old, new, 1)
    applied.append(label)


# ── A. CSS：总表专属列宽（特异性 0,3,1 > 全局 0,2,1）────────────────────────────
ANCHOR = """.report-content table td.rv-vstack { vertical-align:top; }"""
NEW = """.report-content table td.rv-vstack { vertical-align:top; }
/* 【列宽重排 2026-10-19】纵向排列后「发行方 / 方向」只需一个 chip 宽 ⇒ 收窄，
   余量给「一句话核心观点」（目标口径 60~120 字 ≈ 4 行 × 30 字）。
   ⚠ 必须限定 .rv-views-tbl（总表专属，由 _decorateViewTable 动态加）：
     nth-child 规则是全局的，直接改会把第二节「各家观点与逻辑」等**列序不同**的表一起压窄。
   原值：名称 120 / 发行方 110 / 方向 88 / 置信度 78。 */
.report-content table.rv-views-tbl th:nth-child(2), .report-content table.rv-views-tbl td:nth-child(2) { width: 96px; }
.report-content table.rv-views-tbl th:nth-child(3), .report-content table.rv-views-tbl td:nth-child(3) { width: 100px; }
.report-content table.rv-views-tbl th:nth-child(4), .report-content table.rv-views-tbl td:nth-child(4) { width: 68px; }
.report-content table.rv-views-tbl th:nth-child(5), .report-content table.rv-views-tbl td:nth-child(5) { width: 90px; }"""
sub(ANCHOR, NEW, "A. 总表专属列宽（观点列 +约 42px）")

# ── B. JS：标记总表（必须在 clamp 测量**之前**加，宽度会影响"是否被裁"）──────────
OLD = """if (iDir < 0 || iConf < 0) return;
if (tbl.dataset) tbl.dataset.vizDone = '1';"""
NEW2 = """if (iDir < 0 || iConf < 0) return;
// 【2026-10-19】打总表标记：列宽重排规则只认这个 class，避免波及列序不同的其它表格。
// ⚠ 必须在 _clampLongCell 测量**之前**加 —— 列宽变了，"4 行是否放得下"的结论也会变。
if (tbl.classList) tbl.classList.add('rv-views-tbl');
if (tbl.dataset) tbl.dataset.vizDone = '1';"""
sub(OLD, NEW2, "B. 动态标记 .rv-views-tbl")

out = t.replace("\n", "\r\n").encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "出口 EOL 破坏"
P.write_bytes(out)
print("  已应用 %d 处：" % len(applied))
for x in applied:
    print("   ✔", x)
