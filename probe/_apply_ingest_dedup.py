# -*- coding: utf-8 -*-
"""入库去重（用户 2026-10-21：「加一下」）。

背景：同一份 PDF 换个文件名再导一次，就会在观点总览里出现两条一模一样的研报
（本次实例：中信《资金情绪爆发…》一次用原始中文名、一次被重命名成 20260927_2.pdf）。
文件名、来源写法、入库时间都不同，唯一稳定的是**正文完全相同**。

做法：
  1. database.py 新增 `find_research_report_by_text()` —— 按 extracted_text 精确匹配；
  2. web_app.py 在文本提取之后、调 LLM 之前判重，命中就标 skipped 并直接返回
     （省掉两次 LLM 调用，也不会再产生第二条聚合条目）。
"""
import io
from pathlib import Path

DBF = Path(r"D:\AgentSense\AgentSense\database.py")
APP = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"

# ① database.py：插在 get_research_report_by_external_key 之后
db_s = io.open(str(DBF), "r", encoding="utf-8", newline="").read()
anchor = "    def list_research_reports_since(self, since: str) -> list[dict]:" + CRLF
method = (
    "    def find_research_report_by_text(self, text: str, exclude_id: int = 0) -> dict | None:" + CRLF
    + '        """按「正文完全相同」查已入库的研报（同一份 PDF 换个文件名重复导入时判重）。' + CRLF
    + "" + CRLF
    + "        【为什么】2026-10-21 用户报障：中信《资金情绪爆发…》在观点总览里出现两条 ——" + CRLF
    + "                同一份 PDF 一次用原始中文名、一次被重命名成 20260927_2.pdf 各入库一遍；" + CRLF
    + "                文件名与来源写法都不同，唯一相同的是正文 ⇒ 只能按正文判重。" + CRLF
    + "        【参数】text: 刚提取出的正文（与 extracted_text 同源，注意入库时会截到 20000 字）；" + CRLF
    + "                exclude_id: 排除自身（重跑同一条时不至于判自己重复）。" + CRLF
    + "        【返回】dict | None: 命中的已有记录。" + CRLF
    + '        """' + CRLF
    + "        if not (text or \"\").strip():" + CRLF
    + "            return None" + CRLF
    + "        with self._conn() as c:" + CRLF
    + "            row = c.execute(" + CRLF
    + "                \"SELECT * FROM research_reports \"" + CRLF
    + "                \"WHERE id != ? AND status IN ('done','summarized','processing','skipped') \"" + CRLF
    + "                \"AND length(extracted_text) = ? AND extracted_text = ? \"" + CRLF
    + "                \"ORDER BY id LIMIT 1\"," + CRLF
    + "                (int(exclude_id), len(text), text)," + CRLF
    + "            ).fetchone()" + CRLF
    + "        return dict(row) if row else None" + CRLF
    + "" + CRLF
)
assert db_s.count(anchor) == 1, "① database 锚点 %d" % db_s.count(anchor)
db_s = db_s.replace(anchor, method + anchor, 1)
assert db_s.count("\n") == db_s.count(CRLF)
io.open(str(DBF), "w", encoding="utf-8", newline="").write(db_s)

# ② web_app.py：文本落库之后、建 LLM 之前判重
app_s = io.open(str(APP), "r", encoding="utf-8", newline="").read()
a_anchor = (
    "        db.update_research_report(" + CRLF
    + '            report_id, status="processing", extracted_text=text[:20000], layout_text=layout_text' + CRLF
    + "        )" + CRLF
)
ins = a_anchor + (
    "" + CRLF
    + "        # 【2026-10-21 入库去重】同一份 PDF 换个文件名再导一次时，正文完全相同 ⇒ 直接跳过，" + CRLF
    + "        #   既不重复烧两次 LLM，也不会在观点总览里出现两条一模一样的研报。" + CRLF
    + "        #   注意比对的是**截断后**的 text[:20000]（入库存的就是这个），否则长文永远判不中。" + CRLF
    + "        _dup = db.find_research_report_by_text(text[:20000], exclude_id=report_id)" + CRLF
    + "        if _dup:" + CRLF
    + "            db.update_research_report(" + CRLF
    + "                report_id, status=\"skipped\"," + CRLF
    + "                error=(\"与已有研报正文完全相同（重复导入），已跳过：\" " + CRLF
    + "                       f\"#{_dup.get('id')}《{(_dup.get('title') or '')[:60]}》\")," + CRLF
    + "            )" + CRLF
    + "            logger.info(" + CRLF
    + "                \"Research report %s is duplicate of %s (identical text), skipped\"," + CRLF
    + "                report_id, _dup.get(\"id\")," + CRLF
    + "            )" + CRLF
    + "            return" + CRLF
)
assert app_s.count(a_anchor) == 1, "② web_app 锚点 %d" % app_s.count(a_anchor)
app_s = app_s.replace(a_anchor, ins, 1)
assert app_s.count("\n") == app_s.count(CRLF)
io.open(str(APP), "w", encoding="utf-8", newline="").write(app_s)

print("OK")
