// 验收：研报页周期下拉已移除；观点总览只取最新（请求不带 date / report_type）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, m, e = '') => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${m}${e ? '  | ' + e : ''}`); };

const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [], viewReqs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
// 绝不触发真实 LLM 生成
await p.route('**/api/research/variety-summary/generate', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":false,"error":"blocked by probe"}' }));
await p.on('request', r => { if (r.url().includes('/api/research/views?')) viewReqs.push(r.url()); });

await p.goto(BASE, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(2000);
const th = await p.evaluate(() => document.documentElement.getAttribute('data-theme'));
if (th === 'dark') { await p.click('button.theme-toggle'); await p.waitForTimeout(500); }
await p.click('nav button[data-tab="research"]');
await p.waitForTimeout(5000);

const gone = await p.evaluate(() => {
  const ids = ['res-daily-type', 'res-vsum-type', 'res-views-type', 'res-views-date',
               'res-daily-quarter', 'res-vsum-quarter', 'res-views-quarter'];
  return ids.filter(i => document.getElementById(i));
});
log(gone.length === 0, 'A1 周期/日期下拉已全部移除', gone.join(',') || '无残留');

const labels = await p.evaluate(() =>
  [...document.querySelectorAll('#tab-research label')].map(e => e.textContent.trim()));
log(!labels.includes('研报日期') && !labels.includes('类型(分开统计)'), 'A2 两个标签块也已移除', labels.join('|'));

const info = await p.evaluate(() => {
  const t = document.getElementById('res-views');
  const n = document.getElementById('res-views-note');
  const c = document.getElementById('res-views-count');
  return { txt: (t ? t.innerText : '').slice(0, 120), note: n ? n.innerText : '', count: c ? c.innerText : '' };
});
log(info.txt && !info.txt.includes('加载中'), 'B1 观点总览已渲染', info.txt.slice(0, 50));
log(!info.note.includes('类型') && !info.note.includes('仅'), 'B2 说明里不再提类型筛选', info.note.slice(0, 60));

const lastReq = viewReqs[viewReqs.length - 1] || '';
log(!!lastReq && !lastReq.includes('date=') && !lastReq.includes('report_type='),
    'B3 请求只带 variety（不带 date / report_type）', decodeURIComponent(lastReq).slice(-70));

const dateShown = (info.note.match(/研报日期\s*([0-9-]{10})/) || [])[1] || '';
log(!!dateShown, 'B4 页面显示的是最新日期', dateShown || info.note.slice(0, 50));

log(errs.length === 0, 'C1 无 JS 错误', errs.join(';'));
await p.screenshot({ path: 'probe/_research_no_period.png' });
await b.close();
console.log(`\n===== ${pass} PASS / ${fail} FAIL =====`);
process.exit(fail ? 1 : 0);
