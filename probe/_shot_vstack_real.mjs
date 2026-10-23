import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 真实页面验证：周报总表三列纵向对齐 + 截图 + 实测观点列容量
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);

const sel = await page.evaluate(async () => {
  const t = document.getElementById('res-daily-type');
  if (t) { t.value = '周报'; t.dispatchEvent(new Event('change')); }
  await new Promise(r => setTimeout(r, 2600));
  const d = document.getElementById('res-daily-date');
  if (d) { const o = [...d.options].find(x => x.value === '2026-10-11'); if (o) { d.value = '2026-10-11'; d.dispatchEvent(new Event('change')); } }
  await new Promise(r => setTimeout(r, 2600));
  return { type: t ? t.value : null, date: d ? d.value : null };
});
console.log('  口径: ' + JSON.stringify(sel));
await sleep(2200);

const stat = await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('方向') && ths.includes('置信度');
  });
  if (!t) return null;
  t.scrollIntoView({ block: 'center' });
  const trs = [...t.querySelectorAll('tbody tr')];
  const rows = [];
  trs.forEach(tr => {
    const td = [...tr.children];
    const src = [...td[2].querySelectorAll('.src-item')];
    const chips = [...td[3].querySelectorAll('.dir-chip')];
    const bars = [...td[4].querySelectorAll('.conf-bar')];
    if (!src.length) { rows.push({ v: td[0].textContent.trim(), n: 0 }); return; }
    const tops = a => a.map(e => Math.round(e.getBoundingClientRect().top * 10) / 10);
    const A = tops(src), Bq = tops(chips), C = tops(bars);
    const diffs = A.map((a, k) => Math.max(Math.abs(a - (Bq[k] ?? 1e9)), Math.abs(a - (C[k] ?? 1e9))));
    rows.push({ v: td[0].textContent.trim(), n: src.length, maxDiff: Math.round(Math.max(...diffs) * 10) / 10, opLen: td[5].textContent.trim().length });
  });
  const opTd = trs[0].children[5];
  const box = opTd.querySelector('.clamp3');
  const cs = getComputedStyle(opTd);
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font:' + cs.font;
  probe.textContent = '低硫燃料油供应端受配额与检修影响边际收紧';
  document.body.appendChild(probe);
  const charW = probe.getBoundingClientRect().width / probe.textContent.length;
  probe.remove();
  const r = { rows, opW: Math.round(opTd.clientWidth), charW: Math.round(charW * 10) / 10, perLine: Math.floor(opTd.clientWidth / charW) };
  const rect = t.getBoundingClientRect();
  r.clip = { x: Math.max(0, rect.x - 10), y: Math.max(0, rect.y - 52), width: Math.min(rect.width + 20, window.innerWidth - 8), height: Math.min(rect.height + 24, 760) };
  return r;
});
if (!stat) { console.log('  ★ 未找到总表'); process.exit(1); }
console.log('  观点列 ' + stat.opW + 'px，每字 ' + stat.charW + 'px ⇒ 每行约 ' + stat.perLine + ' 字，4 行约 ' + stat.perLine * 4 + ' 字');
const multi = stat.rows.filter(r => r.n > 1);
console.log('  多机构行 ' + multi.length + ' 行，示例: ' + multi.slice(0, 4).map(r => r.v + '(' + r.n + '家,Δ=' + r.maxDiff + 'px)').join(' '));
const bad = multi.filter(r => r.maxDiff > 1.5);
console.log('  对齐检查: ' + (bad.length ? '★ ' + bad.length + ' 行错位' : '全部严格同行 ✓'));
await page.screenshot({ path: 'probe/_shot_vstack_real.png', clip: stat.clip });
console.log('  JS 错误: ' + (errs.length ? errs.join(' | ') : '无'));
await B.close();
