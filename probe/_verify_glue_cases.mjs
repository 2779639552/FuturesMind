import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 用**用户原文**验证：第二、三家之间漏了 <br>，第三家粘在第二家尾部
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 } })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);

const CASES = [
  {
    name: '用户原文：第2、3家粘连（漏写 <br>）',
    html: '东证 看多(58%)—市场预期由震荡转偏多且偏多占优，但缺供需验证<br>国泰君安 看空(77%)—进口增量、国产开工恢复与检修推迟使供应转松，下游采购下滑、己内酰胺累库华泰 看多(68%)—库存未回升、进口到港低，供给偏紧支撑，下游开工回落拖累，',
    want: 3,
  },
  {
    name: '三家全粘连（一个 <br> 都没有）',
    html: '东证 看多(58%)—逻辑A国泰君安 看空(77%)—逻辑B华泰 看多(68%)—逻辑C',
    want: 3,
  },
  {
    name: '单家 + 内部多段（不应拆）',
    html: '国泰君安 中性(55%)—装置停车支撑近月<br>但聚酯负荷下调压制上方空间<br>风险：新装置提前投产',
    want: 0,
  },
  {
    name: '正常两家（有 <br>）',
    html: '华泰 看多(72%)—供应收缩<br>国泰君安 中性(55%)—库存高位',
    want: 2,
  },
];

const results = await page.evaluate((cases) => {
  return cases.map(c => {
    const host = document.createElement('div');
    host.className = 'report-content';
    host.style.cssText = 'position:fixed;left:0;top:0;width:1200px;z-index:99999;background:var(--bg,#fff)';
    host.innerHTML = '<table><thead><tr><th>品种</th><th>分歧状态</th><th>各家观点与逻辑</th><th>分歧焦点</th><th>分歧根源</th></tr></thead>'
      + '<tbody><tr><td>XX</td><td>多空对立</td><td>' + c.html + '</td><td>—</td><td>—</td></tr></tbody></table>';
    document.body.appendChild(host);
    App._decorateConflictTable(host);
    const td = host.querySelector('tbody td:nth-child(3)');
    const rows = [...td.querySelectorAll('.cf-row')].map(d => d.textContent.trim());
    const out = {
      nRows: rows.length,
      rows,
      text: (td.textContent || '').trim(),
      // 机构名是否被切碎：每段开头应能匹配完整机构名
      heads: rows.map(r => (r.match(/^(国泰君安|紫金天风|混沌天成|华泰|永安|东证|中信|国君|国泰[' + ']?)/) || ['?'])[0]),
      hasStrayName: rows.some(r => /^[泰君安]/.test(r)),   // 以"泰/君/安"开头 = 名称被切碎
      literalBr: (td.textContent.match(/<br\s*\/?>/gi) || []).length,
    };
    host.remove();
    return out;
  });
}, CASES);

let pass = 0, fail = 0;
CASES.forEach((c, i) => {
  const r = results[i];
  const ok = r.nRows === c.want;
  const okName = !r.hasStrayName;
  const okBr = r.literalBr === 0;
  console.log('\n  ' + (ok && okName && okBr ? '✓' : '✗') + ' ' + c.name + '  期望 ' + c.want + ' 段 → 实际 ' + r.nRows + ' 段');
  if (r.rows.length) r.rows.forEach((x, k) => console.log('       段' + (k + 1) + ': ' + x.slice(0, 90)));
  if (!okName) console.log('       ★ 机构名被切碎（段首="泰/君/安"）');
  if (!okBr) console.log('       ★ 残留字面 <br> ' + r.literalBr + ' 处');
  if (ok && okName && okBr) pass++; else fail++;
});
console.log('\n  结果: ' + pass + ' PASS / ' + fail + ' FAIL');
await B.close();
process.exit(fail ? 1 : 0);
