import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
errs, reqs = [], []

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    pg.on("console", lambda m: errs.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(f"[pageerror] {e}"))
    pg.on("response", lambda r: reqs.append((r.status, r.url)) if "/api/" in r.url and r.status >= 400 else None)

    pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(4000)

    # 1) 是否被登录页拦截
    title = pg.title()
    has_login = pg.locator("#login-overlay").count() > 0 and pg.locator("#login-overlay").is_visible()
    print("title:", title, "| login form:", has_login)

    # 2) 导航按钮
    navs = pg.eval_on_selector_all("nav button", "els => els.map(e => e.dataset.tab + ':' + e.textContent.trim())")
    print("nav:", navs)

    # 3) tab 容器
    tabs = pg.eval_on_selector_all(".tab", "els => els.map(e => e.id)")
    print("tabs:", tabs)

    if not has_login:
        # 4) 切到 量化分析 页, 看迁入的 4 张卡是否在
        pg.evaluate("App.switchTab('quant')")
        pg.wait_for_timeout(3500)
        print("quant active:", pg.eval_on_selector("#tab-quant", "e => e.classList.contains('active')"))
        for sel in ["#q-analysis", "#chart-q-pi", "#q-sector", "#q-research", "#chart-q-kline"]:
            print(f"  {sel} 存在:", pg.locator(sel).count() > 0, "| 可见:", pg.locator(sel).is_visible() if pg.locator(sel).count() else False)
        print("  q-analysis-body 内容:", (pg.locator("#q-analysis-body").inner_text()[:110]).replace("\n", " / "))
        print("  q-analysis-badge:", pg.locator("#q-analysis-badge").inner_text())
        print("  索引项:", pg.eval_on_selector_all("#tab-quant .sec-index-item", "els=>els.map(e=>e.textContent.trim())"))

        # 5) 切到 基本面量化 页
        pg.evaluate("App.switchTab('fund')")
        pg.wait_for_timeout(4000)
        print("fund active:", pg.eval_on_selector("#tab-fund", "e => e.classList.contains('active')"))
        print("  f-variety 选项数:", pg.eval_on_selector("#f-variety", "e => e.options.length"))
        print("  f-variety 当前值:", pg.eval_on_selector("#f-variety", "e => e.value"))
        for sel in ["#f-strategy-body", "#f-vd-body", "#chart-f-vd", "#f-profit-body", "#f-vr-body"]:
            n = pg.locator(sel).count()
            print(f"  {sel} 存在:{n>0}", ("| 可见:" + str(pg.locator(sel).is_visible())) if n else "")
        print("  ① strategy:", pg.locator("#f-strategy-body").inner_text()[:130].replace("\n", " / "))
        print("  ② vd badge:", pg.locator("#f-vd-badge").inner_text())
        print("  ③ profit:", pg.locator("#f-profit-body").inner_text()[:110].replace("\n", " / "))
        print("  ④ vr:", pg.locator("#f-vr-body").inner_text()[:130].replace("\n", " / "))
        print("  索引项:", pg.eval_on_selector_all("#tab-fund .sec-index-item", "els=>els.map(e=>e.textContent.trim())"))

        # 6) eCharts 是否真画出来(canvas 尺寸)
        print("  chart-f-vd canvas:", pg.eval_on_selector_all("#chart-f-vd canvas", "els=>els.map(c=>c.width+'x'+c.height)"))

        # 7) Ctrl+N 快捷键映射
        print("  Ctrl+5 →", pg.evaluate("(() => { document.dispatchEvent(new KeyboardEvent('keydown',{key:'5',ctrlKey:true,bubbles:true})); return App._currentTab; })()"))

    b.close()

print("\n=== console errors ===")
for e in errs[:25]: print(" ", e)
print("=== /api 40x+ ===")
for s, u in reqs[:25]: print(" ", s, u)
