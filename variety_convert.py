# -*- coding: utf-8 -*-
"""
variety_convert.py — 品种数据库 xlsx → 逐表 JSON (data/variety/<品种>/)

通用导入器: 把永安系列的「<品种>数据库.xlsx」解析为规范化 JSON,
供 web_app.py 的 /api/variety/* 接口与前端「品种数据库」tab 消费。
每个工作表 = 一个数据模块。

用法(新收到一个品种的文件时, 一条命令导入):
    python variety_convert.py --src "甲醇数据库-永安.xlsx"             # 品种名/标识从文件名推断
    python variety_convert.py --src x.xlsx --name 甲醇 --slug methanol  # 显式指定
    python variety_convert.py --variety bitumen                        # 重跑已导入品种
    python variety_convert.py --list                                   # 列出已导入品种
    python bitumen_convert.py                                          # 兼容旧入口(等价 --variety bitumen)

目录约定:
    data/variety/varieties.json       — 品种清单(前端品种下拉用)
    data/variety/<slug>/meta.json     — 品种元信息(slug/中文名/源文件/导入时间/表数)
    data/variety/<slug>/index.json    — 模块索引(slug/名称/类型/序列数/日期范围/单位/分组)
    data/variety/<slug>/s{NN}.json    — 每个工作表一个文件(前端按需加载)
    data/variety/<slug>/source_*.xlsx — 源文件快照(重跑不依赖微信目录)

三类版式(type):
    series   — 时间序列型: {series: [{name, unit, points: [[iso, val],...]}]}
               覆盖 gldt(钢联/Wind纵向)、simple(简单纵向)、yearstack(年度堆叠对比)、
               month_matrix(月份×年矩阵) 四种解析器, 统一转成「日期轴 + 多序列」。
    entity   — 实体×周期型(炼厂排产/日产/负荷/远期结构/报告快照):
               {axis: [{label, date}], entities: [{name, group, meta, values}]}
    table    — 不规则表(原料说明/看板快照): {headers, rows} 原样呈现。

版式识别:
    1) 表名命中 SHEET_CONFIG(永安数据库模板的已知表) → 用配置的解析器;
    2) 未命中的表名 → detect_parser() 启发式识别(见函数注释), 其余表格兜底。
       新版模板新增工作表时无需改代码即可导入; 若识别不准, 把表名加进 SHEET_CONFIG 即可。

口径纪律:
    - #N/A、空串、非数值一律丢弃, 不补 0 不造数;
    - Excel 序列号(20000~60000) 按 1899-12-30 纪元转日期(气温表 2019 列为序列号);
    - yearstack 按「块日期列的月-日 × 年份列」重建完整日度序列;
    - 重复日期后者覆盖前者; 输出点按日期升序、数值 round(4)。
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

import openpyxl

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = BASE_DIR / "data" / "variety"   # 品种数据库根目录(每个品种一个子目录)
OUT_DIR = DEFAULT_ROOT / "bitumen"             # 缺省品种(沥青), 兼容旧入口/旧调用

# 品种中文名 → 目录标识(URL 段用)。文件名含这些词即可自动归位;
# 未收录品种会生成 v<短哈希> 标识, 建议用 --slug 指定可读名字。
VARIETY_ALIASES = {
    "沥青": "bitumen", "原油": "crude", "燃料油": "fueloil", "低硫燃油": "lusfuel",
    "甲醇": "methanol", "乙二醇": "meg", "苯乙烯": "styrene", "纯苯": "benzene",
    "对二甲苯": "px", "PTA": "pta", "瓶片": "petchip", "短纤": "staple",
    "聚丙烯": "pp", "聚乙烯": "pe", "PVC": "pvc", "烧碱": "caustic",
    "尿素": "urea", "纯碱": "sodaash", "玻璃": "glass", "纸浆": "pulp",
    "橡胶": "rubber", "碳酸锂": "lithium", "工业硅": "silicon", "多晶硅": "polysilicon",
    "螺纹钢": "rebar", "铁矿石": "ironore", "焦炭": "coke", "焦煤": "cokingcoal",
    "动力煤": "thermalcoal", "铜": "copper", "铝": "aluminium", "锌": "zinc",
    "镍": "nickel", "黄金": "gold", "白银": "silver", "白糖": "sugar",
    "棉花": "cotton", "玉米": "corn", "豆粕": "soymeal", "豆油": "soyoil",
    "棕榈油": "palmoil", "菜油": "rapeseedoil", "菜粕": "rapeseedmeal",
    "生猪": "hog", "苹果": "apple", "红枣": "reddate", "花生": "peanut", "鸡蛋": "egg",
}
VARIETY_NAMES = {v: k for k, v in VARIETY_ALIASES.items()}

# 【变量】品种 slug → 期货品种代码(运行分析用代码定位品种库; 顺带收录常见别名,
#   如乙二醇在本项目内部代码为 EG, 兼容 MEG 写法)。未收录品种按品种中文名反查。
VARIETY_SYMBOLS = {
    "bitumen": ["BU"], "crude": ["SC"], "fueloil": ["FU"], "lusfuel": ["LU"],
    "methanol": ["MA"], "meg": ["EG", "MEG"], "styrene": ["EB"], "benzene": ["BZ"],
    "px": ["PX"], "pta": ["TA"], "petchip": ["PR"], "staple": ["PF"],
    "pp": ["PP"], "pe": ["L"], "pvc": ["V"], "caustic": ["SH"],
    "urea": ["UR"], "sodaash": ["SA"], "glass": ["FG"], "pulp": ["SP"],
    "rubber": ["RU"], "lithium": ["LC"], "silicon": ["SI"], "polysilicon": ["PS"],
    "rebar": ["RB"], "ironore": ["I"], "coke": ["J"], "cokingcoal": ["JM"],
    "thermalcoal": ["ZC"], "copper": ["CU"], "aluminium": ["AL"], "zinc": ["ZN"],
    "nickel": ["NI"], "gold": ["AU"], "silver": ["AG"], "sugar": ["SR"],
    "cotton": ["CF"], "corn": ["C"], "soymeal": ["M"], "soyoil": ["Y"],
    "palmoil": ["P"], "rapeseedoil": ["OI"], "rapeseedmeal": ["RM"],
    "hog": ["LH"], "apple": ["AP"], "reddate": ["CJ"], "peanut": ["PK"], "egg": ["JD"],
}


def symbols_for(slug):
    """品种 slug → 期货代码列表(未收录返回空列表, 由分析侧按中文名兜底匹配)。"""
    return list(VARIETY_SYMBOLS.get(slug or "", []))
_ALIAS_LONG_FIRST = sorted(VARIETY_ALIASES.items(), key=lambda kv: -len(kv[0]))

_SERIAL_EPOCH = dt.date(1899, 12, 30)
_NA_STRINGS = {"#N/A", "#N/A N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}

# 各表版式配置: sheet名 → (解析器, 参数, 分组)
# 解析器: gldt | simple | yearstack | month_matrix | entity | table
# simple 参数: header_row(idx0, 表头行), axis: date|year
# entity 参数: header_row, meta_cols, name_col, group_col(可选), skip(表头黑名单)
#   (entity 的 meta_cols 之后所有列都当周期列; 表头解析不出周期且在 skip 里则跳过)
SHEET_CONFIG = {
    "布油(Wind)":            ("gldt", {}, "行情价格"),
    "现货价格(钢联)":        ("gldt", {}, "行情价格"),
    "期货价格(钢联)":        ("gldt", {}, "行情价格"),
    "仓单(钢联)":            ("gldt", {}, "库存仓单"),
    "供给(钢联)":            ("gldt", {}, "供需"),
    "需求(钢联)":            ("gldt", {}, "供需"),
    "库存(钢联)":            ("gldt", {}, "库存仓单"),
    "成本(钢联)":            ("gldt", {}, "成本利润"),
    "稀释沥青库存(钢联)":    ("gldt", {}, "库存仓单"),
    "宏观数据(钢联)":        ("gldt", {}, "宏观天气"),
    "利润(钢联)":            ("gldt", {}, "成本利润"),
    "气温(Wind)":            ("gldt", {}, "宏观天气"),
    "道路需求(Wind)":        ("gldt", {}, "需求"),
    "炼厂及成品油(钢联)":    ("gldt", {}, "炼厂排产"),
    "道路需求":              ("month_matrix", {}, "需求"),
    "进出口量":              ("month_matrix", {}, "供需"),
    "炼厂及成品油":          ("yearstack", {}, "炼厂排产"),
    "现货及区域价差":        ("yearstack", {}, "基差价差"),
    "盘面":                  ("yearstack", {}, "行情价格"),
    "基差月差":              ("yearstack", {}, "基差价差"),
    "裂差利润":              ("yearstack", {}, "成本利润"),
    "期货相关":              ("yearstack", {}, "行情价格"),
    "期货库存":              ("yearstack", {}, "库存仓单"),
    "进口利润":              ("yearstack", {}, "成本利润"),
    "开工":                  ("yearstack", {}, "炼厂排产"),
    "产量":                  ("yearstack", {}, "供需"),
    "需求":                  ("yearstack", {}, "供需"),
    "成品库存":              ("yearstack", {}, "库存仓单"),
    "库存加总(隆)":          ("yearstack", {}, "库存仓单"),
    "库存加总(云)":          ("yearstack", {}, "库存仓单"),
    "稀释沥青库存":          ("yearstack", {}, "库存仓单"),
    "气温":                  ("yearstack", {}, "宏观天气"),
    "日产图":                ("yearstack", {}, "炼厂排产"),
    "现货及市场情绪":        ("simple", {"header_row": 0}, "基差价差"),
    "价格关系":              ("simple", {"header_row": 0}, "行情价格"),
    "基差复盘":              ("simple", {"header_row": 0}, "基差价差"),
    "平衡表":                ("simple", {"header_row": 0}, "平衡表"),
    "库存历史数据(隆)":      ("simple", {"header_row": 0}, "库存仓单"),
    "库存历史数据(云)":      ("simple", {"header_row": 0}, "库存仓单"),
    "分集团月度排产(隆)":    ("simple", {"header_row": 0}, "炼厂排产"),
    "分集团月度产量(隆)":    ("simple", {"header_row": 0}, "炼厂排产"),
    "分集团月度排产(云) ":   ("simple", {"header_row": 0}, "炼厂排产"),
    "分集团月度产量(云) ":   ("simple", {"header_row": 0}, "炼厂排产"),
    "排产产量差(总":         ("simple", {"header_row": 0}, "炼厂排产"),
    "每日汇总":              ("simple", {"header_row": 2}, "报告快照"),
    "年度平衡":              ("simple", {"header_row": 1, "axis": "year"}, "平衡表"),
    "稀释沥青升贴水":        ("simple_blocks",
                              {"blocks": [{"date_col": 0, "val_col": 1, "name": "关前升贴水"},
                                          {"date_col": 3, "val_col": 4, "name": "关后升贴水"}]},
                              "基差价差"),
    "远期结构":              ("entity", {"header_row": 0, "meta_cols": 1, "name_col": 0}, "行情价格"),
    "日产":                  ("entity", {"header_row": 0, "meta_cols": 8, "name_col": 5}, "炼厂排产"),
    "装置负荷追踪":          ("entity", {"header_row": 0, "meta_cols": 8, "name_col": 5}, "炼厂排产"),
    "分炼厂月度排产(隆)":    ("entity", {"header_row": 0, "meta_cols": 8, "name_col": 5}, "炼厂排产"),
    "分炼厂月度产量(隆)":    ("entity", {"header_row": 0, "meta_cols": 8, "name_col": 5}, "炼厂排产"),
    "分炼厂月度排产(云) ":   ("entity", {"header_row": 0, "meta_cols": 3, "name_col": 1, "group_col": 2}, "炼厂排产"),
    "分炼厂月度产量(云)":    ("entity", {"header_row": 0, "meta_cols": 3, "name_col": 1, "group_col": 2}, "炼厂排产"),
    "日度数据":              ("entity", {"header_row": 8, "group_col": 1, "name_col": 2,
                                          "skip": {"指标", "日度变化", "周度变化"}}, "报告快照"),
    "早报":                  ("entity", {"header_row": 8, "group_col": 1, "name_col": 2,
                                          "skip": {"指标", "日度变化", "周度变化"}}, "报告快照"),
    "成品油周度数据":        ("table", {}, "报告快照"),
    "原料":                  ("table", {}, "其他"),
}
_FALLBACK = ("table", {}, "其他")

_MONTH_CN = re.compile(r"^(\d{1,2})月$")
_YEAR_CN = re.compile(r"^(20\d{2})\s*年?$")
_YEAR_CN_SHORT = re.compile(r"^(\d{2})\s*年\s*(\d{1,2})\s*月")      # 16年1月
_YEAR_MONTH_CN = re.compile(r"^(20\d{2})\s*年\s*(\d{1,2})\s*月")    # 2021年5月排产
_CLOUD_CODE = re.compile(r"^(\d{2})(\d{2})([AB]?)$")                # 2301 / 2304A
_WEEK_LABEL = re.compile(r"^\d{1,2}\.\d{1,2}\s*[-~～]\s*\d{1,2}\.\d{1,2}$")  # 3.2-3.8


# ────────────────────────── 基础工具 ──────────────────────────
def _norm_date(v):
    """任意单元格 → dt.date | None (不猜格式, 认不出就 None)。"""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)) and 20000 <= float(v) <= 60000:
        # Excel 序列号(1954-10 → 2064-04), 排除普通数值误判
        return _SERIAL_EPOCH + dt.timedelta(days=int(v))
    if isinstance(v, str):
        s = v.strip()
        for pat in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
            try:
                return dt.datetime.strptime(s, pat).date()
            except ValueError:
                pass
    return None


def _norm_date_or_month(v):
    """额外接受 'YYYY-MM' 字符串 → 当月1日(月度轴列头)。"""
    d = _norm_date(v)
    if d:
        return d
    if isinstance(v, str):
        m = re.match(r"^(20\d{2})[-/](\d{1,2})$", v.strip())
        if m:
            return dt.date(int(m.group(1)), int(m.group(2)), 1)
    return None


def _num(v):
    """任意单元格 → float | None。'#N/A' 等、字符串、bool 一律 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN/Inf
            return None
        return f
    if isinstance(v, str):
        s = v.strip()
        if not s or s in _NA_STRINGS:
            return None
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None
    return None


