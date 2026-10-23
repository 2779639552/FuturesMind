// 验收: 原项目(:5000) 研报页二级页签
// ⚠ 拦截 POST /api/research/variety-summary/generate —— 验收不该触发真实 LLM 生成
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, m, e='') => { ok ? pass++ : fail++; console.log(`  ${ok?'PASS':'FAIL'}  ${m}${e?'  | '+e:''}`); };

const browser = await chromium.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe', args:['--no-sandbox'] });
const page = await browser.newPage({ viewport:{width:1600,height:1000} });
const errs = []; const blocked = [];
page.on('pageerror', e => errs.push(String(e).slice(0,160)));
await page.route('**/api/research/variety-summary/generate', route => {
  blocked.push(1);
  return route.fulfill({ status:200, contentType:'application/json', body:'{"ok":false,"error":"blocked by probe"}' });
});
await page.goto(BASE, { waitUntil:'domcontentloaded' });
await page.waitForTimeout(2000);
const th = await page.evaluate(()=>document.documentElement.getAttribute('data-theme'));
if (th === 'dark') { await page.click('button.theme-toggle'); await page.waitForTimeout(500); }

await page.click('nav button[data-tab="research"]');
await page.waitForTimeout(4500);

const st = () => page.evaluate(() => {
  const tab = document.getElementById('tab-research');
  const bar = tab.querySelector(':scope > .rsh-subnav');
  const head = tab.querySelector(':scope > .rsh-head');
  const cards = [...tab.children].filter(e => e.classList && e.classList.contains('card'));
  const tools = tab.querySelector(':scope > .page-tools');
  return {
    heads: tab.querySelectorAll(':scope > .rsh-head').length,
    headTitle: head ? head.querySelector('.rsh-title').textContent : '',
    bar: !!bar,
    btns: bar ? [...bar.children].map(b=>b.textContent) : [],
    on: bar ? [...bar.children].filter(b=>b.classList.contains('on')).map(b=>b.textContent) : [],
    cards: cards.map(c => ({ t:(c.querySelector('h3')||{}).textContent.trim().slice(0,10), vis: c.style.display !== 'none' })),
    toolsVis: tools ? tools.style.display !== 'none' : null,
    h: document.documentElement.scrollHeight,
  };
});

const a = await st();
log(a.heads === 1 && a.headTitle === '研报', 'A1 页面头只有一个且标题为「研报」', `heads=${a.heads} title=${a.headTitle}`);
log(a.bar && a.btns.join(',') === '问答,每日总结,单品种,观点总览', 'A2 四个页签齐全', a.btns.join('|'));
log(a.on.join() === '每日总结', 'A3 默认落在「每日总结」', a.on.join('|'));
log(a.toolsVis === false, 'A4 默认隐藏问答容器(.page-tools)', `toolsVis=${a.toolsVis}`);
log(a.cards.filter(c=>c.vis).length === 1, 'A5 默认只显示 1 张卡', JSON.stringify(a.cards));
console.log('  默认页高', a.h);
await page.screenshot({ path:'probe/_rsh_default.png' });

// 切到问答
await page.click('#tab-research .rsh-subnav button:nth-child(1)');
await page.waitForTimeout(800);
const b = await st();
log(b.toolsVis === true, 'B1 问答: 问答容器显示', `toolsVis=${b.toolsVis}`);
log(b.cards.every(c=>!c.vis), 'B2 问答: 其余卡片全部隐藏', JSON.stringify(b.cards));
console.log('  问答页高', b.h);
await page.screenshot({ path:'probe/_rsh_qa.png' });

// 切到单品种
await page.click('#tab-research .rsh-subnav button:nth-child(3)');
await page.waitForTimeout(600);
const c = await st();
log(c.cards.filter(x=>x.vis).length === 1 && c.cards.find(x=>x.vis).t.includes('单品种'), 'C1 单品种视图只显示对应卡', JSON.stringify(c.cards));
await page.screenshot({ path:'probe/_rsh_single.png' });

// 切到观点总览
await page.click('#tab-research .rsh-subnav button:nth-child(4)');
await page.waitForTimeout(600);
const d = await st();
log(d.cards.filter(x=>x.vis).length === 1 && d.cards.find(x=>x.vis).t.includes('观点'), 'C2 观点总览视图只显示对应卡', JSON.stringify(d.cards));
await page.screenshot({ path:'probe/_rsh_views.png' });

// 离开再回来: 不能重复插页面头/页签
await page.click('nav button[data-tab="run"]'); await page.waitForTimeout(500);
await page.click('nav button[data-tab="research"]'); await page.waitForTimeout(1500);
const e = await st();
log(e.heads === 1 && e.bar && e.btnCount !== 0, 'D1 来回切页不重复注入', `heads=${e.heads}`);
log(d.on.join() === '观点总览', 'D2 返回后保持上次页签', e.on.join('|'));

log(errs.length === 0, 'E1 无 JS 错误', errs.join(';'));
console.log(`  拦截的生成请求: ${blocked.length} 次（0 = 没烧 LLM）`);
await browser.close();
console.log(`\n===== ${pass} PASS / ${fail} FAIL =====`);
process.exit(fail ? 1 : 0);
