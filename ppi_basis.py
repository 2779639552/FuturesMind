# -*- coding: utf-8 -*-
"""ppi_basis.py — 生意社(100ppi)基差源: 为无国君现货指数品种提供现货价/基差序列。

【为什么需要】国君 GTJA 的 basisData.query 只覆盖部分品种(本池 15/21),
  SC 原油 / LU 低硫燃料油 / CJ 红枣 / BZ 纯苯 / FU 燃料油 / PX 对二甲苯
  六个品种返回 0 行 ⇒ 估值+驱动矩阵里少 6 个点。本模块用生意社公开数据补覆盖。

【数据源】生意社 100ppi.com「大宗商品现货与期货对比」
  页面: https://www.100ppi.com/sf/day-YYYY-MM-DD.html
  字段: 现货价格 / 主力合约 / 主力合约价格 / 主力基差 / 主力基差率
  ★ 字段名与国君完全一致(spot_price / dominant_contract_price / dom_basis /
    dom_basis_rate), 因此可直接替换 _fetch_basis 的取数实现, 前端零改动。

【★ 符号口径 —— 必须取负号, 否则估值轴整体反向(2026-09-30 实测钉死)】
  两源**符号相反**, 已用 11 个重叠品种、同一交易日逐笔交叉验证(probe/cross_check_basis_sign.py):
    国君 GTJA : dom_basis = **现货 − 期货**   (CF 17252−15780 = +1472)
    生意社    : dom_basis = **期货 − 现货**   (CF 15780−17541 = −1761, 同品种同日)
  11/11 品种(CF/M/RU/BU/EB/LH/PG/BR/SI/PS/LC)均为「国君=现货-期货、生意社=期货-现货」。
  ⇒ 本模块**一律对生意社的 basis 与 basis_rate 取负**, 转成与国君/本系统一致的口径:
        basis > 0 = 现货升水 = 期货贴水(顺基差做多方向)
  ⚠️ 这是静默错误: 不取负号接口照样全 200、数字看着正常, 但结论完全反了。
     任何改动本模块符号的提交, 必须先跑 probe/cross_check_basis_sign.py 复验。

【缓存策略】100ppi 只能**逐日**取(一天一个 HTML), 180 天串行约 92s。
  故: ① 内存 TTL 缓存; ② 本地 JSON 落盘缓存(按品种 + 日期增量), 次日只补新日期。
  落盘数据带 `_v` 版本号; 口径变更时递增即可让旧缓存自动失效(避免用到取负前的脏数据)。
  落盘目录复用项目既有 data/ 结构, 不新增顶层目录。

【品种名映射】100ppi 用中文名, 且个别品种命名与交易所简称不同(PX → "PX",
  燃料油 → "燃料油", 纯苯 → "纯苯")。映射表见 _PPI_NAME。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── 100ppi 覆盖的品种(中文名 → 本池代码); 实测 2026-09-30 ────────────────────
# 两条登记理由:
#   ① 国君缺失 + 100ppi 有覆盖 → 直接供兜底(PX/FU/BZ 等);
#   ② 国君**当前正常** + 100ppi 亦有覆盖 → 冗余登记, 不改主路径, 只兜"国君失效"。
# 其余品种仍走国君(不改变既有口径)。
_PPI_NAME: dict[str, str] = {
    "PX": "PX",          # 对二甲苯(100ppi 用英文代码)
    "FU": "燃料油",
    "BZ": "纯苯",
    "TA": "PTA",
    "EG": "乙二醇",
    "PG": "液化石油气",
    "M": "豆粕",
    "CF": "棉花",
    "LC": "碳酸锂",
    "RU": "天然橡胶",
    "LH": "生猪",
    "BU": "石油沥青",
    "EB": "苯乙烯",
    "SI": "工业硅",
    "PS": "多晶硅",
    "BR": "丁二烯橡胶",
    # ★ 冗余登记(不走兜底也保留): 该品种国君**当前正常**, 但 100ppi 亦有覆盖。
    #   登记不改变主路径(源 1 命中即不启用源 2), 只是把"国君哪天失效"这一情形
    #   兜住 —— 否则会误报"两源皆无", 把可修故障写成数据源边界。
    "P": "棕榈油",
}

# 【变量】100ppi 未覆盖(全池中仍无现货指数的品种) —— 供前端如实标注
# 注: 2026-09-30 逐类穷举 100ppi 页面形态 + 99qh + 金投 + 东财后确认无解;
#     99qh 虽在品种表列出这三个, 但其历史序列接口返回 401 需授权, 不可用。
NO_SPOT_VARIETIES = ["SC", "LU", "CJ"]

# 【变量】"兜底源在实际承担主责"的品种(即国君确实缺覆盖)。
# ★ 与 _PPI_NAME 的区别: _PPI_NAME 是"**有这个能力**"(含为 P 预留的冗余登记),
#   本集合是"**现在真的在用**"。前端来源标注必须用本集合, 否则会把 P 标成
#   "国君为主+生意社兜底"—— 而 P 实际走国君, 属标注失真。
# 维持方式: 国君若补齐这些品种, 从本集合移除即可(兜底能力保留在 _PPI_NAME)。
FALLBACK_ACTIVE = ["PX", "FU", "BZ"]

_CACHE: dict[str, tuple[float, Any]] = {}  # 【变量】内存 TTL 缓存
_CACHE_LOCK = threading.Lock()  # 【变量】内存缓存锁
_FILE_LOCK = threading.Lock()  # 【变量】落盘文件锁
_TTL = 3600  # 【变量】内存缓存 1h(日频数据)
_PARALLEL = 8  # 【变量】逐日抓取并发数(100ppi 单日页, 实测 8 并发稳定)
# 【变量】落盘缓存口径版本 —— ★ 符号取负修正后必须递增, 否则会继续用旧的(符号相反的)脏缓存。
_CACHE_VER = 2


def _cache_dir() -> str:
    """落盘缓存目录(与项目既有 data/ 同级结构, 失败则退回系统临时目录)。"""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ppi")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        import tempfile

        base = os.path.join(tempfile.gettempdir(), "agentsense_ppi")
        os.makedirs(base, exist_ok=True)
    return base


def _cache_path(code: str) -> str:
    return os.path.join(_cache_dir(), f"basis_{code}.json")


def _load_disk(code: str) -> dict[str, dict]:
    """读该品种的落盘序列 {日期YYYYMMDD: {spot,fut,basis,basis_rate}}。

    【版本校验】文件内 `_v` 与 _CACHE_VER 不一致(口径变更)时整体丢弃重抓,
    避免继续用符号取负修正前的旧缓存。
    """
    p = _cache_path(code)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:  # 损坏文件不应让接口失败, 丢弃重抓
        logger.warning("ppi 缓存读取失败(%s): %s", code, e)
        return {}
    if not isinstance(d, dict):
        return {}
    if d.get("_v") != _CACHE_VER:
        logger.info("ppi 缓存口径版本过期(%s): %s != %s, 重抓", code, d.get("_v"), _CACHE_VER)
        return {}
    days = d.get("days")
    return days if isinstance(days, dict) else {}


def _save_disk(code: str, data: dict[str, dict]) -> None:
    """原子写落盘序列(临时文件 + replace, 避免半写损坏)。"""
    p = _cache_path(code)
    tmp = p + ".tmp"
    try:
        with _FILE_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"_v": _CACHE_VER, "days": data}, f, ensure_ascii=False)
            os.replace(tmp, p)
    except OSError as e:
        logger.warning("ppi 缓存写入失败(%s): %s", code, e)


def supports(code: str) -> bool:
    """该品种是否有 100ppi 现货覆盖。"""
    return code in _PPI_NAME


def fetch_days(code: str, days: int = 180) -> dict[str, dict]:
    """取该品种近 N 天的现货/基差日序列(落盘增量缓存 + 并发补缺)。

    【返回】{YYYYMMDD: {spot, fut, basis, basis_rate}}; 无覆盖品种返回 {}。
    【增量】本地已有该日期则不再请求; 只抓缺失的日期, 因此次日只需补 1 天。
    """
    name = _PPI_NAME.get(code)
    if not name:
        return {}
    key = f"ppi:{code}:{days}"
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]

    disk = _load_disk(code)
    end = date.today()
    start = end - timedelta(days=days)
    # 只补缺失日期(周末/节假日页面存在但无该品种行, 也会被记录为"已尝试")
    todo = []
    d = start
    while d <= end:
        k = d.strftime("%Y%m%d")
        if k not in disk:
            todo.append(k)
        d += timedelta(days=1)

    if todo:
        got = _fetch_range(code, name, todo)
        disk.update(got)
        _save_disk(code, disk)

    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), disk)
    return disk


def _fetch_range(code: str, name: str, ymd_list: list[str]) -> dict[str, dict]:
    """并发抓取指定日期集合(单日页 → 命中该品种则取一行)。失败/非交易日记空字典。"""
    try:
        from akshare.futures.futures_basis import futures_spot_price
    except ImportError:  # 兼容 akshare 旧版路径
        from akshare import futures_spot_price as _f  # type: ignore

        futures_spot_price = _f

    out: dict[str, dict] = {}

    def one(ymd: str) -> tuple[str, dict]:
        try:
            df = futures_spot_price(ymd, [code])
        except Exception as e:  # noqa: BLE001 - 单日失败不应中断整批
            logger.debug("ppi 单日失败(%s %s): %s", code, ymd, e)
            return ymd, {}
        if df is None or getattr(df, "empty", True):
            return ymd, {}  # 非交易日或无该品种 → 记空, 避免反复重试
        try:
            r = df.iloc[0]
            # 【★ 取负号】生意社 dom_basis 是"期货 − 现货", 与国君/本系统的"现货 − 期货"
            # 方向相反(11 品种交叉验证, 见文件头)。此处统一取负, 让两源口径一致。
            b_raw = _num(r.get("dom_basis"))
            br_raw = _num(r.get("dom_basis_rate"))
            return ymd, {
                "spot": _num(r.get("spot_price")),
                "fut": _num(r.get("dominant_contract_price")),
                "basis": (None if b_raw is None else -b_raw),
                "basis_rate": (None if br_raw is None else -br_raw),
                "contract": str(r.get("dominant_contract") or ""),
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("ppi 解析失败(%s %s): %s", code, ymd, e)
            return ymd, {}

    with ThreadPoolExecutor(max_workers=_PARALLEL) as ex:
        for ymd, rec in ex.map(one, ymd_list):
            if rec:
                out[ymd] = rec
            else:
                out[ymd] = {}  # 显式占位 → 下次不再重抓
    return out


def _num(x) -> float | None:
    """安全转 float(NaN/None/不可转 → None)。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def basis_rows(code: str, days: int = 180) -> list[dict]:
    """转成与 quant_module._fetch_basis 完全同构的 rows(前端/接口零改动)。

    【关键】字段名与国君行保持一致(含 date/spot_price/futures_price/basis/
    basis_rate/contract), 这样 fund_module 里 `_fetch_basis` 的调用方无需分支。
    """
    data = fetch_days(code, days)
    rows = []
    for k in sorted(data.keys()):
        rec = data[k]
        if not rec:
            continue
        if k and len(k) == 8:
            iso = f"{k[:4]}-{k[4:6]}-{k[6:]}"
        else:
            iso = k
        rows.append({
            "date": iso,
            "spot_price": rec.get("spot"),
            "futures_price": rec.get("fut"),
            "basis": rec.get("basis"),
            "basis_rate": rec.get("basis_rate"),
            "contract": rec.get("contract", ""),
        })
    return rows


def health(code: str) -> dict:
    """诊断用: 该品种本地缓存的日期数与最新日期(供验收脚本核对)。"""
    disk = _load_disk(code)
    valid = {k: v for k, v in disk.items() if v}
    last = max(valid.keys()) if valid else ""
    return {"code": code, "supported": supports(code), "cached_days": len(disk),
            "valid_days": len(valid), "latest": last,
            "latest_spot": (valid[last].get("spot") if last else None)}
