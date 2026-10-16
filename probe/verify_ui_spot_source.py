# -*- coding: utf-8 -*-
"""verify_ui_spot_source.py — 第 4 层验证: 真实浏览器渲染 + 来源标注可见性。

【验证点】
  1. 基本面量化 tab 打开后, #f-source-badge 有内容且文案含"生意社"或有"无现货指数"
  2. 切到 PX(生意社兜底) → 徽标应出现"生意社 100ppi(兜底)"
  3. 切到 SC(两源皆无) → 徽标应出现"无现货指数"告警
  4. 全池矩阵: 点开后 (a) 气泡点数 = 17 (b) 表格出现"基差源"列
     (c) 出现"未参与评级的 3 个品种"逐条点名块, 且含 SC/LU/CJ
  5. 全程 console error 计数为 0, 无 4xx 响应
"""
from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def main() -> int:
    errors: list[str] = []
    bad_http: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("response", lambda r: bad_http.append(f"{r.status} {r.url}") if r.status >= 400 else None)

        print("=" * 74)
        print("1. 打开站点并进入基本面量化 tab")
        print("=" * 74)
        pg.goto(BASE, wait_until="networkidle", timeout=90000)
        pg.wait_for_timeout(2500)
        pg.evaluate("App.switchTab('fund')")
        pg.wait_for_timeout(6000)

        # 切到 PX(应有生意社兜底标注)
        pg.select_option("#f-variety", "PX")
        pg.wait_for_timeout(9000)
        badge = (pg.inner_text("#f-source-badge") or "").strip()
        print(f"   PX 徽标: {badge[:130]}")
        check("生意社" in badge, "PX 徽标标注生意社兜底", f"实得 {badge[:80]}")
        check("gtja" in badge.lower() or "国君" in badge, "PX 徽标同时说明国君为主")

        # 策略卡基差块应出现"源: 生意社 100ppi"
        sidx = (pg.inner_text("#f-strategy-body") or "")
        check("生意社" in sidx, "策略卡基差块显示来源=生意社", f"{(sidx[:120])}")

        print()
        print("=" * 74)
        print("2. 切到 SC(两源皆无) 应给出告警而非静默")
        print("=" * 74)
        pg.select_option("#f-variety", "SC")
        pg.wait_for_timeout(9000)
        badge_sc = (pg.inner_text("#f-source-badge") or "").strip()
        print(f"   SC 徽标: {badge_sc[:150]}")
        check("无现货指数" in badge_sc, "SC 徽标告警'无现货指数'", f"实得 {badge_sc[:90]}")
        check("估值轴" in badge_sc or "不可用" in badge_sc,
              "SC 徽标说明后果(估值轴不可用)", f"实得 {badge_sc[:120]}")
        vd_sc = (pg.inner_text("#f-vd-body") or "")
        check("无法二维评级" in vd_sc, "SC 的②卡片如实说明无法评级", f"{vd_sc[:100]}")
        check("无现货指数" in vd_sc or "两源" in vd_sc,
              "SC 的②卡片点明是'两源皆无'而非'取数失败'", f"{vd_sc[:160]}")

        print()
        print("=" * 74)
        print("3. 全池矩阵: 点数 / 基差源列 / 未评级逐条点名")
        print("=" * 74)
        pg.evaluate("App.loadFundMatrix(false)")
        pg.wait_for_timeout(45000)
        tbl = pg.inner_text("#f-matrix-table") or ""
        check("基差源" in tbl, "矩阵表出现'基差源'列")
        check("生意社" in tbl, "矩阵表内 FU/PX/BZ 标注为生意社")
        import re as _re
        _m = _re.search(r"未参与评级的\s*(\d+)\s*个品种", tbl)
        check(bool(_m), "矩阵表有未评级逐条点名块(含数量)",
              f"节选 {tbl[-400:]}")
        if _m:
            # 数量应与后端 no_spot 一致(池扩容不该改变"无现货指数"集合)
            n_norating = int(_m.group(1))
            check(n_norating == 3, f"未评级数量为 3(SC/LU/CJ), 实得 {n_norating}")
        for c in ("SC", "LU", "CJ"):
            check(c in tbl[-1200:], f"未评级名单含 {c}")
        check("两源均无现货指数" in tbl, "未评级原因区分为'两源均无现货指数'")
        # 气泡点数: 【动态期望】直接问后端矩阵接口, 避免池扩容后测试写死失真。
        # 池 20→21(新增 P)后 scored 由 17→18, 属正确变化而非回归。
        import json as _json
        import urllib.request as _ur
        try:
            _op = _ur.build_opener(_ur.ProxyHandler({}))
            with _op.open("http://127.0.0.1:5000/api/fund/value-drive/matrix", timeout=240) as _r:
                _mx = _json.loads(_r.read().decode("utf-8"))
            n_expect = int(_mx.get("scored") or 0)
        except Exception as _e:
            n_expect = -1
            print(f"   [WARN] 取后端期望点数失败, 退回不校验: {_e}")
        n_pts = pg.evaluate("""() => {
          const el = document.getElementById('chart-f-matrix');
          if (!el) return -1;
          const inst = echarts.getInstanceByDom(el);
          if (!inst) return -1;
          const o = inst.getOption();
          return (o.series && o.series[0] && o.series[0].data) ? o.series[0].data.length : -1;
        }""")
        if n_expect > 0:
            check(n_pts == n_expect, f"气泡图点数与后端 scored 一致(期望 {n_expect}, 实得 {n_pts})")
        else:
            check(n_pts > 0, f"气泡图有数据点(实得 {n_pts})")

        print()
        print("=" * 74)
        print("4. 控制台与网络")
        print("=" * 74)
        real_err = [e for e in errors if "favicon" not in e.lower()]
        print(f"   console error: {len(real_err)} 条")
        for e in real_err[:5]:
            print(f"     - {e[:150]}")
        check(not real_err, "无 console error")
        print(f"   4xx/5xx 响应: {len(bad_http)} 条")
        for b in bad_http[:5]:
            print(f"     - {b[:150]}")
        check(not bad_http, "无 4xx/5xx 响应")

        br.close()

    print()
    print("=" * 74)
    print(f"结果: {PASS} 通过 / {FAIL} 失败")
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