def _s(v):
    """单元格 → 干净字符串(去首尾空格), None → ''。"""
    if v is None:
        return ""
    return str(v).strip()


def _read_grid(ws, max_rows=11000, max_cols=620):
    """工作表 → 二维 list[rows][cols] (values_only, 截断保护)。"""
    grid = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            break
        r = list(row[:max_cols])
        grid.append(r)
    return grid


def _cell(grid, r, c):
    if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
        return grid[r][c]
    return None


def _push_point(series_pts, date, val, dedupe):
    """写入一个数据点; dedupe 字典负责同日覆盖(后者胜)。"""
    if date is None:
        return
    f = _num(val)
    if f is None:
        return
    dedupe[date] = round(f, 4)


# ────────────────────────── 解析器: gldt(钢联/Wind 纵向) ──────────────────────────
def parse_gldt(ws):
    """钢联/Wind 导出格式: 前几行是 指标名称/单位/数据来源/指标编码/频度 表头块,
    之后 A 列为日期(全表共用一条日期轴), 各列为序列。多块共享 A 列日期轴。"""
    grid = _read_grid(ws)
    hdr_row = unit_row = None
    freq_row = None
    for i in range(min(12, len(grid))):
        a = _s(_cell(grid, i, 0))
        if a == "指标名称" and hdr_row is None:
            hdr_row = i
        elif a == "单位" and unit_row is None:
            unit_row = i
        elif a in ("频率", "频度") and freq_row is None:
            freq_row = i  # 兜底: Wind 表可能没有单位行, 频率行不当单位
    if unit_row is None:
        unit_row = freq_row
    if hdr_row is None:
        return {"type": "series", "axis": "date", "series": []}
    # 数据起始行 = 表头块之后第一行 A 列为日期
    start = hdr_row + 1
    for i in range(hdr_row + 1, min(hdr_row + 15, len(grid))):
        if _norm_date(_cell(grid, i, 0)):
            start = i
            break
    cols = []
    for j in range(1, len(grid[hdr_row])):
        name = _s(_cell(grid, hdr_row, j))
        if name and name not in _NA_STRINGS:
            unit = _s(_cell(grid, unit_row, j)) if unit_row is not None else ""
            cols.append((j, name, unit if unit not in _NA_STRINGS else ""))
    series = []
    for j, name, unit in cols:
        dedupe = {}
        for i in range(start, len(grid)):
            d = _norm_date(_cell(grid, i, 0))
            if d is None:
                continue
            _push_point(series_pts=None, date=d, val=_cell(grid, i, j), dedupe=dedupe)
        pts = sorted(dedupe.items())
        if pts:
            series.append({"name": name, "unit": unit, "points": [[k.isoformat(), v] for k, v in pts]})
    return {"type": "series", "axis": "date", "series": series}


