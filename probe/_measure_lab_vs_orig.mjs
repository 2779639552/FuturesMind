// 逐页测量: 原项目(:5000) vs 实验副本(:5100) 的计算样式与结构
// ⚠ 只在原项目的默认页(run)取样, **不切 research** —— 原项目研报页会触发自动排队(烧 LLM)
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe', args:['--no-sandbox'] });

async function open(base, tab) {
  const p = await browser.newPage({ viewport:{width:1600,height:1000} });
  await p.goto(base, { waitUntil:'domcontentloaded' });
  await p.waitForTimeout(1800);
  const th = await p.evaluate(()=>document.documentElement.getAttribute('data-theme'));
  if (th === 'dark') { await p.click('button.theme-toggle'); await p.waitForTimeout(500); }
  if (tab !== 'run') { await p.click(`nav button[data-tab="${tab}"]`); await p.waitForTimeout(3500); }
  return p;
}

function pick() {
  const cs = e => e ? getComputedStyle(e) : null;
  const card = document.querySelector('.tab.active .card');
  const h3 = card ? card.querySelector('h3') : null;
  const stat = document.querySelector('.tab.active .stat-card .stat-val');
  const th = document.querySelector('.tab.active table th');
  const head = document.querySelector('.tab.active > .lab-head');
  const sub = document.querySelector('.tab.active > .lab-subnav');
  return {
    bodyBg: cs(document.body).backgroundColor,
    cardBg: card ? cs(card).backgroundColor : '',
    cardBorder: card ? cs(card).borderTopWidth + ' ' + cs(card).borderTopColor : '',
    cardRadius: card ? cs(card).borderRadius : '',
    cardPad: card ? cs(card).padding : '',
    h3size: h3 ? cs(h3).fontSize : '',
    statVal: stat ? cs(stat).fontSize : '',
    thWeight: th ? cs(th).fontWeight : '',
    thColor: th ? cs(th).color : '',
    hasHead: !!head, headH: head ? Math.round(head.getBoundingClientRect().height) : 0,
    hasSub: !!sub,
    cards: document.querySelectorAll('.tab.active .card').length,
    tables: document.querySelectorAll('.tab.active table').length,
    h: document.documentElement.scrollHeight,
  };
}

const rows = [];
for (const tab of ['run','quant','fund','sentiment','foreign','warehouse']) {
  const p = await open('http://127.0.0.1:5100', tab);
  rows.push({ tab, ...(await p.evaluate(pick)) });
  await p.close();
}
// 原项目: 只取 run(默认页), 不切页
const po = await open('http://127.0.0.1:5000', 'run');
const orig = await po.evaluate(pick);
await po.close();

await browser.close();
console.log('ORIG:', JSON.stringify(orig));
console.log('LAB|' + rows.map(r => JSON.stringify(r)).join('\nLAB|'));
