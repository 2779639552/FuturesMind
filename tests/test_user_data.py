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
from tradingagents.dataflows import user_data as user_data_module
from tradingagents.dataflows.user_data import (
    _is_data_row,
    _norm_date,
    _parse_markdown_tables,
    _render_dataset_block,
    _self_check_dataset,
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


def test_render_tail_skips_header_and_foreign_block_rows():
    """尾部取样必须跳过表头/单位行与**列结构不同**的别块行(否则注入表格大片空白)。

    复刻真实缺陷(Data_Platts.xlsx):首行是表头(日期列取值 'AssessDate'),
    第二行是单位行;尾部落着另一块列结构完全不同的数据(键集合交集为 0)。
    修复前 rows[-12:] 取到 4 真行 + 8 脏行;修复后应为 12 真行、零全空行。
    """
    cols = ["Code", "A", "B"]
    spec = {"date_column": "Code", "columns": [{"name": "A", "meaning": "甲"},
                                               {"name": "B", "meaning": "乙"}]}
    rows = [{"Code": "AssessDate", "A": "第一列含义", "B": "第二列含义"},  # 表头行
            {"Code": "", "A": "$/bbl", "B": "$/bbl"}]  # 单位行
    for i in range(1, 21):  # 20 个真实行(> MAX_RENDER_ROWS,触发尾部取样)
        rows.append({"Code": f"2026-09-{i:02d}", "A": i, "B": i * 2})
    rows += [{"Unnamed: 0": "2026-09-03", "PPXDK00": 9},  # 别块:日期列名恰是候选名
             {"Unnamed: 0": "2026-09-04", "PPXDK00": 8}]  # (真实 Platts 第二块的形态)
    ds = {"filename": "p.xlsx", "data_type": "价格", "spec": json.dumps(spec),
          "data": json.dumps(rows)}
    block = _render_dataset_block(ds)
    body = [ln for ln in block.split("\n") if ln.startswith("| 20")]
    assert len(body) == 12  # MAX_RENDER_ROWS:取最近 12 个真实行
    assert body[0].startswith("| 2026-09-09") and body[-1].startswith("| 2026-09-20")
    # 关键断言:每行在 A/B 两列上都有值(修复前脏行会渲染成全空单元格)
    assert all(ln.rstrip("|").split("|")[2].strip() for ln in body)
    assert block.count("|  |  |  |") == 0  # 零空白数据行
    assert "有效数据 20 行自 2026-09-01 至 2026-09-20" in block  # 页脚用真实行首尾


def test_is_data_row_distinguishes_header_unit_and_foreign_rows():
    cols = ["Code", "A"]
    assert _is_data_row({"Code": "2026-09-01", "A": 1.5}, "Code", cols) is True
    assert _is_data_row({"Code": "AssessDate", "A": "说明"}, "Code", cols) is False  # 表头
    assert _is_data_row({"Code": "", "A": "$/bbl"}, "Code", cols) is False  # 单位行
    # 别块行:日期**可解析**且在候选日期列里(Unnamed: 0),但在本数据集 cols 上全空
    # ⇒ 必须判为非本块(少了列结构这条闸就会误收,正是真实缺陷的形态)
    assert _is_data_row({"Unnamed: 0": "2026-09-03", "PPXDK00": 9}, "Code", cols) is False
    # 无 cols 时退化:认日期在候选列里即可(表头漂移场景)
    assert _is_data_row({"Unnamed: 0": "2026-09-03", "V": 9}, "Code", None) is True


# ── 上传后自检(_self_check_dataset) ─────────────────────────────────────
# 为什么需要这道护栏:入库 status=done **不等于**注入有效。真实缺陷
# (Data_Platts.xlsx)里尾部取样踩进另一块数据的脏区,注入表格 8/12 行全空、
# 页脚只剩 "AssessDate 至",而状态一直是 done、无任何报错 = 静默失效。

def _chk(rows, spec, filename="p.xlsx", data_type="价格"):
    return _self_check_dataset({"filename": filename, "data_type": data_type,
                                "spec": json.dumps(spec), "data": json.dumps(rows)})


def test_self_check_passes_clean_dataset():
    """干净数据集不得误报(否则护栏会被当噪声忽略掉)。"""
    spec = {"date_column": "日期", "columns": [{"name": "库存", "meaning": "社会库存"}]}
    rows = [{"日期": f"2026-09-{i:02d}", "库存": 60.0 + i} for i in range(1, 21)]
    assert _chk(rows, spec) == ""


def test_self_check_flags_skipped_foreign_rows():
    """文件里混入别的内容(表头/单位/其他数据块)⇒ 报「跳过 N 行」但确认注入正常。

    这是检查项①的针对性用例:那 8 行脏内容的告警,正是当初那个静默缺陷
    最该被用户看见的信号(此前全是无声无息)。
    """
    spec = {"date_column": "Code",
            "columns": [{"name": "A", "meaning": "甲"}, {"name": "B", "meaning": "乙"}]}
    rows = [{"Code": "AssessDate", "A": "第一列含义", "B": "第二列含义"},  # 表头行
            {"Code": "", "A": "$/bbl", "B": "$/bbl"}]  # 单位行
    for i in range(1, 21):
        rows.append({"Code": f"2026-09-{i:02d}", "A": i, "B": i * 2})
    rows += [{"Unnamed: 0": "2026-09-03", "PPXDK00": 9},   # 别块(真实 Platts 形态)
             {"Unnamed: 0": "2026-09-04", "PPXDK00": 8}]
    w = _chk(rows, spec)
    assert "4 行不属于本数据集" in w, w          # 2 表头/单位 + 2 别块
    assert "注入使用 20 行真实数据" in w, w      # 同时确认注入本身没受影响
    assert "整行空白" not in w, w                # 取样器正常 ⇒ 不报取样空白


def test_self_check_flags_blank_sampled_rows(monkeypatch):
    """取样器退化(按位置取尾 N 行)⇒ 必须报「整行空白」。

    这是检查项②的针对性用例。注意:取样器正常时该项逻辑上不可达(脏行必被跳过),
    所以这里**主动注入变异**(把取样器换回 rows[-n:]),证明它是一枚有效的探测器 ——
    若哪天有人改回按位置取样,自检会在上传那一刻就喊出来。
    """
    spec = {"date_column": "Code",
            "columns": [{"name": "A", "meaning": "甲"}, {"name": "B", "meaning": "乙"}]}
    rows = [{"Code": f"2026-09-{i:02d}", "A": i, "B": i * 2} for i in range(1, 13)]
    rows += [{"Unnamed: 0": "2026-09-13", "PPXDK00": 7} for _ in range(4)]  # 尾部别块行
    monkeypatch.setattr(user_data_module, "_tail_data_rows",
                        lambda r, d, c, n: r[-n:])  # 变异:退回按位置取样
    w = _chk(rows, spec)
    assert "整行空白" in w, w
    assert "4/12" in w, w  # 取样 12 行里有 4 行空白


def test_self_check_flags_unparseable_footer_date():
    """日期列名认错(真日期藏在候选列里)⇒ 必须报「页脚日期无法解析」。

    场景:LLM 把日期列判成了 'AssessDate'(表头文字),真实日期在候选列 'Unnamed: 0'
    里 —— 行能被认出来(靠候选列),但页脚按 date_col 取值是空的。
    这是该检查**独立可达**的唯一路径(若真没数据行,会先被"无可注入行"拦下)。
    """
    spec = {"date_column": "AssessDate",
            "columns": [{"name": "A", "meaning": "甲"}, {"name": "B", "meaning": "乙"}]}
    rows = [{"AssessDate": "", "A": "含义", "B": "含义"},
            {"AssessDate": "", "A": "$/bbl", "B": "$/bbl"}]
    for i in range(1, 21):
        rows.append({"AssessDate": f"第{i}周", "Unnamed: 0": f"2026-09-{i:02d}",
                     "A": i, "B": i * 2})
    w = _chk(rows, spec)
    assert "页脚日期无法解析" in w, w
    assert "AssessDate" in w, w            # 点名了是哪个列的问题
    assert "没有识别出可注入的真实数据行" not in w, w  # 行本身认得出来 ⇒ 不该报这条


def test_self_check_flags_no_injectable_rows():
    """一行都认不出来(日期列全不可解析)⇒ 报「没有识别出可注入的真实数据行」。"""
    spec = {"date_column": "日期", "columns": [{"name": "库存", "meaning": "库存"}]}
    rows = [{"日期": "第1周", "库存": 60.0 + i} for i in range(1, 21)]
    w = _chk(rows, spec)
    assert "没有识别出可注入的真实数据行" in w, w
    assert "20 行" in w, w


def test_self_check_flags_empty_and_broken_payload():
    """空数据行 / 非法 JSON ⇒ 行为明确;异常绝不外抛(不炸上传链路)。"""
    assert "解析结果为空" in _chk([], {})
    assert "解析结果为空" in _chk([], {}, filename="e.xlsx")
    # 空 ds(data 键缺失 → json 解析成 [])也归入"解析结果为空",这是正确且安全的行为
    assert "解析结果为空" in _self_check_dataset({})
    # JSON 坏 ⇒ 静默返回 "",绝不抛异常
    assert _self_check_dataset({"filename": "b.xlsx", "data_type": "x",
                                "spec": "{不是JSON", "data": "[]"}) == ""
    assert _self_check_dataset({"filename": "b.xlsx", "data_type": "x",
                                "spec": "{}", "data": "{{坏"}) == ""


def test_self_check_catches_real_platts_defect_shape(monkeypatch):
    """复刻真实缺陷形态:首行表头 + 单位行 + 尾部另一块列结构不同的数据。

    修复后取样干净(注入 12 真行、零空白),但自检**仍要报出混入的 4 行脏内容** ——
    这正是当初那个静默缺陷最该被用户看见的信号。同时验证:若取样器被改回按位置取,
    自检会升级成「整行空白」告警。
    """
    spec = {"date_column": "Code",
            "columns": [{"name": "A", "meaning": "甲"}, {"name": "B", "meaning": "乙"}]}
    rows = [{"Code": "AssessDate", "A": "第一列含义", "B": "第二列含义"},
            {"Code": "", "A": "$/bbl", "B": "$/bbl"}]
    for i in range(1, 21):
        rows.append({"Code": f"2026-09-{i:02d}", "A": i, "B": i * 2})
    rows += [{"Unnamed: 0": "2026-09-03", "PPXDK00": 9},
             {"Unnamed: 0": "2026-09-04", "PPXDK00": 8}]

    w = _chk(rows, spec)
    assert "4 行不属于本数据集" in w, w      # 脏内容被如实报出
    assert "整行空白" not in w, w            # 取样器正常 ⇒ 注入本身没有空白

    # 取样器一旦退化为按位置取,同样的载荷必须被升级为空白告警(证明②承重)
    monkeypatch.setattr(user_data_module, "_tail_data_rows", lambda r, d, c, n: r[-n:])
    w2 = _chk(rows, spec)
    assert "整行空白" in w2, w2
    assert "页脚日期无法解析" in w2, w2      # 末行日期列取不到值 ⇒ 同时报③


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
    assert ds["ingest_warning"] == ""  # 干净数据不告警

    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"x")
    did2 = tmp_db.insert_user_dataset("bad.docx", str(bad), "", client_tag="1.2.3.4")
    ingest_file(did2, str(bad), "bad.docx", "", _FakeLLMClient("{}"))
    ds2 = tmp_db.get_user_dataset(did2)
    assert ds2["status"] == "error" and "不支持的文件类型" in ds2["error"]


