# -*- coding: utf-8 -*-
"""浅色主题下的计算面板可读性检查(项目为双主题, 浅色是历史事故点)。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
SHOTS = ROOT / "probe" / "_ui"
fails = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1560, "height": 1400})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.route("**/api/auth/status", lambda r: r.fulfill(status=200, content_type="application/json",
             body='{"ok":true,"authenticated":true}'))
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1000)
    # 切浅色主题: 页面右上角的 .theme-toggle 按钮(onclick="App.toggleTheme()")
    pg.evaluate("""() => {
      const el = document.querySelector('.theme-toggle');
      if (el) el.click();
    }""")
    pg.wait_for_timeout(800)
    theme = pg.evaluate("() => document.documentElement.getAttribute('data-theme')")
    pg.click('nav button[data-tab="quant"]')
    pg.wait_for_timeout(9000)
    pg.evaluate("""() => {
      const items = Array.from(document.querySelectorAll('#tab-quant .sec-index *'))
        .filter(e => e.children.length === 0 && (e.textContent||'').includes('数据计算面板'));
      if (items.length) items[0].click();
    }""")
    pg.wait_for_timeout(1500)
    pg.evaluate("() => document.getElementById('q-compute').scrollIntoView({block:'start'})")
    pg.wait_for_timeout(1200)
    # 检查关键元素的实际计算样式(颜色是否为深色文字 + 亮背景)
    info = pg.evaluate("""() => {
      const g = el => { if (!el) return null; const s = getComputedStyle(el);
        return {color: s.color, bg: s.backgroundColor}; };
      const card = document.getElementById('q-compute');
      const td = document.querySelector('#q-cmp-corr table td');
      return {theme: document.documentElement.getAttribute('data-theme'),
              card: g(card), td: g(td),
              cardText: (card.textContent||'').slice(0, 40)};
    }""")
    print("theme =", theme, "| computed:", info)
    # 亮度判断: 浅色主题下卡片背景应当偏亮
    bg = info.get('card', {}).get('bg', '') if info else ''
    import re
    m = re.findall(r'\d+', bg)
    if len(m) >= 3:
        r, gg, bb = (int(x) for x in m[:3])
        lum = (r*299 + gg*587 + bb*114) / 1000
        print("card bg luminance =", round(lum, 1))
        if lum < 128:
            fails.append("浅色主题下卡片背景仍旧偏暗(疑似未生效)")
    else:
        print("bg 非 rgb() 形式:", bg)
    pg.screenshot(path=str(SHOTS / "compute_panel_light.png"), clip=pg.eval_on_selector(
        "#q-compute", "el => { const r = el.getBoundingClientRect(); return {x:r.x,y:Math.max(0,r.y),width:r.width,height:Math.min(r.height,1200)}; }"))
    print("saved", SHOTS / "compute_panel_light.png")
    if errs: fails.append("pageerror: " + errs[0])
    b.close()
print("结果:", "通过" if not fails else "失败: " + "; ".join(fails))
raise SystemExit(1 if fails else 0)
