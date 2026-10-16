/**
 * 【变异验证】证明 popover_clip_harness 的几何断言**真的能抓到裁剪**(不是假绿)。
 *
 * 做法: 同一页面、同一测量口径, 只切换"修复是否生效":
 *   A) 修复态   —— 真实交互打开浮层
 *   B) 变异态   —— patch `App._ensurePopoverVisible` 为 no-op + 撤 .sec-open
 *                 + 内联强制 overflow:hidden(还原修复前的折叠外壳行为)
 * 预期: A 的 clippedBy=[] 且命中自身; B 的 clippedBy 非空且底部命中别的元素。
 * 若 B 也"全绿", 说明护栏测不出问题 ⇒ 断言无效, 必须重写。
 *
 * 用法: NO_PROXY=127.0.0.1,localhost node _mut_popover.mjs
 */
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';

const browser = await chromium.launch({
  executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
page.on('pageerror', e => console.log('pageerror: ' + e.message));

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.addStyleTag({ content: '#login-overlay{display:none !important;}#login-card{display:none !important;}' });
await page.evaluate(() => document.querySelectorAll('#login-overlay,#login-card').forEach(e => e.remove()));

/** 打开浮层并测量 —— 与主护栏同一套口径 */
async function measure({ tab, blockLabel, panelId, opener, mutate }) {
  await page.evaluate(t => App.switchTab(t), tab);
  await page.waitForTimeout(1400);

  if (mutate) {
    // 变异: 抹掉两道防线, 还原"修复前"行为
    await page.evaluate(() => { App._ensurePopoverVisible = function () { /* no-op */ }; });
  }

  await page.evaluate(({ label, panelId }) => {
    const tab = document.querySelector('.tab.active');
    const it = Array.from(tab.querySelectorAll('.sec-index-item')).find(x => (x.textContent || '').includes(label));
    if (it && !it.classList.contains('is-open')) it.click();
  }, { label: blockLabel, panelId });
  await page.waitForTimeout(600);

  if (mutate) {
    // 撤掉 .sec-open 并内联强制 hidden —— 折叠外壳的原始行为
    await page.evaluate(({ label }) => {
      const tab = document.querySelector('.tab.active');
      const tabId = tab.id.replace(/^tab-/, '');
      const rows = (App._secRows || {})[tabId] || [];
      const row = rows.find(r => (r.label || '').includes(label));
      (row && row.blocks || []).forEach(b => {
        b.classList.remove('sec-open');
        b.style.overflow = 'hidden';
      });
    }, { label: blockLabel });
    await page.waitForTimeout(100);
  }

  await page.evaluate(({ opener }) => App[opener](), { opener });
  await page.waitForTimeout(300);

  return await page.evaluate((panelId) => {
    const p = document.getElementById(panelId);
    const r = p.getBoundingClientRect();
    const clippedBy = [];
    let n = p.parentElement;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflow !== 'visible' || cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        if (n.tagName !== 'BODY' && n.tagName !== 'HTML') {
          const cr = n.getBoundingClientRect();
          if (r.bottom > cr.bottom + 1 || r.right > cr.right + 1) {
            clippedBy.push({ cls: String(n.className || '').split(' ')[0] || n.tagName, over: Math.round(r.bottom - cr.bottom) });
          }
        }
      }
      n = n.parentElement;
    }
    const pt = [r.left + r.width / 2, r.bottom - 6];
    const hit = document.elementFromPoint(pt[0], pt[1]);
    return {
      h: Math.round(r.height),
      clippedBy,
      bottomHitSelf: !!(hit && (hit === p || p.contains(hit))),
      bottomHit: hit ? (hit.id || String(hit.className).split(' ')[0] || hit.tagName) : 'null',
    };
  }, panelId);
}

const TARGET = { tab: 'warehouse', blockLabel: '上传研报', panelId: 'res-local-import-panel', opener: 'toggleLocalImportPanel' };

console.log('=== A) 修复态 ===');
const a = await measure({ ...TARGET, mutate: false });
console.log(`  h=${a.h} clippedBy=${JSON.stringify(a.clippedBy)} 底部命中自身=${a.bottomHitSelf} (${a.bottomHit})`);

// 重开页面拿干净状态
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
await page.evaluate(() => document.querySelectorAll('#login-overlay,#login-card').forEach(e => e.remove()));

console.log('=== B) 变异态(抹掉修复) ===');
const b = await measure({ ...TARGET, mutate: true });
console.log(`  h=${b.h} clippedBy=${JSON.stringify(b.clippedBy)} 底部命中自身=${b.bottomHitSelf} (${b.bottomHit})`);

const okA = a.clippedBy.length === 0 && a.bottomHitSelf;
const okB = b.clippedBy.length > 0 && !b.bottomHitSelf;
console.log(`\n${'='.repeat(52)}`);
console.log(`A 修复态 未被裁  : ${okA ? 'PASS' : 'FAIL'}`);
console.log(`B 变异态 被裁(被裁 ${b.clippedBy[0] ? b.clippedBy[0].over + 'px' : '-'}) : ${okB ? 'PASS' : 'FAIL'}`);
console.log(okA && okB
  ? '★ 护栏有效: 能区分"修好了"与"没修"—— 不是假绿'
  : '⚠️ 护栏无效: 变异态也通过 ⇒ 几何断言测不出裁剪, 必须重写');

await browser.close();
process.exit(okA && okB ? 0 : 1);
