"""自传数据(user_datasets)链路测试:文件解析 → LLM 规格 → 归一化 → 注入文本
→ 数据库隔离(client_tag)→ 上传/列表/删除路由(2026-09-07)。全 mock——
不触真实 LLM/网络;DB 用 tmp_path 真库,路由测试 monkeypatch web_app.get_db。
"""

import io
import json

import pandas as pd
import pytest

import web_app
from database import AgentSenseDB
from tradingagents.dataflows.user_data import (
    _norm_date,
    _parse_markdown_tables,
    detect_spec,
    ingest_file,
    normalize_rows,
    read_file_rows,
    render_user_data_context,
)

# ── 文件 → 原始行(确定性解析) ────────────────────────────────────────────

def test_parse_markdown_tables_basic():
    text = "# 标题(忽略)\n| 日期 | 库存 |\n|---|---|\n| 2026-09-01 | 68.5 |\n\n正文行(中断表格)\n| 日期 | 开工率 |\n|---|---|\n| 2026-09-02 | 73.9 |"
    rows = _parse_markdown_tables(text)
    assert len(rows) == 2
    assert rows[0] == {"日期": "2026-09-01", "库存": "68.5", "_table": 1}
    assert rows[1]["_table"] == 2  # 第二张表


def test_read_file_rows_md(tmp_path):
    f = tmp_path / "data.md"
    f.write_text("| 日期 | 价格 |\n|---|---|\n| 2026-09-01 | 3220 |", encoding="utf-8")
    rows = read_file_rows(str(f))
    assert rows[0]["价格"] == "3220"


def test_read_file_rows_csv_and_xlsx(tmp_path):
    csv = tmp_path / "a.csv"
    csv.write_text("日期,开工率\n2026-09-01,73.9\n", encoding="utf-8")
    rows = read_file_rows(str(csv))
    assert rows[0]["开工率"] == 73.9

    xlsx = tmp_path / "b.xlsx"
    pd.DataFrame({"日期": ["2026-09-01"], "库存": [68.5]}).to_excel(xlsx, index=False)
    rows = read_file_rows(str(xlsx))
    assert rows[0]["库存"] == 68.5
    assert rows[0]["_sheet"]  # 行附 sheet 名标记


def test_read_file_rows_unsupported_and_empty(tmp_path):
    f = tmp_path / "x.docx"
    f.write_bytes(b"xx")
    with pytest.raises(ValueError):
        read_file_rows(str(f))
    empty = tmp_path / "e.md"
    empty.write_text("没有任何表格的纯文本", encoding="utf-8")
    with pytest.raises(ValueError):
        read_file_rows(str(empty))


# ── LLM 看样张产规格 ──────────────────────────────────────────────────────

class _FakeLLMClient:
    """假 LLM 客户端:get_llm().invoke 返回围栏 JSON(detect_spec 消费)。"""

    def __init__(self, reply):
        self._reply = reply

    def get_llm(self):
        outer = self

        class _Msg:
            content = outer._reply

        class _LLM:
            def invoke(self, _prompt):
                return _Msg()

        return _LLM()


def test_detect_spec_parses_fenced_json():
    reply = "```json\n" + json.dumps({
        "variety": "MA", "data_type": "甲醇库存", "date_column": "日期",
        "columns": [{"name": "库存", "meaning": "社会库存", "unit": "万吨"}],
        "frequency": "周度", "notes": "",
    }, ensure_ascii=False) + "\n```"
    spec = detect_spec("ma.xlsx", [{"日期": "2026-09-01", "库存": 68.5}], "", _FakeLLMClient(reply))
    assert spec["variety"] == "MA"
    assert spec["date_column"] == "日期"


def test_detect_spec_hint_overrides_and_bad_json_raises():
    spec = detect_spec("x.csv", [{"d": "1"}], "TA", _FakeLLMClient('{"variety":"MA"}'))
    assert spec["variety"] == "TA"  # 用户手选品种优先
    with pytest.raises(ValueError):
        detect_spec("x.csv", [{"d": "1"}], "", _FakeLLMClient("不是 JSON"))


# ── 归一化 ────────────────────────────────────────────────────────────────

def test_norm_date_variants():
    assert _norm_date("2026/9/1").startswith("2026-09-01")
    assert _norm_date("第1周") == "第1周"  # 非标准日期原样保留
    assert _norm_date(None) == ""


