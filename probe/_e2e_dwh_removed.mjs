// 验证: 数据仓库页已删除「国君数据集」与「历史分析报告/报告内容」, 其余模块正常
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage();
const results = [];
const check = (n, ok, d = '') => { results.push({ n, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  | ' + d : ''}`); };
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 150)));

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(3000);

  const info = await page.evaluate(() => {
    const tab = document.getElementById('tab-warehouse');
    const h3s = Array.from(tab.querySelectorAll('h3')).map(h => h.textContent.trim().slice(0, 30));
    return {
      hasDataset: !!document.getElementById('dwh-ds-variety'),
      hasDatasetTable: !!document.getElementById('dwh-ds-table'),
      hasHistoryList: !!document.getElementById('history-list'),
      hasReportViewer: !!document.getElementById('report-viewer'),
      hasRepairBar: !!document.getElementById('res-repair-bar'),
      h3s,
    };
  });
  console.log('① 结构:', JSON.stringify(info, null, 2));
  check('国君数据集已删', !info.hasDataset && !info.hasDatasetTable, `dwh-ds-variety=${info.hasDataset}`);
  check('历史分析报告/报告内容已删', !info.hasHistoryList && !info.hasReportViewer, `history=${info.hasHistoryList} viewer=${info.hasReportViewer}`);
  check('一键修复常驻条保留', info.hasRepairBar, `repairBar=${info.hasRepairBar}`);
  check('上传/自传/研报库三卡保留',
    info.h3s.some(h => h.includes('上传研报')) && info.h3s.some(h => h.includes('自传数据')) && info.h3s.some(h => h.includes('研报库')),
    JSON.stringify(info.h3s));

  // ② 研报库列表是否正常加载(不报错)
  await page.waitForTimeout(1500);
  const listLen = await page.evaluate(() => (document.getElementById('res-list') || {}).innerHTML?.length || 0);
  check('研报库列表正常渲染', listLen > 50, `len=${listLen}`);

  // ③ 折叠索引正常(小模块索引条存在)
  const idxLen = await page.evaluate(() => {
    const idx = document.querySelector('#tab-warehouse .sec-index') || document.querySelector('.sec-index');
    return idx ? idx.textContent.length : 0;
  });
  check('小模块索引正常', idxLen > 5, `idxLen=${idxLen}`);

  check('页面无 JS 错误', jsErrors.length === 0, jsErrors.join(';'));
} catch (e) {
  check('探针异常', false, String(e).slice(0, 200));
} finally {
  await browser.close();
}
const fails = results.filter(r => !r.ok);
console.log(`\n===== 汇总: ${results.length - fails.length}/${results.length} PASS =====`);
process.exit(fails.length ? 1 : 0);
