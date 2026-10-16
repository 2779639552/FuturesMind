# -*- coding: utf-8 -*-
"""给 probe/_e2e_page_tools.mjs 的 [9] 段追加「三卡合并」现场断言（LF 文件）。
"""
P = r"D:\AgentSense\AgentSense\probe\_e2e_page_tools.mjs"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src, "该文件应为 LF"

ANCHOR = "console.log(`\\nJS 报错: ${errs.length ? errs.slice(0, 3).join(' | ') : '(无)'}`);\n"
assert src.count(ANCHOR) == 1, "锚点不唯一: %d" % src.count(ANCHOR)

ADD = '''
// ── 9) 国君页三卡合并: 总览/走势图/明细 同卡 + 点「查看」就地出数 ────
// 【为什么单列一段】④⑤ 原本各自成卡且只由 ③ 的「查看」驱动 ⇒ 点一次要靠
//   _gtEnsureVisible 展开下面两块, 三块还各占一条索引项。合并后应满足:
//   三容器同卡 / 卡内只有一个 h3 / 点「查看」后图表非 0×0 且明细出数。
console.log('\\n[9] 国君页三卡合并(同卡 + 就地出数)');
{
  await page.evaluate(() => App.switchTab('gtja'));
  await page.waitForTimeout(1600);
  // 等指标总览出数
  for (let i = 0; i < 40; i++) {
    const ok = await page.evaluate(() => {
      const el = document.getElementById('gt-indicators');
      return !!el && el.querySelectorAll('tbody tr').length > 0;
    });
    if (ok) break;
    await page.waitForTimeout(2000);
  }
  const st = await page.evaluate(() => {
    const ind = document.getElementById('gt-indicators');
    const chart = document.getElementById('gt-chart');
    const tbl = document.getElementById('gt-table');
    const card = ind ? ind.closest('.card') : null;
    const blk = card ? card.closest('.sec-block') : null;
    if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
    const labels = Array.from(document.querySelectorAll('#tab-gtja .sec-index-list .sec-index-item'))
      .map(b => (b.textContent || '').replace('✓', '').trim());
    return {
      sameCard: !!card && !!chart && !!tbl && chart.closest('.card') === card && tbl.closest('.card') === card,
      h3n: card ? card.querySelectorAll('h3').length : -1,
      blocks: document.querySelectorAll('#tab-gtja > .sec-block').length,
      labels,
      rows: ind ? ind.querySelectorAll('tbody tr').length : 0,
    };
  });
  log(st.sameCard, '总览表/走势图/明细 同在①张卡内 (不再跨卡)');
  log(st.h3n === 1, `合并卡内 h3 数 = ${st.h3n} (期望 1, 防索引串名)`);
  log(st.blocks === 3, `国君页小模块数 = ${st.blocks} (期望 3, 原为 5)`);
  log(!/走势图|明细数据/.test(st.labels.slice(2).join('')),
      `索引不再出现「走势图」「明细数据」独立项: [${st.labels.join(' | ')}]`);

  // 点第一行「查看」→ 就地出图出表
  await page.evaluate(() => {
    const b = document.querySelector('#gt-indicators tbody tr button');
    if (b) b.click();
  });
  await page.waitForTimeout(900);
  const a = await page.evaluate(() => {
    const c = document.getElementById('gt-chart');
    const cv = c ? c.querySelector('canvas') : null;
    const tbl = document.getElementById('gt-table');
    const cb = c ? c.getBoundingClientRect() : null;
    const tb = tbl ? tbl.getBoundingClientRect() : null;
    const ib = document.getElementById('gt-indicators').getBoundingClientRect();
    return {
      cw: cv ? Math.round(cv.getBoundingClientRect().width) : 0,
      ch: cv ? Math.round(cv.getBoundingClientRect().height) : 0,
      badge: (document.getElementById('gt-chart-badge') || {}).textContent || '',
      tRows: tbl ? tbl.querySelectorAll('tbody tr').length : 0,
      tBadge: (document.getElementById('gt-tbl-badge') || {}).textContent || '',
      chartBelowTable: cb && cb.top > ib.top,
      gap: (cb && tb) ? Math.round(tb.top - cb.bottom) : -1,
    };
  });
  log(a.cw > 100 && a.ch > 100, `★走势图就地渲染且非 0×0 (${a.cw}×${a.ch}px)`);
  log(!!a.badge, `图表徽标带指标名: "${a.badge}"`);
  log(a.tRows > 0 && a.tBadge === `${a.tRows} 行`, `明细表就地出 ${a.tRows} 行 (徽标="${a.tBadge}")`);
  log(a.chartBelowTable, '走势图紧接总览表之下(视线连贯, 无需跳卡)');
  log(Math.abs(a.gap) < 200, `图表与明细相邻 (间隔 ${a.gap}px)`);
  log(!errs.length, '本段无 JS 报错');
}
'''

src = src.replace(ANCHOR, ADD + "\n" + ANCHOR, 1)
out = src.encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
