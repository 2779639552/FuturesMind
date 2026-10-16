"""真实 HTTP 覆盖率扫描 —— 对运行中的服务打接口, 统计它实际产出多少张能力卡。

【为什么必须走 HTTP】进程内直调 build_verdict 会**绕过两条生产约束**:
  1. 品种白名单(_norm_code) —— 我之前的进程内探针能跑 RB, 但线上 RB 根本不在白名单里。
  2. 路由层的参数处理/降级分支 —— 线上利润取数走 gtja_api, 与进程内构造的 payload 不同。
只有打真实接口, 结论才对得上用户看到的页面。

【用法】python _http_coverage_scan.py [--base http://127.0.0.1:5000] [--varieties SC,TA,...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SLUG_PAT = re.compile(r"cap-([a-z][a-z-]*[a-z])")

# 默认取平台白名单(由 _norm_code 判定), 这里写死避免重复 import 生产模块。
DEFAULT_VARIETIES = ["SC", "TA", "PX", "EG", "FU", "LU", "PG", "M", "CF", "CJ", "LC",
                     "RU", "NR", "BR", "LH", "BU", "BZ", "EB", "SI", "PS", "P"]


def _get_json(base: str, path: str, params: dict, timeout: float = 90.0) -> dict:
    url = base.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 本机回环
        return json.loads(r.read().decode("utf-8"))


def _cards_from_verdict(d: dict) -> set[str]:
    """从 verdict 响应里抽**真正被计算**的卡片 slug。

    【两类合法载体】
      1. 结构化 `'card'` 键(如 stages.risk.trading_plan.card) —— 由纯函数返回, 字段语义即"本卡被用了"。
      2. `evidence.cards_used` 的字符串列表 —— cards.append 的落点, 是"实际用到"的权威记录。
    【★ 为什么不能顺手扫 stages 里的**任意**字符串】踩过: `money_management.notes` 里有一句
      "应与 Carver cap-leverage-kelly 交叉验证" —— 那是**提到**(给用户的提示), 不是**计算**。
      早期实现对 stages 全量 findall, 把这类"文本提及"也算成"用到", 于是 leverage-kelly
      在覆盖率报告里凭空多出一张**根本没算**的卡。教训: 覆盖率口径必须区分
      "输出契约里署名"与"正文里提到" —— 后者不是接入(同 _rebuild_coverage_audit 的 C 层)。
      故此处**只认 card 键与 cards_used**, 不扫 notes/desc 等自由文本。
    """
    out: set[str] = set()

    def walk_card_keys(o):
        """只递归取 `card` 键的值, 忽略其它一切字符串。"""
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "card" and isinstance(v, str):
                    out.update(SLUG_PAT.findall(v))
                else:
                    walk_card_keys(v)
        elif isinstance(o, (list, tuple)):
            for it in o:
                walk_card_keys(it)

    def walk_card_list(o):
        """取 cards_used 这类"纯 slug 列表"(逐个字符串提取, 允许合并署名)。"""
        if isinstance(o, (list, tuple)):
            for it in o:
                if isinstance(it, str):
                    out.update(SLUG_PAT.findall(it))
                else:
                    walk_card_list(it)

    walk_card_keys(d.get("stages") or {})
    ev = d.get("evidence") or {}
    walk_card_list(ev.get("cards_used") or [])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--varieties", default=",".join(DEFAULT_VARIETIES))
    ap.add_argument("--out", default=str(ROOT / "_http_cards.json"))
    a = ap.parse_args()

    varieties = [v.strip().upper() for v in a.varieties.split(",") if v.strip()]
    params_base = {"account": 1_000_000, "risk_pct": 0.30, "times": 10,
                   "point_value": 10.0, "round_trip_fee": 6.0}

    per: dict[str, dict] = {}
    union: set[str] = set()
    ok_n = fail_n = 0
    for v in varieties:
        rec: dict = {"ok": False, "cards": [], "votes": [], "error": ""}
        try:
            d = _get_json(a.base, "/api/quant/verdict", {"variety": v, **params_base})
            if not d.get("ok"):
                rec["error"] = d.get("error") or "ok=false"
                fail_n += 1
            else:
                cards = _cards_from_verdict(d)
                rec["ok"] = True
                rec["cards"] = sorted(cards)
                rec["votes"] = [x.get("book") for x in
                                (d.get("stages", {}).get("direction", {}).get("votes") or [])]
                rec["direction"] = (d.get("verdict") or {}).get("direction")
                union |= cards
                ok_n += 1
        except urllib.error.HTTPError as e:
            rec["error"] = f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'ignore')}"
            fail_n += 1
        except Exception as e:  # noqa: BLE001 - 单品种失败不中断扫描
            rec["error"] = f"{type(e).__name__}: {e}"
            fail_n += 1
        per[v] = rec
        flag = "OK " if rec["ok"] else "ERR"
        print(f"  [{flag}] {v:4s} {len(rec['cards']):2d} 张"
              + (f"  方向={rec.get('direction')}" if rec["ok"] else f"  {rec['error'][:70]}"))

    payload = {"base": a.base, "varieties": varieties, "ok_count": ok_n, "fail_count": fail_n,
               "per_variety": per, "union": sorted(union), "union_count": len(union)}
    Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("-" * 64)
    print(f"成功 {ok_n} / 失败 {fail_n} | 并集能力卡 {len(union)} 张")
    print(f"并集: {' '.join(sorted(union))}")
    print(f"-> {a.out}")
    return 0 if ok_n else 1


if __name__ == "__main__":
    raise SystemExit(main())
