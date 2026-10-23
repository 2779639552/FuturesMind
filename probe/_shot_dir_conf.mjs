// 实拍：① 当日观点总表（方向 chip + 置信度进度条）② 逐品种观点总览
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 1 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);

// ── ① 当日观点总表 ───────────────────────────────
const boxA = await page.evaluate(() => {
  // 找第一张「观点总表」（表头含方向+置信度）所在的板块块
  const t = [...document.querySelectorAll('.report-content table')]
    .find(x => {
      const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
      return ths.includes('方向') && ths.includes('置信度');
    });
  if (!t) return null;
  // 连同它的表头一起拍：向上找最近的 expander / h3 容器
  let el = t;
  for (let i = 0; i < 4 && el.parentElement; i++) el = el.parentElement;
  const r = t.getBoundingClientRect();
  return { x: Math.max(0, r.x - 12), y: Math.max(0, r.y - 60), w: Math.min(r.width + 24, 1500), h: Math.min(r.height + 80, 700) };
});
if (boxA) {
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('.report-content table')]
      .find(x => {
        const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
        return ths.includes('方向') && ths.includes('置信度');
      });
    if (t) t.scrollIntoView({ block: 'center' });
  });
  await sleep(900);
  const box2 = await page.evaluate(() => {
    const t = [...document.querySelectorAll('.report-content table')]
      .find(x => {
        const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
        return ths.includes('方向') && ths.includes('置信度');
      });
    const r = t.getBoundingClientRect();
    return { x: Math.max(0, r.x - 14), y: Math.max(0, r.y - 66), width: Math.min(r.width + 28, window.innerWidth - 8), height: Math.min(r.height + 30, 660) };
  });
  console.log('观点总表 clip: ' + JSON.stringify(box2));
  await page.screenshot({ path: 'probe/_shot_dirconf_table.png', clip: box2 });
}

// ── ② 逐品种观点总览（挑有看多/看空的品种） ──────────
await page.evaluate(() => {
  const sel = document.getElementById('res-views-variety');
  if (sel) { const f = [...sel.options].find(o => o.value); if (f) sel.value = f.value; }
  return App.loadResearchViews && App.loadResearchViews();
});
let done = false;
for (const code of ['BZ', 'TA', 'SC', 'FU', 'M', 'CF']) {
  await page.evaluate(c => {
    const sel = document.getElementById('res-views-variety');
    if (sel) sel.value = c;
    return App.loadResearchViews && App.loadResearchViews();
  }, code);
  for (let i = 0; i < 25; i++) {
    const ok = await page.evaluate(() => {
      const el = document.getElementById('res-views');
      if (!el || (el.innerText || '').includes('加载中')) return 0;
      const n = el.querySelectorAll('.rv-chip').length;
      return n;
    });
    if (ok > 0) {
      const has = await page.evaluate(() => {
        const t = [...document.querySelectorAll('#res-views .rv-chip')].map(c => c.textContent.trim());
        return t.some(x => x.includes('空')) && t.some(x => x.includes('多'));
      });
      if (has) { done = true; console.log('选到品种: ' + code); }
      break;
    }
    await sleep(400);
  }
  if (done) break;
}
await page.evaluate(() => {
  const el = document.getElementById('res-views');
  if (el) el.scrollIntoView({ block: 'center' });
});
await sleep(900);
const boxB = await page.evaluate(() => {
  const el = document.getElementById('res-views');
  const r = el.getBoundingClientRect();
  return { x: Math.max(0, r.x - 14), y: Math.max(0, r.y - 40), width: Math.min(r.width + 28, window.innerWidth - 8), height: Math.min(r.height + 24, 780) };
});
console.log('逐品种总览 clip: ' + JSON.stringify(boxB));
await page.screenshot({ path: 'probe/_shot_dirconf_views.png', clip: boxB });

await B.close();
console.log('done');
