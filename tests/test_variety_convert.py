# -*- coding: utf-8 -*-
"""品种数据库转换器 + 接口测试(不联网, 全部用临时合成工作簿)。

覆盖 variety_convert.py 六种解析器 + 品种层(推断/清单/版式自动识别) +
web_app 的 /api/variety 三个接口(含旧 /api/bitumen 别名):

  解析引擎
    1. gldt(钢联/Wind 纵向): 表头块识别、多序列、#N/A 剔除;
    2. simple(简单纵向): 日期轴 / 年度轴;
    3. simple_blocks(多块纵向, 稀释沥青升贴水式);
    4. yearstack(年度堆叠对比): 脚手架列跳过、月-日×年份重建、2月29平年跳过;
    5. month_matrix(月份×年矩阵);
    6. entity(实体×周期): 日期表头/云编码表头/周标签分类轴、group 纵向继承;
    7. table 兜底。
  品种层
    8. infer_variety 文件名推断(含未收录品种的哈希兜底);
    9. detect_parser 自动识别(未配置表名 → 六种版式各自识别 + 兜底);
   10. convert() 端到端: index.json/meta.json/逐表 slug 文件; 品种清单只在
       数据根目录下刷新(临时目录不污染真实 data/variety/varieties.json)。
   11. 兼容: bitumen_convert 垫片转发全部符号 + 旧 /api/bitumen 路由可用。
  接口
   12. /api/variety/list、/api/variety/<品种>/index、/api/variety/<品种>/sheet/<slug>
       (非法品种/slug 400、缺文件 404、未导入 ok:false)。
"""

import datetime as dt
import json

import openpyxl
import pytest

import variety_convert as vc


# ────────────────────────── 造数工具 ──────────────────────────
def _wb():
    return openpyxl.Workbook()


def _gldt_sheet(wb, title="S"):
    """钢联纵向: r1 块标 / r2 指标名称 / r3 单位 / r4 来源, r5 起日期+两序列。"""
    ws = wb.create_sheet(title)
    ws.append(["钢联数据", None, None])
    ws.append(["指标名称", "指标A", "指标B"])
    ws.append(["单位", "元/吨", "万吨"])
    ws.append(["数据来源", "隆众资讯", "隆众资讯"])
    ws.append([dt.date(2026, 1, 2), 100.5, "#N/A"])
    ws.append([dt.date(2026, 1, 1), 99.0, 55.0])
    return ws


def _yearstack_sheet(wb, title="YS"):
    """年度堆叠: A/B 列脚手架日期(应被跳过), D 列锚点 + E/F 年份列。
    行1=块标题, 行2=年份头, 行3+ 锚点日期(月-日) + 各年数值。"""
    ws = wb.create_sheet(title)
    ws.append([None, None, None, "山东基差", None])
    ws.append([None, None, None, None, 2019, 2020])
    ws.append([dt.date(2019, 1, 1), dt.date(2020, 1, 1),
               dt.date(2023, 1, 1), dt.date(2023, 1, 1), 10.0, 20.0])
    ws.append([dt.date(2019, 1, 2), dt.date(2020, 1, 2),
               dt.date(2023, 1, 2), dt.date(2023, 1, 2), 11.0, "#N/A"])
    ws.append([dt.date(2019, 2, 28), dt.date(2020, 2, 28),
               dt.date(2024, 2, 29), dt.date(2024, 2, 29), 12.0, 22.0])
    return ws


def _entity_sheet(wb, title="EN", cloud=False):
    """实体×周期: 行1 表头(meta+周期), 行2+ 实体行。cloud=True 用 2301 式编码头。"""
    ws = wb.create_sheet(title)
    if cloud:
        ws.append(["地区", "炼厂", "属性", "2301", "2302"])
        ws.append(["西北", "克石化", "中石油", 3.0, 4.0])
        ws.append([None, "乌石化", "中石油", 1.5, None])
    else:
        ws.append(["序号", "简称", "2026-01-05", "2026-01-04"])
        ws.append([1, "克石化", 2000, 2000])
        ws.append([2, "乌石化", 500, None])
    return ws


# ────────────────────────── 解析器单测 ──────────────────────────
def test_norm_date_serial_and_na():
    assert vc._norm_date(43466) == dt.date(2019, 1, 1)   # Excel 序列号
    assert vc._norm_date(dt.datetime(2026, 9, 17, 10, 0)) == dt.date(2026, 9, 17)
    assert vc._norm_date("2026-09-17") == dt.date(2026, 9, 17)
    assert vc._norm_date(123.0) is None                   # 普通数值不当日期
    assert vc._num("#N/A") is None and vc._num("1,234.5") == 1234.5


