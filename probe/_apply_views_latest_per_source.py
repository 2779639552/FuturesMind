# -*- coding: utf-8 -*-
"""观点总览缺省口径：从「全品种最新一天」改为「每家发行方各取自己最新一期」。

病根（用户报障：研报页找不到永安期货，不是有周报吗）：
  · 永安每周日发周报，工作日发日报的机构（华泰/东财/国君）永远比它新；
  · 缺省只取 dates[0]（全品种最新一天）⇒ 一到工作日，**整家周报机构从表里消失**，
    用户看到的现象就是"永安没了"，而不是"永安比别人旧三天"。
  · 这不只影响永安：任何只发周报/双周报的机构都会被日报挤掉。

改法：缺省（未指定 date）时按 source 分组、每组取各自最大日期，再合并。
     行上本来就渲染 publish_date（研报列下方小字），所以不同日期混排不会误导。
     显式传 date 的旧语义保持不动（精确到某一天）。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_app.py")

OLD = '''    elif date in dates:
        rows = [r for r in reports if _rdate(r) == date]
    else:  # 传入日期不属于该品种(切换品种残留) → 回退最新研报日期
        rows = [r for r in reports if _rdate(r) == dates[0]]
        date = dates[0]
    rows.sort(key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)
'''

NEW = '''    elif date in dates:
        rows = [r for r in reports if _rdate(r) == date]
    else:
        # 【2026-10-21 口径变更】缺省不再"只取全品种最新一天"：
        #   永安这类**只发周报**的机构（每周日），日期永远比工作日日报旧 ⇒ 一到工作日
        #   整家从表里消失，用户报障的原话是"找不到永安期货的观点了，不是有周报吗"。
        #   ⇒ 改为**每家发行方各取自己最新一期**再合并。行上本来就渲染 publish_date，
        #     不同日期混排不会误导（表里能直接看出永安是 10-18、华泰是 10-21）。
        #   显式传 date 的旧语义（精确到某一天）保持不变。
        latest_by_src: dict[str, str] = {}
        for r in reports:
            d0 = _rdate(r)
            if not d0:
                continue
            s0 = _normalize_source(r.get("source") or "")
            if d0 > latest_by_src.get(s0, ""):
                latest_by_src[s0] = d0
        rows = [r for r in reports
                if _rdate(r) and _rdate(r) == latest_by_src.get(_normalize_source(r.get("source") or ""))]
        date = dates[0]
    # 排序:先按研报日期、再按入库时间 —— 各家最新一期混排时,新的在前才符合阅读预期
    rows.sort(key=lambda r: (_rdate(r), str(r.get("uploaded_at") or "")), reverse=True)
'''

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
# web_app.py 是 CRLF；三引号里写的是 LF，统一转换后再比对
OLD = OLD.replace("\n", "\r\n")
NEW = NEW.replace("\n", "\r\n")
assert raw.count(OLD) == 1, "锚点命中 %d 次" % raw.count(OLD)
out = raw.replace(OLD, NEW, 1)

# 返回值补一个口径标记，前端据此换文案（不靠"日期是否唯一"猜）
RET_OLD = '''        "date": date,
        "dates": dates,
        "rows": [_research_view_row(r) for r in rows],
    })
'''
RET_NEW = '''        "date": date,
        "dates": dates,
        "latest_per_source": True,   # 【2026-10-21】缺省口径 = 每家发行方各取最新一期
        "span": {"asof": dates[0], "oldest": min((_rdate(r) for r in rows if _rdate(r)), default="")},
        "rows": [_research_view_row(r) for r in rows],
    })
'''
RET_OLD = RET_OLD.replace("\n", "\r\n")
RET_NEW = RET_NEW.replace("\n", "\r\n")
assert out.count(RET_OLD) == 1, "返回锚点命中 %d 次" % out.count(RET_OLD)
out = out.replace(RET_OLD, RET_NEW, 1)

assert out.count("\n") == out.count("\r\n"), "web_app.py 应为 CRLF"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
