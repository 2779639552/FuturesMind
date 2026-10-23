import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 真实数据全量体检：第二节每一行的「段数」是否等于「机构家数」，以及机构名是否完整
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1200 } })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));

const VIEWS = [
  { type: '周报', date: null },
  { type: '', date: null },
  { type: '季报', date: null },
];
for (const v of VIEWS) {
  await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
  await page.click('nav button[data-tab="research"]');
  await sleep(3000);
  const info = await page.evaluate(async (want) => {
    const t = document.getElementById('res-daily-type');
    if (t) { t.value = want.type; t.dispatchEvent(new Event('change')); }
    await new Promise(r => setTimeout(r, 2600));
    const d = document.getElementById('res-daily-date');
    return { type: t ? t.value : null, date: d ? d.value : null };
  }, v);
  await sleep(2000);

  const report = await page.evaluate(() => {
    const tbl = [...document.querySelectorAll('.report-content table')].find(x => {
      const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
      return ths.includes('分歧状态') && ths.includes('各家观点与逻辑');
    });
    if (!tbl) return { found: false };
    const ths = [...tbl.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    const iLog = ths.indexOf('各家观点与逻辑');
    const SRC = '国泰君安|紫金天风|混沌天成|物产中大|中信期货|南华期货|兴业期货|东海期货|国投安信|方正中期|中泰期货|瑞达期货|新湖期货|广发期货|申万期货|宏源期货|华泰|永安|东证|中信|隆众|国君|国泰|紫金|混沌|新湖|广发|宏源|申万|国投|方正|中泰|瑞达|南华|兴业|东海';
    const DIR = '看多|看空|中性|偏多|偏空|震荡|谨慎';
    const HEAD = new RegExp('(?:' + SRC + ')\\s*(?:' + DIR + ')', 'g');
    const bad = [];
    [...tbl.querySelectorAll('tbody tr')].forEach(tr => {
      const td = tr.children[iLog];
      if (!td) return;
      const txt = (td.textContent || '').trim();
      const rows = [...td.querySelectorAll('.cf-row')];
      const nHeads = (txt.match(HEAD) || []).length;
      // 段数：已拆的按 cf-row 数，未拆的按 <br> 段数
      const nSeg = rows.length || txt.split(/<br\s*\/?>/i).filter(s => s.trim()).length;
      // 机构名被切碎：任一段以「泰/君/安/金/天/混/物」等残片开头
      // ⚠ 只查「不可能作为机构名首字」的残片：泰/君/安/金。
      //   第一版把「东华国永中隆…」也放进字符类 ⇒ 正常的「东证 看多」「华泰 看空」全被误报。
      const stray = rows.some(r => /^(泰|君|安|金)\s/.test((r.textContent || '').trim()));
      if (nHeads >= 2 && nSeg !== nHeads) {
        bad.push({ v: tr.children[0].textContent.trim(), nHeads, nSeg, kind: '段数≠家数', head: txt.slice(0, 100) });
      }
      if (stray) {
        bad.push({ v: tr.children[0].textContent.trim(), nHeads, nSeg, kind: '名称被切碎', head: rows.map(r => r.textContent.slice(0, 12)).join(' | ') });
      }
    });
    return { found: true, rows: tbl.querySelectorAll('tbody tr').length, bad };
  });

  if (!report.found) { console.log('\n===== ' + JSON.stringify(info) + ' =====\n  未找到第二节表格'); continue; }
  console.log('\n===== ' + JSON.stringify(info) + ' / ' + report.rows + ' 行 =====');
  if (!report.bad.length) { console.log('  ✓ 段数 == 家数，且无机构名残片'); continue; }
  console.log('  ★ 异常 ' + report.bad.length + ' 处：');
  report.bad.slice(0, 8).forEach(b => console.log('    [' + b.kind + '] ' + b.v + '  家=' + b.nHeads + ' 段=' + b.nSeg + '  ' + b.head));
}
await B.close();