def test_ingest_file_writes_warning_on_dirty_parse(tmp_db, tmp_path):
    """解析成功但自检可疑(日期列不可解析)⇒ status 仍 done,另落 ingest_warning。

    要点:告警**不能**把 done 改成 error —— 数据其实入库了,只是注入质量可疑;
    写成 error 会让用户以为白传了,而真正要做的是提示他去核对日期列。
    """
    md = tmp_path / "w.md"
    md.write_text("| 周次 | 库存 |\n|---|---|\n| 第1周 | 68.5 |\n| 第2周 | 69.0 |",
                  encoding="utf-8")
    did = tmp_db.insert_user_dataset("w.md", str(md), "MA", client_tag="1.2.3.4")
    reply = json.dumps({"variety": "MA", "data_type": "库存", "date_column": "周次",
                        "columns": [{"name": "库存", "meaning": "社会库存"}],
                        "frequency": "周度", "notes": ""}, ensure_ascii=False)
    ingest_file(did, str(md), "w.md", "MA", _FakeLLMClient(reply))
    ds = tmp_db.get_user_dataset(did)
    assert ds["status"] == "done"          # 仍是成功(数据已入库)
    assert ds["ingest_warning"]            # 但带着告警
    assert "没有识别出可注入的真实数据行" in ds["ingest_warning"]
    assert tmp_db.list_user_datasets()[0]["ingest_warning"] == ds["ingest_warning"]  # 列表可见