def test_normalize_rows_strips_internal_keys_and_converts_dates():
    rows = [{"日期": "2026/09/01", "库存": 68.5, "_sheet": "s1"}]
    out = normalize_rows(rows, {"date_column": "日期"})
    assert out[0] == {"日期": "2026-09-01 00:00:00", "库存": 68.5}


# ── 注入文本 + client_tag 隔离 ────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """真库(tmp_path)+ 打桩 user_data 内部懒导入的 database.get_db。"""
    db = AgentSenseDB(tmp_path / "t.db")
    import database
    monkeypatch.setattr(database, "get_db", lambda: db)
    return db


def _make_done_dataset(db, variety="MA", client_tag="1.2.3.4", filename="a.xlsx",
                       date_col="日期"):
    spec = {"variety": variety, "data_type": "库存数据", "date_column": date_col,
            "columns": [{"name": "库存", "meaning": "社会库存", "unit": "万吨"}],
            "frequency": "周度", "notes": ""}
    rows = [{"日期": "2026-09-01", "库存": 68.5}]
    did = db.insert_user_dataset(filename, "/tmp/x", variety, client_tag=client_tag)
    db.update_user_dataset(did, variety=variety, data_type="库存数据",
                           spec=json.dumps(spec), data=json.dumps(rows),
                           row_count=1, status="done", error="")
    return did


def test_render_scoped_by_client_tag(tmp_db):
    _make_done_dataset(tmp_db, client_tag="1.2.3.4")
    _make_done_dataset(tmp_db, client_tag="5.6.7.8")
    ctx = render_user_data_context("MA", client_tag="1.2.3.4")
    assert "用户自传数据" in ctx and "akshare" in ctx  # 权重规则块
    assert ctx.count("《a.xlsx》") == 1  # 只有同 tag 的数据集注入

    assert render_user_data_context("MA") == ""  # 空 tag(批量回测)不注入
    assert render_user_data_context("", client_tag="1.2.3.4") == ""
    assert render_user_data_context("CU", client_tag="1.2.3.4") == ""  # 无该品种数据


def test_ingest_file_success_and_error_paths(tmp_db, tmp_path, monkeypatch):
    md = tmp_path / "ma.md"
    md.write_text("| 日期 | 库存 |\n|---|---|\n| 2026-09-01 | 68.5 |", encoding="utf-8")
    did = tmp_db.insert_user_dataset("ma.md", str(md), "MA", client_tag="1.2.3.4")
    reply = json.dumps({"variety": "MA", "data_type": "库存", "date_column": "日期",
                        "columns": [{"name": "库存", "meaning": "社会库存", "unit": "万吨"}],
                        "frequency": "周度", "notes": ""}, ensure_ascii=False)
    ingest_file(did, str(md), "ma.md", "MA", _FakeLLMClient(reply))
    ds = tmp_db.get_user_dataset(did)
    assert ds["status"] == "done" and ds["row_count"] == 1

    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"x")
    did2 = tmp_db.insert_user_dataset("bad.docx", str(bad), "", client_tag="1.2.3.4")
    ingest_file(did2, str(bad), "bad.docx", "", _FakeLLMClient("{}"))
    ds2 = tmp_db.get_user_dataset(did2)
    assert ds2["status"] == "error" and "不支持的文件类型" in ds2["error"]


# ── 上传/列表/删除路由(client_tag 打标 + 隔离) ──────────────────────────

@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    """路由级:真库 + web_app.get_db 打桩 + 后台解析线程短路(no-op)。"""
    db = AgentSenseDB(tmp_path / "t.db")
    monkeypatch.setattr(web_app, "get_db", lambda: db)
    monkeypatch.setattr(web_app, "_process_user_dataset",
                        lambda *a, **k: None)  # 不真跑 LLM 线程
    return db


def _client():
    return web_app.app.test_client()


def test_upload_tags_client_and_list_scopes(app_db, monkeypatch):
    monkeypatch.setattr(web_app, "_client_tag", lambda: "9.9.9.9")
    resp = _client().post("/api/userdata/upload", data={
        "file": (io.BytesIO(b"a,b\n1,2\n"), "t.csv"), "variety": "MA",
    })
    assert resp.status_code == 200
    ds = app_db.list_user_datasets()[0]
    assert ds["client_tag"] == "9.9.9.9"

    # 他人(另一台电脑)的列表看不到这条
    monkeypatch.setattr(web_app, "_client_tag", lambda: "8.8.8.8")
    assert _client().get("/api/userdata").get_json()["datasets"] == []
    monkeypatch.setattr(web_app, "_client_tag", lambda: "9.9.9.9")
    assert len(_client().get("/api/userdata").get_json()["datasets"]) == 1


