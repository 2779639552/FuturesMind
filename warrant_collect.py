# -*- coding: utf-8 -*-
"""仓单数据采集 —— 统一入口（供定时任务调用）

用法:
    python warrant_collect.py                     # 采最近交易日
    python warrant_collect.py --date 20260911     # 指定交易日
    python warrant_collect.py --only CZCE,SHFE    # 只采部分交易所
    python warrant_collect.py --skip DCE          # 跳过某交易所
    python warrant_collect.py --force             # 忽略当日缓存强制重下
    python warrant_collect.py --from-cache 20260911  # 不联网，只重建统一表与站点 JSON
    python warrant_collect.py --no-site           # 不生成站点 JSON

产出:
    data/warrant/raw/<EXCH>/...              原始文件（xlsx / json / html）
    data/warrant/out/<date>_<EXCH>_*.json    解析结果
    data/warrant/out/warrant_<date>_*.csv    四所统一表（仓库级 / 品种级 / 品牌级）
    data/warrant/web/overview.json           站点「仓单分析」总览
    data/warrant/web/detail.json             站点「仓单分析」品种细则
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import traceback

import urllib3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warrant import czce, dce, gfex, shfe                   # noqa: E402
from warrant import site as site_builder                    # noqa: E402
from warrant import unify                                   # noqa: E402
from warrant.common import LASTGOOD_DIR, OUT_DIR, ensure_dir, log         # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

COLLECTORS = {"CZCE": czce, "DCE": dce, "GFEX": gfex, "SHFE": shfe}


def save_lastgood(name: str, res: dict) -> None:
    """采集成功后落一份「该所最近一次好数据」快照（供下次失败兜底）。"""
    ensure_dir(LASTGOOD_DIR)
    path = os.path.join(LASTGOOD_DIR, "%s.json" % name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, path)      # 原子替换，避免写一半被读到


def load_lastgood(name: str):
    """读取上次成功快照；没有则返回 None。"""
    path = os.path.join(LASTGOOD_DIR, "%s.json" % name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:                       # noqa: BLE001
        log.warning("%s 快照损坏，忽略: %s", name, e)
        return None
ORDER = ["CZCE", "DCE", "GFEX", "SHFE"]


def setup_log():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_cached(date_str: str) -> dict:
    """从 data/warrant/out 已落地的 JSON 重建结果（跳过采集，只重跑归一化）"""
    results = {}
    for name in ORDER:
        base = os.path.join(OUT_DIR, "%s_%s" % (date_str, name))
        res = {"exchange": name,
               "date": "%s-%s-%s" % (date_str[:4], date_str[4:6], date_str[6:])}
        hit = False
        for key in ("detail", "summary", "product", "sub"):
            path = "%s_%s.json" % (base, key)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    res[key] = json.load(f)
                hit = True
        if hit:
            results[name] = res
        else:
            log.warning("%s 无缓存文件（%s_*.json），跳过", name, date_str)
    return results


def write_output(result: dict, date_str: str):
    """落盘某交易所的明细/汇总/品种/品牌四份 JSON。

    【2026-10-23 改原子写】原实现 `open(...,"w") as f: json.dump(...)` 会先把文件
      截断为 0 字节再写。仓单采集每天 00:00 跑，而这四份文件同时被**页面接口与分析**
      读取 ⇒ 只要撞上截断窗口，读方就拿到空文件、`json.loads` 抛
      `JSONDecodeError`。与公众号读书通道那次事故（两轮退出码 1）同源。
    """
    from tradingagents.atomic_io import save_json

    ensure_dir(OUT_DIR)
    base = os.path.join(OUT_DIR, "%s_%s" % (date_str, result["exchange"]))
    for key in ("detail", "summary", "product", "sub"):
        if result.get(key):
            save_json("%s_%s.json" % (base, key), result[key], indent=1)


def build_unified(results: dict, date_hint: str | None = None) -> dict:
    """归一化成统一表（仓库级 / 品种级 / 品牌级），并用仓库行求和校验品种合计。"""
    wrows, prows, brows, warns = [], [], [], []
    for name, res in results.items():
        wh = unify.normalize_warehouse(name, res)
        pr = unify.normalize_product(name, res)
        br = unify.normalize_brand(name, res)
        wrows.extend(wh)
        prows.extend(pr)
        brows.extend(br)
        warns.extend(unify.cross_check(wh, pr))
    for w in warns:
        log.warning("口径校验: %s", w)

    url_date = date_hint or max(r["date"] for r in results.values()).replace("-", "")
    prows.sort(key=lambda x: (x["exchange"], str(x["product_code"])))
    wrows.sort(key=lambda x: (x["exchange"], str(x["product_code"]), str(x["warehouse_no"])))
    brows.sort(key=lambda x: (x["exchange"], str(x["product_code"]), str(x["brand"])))

    p1 = unify.write_csv(os.path.join(OUT_DIR, "warrant_%s_warehouse.csv" % url_date),
                         wrows, unify.UNIFIED_WAREHOUSE_COLS)
    p2 = unify.write_csv(os.path.join(OUT_DIR, "warrant_%s_product.csv" % url_date),
                         prows, unify.UNIFIED_PRODUCT_COLS)
    p3 = unify.write_csv(os.path.join(OUT_DIR, "warrant_%s_brand.csv" % url_date),
                         brows, unify.UNIFIED_BRAND_COLS)
    log.info("归一化表: 仓库级 %d / 品种级 %d / 品牌级 %d 行",
             len(wrows), len(prows), len(brows))
    log.info("口径校验: %d 条不一致", len(warns))
    return {"warehouse": p1, "product": p2, "brand": p3,
            "rows": {"warehouse": len(wrows), "product": len(prows), "brand": len(brows)},
            "warns": warns}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYYMMDD，缺省=自动取最近交易日")
    ap.add_argument("--only", help="只采集这些交易所，逗号分隔")
    ap.add_argument("--skip", help="跳过这些交易所，逗号分隔")
    ap.add_argument("--dce-headless", action="store_true",
                    help="DCE 用无头浏览器（默认有头，无头过不了瑞数防护）")
    ap.add_argument("--from-cache", metavar="YYYYMMDD",
                    help="不联网，直接用 out 目录里已有的 JSON 重建统一表与站点数据")
    ap.add_argument("--force", action="store_true", help="忽略当日缓存强制重新下载")
    ap.add_argument("--no-unify", action="store_true", help="不生成归一化表")
    ap.add_argument("--no-site", action="store_true", help="不生成站点 JSON")
    args = ap.parse_args()
    setup_log()

    if args.from_cache:
        results = load_cached(args.from_cache)
        if not results:
            log.error("缓存为空，退出")
            return 1
        if not args.no_unify:
            build_unified(results, args.from_cache)
        if not args.no_site:
            site_builder.build(results, args.from_cache)
        return 0

    date = dt.datetime.strptime(args.date, "%Y%m%d").date() if args.date else None
    only = set(x.strip().upper() for x in args.only.split(",")) if args.only else None
    skip = set(x.strip().upper() for x in args.skip.split(",")) if args.skip else set()

    summary, results = {}, {}
    for name in ORDER:
        if only and name not in only:
            continue
        if name in skip:
            continue
        log.info("=" * 56)
        log.info(">>> 采集 %s", name)
        try:
            kw = {"date": date, "force": args.force}
            if name == "DCE":
                kw["headless"] = args.dce_headless
            res = COLLECTORS[name].collect(**kw)
            res["_fetch"] = {"ok": True, "stale": False,
                             "at": dt.datetime.now().isoformat(timespec="seconds")}
            results[name] = res
            write_output(res, res["date"].replace("-", ""))
            _prows = len(res.get("product", []))
            summary[name] = {"ok": True, "date": res["date"],
                             "detail_rows": len(res.get("detail", [])),
                             "product_rows": _prows}
            if _prows == 0:
                # 【关键】0 个品种 = 采集异常(典型: 接口返回"总计"占位空壳被当成
                # 有效数据; 2026-09-17 广期所就是这样静默变 0 的, 前端"四所"变三所
                # 而日志里一句异常都没有)。必须显式标红 + 让退出码非 0。
                log.error("%s 采集到 0 个品种 —— 判定为异常(空壳数据/日报未发布)", name)
                summary[name]["ok"] = False
                summary[name]["error"] = "0 个品种(空壳数据或日报未发布)"
                # 【2026-10-21】0 品种也要走快照兜底，否则该所照样从页面消失。
                res["_fetch"] = {"ok": False, "stale": True,
                                 "error": summary[name]["error"],
                                 "at": dt.datetime.now().isoformat(timespec="seconds")}
                _lg = load_lastgood(name)
                if _lg:
                    results[name] = _lg
                    log.warning("%s 采集异常，沿用上次成功快照（数据日 %s）", name, _lg.get("date"))
        except Exception as e:                            # noqa: BLE001
            log.error("%s 采集失败: %s", name, e)
            log.debug(traceback.format_exc())
            summary[name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            # 【2026-10-21 核心修复】采集失败时**不许**让该所从结果里消失。
            #   旧实现只把本次结果交给 site.build ⇒ 一次瞬时失败就让该所整块
            #   从 overview.json 消失（2026-10-21 16:40 郑商所+广期所同时翻车，
            #   页面四所变两所且零提示）。这里退回上次成功快照并标记 stale。
            _lg = load_lastgood(name)
            if _lg:
                _lg = dict(_lg)
                _lg["_fetch"] = {"ok": False, "stale": True,
                                 "error": summary[name]["error"],
                                 "at": dt.datetime.now().isoformat(timespec="seconds")}
                results[name] = _lg
                summary[name]["fallback_date"] = _lg.get("date")
                log.warning("%s 采集失败，沿用上次成功快照（数据日 %s）",
                            name, _lg.get("date"))
            else:
                log.error("%s 采集失败且无历史快照 —— 该所本次将缺数据", name)

    # 成功（ok 且品种数 > 0）的交易所落快照，供下次失败兜底
    for _n, _s in summary.items():
        if _s.get("ok") and _n in results:
            save_lastgood(_n, results[_n])

    if results:
        if not args.no_unify:
            build_unified(results)
        if not args.no_site:
            site_builder.build(results)

    log.info("=" * 56)
    log.info("采集结果汇总:")
    for k in ORDER:
        if k in summary:
            log.info("  %-5s %s", k, json.dumps(summary[k], ensure_ascii=False))
    ensure_dir(OUT_DIR)
    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"run_at": dt.datetime.now().isoformat(timespec="seconds"),
                   "results": summary}, f, ensure_ascii=False, indent=1)
    return 0 if summary and all(v.get("ok") for v in summary.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
