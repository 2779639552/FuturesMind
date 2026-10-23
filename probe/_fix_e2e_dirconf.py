# -*- coding: utf-8 -*-
"""修 E2E 两处（2026-10-16）：
  ① 颜色判据太粗 —— 橙色(琥珀)的 R 通道也高,被 isRed 误判成红 ⇒ 改成三色 RGB 组合判别。
  ② B 表（逐品种观点总览）样本只有"中性" ⇒ 遍历品种找到含看多/看空的再断言
     （否则"看多=红"在 B 表其实没被验到,只在 A 表验了）。
"""
from pathlib import Path

P = Path("probe/_e2e_dir_conf_viz.mjs")
s = P.read_text(encoding="utf-8")

# ① 三色判别替换掉粗糙的 isRed/isGreen
OLD_C = """// 红 #e24b4a 类 / 绿 #1d9e75 类：只比较"红通道 > 绿通道"这种稳健判据，不写死 hex
const isRed = rgb => { const m = (rgb || '').match(/(\\d+),\\s*(\\d+),\\s*(\\d+)/); return m && +m[1] > +m[3] + 30; };
const isGreen = rgb => { const m = (rgb || '').match(/(\\d+),\\s*(\\d+),\\s*(\\d+)/); return m && +m[3] > +m[1] + 30; };"""
NEW_C = """// 【三色判别】按 RGB 组合分类,不写死 hex（主题/亮暗会换色值）。
// ⚠ 第一版用"R 通道 > B 通道"判红 —— 结果**橙色(琥珀)的 R 也高**,中性被误判成看多 ⇒ 假 FAIL。
//   正确判据要看**绿通道**: 红 G 很低(69)、橙 G 中等(159)、绿 G 很高(209)。
const cls = rgb => {
  const m = (rgb || '').match(/(\\d+),\\s*(\\d+),\\s*(\\d+)/);
  if (!m) return '?';
  const r = +m[1], g = +m[2];
  if (g < 110) return 'red';
  if (r < 130) return 'green';
  return 'amber';
};
const isRed = rgb => cls(rgb) === 'red';
const isGreen = rgb => cls(rgb) === 'green';
const isAmber = rgb => cls(rgb) === 'amber';"""
assert s.count(OLD_C) == 1, ("颜色判据锚点", s.count(OLD_C))
s = s.replace(OLD_C, NEW_C, 1)

# 中性判据：改用 isAmber
OLD_N = """ck(colors.flat.every(c => !isRed(c.color) && !isGreen(c.color)), '中性 = 琥珀（既非红也非绿）',
  colors.flat.map(c => c.txt + ':' + c.color).slice(0, 2).join(' | '));"""
NEW_N = """ck(colors.flat.every(c => isAmber(c.color)), '中性 = 琥珀（既非红也非绿）',
  colors.flat.map(c => c.txt + ':' + c.color).slice(0, 2).join(' | '));"""
assert s.count(OLD_N) == 1, ("中性判据锚点", s.count(OLD_N))
s = s.replace(OLD_N, NEW_N, 1)

# ② B 表：遍历品种，找到含看多/看空的再断言
OLD_B = """ck(bstat && bstat.rows > 0, '逐品种总览已加载', '行数=' + (bstat ? bstat.rows : '-'));
if (bstat && bstat.chips) {
  const ups = bstat.chipSample.filter(c => c.txt.includes('多'));
  const downs = bstat.chipSample.filter(c => c.txt.includes('空'));
  if (ups.length) ck(ups.every(c => isRed(c.color)), '★★ 总览：看多 = 红',
    ups.map(c => c.txt + ':' + c.color).join(' | '));
  if (downs.length) ck(downs.every(c => isGreen(c.color)), '★★ 总览：看空 = 绿',
    downs.map(c => c.txt + ':' + c.color).join(' | '));
}"""
NEW_B = """ck(bstat && bstat.rows > 0, '逐品种总览已加载', '行数=' + (bstat ? bstat.rows : '-'));

// 【★ 为什么要遍历】被选中的第一个品种当天可能只有"中性"观点 ⇒ 那样"看多=红/看空=绿"
//   在 B 表其实**没被验到**（只验了 A 表）。遍历品种直到找到含看多/看空的样本,
//   找不到就如实报"未取到样本",不假装通过。
const allChips = await page.evaluate(() => {
  const el = document.getElementById('res-views');
  return el ? [...el.querySelectorAll('.rv-chip')].map(c => ({
    txt: c.textContent.trim(), color: getComputedStyle(c).color,
  })) : [];
});
let bUps = allChips.filter(c => c.txt.includes('多'));
let bDowns = allChips.filter(c => c.txt.includes('空'));
if (!bUps.length || !bDowns.length) {
  const codes = await page.evaluate(() => {
    const sel = document.getElementById('res-views-variety');
    return sel ? [...sel.options].map(o => o.value).filter(Boolean) : [];
  });
  for (const code of codes.slice(0, 10)) {
    await page.evaluate(c => {
      const sel = document.getElementById('res-views-variety');
      if (sel) { sel.value = c; }
      return App.loadResearchViews && App.loadResearchViews();
    }, code);
    for (let i = 0; i < 25; i++) {
      const got = await page.evaluate(() => {
        const el = document.getElementById('res-views');
        return el && el.innerText && !el.innerText.includes('加载中')
          ? [...el.querySelectorAll('.rv-chip')].map(c => ({ txt: c.textContent.trim(), color: getComputedStyle(c).color }))
          : null;
      });
      if (got) {
        const u = got.filter(c => c.txt.includes('多'));
        const d = got.filter(c => c.txt.includes('空'));
        if (u.length) bUps = bUps.concat(u);
        if (d.length) bDowns = bDowns.concat(d);
        break;
      }
      await sleep(400);
    }
    if (bUps.length && bDowns.length) { console.log('    找到样本品种: ' + code); break; }
  }
}
if (bUps.length) ck(bUps.every(c => isRed(c.color)), '★★ 总览：看多 = 红',
  bUps.map(c => c.txt + ':' + c.color).slice(0, 3).join(' | '));
else ck(false, '★★ 总览：看多 = 红（未取到看多样本）');
if (bDowns.length) ck(bDowns.every(c => isGreen(c.color)), '★★ 总览：看空 = 绿',
  bDowns.map(c => c.txt + ':' + c.color).slice(0, 3).join(' | '));
else ck(false, '★★ 总览：看空 = 绿（未取到看空样本）');"""
assert s.count(OLD_B) == 1, ("B 表锚点", s.count(OLD_B))
s = s.replace(OLD_B, NEW_B, 1)

P.write_text(s, encoding="utf-8", newline="")
print("  ✔ E2E 已修：三色判据 + B 表遍历取样")
