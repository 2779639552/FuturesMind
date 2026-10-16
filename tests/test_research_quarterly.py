"""研报季报口径(_quarter_* / _period_* / 季报总结链路)回归护栏。

【要防的回归】2026-09-29 新增季报口径,把研报类型从二元(日报/周报)扩成三元。
风险点集中在四处,本文件逐个锁死:
  1) 标题启发式的**优先级**:日报 → 周报 → 季报,顺序错了会把
     「碳酸锂日报…周度去库」判成周报、「甲醇周报:季度供需回顾」判成季报;
  2) **季度归属**:标题优先(报告期语义)——「2026Q2铁矿季度运营报告」9 月发布仍归 Q2;
     标题无季度标识时才回退发布日期所属季度;
  3) **口径隔离**:日报口径排除周报/季报但**保留未知类型**(库里大量历史行 report_type=''),
     排除它们会让每日总结凭空少一大片内容;周报/季报口径各自只收本类型;
  4) **独立落盘**:季报总结落 research_quarterly,不与 daily/weekly 混目录。

不真调 LLM(client 打桩);文件路径隔离到 tmp_path。
"""

import pytest

import database
import web_app


# ---------------------------------------------------------------------------
# 桩件(与 test_research_daily_summary 同构,保持两文件风格一致)
# ---------------------------------------------------------------------------
class _FakeDB:
    """最小 DB 桩:list_research_reports 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    def list_research_reports(self, variety=None, limit=50, ingest_source=None,
                              report_type=None):
        rows = [dict(r) for r in self._rows]
        if report_type:
            rows = [r for r in rows if (r.get("report_type") or "") == report_type]
        return rows


def _row(rid=1, date="2026-09-02", codes="SC", status="done", direction="看多",
         confidence=0.7, conclusion=None, publish_date=None, report_type="",
         source="华泰期货", title=None):
    return {
        "id": rid,
        "title": title or f"研报{rid}",
        "source": source,
        "variety": codes.split(",")[0],
        "varieties": codes,
        "uploaded_at": f"{date} 08:00:00",
        "publish_date": publish_date or "",
        "report_type": report_type,
        "status": status,
        "direction": direction,
        "confidence": confidence,
        "conclusion_md": conclusion,
    }


_TRED = (
    "## 供需格局\n供应收紧。\n\n"
    "## 交易要素与风险\n方向:看多;形态与区间:单边看多;头寸:轻仓;头寸范围:—;风险:增产\n\n"
    "## 建议权重\n多头 20%~30%。"
)


@pytest.fixture
def all_dirs(tmp_path, monkeypatch):
    """把三类总结目录都隔离到临时目录,返回 (daily, weekly, quarterly)。"""
    d = tmp_path / "research_daily"
    w = tmp_path / "research_weekly"
    q = tmp_path / "research_quarterly"
    m = tmp_path / "research_monthly"
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", d)
    monkeypatch.setattr(web_app, "RESEARCH_WEEKLY_DIR", w)
    monkeypatch.setattr(web_app, "RESEARCH_QUARTERLY_DIR", q)
    monkeypatch.setattr(web_app, "RESEARCH_MONTHLY_DIR", m)
    return d, w, q


# ---------------------------------------------------------------------------
# 1. 标题启发式:优先级与零误报
# ---------------------------------------------------------------------------
def test_guess_report_type_priority_daily_weekly_quarterly():
    """优先级:日报 > 周报 > 季报。顺序错会把带「季度」字样的周报误判成季报。"""
    g = database.guess_report_type
    # 日报优先:标题含「周度」的日报不能判成周报
    assert g("碳酸锂日报20260904：周度去库放缓") == "日报"
    # 周报优先:标题含「季度」的周报不能判成季报(实测「甲醇周报:季度供需回顾」)
    assert g("甲醇周报：季度供需回顾") == "周报"
    # 季报三兄弟:季报 / 季刊 / 季度
    assert g("2026Q2铁矿季度运营报告：增量符合预期") == "季报"
    assert g("海外铅锌矿企业季度运营分析：供应刚性恐限制TC反弹空间") == "季报"
    assert g("黑色金属季刊") == "季报"


def test_guess_report_type_quarter_digits_and_chinese():
    """季度标识的四种写法都要命中;Q 后跟数字(Q10)不能命中。"""
    g = database.guess_report_type
    assert g("某品种2026Q1展望") == "季报"
    assert g("某品种2026q3展望") == "季报"
    assert g("某品种第一季度报告") == "季报"
    assert g("某品种第4季度报告") == "季报"
    assert g("某产品Q10报告") == ""      # Q10 不是季度
    assert g("尿素：区间运行") == ""      # 无任何类型信号
    assert g("") == ""


def test_guess_report_type_no_false_positive_on_real_titles():
    """用真实标题样本做一次零误报体检(核心 6 条):不能把普通日报/周报变季报。"""
    g = database.guess_report_type
    ordinary = [
        "纯碱周报", "玻璃周度观察", "甲醇周刊",
        "燃料油：夜盘窄幅震荡，价格维持高点 低硫燃料油：调整走势为主",
        "繁微长图系列：市场预期·日度机构预期汇总(2026-09-28)",
        "【中信期货能源化工（甲醇、尿素）】库存新低供需紧张，甲醇强势延续——周报20260927",
    ]
    assert [g(t) for t in ordinary] == ["周报", "周报", "周报", "", "", "周报"]


# ---------------------------------------------------------------------------
# 2. 季度归属:标题优先,回退发布日期
# ---------------------------------------------------------------------------
def test_quarter_of_title():
    assert web_app._quarter_of_title("2026Q2铁矿季度运营报告") == 2
    assert web_app._quarter_of_title("某品种2026q4展望") == 4
    assert web_app._quarter_of_title("某品种第二季度报告") == 2
    assert web_app._quarter_of_title("海外铅锌矿企业季度运营分析") == 0  # 无标识
    assert web_app._quarter_of_title("") == 0


def test_quarter_start_title_wins_over_publish_date():
    """核心口径(用户拍板):标题优先。

    「2026Q2铁矿季度运营报告」9 月发布 —— 报告期是 Q2,必须归 Q2 而不是 Q3。
    这是本功能最关键的一条断言:按发布日期归会让它落到 Q3,用户按季度翻不到。
    """
    assert web_app._quarter_start("2026-09-07", "2026Q2铁矿季度运营报告：增量符合预期") == "2026-04-01"
    # 标题无季度标识 → 回退发布日期所属季度(9 月 → Q3)
    assert web_app._quarter_start("2026-09-02", "海外铅锌矿企业季度运营分析") == "2026-07-01"
    # 标题无标识且给了明确季度也没关系(第三季度 → Q3)
    assert web_app._quarter_start("2026-09-02", "某品种第三季度报告") == "2026-07-01"
    # 非法日期 → 空(消费方跳过,不产生幽灵季度)
    assert web_app._quarter_start("bad", "某品种季度报告") == ""


def test_quarter_range_boundaries():
    """季度区间端点:Q1=01-01~03-31,Q2=04-01~06-30,Q4=10-01~12-31(含闰年 2 月)。"""
    assert web_app._quarter_range("2026-01-01") == ("2026-01-01", "2026-03-31")
    assert web_app._quarter_range("2026-04-01") == ("2026-04-01", "2026-06-30")
    assert web_app._quarter_range("2026-07-01") == ("2026-07-01", "2026-09-30")
    assert web_app._quarter_range("2026-10-01") == ("2026-10-01", "2026-12-31")
    assert web_app._quarter_range("2028-01-01") == ("2028-01-01", "2028-03-31")  # 闰年
    assert web_app._quarter_range("bad") == ("", "")


def test_in_quarter_uses_title_report_period():
    """_in_quarter:一份 Q2 季报(9 月发布)在 Q2 选中时命中,在 Q3 选中时不命中。

    这一条是「按季度翻得到」的直接保障:若只看发布日,用户在 Q2 里永远看不到它。
    """
    assert web_app._in_quarter("2026-04-01", "2026-09-07", "2026Q2铁矿季度运营报告")
    assert not web_app._in_quarter("2026-07-01", "2026-09-07", "2026Q2铁矿季度运营报告")
    # 标题无标识 → 按发布日:9 月发布的归 Q3
    assert web_app._in_quarter("2026-07-01", "2026-09-02", "海外铅锌矿企业季度运营分析")
    assert not web_app._in_quarter("2026-04-01", "2026-09-02", "海外铅锌矿企业季度运营分析")


# ---------------------------------------------------------------------------
# 3. 口径归一与周期键(三态分派)
# ---------------------------------------------------------------------------
def test_rtype_norm():
    """'周报'/'季报'/'月报' 原样;其余(含空、'日报'、脏值)一律视为日报口径。"""
    assert web_app._rtype_norm("周报") == "周报"
    assert web_app._rtype_norm("季报") == "季报"
    assert web_app._rtype_norm("月报") == "月报"
    assert web_app._rtype_norm("日报") == ""
    assert web_app._rtype_norm("") == ""
    assert web_app._rtype_norm(None) == ""
    assert web_app._rtype_norm("  周报 ") == "周报"


def test_period_key_dispatch():
    """周期键分派:日报=原日期 / 周报=周起点 / 季报=季度起点(标题优先)。"""
    assert web_app._period_key("2026-09-02", "某日报", "") == "2026-09-02"
    assert web_app._period_key("2026-09-02", "某周报", "周报") == web_app._week_start("2026-09-02")
    assert web_app._period_key("2026-09-07", "2026Q2铁矿季度运营报告", "季报") == "2026-04-01"
    assert web_app._period_key("2026-10-07", "某月报", "月报") == "2026-10-01"  # 月报=发布月起点


def test_period_range_dispatch():
    """周期区间分派:日报=当日(起止同一天);周报=周区间;季报=季度区间。"""
    assert web_app._period_range("2026-09-02", "") == ("2026-09-02", "2026-09-02")
    assert web_app._period_range("2026-07-01", "季报") == ("2026-07-01", "2026-09-30")
    wk = web_app._period_range("2026-08-30", "周报")
    assert wk == ("2026-08-30", "2026-09-05")  # 周起点周日 ~ 周六
    assert web_app._period_range("2026-10-01", "月报") == ("2026-10-01", "2026-10-31")
    assert web_app._period_range("2026-12-01", "月报") == ("2026-12-01", "2026-12-31")  # 跨年月末


# ---------------------------------------------------------------------------
# 4. 落盘目录隔离
# ---------------------------------------------------------------------------
def test_summary_dir_three_way(all_dirs):
    d, w, q = all_dirs
    assert web_app._summary_dir("") == d
    assert web_app._summary_dir("日报") == d
    assert web_app._summary_dir("周报") == w
    assert web_app._summary_dir("季报") == q
    assert web_app._summary_dir("月报") == web_app.RESEARCH_MONTHLY_DIR  # 2026-10-08 月报独立落盘
    assert web_app._research_daily_path("2026-07-01", "季报") == q / "2026-07-01.md"
    assert web_app._research_daily_path("2026-10-01", "月报") == web_app.RESEARCH_MONTHLY_DIR / "2026-10-01.md"


# ---------------------------------------------------------------------------
# 5. 条目收集:口径隔离(日报留未知类型)
# ---------------------------------------------------------------------------
def test_collect_items_daily_keeps_unknown_but_drops_quarterly():
    """日报口径:排除周报**与季报**,但保留未知类型(report_type='')。

    回归点:季报引入时若写成「只收日报」,库里 500+ 条空类型历史行会集体消失,
    每日总结的条目数凭空少一大片(这是本次改造最容易踩的坑)。

    【注意】各行刻意用**不同 source** —— 同源同品种同日会被既有的「同源合并」
    (test_research_daily_summary.py::test_collect_items_merges_same_source...)合并
    成一条,那是另一层语义,会掩盖本用例要验的"类型过滤"。
    """
    rows = [
        _row(rid=1, conclusion=_TRED, report_type="季报", source="季报机构"),  # 季报 → 日报口径排除
        _row(rid=2, conclusion=_TRED, report_type="周报", source="周报机构"),  # 周报 → 排除
        _row(rid=3, conclusion=_TRED, report_type="日报", source="日报机构"),  # 日报 → 收
        _row(rid=4, conclusion=_TRED, report_type="", source="未知机构"),      # 未知 → 收(关键)
    ]
    titles = [i["title"] for i in web_app._collect_daily_report_items(rows, "2026-09-02")]
    assert titles == ["研报3", "研报4"]


def test_collect_items_quarterly_only_quarterly():
    """季报口径只收季报行:日报/周报/未知都不混入。"""
    rows = [
        _row(rid=1, conclusion=_TRED, report_type="季报", date="2026-07-01"),
        _row(rid=2, conclusion=_TRED, report_type="日报", date="2026-07-01", source="甲"),
        _row(rid=3, conclusion=_TRED, report_type="周报", date="2026-07-01", source="乙"),
        _row(rid=4, conclusion=_TRED, report_type="", date="2026-07-01", source="丙"),
    ]
    items = web_app._collect_daily_report_items(rows, "2026-07-01", "季报")
    assert [i["title"] for i in items] == ["研报1"]


def test_collect_items_quarterly_matches_by_period_window():
    """季报口径按季度**区间**匹配:一份 8 月发布的季报,在 Q3 口径下被收到。

    日报口径的精确单日匹配在这里必须让位给区间匹配(与周报同构)。
    """
    rows = [_row(rid=1, conclusion=_TRED, report_type="季报", date="2026-08-20",
                 title="2026Q3某品种季度报告")]
    assert web_app._collect_daily_report_items(rows, "2026-07-01", "季报")
    assert not web_app._collect_daily_report_items(rows, "2026-07-01")  # 日报口径不收


# ---------------------------------------------------------------------------
# 6. 日期并集:季报归季度起点
# ---------------------------------------------------------------------------
def test_research_daily_dates_quarterly(all_dirs):
    """季报日期并集 = 季报行的季度起点集合(标题优先);与日报/周报口径互斥。"""
    db = _FakeDB([
        _row(rid=1, date="2026-09-07", report_type="季报", title="2026Q2铁矿季度运营报告"),
        _row(rid=2, date="2026-09-02", report_type="季报", title="海外铅锌矿企业季度运营分析"),
        _row(rid=3, date="2026-09-02", report_type="日报"),
    ])
    assert web_app._research_daily_dates(db, "季报") == ["2026-07-01", "2026-04-01"]
    assert web_app._research_daily_dates(db) == ["2026-09-02"]      # 日报口径不含季报
    assert web_app._research_daily_dates(db, "周报") == []


def test_research_daily_dates_quarterly_picks_up_generated_files(all_dirs):
    """已生成的季报总结文件(季度起点命名)并入并集;非季初命名的旧文件归到季起点。"""
    d, w, q = all_dirs
    q.mkdir(parents=True, exist_ok=True)
    (q / "2026-10-01.md").write_text("Q4 季报总结", encoding="utf-8")
    (q / "2026-05-15.md").write_text("旧版单日命名", encoding="utf-8")  # → 归到 2026-04-01
    db = _FakeDB([])
    assert web_app._research_daily_dates(db, "季报") == ["2026-10-01", "2026-04-01"]


# ---------------------------------------------------------------------------
# 7. 端到端:季报总结生成落到 research_quarterly
# ---------------------------------------------------------------------------
def test_generate_quarterly_writes_quarterly_dir(all_dirs, monkeypatch):
    """季报口径生成:落盘 research_quarterly,标题进 prompt 用「季报」;日报口径不串味。"""
    d, w, q = all_dirs
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([
        _row(rid=1, date="2026-09-07", conclusion=_TRED, report_type="季报",
             title="2026Q2铁矿季度运营报告"),
    ]))

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    assert "季报" in prompt  # 季报口径文案进 prompt
                    class _R:
                        content = "## 一、季报观点总表\n(季度复盘)"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_daily_summary("2026-04-01", rtype="季报")
    assert out["ok"] and out["reports"] == 1
    saved = web_app._research_daily_path("2026-04-01", "季报")
    assert saved.is_file() and "季度复盘" in saved.read_text(encoding="utf-8")
    assert "research_quarterly" in str(saved)
    assert not (w / "2026-04-01.md").exists()  # 不落周报目录
    assert not (d / "2026-04-01.md").exists()  # 不落日报目录


# ---------------------------------------------------------------------------
# 8. 前端模板:下拉含季报 + 季度二级选择器
# ---------------------------------------------------------------------------
def test_template_has_quarterly_options_and_pickers():
    """模板必须同时具备:5 个类型下拉的「季报」选项 + 3 个季度二级选择器 + 口径助手函数。

    【为什么要这个】前端改动分散在 5 个下拉与 3 张卡上,漏一处就是"选了季报没反应"
    或"下拉里根本没有季报";这条护栏把清单钉死。改前端时若删了某个 id 会立刻红。
    """
    from pathlib import Path

    html = (Path(web_app.__file__).parent / "web_template.html").read_text(encoding="utf-8")
    # 5 个类型下拉都要有季报/月报选项(res-ask / res-daily / res-vsum / res-views / res-filter)
    assert html.count('<option value="季报">季报</option>') == 5
    assert html.count('<option value="月报">月报</option>') == 5  # 2026-10-08 加月报
    # 3 个季度二级选择器
    for qid in ("res-daily-quarter", "res-vsum-quarter", "res-views-quarter"):
        assert f'id="{qid}"' in html, f"缺少季度二级选择器 {qid}"
    # 口径助手与同步函数
    for fn in ("_quarterLabel", "_periodLabel", "_quarterPickerModel", "_syncQuarterPicker", "_monthLabel"):
        assert f"{fn}(" in html, f"模板缺少 {fn}"
    # 三张卡都真的调用了同步函数(漏调用 ⇒ 选季报时下拉不切换)
    assert html.count("_syncQuarterPicker(") >= 4  # 定义 1 + 调用 3
    # 月报走 _periodLabel 周期标签(isPeriodic 含月报)
    assert html.count("isPeriodic") >= 4


# ---------------------------------------------------------------------------
# 9. 【缺陷 A 回归】DB 回填类型必须同步到聚合 JSON
# ---------------------------------------------------------------------------
def test_update_research_report_fields_patches_only_given_fields(tmp_path, monkeypatch):
    """聚合字段级刷新:只改指定字段,不动结论/方向/报告顺序(防误伤既有内容)。"""
    import json as _json
    from tradingagents.dataflows import research_data as rd

    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    rd._research_cache.clear()
    rd.upsert_research_report("SC", {"id": 11, "title": "T", "report_type": "",
                                     "conclusion": "看多", "direction": "看多"})
    rd.upsert_research_report("SC", {"id": 12, "title": "T2", "report_type": "日报"})
    assert rd.update_research_report_fields("SC", 11, {"report_type": "季报"}) is True

    data = _json.loads((tmp_path / "SC_research.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["reports"]}
    assert by_id[11]["report_type"] == "季报"      # 目标字段已刷
    assert by_id[11]["conclusion"] == "看多"        # 其它字段未动
    assert by_id[11]["direction"] == "看多"
    assert [r["id"] for r in data["reports"]] == [12, 11]  # 顺序未变
    # 未命中 id → False,不写盘
    assert rd.update_research_report_fields("SC", 999, {"report_type": "日报"}) is False
    assert rd.update_research_report_fields("SC", 11, {}) is False


def test_set_report_type_everywhere_writes_db_and_aggregate(tmp_path, monkeypatch):
    """统一入口:DB 与聚合 JSON 必须一起写。

    【要防的回归】2026-09-29 实测缺陷:只写 DB 时,「逐品种观点总览」读聚合 JSON
    按季报筛会一条都查不到(聚合里 report_type 仍是 '')——总结里有、总览空白。
    """
    import json as _json
    from tradingagents.dataflows import research_data as rd

    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    rd._research_cache.clear()
    rd.upsert_research_report("ZN", {"id": 11, "title": "T", "report_type": ""})

    writes = []
    row = {"id": 11, "varieties": "ZN,PB", "variety": "ZN"}
    monkeypatch.setattr(web_app, "get_db", lambda: type("D", (), {
        "update_research_report": lambda self, rid, **kw: writes.append((rid, kw)),
        "get_research_report": lambda self, rid: row,
    })())
    rd.upsert_research_report("PB", {"id": 11, "title": "T", "report_type": ""})

    assert web_app._set_report_type_everywhere(11, "季报") == "季报"
    assert writes == [(11, {"report_type": "季报"})]              # DB 已写
    for c in ("ZN", "PB"):
        d = _json.loads((tmp_path / f"{c}_research.json").read_text(encoding="utf-8"))
        assert d["reports"][0]["report_type"] == "季报", f"{c} 聚合未同步"
    # 非法类型 → 不写,返回空(不能把未知标成日报/周报)
    assert web_app._set_report_type_everywhere(11, "其它") == ""
    assert web_app._set_report_type_everywhere(11, "") == ""
    assert len(writes) == 1


def test_repair_research_aggregates_refreshes_stale_type(tmp_path, monkeypatch):
    """对账自愈必须覆盖"类型陈旧"(只补缺失条目的旧实现做不到)。

    【场景】DB 里 report_type 被回填/自愈成季报,聚合 JSON 仍是 ''。旧实现只判
    "id 在不在聚合里" → 认为无需处理,陈旧类型永远留着,季报口径整卡空白。
    """
    import json as _json
    from tradingagents.dataflows import research_data as rd

    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    rd._research_cache.clear()
    rd.upsert_research_report("ZN", {"id": 11, "title": "海外铅锌矿企业季度运营分析",
                                     "report_type": "", "conclusion": "看多",
                                     "publish_date": "2026-09-02", "uploaded_at": "2026-09-02 08:00:00"})
    monkeypatch.setattr(web_app.Path, "home", staticmethod(lambda: tmp_path.parent))

    class _DB:
        def list_research_reports(self, limit=5000, **kw):
            return [{"id": 11, "title": "海外铅锌矿企业季度运营分析", "status": "summarized",
                     "report_type": "季报", "varieties": "ZN", "variety": "ZN",
                     "publish_date": "2026-09-02", "uploaded_at": "2026-09-02 08:00:00",
                     "source": "东证期货", "structured_data": "", "conclusion_md": "看多"}]

    monkeypatch.setattr(web_app, "get_db", lambda: _DB())
    # 把聚合目录指到 rd.RESEARCH_DIR(对账函数用 ~/.tradingagents/external_data,与 rd 同源)
    monkeypatch.setattr(web_app, "Path", web_app.Path)
    res = web_app._repair_research_aggregates(dry_run=False)
    # 若 home 打桩未覆盖到,则该用例退化为无操作 —— 直接断言核心事实:刷新后聚合为季报
    d = _json.loads((tmp_path / "ZN_research.json").read_text(encoding="utf-8"))
    assert res["retyped_entries"] >= 1
    assert d["reports"][0]["report_type"] == "季报"
    assert d["reports"][0]["conclusion"] == "看多"  # 只刷类型,结论未动

# ---------------------------------------------------------------------------
# 10. 【缺陷 B 回归 2026-09-29】低频类型必须全表取数,不能被固定 LIMIT 截掉
# ---------------------------------------------------------------------------
def test_research_rows_for_type_uses_full_table_for_low_frequency():
    """周报/季报走全表(limit=None);日报等仍带 LIMIT。

    回归点:研报逐日累积,日报每天几十上百条,而周报/季报是低频类型。
    固定 LIMIT 是按上传时间截最新 N 条 ⇒ 低频类型整体被挤出窗口:
    实测 LIMIT=500 只覆盖最近 8 天,季报 2/2 全丢、周报丢 194/316(61%),
    现象=前端"选季报什么都不显示",而接口不报错(静默缺陷)。
    """
    calls = []

    class _DB:
        def list_research_reports(self, *a, **kw):
            calls.append(kw.get("limit", "MISSING"))
            return []

    web_app._research_rows_for_type(_DB(), "季报")
    web_app._research_rows_for_type(_DB(), "周报")
    web_app._research_rows_for_type(_DB(), "月报")
    web_app._research_rows_for_type(_DB(), "日报")
    web_app._research_rows_for_type(_DB(), "")
    assert calls == [None, None, None, 500, 500], calls


def test_list_research_reports_limit_none_returns_all(tmp_path):
    """数据库层 limit=None 必须真的"不限条数"。

    不能靠 SQL `LIMIT NULL` —— SQLite 会抛 `datatype mismatch`,
    必须真的不拼 LIMIT 子句(2026-09-29 实测)。
    """
    db = database.AgentSenseDB(tmp_path / "t.db")
    for i in range(8):
        db.insert_research_report(
            "SC", title="r%d" % i, source="华泰期货", report_type="日报")
    assert len(db.list_research_reports(limit=3)) == 3
    assert len(db.list_research_reports(limit=None)) == 8      # 全量,不报错
    assert len(db.list_research_reports(limit=None, report_type="日报")) == 8


def test_low_frequency_type_survives_beyond_limit_window(all_dirs):
    """端到端:季报排在 600 条日报之后,日期列表/条目收集都必须仍能看见它。

    这是缺陷 B 的最小复现 —— 旧实现(固定 limit=500)下 dates 为空。
    """
    rows = [
        _row(rid=1000 + i, date="2026-09-28", codes="SC", report_type="日报",
             title="日报%d" % i)
        for i in range(600)
    ]
    rows.append(_row(rid=11, date="2026-09-02", codes="ZN,PB", report_type="季报",
                     title="海外铅锌矿季度运营分析", conclusion=_TRED))

    class _FullDB:
        """按 uploaded_at 倒序截断,忠实模拟 SQL LIMIT 语义。"""

        def list_research_reports(self, variety=None, limit=50,
                                  ingest_source=None, report_type=None):
            rs = sorted((dict(r) for r in rows),
                        key=lambda r: r["uploaded_at"], reverse=True)
            if report_type:
                rs = [r for r in rs if (r.get("report_type") or "") == report_type]
            return rs if limit is None else rs[:limit]

    db = _FullDB()
    # 日期列表能看见季报的季度键(旧实现这里为空 ⇒ 季度下拉空)
    assert "2026-07-01" in web_app._research_daily_dates(db, "季报")
    # 条目收集也能拿到(旧实现这里为空 ⇒ 点生成说"库里没有")
    got = web_app._collect_variety_report_items(
        web_app._research_rows_for_type(db, "季报"), "2026-07-01", "ZN", "季报")
    assert len(got) == 1, got

# ---------------------------------------------------------------------------
# 11. 【缺陷 C 回归 2026-09-29】标题里的季度/年份必须被识别(裸「N季度」+ 报告期年份)
# ---------------------------------------------------------------------------
def test_quarter_of_title_accepts_bare_n_quarter():
    """标题里的季度标识要认**三种写法**,尤其没有"第"字的裸式。

    回归点:实盘标题常写「农业2026年**四季度**策略报告」——没"第"字。
    旧正则 `第([一二三四1234])季度` 漏掉它 ⇒ 回退按发布日(9 月 ⇒ Q3)归属,
    把 Q4 季报塞进 Q3,并派生"Q4 有数据但点进去空白"的幽灵选项。
    """
    assert web_app._quarter_of_title("2026Q2铁矿季度运营报告") == 2
    assert web_app._quarter_of_title("2026年第四季度策略") == 4
    assert web_app._quarter_of_title("农业2026年四季度策略报告（棉花）") == 4   # 裸式,原漏
    assert web_app._quarter_of_title("四季度策略报告") == 4
    assert web_app._quarter_of_title("三季度展望") == 3
    assert web_app._quarter_of_title("一季度累库与二季度展望") == 1             # 取最靠前的标识
    assert web_app._quarter_of_title("海外铅锌矿企业季度运营分析") == 0         # 无标识
    assert web_app._quarter_of_title("") == 0


def test_year_of_title_extracts_report_year_safely():
    """报告期年份取标题,且**不误伤**合约代码里的 4 位数字。"""
    assert web_app._year_of_title("农业2026年四季度策略报告（棉花）") == 2026   # 「2026年」
    assert web_app._year_of_title("2026Q2铁矿季度运营报告") == 2026           # 「2026Q2」
    assert web_app._year_of_title("无年份标题") == 0
    assert web_app._year_of_title("") == 0
    # 防误伤:合约号 2601 后面既无「年」也无「Q」⇒ 不认作年份
    assert web_app._year_of_title("铁矿2601合约四季度展望") == 0
    assert web_app._year_of_title("2026Q2铁矿2601合约") == 2026               # 认 2026Q2,不认 2601


def test_quarter_key_prefers_title_year_for_cross_year_report():
    """报告期年份优先于发布日期年份:跨年发布的年度季报不能错标到发布年。"""
    t = "农业2026年四季度策略报告（棉花）"
    assert web_app._period_key("2026-09-25", t, "季报") == "2026-10-01"
    # 跨年发布(2027-01 发 2026Q4 报告)——旧实现会把年份取成 2027
    assert web_app._period_key("2027-01-05", t, "季报") == "2026-10-01"
    assert web_app._in_quarter("2026-10-01", "2027-01-05", t) is True
    # 无标题年份时仍回退发布年
    assert web_app._period_key("2026-09-25", "海外铅锌矿企业季度运营分析", "季报") == "2026-07-01"


def test_quarter_title_priority_end_to_end_for_bare_form(all_dirs):
    """端到端:裸式标题的季报必须归到**标题声明的**季度,而不是发布日季度。"""
    rows = [_row(rid=1379, date="2026-09-25", codes="CF", report_type="季报",
                 title="农业2026年四季度策略报告（棉花）", conclusion=_TRED)]

    class _DB:
        def list_research_reports(self, variety=None, limit=50,
                                  ingest_source=None, report_type=None):
            rs = [dict(r) for r in rows]
            if report_type:
                rs = [r for r in rs if (r.get("report_type") or "") == report_type]
            return rs if limit is None else rs[:limit]

    dates = web_app._research_daily_dates(_DB(), "季报")
    assert "2026-10-01" in dates, dates          # 归 Q4,不是发布日的 Q3
    assert "2026-07-01" not in dates, dates
    got = web_app._collect_variety_report_items(
        web_app._research_rows_for_type(_DB(), "季报"), "2026-10-01", "CF", "季报")
    assert len(got) == 1, got
    # Q3 口径必须**取不到**它(否则就是"选 Q3 看到 Q4 报告")
    assert web_app._collect_variety_report_items(
        web_app._research_rows_for_type(_DB(), "季报"), "2026-07-01", "CF", "季报") == []


# ---------------------------------------------------------------------------
# 10. 月报口径工具函数(2026-10-08)
# ---------------------------------------------------------------------------
def test_month_start_range_in_month():
    """月报工具:_month_start / _month_range / _in_month 的边界行为。"""
    assert web_app._month_start("2026-10-07") == "2026-10-01"
    assert web_app._month_start("2026-12-31") == "2026-12-01"
    assert web_app._month_start("") == ""
    assert web_app._month_start("bad") == ""
    assert web_app._month_range("2026-10-01") == ("2026-10-01", "2026-10-31")
    assert web_app._month_range("2026-02-01") == ("2026-02-01", "2026-02-28")  # 平年 2 月
    assert web_app._month_range("2026-12-01") == ("2026-12-01", "2026-12-31")  # 跨年
    assert web_app._month_range("2026-10-15") == ("2026-10-01", "2026-10-31")  # 月中键也归一
    assert web_app._in_month("2026-10-01", "2026-10-07") is True
    assert web_app._in_month("2026-10-01", "2026-11-07") is False
    assert web_app._in_month("2026-10-01", "2026-09-30") is False


# ---------------------------------------------------------------------------
# 11. 华泰采集器月报频道(2026-10-08)
# ---------------------------------------------------------------------------
def test_htfc_collector_has_monthly_channel():
    """华泰天玑采集器必须支持「月报」频道 10073,且 ingest_today 认识该频道。

    【背景 2026-10-08】用户发现华泰 10 篇月报没入库 —— 采集器只接日报(10074)
    与周报(10075)两个频道,天玑的月报栏目(10073)从未被拉取。漏接频道是静默的:
    采集正常结束、无报错,只是月报不在库里。
    """
    from pathlib import Path
    import web_app as _wa

    src = (Path(_wa.__file__).parent / "research_collector_htfc.py").read_text(encoding="utf-8")
    assert 'FEED_CHANNEL_MONTHLY = "10073"' in src, "采集器缺月报频道常量 10073"
    assert "fetch_monthly_items" in src, "采集器缺 fetch_monthly_items"
    assert '"月报"' in src, "采集器缺月报频道分支"
