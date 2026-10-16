// E2E 验收: 国君页「覆盖矩阵 + 四源取数状态」默认展开(v18)
//  用户报障原话: “覆盖矩阵和四源状态仍然折叠”
//  根因: 索引在数据回填前构建, 几何裁决(高度≥200px)量到的是空壳高度 ⇒ 有内容的块被判收起
// 用法: node probe/_e2e_gtja_default_open.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1040 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));

// 等四源状态回填(有内容才谈得上“该展开”)
let filled = false;
for (let i = 0; i < 30; i++) {
  await page.waitForTimeout(2000);
  filled = await page.evaluate(() => {
    const el = document.getElementById('gt-sources');
    return !!el && (el.textContent || '').length > 80;
  });
  if (filled) break;
}
log(filled, '四源取数状态已回填数据');

// 【关键】取真实开合态(用“块的实际高度”而非仅类名 —— 类名对但 max-height:0px 仍然看不见)
const readState = () => page.evaluate(() => {
  const rows = (App._secRows || {})['gtja'] || [];
  const out = rows.map((r, i) => {
    const b = (r.blocks || [])[0];
    return {
      i, label: r.label,
      collapsed: b ? b.classList.contains('is-collapsed') : null,
      openCls: b ? b.classList.contains('sec-open') : null,
      maxH: b ? b.style.maxHeight : null,
      visibleH: b ? Math.round(b.getBoundingClientRect().height) : 0,
      btnOpen: r.btn ? r.btn.classList.contains('is-open') : null,
    };
  });
  return { ver: App._SEC_IDX_VER, rows: out };
});

const vis = r => r.visibleH > 40 && !r.collapsed && r.openCls;
const hide = r => r.collapsed && r.visibleH === 0;

console.log('\n[1] 默认展开: 覆盖矩阵(0) + 四源状态(1) 展开, 指标总览(2) 收起');
const s0 = await readState();
// 版本号可能与别处改动共享, 故只断言"已 bump 到 18+"(= 老索引会被重建)。
log(Number(s0.ver) >= 18, `索引版本 = ${s0.ver} (期望 >=18 —— 否则老浏览器不重建索引, 修复不生效)`);
s0.rows.forEach(r => console.log(`     [${r.i}] h=${r.visibleH}px collapsed=${r.collapsed} open=${r.openCls} btn=${r.btnOpen}  ${r.label}`));
log(s0.rows.length === 3, `模块数 = ${s0.rows.length} (期望 3)`);
log(vis(s0.rows[0]), `覆盖矩阵默认展开 (h=${s0.rows[0].visibleH}px)`);
log(vis(s0.rows[1]), `② 四源取数状态默认展开 (h=${s0.rows[1].visibleH}px)`);
log(hide(s0.rows[2]), `③ 指标总览默认收起 (h=${s0.rows[2].visibleH}px)`);
log(s0.rows[0].btnOpen && s0.rows[1].btnOpen && !s0.rows[2].btnOpen,
  '索引标签的 is-open 态与实际一致(无“高亮却收起”的错位)');

console.log('\n[2] ★「共 N 项」清空筛选后, 默认展开态必须**原样恢复**');
// 先制造一次筛选: 勾选 ③ 指标总览(此时 0/1 应被隐藏)
await page.evaluate(() => {
  const rows = (App._secRows || {})['gtja'] || [];
  rows[2].btn.click();
});
await page.waitForTimeout(700);
const s1 = await readState();
log(s1.rows[2].visibleH > 40, `勾选「③ 指标总览」→ 它被展开 (h=${s1.rows[2].visibleH}px)`);
log(s1.rows[0].visibleH === 0 && s1.rows[1].visibleH === 0,
  '未选中的 覆盖矩阵/四源状态 被隐藏(筛选生效)');

// 点 pill 清空
await page.evaluate(() => document.querySelector('#tab-gtja .sec-pill').click());
await page.waitForTimeout(800);
const s2 = await readState();
log(vis(s2.rows[0]), `清空后 覆盖矩阵 仍展开 (h=${s2.rows[0].visibleH}px) —— 未被收回去`);
log(vis(s2.rows[1]), `清空后 ② 四源取数状态 仍展开 (h=${s2.rows[1].visibleH}px) —— 未被收回去`);
log(hide(s2.rows[2]), `清空后 ③ 指标总览 仍是收起 (h=${s2.rows[2].visibleH}px)`);
log(s2.rows.every(r => r.btnOpen === vis(r)),
  '清空后所有标签 is-open 与块形态一致');

console.log('\n[3] 页面无遗留错位');
const ghost = await page.evaluate(() => ({
  filteredLeft: document.querySelectorAll('#tab-gtja .sec-block.sec-filtered').length,
  hiddenLeft: document.querySelectorAll('#tab-gtja .sec-block.sec-hidden').length,
}));
log(ghost.filteredLeft === 0, `无残留 .sec-filtered (${ghost.filteredLeft})`);
log(ghost.hiddenLeft === 0, `无残留 .sec-hidden (${ghost.hiddenLeft})`);

console.log(`\nJS 报错: ${errs.length ? errs.slice(0, 3).join(' | ') : '(无)'}`);
console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
await browser.close();
process.exit(fail ? 1 : 0);
