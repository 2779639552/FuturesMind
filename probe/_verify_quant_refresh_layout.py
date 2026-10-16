# -*- coding: utf-8 -*-
"""技术面量化两项改动的**只读**验收脚本（可反复重跑，不修改任何文件）。

A. 总览：后端缓存 15 min + 前端每 15 分钟自动刷新
B. 详情排版：K 线缩到一半宽（左半边）+ 右半边技术指标

用法：./venv/Scripts/python.exe probe/_verify_quant_refresh_layout.py
"""
from __future__ import annotations

import glob
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QM = ROOT / "quant_module.py"
TPL = ROOT / "web_template.html"
PYEXE = ROOT / "venv/Scripts/python.exe"

PASS = FAIL = 0
FAILS: list = []


def ck(cond, label, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % label)
    else:
        FAIL += 1
        FAILS.append(label)
        print("  ✘ %s %s" % (label, extra))


def strip_comments(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def main() -> int:
    qm = QM.read_text(encoding="utf-8")
    h = TPL.read_bytes().decode("utf-8")
    assert h.count("\r\n") > 0, "web_template.html 应为 CRLF"
    clean = strip_comments(h)

    # ── A. 15 分钟刷新 + 缓存 ────────────────────────────────────────
    print("=== A. 总览：15 分钟刷新 + 缓存 ===")
    ck("_TTL_OVERVIEW = 900" in qm, "后端缓存 15 min")
    ck("15 min" in qm[qm.index("_TTL_OVERVIEW = 900"):][:200] or "15 min" in qm,
       "TTL 注释写明了 15 min")
    ck("_QOV_REFRESH_MS: 15 * 60 * 1000," in h, "前端周期常量 = 15 min")
    ck("this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;" in h,
       "计算完成后排期 15 min 后自动重取")
    ck("setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_MS);" in h,
       "定时器用 _QOV_REFRESH_MS（不是写死数字）")
    ck("setTimeout(() => this.loadQuantOverview(), 3000);" in h,
       "★ 未算完时仍保留 3s 渐进轮询（否则首屏要等 15 分钟）")
    ck("this._qovNextAt = 0;   // 离开总览" in h, "离开总览时清掉下次刷新时刻")
    ck("每 15 分钟自动刷新（下次 " in h, "进度条显示下次刷新时刻")
    ck("之后走 15 分钟缓存" in h and "之后走 5 分钟缓存" not in h, "首屏占位文案已同步")
    # 自动刷新必须挂在**总览**上，不是详情（详情切走时 _stopQuantOverview 会清掉）
    i_stop = h.index("_stopQuantOverview() {")
    ck("clearTimeout(this._qovPollTimer)" in h[i_stop:i_stop + 300],
       "_stopQuantOverview 仍会清定时器（切进详情不空转）")
    print()

    # ── B. 详情排版 ──────────────────────────────────────────────────
    print("=== B. 详情：K 线左半 + 技术指标右半 ===")
    MARK = "display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px"
    ck(h.count(MARK) == 1, "flex 容器恰好 1 个（不是 0 也不是 2 —— 防重复插入）",
       "实测 %d" % h.count(MARK))
    ck(h.count("flex:1 1 calc(50% - 6px);min-width:420px") == 1, "K 线卡占一半宽（min-width 420）")
    ck(h.count("flex:1 1 calc(50% - 6px);min-width:340px") == 1, "技术指标卡占一半宽（min-width 340）")
    ck('id="chart-q-kline" style="height:520px"' in h, "K 线高度保持 520（只压宽度）")
    ck(h.count("height:130px;margin-top:") == 2, "RSI/MACD 缩到 130px（两处）",
       "实测 %d" % h.count("height:130px;margin-top:"))
    ck(h.count("height:150px;margin-top:") == 0, "旧的 150px 已无残留")
    for cid in ("chart-q-kline", "q-tech-body", "chart-q-rsi", "chart-q-macd",
                "q-kline-title", "q-kline-badge", "q-tech-badge"):
        ck('id="%s"' % cid in clean, "容器 id 保留: %s" % cid)
    # 两卡必须都在 flex 容器内（否则顺序/归属错）
    a = clean.index(MARK)
    b = clean.index("<div id=\"q-signal-body\"", a)
    seg = clean[a:b]
    ck('id="chart-q-kline"' in seg and 'id="chart-q-macd"' in seg,
       "K 线与技术指标都在同一个 flex 容器内")
    ck("align-items:flex-start" in MARK,
       "align-items:flex-start（不用 stretch —— 固定高图会留白）")
    ck("flex-wrap:wrap" in MARK, "flex-wrap:wrap（窄屏自动回落上下排列）")
    print()

    # ── C. 内联 JS 语法 ──────────────────────────────────────────────
    print("=== C. 内联 JS 语法 ===")
    ms = list(re.finditer(r"<script>(.*?)</script>", h, re.S))
    js = max(ms, key=lambda m: len(m.group(1))).group(1)
    tmp = ROOT / "probe" / "_inline_check_refresh.js"
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    ck(r.returncode == 0, "node --check 通过", (r.stderr or "")[:200])
    print()

    # ── D. div 配平（剥注释）─────────────────────────────────────────
    print("=== D. div 配平 ===")
    mi, me = h.index("<main>"), h.index("</main>")
    seg = strip_comments(h[mi:me])
    no, nc = len(re.findall(r"<div\b", seg)), len(re.findall(r"</div>", seg))
    ck(no == nc, "<main> 内配平", "%d/%d 净=%+d" % (no, nc, no - nc))
    a = clean.index('<div id="q-detail-view"')
    dd, k = 0, a
    pat = re.compile(r"<div\b|</div>")
    end = -1
    while k < len(clean):
        m = pat.search(clean, k)
        dd += 1 if m.group(0) == "<div" else -1
        if dd == 0:
            end = m.end()
            break
        k = m.end()
    ck(end > 0, "q-detail-view 自身配平（flex 容器已正确闭合）")
    ck("</main>" in clean[end:end + 400] or "<div id=\"tab-variety\"" not in clean[a:end],
       "flex 容器没有溢出到 tab 外")
    print()

    # ── E. 与改动前对比：未新增缺失 id / 孤儿调用 ────────────────────
    print("=== E. id 完整性（与改动前对比）===")
    baks = sorted(glob.glob(str(ROOT / "backup" / "web_template_pre_refresh_*.html")))
    if not baks:
        print("  (无 pre_refresh 备份，跳过)")
    else:
        def analyse(p: Path):
            t = p.read_bytes().decode("utf-8")
            j = t[t.index("<script>"):]
            defined = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', t))
            used = set(re.findall(r"getElementById\(['\"]([a-zA-Z0-9_-]+)['\"]\)", j))
            used |= set(re.findall(r"_initChart\(['\"]([a-zA-Z0-9_-]+)['\"]\)", j))
            calls = set(re.findall(r"this\.((?:_render|load|toggle)[A-Z]\w*)\(", j))
            defs = set(re.findall(r"^ (?:async )?((?:_render|load|toggle)[A-Z]\w*)\(", j, re.M))
            return used - defined, calls - defs
        b_miss, b_calls = analyse(Path(baks[-1]))
        n_miss, n_calls = analyse(TPL)
        ck(not (n_miss - b_miss), "未新增「引用但不存在」的 id", str(sorted(n_miss - b_miss)))
        ck(not (n_calls - b_calls), "未新增孤儿方法调用", str(sorted(n_calls - b_calls)))
        print("     (改动前缺失 id %d / 孤儿调用 %d —— 均为既有)" % (len(b_miss), len(b_calls)))
    print()

    print("=" * 62)
    print("结果: %d PASS / %d FAIL" % (PASS, FAIL))
    if FAILS:
        print("失败项:")
        for f in FAILS:
            print("   -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
