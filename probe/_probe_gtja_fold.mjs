// 探针: 取国君页各小模块的**真实**默认开合状态(不靠推测)
// 用法: node probe/_probe_gtja_fold.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1040 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(2500);

const r = await page.evaluate(() => {
  const tab = document.getElementById('tab-gtja');
  // 哪些变量是顶层作用域可见的? App 是 const(App 对象字面量), 看能否取到折叠引擎内部量
  const rows = (App._secRows || {})['gtja'] || [];
  return {
    ver: App._SEC_IDX_VER,
    hasVer18: !!App._SEC_OPEN_ALL_TABS,
    secRowsLen: rows.length,
    secRows: rows.map((x, i) => ({
      i,
      label: x.label,
      blocks: (x.blocks || []).length,
      h: (x.blocks || []).map(b => Math.round(b.scrollHeight)),
      collapsed: (x.blocks || []).map(b => b.classList.contains('is-collapsed')),
      openCls: (x.blocks || []).map(b => b.classList.contains('sec-open')),
      maxH: (x.blocks || []).map(b => b.style.maxHeight),
      btnOpen: x.btn ? x.btn.classList.contains('is-open') : null,
    })),
    domBlocks: Array.from(tab.querySelectorAll(':scope > .sec-block')).map(b => {
      const h3 = b.querySelector('h3');
      return {
        title: h3 ? h3.textContent.trim().slice(0, 24) : '(no h3)',
        collapsed: b.classList.contains('is-collapsed'),
        openCls: b.classList.contains('sec-open'),
        maxH: b.style.maxHeight,
        h: Math.round(b.getBoundingClientRect().height),
      };
    }),
  };
});
console.log(JSON.stringify(r, null, 2));
console.log('JS 报错:', errs.length ? errs.slice(0, 3).join(' | ') : '(无)');
await browser.close();
