"""冒烟验证: 分析 tab「图谱」子模块的关系图 + 桑基图真实渲染(2026-08-26)。

- 在 127.0.0.1:5055 起**新代码**临时实例(_smoke_server.py, 不动用户 5000 的旧实例)。
- Playwright 打开页面 → 切分析 tab → 点「图谱」子导航 → 等两个 ECharts canvas
  渲染 → 收集 console 报错 → 截图 scripts/smoke_graph_sankey.png。
"""
import pathlib
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "smoke_graph_sankey.png"
BASE = "http://127.0.0.1:5055"
GRAPH_URL = f"{BASE}/api/analysis/graph"
PAGE_URL = f"{BASE}/"

proc = None


def _alive_graph():
    try:
        with urllib.request.urlopen(GRAPH_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_api(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _alive_graph():
            return True
        time.sleep(1)
    return False


def main():
    global proc
    if not _alive_graph():
        proc = subprocess.Popen(
            [sys.executable, "scripts/_smoke_server.py"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if not _wait_api():
            print("FAIL: server 未在 60s 内就绪")
            return 1
        print("server 已就绪(新拉起)")
    else:
        print("复用已有 server")

    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1000, "device_scale_factor": 1})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(PAGE_URL, wait_until="networkidle")
        page.click('button[data-tab="analysis"]')
        page.click('button[data-asub="graph"]')
        # 等待两个 ECharts canvas 渲染
        page.wait_for_function(
            "document.querySelectorAll('#chart-graph canvas, #chart-sankey canvas').length >= 2",
            timeout=15000,
        )
        page.wait_for_timeout(1200)  # 让力导向/桑基布局稳定
        graph_canvases = page.eval_on_selector_all("#chart-graph canvas", "els => els.length")
        sankey_canvases = page.eval_on_selector_all("#chart-sankey canvas", "els => els.length")
        page.screenshot(path=str(OUT), full_page=True)
        print(f"chart-graph canvas: {graph_canvases}")
        print(f"chart-sankey canvas: {sankey_canvases}")
        print(f"console errors: {len(errors)}")
        for e in errors[:5]:
            print("  ERR:", e[:160])
        b.close()

    ok = graph_canvases >= 1 and sankey_canvases >= 1 and not errors
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        if proc:
            proc.terminate()
