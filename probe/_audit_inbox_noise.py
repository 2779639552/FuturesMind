# -*- coding: utf-8 -*-
"""噪声过滤**审计工具**：拿真实 inbox 跑一遍，打印过滤名单 + 边界样本。

【为什么要有它】规则写在 `wechat_ingest.noise_reason()` 里，单测只能覆盖我造出来的
  样本；**真实语料的形态**（尤其是"含'调研'却是真研报"这类）必须拿真数据核对。
  本工具**直接调用产品代码**（不复制规则），所以它跑出来的就是线上会执行的结果。

用法：
    ./venv/Scripts/python.exe probe/_audit_inbox_noise.py
    ./venv/Scripts/python.exe probe/_audit_inbox_noise.py --state-dir <其它inbox目录>

判读：① "过滤名单"逐条看，必须全是噪声；② "保留侧实质最低 10 条"是离边界最近的，
      逐条确认它们是真内容；③ 末尾的定向反例必须全绿。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wechat_ingest import MIN_BODY_CHARS, MIN_SUBSTANCE_CHARS, noise_reason, substance_len  # noqa: E402

# 定向反例：真实语料里"最像噪声的真研报"，必须全部保留。
MUST_KEEP = [
    "铝合金企业调研：票荒倒逼减产，合规重塑格局",
    "明天要开盘啦，假期外盘哪个品种表现突出？",
    "为什么今年铁矿9-1价差低位但难以大幅走扩",
    "从铜与铁矿比值变化探究背后定价驱动差异",
    "财政政策展望：存量空间为主，供给高峰渐过",
    "年报的跟踪与展望——半年度机构持仓的变动情况",
    "目前大宗商品的估值走到什么位置了？10-13",
    "恒力期货日报20261014",
    "【建投能化|月策合集】原油供应先行回暖，化工板块利润修复2026-10-12",
    "【建投能化|周报合集】海峡复航与地缘风险并行，能化节前震荡为主2026-09-28",
    "【建投能化|深度洞察】天然橡胶&20号胶：周期新篇章，定价新纪元20261006",
    "【建投能化|观数】KPLER全球周度原油库存解读20261012",
    "【半年度报告集锦】有色新能源板块",
    "【能化早评】胡塞威胁攻击沙特能源设施，原油继续震荡上行",
]
# 已知噪声（用于交叉核对；新语料里出现新的噪声不必惊慌，看"过滤名单"即可）
KNOWN_NOISE = [
    "【招聘】能化研究员",
    "第六届物产中大黑色“远洋论剑”在唐山成功举办",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=None, help="inbox 所在目录(默认线上状态目录)")
    args = ap.parse_args()
    d = Path(args.state_dir) if args.state_dir else Path.home() / ".tradingagents" / "wechat"
    inbox = d / "wechat_inbox.jsonl"
    if not inbox.is_file():
        print("✘ 找不到 inbox:", inbox)
        return 2

    rows = [json.loads(l) for l in inbox.read_text(encoding="utf-8").splitlines() if l.strip()]
    seen, drops, keeps, short = set(), [], [], 0
    for r in rows:
        title = (r.get("title") or "").strip()
        if title in seen:
            continue
        seen.add(title)
        text = (r.get("text") or "").strip()
        if len(text) < MIN_BODY_CHARS:
            short += 1                       # 已被长度关拦住，不参与本次评估
            continue
        why = noise_reason(title, text)
        (drops if why else keeps).append((title, why, len(text), substance_len(text)))

    print("inbox: %d 条 / %d 个去重标题" % (len(rows), len(seen)))
    print("  正文 < %d 字被长度关拦住: %d 个标题" % (MIN_BODY_CHARS, short))
    print("  参与内容判据: 过滤 %d / 保留 %d" % (len(drops), len(keeps)))
    print()
    print("=== ① 过滤名单（逐条核对，必须全是噪声）===")
    for title, why, n, sub in sorted(drops):
        print("  %5d字/实质%4d  %s" % (n, sub, title[:52]))
        print("               → %s" % why)
    if not drops:
        print("  （无）")
    print()
    print("=== ② 保留侧「实质字数」最低的 10 条（离边界最近，重点核对）===")
    for title, _why, n, sub in sorted(keeps, key=lambda x: x[3])[:10]:
        print("  %5d字/实质%4d  %s" % (n, sub, title[:56]))
    print("  阈值 MIN_SUBSTANCE_CHARS = %d" % MIN_SUBSTANCE_CHARS)
    print()

    print("=== ③ 定向反例（必须全部保留）===")
    by_title = {}
    for r in rows:
        by_title.setdefault((r.get("title") or "").strip(), (r.get("text") or "").strip())
    bad, found = 0, 0
    for t in MUST_KEEP:
        if t not in by_title:
            print("  %-50s (inbox 无此条)" % t[:50])
            continue
        found += 1
        why = noise_reason(t, by_title[t])
        bad += 1 if why else 0
        print("  %-50s %s" % (t[:50], ("★误伤: " + why) if why else "保留 ✔"))
    print()
    print("定向反例: 命中 %d 条 / 误伤 %d 条 %s"
          % (found, bad, "✔" if bad == 0 else "★必须修规则"))
    known = [t for t in KNOWN_NOISE if t in by_title]
    known_hit = sum(1 for t in known if noise_reason(t, by_title[t]))
    print("已知噪声 %d 条中已过滤 %d 条" % (len(known), known_hit))
    print()
    print("结论:", "✔ 无误伤" if bad == 0 else "★存在误伤，先修规则再上线")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
