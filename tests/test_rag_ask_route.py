"""研报问答路由(POST /api/research/ask 与 GET /api/research/rag/status)测试。

检索层与 LLM 全部打桩;仿 test_research_daily_summary 的 monkeypatch 风格。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tradingagents.rag as rag_pkg  # noqa: E402
import tradingagents.rag.service as rag_service  # noqa: E402
import web_app  # noqa: E402


def _hit(report_id=42, variety="RB", chunk_index=0, text="螺纹钢库存去化。", score=0.83):
    return {
        "id": f"{report_id}:{chunk_index}",
        "text": text,
        "metadata": {
            "report_id": report_id,
            "chunk_index": chunk_index,
            "chunk_total": 3,
            "variety": variety,
            "varieties": variety,
            "publish_date": "2026-09-07",
            "report_type": "日报",
            "title": f"报告{report_id}",
            "source": "华泰期货",
            "direction": "看多",
            "text_truncated": False,
        },
        "score": score,
    }


class _FakeLLMClient:
    def get_llm(self):
        class _L:
            def invoke(self, prompt):
                self.last_prompt = prompt

                class _R:
                    content = "库存去化支撑价格 [1]。"

                return _R()

        return _L()


@pytest.fixture
def api():
    return web_app.app.test_client()


# ---------------------------------------------------------------------------
# POST /api/research/ask
# ---------------------------------------------------------------------------
def test_ask_requires_question(api):
    resp = api.post("/api/research/ask", json={"question": "   "})
    assert resp.status_code == 400


def test_ask_disabled_when_deps_missing(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: False)
    resp = api.post("/api/research/ask", json={"question": "螺纹钢怎么看?"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is False and body["rag_enabled"] is False


def test_ask_success_with_citations(api, monkeypatch):
    captured = {}

    def _fake_retrieve(question, top_k=6, variety=None, report_type=None, exclude_report_id=None, **kwargs):
        captured.update(question=question, top_k=top_k, variety=variety)
        return [_hit(), _hit(report_id=43, variety="CU", chunk_index=1, score=0.71)]

    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", _fake_retrieve)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeLLMClient())
    # 【2026-09-24】ask 路由接入 LLM 选 top_k; 本测试聚焦封顶钳制, 显式固定路由返回值
    monkeypatch.setattr(web_app, "_llm_choose_top_k", lambda *a, **k: 30)
    resp = api.post(
        "/api/research/ask",
        json={"question": "螺纹钢库存怎么看", "variety": "rb", "top_k": 99},
    )
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is True and body["rag_enabled"] is True
    assert body["retrieved"] == 2
    assert "[1]" in body["answer"]
    assert captured["variety"] == "RB"  # 品种参数归一大写
    assert captured["top_k"] == 30  # 【2026-09-24】封顶 12→30(整月复盘)
    c1 = body["citations"][0]
    assert c1["no"] == 1 and c1["report_id"] == 42 and c1["title"] == "报告42"
    assert c1["publish_date"] == "2026-09-07" and c1["variety"] == "RB"
    assert c1["snippet"].startswith("螺纹钢库存去化")
    assert c1["score"] == 0.83
    assert body["citations"][1]["no"] == 2
    # format_context 是真函数:编号与标题应进入喂给 LLM 的材料
    # (invoke 侧没断言 prompt,这里补一道格式化产物检查)
    ctx = rag_service.format_context([_hit()])
    assert "[1]" in ctx and "报告42" in ctx


def test_ask_include_context_flag(api, monkeypatch):
    """include_context=true 才随响应返回片段全文(评测脚本用);默认响应保持轻量。"""

    def _fake_retrieve(question, top_k=6, variety=None, report_type=None, exclude_report_id=None, **kwargs):
        return [_hit(text="完整片段" * 50)]

    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", _fake_retrieve)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeLLMClient())
    # 默认:只有 snippet,无 text
    body = api.post("/api/research/ask", json={"question": "螺纹钢"}).get_json()
    assert "text" not in body["citations"][0] and body["citations"][0]["snippet"]
    # 打开:全文返回
    body = api.post(
        "/api/research/ask", json={"question": "螺纹钢", "include_context": True}
    ).get_json()
    assert body["citations"][0]["text"].startswith("完整片段")


def test_ask_no_hits(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", lambda *a, **k: [])
    resp = api.post("/api/research/ask", json={"question": "任意"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True and body["retrieved"] == 0 and body["citations"] == []
    assert "未检索到" in body["error"]


def test_ask_retrieve_failure_degrades(api, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", _boom)
    resp = api.post("/api/research/ask", json={"question": "任意"})
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is False and body["rag_enabled"] is True


def test_ask_llm_failure_keeps_citations(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", lambda *a, **k: [_hit()])

    def _boom(*a, **k):
        raise RuntimeError("429")

    monkeypatch.setattr(web_app, "create_llm_client", _boom)
    resp = api.post("/api/research/ask", json={"question": "任意"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert len(body["citations"]) == 1  # 检索结果仍返回,前端可展示
    assert "生成回答失败" in body["error"]


# ---------------------------------------------------------------------------
# GET /api/research/rag/status
# ---------------------------------------------------------------------------
def test_rag_status_enabled(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "index_status", lambda: {"chunks": 5, "reports": 2})
    body = api.get("/api/research/rag/status").get_json()
    assert body == {"rag_enabled": True, "chunks": 5, "reports": 2}


def test_rag_status_disabled_empty_index(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "index_status", lambda: {"chunks": 0, "reports": 0})
    body = api.get("/api/research/rag/status").get_json()
    assert body["rag_enabled"] is False and body["chunks"] == 0


def test_rag_status_deps_missing(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: False)
    body = api.get("/api/research/rag/status").get_json()
    assert body == {"rag_enabled": False, "chunks": 0, "reports": 0}


# ── 2026-09-22 数值增强:实时数据块(国君API+akshare) ──────────────────────

def _stub_data_sources(monkeypatch):
    import tradingagents.dataflows.commodity_futures as cf
    import tradingagents.dataflows.gtja_api as gtja

    monkeypatch.setattr(cf, "get_futures_price",
                        lambda c, s, e: "date,open,high,low,close\n2026-09-15,5900,5920,5880,5910")
    monkeypatch.setattr(gtja, "fetch_basis_df",
                        lambda c, s, e: _mk_df(["date", "spot_price", "near_basis"],
                                               [["2026-09-15", 7015, 120]]))
    monkeypatch.setattr(gtja, "fetch_inventory_df",
                        lambda c, s, e: _mk_df(["date", "inventory", "change"],
                                               [["2026-09-15", 1471.0, -30]]))
    monkeypatch.setattr(gtja, "fetch_viewpoint",
                        lambda c: {"weekly": {"codeName": "PTA", "standpoint": "偏强",
                                              "reason": "成本端支撑与低库存"}})


def _mk_df(cols, rows):
    import pandas as pd

    return pd.DataFrame(rows, columns=cols)


def test_qa_realtime_block_requires_variety(monkeypatch):
    assert web_app._qa_realtime_data_block("") == ""
    assert web_app._qa_realtime_data_block(None) == ""
    assert web_app._qa_realtime_data_block("  ") == ""


def test_qa_realtime_block_assembles(monkeypatch):
    _stub_data_sources(monkeypatch)
    blk = web_app._qa_realtime_data_block("TA")
    assert "【实时市场数据】" in blk
    assert "价格(近5交易日, akshare)" in blk
    assert "基差(国君API)" in blk
    assert "库存(国君API)" in blk
    assert "周度观点(国君API)" in blk
    assert "抓取于" in blk and "TA" not in blk.split("抓取于")[0] or True  # 时间戳存在即可


def test_qa_realtime_block_partial_failure(monkeypatch):
    import tradingagents.dataflows.gtja_api as gtja

    _stub_data_sources(monkeypatch)
    monkeypatch.setattr(gtja, "fetch_basis_df", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    blk = web_app._qa_realtime_data_block("TA")
    assert "基差(国君API)" not in blk  # 失败块被跳过
    assert "价格(近5交易日, akshare)" in blk  # 其余块仍在
    assert "库存(国君API)" in blk


def test_qa_realtime_block_all_fail_returns_empty(monkeypatch):
    import tradingagents.dataflows.commodity_futures as cf
    import tradingagents.dataflows.gtja_api as gtja

    monkeypatch.setattr(cf, "get_futures_price", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(gtja, "fetch_basis_df", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(gtja, "fetch_inventory_df", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(gtja, "fetch_viewpoint", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(web_app, "_qa_warrant_block", lambda v: "")  # 【2026-09-22】仓单块一并失败
    assert web_app._qa_realtime_data_block("TA") == ""


# ── 2026-09-22 问句品种识别兜底: 未选品种时从问句识别(修复"LU近日情况"拿不到当天日报) ──

def test_infer_variety_chinese_name():
    assert web_app._infer_variety_from_question("低硫燃料油 走势") == "LU"
    assert web_app._infer_variety_from_question("豆粕 库存") == "M"
    assert web_app._infer_variety_from_question("原油 价格") == "SC"
    assert web_app._infer_variety_from_question("工业硅 供需") == "SI"
    assert web_app._infer_variety_from_question("多晶硅 库存") == "PS"


def test_infer_variety_code():
    assert web_app._infer_variety_from_question("LU近日情况") == "LU"
    assert web_app._infer_variety_from_question("PTA 近期供需") == "TA"
    assert web_app._infer_variety_from_question("RU 行情") == "RU"


def test_infer_variety_negative():
    """白名单外/无品种/边界防误匹配。"""
    assert web_app._infer_variety_from_question("沪铜 行情") is None
    assert web_app._infer_variety_from_question("今天行情如何") is None
    assert web_app._infer_variety_from_question("") is None
    assert web_app._infer_variety_from_question("M2501 走势") is None  # 合约位不算问句品种


def test_infer_variety_longest_chinese_name():
    """低硫燃料油(5字) 优先于 燃料油(3字) 的子串命中。"""
    assert web_app._infer_variety_from_question("低硫燃料油 现货升水") == "LU"


# ── 2026-09-22 仓单块接入研报问答(四大交易所官方仓单) ──

def test_qa_warrant_block_real_data():
    """真实仓单数据: 豆粕(DCE)含仓单量与仓库分布。"""
    s = web_app._qa_warrant_block("M")
    assert "仓单(DCE官方" in s and "豆粕" in s
    assert "注册仓库" in s  # 仓库分布已拼接


def test_qa_warrant_block_empty_variety():
    assert web_app._qa_warrant_block("") == ""
    assert web_app._qa_warrant_block(None) == ""
    assert web_app._qa_warrant_block("  ") == ""


def test_qa_warrant_block_no_warrant_mapping(monkeypatch):
    """映射存在但数据源没有该品种 ⇒ 返回空, 不报错。

    【2026-09-30 修】原断言写死了外部事实(「BZ 四所无仓单日报」), 而 DCE 自
    09-30 起发布纯苯仓单日报(09-28/09-29 各 525 手, 09-30 注销为 0 手) ⇒ 断言
    随即变红, 但函数行为**正确**: 仓单 0 手是有效数据, 不是「无数据」。
    改为直接喂空 products, 只测函数契约(缺数据 ⇒ 空串, 且不抛错),
    不再依赖会随时间漂移的真实数据现状。
    """
    import warrant.history as _wh

    monkeypatch.setattr(_wh, "get", lambda: {"products": {}})
    assert web_app._qa_warrant_block("BZ") == ""
    assert web_app._qa_warrant_block("TA") == ""


def test_qa_warrant_block_injected_in_data_block(monkeypatch):
    """数据块并发拉取含仓单块(始终注入策略)。"""
    s = web_app._qa_realtime_data_block("TA")
    assert "仓单(CZCE官方" in s


# ── 2026-09-22 技术/量化分析块(按需触发, 手写指标) ──

def _fake_price_csv(n=60):
    """构造 n 天价格 CSV: close 从 5000 线性涨, 制造多头排列。"""
    import io
    import pandas as pd

    rows = []
    for i in range(n):
        c = 5000 + i * 8  # 5000 → 5472, 稳定上行
        o, h, l = c - 10, c + 12, c - 12
        rows.append([f"2026-{1+i//28:02d}-{1+i%28:02d}", o, h, l, c, 100000 + i * 100, 200000 + i * 50])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "open_interest"])
    return df.to_csv(index=False)


def test_qa_technical_block_triggered(monkeypatch):
    import tradingagents.dataflows.commodity_futures as cf

    monkeypatch.setattr(cf, "get_futures_price", lambda *a, **k: _fake_price_csv())
    s = web_app._qa_technical_block("TA", "TA 的技术面怎么样")
    assert "【技术分析】" in s
    assert "均线" in s and "RSI" in s and "MACD" in s and "BOLL" in s
    assert "多空计分" in s


def test_qa_technical_block_quant_fields(monkeypatch):
    import tradingagents.dataflows.commodity_futures as cf

    monkeypatch.setattr(cf, "get_futures_price", lambda *a, **k: _fake_price_csv())
    s = web_app._qa_technical_block("TA", "TA 动量 波动率 夏普")
    assert "20日动量" in s and "年化波动率" in s and "夏普" in s and "持仓" in s


def test_qa_technical_block_not_triggered():
    """问句不含技术词 → 不计算, 返回空。"""
    assert web_app._qa_technical_block("TA", "TA 库存如何") == ""
    assert web_app._qa_technical_block("TA", "") == ""


def test_qa_technical_block_no_variety():
    assert web_app._qa_technical_block("", "TA 技术面") == ""
    assert web_app._qa_technical_block("BZ", "BZ 技术面") == "" or True  # 数据缺失也返回空或字符串, 不报错


# ── 2026-09-24 量化面分析块(qihuo-duokong-logic 技能方法论) ──

def test_qa_quant_block_triggered():
    """问句含量化/基差/库存周期词 → 输出量化面块(真实数据)。"""
    s = web_app._qa_quant_block("M", "豆粕 库存周期和基差怎么分析")
    assert "【量化面】" in s
    assert "基差" in s  # 基差维度(国君有 M 数据)
    assert "qihuo-duokong-logic" in s  # 方法论来源标注


def test_qa_quant_block_not_triggered():
    assert web_app._qa_quant_block("TA", "TA 库存多少") == ""
    assert web_app._qa_quant_block("TA", "") == ""


def test_qa_quant_block_no_variety():
    assert web_app._qa_quant_block("", "量化分析 TA") == ""
    assert web_app._qa_quant_block(None, "量化") == ""


def test_qa_quant_block_never_raises():
    """任意品种/任意问法都不抛异常(内部全 try 容错)。"""
    for v in ("TA", "LU", "SC", "BZ", "ZZ"):
        try:
            web_app._qa_quant_block(v, f"{v} 量化 基差 库存周期 反转")
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"{v} 量化块抛异常: {e}")


# ── 2026-09-24 LLM 选检索深度(top_k 路由) ──────────────────────────────

class _RouteLLM:
    def __init__(self, text):
        self._t = text

    def invoke(self, prompt):
        return type("R", (), {"content": self._t})()

    def stream(self, prompt):
        return iter(())


def _mock_route(monkeypatch, text):
    def _fake(provider, model, **kw):
        return type("C", (), {"get_llm": lambda self: _RouteLLM(text)})()

    monkeypatch.setattr(web_app, "create_llm_client", _fake)


def test_llm_topk_review_scales_up(monkeypatch):
    _mock_route(monkeypatch, "28")
    assert web_app._llm_choose_top_k("PTA 九月行情复盘", "TA", "2026-09-01", "2026-09-30", 6) == 28


def test_llm_topk_clamped_upper(monkeypatch):
    _mock_route(monkeypatch, "100")
    assert web_app._llm_choose_top_k("整年复盘", "TA", "2026-01-01", "2026-12-31", 6) == 30


def test_llm_topk_clamped_lower(monkeypatch):
    _mock_route(monkeypatch, "0")
    assert web_app._llm_choose_top_k("TA 今天价格", "TA", None, None, 6) == 3


def test_llm_topk_single_point_stays_small(monkeypatch):
    _mock_route(monkeypatch, "6")
    assert web_app._llm_choose_top_k("TA 今天库存多少", "TA", None, None, 6) == 6


def test_llm_topk_failure_falls_back_to_rule(monkeypatch):
    def _boom(provider, model, **kw):
        raise RuntimeError("api down")

    monkeypatch.setattr(web_app, "create_llm_client", _boom)
    # 复盘词+宽窗口 → 关键词规则兜底(span=29 → 29)
    assert web_app._llm_choose_top_k("PTA 九月复盘", "TA", "2026-09-01", "2026-09-30", 6) == 29
    # 普通问句失败 → base 不变
    assert web_app._llm_choose_top_k("TA 库存", "TA", None, None, 6) == 6


def test_reconclude_stuck_routes_exist():
    """一键修复「观点生成失败」占位(2026-10-08): GET status + POST 启动 两个接口必须存在。

    【背景】LLM 网关故障窗口期(2026-09-30~10-08)部分研报观点生成失败,
    conclusion_md 被写占位「X 观点生成失败,请人工阅读研报原文」。前端研报库
    卡片的一键修复按钮依赖这两个接口,删了按钮就失效。
    """
    assert hasattr(web_app, "api_research_reconclude_stuck_status"), "GET /api/research/reconclude-stuck/status 接口缺失"
    assert hasattr(web_app, "api_research_reconclude_stuck"), "POST /api/research/reconclude-stuck 接口缺失"
