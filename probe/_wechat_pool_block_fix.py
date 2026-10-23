# -*- coding: utf-8 -*-
"""修正「品种池拦截」统计的判据：改用稳定特征，不依赖被 LLM 覆盖过的 source。

【为什么必须改】
  ① 我第一版用 `source LIKE '%公众号%'` 统计 —— 实测**假阴性**：
     `web_app.py:4506` 的 `source = structured.publisher or report.source` 会把采集器写的
     `公众号-混沌天成研究` **覆盖成** LLM 识别出的 `混沌天成研究` ⇒ 前缀没了。
     同一批里同一个号两种写法并存（`混沌天成研究` 6 篇 + `公众号-混沌天成研究` 3 篇
     + `混沌天成研究院` 1 篇 = **3 种写法**）。
  ② 两条采集通道的**文件名特征**是稳定的（采集器写入、不被后续流程改写）：
     · 读书通道 `wechat_weread_collector.py`  → `filename = "wxmp_<original_id>_<title>.md"`
     · 合集通道 `wechat_album_collector.py`   → `filename = "<biz>_<mid>_<idx>_<title>.md"`
       且 source 前缀为 `公众号-`（号名固定为「多空逻辑」，未观察到被覆盖）
  ⇒ 判据改为 `filename LIKE 'wxmp_%' OR source LIKE '公众号-%'`：
     前者覆盖读书通道（不依赖 source），后者覆盖合集通道。

⚠ 一律用 chr(13)+chr(10) 构造 CRLF，不写字面转义。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE = "--write" in sys.argv
CR, LF = chr(13), chr(10)
EOL = CR + LF


def to_crlf(s: str) -> str:
    return s.replace(EOL, LF).replace(LF, EOL)


OLD_LF = '''    # ── ★ 品种池拦截：解释"采到了却没进库"（这是最容易被误判成故障的一环）──
    pool_block = {"days": 7, "count": 0, "sample": ""}
    try:
        import sqlite3 as _sq
        from database import DB_PATH
        with _sq.connect(str(DB_PATH)) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM research_reports WHERE source LIKE '%公众号%' "
                "AND status='skipped' AND uploaded_at >= date('now','-7 day')"
            ).fetchone()
            pool_block["count"] = int(row[0] or 0)
            row2 = c.execute(
                "SELECT error FROM research_reports WHERE source LIKE '%公众号%' "
                "AND status='skipped' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row2 and row2[0]:
                mm = _re.search(r"识别品种\\s*([A-Za-z,]+)\\s*均不在", str(row2[0]))
                pool_block["sample"] = mm.group(1) if mm else ""
    except Exception:
        pass
'''

NEW_LF = '''    # ── ★ 品种池拦截：解释"采到了却没进库"（这是最容易被误判成故障的一环）──
    # 【★ 判据选择】必须用**采集器写入的稳定特征**，不能靠 `source LIKE '%公众号%'`：
    #   实测 `web_app.py` 的处理流程里 `source = structured.publisher or report.source`
    #   会用 LLM 识别出的发行方**覆盖**采集器写的 `公众号-XXX`（渠道语义丢失）⇒
    #   同一批同一个号会裂成 `混沌天成研究` / `公众号-混沌天成研究` / `混沌天成研究院`
    #   三种写法，按 source 统计**必然假阴性**（实测漏掉 15 篇）。
    #   两条通道的稳定特征：
    #     · 读书通道 → filename 以 `wxmp_` 开头（采集器写的 key，不被后续流程改写）
    #     · 合集通道 → source 以 `公众号-` 开头（号名固定，未观察到被覆盖）
    _WECHAT_SRC = "(filename LIKE 'wxmp_%' OR source LIKE '公众号-%')"
    pool_block = {"days": 7, "count": 0, "sample": ""}
    try:
        import sqlite3 as _sq
        from database import DB_PATH
        with _sq.connect(str(DB_PATH)) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM research_reports WHERE " + _WECHAT_SRC +
                " AND status='skipped' AND uploaded_at >= date('now','-7 day')"
            ).fetchone()
            pool_block["count"] = int(row[0] or 0)
            row2 = c.execute(
                "SELECT error FROM research_reports WHERE " + _WECHAT_SRC +
                " AND status='skipped' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row2 and row2[0]:
                mm = _re.search(r"识别品种\\s*([A-Za-z,]+)\\s*均不在", str(row2[0]))
                pool_block["sample"] = mm.group(1) if mm else ""
    except Exception:
        pass
'''


def main() -> int:
    p = ROOT / "web_app.py"
    raw = p.read_bytes()
    assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 不是纯 CRLF"
    t = raw.decode("utf-8")
    o, n = to_crlf(OLD_LF), to_crlf(NEW_LF)
    c = t.count(o)
    if c != 1:
        print("  ★ 锚点命中 %d 次（应为 1）" % c)
        return 2
    t = t.replace(o, n, 1)
    out = t.encode("utf-8")
    assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏 EOL"
    assert "wxmp_%" in t
    if WRITE:
        p.write_bytes(out)
    print("=== %s ===  %d → %d 字节" % ("落盘" if WRITE else "空跑", len(raw), len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
