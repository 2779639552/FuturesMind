# -*- coding: utf-8 -*-
"""存量 batch_*.jsonl 的 NER 标注回填工具。

【什么时候需要】
  NER 词典/匹配规则改动后, **只影响新采集**; 存量 jsonl 里存的是采集当时的
  varieties, 页面"采集帖子"直接读它 —— 所以用户在页面上看到的仍是旧误标。
  品种标签无法像 URL 那样在展示层兜底(必须重算), 只能回填数据。

【同步重算的字段】(三者联动, 缺一即不一致)
  varieties          = ner.extract(text)["varieties"]
  variety_count      = len(varieties)                                   <- 派生
  variety_sentiments = sentiment.analyze_aspects(text, varieties)        <- 依赖 varieties
  contracts / sentiment / sentiment_score 等与 varieties 无关, 保持原样。

【安全设计】
  1. 只替换这三个 key 的**值字节片段**, 其余字节原样保留(不做整体重序列化, 免格式漂移)
  2. 保留原 CRLF 行尾
  3. 逐行校验: 替换后除这三个字段外其余字段必须完全一致, 不一致则放弃该行
  4. 三字段缺任一 -> 跳过该行(说明是更早的 schema, 需另行补齐)
  5. 建议先手工备份 output/batch_*.jsonl 再跑

【用法】
  python refill_varieties.py                      # dry-run, 只统计
  python refill_varieties.py --limit 2            # dry-run 前 2 个文件
  python refill_varieties.py --apply              # 实际写入(按文件原子替换)
"""
import collections
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
sys.path.insert(0, str(HERE))
from ner import FuturesNER  # noqa: E402
from sentiment import SentimentAnalyzer  # noqa: E402

FIELDS = ("varieties", "variety_count", "variety_sentiments")


def span_of(s: str, key: str):
    """定位顶层 key 的值字节跨度 (start, end)。

    直接在字节层找 '"key"' —— JSON 字符串值内部的引号会被转义成 \\",
    故该模式只可能出现在真正的 key 位置, 不会误命中 desc 文本。
    """
    i = s.find('"' + key + '"')
    if i < 0:
        return None
    j = s.find(":", i + len(key) + 2)
    if j < 0:
        return None
    k = j + 1
    while k < len(s) and s[k] in " \t":
        k += 1
    try:
        _, consumed = json.JSONDecoder().raw_decode(s[k:])
    except ValueError:
        return None
    return (k, k + consumed)


def main():
    apply = "--apply" in sys.argv
    limit = 10**9
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    ner = FuturesNER()
    sent = SentimentAnalyzer()

    def refill_line(body: str):
        try:
            d = json.loads(body)
        except ValueError:
            return body, "skip_badjson"
        if not isinstance(d, dict):
            return body, "skip_badjson"
        for key in FIELDS:
            if key not in d:
                return body, "skip_nokey"
        spans = [span_of(body, key) for key in FIELDS]
        if any(r is None or r[1] <= r[0] for r in spans):
            return body, "skip_nokey"

        text = ((d.get("title") or "") + " " + (d.get("desc") or "")).strip()
        ents = ner.extract(text)
        new_vals = {
            "varieties": ents["varieties"],
            "variety_count": ents["variety_count"],
            "variety_sentiments": sent.analyze_aspects(text, ents["varieties"]) if text else [],
        }
        newbody = body
        for key, (st, en) in sorted(zip(FIELDS, spans), key=lambda x: x[1][0], reverse=True):
            newbody = newbody[:st] + json.dumps(new_vals[key], ensure_ascii=False) + newbody[en:]
        try:
            d2 = json.loads(newbody)
        except ValueError:
            return body, "skip_badjson"
        for k in set(d) | set(d2):
            if k in FIELDS:
                continue
            if d.get(k) != d2.get(k):
                return body, "skip_verify"
        return newbody, ("unchanged" if newbody == body else "ok")

    files = sorted(glob.glob(str(OUT / "batch_*.jsonl")))[:limit]
    stat = collections.Counter()
    var_before = collections.Counter()
    var_after = collections.Counter()

    for fi, fp in enumerate(files, 1):
        p = Path(fp)
        lines = p.read_bytes().decode("utf-8").split("\n")
        out = []
        for ln in lines:
            cr = "\r" if ln.endswith("\r") else ""
            body = ln[:-1] if cr else ln
            if not body.strip():
                out.append(ln)
                continue
            before = json.loads(body).get("varieties") or []
            newbody, st = refill_line(body)
            stat[st] += 1
            if st == "ok":
                after = json.loads(newbody).get("varieties") or []
                for v in before:
                    if isinstance(v, dict):
                        var_before[v.get("name")] += 1
                for v in after:
                    if isinstance(v, dict):
                        var_after[v.get("name")] += 1
            out.append(newbody + cr)
        if apply and stat["ok"]:
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_bytes("\n".join(out).encode("utf-8"))
            tmp.replace(p)
        if fi % 40 == 0:
            print(f"  ... 已处理 {fi}/{len(files)} 文件", flush=True)

    print()
    print("=" * 90)
    print(f"{'【实际写入】' if apply else '【DRY-RUN 未写入】'}  文件 {len(files)} 个")
    print("=" * 90)
    for k, v in stat.most_common():
        print(f"  {k:16} {v}")
    print()
    print("=== 品种标签数变化 (前 25, 仅统计被改动的行) ===")
    for name in sorted(set(var_before) | set(var_after),
                       key=lambda k: var_before.get(k, 0) - var_after.get(k, 0),
                       reverse=True)[:25]:
        o, n = var_before.get(name, 0), var_after.get(name, 0)
        if o != n:
            print(f"  {name:14} {o:6} -> {n:6}  ({n - o:+5})")


if __name__ == "__main__":
    main()
