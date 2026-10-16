// 横版两列版式 · 浏览器几何实测
//
// 为什么要测而不是看图：多列布局（CSS multicol）与 `column-span:all` 在
// Chromium 的**分页**模式下有已知的怪异行为（跨列标题会强制"关掉"当前多列上下文，
// 后续内容可能只落到单列）。看图只能猜，这里直接量：
//   · 每个内容块的 x 坐标 ⇒ 判断它落在左列还是右列
//   · 每列的实际占用高度 ⇒ 判断"空列"是不是真的空
//   · 有无元素 x+width 超出内容区 ⇒ 溢出检测

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const htmlPath = process.argv[2];
if (!htmlPath) { console.error('用法: node _measure_columns.mjs <file:///...html>'); process.exit(2); }

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
// 与 _render_pdf 同样的页宽高与边距（mm → px，CSS 96dpi：1mm = 3.7795px）
const MM = 3.7795275591;
const page = await browser.newPage({
  viewport: { width: Math.round(338.67 * MM), height: Math.round(190.5 * MM) },
});
await page.goto(htmlPath, { waitUntil: 'load' });
await page.waitForTimeout(500);

const data = await page.evaluate(() => {
  const r2 = (r) => ({ x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1) });
  const box = (sel) => Array.from(document.querySelectorAll(sel)).map((e) => ({
    cls: (e.className || '').toString().slice(0, 40),
    tag: e.tagName,
    rect: r2(e.getBoundingClientRect()),
    txt: (e.textContent || '').trim().slice(0, 26),
  }));
  const body = document.body;
  const cs = getComputedStyle(body);
  const pg = document.querySelector('.pg');
  const pgcs = pg ? getComputedStyle(pg) : null;
  return {
    bodyWidth: body.getBoundingClientRect().width,
    bodyColumnCount: cs.columnCount,
    pgColumnCount: pgcs ? pgcs.columnCount : null,
    pgColumnFill: pgcs ? pgcs.columnFill : null,
    cover: box('.cover'),
    h2: box('h2.sec'),
    tldr: box('.tldr'),
    cards: box('.card'),
    boxes: box('.box'),
    t: box('h3.t'),
    gloss: box('.gloss'),
    figRow: box('.fig-row'),
    // 溢出检测：任何元素右边界超过 body 右边界
    overflow: Array.from(document.querySelectorAll('body *'))
      .filter((e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && (r.right > body.getBoundingClientRect().right + 1 || r.left < -1);
      })
      .slice(0, 12)
      .map((e) => ({ cls: (e.className || '').toString().slice(0, 36), tag: e.tagName,
                     rect: r2(e.getBoundingClientRect()) })),
  };
});

console.log('=== 容器 ===');
console.log('  body 宽度 =', data.bodyWidth.toFixed(1), 'px');
console.log('  body column-count =', data.bodyColumnCount);
console.log('  .pg column-count =', data.pgColumnCount, ', column-fill =', data.pgColumnFill);

console.log('\n=== 顶部色块（应只在最上面，高度远小于一页）===');
data.cover.forEach((e) => console.log(`  ${e.cls}  x=${e.rect.x} y=${e.rect.y} w=${e.rect.w} h=${e.rect.h}`));

console.log('\n=== 跨列节标题 h2.sec（column-span:all ⇒ w 应≈整行宽）===');
data.h2.slice(0, 6).forEach((e) => console.log(`  ${e.rect.w}×${e.rect.h} @(${e.rect.x},${e.rect.y})  ${e.txt}`));

console.log('\n=== 三栏数字卡 .tldr ===');
data.tldr.forEach((e) => console.log(`  ${e.rect.w}×${e.rect.h} @(${e.rect.x},${e.rect.y})`));

// 列边界推断：列宽 = (body 宽 - gap) / 2，gap 取 .pg 的 column-gap
const gap = await page.evaluate(() => {
  const pg = document.querySelector('.pg');
  return pg ? parseFloat(getComputedStyle(pg).columnGap) : 0;
});
const colW = (data.bodyWidth - gap) / 2;
console.log(`\n  推断：列宽 = ${colW.toFixed(1)}px，第 2 列起点 x ≈ ${colW.toFixed(1)} 或 ${(colW + gap).toFixed(1)}`);

const inCol = (x) => (x < colW + gap / 2 ? '左列' : '右列');
console.log('\n=== 卡片落列分布（前 12 张）===');
data.cards.slice(0, 12).forEach((e) =>
  console.log(`  ${inCol(e.rect.x)}  x=${e.rect.x} w=${e.rect.w} h=${e.rect.h}  ${e.txt}`));

console.log('\n=== 溢出元素（应为空）===');
if (!data.overflow.length) console.log('  无 ✓');
data.overflow.forEach((e) => console.log(`  ✘ ${e.tag}.${e.cls}  x=${e.rect.x} w=${e.rect.w} right=${(e.rect.x + e.rect.w).toFixed(1)}`));

await browser.close();
