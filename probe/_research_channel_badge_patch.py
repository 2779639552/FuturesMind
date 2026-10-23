# -*- coding: utf-8 -*-
"""研报库增加「公众号」通道标识与筛选（2026-10-19）。

背景：
  · 公众号入库的 `source` 写成 `公众号-<号名>`，但 `/api/research` 用
    `_normalize_source()` 归一成机构名（混沌天成/中信期货）⇒ 界面上**认不出**来源通道。
  · 来源下拉只有 manual/auto 两个「入库方式」，没有「通道」维度。
  · `filename` 形如 `wxmp_<originalId>.md` 是通道的可靠判据（不依赖被归一过的 source）。

改动：
  1. database.list_research_reports 增加 `filename_prefix` 过滤（SQL 层面，先过滤再 LIMIT）。
  2. api_research 增加 `channel=wechat` ⇒ filename_prefix='wxmp_'.
  3. 前端：来源下拉加「公众号（微信读书）」选项（走 channel 参数）+ 列表加「公众号」徽标。
"""
from pathlib import Path

FILES = {}


def load(p: str) -> str:
    raw = Path(p).read_bytes()
    crlf = raw.count(b"\r\n")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    FILES[p] = ("crlf" if crlf > 0 else "lf", text)
    return text


def save(p: str, text: str) -> None:
    eol, _ = FILES[p]
    out = text.replace("\n", "\r\n") if eol == "crlf" else text
    Path(p).write_bytes(out.encode("utf-8"))


def sub(text: str, old: str, new: str, label: str) -> str:
    assert text.count(old) == 1, "%s：锚点命中 %d 次" % (label, text.count(old))
    return text.replace(old, new, 1)


# ═══════════════════════════════ ① database.py
t = load("database.py")
t = sub(t,
        """        ingest_source: str | None = None,
        report_type: str | None = None,
    ) -> list[dict]:""",
        """        ingest_source: str | None = None,
        report_type: str | None = None,
        filename_prefix: str | None = None,
    ) -> list[dict]:""",
        "① signature")
t = sub(t,
        """        if report_type:
            where.append("report_type = ?")
            params.append(report_type)""",
        """        if report_type:
            where.append("report_type = ?")
            params.append(report_type)
        if filename_prefix:
            # 【2026-10-19】按**通道**过滤：公众号入库的 filename 形如 `wxmp_<originalId>.md`。
            # 必须放在 SQL 层（先过滤再 LIMIT），否则最近 50 条里命中率低时会被整片挤掉。
            where.append("filename LIKE ?")
            params.append(filename_prefix + "%")""",
        "① where")
save("database.py", t)

# ═══════════════════════════════ ② web_app.py
t = load("web_app.py")
t = sub(t,
        """    report_type = (request.args.get("report_type") or "").strip() or None
    rows = get_db().list_research_reports(
        variety or None, ingest_source=ingest_source, report_type=report_type
    )""",
        """    report_type = (request.args.get("report_type") or "").strip() or None
    # 【2026-10-19】通道维度：channel=wechat ⇒ 只看公众号（微信读书）入库的研报。
    #   为什么不复用 ingest_source：那是「入库方式」(manual/auto)，公众号既有自动也有手工补录；
    #   而用户要的是「来源通道」，用 filename 前缀 `wxmp_` 判定最可靠
    #   （source 会被 _normalize_source 归一成机构名，认不出通道）。
    channel = (request.args.get("channel") or "").strip().lower()
    prefix = "wxmp_" if channel == "wechat" else None
    rows = get_db().list_research_reports(
        variety or None, ingest_source=ingest_source, report_type=report_type,
        filename_prefix=prefix,
    )""",
        "② api_research")
save("web_app.py", t)

# ═══════════════════════════════ ③ web_template.html
t = load("web_template.html")
t = sub(t,
        """ <option value="auto">自动接入</option>
 </select>""",
        """ <option value="auto">自动接入</option>
 <option value="__wechat">公众号（微信读书）</option>
 </select>""",
        "③ 下拉选项")
t = sub(t,
        """        if (v) q.set('variety', v);
        if (src) q.set('ingest_source', src);
        if (rtype) q.set('report_type', rtype);""",
        """        if (v) q.set('variety', v);
        // 【2026-10-19】「公众号」不是入库方式（auto/manual），而是**通道**维度
        //   ⇒ 走独立参数 channel，避免污染 ingest_source 的口径。
        if (src === '__wechat') q.set('channel', 'wechat');
        else if (src) q.set('ingest_source', src);
        if (rtype) q.set('report_type', rtype);""",
        "③ 查询参数")
t = sub(t,
        """        const typeBadge = rp.report_type === '周报'""",
        """        // 通道徽标：公众号（微信读书）入库的 filename 前缀是 wxmp_。
        //   ⚠ 不能用 source 判断 —— 它在接口里被 _normalize_source 归一成机构名了，
        //   「公众号-混沌天成研究」最终会显示成「混沌天成研究」，通道信息已丢。
        const chanBadge = String(rp.filename || '').startsWith('wxmp_')
            ? '<span class="badge" style="background:rgba(99,102,241,.16);color:#6366f1;margin-right:6px">公众号</span>'
            : '';
        const typeBadge = rp.report_type === '周报'""",
        "③ 徽标定义")
t = sub(t,
        """${typeBadge}${srcBadge}${statusBadge}""",
        """${typeBadge}${chanBadge}${srcBadge}${statusBadge}""",
        "③ 徽标渲染")
save("web_template.html", t)

print("已应用 6 处：")
for p, (eol, _) in FILES.items():
    print("  · %-22s (%s)" % (p, eol.upper()))
