# -*- coding: utf-8 -*-
"""在原 E2E 结尾追加 [8] 段：国君页覆盖矩阵回归验证。"""
import io

P = 'probe/_e2e_page_tools.mjs'
raw = io.open(P, 'rb').read()
orig = raw

ANCHOR = """console.log(`\\nJS 报错: ${errs.length ? errs.slice(0, 3).join(' | ') : '(无)'}`);"""

SECTION = r"""// ── 8) 国君页「覆盖矩阵」: 必须进入小模块 + 不吸顶遮挡 ──────────
// 【为什么单列一段】覆盖矩阵曾被放在 .page-tools 内部 ⇒ ①_secScan 整块跳过它,
//   索引里没有它; ②.page-tools 吸顶 + 实色底, 21 行大表格整块钉在顶部遮住下方模块。
console.log('\n[8] 国君页覆盖矩阵(进小模块 + 不吸顶)');
{
  await page.evaluate(() => App.switchTab('gtja'));
  await page.waitForTimeout(2200);
  const r = await page.evaluate(() => {
    const el = document.getElementById('tab-gtja');
    const mat = document.getElementById('gt-matrix');
    const card = mat ? mat.closest('.card') : null;
    const pt = el.querySelector(':scope > .page-tools');
    const kids = Array.from(el.children);
    // 索引里的标签文字
    const labels = Array.from(el.querySelectorAll('.sec-index-list .sec-index-item'))
      .map(b => (b.textContent || '').replace('✓', '').trim());
    // 覆盖矩阵卡是否被 .page-tools 包着
    const inPageTools = !!(card && card.closest('.page-tools'));
    // 是否被折叠外壳包住(应为是 —— 它现在是小模块)
    const inBlock = !!(card && card.closest('.sec-block'));
    // 吸顶容器高度: 只应包含"① 查询控制"一张矮卡
    const ptH = pt ? Math.round(pt.getBoundingClientRect().height) : -1;
    // 控制条内卡片数(应=1)
    const ptCards = pt ? pt.querySelectorAll(':scope > .card').length : -1;
    // 覆盖矩阵卡自身 position(不应 sticky)
    const matCardPos = card ? getComputedStyle(card).position : 'n/a';
    // badge 位置(应挂在覆盖矩阵标题下)
    const badge = document.getElementById('gt-matrix-badge');
    const badgeInMatrixH3 = !!(badge && badge.closest('h3') &&
      /覆盖矩阵/.test(badge.closest('h3').textContent || ''));
    return {
      hasMatrix: !!mat, inPageTools, inBlock, ptH, ptCards, matCardPos,
      labels, badgeInMatrixH3,
      inIdx: !!el.querySelector('.sec-index-list'),
    };
  });
  log(r.hasMatrix, '#gt-matrix 存在');
  log(r.inPageTools === false, `覆盖矩阵已移出 .page-tools (inPageTools=${r.inPageTools})`);
  log(r.inBlock === true, `覆盖矩阵已进小模块折叠块 (inBlock=${r.inBlock})`);
  log(r.inIdx && r.labels.some(s => /覆盖矩阵/.test(s)),
      `索引中出现「覆盖矩阵」: [${r.labels.join(' | ')}]`);
  log(r.ptCards === 1, `控制条内只余 1 张矮卡 (ptCards=${r.ptCards})`);
  log(r.ptH > 0 && r.ptH < 200, `控制条高度合理、不会遮屏 (h=${r.ptH}px)`);
  log(r.matCardPos !== 'sticky', `覆盖矩阵卡 position=${r.matCardPos} (期望非 sticky)`);
  log(r.badgeInMatrixH3, `badge #gt-matrix-badge 挂在「覆盖矩阵」标题下 (${r.badgeInMatrixH3})`);

  // 数据集: 展开覆盖矩阵后，滚动页面，验证它**不**跟随吸顶
  const stick = await page.evaluate(async () => {
    const el = document.getElementById('tab-gtja');
    // 先确保矩阵可见
    const card = document.getElementById('gt-matrix').closest('.card');
    const blk = card.closest('.sec-block');
    if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
    await new Promise(r => requestAnimationFrame(r));
    const before = card.getBoundingClientRect().top;
    window.scrollTo(0, 900);
    await new Promise(r => setTimeout(r, 260));
    const after = card.getBoundingClientRect().top;
    const pt = el.querySelector(':scope > .page-tools');
    const ptAfter = pt ? pt.getBoundingClientRect().top : null;
    window.scrollTo(0, 0);
    return { before: Math.round(before), after: Math.round(after), ptAfter: ptAfter === null ? null : Math.round(ptAfter) };
  });
  // 覆盖矩阵应随页面滚走(top 明显上移 ⇒ 负值)，而不是被钉住
  log(stick.after < stick.before - 100 || stick.after < 0,
      `覆盖矩阵随页面滚动上移、未吸顶 (before=${stick.before}px → after=${stick.after}px)`);
  log(stick.ptAfter !== null && stick.ptAfter >= 40 && stick.ptAfter <= 80,
      `控制条仍正常吸顶 (滚动后 top=${stick.ptAfter}px, 期望≈56px)`);
}

"""

assert raw.count(ANCHOR.encode()) == 1, 'anchor count=%d' % raw.count(ANCHOR.encode())
raw = raw.replace(ANCHOR.encode(), SECTION.encode('utf-8') + ANCHOR.encode())
assert raw != orig
assert raw.count(b'\r\n') == 0, 'LF file got CRLF'
io.open(P, 'wb').write(raw)
print('OK delta %+d' % (len(raw) - len(orig)))
