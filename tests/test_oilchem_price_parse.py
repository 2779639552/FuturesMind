# -*- coding: utf-8 -*-
"""隆众现货价格采集器的解析逻辑回归测试。

重点覆盖 `priceBodyMap` 的**三种**形态 —— 这是最容易漏判的地方：
用 JS 的 Object.keys() 看数组也会返回 ["0","1",...]，看起来像 dict 实为 list，
本次就因此让原油静默返回 0 行。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "oilchem_price_collect", ROOT / "oilchem_price_collect.py")
opc = importlib.util.module_from_spec(_spec)
sys.modules["oilchem_price_collect"] = opc
_spec.loader.exec_module(opc)


# ── 形态 A：按区域分组（沥青实测结构）──
SHAPE_A = {"status": "200", "response": {
    "varietiesId": "372", "varietiesName": "重交沥青", "total": 69,
    "dateList": ["2026/10/12", "2026/10/13", "2026/10/14"],
    "priceHeadList": [{"cnField": "市场"}, {"cnField": "规格"}, {"cnField": "品牌"}],
    "pricePowerBO": {"status": 9, "see": True, "download": True},
    "priceBodyMap": {
        "东北地区": [
            {"regionName": "东北地区", "specificationsName": "90#", "brandName": "鑫锦源",
             "unitValuationName": "元/吨", "businessId": 174930, "dataRiseOrFall": "0",
             "dataRate": "0%", "remark": "货少",
             "2026/10/13": {"price": {"主流价": "6400"}, "dataRiseOrFall": {"主流价": "0"}},
             "2026/10/14": {"price": {"主流价": "6400"}, "dataRiseOrFall": {"主流价": "0"}}},
            # 询价中 → 应被剔除
            {"regionName": "东北地区", "specificationsName": "重交沥青", "brandName": "",
             "unitValuationName": "元/吨",
             "2026/10/14": {"price": {}, "dataRiseOrFall": {}}},
        ],
        "华东地区": [
            {"regionName": "华东地区", "marketName": "浙江", "specificationsName": "70#",
             "brandName": "海韵牌", "unitValuationName": "元/吨", "dataRiseOrFall": "20",
             "dataRate": "0.31%",
             "2026/10/13": {"price": {"主流价": "6400", "高端价": "6550"},
                            "dataRiseOrFall": {"主流价": "0"}},
             "2026/10/14": {"price": {"主流价": "6420", "高端价": "6570"},
                            "dataRiseOrFall": {"主流价": "20"}}},
        ],
    }}}

# ── 形态 C：list（原油实测结构）──
SHAPE_C = {"status": "200", "response": {
    "varietiesId": "110", "varietiesName": "原油", "total": 2,
    "dateList": ["2026/10/12", "2026/10/13"],
    "priceHeadList": [{"cnField": "市场"}, {"cnField": "价格条件"}],
    "pricePowerBO": {"status": 9, "see": True},
    "priceBodyMap": [
        {"priceTypeName": "DES", "regionName": "南美洲", "specificationsName": "马瑞原油",
         "unitValuationName": "美元/桶", "rmbPrice": "5540.08", "dataRiseOrFall": "-0.96",
         "2026/10/12": {"price": {"主流价": "111.15"}, "dataRiseOrFall": {"主流价": "1"}},
         "2026/10/13": {"price": {"主流价": "110.19"}, "dataRiseOrFall": {"主流价": "-1"}}},
        {"regionName": "中东", "specificationsName": "巴士拉轻质", "unitValuationName": "美元/桶",
         "2026/10/13": {"price": {"主流价": "95.30"}, "dataRiseOrFall": {"主流价": "0"}}},
    ]}}

# ── 形态 B：dict[key] = 单行（少见，保留兼容）──
SHAPE_B = {"status": "200", "response": {
    "varietiesId": "218", "varietiesName": "纯苯", "total": 1,
    "dateList": ["2026/10/14"],
    "priceHeadList": [{"cnField": "市场"}],
    "pricePowerBO": {"status": 9, "see": True},
    "priceBodyMap": {
        "0": {"regionName": "东北地区", "specificationsName": "石油苯",
              "unitValuationName": "元/吨",
              "2026/10/14": {"price": {"主流价": "10550"}, "dataRiseOrFall": {"主流价": "100"}}},
    }}}

# ── 无权限：行骨架完整但价格全空（实测 CJ/LH/M/PS/SI）──
NO_PERM = {"status": "200", "response": {
    "varietiesId": "4730", "varietiesName": "生猪", "total": 171,
    "dateList": ["2026/10/14"],
    "priceHeadList": [{"cnField": "市场"}],
    "pricePowerBO": {"status": 1, "see": False, "download": False},
    "priceBodyMap": {"东北地区": [
        {"regionName": "东北地区", "specificationsName": "均重50公斤",
         "unitValuationName": "元/头",
         "2026/10/14": {"price": {"主流价": ""}, "dataRiseOrFall": {"主流价": ""}}},
    ]}}}


def test_shape_a_region_group():
    a = opc.parse_price_response(SHAPE_A, "BU")
    assert a is not None
    assert len(a["rows"]) == 2, "询价中的行应被剔除"
    assert a["rowsDropped"] == 1
    r0 = a["rows"][0]
    assert r0["latest"]["主流价"] == 6400.0
    assert r0["prev"].get("主流价") == 6400.0
    assert r0["change"] == 0.0
    assert r0["remark"] == "货少"
    r1 = a["rows"][1]
    assert set(r1["latest"]) == {"主流价", "高端价"}, "多价格分项必须都保留"
    assert r1["market"] == "浙江"
    assert r1["change"] == 20.0
    assert a["priceItems"] == ["主流价", "高端价"]
    assert a["power"]["status"] == 9 and a["power"]["download"] is True


def test_shape_c_list():
    """★ 关键回归：priceBodyMap 是 list（原油）时不能返回空。"""
    c = opc.parse_price_response(SHAPE_C, "SC")
    assert c is not None, "list 形态必须能解析（否则原油永远 0 行）"
    assert len(c["rows"]) == 2
    assert c["rows"][0]["latest"]["主流价"] == 110.19
    assert c["rows"][0]["rmbPrice"] == 5540.08
    assert c["rows"][0]["priceType"] == "DES"
    assert c["rows"][0]["unit"] == "美元/桶"


def test_shape_b_single_row_dict():
    b = opc.parse_price_response(SHAPE_B, "BZ")
    assert b is not None and len(b["rows"]) == 1
    assert b["rows"][0]["latest"]["主流价"] == 10550.0


def test_no_permission_rows_dropped():
    """无权限时价格全空 → 行必须被剔除，不产出"有行无价"的脏数据。"""
    n = opc.parse_price_response(NO_PERM, "LH")
    assert n is None, "全部为空值的行应被剔到不剩任何结果"


def test_empty_and_bad_input():
    assert opc.parse_price_response({"response": {}}, "X") is None
    assert opc.parse_price_response(
        {"response": {"dateList": ["2026/10/14"],
                      "priceBodyMap": {"东北": [{"regionName": "东北",
                                                 "2026/10/14": {"price": {}}}]}}}, "X") is None


def test_num_normalization():
    assert opc._num("1,234.5") == 1234.5
    assert opc._num("") is None
    assert opc._num("--") is None
    assert opc._num(None) is None
    assert opc._num("6400") == 6400.0


def test_variety_map_covers_pool():
    """映射表的基本卫生检查（防止日后改动漏项）。"""
    assert len(opc.VARIETY_CHANNELS) == 20
    assert "NR" not in opc.VARIETY_CHANNELS, "20 号胶隆众未覆盖，不应出现在表内"
    assert opc.PAGE_SIZE <= 100, "pageSize 上限 100，超过会被服务端拒（400）"
    assert all(isinstance(v, int) for v in opc.VARIETY_CHANNELS.values())
