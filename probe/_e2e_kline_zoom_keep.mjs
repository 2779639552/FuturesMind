// E2E: 自动刷新时 dataZoom(缩放/拖动)是否被保留 + 双主题截图
//   【为什么不用"固定等 36 秒"】自动刷新的实际发生时刻 = 上一次取数成功 + 30s + 本次请求耗时,
//   与测试脚本的等待时长不同步。固定等待会读到"刷新尚未发生"或"渲染中间态"的中间值,
//   造成假 FAIL(本项目实测踩过)。改为**等真实渲染事件**再断言。
//   [1] 设 dataZoom 20~60 → 等到确有新渲染 → 断言仍是 20~60
//   [2] 暗色/亮色各截一张
// 用法: node probe/_e2e_kline_zoom_keep.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1040 } });
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// 渲染埋点: 计数 + 记录每次渲染的 keepZoom 与前后 dataZoom
await page.evaluate(() => {
  window.__renders = [];
  const orig = App._renderQuantKline.bind(App);
  const readZ = () => {
    const inst = App._chartInstances['chart-q-kline'];
    if (!inst) return null;
    try { const dz = (inst.getOption().dataZoom || [])[0]; return dz ? dz.start : null; } catch (e) { return 'ERR'; }
  };
  App._renderQuantKline = function (d, keepZoom) {
    const before = readZ();
    const r = orig(d, keepZoom);
    window.__renders.push({ t: Date.now(), keepZoom: !!keepZoom, before, after: readZ() });
    return r;
  };
});

await page.evaluate(() => App.switchTab('quant'));
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  if (s) { s.value = '1'; s.dispatchEvent(new Event('change')); }
});
await page.waitForTimeout(14000);

// 手动缩放 + 拖动到 20~60,并记下当前渲染次数
const baseline = await page.evaluate(() => {
  const inst = window.echarts.getInstanceByDom(document.getElementById('chart-q-kline'));
  inst.dispatchAction({ type: 'dataZoom', start: 20, end: 60 });
  return { renders: window.__renders.length, start: inst.getOption().dataZoom[0].start };
});
log(baseline.start === 20, `已设 dataZoom.start=${baseline.start}（基准渲染次数 ${baseline.renders}）`);

console.log('\n[1] 等待一次真实自动刷新后校验缩放');
let refreshed = false;
for (let i = 0; i < 24; i++) {            // 最多等 72s
  await page.waitForTimeout(3000);
  refreshed = await page.evaluate(n => window.__renders.length > n, baseline.renders);
  if (refreshed) break;
}
log(refreshed, '自动刷新确已发生（检测到新的渲染事件）');
await page.waitForTimeout(1500);

const after = await page.evaluate(() => {
  const inst = window.echarts.getInstanceByDom(document.getElementById('chart-q-kline'));
  const dz = inst.getOption().dataZoom[0];
  return { start: dz.start, end: dz.end };
});
const kept = Math.abs(after.start - 20) < 3 && Math.abs(after.end - 60) < 3;
log(kept, `刷新后 dataZoom = ${JSON.stringify(after)}（期望约 20~60）`);

const keepZoomFlag = await page.evaluate(() => {
  const last = window.__renders[window.__renders.length - 1];
  return last && last.keepZoom;
});
log(!!keepZoomFlag, '自动刷新那次渲染的 keepZoom 入参为 true');

console.log('\n[2] 双主题截图');
await page.evaluate(() => App.toggleTheme());
await page.waitForTimeout(1800);
await page.screenshot({ path: 'probe/_shot_kline_autorefresh_dark.png' });
log(true, '已存 probe/_shot_kline_autorefresh_dark.png');
await page.evaluate(() => App.toggleTheme());
await page.waitForTimeout(1800);
await page.screenshot({ path: 'probe/_shot_kline_autorefresh_light.png' });
log(true, '已存 probe/_shot_kline_autorefresh_light.png');

console.log(`\n结果: ${pass} passed, ${fail} failed`);
await browser.close();
process.exit(fail ? 1 : 0);
