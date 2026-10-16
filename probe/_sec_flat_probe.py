# -*- coding: utf-8 -*-
"""折叠引擎状态探测（基线 / 改动后都用它）。

用法:
    python probe/_sec_flat_probe.py            # 探测全部 tab
    python probe/_sec_flat_probe.py run        # 只探测指定 tab

【纪律】切页签一律真实点击 nav 按钮（App.switchTab 未挂到 window，evaluate 调它会静默失败）。
【判据】某 tab 已平铺 = .sec-index 计数 0 且 .sec-block 计数 0，
        且卡片是 tab 的**直接子元素**（只测"没折叠"不够，要测"真平铺"）。
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign",
        "warehouse", "gtja", "variety"]
targets = sys.argv[1:] or TABS
BASE = "http://127.0.0.1:5000"

JS = """(tid) => {
  const tab = document.getElementById(tid);
  if (!tab) return {missing: true};
  const idx = tab.querySelectorAll('.sec-index').length;
  const blocks = tab.querySelectorAll('.sec-block').length;
  const collapsed = tab.querySelectorAll('.sec-block.is-collapsed').length;
  const directCards = tab.querySelectorAll(':scope > .card').length;
  const allCards = tab.querySelectorAll('.card').length;
  const idxLabels = [...tab.querySelectorAll('.sec-index [data-sec-jump], .sec-index button, .sec-index a')]
      .map(e => (e.textContent||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0, 12);
  return {missing:false, active: tab.classList.contains('active'),
          idx, blocks, collapsed, directCards, allCards, idxLabels};
}"""

rows = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    for t in targets:
        sel = f'nav button[data-tab="{t}"]'
        vis = pg.evaluate("""(s) => { const b = document.querySelector(s);
            if (!b) return false; const r = b.getBoundingClientRect();
            return getComputedStyle(b).display !== 'none' && r.width > 0; }""", sel)
        if not vis:
            rows[t] = {"hidden_nav": True}
            continue
        try:
            pg.click(sel)
            pg.wait_for_timeout(2600)
            rows[t] = pg.evaluate(JS, f"tab-{t}")
        except Exception as e:
            rows[t] = {"click_failed": f"{type(e).__name__}: {str(e)[:100]}"}
    b.close()

print(f"{'tab':<12} {'active':<7} {'sec-index':>10} {'sec-block':>10} {'collapsed':>10} "
      f"{'直系.card':>9} {'全部.card':>9}  索引项")
print("-" * 108)
for t in targets:
    r = rows[t]
    if r.get("hidden_nav"):
        print(f"{t:<12} (nav 按钮 display:none, 跳过)")
        continue
    if r.get("click_failed"):
        print(f"{t:<12} (点击失败) {r['click_failed'][:60]}")
        continue
    if r.get("missing"):
        print(f"{t:<12} (tab 容器缺失)")
        continue
    print(f"{t:<12} {str(r['active']):<7} {r['idx']:>10} {r['blocks']:>10} {r['collapsed']:>10} "
          f"{r['directCards']:>9} {r['allCards']:>9}  {r['idxLabels']}")

out = Path(__file__).with_suffix(".json")
out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n明细已写入 {out}")