def test_period_header_numeric_text_no_crash():
    """回归: 任意 4 位数字(如价格 3630)不再被当合约码算出 month=36 抛异常。"""
    assert vc._period_header("3630") == ("3630", None)
    assert vc._period_header("2025") == ("2025", None)   # 裸年份 → 分类轴, 不崩
    assert vc._period_header("2301")[1] == dt.date(2023, 1, 1)      # 合约码照常
    assert vc._period_header("2304A")[1] == dt.date(2023, 4, 1)
    assert vc._period_header("3.2-3.8") == ("3.2-3.8", None)        # 周标签分类轴
    assert vc._period_header(None) == (None, None)


def test_parse_gldt():
    wb = _wb()
    _gldt_sheet(wb)
    obj = vc.parse_gldt(wb["S"])
    assert obj["type"] == "series" and obj["axis"] == "date"
    names = {s["name"] for s in obj["series"]}
    assert names == {"指标A", "指标B"}
    a = next(s for s in obj["series"] if s["name"] == "指标A")
    assert a["points"] == [["2026-01-01", 99.0], ["2026-01-02", 100.5]]  # 升序
    b = next(s for s in obj["series"] if s["name"] == "指标B")
    assert b["points"] == [["2026-01-01", 55.0]]                          # #N/A 剔除


def test_parse_simple_year_axis():
    wb = _wb()
    ws = wb.create_sheet("B")
    ws.append(["年份", "产量", "开工率"])
    ws.append([2011, 1267, 0.53])
    ws.append([2012, 1864.6, 0.71])
    obj = vc.parse_simple(ws, header_row=0, axis="year")
    assert obj["axis"] == "year"
    p = obj["series"][0]["points"]
    assert p[0] == ["2011-01-01", 1267] and len(p) == 2


def test_parse_simple_blocks():
    wb = _wb()
    ws = wb.create_sheet("M")
    ws.append([None, "关前升贴水", None, "关后升贴水"])
    ws.append([dt.date(2026, 1, 1), -3.6, None, dt.date(2014, 5, 5), -6.0])
    ws.append([dt.date(2026, 1, 2), -3.7, None, dt.date(2014, 5, 6), -6.5])
    obj = vc.parse_simple_blocks(ws, blocks=[{"date_col": 0, "val_col": 1, "name": "关前升贴水"},
                                             {"date_col": 3, "val_col": 4, "name": "关后升贴水"}])
    assert {s["name"] for s in obj["series"]} == {"关前升贴水", "关后升贴水"}
    post = next(s for s in obj["series"] if s["name"] == "关后升贴水")
    assert post["points"][0][0] == "2014-05-05"


def test_parse_yearstack_rebuild():
    wb = _wb()
    _yearstack_sheet(wb)
    obj = vc.parse_yearstack(wb["YS"])
    assert obj["axis"] == "date"
    s = {x["name"]: x for x in obj["series"]}
    assert list(s) == ["山东基差"]        # 只有数据块, 脚手架不产序列
    pts = dict(s["山东基差"]["points"])
    assert pts[dt.date(2019, 1, 1).isoformat()] == 10.0
    assert pts[dt.date(2019, 1, 2).isoformat()] == 11.0
    assert dt.date(2020, 2, 29).isoformat() in pts
    assert '2019-02-29' not in pts        # 平年 2月29 无效, 不应出现


def test_parse_month_matrix():
    wb = _wb()
    ws = wb.create_sheet("MM")
    ws.append(["挖掘机开工小时数"])
    ws.append([None, 2020, 2021])
    ws.append(["1月", 59.1, 106.9])
    ws.append(["2月", 30.6, 44.0])
    obj = vc.parse_month_matrix(ws)
    assert obj["axis"] == "month"
    s = obj["series"][0]
    assert s["name"] == "挖掘机开工小时数"
    assert ["2020-01-01", 59.1] in s["points"] and ["2021-02-01", 44.0] in s["points"]


def test_parse_entity_dated_axis_sorted():
    wb = _wb()
    _entity_sheet(wb)
    obj = vc.parse_entity(wb["EN"], header_row=0, meta_cols=2, name_col=1)
    assert [a["date"] for a in obj["axis"]] == ["2026-01-04", "2026-01-05"]  # 轴升序
    ent = {e["name"]: e for e in obj["entities"]}
    assert ent["克石化"]["values"] == [2000.0, 2000.0]
    assert ent["乌石化"]["values"] == [None, 500.0]   # 缺数留空不补 0


