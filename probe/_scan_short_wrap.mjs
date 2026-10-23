import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 全页扫描：找"短文本却占多行"的叶子元素（折行/被拆行的直接证据）
// 【为什么要全页】用户反馈"国君一家时名称被拆成三行"，但数据层查不到单家拆行 ⇒
//   现象可能在别处（逐品种总览 / 单品种卡 / 某个窄列）。
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const W = Number(process.env.VW || 1760);
const page = await (await B.newContext({ viewport: { width: W, height: 1400 }, deviceScaleFactor: 2 })).newPage();
console.log('  视口宽度 ' + W + 'px');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const TABS = ['research'];
for (const tab of TABS) {
  await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('nav button[data-tab="' + tab + '"]', { timeout: 30000 });
  await page.click('nav button[data-tab="' + tab + '"]');
  await sleep(3500);

  // 依次切换 日报/周报/季报，覆盖不同数据
  for (const tp of ['周报', '', '季报']) {
    const info = await page.evaluate(async (type) => {
      const t = document.getElementById('res-daily-type');
      if (t) { t.value = type; t.dispatchEvent(new Event('change')); }
      await new Promise(r => setTimeout(r, 2400));
      const d = document.getElementById('res-daily-date');
      return { type: t ? t.value : null, date: d ? d.value : null };
    }, tp);
    await sleep(1800);

    const bad = await page.evaluate(() => {
      const out = [];
      const walk = (root) => {
        for (const el of root.querySelectorAll('*')) {
          if (el.children.length) continue;                 // 只看叶子
          const txt = (el.textContent || '').trim();
          if (!txt || txt.length > 14) continue;            // 只看短文本（名称/状态/代码）
          if (!/[\u4e00-\u9fa5]/.test(txt)) continue;       // 只关心中文
          const cs = getComputedStyle(el);
          if (cs.display === 'none' || cs.visibility === 'hidden') continue;
          const r = document.createRange();
          r.selectNodeContents(el);
          const rects = [...r.getClientRects()].filter(x => x.width > 1 && x.height > 1);
          if (!rects.length) continue;
          const tops = [];
          rects.forEach(x => {
            const t = Math.round(x.top);
            if (!tops.some(y => Math.abs(y - t) <= 3)) tops.push(t);
          });
          if (tops.length < 2) continue;                    // 单行 => 正常
          const box = el.getBoundingClientRect();
          out.push({
            text: txt, lines: tops.length,
            w: Math.round(box.width), h: Math.round(box.height),
            tag: el.tagName.toLowerCase(),
            cls: (el.className || '').toString().slice(0, 40),
            parent: (el.parentElement && el.parentElement.className || '').toString().slice(0, 40),
          });
        }
      };
      walk(document.body);
      return out;
    });
    console.log('\n===== ' + JSON.stringify(info) + ' =====');
    if (!bad.length) { console.log('  无短文本折行 ✓'); continue; }
    console.log('  ★ 短文本折行 ' + bad.length + ' 处：');
    bad.slice(0, 14).forEach(b => console.log(
      '    "' + b.text + '"  ' + b.lines + ' 行  ' + b.w + '×' + b.h + 'px  <' + b.tag + ' class="' + b.cls + '">  in .' + b.parent));
  }
}
await B.close();
