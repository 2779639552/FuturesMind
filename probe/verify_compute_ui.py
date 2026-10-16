# -*- coding: utf-8 -*-
"""浏览器验证: 量化分析页「⑫ 数据计算面板」。

【为什么不用桩】本项目吃过两次假阳性的亏(① 没复刻生产调用签名 ② 桩数据手写了后端
  从不返回的字段)。故本脚本**只拦 auth 免登录**, 维度清单/曲线/相关矩阵全部走
  **真实接口 /api/quant/compute**, 并把页面上的 R 值与接口原始值双向核对。

验收项:
  ① 量化页出现「数据计算面板」卡, 且被小模块索引收进目录(折叠引擎重建成功)
  ② 维度勾选框按家族渲染, 默认勾选 = 收盘价 + 仓单日变化(用户原话场景)
  ③ 曲线图渲染, series 条数 == 已选维度数
  ④ 相关矩阵成表, 格内 R 与接口值一致(±0.001), 且**格内显示 n**
  ⑤ 明细表列出所选维度与点数
  ⑥ 加选第三个维度 → 重算 → curve 3 条 / 矩阵 3×3(证明确实按勾选重算)
  ⑦ 全程无 console error / pageerror
"""
import json
import re
import sys
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


# ── 接口基线: 页面上的值必须与它一致 ──
import urllib.request  # noqa: E402


