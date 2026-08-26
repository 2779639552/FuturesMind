"""对已有东财股吧 JSONL 补跑 NER+情感 enrich（2026-08-26）。

桌面 THINK2_DIR 缺东财采集批次，而仓库侧冒烟采的 18 条带 --no-enrich（varieties=None），
trend_aggregator 从 variety_sentiments 提取品种 → 不 enrich 进不了平台聚合。本脚本用
桌面运行目录的 ner/sentiment 规则引擎对东财记录补 NER+情感，输出为可被聚合的 JSONL。
"""
import json
import pathlib
import sys

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from path_utils import resolve_think2_dir  # noqa: E402

DESK = resolve_think2_dir()
sys.path.insert(0, str(DESK))  # 用桌面运行目录的 ner/sentiment(与桌面 batch_collect 一致)

from ner import FuturesNER  # noqa: E402
from sentiment import SentimentAnalyzer  # noqa: E402

ner = FuturesNER()
sentiment = SentimentAnalyzer()

SRC = pathlib.Path(__file__).resolve().parents[1] / "data_collection" / "validate" / "output"
DST = DESK / "output"


def enrich(note: dict) -> dict:
    text = (note.get("title", "") + " " + note.get("desc", "")).strip()
    if not text:
        note.setdefault("varieties", [])
        note.setdefault("contracts", [])
        note.setdefault("variety_count", 0)
        note.setdefault("sentiment", "neutral")
        note.setdefault("sentiment_score", 0.0)
        note.setdefault("sentiment_confidence", 0.0)
        note.setdefault("variety_sentiments", [])
        return note
    entities = ner.extract(text)
    note["varieties"] = entities["varieties"]
    note["contracts"] = entities["contracts"]
    note["variety_count"] = entities["variety_count"]
    r = sentiment.analyze(text)
    note["sentiment"] = r["sentiment"]
    note["sentiment_score"] = r["score"]
    note["sentiment_confidence"] = r["confidence"]
    note["variety_sentiments"] = sentiment.analyze_aspects(text, entities["varieties"])
    return note


files = sorted(SRC.glob("batch_eastmoney_guba_*.jsonl"))
if not files:
    print("无东财 JSONL 源文件")
    raise SystemExit(1)

all_records = []
for f in files:
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        all_records.append(enrich(json.loads(line)))

dst = DST / "batch_eastmoney_guba_20260826_000001.jsonl"
with open(dst, "w", encoding="utf-8") as fh:
    for rec in all_records:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

total = len(all_records)
with_var = sum(1 for r in all_records if r.get("variety_count", 0) > 0)
with_vs = sum(1 for r in all_records if r.get("variety_sentiments"))
print(f"写出 {dst.name}: 共 {total} 条, 含品种 {with_var} 条, 含品种情感 {with_vs} 条")
