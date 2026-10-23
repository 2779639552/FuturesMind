// E2E 验收: 数据仓库「明细数据」折叠栏(2026-10-19)
// 用法: node probe/_e2e_gtja_detail_collapse.mjs [BASE]
// 覆盖:
//   A 进页面(自动渲染第一个指标)时明细仍是**收起**的
//   B 点指标行「查看」后才下拉展开, 且表里有行
//   C 点表头可收回(同一状态机)
//   D 展开/收起不影响上面的季节性图(canvas 仍在、高度不变)
//   E 无 JS 错误 + 截图
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg, extra = '') => {
  ok ? pass++ : fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}${extra ? '  | ' + extra : ''}`);
};

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(String(e.message).slice(0, 160)));

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2200);
await page.click('button[data-tab="warehouse"]');
await page.waitForTimeout(3500);   // 等 overview + 自动渲染第一个指标

const st = () => page.evaluate(() => {
  const ex = document.getElementById('gt-detail-expander');
  const tbl = document.getElementById('gt-table');
  const cv = document.querySelector('#gt-chart canvas');
  const badge = document.getElementById('gt-tbl-badge');
  return {
    open: !!ex && ex.classList.contains('open'),
    tblH: tbl ? tbl.getBoundingClientRect().height : -1,
    tblRows: tbl ? tbl.querySelectorAll('tbody tr').length : 0,
    badge: badge ? badge.textContent : '',
    chartH: cv ? cv.clientHeight : 0,
    chartW: cv ? cv.clientWidth : 0,
  };
});

const s1 = await st();
log(!s1.open, 'A1 进页面时明细栏是收起的', JSON.stringify({ open: s1.open, tblH: s1.tblH }));
log(s1.tblH < 5, 'A2 收起态表格高度为 0(不是"藏了但仍占位")', `tblH=${s1.tblH}`);
log(s1.badge.includes('行'), 'A3 收起态表头仍显示行数, 用户知道后面有数据', `badge=${s1.badge}`);
log(s1.chartH > 100 && s1.chartW > 100, 'A4 季节性图正常', `${s1.chartW}×${s1.chartH}`);

// 点第 3 个指标的「查看」(避开自动渲染的 0 号, 确保是"人点的")
const clicked = await page.evaluate(() => {
  const btns = [...document.querySelectorAll('#gt-indicators button')];
  if (btns.length < 3) return false;
  btns[2].click();
  return true;
});
await page.waitForTimeout(1200);
const s2 = await st();
log(clicked, 'B0 找到并点击了第 3 个指标的「查看」');
log(s2.open, 'B1 ★点「查看」后明细下拉展开', `open=${s2.open}`);
log(s2.tblH > 40, 'B2 展开后表格真的显示出来', `tblH=${s2.tblH}`);
log(s2.tblRows > 1, 'B3 表格有数据行', `rows=${s2.tblRows}`);
log(s2.chartH === s1.chartH, 'D1 展开明细不影响季节性图高度', `${s1.chartH}→${s2.chartH}`);

// 点表头收回
await page.click('#gt-detail-expander .expander-header');
await page.waitForTimeout(600);
const s3 = await st();
log(!s3.open && s3.tblH < 5, 'C1 点表头可收回(与按钮共用同一状态机)', `open=${s3.open} tblH=${s3.tblH}`);

// 再点开, 用于截图
await page.click('#gt-detail-expander .expander-header');
await page.waitForTimeout(500);
const box = await page.evaluate(() => {
  const r = document.getElementById('gt-detail-expander').getBoundingClientRect();
  return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.min(1600, r.width), height: Math.min(1000, r.height) };
});
await page.screenshot({ path: 'probe/_shot_gtja_detail_open.png', clip: box });

log(errs.length === 0, 'E1 页面无 JS 错误', errs.join(';'));
await browser.close();
console.log(`\n===== 汇总: ${pass} PASS / ${fail} FAIL =====`);
process.exit(fail ? 1 : 0);
