# -*- coding: utf-8 -*-
import json, os
J = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bt_result.json")
r = json.load(open(J, encoding="utf-8"))
per = r.get("by_variety") or {}
print("分品种(动量20×库存, 10日, lag7) —— 技术侧收益为正=技术赢")
win_t = [c for c, v in per.items() if (v["tech"] or 0) > 0]
print("品种数 %d; 技术侧为正 %d (%d%%), 基本面侧为正 %d" %
      (len(per), len(win_t), round(100*len(win_t)/max(1,len(per))), len(per)-len(win_t)))
for c, v in sorted(per.items(), key=lambda kv: -(kv[1]["tech"] or 0)):
    print("  %-4s n=%-4d 技术 %+.3f%%  基本面 %+.3f%%" % (c, v["n"], v["tech"] or 0, v["fund"] or 0))