def test_parse_entity_cloud_codes_and_group_carry():
    wb = _wb()
    _entity_sheet(wb, cloud=True)
    obj = vc.parse_entity(wb["EN"], header_row=0, meta_cols=3, name_col=1, group_col=2)
    assert [a["label"] for a in obj["axis"]] == ["2301", "2302"]   # 2301/2302 → 2023-01/02
    ent = {e["name"]: e for e in obj["entities"]}
    assert ent["乌石化"]["group"] == "中石油"        # 空行 group 向下继承
    assert ent["乌石化"]["values"][1] is None


def test_parse_entity_week_labels_category_axis():
    wb = _wb()
    ws = wb.create_sheet("W")
    ws.append(["简称", "3.2-3.8", "3.9-3.15"])
    ws.append(["克石化", 0.73, 0.73])
    obj = vc.parse_entity(ws, header_row=0, meta_cols=1, name_col=0)
    assert [a["date"] for a in obj["axis"]] == [None, None]  # 周标签 → 分类轴
    assert obj["axis"][0]["label"] == "3.2-3.8"


def test_parse_table_and_fallback():
    wb = _wb()
    ws = wb.create_sheet("T")
    ws.append(["油种", "产地", "收率"])
    ws.append(["马瑞", "委内瑞拉", 0.62])
    ws.append([None, None, None])  # 空行应被剔除
    obj = vc.parse_table(ws)
    assert obj["type"] == "table" and obj["rows"] == [["马瑞", "委内瑞拉", 0.62]]


# ────────────────────────── 品种层: 名称推断 / 版式识别 ──────────────────────────
def test_infer_variety_from_filename():
    assert vc.infer_variety("沥青数据库-永安.xlsx") == ("bitumen", "沥青")
    assert vc.infer_variety("甲醇数据库-永安.xlsx") == ("methanol", "甲醇")
    assert vc.infer_variety("source_PTA数据库.xlsx") == ("pta", "PTA")
    assert vc.infer_variety("乙二醇数据.xlsx") == ("meg", "乙二醇")
    # 未收录品种: 中文名保留(去后缀), slug 用短哈希 → 前缀 v
    slug, name = vc.infer_variety("液碱数据库-永安.xlsx")
    assert name == "液碱" and slug.startswith("v") and len(slug) == 9


def test_detect_parser_six_layouts():
    wb = _wb()
    _gldt_sheet(wb, "g")
    _entity_sheet(wb, "e")
    _yearstack_sheet(wb, "y")
    ws = wb.create_sheet("sb")                     # 多块纵向
    ws.append([None, "关前升贴水", None, "关后升贴水"])
    ws.append([dt.date(2026, 1, 1), -3.6, None, dt.date(2014, 5, 5), -6.0])
    ws.append([dt.date(2026, 1, 2), -3.7, None, dt.date(2014, 5, 6), -6.5])
    ws.append([dt.date(2026, 1, 3), -3.8, None, dt.date(2014, 5, 7), -6.6])
    ws2 = wb.create_sheet("s")                     # 简单纵向(日度)
    ws2.append(["日期", "价格"])
    for d in (1, 2, 3, 4):
        ws2.append([dt.date(2026, 1, d), 100 + d])
    ws3 = wb.create_sheet("sy")                    # 年度纵向
    ws3.append(["年份", "产量"])
    for y in (2019, 2020, 2021):
        ws3.append([y, 100 + y])
    ws4 = wb.create_sheet("t")                     # 无可识别结构 → 表格
    ws4.append(["油种", "收率"])
    ws4.append(["马瑞", 0.62])
    ws5 = wb.create_sheet("mm")                    # 月份×年矩阵
    ws5.append(["开工小时数"])
    ws5.append([None, 2020, 2021])
    for m in range(1, 8):
        ws5.append([f"{m}月", 10 + m, 20 + m])

    assert vc.detect_parser(wb["g"])[0] == "gldt"
    assert vc.detect_parser(wb["e"])[0] == "entity"
    assert vc.detect_parser(wb["y"])[0] == "yearstack"
    assert vc.detect_parser(wb["sb"])[0] == "simple_blocks"
    assert vc.detect_parser(wb["s"]) == ("simple", {"header_row": 0})
    assert vc.detect_parser(wb["sy"]) == ("simple", {"header_row": 0, "axis": "year"})
    assert vc.detect_parser(wb["mm"])[0] == "month_matrix"
    assert vc.detect_parser(wb["t"])[0] == "table"
    # entity 的 meta/name 列经启发式给出(名称列取 meta 区里最靠右的高区分度列)
    p = vc.detect_parser(wb["e"])[1]
    assert p["header_row"] == 0 and p["meta_cols"] == 2 and p["name_col"] == 1


