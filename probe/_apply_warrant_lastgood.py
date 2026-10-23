# -*- coding: utf-8 -*-
"""仓单采集：单所失败不许把该所数据整体丢掉（last-good 快照兜底）。

【背景】2026-10-21 16:40 那次自动采集，郑商所与广期所同时报
「最近 12 天内未找到仓单日报数据」，而 `site.build(results)` 只用**本次** results
⇒ 两个所从 overview.json 里直接消失，页面「四所」变两所，且没有任何提示。
单独重跑这两所都一次成功（CZCE 25 品种 / GFEX 5 品种）⇒ 是瞬时访问失败。

【改法】
1. `warrant/common.py` 加 `LASTGOOD_DIR`。
2. `warrant_collect.py`：采集成功 ⇒ 落一份每所快照；采集失败（含"0 个品种"）
   ⇒ 读回上次成功快照参与 build，并带上 `_fetch.stale/error`。
3. `warrant/site.py`：exchanges 里透出 `date / stale / fetch_error`，
   overview 顶层加 `fetch_errors`（前端据此提示"哪一所没采到、用的是哪天数据"）。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LASTGOOD_DIR = os.path.join(ROOT, "data", "warrant", "lastgood")


def patch(path: str, pairs):
    p = os.path.join(ROOT, path)
    s = io.open(p, "r", encoding="utf-8", newline="").read()
    for old, new in pairs:
        n = s.count(old)
        assert n == 1, "%s 锚点命中 %d 次:\n%s" % (path, n, old[:120])
        s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    print("patched", path, "len", len(s))


# ── ① common.py：加快照目录 ───────────────────────────────────────────────
patch("warrant/common.py", [(
    'WEB_DIR = os.path.join(DATA_DIR, "web")          # 供前端直接读取的 JSON',
    'WEB_DIR = os.path.join(DATA_DIR, "web")          # 供前端直接读取的 JSON\n'
    '# 【2026-10-21】每所「上一次成功采集」的快照。单所失败时用它兜底，\n'
    '#   避免一个瞬时网络故障就把该所从页面上整块抹掉（当时四所变两所、无提示）。\n'
    'LASTGOOD_DIR = os.path.join(DATA_DIR, "lastgood")',
)])

# ── ② warrant_collect.py：成功后落快照 / 失败后读快照 ────────────────────
patch("warrant_collect.py", [
    (
        "from warrant.common import OUT_DIR, ensure_dir, log",
        "from warrant.common import LASTGOOD_DIR, OUT_DIR, ensure_dir, log",
    ),
    (
        'COLLECTORS = {"CZCE": czce, "DCE": dce, "GFEX": gfex, "SHFE": shfe}',
        'COLLECTORS = {"CZCE": czce, "DCE": dce, "GFEX": gfex, "SHFE": shfe}\n'
        '\n'
        '\n'
        'def save_lastgood(name: str, res: dict) -> None:\n'
        '    """采集成功后落一份「该所最近一次好数据」快照（供下次失败兜底）。"""\n'
        '    ensure_dir(LASTGOOD_DIR)\n'
        '    path = os.path.join(LASTGOOD_DIR, "%s.json" % name)\n'
        '    tmp = path + ".tmp"\n'
        '    with open(tmp, "w", encoding="utf-8") as f:\n'
        '        json.dump(res, f, ensure_ascii=False)\n'
        '    os.replace(tmp, path)      # 原子替换，避免写一半被读到\n'
        '\n'
        '\n'
        'def load_lastgood(name: str):\n'
        '    """读取上次成功快照；没有则返回 None。"""\n'
        '    path = os.path.join(LASTGOOD_DIR, "%s.json" % name)\n'
        '    if not os.path.exists(path):\n'
        '        return None\n'
        '    try:\n'
        '        with open(path, "r", encoding="utf-8") as f:\n'
        '            return json.load(f)\n'
        '    except Exception as e:                       # noqa: BLE001\n'
        '        log.warning("%s 快照损坏，忽略: %s", name, e)\n'
        '        return None',
    ),
    (
        "            res = COLLECTORS[name].collect(**kw)\n"
        "            results[name] = res\n"
        "            write_output(res, res[\"date\"].replace(\"-\", \"\"))",
        "            res = COLLECTORS[name].collect(**kw)\n"
        "            res[\"_fetch\"] = {\"ok\": True, \"stale\": False,\n"
        "                             \"at\": dt.datetime.now().isoformat(timespec=\"seconds\")}\n"
        "            results[name] = res\n"
        "            write_output(res, res[\"date\"].replace(\"-\", \"\"))",
    ),
    (
        '                log.error("%s 采集到 0 个品种 —— 判定为异常(空壳数据/日报未发布)", name)\n'
        '                summary[name]["ok"] = False\n'
        '                summary[name]["error"] = "0 个品种(空壳数据或日报未发布)"',
        '                log.error("%s 采集到 0 个品种 —— 判定为异常(空壳数据/日报未发布)", name)\n'
        '                summary[name]["ok"] = False\n'
        '                summary[name]["error"] = "0 个品种(空壳数据或日报未发布)"\n'
        '                # 【2026-10-21】0 品种也要走快照兜底，否则该所照样从页面消失。\n'
        '                res[\"_fetch\"] = {\"ok\": False, \"stale\": True,\n'
        '                                 "error": summary[name][\"error\"],\n'
        '                                 "at\": dt.datetime.now().isoformat(timespec=\"seconds\")}\n'
        '                _lg = load_lastgood(name)\n'
        '                if _lg:\n'
        '                    results[name] = _lg\n'
        '                    log.warning("%s 采集异常，沿用上次成功快照（数据日 %s）", name, _lg.get("date"))',
    ),
    (
        '            summary[name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}',
        '            summary[name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}\n'
        '            # 【2026-10-21 核心修复】采集失败时**不许**让该所从结果里消失。\n'
        '            #   旧实现只把本次结果交给 site.build ⇒ 一次瞬时失败就让该所整块\n'
        '            #   从 overview.json 消失（2026-10-21 16:40 郑商所+广期所同时翻车，\n'
        '            #   页面四所变两所且零提示）。这里退回上次成功快照并标记 stale。\n'
        '            _lg = load_lastgood(name)\n'
        '            if _lg:\n'
        '                _lg = dict(_lg)\n'
        '                _lg["_fetch"] = {"ok": False, "stale": True,\n'
        '                                 "error": summary[name]["error"],\n'
        '                                 "at": dt.datetime.now().isoformat(timespec="seconds")}\n'
        '                results[name] = _lg\n'
        '                summary[name]["fallback_date"] = _lg.get("date")\n'
        '                log.warning("%s 采集失败，沿用上次成功快照（数据日 %s）",\n'
        '                            name, _lg.get("date"))\n'
        '            else:\n'
        '                log.error("%s 采集失败且无历史快照 —— 该所本次将缺数据", name)',
    ),
    # 成功后落快照（放在 0 品种判定之后、except 之前的位置不合适，改用独立钩子）
    (
        '    if results:\n        if not args.no_unify:',
        '    # 成功（ok 且品种数 > 0）的交易所落快照，供下次失败兜底\n'
        '    for _n, _s in summary.items():\n'
        '        if _s.get("ok") and _n in results:\n'
        '            save_lastgood(_n, results[_n])\n'
        '\n'
        '    if results:\n        if not args.no_unify:',
    ),
])

# ── ③ site.py：透出 date / stale / fetch_error ────────────────────────────
patch("warrant/site.py", [
    (
        '        ex_meta.append({\n'
        '            "code": ex, "name": EXCHANGE_CN.get(ex, ex), "unit": unit,',
        '        # 【2026-10-21】把"这一所是不是沿用了上次快照"透给前端：不标出来，\n'
        '        #   用户只会看到日期不动的旧数据，误以为是最新的。\n'
        '        _fetch = res.get("_fetch") or {}\n'
        '        ex_meta.append({\n'
        '            "code": ex, "name": EXCHANGE_CN.get(ex, ex), "unit": unit,\n'
        '            "date": res.get("date"),\n'
        '            "stale": bool(_fetch.get("stale")),\n'
        '            "fetch_error": _fetch.get("error"),',
    ),
    (
        '    overview = {\n'
        '        "date": date_str,\n'
        '        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),\n'
        '        "exchanges": ex_meta,\n'
        '        "products": products,\n'
        '    }',
        '    # 本次真正采集失败的交易所清单（有快照兜底也会有数据，但必须外显）\n'
        '    fetch_errors = {m["code"]: {"error": m.get("fetch_error"), "date": m.get("date")}\n'
        '                    for m in ex_meta if m.get("stale")}\n'
        '    overview = {\n'
        '        "date": date_str,\n'
        '        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),\n'
        '        "exchanges": ex_meta,\n'
        '        "products": products,\n'
        '        "fetch_errors": fetch_errors,\n'
        '    }',
    ),
])

print("ALL PATCHED")
