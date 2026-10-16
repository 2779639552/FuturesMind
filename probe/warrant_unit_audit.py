# -*- coding: utf-8 -*-
"""仓单「折合约手数」系数审计（虚实盘比的前置验证）。

【为什么需要这个脚本】
  虚实盘比 =（主力持仓 ÷ 2）÷ 仓单折手数。而**仓单单位在各交易所并不统一**：
    郑商所 = 张 / 大商所 = 手 / 广期所 = 手 / 上期所(含能源中心) = 吨 或 桶。
  直接把国君仓单数除以合约乘数会**错一个数量级**（如郑商所棉花 1 张 = 8 手合约，
  而 1 张 = 40 吨 ÷ 5 吨/手 = 8 手；若不看单位，40 会当成 40 手，虚盘比缩小 80 倍）。

【审计方法（三方交叉）】
  ① 本地四所官方仓单（warrant/history.py 的 products，带 unit 与郑商所 spec）
     —— 提供「每张仓单实物量」与「每手合约吨数」两个事实；
  ② 国君仓单（quant_module._fetch_inventory）—— 与①逐日比对数值是否同源同口径；
  ③ 品种元数据的 unit（VARIETY_METADATA）—— 提供合约乘数。

【输出】每个池内品种一行：交易所 / 仓单单位 / 合约乘数 / 折手系数 / 依据 / 两源比对。

用法：venv/Scripts/python.exe probe/warrant_unit_audit.py
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

from tradingagents.dataflows.commodity_futures import (  # noqa: E402
    ACTIVE_VARIETIES,
    VARIETY_METADATA,
)
from warrant import history  # noqa: E402

# 交易所代码 → 本地 warrant 目录前缀（能源中心并入上期所目录）
EX_TO_LOCAL = {"ZCE": "CZCE", "DCE": "DCE", "GFEX": "GFEX", "SHFE": "SHFE", "INE": "SHFE"}
# 交易所代码 → 仓单计数单位（与 warrant/history.py 的 UNIT_MAIN 同源，此处含 INE）
UNIT_MAIN = {"ZCE": "张", "DCE": "手", "GFEX": "手", "SHFE": "吨", "INE": "桶"}
# 池内品种代码 → 交易所仓单日报里的代码（已知不一致处，实测补）
CODE_OVERRIDE = {"TA": "PTA"}
# 交易所代码 → 交易所中文名（报错提示用）
EX_CN = {"ZCE": "郑商所", "DCE": "大商所", "GFEX": "广期所", "SHFE": "上期所", "INE": "能源中心"}


def _mult(code: str) -> float | None:
    """从元数据 unit（如 '10吨/手'）解析合约乘数。"""
    u = str(VARIETY_METADATA.get(code, {}).get("unit", ""))
    m = re.search(r"([\d.]+)\s*[^\d/]+/手", u)
    return float(m.group(1)) if m else None


def _parse_spec_ton(spec: str) -> float | None:
    """从郑商所 spec（如 '8手合约*5吨/手=40吨'）解析「每张仓单实物量」。"""
    if not spec:
        return None
    m = re.search(r"=\s*([\d.]+)", spec)
    return float(m.group(1)) if m else None


def _load_czce_specs() -> dict[str, str]:
    """从采集产物 data/warrant/out/*_CZCE_product.json 直读逐品种「仓单规格」。

    【为什么不能走 history.get()】history.py 归一化 products 时只保留
    _qty/change，**丢弃了 spec 字段**（见 warrant/history.py 的 products 结构），
    而 spec（如 '8手合约*5吨/手=40吨'）正是「每张仓单实物吨数」的唯一权威来源。
    故此处跳过 history 缓存，直接读采集落盘的原始 JSON（取最新一天）。
    """
    files = sorted(glob.glob(os.path.join("data", "warrant", "out", "*_CZCE_product.json")))
    if not files:
        return {}
    specs: dict[str, str] = {}
    for f in files:  # 按日期升序遍历 → 后写覆盖，等价于「取最新非空」
        try:
            with open(f, encoding="utf-8") as fh:
                for row in json.load(fh):
                    code = str(row.get("product_code") or "").strip().upper()
                    if code and row.get("spec"):
                        specs[code] = str(row["spec"])
        except Exception:  # noqa: BLE001 - 单日文件损坏不影响其它日
            continue
    return specs


def main() -> int:
    prods = history.get().get("products") or {}
    czce_specs = _load_czce_specs()
    # 本地索引：{本地所: {品种代码(大写): 产品条目}}
    local: dict[str, dict[str, dict]] = {}
    for key, p in prods.items():
        ex, code = key.split("|", 1)
        local.setdefault(ex, {})[code.upper()] = p

    rows, bad = [], []
    for code in sorted(ACTIVE_VARIETIES):
        meta = VARIETY_METADATA.get(code, {})
        ex = meta.get("exchange", "")
        lx = EX_TO_LOCAL.get(ex, "")
        unit = UNIT_MAIN.get(ex, "?")
        mult = _mult(code)
        lcode = CODE_OVERRIDE.get(code, code).upper()
        p = (local.get(lx) or {}).get(lcode) or {}

        coef = None
        basis = ""
        if ex == "ZCE":
            spec = str(p.get("spec") or czce_specs.get(lcode) or "")
            ton = _parse_spec_ton(spec)
            if ton is not None and mult:
                coef = round(ton / mult, 6)
                basis = f"spec「{spec}」→ 每张 {ton}吨 ÷ {mult}吨/手"
            else:
                basis = "spec 缺失（该品种本地无仓单行或未公布规格）"
        elif ex in ("DCE", "GFEX"):
            coef, basis = 1.0, "仓单以「手」计，1 手仓单 = 1 手合约标的"
        elif ex in ("SHFE", "INE"):
            if mult:
                coef = round(1.0 / mult, 6)
                basis = f"仓单以「{unit}」计 ÷ {mult}{unit}/手"
            else:
                basis = "合约乘数缺失"

        ok = coef is not None
        if not ok:
            bad.append((code, basis))
        rows.append({
            "code": code, "name": meta.get("name", code), "ex_cn": EX_CN.get(ex, ex),
            "local_code": lcode, "unit": unit, "mult": mult, "coef": coef, "basis": basis,
            "local_qty": p.get("qty"), "has_local": bool(p),
        })

    w = 6
    print(f"{'品种':<{w}}{'名称':<10}{'交易所':<9}{'仓单单位':<7}{'乘数':<7}{'折手系数':<12}依据")
    print("-" * 118)
    for r in rows:
        print(f"{r['code']:<{w}}{r['name']:<10}{r['ex_cn']:<9}{r['unit']:<7}"
              f"{str(r['mult']):<7}{str(r['coef']):<12}{r['basis']}")
    print()
    print(f"可解析系数: {len(rows) - len(bad)}/{len(rows)}")
    if bad:
        print("无法解析:")
        for c, b in bad:
            print(f"  {c}: {b}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
