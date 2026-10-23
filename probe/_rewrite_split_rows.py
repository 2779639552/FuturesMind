# -*- coding: utf-8 -*-
"""重写 _splitSourceRows：用机构名词表作为切分锚点 + 段内也切（2026-10-19 四次反馈）。

【用户反馈】两家之间漏了 <br>，第三家（华泰）粘在第二家（国泰君安）尾部，没有单开一行：
    「…下游采购下滑、己内酰胺累库华泰 看多(68%)—库存未回升…」

【两个真缺陷】
  ① **粘连不触发**：兜底切分只在 `heads < 2` 时跑；而"前两家有 <br>、第二三家粘连"时
     heads 已经是 2 ⇒ 不进兜底 ⇒ 华泰一直挂在国君尾部。
  ② **贪婪 lookahead 切碎机构名**：旧 pattern `[\\u4e00-\\u9fa5A-Za-z]{2,8}` 是贪婪可回溯的，
     「国泰君安 看空(70%)」在「国」「泰」「君安」三处都能匹配 ⇒ 把名称切成三段
     （这正是用户上一轮说的"名称被拆成三行"的另一个来源）。
【修法】
  · 切分锚点改为**机构名词表 + 方向 + (数字%)**（长名优先，避免「国泰」抢在「国泰君安」前）
  · 对**每一段内部**都执行切分（不再只在 heads<2 时兜底）
  · 家数统计也用同一词表，避免子串重复计数
【EOL】web_template.html 为 CRLF。
"""
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 非纯 CRLF"
t = raw.decode("utf-8").replace("\r\n", "\n")

START = "_splitSourceRows(td) {"
END = "// 【功能】把「当日/本周/本季观点总表」的方向列与置信度列转成可视化(2026-10-16 用户要求)。"
i = t.index(START)
j = t.index(END, i)

NEW = r'''_splitSourceRows(td) {
if (!td || td.dataset.cfSplit === '1') return;
const html = td.innerHTML || '';
// `&lt;br>` / `&lt;br&gt;` / `<br>` / `<br/>` 全部视为分隔符
const BR = /(?:&lt;|<)br\s*\/?(?:&gt;|>)/gi;
const plain = s => String(s).replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();

// 【★ 机构名词表 2026-10-19】切分锚点必须用**明确的机构名**，不能靠「2~8 个中文字符」这种
//   模糊通配：贪婪匹配会让「国泰君安 看空(70%)」在「国」「泰」「君安」三处都成立，
//   于是把名称切成三段（用户反馈过"名称被拆成三行"）。词表按**长名在前**排列，
//   保证「国泰君安」先于「国泰」命中。
const SRC = '国泰君安|紫金天风|混沌天成|物产中大|中信期货|南华期货|兴业期货|东海期货'
+ '|国投安信|方正中期|中泰期货|瑞达期货|新湖期货|广发期货|申万期货|宏源期货'
+ '|华泰|永安|东证|中信|隆众|国君|国泰|紫金|混沌|新湖|广发|宏源|申万|国投|方正|中泰|瑞达|南华|兴业|东海';
const DIR = '看多|看空|中性|偏多|偏空|震荡|谨慎';
// 切分点：机构名 + 方向 + (数字%)；lookahead 保留机构名本身，切在它**前面**
const SPLITTER = new RegExp('(?=(?:' + SRC + ')\\s*(?:' + DIR + ')\\s*[(（]\\s*\\d+\\s*%)', 'g');
// 段首判据（允许序号/项目符号前缀：「① 华泰 看多」「1. 华泰 看多」）
const HEAD = new RegExp('^[\\s\u2460-\u2473 0-9.、\\-·]*(?:' + SRC + ')\\s*(?:' + DIR + ')');

// ① 先按 <br> 粗切
let parts = html.split(BR).map(x => x.trim()).filter(Boolean);
// ② ★ 再对**每一段内部**切 —— LLM 会漏写 <br>，把两家粘成一段
//   （旧实现只在 heads<2 时兜底 ⇒ "前两家正常、第二三家粘连"这种永远修不到）
parts = parts.flatMap(x => x.split(SPLITTER).map(s => s.trim()).filter(Boolean));

// ③ 数**真正的家数**：段首是「机构名 + 方向」
const heads = parts.filter(x => HEAD.test(plain(x))).length;
if (heads < 2) {
// 只有一家（或结构不符合"多家"）⇒ 不拆段、**不加分隔线**；
// 但要把 marked 转义出来的字面 <br> 还原成真换行（否则页面上会看到 "<br>" 这几个字符）。
if (parts.length >= 2) {
td.dataset.cfSplit = '1';
td.innerHTML = parts.join('<br>');
}
return;
}
td.dataset.cfSplit = '1';
td.classList.add('cf-cell');
td.innerHTML = parts.map(x => '<div class="cf-row">' + x + '</div>').join('');
},

'''

t = t[:i] + NEW + t[j:]
out = t.replace("\n", "\r\n").encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "出口 EOL 破坏"
P.write_bytes(out)
print("  ✔ 已重写 _splitSourceRows：词表锚点 + 段内也切")
print("  ✔ 删除了原先「只在 heads<2 时兜底」的两处 PAT 逻辑")