# ────────────────────────── 解析器: simple(简单纵向) ──────────────────────────
def parse_simple(ws, header_row=0, axis="date"):
    """第一行(或指定行)为序列名, A 列为日期/年份轴。多组表头由调用方指定行号。"""
    grid = _read_grid(ws)
    ncols = max((len(r) for r in grid[: header_row + 1]), default=0)
    cols = []
    for j in range(1, ncols):
        name = _s(_cell(grid, header_row, j))
        if name:
            cols.append((j, name))
    series = []
    for j, name in cols:
        dedupe = {}
        for i in range(header_row + 1, len(grid)):
            raw = _cell(grid, i, 0)
            d = _norm_date(raw) if axis == "date" else None
            if axis == "year":
                y = raw if isinstance(raw, (int, float)) and 1990 <= float(raw) <= 2100 else None
                if y is None:
                    d = _norm_date(raw)
                    if d:
                        y = d.year
                if y is not None:
                    d = dt.date(int(y), 1, 1)
            else:
                d = _norm_date_or_month(raw)
            if d is None:
                continue
            _push_point(None, d, _cell(grid, i, j), dedupe)
        pts = sorted(dedupe.items())
        if pts:
            series.append({"name": name, "unit": "", "points": [[k.isoformat(), v] for k, v in pts]})
    return {"type": "series", "axis": axis, "series": series}


