# -*- coding: utf-8 -*-
"""库存族迁移验收：基本面页应"真渲染", 量化页应"真消失"。

【纪律】1) 切页签一律真实点击 nav(App 未挂 window); 2) 断言"有内容"必须先断言
  非空/非"加载中", 否则"没渲染"会伪装成"渲染正常"; 3) 走真实接口(不拦桩) ——
  桩数据验不了字段契约, 本项目栽过两次。
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"

# 迁入基本面页的容器 → (说明, 类型)
FUND_IDS = {
    "chart-f-inv": ("仓单库存图", "chart"),
    "f-cycle-body": ("库存周期四象限", "text"),
    "chart-f-pi": ("价格vs仓单双轴", "chart"),
    "f-analysis-body": ("价格-库存关联分析", "text"),
    "f-inv-badge": ("仓单徽章", "text"),
    "f-pi-badge": ("双轴徽章", "text"),
    "f-analysis-badge": ("关联徽章", "text"),
}
# 量化页不应再存在的容器
QUANT_GONE = ["chart-q-inv", "chart-q-pi", "q-analysis-body", "q-cycle-body",
              "q-sector-panel", "q-research"]
# 量化页应保留的技术模块（抽样）
QUANT_KEEP = ["chart-q-kline", "q-tech-body", "chart-q-basis", "chart-q-term", "q-cmp-dims"]

JS_PROBE = """(ids) => {
  const out = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) { out[id] = {exists: false}; continue; }
    const r = el.getBoundingClientRect();
    const cv = el.querySelector('canvas');
    out[id] = {
      exists: true,
      w: Math.round(r.width), h: Math.round(r.height),
      hasCanvas: !!cv,
      canvasW: cv ? cv.width : 0,
      textLen: (el.textContent || '').replace(/\\s+/g, ' ').trim().length,
      loading: (el.textContent || '').includes('加载中'),
      sample: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 70),
    };
  }
  return out;
}"""

fails = []

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})

    # ── A. 基本面量化页 ──
    pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_timeout(3000)
    pg.click('nav button[data-tab="fund"]')
    pg.wait_for_timeout(6000)  # 库存族 + 研报指标都要拉接口

    res = pg.evaluate(JS_PROBE, list(FUND_IDS))
    print("=== A. 基本面页：迁入模块渲染情况 ===")
    for cid, (label, kind) in FUND_IDS.items():
        r = res.get(cid) or {}
        if not r.get("exists"):
            fails.append(f"[fund] {label} (#{cid}) 容器不存在")
            print(f"  ✗ {label:<18} 容器不存在")
            continue
        if kind == "chart":
            ok = r["hasCanvas"] and r["canvasW"] > 0
            flag = "✓" if ok else "✗"
            if not ok:
                fails.append(f"[fund] {label} 图表未渲染(canvas={r['hasCanvas']}, w={r['canvasW']})")
            print(f"  {flag} {label:<18} canvas={r['hasCanvas']} 宽={r['canvasW']} 高={r['h']}")
        else:
            ok = r["textLen"] > 0 and not r["loading"]
            flag = "✓" if ok else "✗"
            if not ok:
                fails.append(f"[fund] {label} 无内容(loading={r['loading']}, len={r['textLen']})")
            print(f"  {flag} {label:<18} 文字 {r['textLen']} 字 | {r['sample'][:52]}")

    # 板块相关性需要点按钮（设计为不自动加载）—— 用 JS 触发按钮点击，
    # 避开 playwright 的可见性检查（该按钮在卡片深处，视口外时 click 会超时）
    clicked = pg.evaluate("""() => {
      const sel = document.getElementById('f-sector');
      if (!sel) return false;
      const row = sel.closest('div');
      const box = row && row.parentElement;
      const btn = box && box.querySelector('button');
      if (!btn) return false;
      btn.click();
      return true;
    }""")
    if not clicked:
        fails.append("[fund] 板块相关性「拉取/刷新」按钮未找到")
        print("  ✗ 板块相关性按钮未找到")
    else:
        pg.wait_for_timeout(40000)  # 逐品种拉价格+仓单
        r = pg.evaluate(JS_PROBE, ["f-sector-panel"])["f-sector-panel"]
        has_chart = r.get("hasCanvas") or r.get("textLen", 0) > 20
        ok = has_chart and not r.get("loading")
        if not ok:
            fails.append(f"[fund] 板块相关性点击后仍无内容(len={r.get('textLen')}, canvas={r.get('hasCanvas')})")
        print(f"  {'✓' if ok else '✗'} 板块相关性(点击后)   文字 {r.get('textLen')} 字 canvas={r.get('hasCanvas')} | {r.get('sample','')[:50]}")

    # 基本面页自身的 4 个原有模块不能坏
    orig = pg.evaluate(JS_PROBE, ["f-strategy-body", "f-vd-body", "f-profit-body", "f-vr-body"])
    print("\n=== B. 基本面页：原有模块未受影响 ===")
    for cid in ["f-strategy-body", "f-vd-body", "f-profit-body", "f-vr-body"]:
        r = orig.get(cid) or {}
        ok = r.get("exists") and r.get("textLen", 0) > 0 and not r.get("loading")
        if not ok:
            fails.append(f"[fund] 原有模块 {cid} 异常(len={r.get('textLen')})")
        print(f"  {'✓' if ok else '✗'} {cid:<20} {r.get('textLen')} 字")

    # ── C. 量化分析页：迁出的应消失, 技术模块应保留 ──
    pg.click('nav button[data-tab="quant"]')
    pg.wait_for_timeout(6000)
    gone = pg.evaluate(JS_PROBE, QUANT_GONE)
    print("\n=== C. 量化页：迁出容器应已消失 ===")
    for cid in QUANT_GONE:
        r = gone.get(cid) or {}
        ok = not r.get("exists")
        if not ok:
            fails.append(f"[quant] 迁出容器 #{cid} 仍存在")
        print(f"  {'✓' if ok else '✗'} #{cid:<18} {'已消失' if ok else '仍存在!'}")

    keep = pg.evaluate(JS_PROBE, QUANT_KEEP)
    print("\n=== D. 量化页：技术模块应保留 ===")
    for cid in QUANT_KEEP:
        r = keep.get(cid) or {}
        ok = r.get("exists")
        if not ok:
            fails.append(f"[quant] 技术模块 #{cid} 丢失")
        print(f"  {'✓' if ok else '✗'} #{cid:<18} {'在' if ok else '丢了!'}")

    b.close()

print()
if fails:
    print(f"❌ 验证失败 {len(fails)} 项：")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过：基本面页真渲染 / 量化页真消失 / 原有模块与保留模块均正常")
