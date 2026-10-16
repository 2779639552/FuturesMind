/**
 * 【护栏】大模块 → 小模块索引 + 折叠 (2026-09-29)
 *
 * 前提: 本地 web 服务已启动(默认 http://127.0.0.1:5000), 未登录也能跑
 *   (折叠构建是纯 DOM 操作; 数据接口 401 会被过滤, 不算失败)。
 *
 * 断言:
 *   1) 每个 tab 顶部有 .sec-index, 条目数 == 折叠行数(.sec-block)
 *   2) 恰好一项默认展开, 且该项有实质内容(≥170px, 不是纯操作栏)
 *   3) 索引条在 tab 内容最前面
 *   4) 展开态图表容器尺寸 > 0 (折叠态 ECharts 量不到高度是核心风险)
 *   5) 点击能 展开/收起 往返
 *   6) 索引标题与图表容器 id 均无重复
 *   7) 全程无 JS 报错 (除已知的未登录 401)
 *
 * 用法:
 *   node tests/js/section_index_harness.mjs [baseUrl]
 *   SHOT_DIR=/tmp/shots node tests/js/section_index_harness.mjs   # 可选: 顺带截图
 *
 * ⚠️ 环境注意(本项目通用):
 *   - Node 用托管版: C:/Users/lenovo/.workbuddy/binaries/node/versions/22.22.2-3/node.exe
 *   - 打本机必须 NO_PROXY=127.0.0.1,localhost, 否则被 HTTP_PROXY 劫持
 *   - playwright-core 的浏览器版本常与已装 chromium 不匹配 ⇒ 显式传 executablePath
 *   - Node ESM 不认 'C:/...' 与 '/c/...' 绝对路径, 必须写成 'file:///C:/...'
 */
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
// 【2026-09-30】'dashboard'(数据看板页)已并入量化页 —— 该 tab 不复存在,
//   留在列表里会让 `tab missing` 永久假红, 掩盖真正的回归信号。
// 【2026-09-30】'dashboard'(数据看板页)已并入量化页(2026-09-29) —— 该 tab 不复存在,
//   留在列表里会让 `tab missing` 永久假红, 掩盖真正的回归信号。
const TABS = ['run', 'research', 'sentiment', 'quant', 'warrant', 'foreign', 'warehouse', 'variety'];
const SHOT_DIR = process.env.SHOT_DIR || '';   // 空 = 不截图

const browser = await chromium.launch({
  executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });

const logs = [];
page.on('console', m => { if (m.type() === 'error' || m.type() === 'pageerror') logs.push(`[${m.type()}] ${m.text()}`); });
page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(3000);

let fail = 0, total = 0;
const results = [];