def parse_simple_blocks(ws, blocks):
    """多块纵向(稀释沥青升贴水): 每块独立 日期列+数值列+名称。"""
    grid = _read_grid(ws)
    series = []
    for b in blocks:
        dc, vc, name = b["date_col"], b["val_col"], b["name"]
        dedupe = {}
        for i in range(1, len(grid)):
            d = _norm_date(_cell(grid, i, dc))
            if d is None:
                continue
            _push_point(None, d, _cell(grid, i, vc), dedupe)
        pts = sorted(dedupe.items())
        if pts:
            series.append({"name": name, "unit": "", "points": [[k.isoformat(), v] for k, v in pts]})
    return {"type": "series", "axis": "date", "series": series}


# ────────────────────────── 解析器: yearstack(年度堆叠对比) ──────────────────────────
def _year_from_header(v):
    """表头单元格 → 年份 int | None (2019 / '2019' / '2018年' 均可)。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 1990 <= v <= 2100 and float(v).is_integer():
            return int(v)
        return None
    s = _s(v)
    m = _YEAR_CN.match(s)
    if m:
        return int(m.group(1))
    return None


def parse_yearstack(ws):
    """年度堆叠对比格式(盘面/基差月差/开工/气温/日产图等):
    - 若干「脚手架」日期列(2019..2026 逐年一列, 仅提供月-日刻度, 无数据, 忽略);
    - 数据块: 锚点日期列(月-日刻度) + 右侧一组年份列(2019|2020|...), 块标题在锚点列上方 r1。
    重建: 每块每行, 月-日 × 各年份列 → 完整日度序列。"""
    grid = _read_grid(ws)
    nrow, ncol = len(grid), max((len(r) for r in grid), default=0)
    # 1) 找锚点列: 第2行(idx1)起日期单元格数 ≥ 3 (真实表数百行, 阈值低只影响合成小表)
    anchors = []
    for c in range(ncol):
        n = 0
        for r in range(1, nrow):
            if _norm_date(_cell(grid, r, c)):
                n += 1
        if n >= 3:
            anchors.append(c)
    # 2) 每个锚点找右侧年份列(表头在 r1 或 r2), 年份严格递增; 允许 1 列全空隔断
    # 【关键】跳过"落在已有块年份列区间内"的锚点: 宽矩阵表的数值列里, 像 44197/55820
    #        这种数字会被 _norm_date 当 Excel 序列号识别成日期 → 每个假锚点都会生出一个
    #        垃圾块(标题取到该列自己的年份表头, 数据混入未来日期)。跳过被覆盖区间即可。
    blocks = []
    covered_until = -1
    for a in anchors:
        if a <= covered_until:
            continue
        year_cols = []
        gap = 0
        c = a + 1
        last_year = 0
        while c < ncol and gap <= 1 and len(year_cols) < 12:
            y = _year_from_header(_cell(grid, 0, c)) or _year_from_header(_cell(grid, 1, c))
            if y and y > last_year:
                year_cols.append((y, c))
                last_year = y
                gap = 0
            else:
                gap += 1
            c += 1
        if len(year_cols) < 2:
            continue  # 脚手架列(无年份头)跳过
        # 3) 块标题: 锚点列..首个年份列之间 r1/r2 的第一个非空字符串
        title = ""
        for c2 in range(a, min(year_cols[0][1], a + 3)):
            title = _s(_cell(grid, 0, c2)) or _s(_cell(grid, 1, c2))
            if title:
                break
        blocks.append((a, title or f"序列{a + 1}", year_cols))
        covered_until = year_cols[-1][1]
    # 4) 重建
    out = {}  # title → dedupe dict (同块同名合并, 如气温多个块)
    for a, title, year_cols in blocks:
        dedupe = out.setdefault(title, {})
        for r in range(1, nrow):
            d_anchor = _norm_date(_cell(grid, r, a))
            if d_anchor is None:
                continue
            mo, da = d_anchor.month, d_anchor.day
            for y, c in year_cols:
                v = _num(_cell(grid, r, c))
                if v is None:
                    continue
                try:
                    d = dt.date(y, mo, da)
                except ValueError:
                    continue  # 2月29 平年
                dedupe[d] = round(v, 4)
    series = []
    for title, dedupe in out.items():
        pts = sorted(dedupe.items())
        if pts:
            series.append({"name": title, "unit": "", "points": [[k.isoformat(), v] for k, v in pts]})
    series.sort(key=lambda s: s["name"])
    return {"type": "series", "axis": "date", "series": series}


# ────────────────────────── 解析器: month_matrix(月份×年矩阵) ──────────────────────────
def parse_month_matrix(ws):
    """行=1月..12月, 列=年份(表头行在 '1月' 行的上一行), 单指标多年度 → 月度序列。"""
    grid = _read_grid(ws)
    month_row = None
    for i in range(min(8, len(grid))):
        if _MONTH_CN.match(_s(_cell(grid, i, 0))):
            month_row = i
            break
    if month_row is None or month_row == 0:
        return {"type": "series", "axis": "month", "series": []}
    year_row = month_row - 1
    year_cols = []
    for j in range(1, max((len(r) for r in grid[: year_row + 1]), default=0)):
        y = _year_from_header(_cell(grid, year_row, j))
        if y:
            year_cols.append((y, j))
    # 标题: r1(idx0) 第一个非空, 否则用 A 列 month_row-2 行
    title = ""
    for i in range(0, max(year_row, 1)):
        title = _s(_cell(grid, i, 0)) or _s(_cell(grid, i, 1))
        if title:
            break
    title = title or ws.title
    dedupe = {}
    for i in range(month_row, min(month_row + 12, len(grid))):
        m = _MONTH_CN.match(_s(_cell(grid, i, 0)))
        if not m:
            continue
        mo = int(m.group(1))
        for y, j in year_cols:
            v = _num(_cell(grid, i, j))
            if v is None:
                continue
            dedupe[dt.date(y, mo, 1)] = round(v, 4)
    pts = sorted(dedupe.items())
    series = []
    if pts:
        series.append({"name": title, "unit": "", "points": [[k.isoformat(), v] for k, v in pts]})
    return {"type": "series", "axis": "month", "series": series}


# ────────────────────────── 解析器: entity(实体×周期) ──────────────────────────
def _period_header(v):
    """周期列表头 → (label, date|None)。认不出的返回 (label, None) 走分类轴。"""
    d = _norm_date(v)
    if d:
        return d.isoformat(), d
    s = _s(v)
    if not s:
        return None, None
    m = _YEAR_MONTH_CN.match(s)
    if m:
        return s, dt.date(int(m.group(1)), int(m.group(2)), 1)
    m = _YEAR_CN_SHORT.match(s)
    if m:
        return s, dt.date(2000 + int(m.group(1)), int(m.group(2)), 1)
    m = _CLOUD_CODE.match(s)
    if m:
        yy, mo = int(m.group(1)), int(m.group(2))
        # 仅当形如合约码(2020~2040 年 / 1~12 月)才当日期; 否则按普通文本走分类轴。
        # 修正: 旧实现把任意 4 位数字(如价格 3630)当合约码 → month=36 直接抛异常,
        #       新品种文件里出现数值表头就会让整本转换中断。
        if 20 <= yy <= 40 and 1 <= mo <= 12:
            try:
                return s, dt.date(2000 + yy, mo, 1)
            except ValueError:
                pass
    return s, None  # '3.2-3.8' 之类周标签 / 普通文本 → 分类轴


def parse_entity(ws, header_row=0, meta_cols=1, name_col=0, group_col=None, skip=()):
    """行=实体(炼厂/合约/指标), 列=周期(日期/月份/周标签)。
    meta 列(地区/省份/产能…)随实体保留; group_col 支持纵向合并单元格向下继承。"""
    grid = _read_grid(ws)
    ncol = max((len(r) for r in grid[: header_row + 1]), default=0)
    axis, seen_axis = [], set()
    for j in range(meta_cols, ncol):
        label, d = _period_header(_cell(grid, header_row, j))
        if label is None:
            continue
        if _s(label) in skip:
            continue
        key = label
        if key in seen_axis:
            continue
        seen_axis.add(key)
        axis.append({"label": label, "date": d.isoformat() if d else None, "col": j})
    # 分类轴(无日期)按表头顺序即为时间序, 不排序; 日期轴按日期升序
    if all(a["date"] for a in axis) and axis:
        axis.sort(key=lambda a: a["date"])
    carry_group = ""
    entities = []
    for i in range(header_row + 1, len(grid)):
        if group_col is not None:
            g = _s(_cell(grid, i, group_col))
            if g:
                carry_group = g
        name = _s(_cell(grid, i, name_col))
        if not name:
            continue
        meta = {}
        for j in range(0, meta_cols):
            if j == group_col or j == name_col:
                continue
            v = _cell(grid, i, j)
            if v is None or _s(v) == "":
                continue
            meta[_s(_cell(grid, header_row, j)) or f"col{j}"] = _s(v)[:30]
        values = []
        n_val = 0
        for a in axis:
            f = _num(_cell(grid, i, a["col"]))
            values.append(round(f, 4) if f is not None else None)
            n_val += 1 if f is not None else 0
        if n_val == 0:
            continue
        entities.append({"name": name, "group": carry_group if group_col is not None else "",
                         "meta": meta, "values": values})
    return {"type": "entity", "axis": [{"label": a["label"], "date": a["date"]} for a in axis],
            "entities": entities}


# ────────────────────────── 解析器: table(原样兜底) ──────────────────────────
def parse_table(ws):
    """不规则表原样导出为表格(行列截断保护), 供前端直接渲染。"""
    grid = _read_grid(ws, max_rows=400, max_cols=40)
    # 去掉完全为空的行/列
    grid = [r for r in grid if any(_s(v) or _num(v) is not None for v in r)]
    if not grid:
        return {"type": "table", "headers": [], "rows": []}
    ncol = max(len(r) for r in grid)
    keep = [j for j in range(ncol) if any(_s(_cell(grid, i, j)) or _num(_cell(grid, i, j)) is not None
                                          for i in range(len(grid)))]
    rows = [[(lambda v: (round(v, 4) if isinstance(v, float) else
                         (v.isoformat() if isinstance(v, dt.date) else _s(v)))  \
              if v is not None else "")(_cell(grid, i, j)) for j in keep] for i in range(len(grid))]
    headers = [str(h) for h in rows[0]]
    return {"type": "table", "headers": headers, "rows": rows[1:]}


# ────────────────────────── 汇总 ──────────────────────────
def _date_range(obj):
    dates = []
    if obj.get("type") == "series":
        for s in obj["series"]:
            if s["points"]:
                dates.append(s["points"][0][0])
                dates.append(s["points"][-1][0])
    elif obj.get("type") == "entity":
        ds = [a["date"] for a in obj["axis"] if a["date"]]
        if ds:
            dates.extend([min(ds), max(ds)])
    return (min(dates), max(dates)) if dates else (None, None)


# ────────────────────────── 品种层: 推断/清单/版式识别 ──────────────────────────
def _now_iso():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def infer_variety(src_path):
    """从文件名推断品种 → (slug, 中文名)。'沥青数据库-永安.xlsx' → ('bitumen', '沥青')。

    优先匹配 VARIETY_ALIASES 中的品种词(长词优先, 避免「燃料油」被「油」类误配);
    未收录品种: 中文名取文件名首个片段(去掉"数据库/数据/日报/周报"后缀), slug 用短哈希。
    """
    stem = re.sub(r"^source[_-]", "", Path(src_path).stem)
    parts = [p for p in re.split(r"[-—－_（）()\s]+", stem) if p]
    for zh, slug in _ALIAS_LONG_FIRST:
        if any(zh.lower() in p.lower() for p in parts):
            return slug, zh
    core = re.sub(r"(数据库|数据|日报|周报)$", "", parts[0]) if parts else stem
    core = core or stem
    return "v" + hashlib.sha1(core.encode("utf-8")).hexdigest()[:8], core


def read_meta(variety_dir):
    """读品种元信息 data/variety/<slug>/meta.json(缺失/损坏返回 {})。"""
    p = Path(variety_dir) / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def list_varieties(root=None):
    """品种清单: 优先读 varieties.json, 缺失时按子目录(含 index.json)重建。"""
    root = Path(root) if root else DEFAULT_ROOT
    p = root / "varieties.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    out = []
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "index.json").exists():
                m = read_meta(d)
                out.append({"slug": d.name, "name": m.get("name", d.name),
                            "source": m.get("source"), "imported_at": m.get("imported_at"),
                            "n_sheets": m.get("n_sheets"), "n_series": m.get("n_series")})
    return out


def _write_registry(variety_dir, meta, root=None):
    """刷新品种清单。仅当品种目录位于 root 之下才写(测试用 tmp 目录时自动跳过)。"""
    root = Path(root) if root else DEFAULT_ROOT
    variety_dir = Path(variety_dir)
    try:
        if variety_dir.resolve().parent != root.resolve():
            return
    except OSError:
        return
    items = [v for v in list_varieties(root) if v.get("slug") != meta["slug"]]
    items.append({k: meta.get(k) for k in
                  ("slug", "name", "source", "imported_at", "n_sheets", "n_series",
                   "groups", "symbols")})
    items.sort(key=lambda v: v.get("slug", ""))
    (root / "varieties.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _period_like(v):
    """表头像『周期』吗: 日期 / 年月 / 合约码(2301) / 周标签(3.2-3.8)。"""
    if _norm_date(v):
        return True
    t = _s(v)
    if not t:
        return False
    if _YEAR_MONTH_CN.match(t) or _YEAR_CN_SHORT.match(t) or _WEEK_LABEL.match(t):
        return True
    m = _CLOUD_CODE.match(t)
    return bool(m and 20 <= int(m.group(1)) <= 40 and 1 <= int(m.group(2)) <= 12)


def _detect_entity(grid, nrow, ncol):
    """实体×周期识别: 某表头行有 ≥2 个周期列, 且其左侧是实体名列。

    meta_cols = 首个周期列下标; name_col = meta 区里非空文本种类最多的列
    (近似「实体名」列)。返回 (parser, params) 或 None。
    """
    for r in range(min(12, nrow)):
        ph = [c for c in range(ncol) if _period_like(_cell(grid, r, c))]
        if len(ph) < 2 or ph[0] == 0:
            continue
        meta_cols = ph[0]
        best, best_n = None, 0
        for c in range(meta_cols):
            vals = {_s(_cell(grid, rr, c)) for rr in range(r + 1, nrow) if _s(_cell(grid, rr, c))}
            if vals and len(vals) >= best_n:   # 并列取更靠右的列(名称列通常在 meta 区右侧)
                best, best_n = c, len(vals)
        if best is None:
            continue
        return "entity", {"header_row": r, "meta_cols": meta_cols, "name_col": best}
    return None


def detect_parser(ws):
    """未配置工作表的版式自动识别 → (parser, params)。

    判定顺序(只看表头区前 80 行):
      1. 表头出现「指标名称/单位/频率」等块表头 → gldt(钢联/Wind 纵向导出);
      2. 表头区某行有 ≥2 个周期列且左侧是实体名列 → entity(实体×周期);
      3. 前 3 行出现 ≥2 个年份列 → yearstack(年度堆叠对比);
      4. 有 ≥2 个日期锚点列 → simple_blocks(多块「日期+值」并排, 如升贴水);
      5. A 列有 ≥3 个日期 → simple(简单纵向); A 列是年份 → 年度纵向;
      6. 其余 → table 表格兜底。
    """
    grid = _read_grid(ws, max_rows=80, max_cols=80)
    nrow, ncol = len(grid), max((len(r) for r in grid), default=0)
    if not grid or ncol == 0:
        return "table", {}
    # ① gldt: 表头块关键字可能在首行, 也可能在第 2~8 行(上方有标题行), 故扫一遍左上角
    _GLDT_KEYS = {"指标名称", "单位", "频率", "频度", "数据来源", "指标编码"}
    for r in range(min(8, nrow)):
        for c in range(min(6, ncol)):
            if _s(_cell(grid, r, c)) in _GLDT_KEYS:
                return "gldt", {}
    # ② month_matrix: 某行出现 ≥6 个月份表头(1月..12月)
    for r in range(min(4, nrow)):
        nmonth = sum(1 for c in range(ncol) if _MONTH_CN.match(_s(_cell(grid, r, c)) or ""))
        if nmonth >= 6:
            return "month_matrix", {}
    ent = _detect_entity(grid, nrow, ncol)
    if ent:
        return ent
    # ③ month_matrix: A 列是月份(1月..12月)纵向排列 + 表头行有年份列
    if sum(1 for r in range(nrow) if _MONTH_CN.match(_s(_cell(grid, r, 0)) or "")) >= 6:
        return "month_matrix", {}
    years = 0
    for r in range(min(3, nrow)):
        years = max(years, sum(1 for c in range(ncol) if _year_from_header(_cell(grid, r, c))))
    if years >= 2:
        return "yearstack", {}
    anchors = [c for c in range(min(ncol, 40))
               if sum(1 for r in range(1, nrow) if _norm_date(_cell(grid, r, c))) >= 3]
    if len(anchors) >= 2:
        blocks = [{"date_col": c, "val_col": c + 1,
                   "name": _s(_cell(grid, 0, c + 1)) or f"序列{k + 1}"}
                  for k, c in enumerate(anchors)]
        return "simple_blocks", {"blocks": blocks}
    if anchors:
        return "simple", {"header_row": 0}
    yr_rows = sum(1 for r in range(1, nrow) if _year_from_header(_cell(grid, r, 0)))
    if yr_rows >= 3:
        return "simple", {"header_row": 0, "axis": "year"}
    return "table", {}


def convert(src_path, out_dir=None, slug=None, name=None, symbols=None):
    """把一本品种数据库 xlsx 转成逐表 JSON, 返回模块索引 list。

    src_path — 源 xlsx; out_dir — 输出目录(默认 data/variety/<slug>);
    slug/name — 品种标识与中文名(缺省从文件名推断);
    symbols — 期货代码列表(缺省按 VARIETY_SYMBOLS 表推断)。
    """
    src_path = Path(src_path)
    if not slug:
        slug, inferred = infer_variety(src_path)
        name = name or inferred
    name = name or VARIETY_NAMES.get(slug, slug)
    out_dir = Path(out_dir) if out_dir else (DEFAULT_ROOT / slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.load_workbook(str(src_path), read_only=True, data_only=True)
    index = []
    for n, ws in enumerate(wb.worksheets, start=1):
        sheet_name = ws.title
        if sheet_name in SHEET_CONFIG:
            parser, params, group = SHEET_CONFIG[sheet_name]
            recognized_by = "config"
        else:
            parser, params = detect_parser(ws)
            group = "其他"
            recognized_by = "auto"
            print(f"  [warn] 未配置版式 {sheet_name!r}: 自动识别为 {parser}"
                  + ("(表格兜底)" if parser == "table" else ""))
        if parser == "gldt":
            obj = parse_gldt(ws)
        elif parser == "simple":
            obj = parse_simple(ws, **params)
        elif parser == "simple_blocks":
            obj = parse_simple_blocks(ws, **params)
        elif parser == "yearstack":
            obj = parse_yearstack(ws)
        elif parser == "month_matrix":
            obj = parse_month_matrix(ws)
        elif parser == "entity":
            obj = parse_entity(ws, **params)
        else:
            obj = parse_table(ws)
        sheet_slug = f"s{n:02d}"
        (out_dir / f"{sheet_slug}.json").write_text(
            json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        dmin, dmax = _date_range(obj)
        if obj["type"] == "series":
            n_series = len(obj["series"])
            units = sorted({s["unit"] for s in obj["series"] if s.get("unit")})[:4]
        elif obj["type"] == "entity":
            n_series = len(obj["entities"])
            units = []
        else:
            n_series = 0
            units = []
        index.append({"slug": sheet_slug, "name": sheet_name, "type": obj["type"],
                      "group": group, "n_series": n_series, "date_min": dmin,
                      "date_max": dmax, "units": units, "recognized_by": recognized_by})
        print(f"  {sheet_slug} [{sheet_name}] type={obj['type']} series={n_series} "
              f"range={dmin}..{dmax} ({recognized_by})")
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    prev = read_meta(out_dir)
    meta = {"slug": slug, "name": name, "source": src_path.name,
            "symbols": [c.upper() for c in (symbols or prev.get("symbols")
                                             or symbols_for(slug))],
            "imported_at": _now_iso(),
            "first_imported_at": prev.get("first_imported_at") or _now_iso(),
            "n_sheets": len(index),
            "n_series": sum(i["n_series"] for i in index),
            "n_auto": sum(1 for i in index if i.get("recognized_by") == "auto"),
            "groups": sorted({i["group"] for i in index})}
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_registry(out_dir, meta)
    print(f"done: {name}({slug}) {len(index)} sheets → {out_dir}")
    return index


def main(default_variety=None):
    ap = argparse.ArgumentParser(description="品种数据库 xlsx → JSON (通用导入器)")
    ap.add_argument("--src", default=None, help="源 xlsx 路径(新收到的品种文件)")
    ap.add_argument("--name", default=None, help="品种中文名, 如 甲醇(缺省从文件名推断)")
    ap.add_argument("--slug", default=None, help="品种目录标识, 如 methanol(缺省自动)")
    ap.add_argument("--variety", default=default_variety,
                    help="重跑已导入品种(目录标识); 缺省重跑全部品种")
    ap.add_argument("--symbols", default=None,
                    help="期货代码, 逗号分隔, 如 BU(缺省按品种名推断)")
    ap.add_argument("--root", default=None, help="数据根目录(默认 data/variety)")
    ap.add_argument("--list", action="store_true", help="列出已导入品种")
    args = ap.parse_args()
    root = Path(args.root) if args.root else DEFAULT_ROOT

    if args.list:
        items = list_varieties(root)
        if not items:
            print(f"(空) {root} 下暂无品种")
        for v in items:
            syms = ",".join(v.get("symbols") or []) or "-"
            print(f"  {v.get('slug', ''):12s} {v.get('name', ''):6s} {syms:6s} "
                  f"{v.get('n_sheets') or 0} 表 / {v.get('n_series') or 0} 序列  "
                  f"{v.get('imported_at') or ''}")
        return

    if args.src:
        src = Path(args.src)
        if not src.exists():
            print(f"源文件不存在: {src}")
            sys.exit(1)
        slug, inferred = (args.slug, args.name)
        if not slug:
            auto_slug, auto_name = infer_variety(src)
            slug = slug or auto_slug
            inferred = inferred or auto_name
        out_dir = root / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        snap = out_dir / f"source_{src.name}"
        if src.resolve() != snap.resolve():
            snap.write_bytes(src.read_bytes())
            print(f"snapshot: {src} → {snap}")
        print(f"variety: {inferred or slug} ({slug})")
        syms = [c.strip().upper() for c in (args.symbols or "").split(",") if c.strip()]
        convert(snap, out_dir=out_dir, slug=slug, name=inferred, symbols=syms or None)
        return

    targets = [args.variety] if args.variety else [v["slug"] for v in list_varieties(root)]
    if not targets:
        print("没有已导入品种, 也未指定 --src。用法: python variety_convert.py "
              "--src <品种数据库.xlsx> [--name 品种名]")
        sys.exit(1)
    for vs in targets:
        d = root / vs
        cands = sorted(d.glob("source_*.xlsx"))
        if not cands:
            print(f"[warn] {vs}: 目录内没有 source_*.xlsx, 跳过")
            continue
        m = read_meta(d)
        print(f"variety: {m.get('name', vs)} ({vs})  源: {cands[-1].name}")
        convert(cands[-1], out_dir=d, slug=vs, name=m.get("name"), symbols=m.get("symbols"))


if __name__ == "__main__":
    main()
