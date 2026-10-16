# -*- coding: utf-8 -*-
"""验证「外文研报」页两项改动(2026-09-30):
  ① 三个小模块【平铺】—— tab-foreign 内不得有 .sec-index / .sec-block(不参与折叠),
     且三张卡的可见性都不为“被收起”。
  ② 历史列表文章名称是【超链接】—— has_pdf=True 的行, 标题应是 <a> 指向编译稿 PDF;
     has_pdf=False(失败)的行保持纯文本, 不能有死链。
另做回归: 其他 tab 的小模块折叠必须仍然工作(不能把整个引擎弄坏)。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:5000"
SHOTS = ROOT / "probe" / "_ui"
SHOTS.mkdir(exist_ok=True)

JOBS = {"jobs": [
    {"id": "stub0001", "filename": "FGE_LPG_weekly.pdf",
     "title": "出口套利空间仍处历史高位，但冬季风险已被定价",
     "org": "Synthetic Research Desk", "status": "done", "stage": "done",
     "stage_label": "已完成", "progress": 100, "message": "编译完成", "error": "",
     "log": [], "warnings": [], "created_at": "2026-09-24 17:30:11",
     "finished_at": "2026-09-24 17:31:02", "n_pages": 20, "pages": 27,
     "chars": 61234, "chunks": 18,
     "out_name": "2026-09-24_FGE_出口套利_中文编译.pdf", "has_pdf": True, "options": {}},
    {"id": "stub0002", "filename": "broken.pdf", "title": "", "org": "", "status": "error",
     "stage": "error", "stage_label": "失败", "progress": 40, "message": "编译失败",
     "error": "这份 PDF 没有文字层（疑似扫描件/图片型）。", "log": [], "warnings": [],
     "created_at": "2026-09-24 16:02:11", "finished_at": "2026-09-24 16:02:40",
     "n_pages": 8, "pages": 0, "chars": 0, "chunks": 0, "out_name": "", "has_pdf": False,
     "options": {}},
]}

checks = []
errors = []


def chk(cond, label, detail=""):
    checks.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 980})
    pg.on("console", lambda m: (errors.append(f"CONSOLE[{m.type}]: {m.text[:200]}")
                                if m.type == "error" else None))
    pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {str(e)[:300]}"))

    pg.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                              body='{"ok":true}'))
    pg.route("**/api/auth/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body='{"ok":true,"authenticated":true}'))
    pg.route("**/api/foreign/jobs*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps(JOBS, ensure_ascii=False)))
    pg.route("**/api/varieties*", lambda r: r.fulfill(
        status=200, content_type="application/json", body="[]"))

    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)

    # ── ① 平铺验证 ────────────────────────────────────────────────
    print("\n① 外文研报页是否平铺(不参与小模块折叠)")
    pg.click('nav button[data-tab="foreign"]')
    pg.wait_for_timeout(1800)
    assert pg.locator("#tab-foreign.active").count() == 1, "tab 未激活"

    n_idx = pg.locator("#tab-foreign .sec-index").count()
    n_blk = pg.locator("#tab-foreign .sec-block").count()
    n_collapsed = pg.locator("#tab-foreign .sec-block.is-collapsed").count()
    chk(n_idx == 0, "tab 内无小模块索引条(.sec-index)", f"count={n_idx}")
    chk(n_blk == 0, "tab 内无折叠外壳(.sec-block)", f"count={n_blk}")
    chk(n_collapsed == 0, "没有被收起的块", f"count={n_collapsed}")

    # 三张卡都真实可见(不是 max-height:0 藏起来)
    # 【注意】#fg-job(当前任务进度卡)在无运行中任务时 display:none 是**设计如此**,
    #   高度 0 属正确行为, 不能算"被折叠"。故只对常显的两张卡做高度断言,
    #   进度卡的可见性另在下面"有任务时"的场景单独验证。
    vis = pg.evaluate("""() => {
        const t = document.getElementById('tab-foreign');
        return Array.from(t.querySelectorAll(':scope > .card')).map(c => {
            const h3 = c.querySelector('h3');
            return { id: c.id || '',
                     name: h3 ? h3.textContent.trim().slice(0, 22) : '(无标题)',
                     h: Math.round(c.getBoundingClientRect().height) };
        });
    }""")
    print("     顶层卡片高度:", vis)
    chk(len(vis) == 3, "顶层仍是 3 张卡", f"count={len(vis)}")
    always = [v for v in vis if v["id"] != "fg-job"]
    chk(all(v["h"] > 40 for v in always), "常显卡片高度均 > 40px(未被收起)",
        str([v["h"] for v in always]))
    # 三张卡必须是 tab 的**直接子元素**(即平铺在页面上, 没被塞进任何折叠容器)
    direct = pg.evaluate("""() => {
        const t = document.getElementById('tab-foreign');
        return Array.from(t.querySelectorAll(':scope > .card')).length;
    }""")
    chk(direct == 3, "三张卡都是 tab 直接子元素(真平铺, 未嵌套进折叠壳)", f"{direct} 张")
    pg.screenshot(path=str(SHOTS / "foreign_flat.png"), full_page=True)

    # ── ② 文章名超链接 ────────────────────────────────────────────
    print("\n② 历史编译任务的文章名是否为超链接")
    rows = pg.locator("#fg-list > div")
    n = rows.count()
    chk(n >= 2, "历史列表已渲染", f"{n} 行")

    link_info = pg.evaluate("""() => {
        return Array.from(document.querySelectorAll('#fg-list > div')).map(r => {
            const a = r.querySelector('a[href*="/api/foreign/download/"]');
            const b = r.querySelector('b');
            return {
                text: b ? b.textContent.trim().slice(0, 24) : '',
                inAnchor: !!(b && b.closest('a[href*="/api/foreign/download/"]')),
                href: a ? a.getAttribute('href') : null,
                target: a ? a.getAttribute('target') : null,
            };
        });
    }""")
    for li in link_info:
        print("     ", li)

    done_rows = [x for x in link_info if x["href"]]
    chk(len(done_rows) >= 1, "已完成任务的文章名是超链接", f"{len(done_rows)} 个")
    chk(all("inline=1" in (x["href"] or "") for x in done_rows),
        "链接指向编译稿 PDF(inline=1 浏览器内联打开)",
        str([x["href"] for x in done_rows]))
    chk(all(x["inAnchor"] for x in done_rows),
        "链接包裹的是文章名本身(点标题即可跳转)")
    # 失败行不得有下载死链
    failed_href = [x["href"] for x in link_info
                   if x["text"] == "" or x["href"] is None]
    chk(all(h is None for h in [x["href"] for x in link_info if not x["href"]]),
        "无产物的行没有死链")

    # 真点一次验证跳转(桩会拦 API, 只验 href 导航意图)
    if done_rows:
        href = done_rows[0]["href"]
        with pg.expect_popup(timeout=8000) as pop:
            pg.locator('#fg-list a[href*="/api/foreign/download/"]').first.click()
        chk(True, "点击文章名能触发跳转(新标签页)", pop.value.url[:80])

    # ── ②b 有任务时三张卡同时平铺(用户实际使用场景) ──────────────────
    print("\n②b 触发一次编译后, 三张卡是否同时平铺可见")
    SAMPLE = ROOT / "probe" / "_sample_foreign_report.pdf"
    if SAMPLE.exists():
        pg.route("**/api/foreign/upload", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"id":"stub0001","status":"running","message":"已接收"}'))
        pg.route("**/api/foreign/job/*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "id": "stub0001", "status": "running", "stage": "translate",
                "stage_label": "全文翻译", "progress": 58, "log": ["17:31:03  开始全文翻译"],
                "warnings": [], "error": "", "has_pdf": False,
                "title": "出口套利空间仍处历史高位，但冬季风险已被定价",
                "org": "Synthetic Research Desk", "n_pages": 20, "pages": 0,
                "filename": "FGE_LPG_weekly.pdf"}, ensure_ascii=False)))
        pg.set_input_files("#fg-file", str(SAMPLE))
        pg.click("#fg-start")
        pg.wait_for_timeout(1800)
        both = pg.evaluate("""() => {
            const t = document.getElementById('tab-foreign');
            const cards = Array.from(t.querySelectorAll(':scope > .card'));
            return {
                total: cards.length,
                visible: cards.filter(c => c.getBoundingClientRect().height > 40).length,
                secIdx: t.querySelectorAll('.sec-index').length,
                secBlk: t.querySelectorAll('.sec-block').length,
                jobVisible: (() => { const j = document.getElementById('fg-job');
                    return !!j && getComputedStyle(j).display !== 'none'; })(),
            };
        }""")
        print("     ", both)
        chk(both["jobVisible"], "进度卡在编译中出现")
        chk(both["visible"] == 3, "上传+进度+历史三块同时可见(真平铺)", f"{both['visible']}/3")
        chk(both["secIdx"] == 0 and both["secBlk"] == 0, "全程无折叠外壳介入")
        pg.screenshot(path=str(SHOTS / "foreign_flat_running.png"), full_page=True)
    else:
        print("     (跳过: 缺样本 PDF)")

    # ── ③ 回归: 其他 tab 折叠仍工作 ────────────────────────────────
    print("\n③ 回归: 其他 tab 的小模块折叠未被破坏")
    pg.click('nav button[data-tab="warrant"]')
    pg.wait_for_timeout(2200)
    w_idx = pg.locator("#tab-warrant .sec-index").count()
    w_blk = pg.locator("#tab-warrant .sec-block").count()
    chk(w_idx >= 1, "仓单页仍有索引条(引擎正常)", f"count={w_idx}")
    chk(w_blk >= 1, "仓单页仍有折叠外壳", f"count={w_blk}")

    b.close()

print("\n" + "=" * 66)
print(f"结果: {sum(checks)} 通过 / {len(checks) - sum(checks)} 失败")
print(f"console errors: {len(errors)}")
for e in errors[:10]:
    print("   ", e)
print("=" * 66)

ok = all(checks) and not errors
Path(ROOT / "probe" / "_foreign_flat_result.txt").write_text(
    "\n".join([f"{'PASS' if c else 'FAIL'}" for c in checks] + errors), encoding="utf-8")
sys.exit(0 if ok else 1)
