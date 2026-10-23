# -*- coding: utf-8 -*-
"""E2E 验收：每日总结三/四节是否真的变成左三右四（真实服务 + 真实总结 md）。

只读：打开页面、切到研报页、等渲染，做 DOM 测量与截图，不改任何数据。
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000/"
OUT = pathlib.Path(r"D:\AgentSense\AgentSense\docs\shots")
OUT.mkdir(parents=True, exist_ok=True)

PROBE = r"""
() => {
  const el = document.getElementById('res-daily');
  if (!el) return { err: '#res-daily 不存在' };
  const box = el.querySelector('.rv-twocol');
  const h2 = Array.from(el.querySelectorAll('h2')).map(h => h.textContent.trim());
  if (!box) return { err: '没有 .rv-twocol', h2, marks: el.dataset.rvTwoCol || '',
                     cols: 0, text: (el.textContent || '').slice(0, 120) };
  const L = box.children[0], R = box.children[1];
  const rr = n => { const b = n.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }; };
  return {
    h2,
    marks: el.dataset.rvTwoCol || '',
    cols: box.children.length,
    box: rr(box),
    left: rr(L), right: rr(R),
    leftTitle: (L.children[0] || {}).textContent.trim(),
    rightTitle: (R.children[0] || {}).textContent.trim(),
    leftKids: L.children.length,
    rightKids: R.children.length,
    sameTop: Math.abs(rr(L).y - rr(R).y) <= 1,
    sideBySide: rr(R).x > rr(L).x + 100,
    wraps: el.querySelectorAll('.rv-twocol').length,
    h2Count: el.querySelectorAll('h2').length,
  };
}
"""


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1680, "height": 1000})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1200)
        pg.evaluate("() => App.switchTab('research')")
        pg.wait_for_timeout(1500)
        try:
            pg.wait_for_selector("#res-daily .rv-twocol", timeout=20000)
        except Exception:
            pass
        pg.wait_for_timeout(600)
        info = pg.evaluate(PROBE)
        print(json.dumps(info, ensure_ascii=False, indent=2))

        card = pg.query_selector("#tab-research")
        if card:
            card.screenshot(path=str(OUT / "daily_twocol_full.png"))
        daily = pg.query_selector("#res-daily")
        if daily:
            daily.screenshot(path=str(OUT / "daily_twocol_block.png"))

        # ── 切日期：innerHTML 重渲染后必须**重新**两列（这是幂等闸门最易踩的坑）──
        opts = pg.evaluate("""() => {
          const s = document.getElementById('res-daily-date');
          return s ? Array.from(s.options).map(o => o.value) : [];
        }""")
        swap = None
        if len(opts) >= 2:
            swap = opts[1]
            pg.evaluate("""(d) => {
              const s = document.getElementById('res-daily-date');
              s.value = d; App.loadDailySummary();
            }""", swap)
            pg.wait_for_timeout(2500)
            after = pg.evaluate(PROBE)
            print("\n--- 切到 %s 之后 ---" % swap)
            print(json.dumps({k: after.get(k) for k in
                               ("cols", "leftTitle", "rightTitle", "sameTop", "sideBySide", "wraps", "h2Count", "err")},
                              ensure_ascii=False))
            swap_ok = (after.get("cols") == 2 and after.get("sameTop")
                       and after.get("leftTitle", "").startswith("三、"))
            print("切日期后仍两列:", "PASS" if swap_ok else "FAIL")
            if daily:
                daily.screenshot(path=str(OUT / "daily_twocol_after_switch.png"))
        else:
            swap_ok = True
            print("\n日期下拉只有 1 项，跳过切换验证")

        # 窄窗（700px）：两列应自动堆叠成单列，避免挤成两半
        pg.set_viewport_size({"width": 700, "height": 1000})
        pg.wait_for_timeout(500)
        narrow = pg.evaluate(PROBE)
        nl, nr = narrow.get("left", {}), narrow.get("right", {})
        stacked = nl.get("y") is not None and nr.get("y") is not None and nr["y"] > nl["y"] + 20
        print("\n--- 700px ---  left.w=%s right.w=%s 堆叠=%s" % (nl.get("w"), nr.get("w"), stacked))
        if daily:
            daily.screenshot(path=str(OUT / "daily_twocol_narrow.png"))
        b.close()

    wide_ok = bool(info.get("cols") == 2 and info.get("sameTop") and info.get("sideBySide")
                   and info.get("leftTitle", "").startswith("三、")
                   and info.get("rightTitle", "").startswith("四、"))
    print("\nRESULT 宽屏并排:", "PASS" if wide_ok else "FAIL",
          "| 切日期:", "PASS" if swap_ok else "FAIL",
          "| 窄屏堆叠:", "PASS" if stacked else "FAIL")
    return 0 if (wide_ok and swap_ok and stacked) else 1


if __name__ == "__main__":
    sys.exit(main())