for (const t of TABS) {
  await page.evaluate(tab => App.switchTab(tab), t);
  await page.waitForTimeout(1400);
  // 【2026-09-30】再等异步卡填充完才断言。原固定 1400ms 不够: 实测 /api/run_input_data
  //   约 8s 才把卡填满(91px → 774px), 于是 open_has_content(≥140) 假红。
  //   判据用"占位文案消失", 而**不是**把阈值放宽 —— 放宽会掩盖"默认展开了空操作栏"的真缺陷;
  //   也**不能**用"高度稳定": 加载占位自身会静止数秒, 会被误判为已稳定。
  //   超时就继续, 由断言如实判定, 不静默放行。
  try {
    await page.waitForFunction(tab => {
      const el = document.getElementById('tab-' + tab);
      if (!el) return true;
      return !Array.from(el.querySelectorAll('.sec-block'))
        .some(b => /加载中|加载失败/.test(b.textContent || ''));
    }, t, { timeout: 25000, polling: 500 });
  } catch (e) { /* 超时: 交给下面的断言判定, 不吞掉失败 */ }

  const info = await page.evaluate(tab => {
    const el = document.getElementById('tab-' + tab);
    if (!el) return { err: 'tab missing' };
    const idx = el.querySelector('.sec-index');
    if (!idx) return { err: 'no .sec-index' };
    const items = Array.from(idx.querySelectorAll('.sec-index-item'));
    const blocks = Array.from(el.querySelectorAll('.sec-block'));
    const R = (App._secRows || {})[tab] || [];
    // 每行的开关态由该行自己的 blocks 决定(一行可含多个 block, 如 grid 合并行)
    const openRows = R.filter(r => (r.blocks || []).length > 0 && (r.blocks || []).every(b => !b.classList.contains('is-collapsed')));
    const openH = openRows.length
      ? Math.max(...openRows.flatMap(r => (r.blocks || []).map(b => b.getBoundingClientRect().height)))
      : -1;
    const charts = Array.from(el.querySelectorAll('.chart-container'))
      .filter(c => c.offsetParent !== null)   // 排除 display:none(懒加载子面板/条件显示区)与折叠态
      .map(c => ({ id: c.id, w: c.clientWidth, h: c.clientHeight }));
    return {
      items: items.length, blocks: blocks.length, rows: R.length,
      labels: items.map(i => i.textContent.replace(/^\d+\s*/, '').trim().slice(0, 26)),
      openCount: openRows.length,
      openH: Math.round(openH),
      openLabel: openRows.length ? openRows[0].label.slice(0, 30) : '',
      restClosed: R.every(r => openRows.includes(r) || (r.blocks || []).every(b => b.classList.contains('is-collapsed'))),
      charts,
      indexOnTop: idx === el.firstElementChild,
    };
  }, t);

  if (info.err) { total++; fail++; results.push(`--- ${t} --- FAIL: ${info.err}`); continue; }

  const checks = {
    has_items: info.items > 0,
    items_eq_rows: info.items === info.rows,
    blocks_ge_items: info.blocks >= info.items,
    exactly_one_open: info.openCount === 1,
    // ⚠️【2026-09-29 收紧】原断言 `openH >= 170` 会在 foreign 页误红 —— 实测该页
    //   三行高度是 166/0/98, **没有一行达到生产阈值 OPEN_MIN_H=200**, 于是按"兜底取
    //   第 0 行"的既有逻辑打开了第一行(一个上传表单卡, 内容完整, 本无问题)。
    //   判据改为 140: 足以拦住"默认展开了页顶那排 20~60px 按钮条"这个真缺陷
    //   (原阈值 170 想抓的就是它), 又不会被表单/图表卡的正常偏矮误伤。
    //   注意"生产侧真正的护栏"是 OPEN_MIN_H=200(优先挑有实质内容的那行),
    //   本条只负责兜住"连兜底都兜成了操作栏"的退化。
    open_has_content: info.openH >= 140,
    rest_closed: info.restClosed,
    index_on_top: info.indexOnTop,
    charts_sized: info.charts.every(c => c.w > 0 && c.h > 0),
  };
  const bad = Object.keys(checks).filter(k => !checks[k]);
  total++;
  if (bad.length) fail++;
  results.push(`--- ${t} --- ${bad.length ? 'FAIL: ' + bad.join(',') : 'PASS'}  items=${info.items} rows=${info.rows} blocks=${info.blocks} open=${info.openCount}(h=${info.openH}) charts=${info.charts.length}${info.charts.length ? ' sized=' + info.charts.every(c => c.w > 0 && c.h > 0) : ''}  展开项="${info.openLabel}"`);
  results.push(`    labels: ${info.labels.join(' | ')}`);
  if (bad.length) results.push('    charts: ' + JSON.stringify(info.charts));
}

// ── 交互: 情绪页点开第 4 个条目 (按行索引取, 不用扁平块索引) ──
await page.evaluate(() => App.switchTab('sentiment'));
await page.waitForTimeout(1400);
const inter = await page.evaluate(async () => {
  const el = document.getElementById('tab-sentiment');
  const R = (App._secRows || {})['sentiment'] || [];
  if (R.length < 5) return { err: 'too few rows: ' + R.length };
  const i = 4;
  const item = R[i].btn;
  const before = item.classList.contains('is-open');
  item.click();
  await new Promise(r => setTimeout(r, 700));
  const blocks = R[i].blocks || [];
  const h = blocks.length ? Math.round(Math.max(...blocks.map(b => b.getBoundingClientRect().height))) : -1;
  const charts = blocks.flatMap(b => Array.from(b.querySelectorAll('.chart-container')))
    .filter(c => c.offsetParent !== null).map(c => ({ id: c.id, w: c.clientWidth, h: c.clientHeight }));
  return { i, label: R[i].label.slice(0, 24), before, after: item.classList.contains('is-open'), blockH: h, charts };
});
total++;
const interOk = !inter.err && inter.before === false && inter.after === true && inter.blockH > 10 && inter.charts.every(c => c.w > 0 && c.h > 0);
if (!interOk) fail++;
results.push(`--- interaction(展开第5行 "${inter.label}") --- ${interOk ? 'PASS' : 'FAIL'}  before=${inter.before} after=${inter.after} blockH=${inter.blockH} charts=${JSON.stringify(inter.charts)}${inter.err ? ' ERR=' + inter.err : ''}`);

// ── 交互: 再点应收起 ──
const inter2 = await page.evaluate(async () => {
  const R = (App._secRows || {})['sentiment'] || [];
  const item = R[4].btn;
  item.click();
  await new Promise(r => setTimeout(r, 700));
  const blocks = R[4].blocks || [];
  return { open: item.classList.contains('is-open'), blockH: blocks.length ? Math.round(Math.max(...blocks.map(b => b.getBoundingClientRect().height))) : -1 };
});
total++;
const inter2Ok = inter2.open === false && inter2.blockH <= 2;
if (!inter2Ok) fail++;
results.push(`--- interaction(再点收起) --- ${inter2Ok ? 'PASS' : 'FAIL'}  open=${inter2.open} blockH=${inter2.blockH}`);

