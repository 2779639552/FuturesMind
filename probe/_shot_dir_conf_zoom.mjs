// 高清特写：把方向/置信度两列放大拍清楚（+程序化打印每格的颜色与文本）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
// 放大 2 倍拍，看清颜色
const page = await (await B.newContext({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);

// 把「品种 / 名称 / 方向 / 置信度」四列单独截出来（最关心的就是这两列）
const clip = await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')]
    .find(x => {
      const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
      return ths.includes('方向') && ths.includes('置信度');
    });
  if (!t) return null;
  const ths = [...t.querySelectorAll('thead th')];
  const iDir = ths.findIndex(h => (h.textContent || '').trim() === '方向');
  const iConf = ths.findIndex(h => (h.textContent || '').trim() === '置信度');
  if (iDir < 0 || iConf < 0) return null;
  const a = ths[iDir].getBoundingClientRect();
  const b = ths[iConf].getBoundingClientRect();
  // 从「方向」左边缘到「置信度」右边缘
  const x = Math.max(0, a.x - 6);
  const right = b.x + b.width;
  // 只拍前 4 行
  const rows = [...t.querySelectorAll('tbody tr')].slice(0, 4);
  const yTop = a.y - 4;
  const yBot = rows.length ? rows[rows.length - 1].getBoundingClientRect().bottom : a.bottom;
  return { x, y: Math.max(0, yTop), width: Math.min(right - x + 12, 900), height: Math.min(yBot - yTop + 8, 400) };
});
console.log('特写 clip: ' + JSON.stringify(clip));
await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')]
    .find(x => {
      const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
      return ths.includes('方向') && ths.includes('置信度');
    });
  if (t) t.scrollIntoView({ block: 'center' });
});
await sleep(1000);
if (clip) await page.screenshot({ path: 'probe/_shot_dirconf_zoom.png', clip });

// 程序化打印：每行 方向/置信度 的文本 + 颜色
const dump = await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')]
    .find(x => {
      const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
      return ths.includes('方向') && ths.includes('置信度');
    });
  if (!t) return null;
  const ths = [...t.querySelectorAll('thead th')];
  const iDir = ths.findIndex(h => (h.textContent || '').trim() === '方向');
  const iConf = ths.findIndex(h => (h.textContent || '').trim() === '置信度');
  return [...t.querySelectorAll('tbody tr')].slice(0, 6).map(tr => {
    const tds = tr.children;
    const dc = tds[iDir] ? tds[iDir].querySelectorAll('.dir-chip') : [];
    const cb = tds[iConf] ? tds[iConf].querySelectorAll('.conf-bar') : [];
    return {
      dir: [...dc].map(c => c.textContent.trim() + '@' + getComputedStyle(c).color + ' bg=' + getComputedStyle(c).backgroundColor),
      conf: [...cb].map(b => {
        const f = b.querySelector('.fill');
        return b.querySelector('.pct').textContent.trim() + ' w=' + (f ? f.style.width : '?') + ' ' + (f ? getComputedStyle(f).backgroundColor : '');
      }),
    };
  });
});
console.log('=== 逐行明细 ===');
(dump || []).forEach((r, i) => {
  console.log('  行%d 方向: %s', i + 1, r.dir.join(' | '));
  console.log('      置信度: %s', r.conf.join(' | '));
});

await B.close();
