# -*- coding: utf-8 -*-
"""给计算面板(⑫)拍特写: 滚到该卡并截取卡片本身, 供人工核对版式。"""
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:5000"
SHOTS = ROOT / "probe" / "_ui"
SHOTS.mkdir(exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1560, "height": 1400})
    pg.route("**/api/auth/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body='{"ok":true,"authenticated":true}'))
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    pg.click('nav button[data-tab="quant"]')
    pg.wait_for_timeout(9000)
    # 滚到计算面板。⚠️ .sec-block.is-collapsed 带 overflow:hidden, 折叠态截图会得到空白,
    #   故先用**索引项**把该块展开(点索引条目 = 折叠引擎自己的开关, 不绕开它的状态同步)。
    pg.evaluate("""() => {
      const items = Array.from(document.querySelectorAll('#tab-quant .sec-index *'))
        .filter(e => (e.children.length === 0) && (e.textContent || '').includes('数据计算面板'));
      if (items.length) items[0].click();
    }""")
    pg.wait_for_timeout(1200)
    pg.evaluate("""() => {
      const el = document.getElementById('q-compute');
      el.scrollIntoView({block:'start'});
    }""")
    pg.wait_for_timeout(1500)
    cls = pg.eval_on_selector("#q-compute", "el => (el.closest('.sec-block')||{}).className || ''")
    print("sec-block class after expand:", cls)
    el = pg.query_selector("#q-compute")
    if el:
        el.screenshot(path=str(SHOTS / "compute_panel_card.png"))
        print("saved", SHOTS / "compute_panel_card.png")
    else:
        print("FAIL: #q-compute not found")
    # 顺带把整卡高度报出来(佐证内容确实渲染了)
    print("card box:", pg.eval_on_selector("#q-compute", "el => JSON.stringify(el.getBoundingClientRect())"))
    b.close()
