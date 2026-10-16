# -*- coding: utf-8 -*-
"""浏览器层验证「外文研报」tab 的 UI 交互(拦截 API 返回桩数据, 不需要登录凭据)。

覆盖: tab 切换渲染 → 选文件 → 点开始编译 → 进度条/日志推进 → 完成态出下载按钮。
截图落 probe/_ui_foreign_*.png
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:5000"
SAMPLE = ROOT / "probe" / "_sample_foreign_report.pdf"
SHOTS = ROOT / "probe" / "_ui"
SHOTS.mkdir(exist_ok=True)

JOBS = {"jobs": [
    {"id": "stub0001", "filename": "FGE_LPG_weekly.pdf", "title": "出口套利空间仍处历史高位，但冬季风险已被定价",
     "org": "Synthetic Research Desk", "status": "done", "stage": "done", "stage_label": "已完成",
     "progress": 100, "message": "编译完成", "error": "", "log": [], "warnings": [],
     "created_at": "2026-09-24 17:30:11", "finished_at": "2026-09-24 17:31:02",
     "n_pages": 20, "pages": 27, "chars": 61234, "chunks": 18,
     "out_name": "2026-09-24_FGE_出口套利_中文编译.pdf", "has_pdf": True, "options": {}},
    {"id": "stub0002", "filename": "broken.pdf", "title": "", "org": "", "status": "error",
     "stage": "error", "stage_label": "失败", "progress": 40, "message": "编译失败",
     "error": "这份 PDF 没有文字层（疑似扫描件/图片型），无法直接提取正文。请先用 OCR 工具转成带文字层的 PDF 后再上传。",
     "log": [], "warnings": [], "created_at": "2026-09-24 16:02:11", "finished_at": "2026-09-24 16:02:40",
     "n_pages": 8, "pages": 0, "chars": 0, "chunks": 0, "out_name": "", "has_pdf": False, "options": {}},
]}

STAGES = [
    {"status": "running", "stage": "extract", "stage_label": "解析原文", "progress": 5,
     "message": "正在解析原文 PDF（文字层 + 内嵌图表）…", "title": "出口套利空间仍处历史高位，但冬季风险已被定价",
     "org": "Synthetic Research Desk", "n_pages": 20, "log": ["17:30:11  正在解析原文 PDF（文字层 + 内嵌图表）…"],
     "warnings": [], "error": "", "has_pdf": False, "filename": "FGE_LPG_weekly.pdf", "pages": 0, "chars": 0, "chunks": 0},
    {"status": "running", "stage": "translate", "stage_label": "全文翻译", "progress": 58,
     "message": "翻译进度 11/18 段", "title": "出口套利空间仍处历史高位，但冬季风险已被定价",
     "org": "Synthetic Research Desk", "n_pages": 20, "chunks": 18,
     "log": ["17:30:11  正在解析原文 PDF（文字层 + 内嵌图表）…",
             "17:30:19  解析完成：20 页、61,234 字符、5 张内嵌图",
             "17:30:20  正在通读全文并归纳结论、逻辑、数据、内部解读（4 路并行）…",
             "17:31:02  通读完成：《出口套利空间仍处历史高位，但冬季风险已被定价》",
             "17:31:03  开始全文翻译：共 18 段，3 路并行…",
             "17:32:40  翻译进度 11/18 段"],
     "warnings": [], "error": "", "has_pdf": False, "filename": "FGE_LPG_weekly.pdf", "pages": 0},
    {"status": "done", "stage": "done", "stage_label": "已完成", "progress": 100,
     "message": "编译完成：27 页 · 原文 20 页 · 10 条书签", "title": "出口套利空间仍处历史高位，但冬季风险已被定价",
     "org": "Synthetic Research Desk", "n_pages": 20, "pages": 27, "chars": 61234, "chunks": 18,
     "log": ["17:30:11  正在解析原文 PDF（文字层 + 内嵌图表）…",
             "17:32:40  翻译进度 18/18 段",
             "17:32:41  正在渲染 A4 PDF 并写入书签…",
             "17:32:46  检测到 1 页大面积空白（第 5 页），尝试改用续排版式…",
             "17:32:51  续排版式更优：异常空白页 0 页",
             "17:32:51  PDF 已生成：27 页、10 条书签",
             "17:32:51  编译完成：27 页 · 原文 20 页 · 10 条书签"],
     "warnings": [], "error": "", "has_pdf": True,
     "out_name": "2026-09-24_FGE_出口套利_中文编译.pdf", "filename": "FGE_LPG_weekly.pdf"},
]

calls = []


def log_route(route, request):
    calls.append(request.url)
    route.fulfill(status=200, content_type="application/json", body='{"ok":true}')


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 980})
    pg.on("console", lambda m: print("CONSOLE[%s]: %s" % (m.type, m.text[:300]))
          if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: print("PAGEERROR:", str(e)[:500]))
    # 通用兜底须先注册(后注册者优先生效, 覆盖真实接口, 免登录)
    pg.route("**/api/**", log_route)
    pg.route("**/api/auth/status", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true,"authenticated":true}'))
    pg.route("**/api/foreign/jobs*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(JOBS, ensure_ascii=False)))
    pg.route("**/api/foreign/upload", lambda r: r.fulfill(status=200, content_type="application/json", body='{"id":"stub0001","status":"running","message":"已接收"}'))
    job_state = {"i": 0}

    def job_route(route, request):
        k = job_state["i"]
        body = json.dumps(STAGES[min(k, len(STAGES) - 1)], ensure_ascii=False)
        if k < len(STAGES) - 1:
            job_state["i"] = k + 1
        route.fulfill(status=200, content_type="application/json", body=body)

    pg.route("**/api/foreign/job/*", job_route)
    pg.route("**/api/varieties*", lambda r: r.fulfill(status=200, content_type="application/json", body="[]"))

    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1500)
    print("DIAG typeof loadForeign:", pg.evaluate("typeof App.loadForeign"))
    print("DIAG typeof loadForeignJobs:", pg.evaluate("typeof App.loadForeignJobs"))
    print("DIAG switchTab body has foreign:", pg.evaluate("App.switchTab.toString().includes('foreign')"))
    print("DIAG raw jobs fetch:", pg.evaluate(
        "fetch('/api/foreign/jobs?limit=30').then(r => r.text()).then(t => t.slice(0, 120))"))

    # 1) 切到外文研报 tab
    pg.click('nav button[data-tab="foreign"]')
    pg.wait_for_timeout(1500)
    assert pg.locator("#tab-foreign.active").count() == 1, "tab 未激活"
    print("DIAG jobs_url_calls:", [u for u in calls if "foreign" in u])
    print("DIAG fg-list html:", (pg.inner_html("#fg-list") or "")[:400])
    pg.screenshot(path=str(SHOTS / "foreign_tab.png"))
    n_items = pg.locator("#fg-list > div").count()
    assert n_items >= 2, f"历史列表未渲染(仅 {n_items} 项)"

    # 2) 上传 → 进度(轮询节奏: 立即 1 次, 之后每 5s 一次 → 桩按次推进)
    pg.set_input_files("#fg-file", str(SAMPLE))
    pg.click("#fg-start")
    pg.wait_for_timeout(1500)
    assert pg.locator("#fg-job").is_visible(), "进度卡未显示"
    assert "5%" in pg.inner_text("#fg-pct"), "进度未显示: " + pg.inner_text("#fg-pct")
    pg.screenshot(path=str(SHOTS / "foreign_progress.png"))

    # 3) 推进到翻译中段(第二次轮询在 +5s)
    pg.wait_for_timeout(5200)
    pct = pg.inner_text("#fg-pct")
    assert pct.strip() == "58%", "进度未推进: " + pct
    pg.screenshot(path=str(SHOTS / "foreign_translating.png"))

    # 4) 完成态: 下载按钮出现(第三次轮询在 +10s)
    pg.wait_for_timeout(5200)
    assert pg.locator("#fg-dl").is_visible(), "下载按钮未出现"
    assert "27 页" in pg.inner_text("#fg-job-meta"), "编译稿页数未显示"
    pg.screenshot(path=str(SHOTS / "foreign_done.png"))

    b.close()

report = ["UI flow OK", "shots: " + ", ".join(sorted(x.name for x in SHOTS.glob('*.png'))),
          "api calls stubbed: %d" % len(calls)]
(ROOT / "probe" / "_foreign_ui_check.txt").write_text("\n".join(report), encoding="utf-8")
print("\n".join(report))
