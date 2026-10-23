// 观点长文本「显示收敛」的确定性验证（2026-10-16 用户报障「观点被截断」）
//
// 【为什么用构造内容】已验证落盘的历史 md 本身是被截断的（旧代码写的 80 字）⇒
//   页面上现有内容的长度不足以触发 clamp。要验 clamp 逻辑，就**直接构造**长内容
//   再调 App._decorateViewTable —— 这是与数据无关的确定性判据。
//   真实数据的端到端（观点列 849 字、无省略号）已在 probe/_trace_truncation_layers.py 里验过。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 } })).newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e.message).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errs.push('[console] ' + m.text().slice(0, 200)); });

let pass = 0, fail = 0;
const ck = (ok, label, extra) => {
  if (ok) { pass++; console.log('  ✔ ' + label + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★ FAIL ' + label + (extra ? '  [' + extra + ']' : '')); }
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(2500);

// ── 造一个总表结构（含 9 列，其中观点/头寸为长文本）────────────────
const built = await page.evaluate(() => {
  document.querySelectorAll('#__t_probe').forEach(e => e.remove());
  const host = document.createElement('div');
  host.id = '__t_probe';
  host.className = 'report-content';
  const LONG = '综述：LU价差多配看多，单边震荡。' + '；利空：外盘低硫基差走弱（逻辑：外盘现货转弱、库存回升）'.repeat(6)
    + '；风险：油价大幅波动。';                        // 约 250+ 字
  const SHORT = '综述：中性，区间震荡。';
  host.innerHTML = `<table>
    <thead><tr>
      <th>品种</th><th>名称</th><th>发行方</th><th>方向</th><th>置信度</th>
      <th>一句话核心观点</th><th>头寸建议</th><th>当日研报数</th><th>当日有新研报</th>
    </tr></thead>
    <tbody>
      <tr><td>LU</td><td>低硫燃料油</td><td>永安期货</td><td>看多</td><td>58%</td>
          <td>${LONG}</td><td>${LONG}</td><td>1</td><td>✓</td></tr>
      <tr><td>M</td><td>豆粕</td><td>X</td><td>中性</td><td>55%</td>
          <td>${SHORT}</td><td>—</td><td>1</td><td>✓</td></tr>
    </tbody></table>`;
  document.body.appendChild(host);
  App._decorateViewTable(host);
  const rows = host.querySelectorAll('tbody tr');
  const cellsOf = tr => [...tr.children];
  const c0 = cellsOf(rows[0]), c1 = cellsOf(rows[1]);
  return {
    longLen: LONG.length,
    r0view: { cls: c0[5].className, hasClamp: !!c0[5].querySelector('.clamp3'), btn: c0[5].querySelector('.clamp-toggle') ? c0[5].querySelector('.clamp-toggle').textContent.trim() : null },
    r0trade: { cls: c0[6].className, hasClamp: !!c0[6].querySelector('.clamp3'), btn: !!c0[6].querySelector('.clamp-toggle') },
    r1view: { cls: c1[5].className, hasClamp: !!c1[5].querySelector('.clamp3'), btn: !!c1[5].querySelector('.clamp-toggle') },
    r1trade: { cls: c1[6].className, hasClamp: !!c1[6].querySelector('.clamp3'), btn: !!c1[6].querySelector('.clamp-toggle') },
  };
});
console.log('  构造长文本 %d 字' % built.longLen);
console.log('  LU 观点列:', JSON.stringify(built.r0view));
console.log('  LU 头寸列:', JSON.stringify(built.r0trade));
console.log('  M  观点列:', JSON.stringify(built.r1view));

console.log('\n=== A. 长文本列被收敛 ===');
ck(built.r0view.hasClamp, '★ 长观点被包进 .clamp3（默认收 3 行）');
ck(built.r0view.cls.includes('rv-long'), '★ 单元格带 rv-long 类（CSS 生效的前提）');
ck(!!built.r0view.btn, '★ 给了「展开全文」入口（否则用户以为还是被截的）', built.r0view.btn);
ck(built.r0trade.hasClamp, '★ 头寸建议列同样收敛');
ck(built.r0trade.btn, '头寸列也给了展开入口');

console.log('\n=== B. 短内容不动（不误加按钮）===');
ck(!built.r1view.hasClamp && !built.r1view.btn, '短观点（<120 字）不包裹、不加按钮',
  built.r1view.cls || '(无类)');
ck(!built.r1trade.hasClamp && !built.r1trade.btn, '空值/「—」不处理');

console.log('\n=== C. 视觉：确实被裁到 3 行 ===');
const vis = await page.evaluate(() => {
  const host = document.getElementById('__t_probe');
  const td = host.querySelectorAll('tbody tr')[0].children[5];
  const box = td.querySelector('.clamp3');
  const cs = getComputedStyle(box);
  return {
    display: cs.display, clamp: cs.webkitLineClamp || cs['-webkit-line-clamp'],
    boxH: Math.round(box.getBoundingClientRect().height),
    scrollH: Math.round(box.scrollHeight),
    lineH: cs.lineHeight,
  };
});
console.log('  ' + JSON.stringify(vis));
// ⚠ 别断言 display 的具体值：Chrome 把 \ 的**计算值**报成 //   （是盒模型的实现细节、会随版本变），而 line-clamp 实际生效。
//   ⇒ 判据钉**行为**（是否真被裁）而不是钉实现（display 叫什么）。
ck(vis.scrollH > vis.boxH + 4, '★ 内容确实被裁到 3 行（行为判据，不依赖 display 取值）',
  '显示 ' + vis.boxH + 'px < 实际 ' + vis.scrollH + 'px');
ck(String(vis.clamp) === '3', 'line-clamp = 3', String(vis.clamp));
ck(vis.scrollH > vis.boxH + 4, '★ 内容确实被裁（scrollHeight > 显示高度）',
  vis.boxH + ' < ' + vis.scrollH);
ck(vis.boxH <= vis.lineH.replace('px', '') * 3 + 6, '显示高度 ≈ 3 行',
  vis.boxH + 'px vs 3×' + vis.lineH);

console.log('\n=== D. 点击「展开全文」→ 完整可见 ===');
const afterClick = await page.evaluate(async () => {
  const host = document.getElementById('__t_probe');
  const td = host.querySelectorAll('tbody tr')[0].children[5];
  const btn = td.querySelector('.clamp-toggle');
  btn.click();
  await new Promise(r => setTimeout(r, 150));
  const box = td.querySelector('.clamp3');
  const b2 = td.querySelector('.clamp-toggle');
  return {
    open: td.classList.contains('open'),
    boxH: Math.round(box.getBoundingClientRect().height),
    scrollH: Math.round(box.scrollHeight),
    btnTxt: b2.textContent.trim(),
    fullText: box.textContent.trim().length,
  };
});
console.log('  ' + JSON.stringify(afterClick));
ck(afterClick.open, '★ 点击后进入展开态（.open）');
ck(afterClick.boxH >= afterClick.scrollH - 4, '★ 展开后内容完整可见（高度不再受限）',
  afterClick.boxH + ' >= ' + afterClick.scrollH);
ck(afterClick.btnTxt.includes('收起'), '按钮文案切换为「收起」', afterClick.btnTxt);
// ⚠ 用**精确相等**而不是阈值：全文长度必须等于构造时的长度
//   （第一版写 >200 而实际 196，属判据拍错阈值；精确断言还能顺带发现被悄悄裁短）
ck(afterClick.fullText === built.longLen, '★★ 全文一字不少（长度与构造时精确相等）',
  afterClick.fullText + ' vs ' + built.longLen);

console.log('\n=== E. 幂等：重复调用不叠加 ===');
const twice = await page.evaluate(() => {
  const host = document.getElementById('__t_probe');
  const td = host.querySelectorAll('tbody tr')[0].children[5];
  const before = { clamp: td.querySelectorAll('.clamp3').length, btn: td.querySelectorAll('.clamp-toggle').length };
  App._decorateViewTable(host);
  App._decorateViewTable(host);
  const after = { clamp: td.querySelectorAll('.clamp3').length, btn: td.querySelectorAll('.clamp-toggle').length };
  return { before, after };
});
ck(JSON.stringify(twice.before) === JSON.stringify(twice.after), '★ 重复调用不叠加包裹/按钮',
  JSON.stringify(twice.before) + ' → ' + JSON.stringify(twice.after));

console.log('\n=== F. 打印时全部展开（截在 PDF 里等于丢数据）===');
const printCss = await page.evaluate(() => {
  for (const ss of document.styleSheets) {
    try {
      for (const r of ss.cssRules) {
        if (r.type === CSSRule.MEDIA_RULE && r.conditionText && r.conditionText.includes('print')) {
          const txt = r.cssText;
          if (txt.includes('clamp3')) return txt.slice(0, 300);
        }
      }
    } catch (e) { /* 跨域样式表跳过 */ }
  }
  return null;
});
ck(!!printCss && printCss.includes('clamp3'), '★ 打印媒体查询里放开 clamp', printCss ? '已定义' : '★ 缺');
ck(!!printCss && printCss.includes('clamp-toggle'), '打印时隐藏「展开」按钮');

console.log('\n=== G. JS 错误 ===');
ck(errs.length === 0, '无 JS 运行时错误', errs.slice(0, 2).join(' | ') || '0 条');

await page.evaluate(() => { const h = document.getElementById('__t_probe'); if (h) h.scrollIntoView({ block: 'center' }); });
await sleep(600);
await page.screenshot({ path: 'probe/_shot_clamp_demo.png', clip: await page.evaluate(() => {
  const h = document.getElementById('__t_probe');
  const r = h.getBoundingClientRect();
  return { x: Math.max(0, r.x - 6), y: Math.max(0, r.y - 6), width: Math.min(r.width + 12, window.innerWidth - 8), height: Math.min(r.height + 12, 700) };
}) });

console.log('\n' + '='.repeat(58));
console.log('  PASS ' + pass + ' / FAIL ' + fail);
console.log('='.repeat(58));
await B.close();
process.exit(fail ? 1 : 0);
