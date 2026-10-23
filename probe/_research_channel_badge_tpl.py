# -*- coding: utf-8 -*-
"""仅改 web_template.html：公众号筛选选项 + 通道徽标（缩进是 1 空格）。"""
from pathlib import Path

p = Path("web_template.html")
raw = p.read_bytes()
crlf = raw.count(b"\r\n") > 0
t = raw.decode("utf-8").replace("\r\n", "\n")


def sub(old: str, new: str, label: str) -> None:
    global t
    assert t.count(old) == 1, "%s：锚点命中 %d" % (label, t.count(old))
    t = t.replace(old, new, 1)


sub(""" <option value="auto">自动接入</option>
 </select>""",
    """ <option value="auto">自动接入</option>
 <option value="__wechat">公众号（微信读书）</option>
 </select>""",
    "下拉选项")

sub(""" if (v) q.set('variety', v);
 if (src) q.set('ingest_source', src);
 if (rtype) q.set('report_type', rtype);""",
    """ if (v) q.set('variety', v);
 // 【2026-10-19】「公众号」不是入库方式（auto/manual），而是**通道**维度
 //   ⇒ 走独立参数 channel，避免污染 ingest_source 的口径。
 if (src === '__wechat') q.set('channel', 'wechat');
 else if (src) q.set('ingest_source', src);
 if (rtype) q.set('report_type', rtype);""",
    "查询参数")

sub(""" const typeBadge = rp.report_type === '周报'""",
    """ // 通道徽标：公众号（微信读书）入库的 filename 前缀是 wxmp_。
 //   ⚠ 不能用 source 判断 —— 它在接口里被 _normalize_source 归一成机构名了，
 //   「公众号-混沌天成研究」最终显示成「混沌天成研究」，通道信息已丢。
 const chanBadge = String(rp.filename || '').startsWith('wxmp_')
     ? '<span class="badge" style="background:rgba(99,102,241,.16);color:#6366f1;margin-right:6px">公众号</span>'
     : '';
 const typeBadge = rp.report_type === '周报'""",
    "徽标定义")

sub("""${typeBadge}${srcBadge}${statusBadge}""",
    """${typeBadge}${chanBadge}${srcBadge}${statusBadge}""",
    "徽标渲染")

out = t.replace("\n", "\r\n") if crlf else t
p.write_bytes(out.encode("utf-8"))
print("模板已应用 4 处（EOL=%s）" % ("CRLF" if crlf else "LF"))
