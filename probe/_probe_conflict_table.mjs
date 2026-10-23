import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 探针：第二节「观点对比与冲突」的真实 DOM —— 列宽、<br> 是否保留、单元格实际行数
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);
await page.evaluate(async () => {
  const t = document.getElementById('res-daily-type');
  if (t) { t.value = '周报'; t.dispatchEvent(new Event('change')); }
  await new Promise(r => setTimeout(r, 2600));
  const d = document.getElementById('res-daily-date');
  if (d) { const o = [...d.options].find(x => x.value === '2026-10-11'); if (o) { d.value = '2026-10-11'; d.dispatchEvent(new Event('change')); } }
  await new Promise(r => setTimeout(r, 2600));
});
await sleep(2500);

const info = await page.evaluate(() => {
  const tbl = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('分歧状态') && ths.includes('各家观点与逻辑');
  });
  if (!tbl) return null;
  const ths = [...tbl.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
  const rows = [...tbl.querySelectorAll('tbody tr')];
  const widths = [...tbl.querySelectorAll('thead th')].map(h => Math.round(h.getBoundingClientRect().width));
  const first = rows[0];
  const cells = [...first.children];
  const cellInfo = cells.map((td, k) => {
    const r = td.getBoundingClientRect();
    const cs = getComputedStyle(td);
    return {
      k, head: ths[k], w: Math.round(r.width), h: Math.round(r.height),
      brs: td.querySelectorAll('br').length,
      html: td.innerHTML.slice(0, 200),
      // 文本按 <br> 拆出的段数（若被折行，视觉行数由布局决定，需另测）
      segs: (td.innerHTML.split(/<br\s*\/?>/i).length),
      lineHeight: Math.round(parseFloat(cs.lineHeight)),
    };
  });
  const rect = tbl.getBoundingClientRect();
  return {
    ths, widths, cellInfo,
    tblW: Math.round(rect.width),
    rows: rows.length,
    clip: { x: Math.max(0, rect.x - 10), y: Math.max(0, rect.y - 50), width: Math.min(rect.width + 20, window.innerWidth - 8), height: Math.min(rect.height + 20, 760) },
  };
});

if (!info) { console.log('  ★ 未找到第二节表格'); await B.close(); process.exit(1); }
console.log('  表宽 ' + info.tblW + 'px, ' + info.rows + ' 行');
console.log('  列: ' + info.ths.map((h, k) => h + '=' + info.widths[k] + 'px').join(' | '));
console.log();
const c3 = info.cellInfo[2];
console.log('  【各家观点与逻辑】宽 ' + c3.w + 'px 高 ' + c3.h + 'px, <br> 数量 ' + c3.brs + ', 段数 ' + c3.segs + ', lineHeight ' + c3.lineHeight);
console.log('  首行 HTML 片段:');
console.log('    ' + c3.html.replace(/</g, '&lt;').slice(0, 260));
await page.screenshot({ path: 'probe/_shot_conflict_before.png', clip: info.clip });
await B.close();
