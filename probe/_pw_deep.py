from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:5000"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 1200})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(3500)

    # 基本面量化页: 逐个品种切换核对(覆盖 郑商所/大商所/上期所/广期所 四类单位口径)
    pg.evaluate("App.switchTab('fund')")
    pg.wait_for_timeout(3000)
    for code in ["CF", "TA", "M", "SC", "SI", "BR"]:
        pg.eval_on_selector("#f-variety", f"e => {{ e.value='{code}'; e.dispatchEvent(new Event('change')); }}")
        pg.wait_for_timeout(4200)
        s = pg.locator("#f-strategy-body").inner_text().replace("\n", " | ")[:150]
        vd = pg.locator("#f-vd-body").inner_text().replace("\n", " | ")[:120]
        vr = pg.locator("#f-vr-body").inner_text().replace("\n", " | ")[:170]
        print(f"\n===== {code} =====")
        print("  ① 三指标:", s)
        print("  ② 四象  :", vd)
        print("  ③ 利润  :", pg.locator("#f-profit-body").inner_text().replace("\n", " | ")[:110])
        print("  ④ 虚实  :", vr)

    # 量化页迁入卡的图是否真画出来
    pg.evaluate("App.switchTab('quant')")
    pg.wait_for_timeout(4500)
    for cid in ["chart-q-kline", "chart-q-pi", "chart-q-basis", "chart-q-inv-chg"]:
        n = pg.eval_on_selector_all(f"#{cid} canvas", "els=>els.map(c=>c.width+'x'+c.height)")
        print(f"  {cid} canvas: {n}")
    pg.evaluate("App.loadQuantSector(true)")
    pg.wait_for_timeout(25000)
    print("  板块面板:", pg.locator("#q-sector-panel").inner_text().replace("\n", " | ")[:200])
    print("  chart-q-sector canvas:", pg.eval_on_selector_all("#chart-q-sector canvas", "els=>els.map(c=>c.width+'x'+c.height)"))

    # 全池矩阵
    pg.evaluate("App.switchTab('fund')")
    pg.wait_for_timeout(2500)
    pg.evaluate("App.loadFundMatrix(true)")
    pg.wait_for_timeout(45000)
    print("\n  矩阵表:", pg.locator("#f-matrix-table").inner_text().replace("\n", " | ")[:300])
    print("  chart-f-matrix canvas:", pg.eval_on_selector_all("#chart-f-matrix canvas", "els=>els.map(c=>c.width+'x'+c.height)"))

    pg.screenshot(path="probe/_shot_fund.png", full_page=False)
    pg.evaluate("App.switchTab('fund')")
    pg.wait_for_timeout(1500)
    pg.locator("#tab-fund").screenshot(path="probe/_shot_fund_tab.png")
    b.close()
print("\n=== errors ===")
for e in errs[:20]: print(" ", e)