def test_detail_and_delete_reject_other_client(app_db, monkeypatch, tmp_path):
    did = app_db.insert_user_dataset("a.csv", str(tmp_path / "a.csv"), "MA",
                                     client_tag="1.1.1.1")
    monkeypatch.setattr(web_app, "_client_tag", lambda: "2.2.2.2")
    assert _client().get(f"/api/userdata/{did}").status_code == 404  # 不可见
    assert _client().delete(f"/api/userdata/{did}").status_code == 404  # 不可删

    monkeypatch.setattr(web_app, "_client_tag", lambda: "1.1.1.1")
    assert _client().get(f"/api/userdata/{did}").status_code == 200
    assert _client().delete(f"/api/userdata/{did}").status_code == 200
    assert app_db.get_user_dataset(did) is None


def test_upload_bad_ext_400(app_db, monkeypatch):
    monkeypatch.setattr(web_app, "_client_tag", lambda: "9.9.9.9")
    resp = _client().post("/api/userdata/upload",
                          data={"file": (io.BytesIO(b"x"), "t.docx")})
    assert resp.status_code == 400


def test_upload_chinese_filename_keeps_ext(app_db, monkeypatch, tmp_path):
    """纯中文文件名被 secure_filename 清空时,落盘文件须保住扩展名(解析器按它分流)。"""
    monkeypatch.setattr(web_app, "_client_tag", lambda: "9.9.9.9")
    resp = _client().post("/api/userdata/upload", data={
        "file": (io.BytesIO(b"a,b\n1,2\n"), "甲醇库存.md"),
    })
    assert resp.status_code == 200
    fp = app_db.get_user_dataset(resp.get_json()["id"])["file_path"]
    assert fp.endswith(".md")


# ── 分析师节点确定性注入 ──────────────────────────────────────────────────

import tradingagents.agents.analysts.commodity_analysts as ca  # noqa: E402
import tradingagents.agents.analysts.sentiment_analyst as sa  # noqa: E402

_UD_BLOCK = "# 用户自传数据(最高优先级)\n### 数据集:《a.xlsx》"


class _FakeLLM:
    """占位 LLM:节点只透传给 _run_tool_loop(已被替换),不真正调用。"""


def _patch_loop(monkeypatch, module, captured):
    def _fake(llm, tools, initial_messages, **kwargs):
        captured.append(initial_messages[0].content)
        return "FAKE REPORT"

    monkeypatch.setattr(module, "_run_tool_loop", _fake)


def test_macro_node_injects_user_data(monkeypatch):
    import tradingagents.agents.analysts.commodity_analysts as cam
    captured = []
    monkeypatch.setattr(cam, "user_data_context", lambda sym, tag: _UD_BLOCK)
    _patch_loop(monkeypatch, cam, captured)
    cam.create_commodity_macro_analyst(_FakeLLM())({
        "trade_date": "2026-09-07", "company_of_interest": "MA",
        "messages": [], "client_tag": "1.2.3.4",
    })
    assert "用户自传数据" in captured[0]


def test_macro_node_no_client_tag_no_injection(monkeypatch):
    captured = []
    seen_tag = []
    monkeypatch.setattr(ca, "user_data_context",
                        lambda sym, tag: seen_tag.append(tag) or (_UD_BLOCK if tag else ""))
    _patch_loop(monkeypatch, ca, captured)
    ca.create_commodity_macro_analyst(_FakeLLM())({
        "trade_date": "2026-09-07", "company_of_interest": "MA", "messages": [],
    })  # 无 client_tag(批量回测形态)
    assert "用户自传数据" not in captured[0]
    assert seen_tag == [""]  # 节点以空串查询 → 查不到数据,不注入


def test_sentiment_node_injects_user_data(monkeypatch):
    captured = []
    monkeypatch.setattr(sa, "user_data_context", lambda sym, tag: _UD_BLOCK)
    _patch_loop(monkeypatch, sa, captured)
    sa.create_commodity_sentiment_analyst(_FakeLLM())({
        "trade_date": "2026-09-07", "company_of_interest": "MA",
        "messages": [], "client_tag": "1.2.3.4",
    })
    assert "用户自传数据" in captured[0]