# ────────────────────────── convert() 端到端 ──────────────────────────
def test_convert_end_to_end(tmp_path):
    wb = _wb()
    _gldt_sheet(wb, "布油(Wind)")
    _yearstack_sheet(wb, "盘面")
    _entity_sheet(wb, "日产")
    ws_t = wb.create_sheet("原料")
    ws_t.append(["油种", "收率"])
    ws_t.append(["马瑞", 0.62])
    del wb["Sheet"]
    src = tmp_path / "src.xlsx"
    wb.save(src)

    out = tmp_path / "out"
    index = vc.convert(src, out_dir=out, slug="bitumen", name="沥青")
    assert [i["slug"] for i in index] == ["s01", "s02", "s03", "s04"]
    by_slug = {i["slug"]: i for i in index}
    assert by_slug["s01"]["type"] == "series" and by_slug["s01"]["n_series"] == 2
    assert by_slug["s02"]["date_min"] == "2019-01-01"
    assert by_slug["s03"]["type"] == "entity"
    assert by_slug["s04"]["type"] == "table"
    for i in index:
        p = out / f"{i['slug']}.json"
        assert p.exists() and json.loads(p.read_text(encoding="utf-8"))["type"] == i["type"]
    assert len(json.loads((out / "index.json").read_text(encoding="utf-8"))) == 4
    # 品种元信息
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "bitumen" and meta["name"] == "沥青"
    assert meta["n_sheets"] == 4
    assert meta["n_series"] == sum(i["n_series"] for i in index)  # 与索引自洽
    assert meta["n_auto"] == 0 and "行情价格" in meta["groups"]


def test_convert_unconfigured_sheet_auto_detected(tmp_path):
    """未配置表名(新模板新增工作表) → 自动识别, 不再一律兜底成表格。"""
    wb = _wb()
    _entity_sheet(wb, "新表(自动)")
    del wb["Sheet"]
    src = tmp_path / "src.xlsx"
    wb.save(src)
    index = vc.convert(src, out_dir=tmp_path / "out", slug="x", name="X")
    assert index[0]["type"] == "entity" and index[0]["recognized_by"] == "auto"


def test_convert_unknown_sheet_falls_back_to_table(tmp_path, capsys):
    wb = _wb()
    wb.create_sheet("完全未知的表")
    del wb["Sheet"]
    src = tmp_path / "src.xlsx"
    wb.save(src)
    index = vc.convert(src, out_dir=tmp_path / "out", slug="x", name="X")
    assert index[0]["type"] == "table" and index[0]["recognized_by"] == "auto"
    assert "兜底" in capsys.readouterr().out


def test_convert_registry_only_under_root(tmp_path, monkeypatch):
    """品种清单只在数据根目录下刷新; 临时输出目录不污染真实 varieties.json。"""
    root = tmp_path / "variety"
    monkeypatch.setattr(vc, "DEFAULT_ROOT", root)
    wb = _wb()
    _gldt_sheet(wb, "布油(Wind)")
    del wb["Sheet"]
    src = tmp_path / "src.xlsx"
    wb.save(src)
    vc.convert(src, out_dir=tmp_path / "solo", slug="solo", name="临时")
    assert not (root / "varieties.json").exists()      # 根目录之外 → 不写清单

    vc.convert(src, out_dir=root / "methanol", slug="methanol", name="甲醇")
    reg = json.loads((root / "varieties.json").read_text(encoding="utf-8"))
    assert [r["slug"] for r in reg] == ["methanol"]
    assert reg[0]["name"] == "甲醇" and reg[0]["n_sheets"] == 1
    assert vc.list_varieties(root)[0]["slug"] == "methanol"


# ────────────────────────── 兼容: 旧模块名/旧路由 ──────────────────────────
def test_bitumen_convert_shim_forwards():
    import bitumen_convert as bc
    assert hasattr(bc, "parse_gldt") and hasattr(bc, "_norm_date")
    assert hasattr(bc, "detect_parser") and hasattr(bc, "infer_variety")
    assert bc.OUT_DIR.name == "bitumen" and bc.OUT_DIR.parent.name == "variety"


