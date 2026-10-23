import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 全面扫描：所有显示机构名的单元格，检测是否发生**折行**（名字被拆成多行）
// 【为什么要扫全页】用户反馈"国君只有一家时名称被拆成三行" —— 不只第二节，
//   总表「发行方」列、逐品种观点总览的「发行方」列都可能发生。
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1200 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
const VIEWS = [
  { type: '周报', date: '2026-10-11' },
  { type: '', date: null },
];
for (const v of VIEWS) {
  await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
  await page.click('nav button[data-tab="research"]');
  await sleep(3000);
  const sel = await page.evaluate(async (want) => {
    const t = document.getElementById('res-daily-type');
    if (t) { t.value = want.type; t.dispatchEvent(new Event('change')); }
    await new Promise(r => setTimeout(r, 2600));
    const d = document.getElementById('res-daily-date');
    if (d && want.date) {
      const o = [...d.options].find(x => x.value === want.date);
      if (o) { d.value = want.date; d.dispatchEvent(new Event('change')); }
    }
    await new Promise(r => setTimeout(r, 2600));
    return { type: t ? t.value : null, date: d ? d.value : null };
  }, v);
  console.log('\n===== 口径: ' + JSON.stringify(sel) + ' =====');

  const bad = await page.evaluate(() => {
    const out = [];
    // ① 任何 .src-item / .dir-chip / .cf-row / 以及表格里的发行方单元格
    // 只取**叶子**：单个机构名元素，或没有子块的纯文本单元格。
    // ⚠ 别把整格 td 当检查对象 —— textContent 会把多个 .src-item 拼成"东证国泰君安华泰"（假折行）。
    const nodes = [...document.querySelectorAll('.src-item, .cf-row')].concat(
      [...document.querySelectorAll('.rv-tbl td, .report-content table td')].filter(
        td => !td.querySelector('.src-item, .cf-row, div, span')
      )
    );
    const FAMS = /(国泰君安|紫金天风|混沌天成|物产中大|中信期货|国泰|国君|东证|华泰|永安|中信|隆众|南华|兴业)/;
    nodes.forEach((el) => {
      const txt = (el.textContent || '').trim();
      if (!txt || !FAMS.test(txt)) return;
      // 只关心"机构名所在的位置"：找文本节点里机构名那一段的矩形
      const cs = getComputedStyle(el);
      const lh = parseFloat(cs.lineHeight) || 0;
      const h = el.getBoundingClientRect().height;
      const padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
      const contentH = h - padT - padB;
      // 用 Range 量整个元素的文本行数（按 client rects 数）
      const r = document.createRange();
      r.selectNodeContents(el);
      const rects = [...r.getClientRects()].filter(x => x.width > 1 && x.height > 1);
      // 合并同一行的 rect（top 相近）
      const tops = [];
      rects.forEach(x => {
        const t = Math.round(x.top);
        if (!tops.some(y => Math.abs(y - t) <= 3)) tops.push(t);
      });
      const lines = tops.length;
      // 单个机构名（≤8 字）却被折成 ≥2 行 ⇒ 可疑
      const nameOnly = txt.length <= 8 && FAMS.test(txt);
      if (nameOnly && lines >= 2) {
        out.push({
          kind: 'name-wrapped', text: txt, lines,
          w: Math.round(el.getBoundingClientRect().width),
          lh: Math.round(lh), tag: el.className || el.tagName,
        });
      }
    });
    return out;
  });
  if (!bad.length) { console.log('  未发现机构名折行 ✓'); continue; }
  console.log('  ★ 疑似机构名折行 ' + bad.length + ' 处：');
  bad.slice(0, 12).forEach(b => console.log('    [' + b.kind + '] ' + b.text + ' | ' + b.lines + ' 行 | 宽 ' + b.w + 'px | class=' + b.tag));
}
await B.close();
