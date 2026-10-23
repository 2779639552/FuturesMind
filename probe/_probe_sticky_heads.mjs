// 验证两张“全品种截面”表的表头吸顶：行数稳定后，滚动容器 400px，表头应保持贴顶。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1000 }, deviceScaleFactor: 2 })).newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 140)));

async function waitStable(id, timeoutMs = 90000) {
  const t0 = Date.now();
  let last = -1, same = 0;
  while (Date.now() - t0 < timeoutMs) {
    const n = await page.evaluate(i => {
      const t = document.getElementById(i);
      return t ? t.querySelectorAll('tbody tr').length : 0;
    }, id);
    if (n > 1 && n === last) { same++; if (same >= 3) return n; } else { same = 0; }
    last = n;
    await page.waitForTimeout(700);
  }
  return last;
}

async function probe(tab, tableId, label, shot) {
  await page.click(`nav button[data-tab="${tab}"]`);
  await page.waitForTimeout(1500);
  const rows = await waitStable(tableId);
  if (rows < 2) { console.log(`【${label}】表格无数据（rows=${rows}）`); return; }

  const r = await page.evaluate(async (id) => {
    const t = document.getElementById(id);
    const box = t.parentElement;
    const th = t.querySelector('thead th');
    const csTh = getComputedStyle(th);
    const csBox = getComputedStyle(box);
    // 真实数据可能还没渲染满（内容不足 ⇒ 无滚动）⇒ **临时压矮容器**强制产生滚动，
    //   直接验证"吸顶机制"本身；测完恢复原状。
    const origMax = box.style.maxHeight;
    box.style.maxHeight = '300px';
    await new Promise(r => setTimeout(r, 200));
    const before = { th: th.getBoundingClientRect().top, box: box.getBoundingClientRect().top };
    box.scrollTop = 400;
    await new Promise(r => setTimeout(r, 500));
    const after = { th: th.getBoundingClientRect().top, box: box.getBoundingClientRect().top };
    const res = {
      rows: t.querySelectorAll('tbody tr').length,
      boxClass: box.className,
      boxMaxH: csBox.maxHeight,
      clientH: box.clientHeight, scrollH: box.scrollHeight,
      scrollable: box.scrollHeight > box.clientHeight + 5,
      scrollTop: box.scrollTop,
      thPosition: csTh.position, thTop: csTh.top, thBg: csTh.backgroundColor,
      stuck: Math.abs(after.th - after.box) <= 2,
      before, after,
    };
    box.style.maxHeight = origMax;
    return res;
  }, tableId);

  console.log(`【${label}】rows=${r.rows} box=${r.boxClass} maxH=${r.boxMaxH} scrollable=${r.scrollable} ` +
    `(client ${r.clientH}/scroll ${r.scrollH}, top=${r.scrollTop}) th=${r.thPosition}/${r.thTop} ` +
    `bg=${r.thBg} | 表头 top ${Math.round(r.before.th)} → ${Math.round(r.after.th)} | 吸顶=${r.stuck}`);

  await page.evaluate(() => document.querySelector('.tab:not([style*="none"])')?.scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(300);
  await page.screenshot({ path: shot });
  await page.evaluate(id => { document.getElementById(id).parentElement.scrollTop = 0; }, tableId);
}

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await probe('quant', 'q-ov-table', '技术面', 'probe/_shot_sticky_quant.png');
await probe('fund', 'f-rk-table', '基本面', 'probe/_shot_sticky_fund.png');
console.log('  JS 错误:', errs.length ? errs.slice(0, 3) : '无');
await B.close();
