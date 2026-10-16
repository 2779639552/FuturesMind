# -*- coding: utf-8 -*-
"""后端补丁：⑨ 基本面截面表 —— 行加「粗板块」+ 接口下发「池清单」。

【改动 2 处，都很小】
  1. `_fund_xsection_one` 的行里加 `sector_bucket` —— **复用 `quant_module._sector_bucket`**,
     与技术面总览的板块筛选**同一归并函数**。不这么做的话前端要么自己复制一份映射表
     (两处口径迟早漂移), 要么按「能化(化工)」这类**细分子板块**筛 ⇒ 与技术面的
     「能化」不是同一个筛选粒度, 用户会奇怪"为什么两页的板块选项不一样"。
  2. `/api/fund/ranking` 返回顶层 `pools`(与技术面 `d.pools` 同形) —— 供前端做板块胶囊。
     ⚠ 按 **codes** 派生而不是按 rows: 若只按"已拿到的行"派生, 渐进阶段或部分源失败时
     下拉里的板块会**缺项**(筛着筛着选项变少)。这条纪律来自技术面 (见 quant_module
     api_quant_overview 同一处注释)。

【接口只加字段、不改字段】—— 既有护栏与旧消费端零影响。

【不幂等】重跑会重复插入 ⇒ 开头检测标记并拒绝。要从 backup 还原后再跑。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FM = ROOT / "fund_module.py"

MARK = "sector_bucket"
MARK2 = '"pools": ["全部品种"] + pools,'


def _eol_of(raw: bytes) -> str:
    return "\r\n" if raw.count(b"\r\n") else "\n"


def patch(path: Path, edits, write: bool) -> bool:
    raw = path.read_bytes()
    eol = _eol_of(raw)
    txt = raw.decode("utf-8")
    ok = True
    new = txt
    for old, rep, n, tag in edits:
        o = old.replace("\n", eol)
        r = rep.replace("\n", eol)
        got = new.count(o)
        flag = "OK " if got == n else "★  "
        print("  %s %-46s 命中 %d(期望 %d)" % (flag, tag, got, n))
        if got != n:
            ok = False
            continue
        new = new.replace(o, r, 1)
    if not ok:
        print("  ⇒ 有锚点未命中, **未写盘**")
        return False
    if not write:
        print("  ⇒ 空跑通过(未写盘)。加 --write 落盘。")
        return True
    path.write_bytes(new.encode("utf-8"))
    return True


E1_OLD = '''    row: dict[str, Any] = {"variety": code, "name": meta.get("name", code),
                           "sector": meta.get("sector_cn") or meta.get("sector") or ""}'''
E1_NEW = '''    _sector_cn = meta.get("sector_cn") or meta.get("sector") or ""
    row: dict[str, Any] = {"variety": code, "name": meta.get("name", code),
                           "sector": _sector_cn,
                           # 【2026-10-16 新增】粗板块 —— **复用技术面同一个归并函数**
                           #   (`quant_module._sector_bucket`: 精确表 + 前缀兜底)。
                           #   本表的 `sector` 是细分子板块(「能化(化工)」), 直接拿它做筛选会
                           #   得到与技术面**不同粒度**的选项(那边是「能化」) ⇒ 两页筛选不一致。
                           #   这里下发归一后的 `sector_bucket`, 前端只按它筛。
                           "sector_bucket": q._sector_bucket(_sector_cn)}'''

E2_OLD = '''    rows = _fund_rank_all(codes, force=refresh)'''
E2_NEW = '''    rows = _fund_rank_all(codes, force=refresh)
    # 板块清单按**池内全部品种**派生(扩池自动跟随, 不硬编码) —— 与技术面 `pools` 同做法。
    # ⚠ 用 codes 而非 rows: 渐进阶段或个别源失败时 rows 可能不全, 用 rows 会让
    #   可选的板块**越筛越少**(技术面 api_quant_overview 同一处已有此纪律)。
    #   顺序用 sorted 固定 —— 不随请求顺序抖动, 否则胶囊位置会跳。
    _pools = sorted({_q()._sector_bucket(_meta(c).get("sector_cn") or "") for c in codes})'''

E3_OLD = '''    return jsonify(_q()._json_safe({"ok": True, "metric": metric, "order": order,
                               "count": len(rows), "cached": not refresh, "rows": rows}))'''
E3_NEW = '''    return jsonify(_q()._json_safe({"ok": True, "metric": metric, "order": order,
                               "count": len(rows), "cached": not refresh,
                               "pools": ["全部品种"] + _pools, "rows": rows}))'''


def main(write: bool) -> int:
    raw = FM.read_bytes()
    txt = raw.decode("utf-8")
    if MARK in txt and MARK2 in txt:
        print("★ 改动已应用过 —— 本脚本不幂等, 勿重跑。要从 backup 还原后再跑。")
        return 2
    print("=== 后端补丁(空跑=%s) ===" % (not write))
    ok = patch(FM, [
        (E1_OLD, E1_NEW, 1, "B1 行加 sector_bucket(复用技术面归并)"),
        (E2_OLD, E2_NEW, 1, "B2 ranking 派生 pools"),
        (E3_OLD, E3_NEW, 1, "B3 响应下发 pools"),
    ], write)
    if ok and write:
        b = FM.read_bytes()
        print("  落盘后 md5=%s, eol=%s" % (hashlib.md5(b).hexdigest()[:12],
                                        "CRLF" if b.count(b"\r\n") else "LF"))
        assert b.count(b"\r\n") == 0, "fund_module 应保持 LF"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