def api(qs: str) -> dict:
    req = urllib.request.Request(f"{BASE}/api/quant/compute?{qs}")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1560, "height": 1200})
    errors = []
    pg.on("console", lambda m: errors.append(f"CONSOLE[{m.type}]: {m.text[:200]}")
          if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {str(e)[:300]}"))
    # 只拦 auth 免登录
    pg.route("**/api/auth/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body='{"ok":true,"authenticated":true}'))

    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    pg.click('nav button[data-tab="quant"]')
    # 面板要拉价格+基差+仓单三源, 首拉慢; 给足时间
    pg.wait_for_timeout(9000)

    # ① 卡片 + 索引
    card = pg.query_selector("#q-compute")
    chk(card is not None, "面板卡 #q-compute 存在")
    if card:
        title = (card.query_selector("h3") or card).inner_text()
        chk("数据计算面板" in title, "卡片标题为「数据计算面板」", title.split("\n")[0])
    idx_txt = pg.eval_on_selector("#tab-quant", "el => (el.querySelector('.sec-index')||{}).textContent || ''")
    chk("数据计算面板" in idx_txt, "小模块索引已收进「数据计算面板」(折叠引擎重建成功)")

    # ② 维度勾选框
    boxes = pg.eval_on_selector_all(
        "#q-cmp-dims input[type=checkbox]",
        "els => els.map(e => ({v: e.value, on: e.checked, label: (e.closest('label')||{}).textContent||''}))")
    chk(len(boxes) > 0, "维度勾选框已渲染", f"{len(boxes)} 个")
    checked = [x["v"] for x in boxes if x["on"]]
    chk(checked == ["price", "warrant_chg"], "默认勾选 price + warrant_chg(用户原话场景)", str(checked))
    fam_text = pg.eval_on_selector("#q-cmp-dims", "el => el.textContent")
    for fam in ("价格", "仓单"):
        chk(fam in fam_text, f"维度按家族分组出现「{fam}」")

    # 接口基线(与页面 default 一致)
    base = api("variety=RU&days=180&dims=price,warrant_chg&method=pearson")
    # 页面品种可能不是 RU ⇒ 从页面徽标反查品种代码太绕, 直接用接口按页面对齐的品种重算
    # (页面默认品种由 /api/varieties 决定, 这里以页面实际品种为准重取一次基线)
    sym = pg.eval_on_selector("#q-variety", "el => el.value")
    base = api(f"variety={sym}&days=180&dims=price,warrant_chg&method=pearson")
    r_api = base["corr"]["matrix"][0][1]
    n_api = base["corr"]["n_matrix"][0][1]
    out.append(f"      品种={sym} 基线 R={r_api} n={n_api} (接口原始值)")

    # ③ 曲线 series 条数
    sers = pg.evaluate("""() => {
      const el = document.getElementById('chart-q-cmp');
      const ins = echarts.getInstanceByDom(el);
      if (!ins) return null;
      const o = ins.getOption();
      return {n: (o.series||[]).length, names: (o.series||[]).map(s => s.name)};
    }""")
    chk(bool(sers) and sers["n"] == 2, "曲线渲染且 series 条数 = 2", str(sers))

    # ④ 相关矩阵: 格内 R + n 与接口一致
    corr_html = pg.eval_on_selector("#q-cmp-corr", "el => el.innerHTML")
    chk("<table" in corr_html, "相关矩阵已渲染为表格")
    m = re.findall(r">\s*(-?\d+\.\d{3})\s*<span[^>]*>n=(\d+)</span>", corr_html)
    out.append(f"      页面上抓到的 (R, n) 对: {m}")
    chk(len(m) > 0, "矩阵格内同时给出 R 与 n")
    if r_api is not None and m:
        want = f"{r_api:.3f}"
        hit = any(abs(float(a) - r_api) < 0.001 and int(b) == n_api for a, b in m)
        chk(hit, "页面 R 与接口原始值一致(±0.001) 且 n 一致", f"期望 {want} / n={n_api}")
    # 对角自相关为 1.000
    chk("1.000" in corr_html, "对角线自相关 = 1.000")

    # ⑤ 明细表
    ser_html = pg.eval_on_selector("#q-cmp-series", "el => el.textContent")
    for nm in ("收盘价", "仓单日变化"):
        chk(nm in ser_html, f"明细表含「{nm}」")
    chk("最新值" in ser_html and "点数" in ser_html, "明细表含 点数 / 最新值 列")

    pg.screenshot(path=str(SHOTS / "compute_panel_default.png"), full_page=False)

    # ⑥ 加选第三个维度 → 重算
    pg.evaluate("""() => {
      const b = Array.from(document.querySelectorAll('#q-cmp-dims input[type=checkbox]'))
        .find(e => e.value === 'basis');
      if (b) { b.checked = true; b.dispatchEvent(new Event('change', {bubbles: true})); }
    }""")
    pg.wait_for_timeout(7000)
    pg.evaluate("""() => { const el = document.getElementById('q-compute');
      if (el.scrollIntoView) el.scrollIntoView({block:'start'}); }""")
    pg.wait_for_timeout(600)
    sers3 = pg.evaluate("""() => {
      const el = document.getElementById('chart-q-cmp');
      const ins = echarts.getInstanceByDom(el);
      if (!ins) return null;
      const o = ins.getOption();
      return {n: (o.series||[]).length, names: (o.series||[]).map(s => s.name), yaxes: (o.yAxis||[]).length};
    }""")
    chk(bool(sers3) and sers3["n"] == 3, "加选基差后 series 条数 = 3(确实按勾选重算)", str(sers3))
    corr3 = pg.eval_on_selector_all("#q-cmp-corr table tr", "els => els.length")
    chk(corr3 == 4, "矩阵变为 3×3(表头 1 行 + 3 行)", f"tr={corr3}")
    out.append(f"      3 维时 y 轴数 = {sers3 and sers3.get('yaxes')} (元/吨×2 同轴 + 仓单单位另轴 ⇒ 期望 2)")
    chk(bool(sers3) and sers3.get("yaxes") == 2,
        "量纲自动分轴: 元/吨(价格,基差) 同轴 + 仓单单位另轴 = 2 根")
    pg.screenshot(path=str("compute_panel_3dims.png"), full_page=False)

    # ⑦ 控制台
    real = [e for e in errors if "favicon" not in e.lower()]
    chk(not real, "无 console error / pageerror", "; ".join(real[:3]))
    b.close()

print("=" * 78)
print("浏览器验证: 量化页 ⑫ 数据计算面板 (真实数据)")
print("=" * 78)
print("\n".join(out))
print("=" * 78)
if all(checks):
    print(f"结果: 全部通过 ({len(checks)} 项)")
    print(f"截图: {SHOTS / 'compute_panel_default.png'}")
    print(f"截图: {SHOTS / 'compute_panel_3dims.png'}")
    raise SystemExit(0)
print(f"结果: 失败 {checks.count(False)} / {len(checks)} 项")
raise SystemExit(1)
