// E2E 验收: 数据仓库 → 卖方数据 → 季节性图(2026-10-19)
// 用法: node probe/_e2e_gtja_seasonal.mjs [BASE]
// 覆盖:
//   A 容器高度减半(170px, 原 340px) 且 canvas 真的按 170 渲染
//   B ★ 多年历史到位: 图例/曲线数 ≥ 2 年(不是只画当前窗口的单年)
//   C ★ 当年为粗红线、历史年为细线且半透明(绘制包口径)
//   D x 轴为 366 天类目 + 12 个月份标签(1月~12月)
//   E 最新点有标注、脚注写明年份跨度
//   F 双主题截图 + 无 JS 错误
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
await page.waitForTimeout(2500);
await page.click('button[data-tab="warehouse"]');

// 等季节性数据补齐(脚注出现"季节性对比"), 最多 90s(基差冷启动要分片请求)
let noteText = '';
for (let i = 0; i < 45; i++) {
  await page.waitForTimeout(2000);
  noteText = await page.evaluate(() => {
    const el = document.getElementById('gt-chart-note');
    return el ? el.textContent : '';
  });
  if (noteText.includes('季节性对比')) break;
}

const st = await page.evaluate(() => {
  const box = document.getElementById('gt-chart');
  const cv = box.querySelector('canvas');
  const inst = window.echarts ? window.echarts.getInstanceByDom(box) : null;
  const opt = inst ? inst.getOption() : null;
  const pick = s => ({
    name: s.name,
    color: s.lineStyle && s.lineStyle.color,
    width: s.lineStyle && s.lineStyle.width,
    opacity: s.lineStyle && s.lineStyle.opacity,
    pts: (s.data || []).length,
    label: s.markPoint && s.markPoint.label
      ? (typeof s.markPoint.label.formatter === 'function'
        ? s.markPoint.label.formatter() : s.markPoint.label.formatter)
      : '',
  });
  return {
    boxH: box.clientHeight,
    inlineH: box.style.height,
    canvasH: cv ? cv.clientHeight : 0,
    canvasW: cv ? cv.clientWidth : 0,
    xLen: opt && opt.xAxis && opt.xAxis[0] ? (opt.xAxis[0].data || []).length : 0,
    xFirst: opt && opt.xAxis && opt.xAxis[0] ? (opt.xAxis[0].data || [])[0] : '',
    xFeb: opt && opt.xAxis && opt.xAxis[0] ? (opt.xAxis[0].data || [])[31] : '',
    xDec: opt && opt.xAxis && opt.xAxis[0] ? (opt.xAxis[0].data || [])[334] : '',
    legend: opt && opt.legend && opt.legend[0] ? (opt.legend[0].data || []) : [],
    series: opt ? (opt.series || []).map(pick) : [],
  };
});

console.log('  渲染状态:', JSON.stringify({
  boxH: st.boxH, canvasH: st.canvasH, canvasW: st.canvasW,
  xLen: st.xLen, legend: st.legend,
  series: st.series.map(s => `${s.name}:${s.color}/${s.width}/${s.opacity}/${s.pts}`),
}, null, 0));

log(st.boxH === 170 && st.inlineH === '170px', 'A1 容器高度 = 170px(原 340 的一半)',
  `boxH=${st.boxH} inline=${st.inlineH}`);
log(st.canvasH >= 160 && st.canvasH <= 172, 'A2 canvas 按 170 渲染(未被 320px 兜底顶回去)',
  `canvasH=${st.canvasH}`);
log(st.legend.length >= 2, '★B1 多年历史到位(图例 ≥ 2 年)', `legend=${st.legend.join(',')}`);
log(st.series.length >= 2, '★B2 每年一条线', `series=${st.series.length}`);
const cur = st.series[st.series.length - 1];
const his = st.series.slice(0, -1);
log(!!cur && cur.color === '#FF0000' && cur.width === 2.4 && cur.opacity === 1,
  '★C1 当年粗红线', `${cur && cur.color}/${cur && cur.width}/${cur && cur.opacity}`);
log(his.length > 0 && his.every(s => s.width === 1.2 && s.opacity === 0.6),
  '★C2 历史年细线 + alpha 0.6', his.map(s => `${s.width}/${s.opacity}`).join(' '));
log(st.xLen === 366 && st.xFirst === '1月' && st.xFeb === '2月' && st.xDec === '12月',
  'D1 x 轴 366 天类目 + 月份标签', `${st.xLen} [${st.xFirst}|${st.xFeb}|${st.xDec}]`);
log(!!cur && /\(\+?-?\d/.test(cur.label || ''), 'E1 最新点标注「值 (±变化)」', cur && cur.label);
log(noteText.includes('季节性对比') && /\d{4}–\d{4}/.test(noteText),
  'E2 脚注写明年份跨度', noteText.slice(0, 90));
log(errs.length === 0, 'F1 页面无 JS 错误', errs.join(';'));

// 截图(亮/暗)
await page.evaluate(() => document.getElementById('gt-chart').scrollIntoView({ block: 'center' }));
await page.waitForTimeout(400);
await page.screenshot({ path: 'probe/_shot_gtja_seasonal_light.png', clip: await page.evaluate(() => {
  const r = document.getElementById('gt-chart').closest('.card').getBoundingClientRect();
  return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.min(1600, r.width), height: Math.min(1000, r.height) };
}) });
await page.evaluate(() => {
  const b = document.getElementById('theme-toggle');
  if (b) b.click();
});
await page.waitForTimeout(900);
await page.screenshot({ path: 'probe/_shot_gtja_seasonal_dark.png', clip: await page.evaluate(() => {
  const r = document.getElementById('gt-chart').closest('.card').getBoundingClientRect();
  return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.min(1600, r.width), height: Math.min(1000, r.height) };
}) });
log(true, 'F2 双主题截图已保存 probe/_shot_gtja_seasonal_{light,dark}.png');

await browser.close();
console.log(`\n===== 汇总: ${pass} PASS / ${fail} FAIL =====`);
process.exit(fail ? 1 : 0);
