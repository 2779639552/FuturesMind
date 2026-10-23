# -*- coding: utf-8 -*-
"""E2E 的 C 段改成**按接口真实状态自适应**（消除"前提过期"型假红）。

【为什么必须改】C 段原来的前提是"有通道处于中断" —— 那是 2026-10-16 上午的实况。
链路修好后（改走 /web/mp/cover）状态自然变 ok/empty ⇒ 断言仍写死"必须出现「中断」
与修复命令"，就会**每次都假红**，而产品是对的（本项目已多次踩"前提过期"型假红：
索引条那轮 3 条、视图那轮 9 条）。

正解：**按接口返回的真实状态分两条路**，两条路都验"界面与状态一致"：
  · 有 failed ⇒ 必须点名 + 原因 + 可执行修复命令；
  · 无 failed ⇒ **必须不出现**"中断"字样、**不**显示修复命令（否则让人去修没坏的东西）。
后者还顺带端到端验了本次修复的核心：**陈旧失败告警被更晚的成功记录推翻**。
"""
from pathlib import Path

P = Path("probe/_e2e_channel_status.mjs")
s = P.read_text(encoding="utf-8")

OLD = """console.log('\\n=== C. ★ 说到"哪条通道断了"（本次改动的唯一目的）===');
const txt = seen ? seen.text : '';
ck(txt.includes('微信读书'), '点名了中断的通道「微信读书通道」');
ck(txt.includes('中断'), '明确写出「中断」（不是含糊的"没数据"）');
ck(txt.includes('登录') || txt.includes('cookie') || txt.includes('退出码'),
  '给出了失败原因', txt.match(/失败[：:][^。]{0,60}/) ? txt.match(/失败[：:][^。]{0,60}/)[0] : '-');
ck(txt.includes('_weread_login.mjs'), '★ 给出可执行的**修复命令**（用户知道怎么办）');
ck(txt.includes('合集') , '合集通道也在提示范围内（只说一半会让人以为另一条也坏了）');
"""

NEW = """console.log('\\n=== C. ★ 按接口真实状态自适应（本次改动的唯一目的）===');
const txt = seen ? seen.text : '';
// 【★ 为什么要自适应】本段原来的前提是"有通道处于中断"—— 那是 2026-10-16 上午的实况。
//   链路修好后状态自然变 ok/empty ⇒ 写死"必须出现「中断」与修复命令"就会**每次都假红**
//   （产品是对的，判据过期了。本项目已多次踩"前提过期"型假红）。
//   正解：按接口真实状态分两条路，两条路都验"界面与状态一致"。
const apiCh = (api && api.channels) || [];
const failedCh = apiCh.filter(c => c.state === 'failed');
console.log('   接口状态: ' + apiCh.map(c => c.key + '=' + c.state).join(' / ')
  + '   failed=' + failedCh.length);
ck(apiCh.length >= 2, '接口返回两条通道', String(apiCh.length));
ck(txt.includes('合集'), '合集通道也在提示范围内（只说一半会让人以为另一条也坏了）');

if (failedCh.length) {
  // ── 有中断：必须点名 + 说原因 + 给可执行修复命令
  const nm = failedCh[0].name || '';
  ck(txt.includes('微信读书') || (nm && txt.includes(nm.replace('公众号 · ', ''))),
    '点名了中断的通道', nm);
  ck(txt.includes('中断'), '明确写出「中断」（不是含糊的"没数据"）');
  ck(!!failedCh[0].error, '接口给出了失败原因', String(failedCh[0].error).slice(0, 60));
  ck(txt.includes('_weread_login.mjs'), '★ 给出可执行的**修复命令**（用户知道怎么办）');
} else {
  // ── 无中断：界面**必须不**报中断、**不**给修复命令（否则让人去修没坏的东西）
  ck(!txt.includes('中断'), '★ 无通道中断时，界面不应出现「中断」字样',
    txt.includes('中断') ? txt.slice(0, 90) : 'ok ✓');
  ck(!txt.includes('_weread_login.mjs'), '★ 无中断时不应显示修复命令（否则误导用户去重登）');
  ck(apiCh.every(c => c.state === 'ok' || c.state === 'empty'),
    '两条通道状态均为 ok / empty', apiCh.map(c => c.key + '=' + c.state).join('/'));
  // ★ 本轮修复的核心：陈旧失败告警必须被更晚的成功记录推翻
  const wr = apiCh.find(c => c.key === 'weread');
  if (wr && wr.new > 0) {
    ck(wr.state === 'ok',
      '★ 本轮有新文(new=' + wr.new + ') ⇒ 状态应为 ok 而非 failed（陈旧告警已被推翻）',
      'state=' + wr.state);
  }
}
"""

assert s.count(OLD) == 1, ("C 段锚点", s.count(OLD))
s = s.replace(OLD, NEW, 1)
P.write_text(s, encoding="utf-8", newline="")
print("  ✔ E2E 的 C 段已改为自适应")
print("  含 failed 分支:", "if (failedCh.length) {" in s)
print("  含 else 分支:", "} else {" in s and "无中断时不应显示修复命令" in s)
