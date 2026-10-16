"""页面回归检查:确认前端网络护栏没有破坏主页面初始化。

检查项:
  1. 页面无 JS 控制台错误(尤其 Init 阶段)
  2. 品种下拉被 /api/varieties 填充(证明 App.init() 跑通)
  3. 研报页的"每日总结"卡片不是"加载失败"
  4. 护栏已安装(window.fetch 被包装)
  5. 研报问答(QA)卡片结构完整:会话下拉 / 日期范围 / 错题本面板 / RAG 回填按钮
     (2026-09-14 合并 QA 模块后新增;这些 id 一旦被改坏,前端会静默失效)
  6. 研报库队列口径:#res-count 要能报出「N 份 · k 处理中 · m 排队中」,
     且 pending 行的徽标渲染成「排队中」(2026-09-14:批量导入必须一眼看出
     整批在排队,而不是呈现成"只有一篇在处理")
"""
import json
import sys
from playwright.sync_api import sync_playwright

# 【控制台编码 2026-09-14】Windows 控制台默认 GBK,页面正文里带 emoji(✨ 等)时
# print 会抛 UnicodeEncodeError 把冒烟脚本打断(实测就崩在"每日总结卡片正文"那行,
# 导致后面的 QA 卡片检查根本没跑)。这里强制 stdout 用 UTF-8 + 替换,保证脚本跑完。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "http://127.0.0.1:5000/"
errors, warnings, bad_resp = [], [], []

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.on("console", lambda m: (errors if m.type == "error" else warnings).append(m.text))
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("response", lambda r: bad_resp.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3500)

    guarded = page.evaluate("() => window.fetch.toString().includes('__nativeFetch')")
    print("护栏已安装(window.fetch 被包装):", guarded)

    n_opt = page.evaluate("() => (document.querySelector('#variety-select') || document.querySelector('select')).options.length")
    print("首个下拉选项数(>1 说明 App.init 跑通):", n_opt)

    # 切到研报页
    page.evaluate("() => App.switchTab('research')")
    page.wait_for_timeout(4000)
    daily = page.inner_text("#res-daily") if page.query_selector("#res-daily") else "(无 #res-daily)"
    tag = page.inner_text("#res-daily-tag") if page.query_selector("#res-daily-tag") else ""
    print("每日总结卡片状态标记:", repr(tag))
    print("每日总结卡片正文:", repr(daily[:180].replace("\n", " ")))
    print("是否仍显示'加载失败':", "加载失败" in daily)

    toasts = page.eval_on_selector_all("#toasts .toast", "els => els.map(e => e.textContent)")
    print("页面内 toast:", toasts)

    # --- QA(研报问答)卡片结构检查 ---
    qa_ids = [
        "#res-ask-question", "#res-ask-variety", "#res-ask-type",
        "#res-qa-session", "#res-qa-from", "#res-qa-to", "#res-qa-insights",
        "#res-ask-hist", "#res-rag-index-btn",
    ]
    missing = [i for i in qa_ids if not page.query_selector(i)]
    print("QA 卡片关键节点:", "全部存在" if not missing else ("缺失 " + ", ".join(missing)))

    # QA 前端方法是否挂到 App 上(合并时漏挂会静默失效)
    qa_methods = ["askResearch", "switchQaSession", "newQaSession", "deleteQaSession",
                  "loadQaInsights", "addQaTerm", "sendQaFeedback", "indexRag", "_loadQaSessions"]
    miss_m = page.evaluate("(ms) => ms.filter(m => typeof App[m] !== 'function')", qa_methods)
    print("QA 前端方法:", "全部就绪" if not miss_m else ("缺失 " + ", ".join(miss_m)))

    # 会话下拉是否成功拉到后端会话(证明 /api/research/qa/sessions 链路通)
    sess_opts = page.eval_on_selector("#res-qa-session", "el => el.options.length")
    print("QA 会话下拉选项数(>=1 说明会话接口已响应):", sess_opts)

    # --- 研报库队列口径检查(2026-09-14) ---
    # 【为什么查】#res-count 是用户判断"到底几篇在跑"的第一入口。批量导入后必须
    # 一眼看出"整批都在队列里、其中 N 路在跑"——旧版只报总数,配合"边处理边建行"
    # 的建行时机,就会呈现成"研报库只有一篇在处理"。
    # 先看真实数据,再用拦截的合成数据证明「排队中」这一档渲染路径通(真跑一次
    # 批量导入才能看到 pending,代价太高)。
    page.evaluate("() => App._refreshResearch()")
    page.wait_for_timeout(3000)
    print("研报库计数(真实):", repr(page.evaluate("() => (document.getElementById('res-count') || {}).textContent")))

    fake = {"reports": [
        {"id": 9001 + i, "status": "pending", "title": "排队样例%d" % i, "variety": "",
         "source": "本地导入-研报暂存", "ingest_source": "auto",
         "uploaded_at": "2026-09-14 14:00:00", "varieties": "", "direction": "",
         "confidence": None, "report_type": ""} for i in range(4)
    ] + [
        {"id": 9101 + i, "status": "processing", "title": "处理样例%d" % i, "variety": "",
         "source": "本地导入-研报暂存", "ingest_source": "auto",
         "uploaded_at": "2026-09-14 14:00:00", "varieties": "", "direction": "",
         "confidence": None, "report_type": ""} for i in range(2)
    ]}
    page.route("**/api/research?*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8",
        body=json.dumps(fake, ensure_ascii=False),
    ))
    page.evaluate("() => App._refreshResearch()")
    page.wait_for_timeout(1500)
    print("研报库计数(合成 4 排队 + 2 处理):",
          repr(page.evaluate("() => document.getElementById('res-count').textContent")))
    print("状态徽标(合成):", page.evaluate(
        "() => Array.from(document.querySelectorAll('#res-list .badge'))"
        ".map(x => x.textContent).filter(t => /处理中|排队中/.test(t))"
    ))

    # --- 同源合并口径检查(2026-09-14) ---
    # 【为什么查】"同一家公司同品种多篇"必须先合并成一条。用户看到过"四个研报源、
    # 六个观点"的表格,根因就是同源多篇各占一行。这里用拦截的合成数据喂一个
    # report_rows(1 家 3 篇 + 1 家 1 篇),证明前端按"家"渲染且把该机构全部篇目列出来。
    # 真跑一次生成要烧 LLM,所以走合成数据。
    merged = {"date": "2026-09-13", "report_type": "周报", "varieties": [{
        "code": "SC", "name": "原油", "report_count": 2, "merged_total": 4,
        "directions": ["看多", "看空"], "summary_md": None, "generated_at": "", "reports": 0,
        "report_rows": [
            {"report_id": 11, "report_ids": [11, 12, 13],
             "titles": [{"report_id": 11, "title": "能源数据周报"},
                        {"report_id": 12, "title": "Weekly Updates on Energy"},
                        {"report_id": 13, "title": "美国CPI 符合预期"}],
             "merged_count": 3, "source": "东证", "source_raw": "东证期货-繁微MCP",
             "title": "能源数据周报", "direction": "看多", "confidence": "62%",
             # 【衍生策略分列 2026-09-15】有值 → 该列显示策略原文(不并进方向列)
             "deriv": "期现与月差均可考虑逢低正套; 多PX空PTA"},
            {"report_id": 21, "report_ids": [21],
             "titles": [{"report_id": 21, "title": "华泰期货原油周报20260913"}],
             "merged_count": 1, "source": "华泰", "source_raw": "华泰期货",
             "title": "华泰期货原油周报20260913", "direction": "看空", "confidence": "70%",
             # 无衍生策略 → 该单元格显示 "—"(灰),而不是空白把列撑塌
             "deriv": ""},
        ],
    }]}
    page.route("**/api/research/variety-summary/*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8",
        body=json.dumps(merged, ensure_ascii=False),
    ))
    page.evaluate("() => App.switchTab('research')")
    page.evaluate("() => App.loadVarietySummaries && App.loadVarietySummaries()")
    page.wait_for_timeout(2500)
    vsum_txt = page.evaluate(
        "() => (document.getElementById('res-vsum-grid') || {}).innerText || ''")
    vsum_html = page.evaluate(
        "() => (document.getElementById('res-vsum-grid') || {}).innerHTML || ''")
    print("来源观点对比表头含『2 家 · 共 4 篇』:", "2 家 · 共 4 篇" in vsum_txt or "2 家 · 共 4 篇" in vsum_html)
    print("合并条目标注『3 篇已合并』:", "3 篇已合并" in vsum_txt or "3 篇已合并" in vsum_html)
    print("该机构全部篇目链接数(东证 3 篇应全在):",
          vsum_html.count("/api/research/11/file") + vsum_html.count("/api/research/12/file")
          + vsum_html.count("/api/research/13/file"))
    print("卡片计数含『2 家 · 共 4 篇』或『2 家』:", "2 家" in vsum_txt)

    # --- 方向 / 衍生策略分列检查(2026-09-15 用户定) ---
    # 【为什么查】单品种观点对比表原先只给一个方向标签,把研报里的组合思路(期现正套、
    # 月差反套、多PX空PTA)整个吃掉 —— 用户看到的"观点"比研报窄。现在两列并存:
    # 方向列只回答单边看多/看空/中性;衍生策略列放非单边策略,没提及显示 —。
    print("对比表含『衍生策略』列头:", "衍生策略" in vsum_html)
    print("方向仍独立成列(看多/看空都在):",
          ("看多" in vsum_txt) and ("看空" in vsum_txt))
    print("衍生策略单元格渲染出策略原文:",
          "期现与月差均可考虑逢低正套" in vsum_txt)
    print("无衍生策略的条目显示 —(而不是空白):",
          page.evaluate(
              "() => Array.from(document.querySelectorAll('#res-vsum-grid td'))"
              ".some(td => td.textContent.trim() === '—')"))
    print("衍生策略未被并进方向徽标:",
          page.evaluate(
              "() => !Array.from(document.querySelectorAll('#res-vsum-grid .badge'))"
              ".some(b => b.textContent.includes('正套'))"))

    print("--- 控制台错误 ---")
    for e in errors[:8]:
        print("  ", e[:160])
    print("(共 %d 条)" % len(errors))
    print("--- HTTP >= 400 的请求 ---")
    for r in bad_resp[:8]:
        print("  ", r[:160])
    print("(共 %d 条)" % len(bad_resp))
    b.close()
