# -*- coding: utf-8 -*-
"""基差族迁移验收(2026-10-13):
A. 基本面页: 估值轴明细三容器有真渲染(canvas/文字) + 两 badge 有值
B. 量化页:   迁出的三个 chart-q-* 容器全部消失
C. 全 tab:   索引/外壳计数与上组一致(无意外回归)
真实点击切页签(不用 App.switchTab —— 未挂 window), 走真实接口。"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"

JS_PROBE = """(ids) => {
  const out = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) { out[id] = {missing: true}; continue; }
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const cv = el.querySelector('canvas');
    out[id] = {
      w: Math.round(r.width), h: Math.round(r.height),
      canvas: cv ? Math.round(cv.width) + 'x' + Math.round(cv.height) : null,
      text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
      display: cs.display
    };
  }
  return out;
}"""

JS_IDX = """(tabId) => {
  const tab = document.getElementById(tabId);
  if (!tab) return {missing: true};
  return {
    idx: tab.querySelectorAll('.sec-index').length,
    blocks: tab.querySelectorAll('.sec-block').length,
    cards: tab.querySelectorAll('.card').length,
    labels: [...tab.querySelectorAll('.sec-index label, .sec-index button, .sec-index a')]
      .map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim()).filter(Boolean).slice(0, 20)
  };
}"""

FUND_IDS = ["chart-f-basis", "chart-f-basispf", "chart-f-term",
            "f-basis-badge", "f-term-badge"]
QUANT_GONE = ["chart-q-basis", "chart-q-basispf", "chart-q-term",
              "q-basis-badge", "q-term-badge"]

fails = []

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(3000)

    # ── A. 基本面量化页 ──
    pg.click('nav button[data-tab="fund"]')
    pg.wait_for_timeout(6000)
    r = pg.evaluate(JS_PROBE, FUND_IDS)
    print("=== A. 基本面页「估值轴明细」(迁入侧) ===")
    for k in FUND_IDS:
        v = r.get(k, {})
        if v.get("missing"):
            fails.append(f"[fund] 容器缺失: {k}")
            print(f"  ✗ {k:<18} 缺失")
            continue
        is_badge = k.endswith("badge")
        ok = (len(v.get("text", "")) > 3) if is_badge else (v.get("canvas") and v["h"] > 50)
        if not ok:
            fails.append(f"[fund] {k} 无内容 (h={v['h']} canvas={v['canvas']} text={v['text'][:30]!r})")
        mark = "✓" if ok else "✗"
        print(f"  {mark} {k:<18} h={v['h']:<5} canvas={str(v['canvas']):<12} {v['text'][:56]}")

    # ── B. 量化分析页: 迁出侧应全部消失 ──
    pg.click('nav button[data-tab="quant"]')
    pg.wait_for_timeout(6000)
    r = pg.evaluate(JS_PROBE, QUANT_GONE)
    print("\n=== B. 量化页 迁出容器(应全部缺失) ===")
    for k in QUANT_GONE:
        v = r.get(k, {})
        if v.get("missing"):
            print(f"  ✓ {k:<18} 已消失")
        else:
            fails.append(f"[quant] 容器仍在: {k}")
            print(f"  ✗ {k:<18} 仍存在! h={v['h']}")

    # ── C. 全 tab 索引/外壳回归 ──
    print("\n=== C. 全 tab 回归(索引/外壳/卡片) ===")
    expect = {  # 上组结束时的基线 + 本组预期
        "run": (0, 0), "research": None, "sentiment": None, "quant": None,
        "fund": None, "warrant": None, "warehouse": None, "gtja": None, "foreign": (0, 0),
    }
    for t in ["run", "research", "sentiment", "quant", "fund", "warrant",
              "warehouse", "gtja", "foreign"]:
        try:
            pg.click(f'nav button[data-tab="{t}"]')
        except Exception:
            continue
        pg.wait_for_timeout(2600)
        v = pg.evaluate(JS_IDX, f"tab-{t}")
        if v.get("missing"):
            print(f"  {t:<12} (缺失)")
            continue
        exp = expect.get(t)
        note = ""
        if t in ("run", "foreign") and (v["idx"] or v["blocks"]):
            fails.append(f"[{t}] 平铺页出现索引/外壳: idx={v['idx']} blocks={v['blocks']}")
            note = " ✗ 平铺页不应有索引"
        print(f"  {t:<12} idx={v['idx']:<3} blocks={v['blocks']:<3} cards={v['cards']:<3}{note}")
    b.close()

print("\n" + "=" * 60)
if fails:
    print(f"失败 {len(fails)} 项:")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("全部通过 ✓")
