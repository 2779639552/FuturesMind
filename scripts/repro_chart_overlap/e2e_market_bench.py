"""E2E: 市场(买入持有) benchmark must change when the 区间 (startDate) changes."""
import pathlib
import re
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000/"

SETUP_JS = r"""
(arg) => {
  const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  setVal('trade-variety', 'RB');
  setVal('trade-start', arg.start);
  setVal('trade-end', arg.end);
  // 勾选一个策略, 关掉风控/成本保持口径一致
  const cbs = document.querySelectorAll('.multi-cb');
  cbs.forEach(cb => { cb.checked = false; });
  const target = Array.from(cbs).find(cb => cb.value === 'turtle') || cbs[0];
  if (target) target.checked = true;
  const off = ['risk-enabled', 'cost-enabled'];
  off.forEach(id => { const el = document.getElementById(id); if (el) el.checked = false; });
  return target ? target.value : null;
}
"""

READ_JS = r"""
() => {
  const el = document.getElementById('trading-summary');
  const txt = el ? el.innerText : '';
  const m = txt.match(/市场\(买入持有\)\s*([+-]?\d+(?:\.\d+)?)%/);
  return m ? m[1] : null;
}
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 800})
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_function("typeof App !== 'undefined'")
    page.set_default_timeout(90_000)

    results = []
    for label, start, end in [("区间A(近~8月)", "2026-01-01", "2026-08-25"),
                              ("区间B(近~4月)", "2026-05-01", "2026-08-25")]:
        strat = page.evaluate(SETUP_JS, {"start": start, "end": end})
        assert strat, "no strategy checkbox found"
        page.evaluate("App.runMultiCompare()")  # async → playwright awaits promise
        page.wait_for_function(
            "document.getElementById('trading-summary').innerText.includes('市场')",
            timeout=90_000,
        )
        val = page.evaluate(READ_JS)
        results.append((label, val))
        print(f"{label}: 市场(买入持有) = {val}%  (strategy={strat})")

    b.close()

    vals = [v for _, v in results]
    assert all(v is not None for v in vals), f"benchmark not shown: {results}"
    assert len(set(vals)) > 1, f"benchmark unchanged across 区间: {results}"
    print("\nPASS: 基准随区间变化 →", results)
