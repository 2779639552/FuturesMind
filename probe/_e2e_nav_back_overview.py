# -*- coding: utf-8 -*-
"""E2E 验收：单品种明细下点模块名是否回到全品种总览（真实服务）。

只读：只切视图、点按钮，不写任何数据。
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000/"

STATE = r"""
() => {
  const g = id => {
    const el = document.getElementById(id);
    if (!el) return 'MISSING';
    return (el.style.display === 'none') ? 'hidden' : 'shown';
  };
  return {
    q_dv: g('q-detail-view'), q_ov: g('q-overview-view'),
    f_dv: g('f-detail-view'), f_ov: g('f-overview-view'),
    tab: document.querySelector('.tab.active') ? document.querySelector('.tab.active').id : '',
  };
}
"""


def main() -> int:
    results = {}

    def check(name, ok, extra=''):
        results[name] = ok
        print("--- %s --- %s%s" % (name, "PASS" if ok else "FAIL", "" if ok else "  " + str(extra)))

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1680, "height": 1000})
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1200)

        # ── 1. 技术面：进明细 → 点 nav「技术面量化」→ 回总览 ──
        pg.evaluate("() => App.switchTab('quant')")
        pg.wait_for_timeout(800)
        pg.evaluate("() => App.qOpenDetail('RB')")
        pg.wait_for_timeout(900)
        s1 = pg.evaluate(STATE)
        check("技术面已进明细", s1["q_dv"] == "shown" and s1["q_ov"] == "hidden", json.dumps(s1, ensure_ascii=False))

        pg.click('nav button[data-tab="quant"]')
        pg.wait_for_timeout(900)
        s2 = pg.evaluate(STATE)
        check("点模块名回技术面总览", s2["q_dv"] == "hidden" and s2["q_ov"] == "shown", json.dumps(s2, ensure_ascii=False))
        check("仍在技术面 tab", s2["tab"] == "tab-quant", s2["tab"])

        # ── 2. 基本面：进明细 → 点 nav「基本面量化」→ 回总览 ──
        pg.evaluate("() => App.switchTab('fund')")
        pg.wait_for_timeout(1200)
        pg.evaluate("() => App.fOpenDetail('RB')")
        pg.wait_for_timeout(900)
        s3 = pg.evaluate(STATE)
        check("基本面已进明细", s3["f_dv"] == "shown" and s3["f_ov"] == "hidden", json.dumps(s3, ensure_ascii=False))

        pg.click('nav button[data-tab="fund"]')
        pg.wait_for_timeout(900)
        s4 = pg.evaluate(STATE)
        check("点模块名回基本面总览", s4["f_dv"] == "hidden" and s4["f_ov"] == "shown", json.dumps(s4, ensure_ascii=False))

        # ── 3. 基本面明细页的「基本面量化」大字标题也可点 ──
        pg.evaluate("() => App.fOpenDetail('RB')")
        pg.wait_for_timeout(900)
        pg.click('#tab-fund-tools h3 > span[onclick]')
        pg.wait_for_timeout(700)
        s5 = pg.evaluate(STATE)
        check("点基本面大标题也回总览", s5["f_dv"] == "hidden" and s5["f_ov"] == "shown", json.dumps(s5, ensure_ascii=False))

        # ── 4. ★ 原语义不能被破坏：非 nav 切页（Ctrl+数字走的就是这条）仍保留明细上下文 ──
        pg.evaluate("() => App.fOpenDetail('RB')")
        pg.wait_for_timeout(800)
        pg.evaluate("() => App.switchTab('run')")
        pg.wait_for_timeout(500)
        pg.evaluate("() => App.switchTab('fund')")   # 不带 opts，等价 Ctrl+4
        pg.wait_for_timeout(900)
        s6 = pg.evaluate(STATE)
        check("快捷键切回仍停在明细（上下文未丢）", s6["f_dv"] == "shown" and s6["f_ov"] == "hidden",
              json.dumps(s6, ensure_ascii=False))

        b.close()

    ok = all(results.values())
    print("\nRESULT:", "PASS" if ok else "FAIL", "(%d/%d)" % (sum(results.values()), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