def test_selfcheck_crash_does_not_break_ingest(tmp_db, tmp_path, monkeypatch):
    """体检自身崩了也不能把已成功的解析结果带崩成 error(自检是附加能力)。"""
    md = tmp_path / "ok.md"
    md.write_text("| 日期 | 库存 |\n|---|---|\n| 2026-09-01 | 68.5 |", encoding="utf-8")
    did = tmp_db.insert_user_dataset("ok.md", str(md), "MA", client_tag="1.2.3.4")
    monkeypatch.setattr(user_data_module, "_self_check_dataset",
                        lambda ds: (_ for _ in ()).throw(RuntimeError("boom")))
    reply = json.dumps({"variety": "MA", "data_type": "库存", "date_column": "日期",
                        "columns": [{"name": "库存", "meaning": "库存"}],
                        "frequency": "周度", "notes": ""}, ensure_ascii=False)
    ingest_file(did, str(md), "ok.md", "MA", _FakeLLMClient(reply))
    ds = tmp_db.get_user_dataset(did)
    assert ds["status"] == "done" and ds["row_count"] == 1  # 不受影响


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


def test_user_datasets_schema_has_ingest_warning(app_db):
    """新库直接建表即带 ingest_warning;旧库由 _migrate 补列(两条路径都要通)。"""
    with app_db._conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(user_datasets)").fetchall()}
    assert "ingest_warning" in cols
    app_db._migrate()  # 幂等:重复迁移不报错
    assert app_db.list_user_datasets() == []  # 列表查询含新列且空库可用


def test_migrate_adds_ingest_warning_to_legacy_table(tmp_path):
    """旧库(建表时无 ingest_warning)经 _migrate 必须补上该列,且存量行取默认空串。"""
    import sqlite3

    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE user_datasets (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " filename TEXT DEFAULT '', file_path TEXT DEFAULT '', variety TEXT DEFAULT '',"
        " client_tag TEXT DEFAULT '', data_type TEXT DEFAULT '', spec TEXT DEFAULT '',"
        " data TEXT DEFAULT '', row_count INTEGER DEFAULT 0,"
        " status TEXT DEFAULT 'processing', error TEXT DEFAULT '',"
        " created_at TEXT DEFAULT (datetime('now')))"
    )
    con.execute("INSERT INTO user_datasets (filename, variety, status) VALUES ('old.xlsx','MA','done')")
    con.commit()
    con.close()

    db = AgentSenseDB(legacy)  # 构造即触发 _migrate
    with db._conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(user_datasets)").fetchall()}
    assert "ingest_warning" in cols
    assert db.get_user_dataset(1)["ingest_warning"] == ""  # 存量行默认空
    assert db.list_user_datasets()[0]["ingest_warning"] == ""


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
