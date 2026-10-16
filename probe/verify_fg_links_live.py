# -*- coding: utf-8 -*-
"""浏览器验证(★ 用真实后端数据, 不桩 /api/foreign/jobs)。

为什么不用桩: 上一次验证就是栽在这 —— 桩数据里手写了 has_pdf=True, 于是"通过"了,
但真实接口根本不返回该字段, 线上链接一个都没渲染。桩只能验"渲染逻辑", 验不了"字段契约"。
本脚本只拦 auth(免登录), 历史列表走**真实接口**。

验收:
  ① 历史列表每条已完成任务的**文章名**都是指向编译稿的超链接
  ② 链接 href 指向 /api/foreign/download/<id>?inline=1, target=_blank
  ③ 失败任务保持纯文本(无死链)
  ④ 点一次标题能真的触发跳转(新标签页)
  ⑤ 自动刷新: 列表含 running 时, 8s 内应出现第二次 /api/foreign/jobs 请求;
     全终态时不应继续空转
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

out = []
checks = []


def chk(cond, label, detail=""):
    checks.append(bool(cond))
    out.append(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 1100})
    errors = []
    pg.on("console", lambda m: errors.append(f"CONSOLE[{m.type}]: {m.text[:200]}")
          if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {str(e)[:300]}"))

    reqs = []
    pg.on("request", lambda r: reqs.append(r.url))

    # 只拦 auth 免登录; 其余(含 /api/foreign/jobs)走真实后端
    pg.route("**/api/auth/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body='{"ok":true,"authenticated":true}'))

    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)

    out.append("① 切到外文研报页(真实数据)")
    pg.click('nav button[data-tab="foreign"]')
    pg.wait_for_timeout(2500)

    n_rows = pg.locator("#fg-list > div").count()
    out.append(f"     历史列表行数 = {n_rows}")
    chk(n_rows > 0, "历史列表已用真实数据渲染", f"{n_rows} 行")

    out.append("")
    out.append("② 文章名超链接(真实数据)")
    info = pg.evaluate("""() => {
        return Array.from(document.querySelectorAll('#fg-list > div')).map(r => {
            const a = r.querySelector('a[href*="/api/foreign/download/"]');
            const b = r.querySelector('b');
            const txt = r.textContent.replace(/\\s+/g, ' ').trim().slice(0, 40);
            return {
                row: txt,
                bold: b ? b.textContent.trim().slice(0, 30) : '',
                inAnchor: !!(b && b.closest('a[href*="/api/foreign/download/"]')),
                href: a ? a.getAttribute('href') : null,
                target: a ? a.getAttribute('target') : null,
                status: (r.textContent.match(/已完成|失败|进行中/) || [''])[0],
            };
        });
    }""")
    for i in info:
        out.append(f"     [{i['status']}] bold={i['bold'][:22]!r} href={i['href']} inAnchor={i['inAnchor']}")

    linked = [i for i in info if i["href"]]
    chk(len(linked) >= 1, "有任务的文章名是超链接", f"{len(linked)} 个")
    chk(all("inline=1" in (i["href"] or "") for i in linked),
        "链接都带 inline=1", str([i["href"] for i in linked])[:120])
    chk(all(i["inAnchor"] for i in linked), "链接包裹的是文章名本身")
    chk(all(i["target"] == "_blank" for i in linked), "链接 target=_blank")

    # 已完成/失败 与 有无链接 必须自洽
    done_rows = [i for i in info if i["status"] == "已完成"]
    fail_rows = [i for i in info if i["status"] == "失败"]
    chk(all(i["href"] for i in done_rows),
        "所有『已完成』行都有链接", f"{len(done_rows)} 行")
    chk(all(not i["href"] for i in fail_rows),
        "所有『失败』行都无链接(无死链)", f"{len(fail_rows)} 行")

    pg.screenshot(path=str(SHOTS / "fg_links_live.png"), full_page=True)

    out.append("")
    out.append("③ 真点击跳转 + 链接目标可达性")
    if linked:
        href = linked[0]["href"]
        with pg.expect_popup(timeout=20000) as pop:
            pg.locator('#fg-list a[href*="/api/foreign/download/"]').first.click()
        popup = pop.value
        # 【坑】不要断言 popup.url —— 目标被 Chrome 内置 PDF 查看器接管时, 该页面的
        #   URL 可能为空/为扩展页, 读不到真实地址(上一版就栽在这)。改为断言
        #   "确实打开了新页面"(浏览器跳转语义), 链接可达性另由下面的 HTTP 直测证明。
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        out.append(f"     已打开新页面(URL 由 PDF 查看器接管, 可能是空): {popup.url!r}")
        chk(popup is not None, "点击文章名打开了新标签页(浏览器跳转生效)")
        popup.close()

        # 用 HTTP 直测该链接是否真能取到 PDF(排除死链)
        try:
            import urllib.request
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            resp = op.open("http://127.0.0.1:5000" + href, timeout=60)
            ct = resp.headers.get("Content-Type", "")
            body = resp.read(5)
            out.append(f"     链接目标: HTTP {resp.status} ct={ct[:40]} magic={body!r}")
            chk(resp.status == 200 and body.startswith(b"%PDF"),
                "链接目标真实返回 PDF(非死链)", f"{resp.status} {ct[:30]}")
        except Exception as e:
            out.append(f"     链接目标校验失败: {type(e).__name__}: {e}")
            chk(False, "链接目标真实返回 PDF(非死链)", str(e)[:60])
    else:
        chk(False, "有点击目标可测", "无链接")

    out.append("")
    out.append("④a 全终态时列表不空转轮询(真实数据)")
    n_before = len([u for u in reqs if "/api/foreign/jobs" in u])
    has_running = pg.evaluate(
        "Array.from(document.querySelectorAll('#fg-list > div')).some(r => r.textContent.includes('进行中'))")
    out.append(f"     列表当前含『进行中』行 = {has_running}")
    pg.wait_for_timeout(9500)
    n_after = len([u for u in reqs if "/api/foreign/jobs" in u])
    out.append(f"     /api/foreign/jobs 请求数: 采样前 {n_before} → 9.5s 后 {n_after}")
    if has_running:
        chk(n_after > n_before, "有进行中任务时会自动重拉列表", f"{n_before} -> {n_after}")
    else:
        chk(n_after == n_before, "全终态时不产生额外请求(不空转)", f"{n_before} -> {n_after}")

    out.append("")
    out.append("④b 存在『进行中』任务时必须自动重拉(桩造 running 场景)")
    # 【为什么这里可以用桩】本次验的是**刷新行为**(纯前端逻辑);
    #   字段契约已在上面的真实数据用例中验过, 不依赖桩。
    pg.route("**/api/foreign/jobs*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"jobs": [{
            "id": "aaaa11112222", "filename": "x.pdf", "title": "进行中的示例报告",
            "org": "Org", "status": "running", "stage": "translate",
            "stage_label": "全文翻译", "progress": 40, "message": "翻译进度 4/10 段",
            "error": "", "created_at": "2026-09-30 16:00:00", "finished_at": "",
            "n_pages": 10, "pages": 0, "chars": 0,
            "has_pdf": False, "out_name": "",
        }]}, ensure_ascii=False)))
    n0 = len([u for u in reqs if "/api/foreign/jobs" in u])
    pg.evaluate("App.loadForeignJobs()")          # 触发一次, 进入 running 态
    pg.wait_for_timeout(1200)
    n1 = len([u for u in reqs if "/api/foreign/jobs" in u])
    pg.wait_for_timeout(9500)                     # 等一个 8s 周期
    n2 = len([u for u in reqs if "/api/foreign/jobs" in u])
    out.append(f"     请求数: 触发后 {n1} → 再等 9.5s → {n2}")
    chk(n2 > n1, "列表含 running 时自动重拉(这是『一直进行中』的修复)", f"{n1} -> {n2}")
    # 切回终态应停止
    pg.unroute("**/api/foreign/jobs*")
    pg.evaluate("App.loadForeignJobs()")
    pg.wait_for_timeout(1200)
    n3 = len([u for u in reqs if "/api/foreign/jobs" in u])
    pg.wait_for_timeout(9500)
    n4 = len([u for u in reqs if "/api/foreign/jobs" in u])
    out.append(f"     切回终态: {n3} → {n4}")
    chk(n4 == n3, "回到全终态后停止轮询(不无限重拉)", f"{n3} -> {n4}")

    b.close()

out.append("")
out.append("=" * 70)
out.append(f"结果: {sum(checks)} 通过 / {len(checks) - sum(checks)} 失败")
out.append(f"console errors: {len(errors)}")
for e in errors[:8]:
    out.append("   " + e)
out.append("=" * 70)

text = "\n".join(out)
(ROOT / "probe" / "_fgl2.txt").write_text(text, encoding="utf-8")
print(text)
sys.exit(0 if all(checks) and not errors else 1)
