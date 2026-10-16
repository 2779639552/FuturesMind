# -*- coding: utf-8 -*-
"""浏览器验证: 仓单分析页「品种池筛选」(2026-09-30)。

【为什么不用桩】本项目吃过假阳性的亏(桩数据手写了后端从不返回的字段)。
故本脚本**只拦 auth 免登录**, 品种清单/池内标记/历史走势全部走**真实接口**,
并把页面 DOM 上的计数与接口原始值双向核对。

【踩坑】`window.App` **不存在**(脚本作用域变量, 未挂 window):
  早前用 `App.switchTab('warrant')` 静默失败 ⇒ 页面停在默认 tab、数据全空,
  而"空集合 ⊆ 池内集合"会让筛选类断言**假 PASS**。必须用真实点击切页签。
  同理切交易所/切范围一律走真实 DOM 事件, 并用"非空"前置断言兜住空集合假阳性。

验收项:
  ① 控制条出现「品种范围」下拉, 默认 仅品种池
  ② 默认态: 总览徽章 = 池内品种数(21)
  ③ 默认态: 交易所汇总卡品种数按池口径(各所卡片数 = 该所池内品种数)
  ④ 默认态: ② 品种下拉只含池内品种(且非空)
  ⑤ 默认态: ③ 历史品种下拉只含池内品种(且非空)
  ⑥ 默认态: 历史交易所下拉各所计数 = 池内分布
  ⑦ 切「全部品种」: 徽章回到 72, 排行榜/历史下拉随之放开
  ⑧ 切回「仅品种池」: 各处同步收回(证明是联动而非一次性渲染)
  ⑨ 新补 mapping 的 BZ(纯苯)/P(棕榈油) 在池内清单中
  ⑩ 全程无 console error / pageerror
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:5000"
SHOTS = ROOT / "probe" / "_ui"
SHOTS.mkdir(exist_ok=True)

out, checks = [], []


def chk(cond, label, detail=""):
    checks.append(bool(cond))
    out.append(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def api(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# ── 接口基线 ──
ov = api("/api/warrant/overview")
hs = api("/api/warrant/history")
pool_all = [p for p in ov["products"] if p.get("in_pool")]
pool_hist = [k for k, v in hs["products"].items() if v.get("in_pool")]
exp_ex = {}
for p_ in pool_all:
    exp_ex[p_["exchange"]] = exp_ex.get(p_["exchange"], 0) + 1
pool_hist_ex = {}
for k in pool_hist:
    ex = k.split("|")[0]
    pool_hist_ex[ex] = pool_hist_ex.get(ex, 0) + 1
print(f"接口基线: overview 全量 {len(ov['products'])} / 池内 {len(pool_all)} {exp_ex};"
      f" history 全量 {len(hs['products'])} / 池内 {len(pool_hist)} {pool_hist_ex}")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1560, "height": 1300})
    errors = []
    pg.on("console", lambda m: errors.append(f"CONSOLE[{m.type}]: {m.text[:200]}")
          if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {str(e)[:300]}"))
    pg.route("**/api/auth/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body='{"ok":true,"authenticated":true}'))

    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    # 【必须真实点击】switchTab 走 nav 绑定; 见文件头"踩坑"说明
    pg.click('nav button[data-tab="warrant"]')
    pg.wait_for_timeout(6000)
    chk(pg.evaluate("() => { const t=document.querySelector('.tab.active'); return t?t.id:null; }") == "tab-warrant",
        "前置: 已切到仓单页(tab-warrant active)")

    def badge_text():
        return pg.evaluate("() => { const e=document.getElementById('wt-ov-badge'); return e?e.textContent.trim():''; }")

    def detail_sel():
        return pg.evaluate("""() => ({
          on: (document.querySelector('#wt-extabs .wt-tab.on')||{}).textContent || '',
          opts: Array.from(document.querySelectorAll('#wt-product option')).map(o=>o.value)
        })""")

    def hist_opts():
        return pg.evaluate("() => Array.from(document.querySelectorAll('#wt-h-product option')).map(o=>o.value)")

    def hist_ex_txt():
        return pg.evaluate("() => Array.from(document.querySelectorAll('#wt-h-ex option')).map(o=>o.textContent)")

    # ① 控制条
    chk(pg.query_selector("#wt-scope") is not None, "① 控制条出现「品种范围」下拉")
    chk(pg.eval_on_selector("#wt-scope", "e=>e.value") == "POOL", "① 默认值 = 仅品种池")

    # ② 默认态徽章
    bt = badge_text()
    chk(f"{len(pool_all)}" in bt, "② 默认态徽章 = 池内品种数", f"badge={bt!r} expect {len(pool_all)}")

    # ③ 交易所汇总卡按池口径
    excards = pg.evaluate("""() => Array.from(document.querySelectorAll('#wt-excards .wt-excard')).map(c => {
      const ss = c.querySelectorAll('.s');
      const txt = ss.length ? ss[0].textContent : '';
      const m = txt.match(/(\\d+)\\s*个品种/);
      return { count: m ? parseInt(m[1],10) : null, txt: txt.trim() };
    })""")
    got_counts = [e["count"] for e in excards if e["count"] is not None]
    chk(len(excards) == 4 and got_counts,
        "③ 交易所汇总卡渲染 4 张", f"counts={got_counts}")
    chk(sorted(got_counts) == sorted(exp_ex.values()),
        "③ 各所卡片品种数 = 该所池内品种数", f"got={sorted(got_counts)} expect={sorted(exp_ex.values())}")

    # ④ ② 品种下拉(细则): 逐个交易所真实点击后收集, 并核对是否池内
    cn2ex = {"郑州商品交易所": "CZCE", "大连商品交易所": "DCE",
             "广州期货交易所": "GFEX", "上海期货交易所": "SHFE"}
    pool_by_ex = {}
    for p_ in pool_all:
        pool_by_ex.setdefault(p_["exchange"], set()).add(p_["code"])
    n_tabs = pg.eval_on_selector_all("#wt-extabs .wt-tab", "els=>els.length")
    chk(n_tabs > 0, "④② 交易所标签存在", f"{n_tabs} 个")
    detail_nonpool = []
    detail_total = 0
    for i in range(n_tabs):
        pg.evaluate(f"""() => {{ const b=document.querySelectorAll('#wt-extabs .wt-tab')[{i}]; b.click(); }}""")
        pg.wait_for_timeout(900)
        s = detail_sel()
        ex = cn2ex.get(s["on"].strip(), None)
        detail_total += len(s["opts"])
        for code in s["opts"]:
            if not (ex and code in pool_by_ex.get(ex, set())):
                detail_nonpool.append((ex, code))
    chk(detail_total > 0, "④② 品种下拉非空(防空集合假阳性)", f"累计 {detail_total} 项")
    chk(not detail_nonpool, "④② 品种下拉只含池内品种", f"非池内={detail_nonpool[:5]}")

    # ⑤ 历史品种下拉
    hopts = hist_opts()
    nonpool_hist = [k for k in hopts if k not in set(pool_hist)]
    chk(len(hopts) > 0, "⑤ 历史品种下拉非空", f"{len(hopts)} 项")
    chk(not nonpool_hist, "⑤ 历史品种下拉只含池内品种", f"非池内={nonpool_hist[:5]}")

    # ⑥ 历史交易所下拉计数
    htex = hist_ex_txt()
    chk(f"（{len(pool_hist)}）" in (htex[0] if htex else ""),
        "⑥ 历史『四所全部』计数按池口径", f"first={htex[0] if htex else None!r}")
    ex_cnt_ok = True
    for ex, n in pool_hist_ex.items():
        cn = {"CZCE": "郑商所", "DCE": "大商所", "GFEX": "广期所", "SHFE": "上期所"}[ex]
        key = f"仅 {cn}（{n}）"
        if not any(key == t.strip() for t in htex):
            ex_cnt_ok = False
            out.append(f"      缺: {key!r} 实际={htex}")
    chk(ex_cnt_ok, "⑥ 历史各所计数按池口径", str(htex))

    # ⑨ BZ/P
    pool_codes = {p_["code"].upper() for p_ in pool_all}
    chk("BZ" in pool_codes and "P" in pool_codes, "⑨ 新补 mapping 的 BZ/P 在池内清单",
        f"{sorted(pool_codes)}")

    pg.screenshot(path=str(SHOTS / "wt_pool_default.png"))

    # ⑦ 切「全部品种」
    pg.evaluate("""() => { const s=document.getElementById('wt-scope'); s.value='ALL'; s.onchange(); }""")
    pg.wait_for_timeout(3500)
    bt_all = badge_text()
    chk(f"{len(ov['products'])}" in bt_all, "⑦ 切全部品种: 徽章回到全量",
        f"badge={bt_all!r} expect {len(ov['products'])}")
    hopts_all = hist_opts()
    chk(len(hopts_all) > len(hopts), "⑦ 切全部品种: 历史下拉放开",
        f"{len(hopts)} -> {len(hopts_all)}")
    exc_all = pg.evaluate("""() => Array.from(document.querySelectorAll('#wt-excards .wt-excard')).map(c => {
      const m = (c.querySelector('.s')||{}).textContent||''; const r=m.match(/(\\d+)\\s*个品种/); return r?parseInt(r[1],10):null;} )""")
    full_ex = {}
    for p_ in ov["products"]:
        full_ex[p_["exchange"]] = full_ex.get(p_["exchange"], 0) + 1
    chk(sorted([x for x in exc_all if x is not None]) == sorted(full_ex.values()),
        "⑦ 切全部品种: 汇总卡回到全量口径",
        f"got={sorted([x for x in exc_all if x is not None])} expect={sorted(full_ex.values())}")
    pg.screenshot(path=str(SHOTS / "wt_pool_all.png"))

    # ⑧ 切回
    pg.evaluate("""() => { const s=document.getElementById('wt-scope'); s.value='POOL'; s.onchange(); }""")
    pg.wait_for_timeout(3500)
    bt_back = badge_text()
    hopts_back = hist_opts()
    chk(f"{len(pool_all)}" in bt_back, "⑧ 切回仅品种池: 徽章收回", f"badge={bt_back!r}")
    chk(len(hopts_back) == len(hopts), "⑧ 切回仅品种池: 历史下拉收回",
        f"{len(hopts_all)} -> {len(hopts_back)} expect {len(hopts)}")

    # ⑩ 错误
    chk(not errors, "⑩ 全程无 console error / pageerror", "; ".join(errors[:3]))

    pg.screenshot(path=str(SHOTS / "wt_pool_back.png"))
    b.close()

print("\n".join(out))
ok = sum(1 for c in checks if c)
print(f"\n==== {ok}/{len(checks)} PASS ====")
sys.exit(0 if ok == len(checks) else 1)