# ────────────────────────── API ──────────────────────────
def _seed_variety(root, vs, name="品种", source="source_x.xlsx"):
    d = root / vs
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps(
        [{"slug": "s01", "name": "盘面", "type": "series", "group": "行情价格",
          "n_series": 2, "date_min": "2019-01-02", "date_max": "2026-09-17",
          "units": [], "recognized_by": "config"}], ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(
        {"slug": vs, "name": name, "source": source, "imported_at": "2026-09-18 10:25:01",
         "n_sheets": 1, "n_series": 2}, ensure_ascii=False), encoding="utf-8")
    (d / "s01.json").write_text(json.dumps(
        {"type": "series", "axis": "date", "series": []}, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture
def variety_root(tmp_path, monkeypatch):
    root = tmp_path / "variety"
    root.mkdir()
    monkeypatch.setattr(web_app_mod, "_VARIETY_DIR", root)
    return root


def test_api_variety_list(variety_root):
    (variety_root / "varieties.json").write_text(json.dumps(
        [{"slug": "bitumen", "name": "沥青", "n_sheets": 58, "n_series": 1154},
         {"slug": "methanol", "name": "甲醇", "n_sheets": 30, "n_series": 200}],
        ensure_ascii=False), encoding="utf-8")
    r = web_app_mod.app.test_client().get("/api/variety/list")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    slugs = [v["slug"] for v in r.get_json()["varieties"]]
    assert slugs == ["bitumen", "methanol"]


def test_api_variety_list_rebuild_without_registry(variety_root):
    """varieties.json 缺失 → 按子目录重建(兼容手工放目录)。"""
    _seed_variety(variety_root, "bitumen", "沥青")
    _seed_variety(variety_root, "methanol", "甲醇")
    j = web_app_mod.app.test_client().get("/api/variety/list").get_json()
    assert [v["slug"] for v in j["varieties"]] == ["bitumen", "methanol"]
    assert j["varieties"][1]["name"] == "甲醇"          # 名称取自 meta.json


def test_api_variety_index_and_sheet(variety_root):
    _seed_variety(variety_root, "methanol", "甲醇")
    c = web_app_mod.app.test_client()
    j = c.get("/api/variety/methanol/index").get_json()
    assert j["ok"] is True and j["variety"] == "methanol"
    assert j["meta"]["name"] == "甲醇" and j["sheets"][0]["name"] == "盘面"
    r = c.get("/api/variety/methanol/sheet/s01")
    assert r.status_code == 200 and r.get_json()["type"] == "series"


def test_api_variety_guards(variety_root):
    _seed_variety(variety_root, "bitumen", "沥青")
    c = web_app_mod.app.test_client()
    # 非法品种标识(大写/穿越/空) → 400
    assert c.get("/api/variety/AB/index").status_code == 400
    # 编码斜杠由路由层直接拦成 404(不进视图), 亦无法穿越目录
    assert c.get("/api/variety/..%2fx/index").status_code in (400, 404)
    assert c.get("/api/variety/x!/index").status_code == 400
    # 非法表 slug(白名单 s+数字) → 400
    assert c.get("/api/variety/bitumen/sheet/xyz").status_code == 400
    assert c.get("/api/variety/bitumen/sheet/s01x").status_code == 400
    assert c.get("/api/variety/bitumen/sheet/").status_code in (400, 404)
    # 合法 slug 但文件缺失 → 404
    assert c.get("/api/variety/bitumen/sheet/s09").status_code == 404
    # 品种未导入 → 200 + ok:false(前端据此提示)
    r = c.get("/api/variety/methanol/index")
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert r.get_json()["sheets"] == []


def test_api_bitumen_legacy_alias(variety_root):
    """旧路由 /api/bitumen/* 保留为 bitumen 品种别名。"""
    _seed_variety(variety_root, "bitumen", "沥青")
    c = web_app_mod.app.test_client()
    j = c.get("/api/bitumen/index").get_json()
    assert j["ok"] is True and j["sheets"][0]["name"] == "盘面"
    assert c.get("/api/bitumen/sheet/s01").get_json()["type"] == "series"
    assert c.get("/api/bitumen/sheet/xyz").status_code == 400


# web_app 导入放最后: 其 import 开销大, 且 fixture 需要引用它
import web_app as web_app_mod  # noqa: E402
