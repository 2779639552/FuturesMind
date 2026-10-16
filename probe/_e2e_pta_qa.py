# -*- coding: utf-8 -*-
"""端到端: 修复后研报问答问「PTA近日情况」是否召回今天的 PTA 研报。

【背景】2026-09-30 报障。根因 = 前端 _populateVarietySelects 把 #res-ask-variety
(空串="全部品种") 静默补成池首 BR ⇒ 后端拿到 variety="BR" 把检索过滤到橡胶板块,
当日国泰君安「PX PTA MEG 基本面数据」(rid=1463) 与华泰「聚酯日报」(rid=1440) 全部搜不到。

【本脚本】模拟前端在**修复后**的两种取值直连 /api/research/qa/ask:
  A) variety=""   ← 修复后前端行为(空串="全部品种", 后端按问句识别品种)
  B) variety="BR" ← 修复前前端行为(复现旧故障)
比对 citations 里是否出现 rid=1463 / 1440。
"""
import json
import urllib.request

URL = "http://127.0.0.1:5000/api/research/qa/ask"
TARGETS = {1463: "国泰君安 PX PTA MEG 基本面数据", 1440: "华泰 聚酯日报 20260930"}


def ask(variety, tag):
    body = json.dumps({
        "question": "PTA近日情况",
        "variety": variety,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=600)
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        print("[%s] ERR %s: %s" % (tag, type(e).__name__, e))
        return None
    cits = data.get("citations") or []
    rid_map = {}
    for c in cits:
        rid = c.get("report_id") or (c.get("metadata") or {}).get("report_id")
        if rid is not None:
            rid_map.setdefault(int(rid), (c.get("title") or "")[:52])
    print("\n===== [%s] variety=%r ok=%s =====" % (tag, variety, data.get("ok")))
    print("  answer[:160]: %s" % str(data.get("answer") or "")[:160].replace("\n", " "))
    print("  citations: %d 篇" % len(cits))
    for rid, t in sorted(rid_map.items()):
        mark = " ★目标" if rid in TARGETS else ""
        print("    rid=%-5s %s%s" % (rid, t, mark))
    hit = [rid for rid in TARGETS if rid in rid_map]
    print("  目标命中: %s" % (hit or "无"))
    return rid_map


if __name__ == "__main__":
    print("### A) 修复后前端行为: variety='' (全部品种 → 后端按问句识别)")
    ra = ask("", "A-fixed")
    print("\n### B) 修复前前端行为: variety='BR' (复现旧故障)")
    rb = ask("BR", "B-bug")
    print("\n===== 结论 =====")
    for rid, name in TARGETS.items():
        print("  rid=%s %s -> A(修复后)=%s  B(旧故障)=%s"
              % (rid, name,
                 (ra is not None and rid in ra), (rb is not None and rid in rb)))
