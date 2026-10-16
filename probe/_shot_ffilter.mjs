// 交付截图：⑨ 基本面截面表的筛选 / 排序 / 列显隐
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await B.newContext({ viewport: { width: 1760, height: 1150 } });
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');
for (let i = 0; i < 120; i++) {
  const n = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (n >= 21) break;
  await page.waitForTimeout(1000);
}
await page.evaluate(() => window.scrollTo(0, 0));

// ① 全景：控制区（搜索 + 板块胶囊 + 8 个维度下拉 + 排序 + 列/清除）+ 全 21 行
await page.waitForTimeout(500);
await page.screenshot({ path: 'probe/_shot_ffilter_overview.png' });

// ② 筛选生效：点「能源」+ 选「库存综合=高」
await page.evaluate(() => {
  const b = [...document.querySelectorAll('#f-rk-pools button')].find(x => x.textContent.trim() === '能源');
  if (b) b.click();
});
await page.waitForTimeout(400);
try {
  await page.selectOption('#f-rk-dims select[data-dim="inv_grade"]', '高');
} catch (e) { console.log('（该取值当前无行，跳过）'); }
await page.waitForTimeout(500);
const n2 = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
const fc2 = await page.evaluate(() => (document.getElementById('f-rk-fcount') || {}).textContent || '');
console.log('筛选后：%d 行 | %s', n2, fc2);
await page.screenshot({ path: 'probe/_shot_ffilter_filtered.png' });

// ③ 重置后按「库存周期」列头排序（文本列、按多空方向）+ 隐藏两列
await page.evaluate(() => App._fRkClearFilters());
await page.waitForTimeout(300);
await page.evaluate(() => App._fRkClickHead('skill_cyc'));
await page.waitForTimeout(300);
await page.evaluate(() => {
  App._fRkCols = App._FRK_COLS.filter(c => !c.fixed).map(c => c.key)
    .filter(k => ['ck_chg5', 'soc_chg4', 'prof_grade', 'basis_pct'].indexOf(k) < 0);
  App._fRkColsSave();
  App._fRkApplyCols();
});
await page.waitForTimeout(400);
const head = await page.evaluate(() => (document.getElementById('f-rk-badge') || {}).textContent || '');
console.log('排序 badge:', head);
await page.screenshot({ path: 'probe/_shot_ffilter_sorted.png' });

// ④ 列面板打开态
await page.evaluate(() => { App._fRkAllCols(); App._fRkClickHead('skill_cyc'); });
await page.click('text=列 ▾');
await page.waitForTimeout(400);
await page.screenshot({ path: 'probe/_shot_ffilter_cols.png' });
console.log('已保存 4 张实拍');
await B.close();