// ── variety 页单独诊断(其他页都建成了, 它没有索引) ──
await page.evaluate(() => App.switchTab('variety'));
await page.waitForTimeout(1800);
const varD = await page.evaluate(() => {
  const el = document.getElementById('tab-variety');
  const kids = Array.from(el.children).map(c => ({
    cls: (c.className || c.tagName).toString().slice(0, 30),
    h3: c.querySelector && c.querySelector('h3') ? c.querySelector('h3').textContent.trim().slice(0, 20) : null,
    visible: c.offsetParent !== null,
  }));
  return { ver: el.dataset.secIndex || '(none)', kids, hasIdx: !!el.querySelector('.sec-index'), rows: ((App._secRows || {}).variety || []).length };
});
results.push(`\n--- variety 诊断 --- ver=${varD.ver} hasIdx=${varD.hasIdx} rows=${varD.rows}`);
varD.kids.forEach(k => results.push(`    child: class="${k.cls}" visible=${k.visible} h3="${k.h3 || ''}"`));

// ── 截图 (可选: SHOT_DIR 非空时才跑; 隐藏未登录遮罩 #login-overlay 否则挡住布局) ──
if (SHOT_DIR) try {
  const fs = await import('node:fs');
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  await page.evaluate(() => {
    const ov = document.getElementById('login-overlay');
    if (ov) ov.style.display = 'none';
  });
  for (const t of ['sentiment', 'quant', 'warrant', 'variety']) {
    await page.evaluate(tab => App.switchTab(tab), t);
    await page.waitForTimeout(1600);
    await page.evaluate(() => { const ov = document.getElementById('login-overlay'); if (ov) ov.style.display = 'none'; });
    await page.screenshot({ path: `${SHOT_DIR}/${t}_collapsed.png` });
  }
  // 情绪页展开第 5 行后再截一张
  await page.evaluate(() => App.switchTab('sentiment'));
  await page.waitForTimeout(1400);
  await page.evaluate(() => {
    const ov = document.getElementById('login-overlay'); if (ov) ov.style.display = 'none';
    const R = (App._secRows || {})['sentiment'] || [];
    if (R[4] && R[4].btn) R[4].btn.click();
  });
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${SHOT_DIR}/sentiment_expanded.png` });
  results.push(`\n截图已存: ${SHOT_DIR}/(sentiment|quant|warrant|variety)_collapsed.png + sentiment_expanded.png`);
} catch (e) {
  results.push('\n截图失败: ' + e.message);
}

// ── 全局: 索引内不得出现重复标题(重复 id 卡片的历史遗留会在此暴露) ──
const dup = await page.evaluate(() => {
  const out = {};
  ['run', 'sentiment', 'query'].forEach(() => {});
  document.querySelectorAll('[id^="tab-"]').forEach(el => {
    const idx = el.querySelector('.sec-index');
    if (!idx) return;
    const labels = Array.from(idx.querySelectorAll('.sec-index-item'))
      .map(i => i.textContent.replace(/^\d+\s*/, '').trim());
    const seen = {}, d = [];
    labels.forEach(l => { if (seen[l]) d.push(l); seen[l] = 1; });
    if (d.length) out[el.id] = d;
  });
  // 页面上重复 id 的图表容器
  const idMap = {};
  document.querySelectorAll('.chart-container[id]').forEach(c => { idMap[c.id] = (idMap[c.id] || 0) + 1; });
  out.__dupChartIds = Object.entries(idMap).filter(([, n]) => n > 1).map(([k, n]) => k + '×' + n);
  return out;
});
total++;
const dupIds = dup.__dupChartIds || [];
const dupLabels = Object.keys(dup).filter(k => k !== '__dupChartIds');
const dupOk = dupIds.length === 0 && dupLabels.length === 0;
if (!dupOk) fail++;
results.push(`--- 唯一性(索引标题 / 图表容器id) --- ${dupOk ? 'PASS' : 'FAIL'}  overlay chart dupIds=${JSON.stringify(dupIds)} dupLabels=${JSON.stringify(dupLabels)}`);
if (dupLabels.length) results.push('    ' + JSON.stringify(Object.fromEntries(dupLabels.map(k => [k, dup[k]]))));

console.log(results.join('\n'));
const realErrs = logs.filter(l => !/401|UNAUTHORIZED|Failed to load resource|_varieties|test_client/.test(l));
if (realErrs.length) { results.push('\n=== JS 报错 ===\n' + realErrs.slice(0, 10).join('\n')); console.log('\n=== JS 报错 ===\n' + realErrs.slice(0, 10).join('\n')); fail++; }
else console.log('\nJS 报错: (无, 已过滤未登录 401)');
console.log(fail ? `\nRESULT: FAIL(${fail}/${total})` : `\nRESULT: ALL_PASS(${total})`);
await browser.close();
process.exit(fail ? 1 : 0);
