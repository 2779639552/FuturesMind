"""真实浏览器端到端验收: 情绪数据页「情绪得分 vs 价格走势」的独立品种下拉(2026-09-21)。

【它不是 pytest 用例】文件名不以 test_ 开头, 所以 pytest 不会收集 —— 它需要**服务已在跑**,
不满足这个前提就报错的话会污染回归结果。手动跑:

    D:/AgentSense/AgentSense/venv/Scripts/python.exe tests/e2e_overlay_variety_picker.py
    # 换地址: python tests/e2e_overlay_variety_picker.py http://127.0.0.1:5000/
    # 存截图: AGENTSENSE_E2E_SHOT=D:/shot.png python tests/e2e_overlay_variety_picker.py

【为什么必须真跑浏览器】本轮改的全是模板里的 JS。Node 行为 harness
(tests/js/overlay_variety_picker_harness.mjs) 能验逻辑, 但验不了"真实 DOM 里下拉长什么样、
change 事件有没有真接上、切换后画布有没有真重画"。这里用项目 venv 自带的 Playwright。
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AGENTSENSE_URL", "http://127.0.0.1:5000/")
fails = []
passes = []


def ok(cond, msg, extra=""):
    if cond:
        passes.append(msg)
        print(f"  PASS  {msg}{('  ' + extra) if extra else ''}")
    else:
        fails.append(msg)
        print(f"  FAIL  {msg}{('  ' + extra) if extra else ''}")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    overlay_reqs = []
    page.on("request", lambda r: overlay_reqs.append(r.url) if "/api/overlay/" in r.url else None)
    console_errors = []
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    # 等品种列表回来(下拉被填充)
    page.wait_for_function(
        "() => { const s=document.getElementById('overlay-sym'); return s && s.options.length > 1; }",
        timeout=30000,
    )
    page.evaluate("App.switchTab('sentiment')")
    page.wait_for_timeout(2500)  # 等叠加图首次渲染

    print("\n[1] 下拉存在且是「全部品种」(不再只跟随运行分析品种)")
    n_opts = page.eval_on_selector("#overlay-sym", "el => el.options.length")
    ok(n_opts == 21, "下拉选项数 = 21 个白名单品种", f"实际={n_opts}")
    texts = page.eval_on_selector(
        "#overlay-sym", "el => Array.from(el.options).map(o => o.textContent)"
    )
    ok(all("帖" in t for t in texts), "选项文案带帖数, 便于识别哪些品种有情绪数据", texts[0])
    ok("原油" in " ".join(texts) and "多晶硅" in " ".join(texts), "含此前无情绪数据的品种(可点开看纯价格)")

    print("\n[2] 与「运行分析」的品种彻底解耦")
    ov_before = page.eval_on_selector("#overlay-sym", "el => el.value")
    page.evaluate("document.getElementById('sym').value = 'TA'")  # 改运行分析品种
    page.evaluate("App.switchTab('sentiment')")
    page.wait_for_timeout(800)
    ov_after = page.eval_on_selector("#overlay-sym", "el => el.value")
    ok(ov_after == ov_before, "#sym 改成 TA 后, 图表品种保持不变", f"{ov_before} -> {ov_after}")

    print("\n[3] 切换下拉 → 只重画本图, 请求的是选中的品种")
    overlay_reqs.clear()
    target = "PS" if ov_after != "PS" else "M"
    page.evaluate(
        "(v) => { const s=document.getElementById('overlay-sym'); s.value=v;"
        " s.dispatchEvent(new Event('change')); }",
        target,
    )
    page.wait_for_timeout(2500)
    ok(any(f"/api/overlay/{target}?" in u for u in overlay_reqs),
       "真的按选中品种发起了请求", f"target={target}, reqs={overlay_reqs[-2:]}")
    badge = page.inner_text("#overlay-badge")
    ok("交易日" in badge, "徽标刷新为交易日数", badge)
    ok("数据加载失败" not in badge, "正常品种不显示失败态", badge)

    print("\n[4] 画布真的会重画 + 下拉就在这张卡的标题行里")
    sig1 = page.evaluate(
        "() => { const c=document.querySelector('#chart-overlay canvas');"
        " return c ? c.toDataURL().length + ':' + c.width + 'x' + c.height : 'no-canvas'; }"
    )
    ok(sig1 != "no-canvas", "图表 canvas 存在", sig1)
    parent_h3 = page.evaluate(
        "() => { const s=document.getElementById('overlay-sym');"
        " const h=s && s.closest('h3'); return h ? h.textContent.trim().slice(0,20) : null; }"
    )
    ok(parent_h3 is not None and "情绪得分" in (parent_h3 or ""),
       "下拉就在「情绪得分 vs 价格走势」标题行内(位置可发现)", str(parent_h3))

    print("\n[5] 回看天数共享, 下拉选择在切换天数后不被重置")
    page.select_option("#lookback-days", "90")
    page.wait_for_timeout(2200)
    ov_keep = page.eval_on_selector("#overlay-sym", "el => el.value")
    ok(ov_keep == target, "改回看天数后, 选中的品种被保留", f"{target} -> {ov_keep}")

    page.evaluate(
        "(v) => { const s=document.getElementById('overlay-sym'); s.value=v;"
        " s.dispatchEvent(new Event('change')); }",
        "SC",
    )
    page.wait_for_timeout(2500)
    sig2 = page.evaluate(
        "() => { const c=document.querySelector('#chart-overlay canvas');"
        " return c ? c.toDataURL().length + ':' + c.width + 'x' + c.height : 'no-canvas'; }"
    )
    ok(sig1 != sig2, "换成原油后画布内容变了(不是留着上一品种的图)", f"{sig1[:18]} -> {sig2[:18]}")

    print("\n[6] 页面无未捕获 JS 异常")
    real_errors = [e for e in console_errors if "favicon" not in e.lower()]
    ok(not real_errors, "无未捕获 JS 异常", str(real_errors[:2]))

    shot = os.environ.get("AGENTSENSE_E2E_SHOT")
    if shot:
        page.screenshot(path=shot, full_page=False)
        print(f"\n截图: {shot}")
    browser.close()

print(f"\n结果: {len(passes)} 通过, {len(fails)} 失败")
if fails:
    for f in fails:
        print("  FAIL " + f)
    sys.exit(1)
print("全部通过")
