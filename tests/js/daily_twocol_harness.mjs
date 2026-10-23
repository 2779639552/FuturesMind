// 【护栏】研报「每日总结」三/四节横向排列（2026-10-21 用户要求：左「三、可信程度分析」右「四、综合结论与关注要点」）
//
// 为什么需要: 这次改的是**节级 DOM 结构**，不是样式。纯文本断言（grep 有没有
//   .rv-twocol）抓不到真正的失效：
//     ① 只包了一节，另一节留在外面 → 页面看着"变两列了"但右边是空的；
//     ② 搬节点顺序写反（先 appendChild 搬走 h3 再 insertBefore）→ 抛 NotFoundError，
//        被 loadDailySummary 的 catch 吃掉 ⇒ 整卡变「加载失败」；
//     ③ 第四节后面的第五节被一起卷进去 → 后面的小节凭空消失。
//   所以必须真的执行方法体，在桩 DOM 上断言"哪个节点在哪个父级、什么顺序"。
//
// 做法: 按花括号配平从模板抠出 _layoutDailyTwoCol 的真实源码（模板已压平缩进，
//   不能按行尾 '  },' 判结束），配一个最小 DOM 桩执行。
//
// 用法: node tests/js/daily_twocol_harness.mjs [模板路径]
//        退出码 0 = 全通过 / 1 = 有断言失败 / 2 = 环境或取源码失败
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = process.argv[2] || path.join(ROOT, 'web_template.html');
if (!fs.existsSync(TEMPLATE)) { console.error(`模板不存在: ${TEMPLATE}`); process.exit(2); }

const html = fs.readFileSync(TEMPLATE, 'utf8');
const LINES = html.split('\n').map(l => l.replace(/\r$/, ''));

// ── 取方法体: 按花括号配平（不看缩进，模板已压平为 1 空格/层甚至顶格）──
function extractMethod(name) {
  const s = LINES.findIndex(l => new RegExp('^\\s*' + name + '\\s*\\([^)]*\\)\\s*\\{$').test(l));
  if (s < 0) { console.error('找不到方法定义: ' + name); process.exit(2); }
  let depth = 0, started = false;
  for (let i = s; i < LINES.length; i++) {
    for (const ch of LINES[i]) {
      if (ch === '{') { depth++; started = true; }
      else if (ch === '}') depth--;
    }
    if (started && depth === 0) return LINES.slice(s, i + 1).join('\n');
  }
  console.error('方法花括号未闭合: ' + name); process.exit(2);
}

const SRC = extractMethod('_layoutDailyTwoCol').replace(/,\s*$/, '');
const App = eval('({' + SRC + '})');

// ── 最小 DOM 桩 ──
class N {
  constructor(tag, text) {
    this.tagName = String(tag).toUpperCase();
    this._text = text == null ? '' : String(text);
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.className = '';
    this.style = {};
  }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  get nextElementSibling() {
    const p = this.parentNode; if (!p) return null;
    const i = p.children.indexOf(this);
    return i < 0 ? null : (p.children[i + 1] || null);
  }
  appendChild(c) {
    if (c.parentNode) c.parentNode.children.splice(c.parentNode.children.indexOf(c), 1);
    c.parentNode = this; this.children.push(c); return c;
  }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.children.splice(n.parentNode.children.indexOf(n), 1);
    const i = ref ? this.children.indexOf(ref) : this.children.length;
    if (i < 0) { const e = new Error('insertBefore: ref 不在该父级下'); e.name = 'NotFoundError'; throw e; }
    n.parentNode = this; this.children.splice(i, 0, n); return n;
  }
  querySelectorAll(sel) {
    const want = String(sel).toUpperCase(), out = [];
    const walk = n => n.children.forEach(c => { if (c.tagName === want) out.push(c); walk(c); });
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
globalThis.document = { createElement: t => new N(t) };

// ── 构造一份"像真总结"的树 ──
function buildDoc(opts = {}) {
  const root = new N('div'); root.id = 'res-daily';
  const box = new N('div'); box.className = 'report-content';
  root.appendChild(box);

  box.appendChild(new N('h1', '研报每日总结 2026-10-21'));
  box.appendChild(new N('p', '当日共 12 条研报'));
  // prefixH2: 在「一、总表」之前再插一节 ⇒ 三/四不再固定落在下标 2/3，
  //   用来咬死"写死下标定位"的回归（标题级数会随 prompt 演进而变）。
  if (opts.prefixH2) box.appendChild(new N('h2', '〇、明日关注'));
  box.appendChild(new N('h2', '一、当日观点总表'));
  box.appendChild(new N('table', '总表'));
  box.appendChild(new N('h2', '二、观点对比与冲突'));
  box.appendChild(new N('table', '冲突表'));
  const h3 = new N('h2', opts.t3 || '三、可信程度分析');
  box.appendChild(h3);
  const ul3 = new N('ul', '可信度高: 华泰(数字充分)');
  box.appendChild(ul3);
  const p3 = new N('p', '拥挤风险提示');
  box.appendChild(p3);
  if (!opts.noFour) {
    const h4 = new N('h2', opts.t4 || '四、综合结论与关注要点');
    box.appendChild(h4);
    box.appendChild(new N('ul', '关注 10/25 库存数据'));
    box.appendChild(new N('p', '净倾向偏多'));
  }
  if (opts.tailH2) {
    box.appendChild(new N('h2', '五、风险提示'));
    box.appendChild(new N('p', '政策风险'));
  }
  return { root, box, h3, ul3, p3 };
}

let pass = 0, fail = 0;
const check = (name, ok, extra = '') => { ok ? pass++ : fail++; console.log(`--- ${name} --- ${ok ? 'PASS' : 'FAIL'}${ok ? '' : '  ' + extra}`); };

// ── 1. 正常两节: 生成一列组，左三右四，内容完整 ──
{
  const { root, box } = buildDoc();
  App._layoutDailyTwoCol(root);
  const wraps = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol');
  check('正常: 只生成一个两列容器', wraps.length === 1, `实际 ${wraps.length}`);
  const w = wraps[0];
  check('正常: 容器恰好两列', w && w.children.length === 2, w ? `实际 ${w.children.length}` : '无容器');
  const L = w && w.children[0], R = w && w.children[1];
  check('正常: 左列标题是三', L && (L.children[0] || {}).textContent === '三、可信程度分析', L ? (L.children[0] || {}).textContent : '—');
  check('正常: 左列含本节全部节点(3)', L && L.children.length === 3, L ? `实际 ${L.children.length}` : '—');
  check('正常: 右列标题是四', R && (R.children[0] || {}).textContent === '四、综合结论与关注要点', R ? (R.children[0] || {}).textContent : '—');
  check('正常: 右列含本节全部节点(3)', R && R.children.length === 3, R ? `实际 ${R.children.length}` : '—');
  // buildDoc 顺序: 0 h1 / 1 p / 2 h2一 / 3 table / 4 h2二 / 5 table / 6 h3 ⇒ 容器应落在 6
  check('正常: 两列容器插在第二节表格之后', box.children[6] === w,
    `box[6]=${box.children[6] && box.children[6].textContent} / box[5]=${box.children[5] && box.children[5].textContent}`);
  // grid 子项默认 min-width:auto，长表格（比如可信度分层用表）会把单列撑破 → 横向溢出
  check('正常: 两列都带 min-width:0', L && R && /min-width:\s*0/.test(L.style.cssText)
    && /min-width:\s*0/.test(R.style.cssText),
    `${L && L.style.cssText} | ${R && R.style.cssText}`);
  check('正常: 列宽用 auto-fit 自适应堆叠', /auto-fit/.test(w.style.cssText), w.style.cssText);
  check('正常: 容器带幂等标记', w && w.dataset.rvTwoCol === '1', w ? String(w.dataset.rvTwoCol) : '—');
  check('正常: root 上不留标记（否则切日期后被误判已排过）', root.dataset.rvTwoCol === undefined);
  const all = root.querySelectorAll('h2').map(h => h.textContent);
  check('正常: 标题总数不变(4)', all.length === 4, all.join(' | '));
  check('正常: 一二节仍在容器之前',
    box.children[0].tagName === 'H1' && box.children[2].textContent === '一、当日观点总表'
    && box.children[4].textContent === '二、观点对比与冲突',
    box.children.map(c => c.textContent).join(' | '));
}

// ── 2. 幂等: 再调一次不新增容器、不重排 ──
{
  const { root, box } = buildDoc();
  App._layoutDailyTwoCol(root); App._layoutDailyTwoCol(root);
  const wraps = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol');
  check('幂等: 重复调用仍只一个容器', wraps.length === 1, `实际 ${wraps.length}`);
}

// ── 3. 缺第四节: 原样不动（绝不半包）──
{
  const { root, box } = buildDoc({ noFour: true });
  const before = box.children.length;
  App._layoutDailyTwoCol(root);
  const wraps = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol');
  check('缺四节: 不生成容器', wraps.length === 0, `实际 ${wraps.length}`);
  check('缺四节: 节点数不变', box.children.length === before);
  check('缺四节: 不打幂等标记（将来补上四节还能重排）', root.dataset.rvTwoCol !== '1');
}

// ── 4. 四节后面还有五节: 五节留在容器之外 ──
{
  const { root, box } = buildDoc({ tailH2: true });
  App._layoutDailyTwoCol(root);
  const w = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol')[0];
  check('有后续节: 容器存在', !!w);
  check('有后续节: 五节未被卷进右列', !!w && w.children[1].children.length === 3, w ? `右列 ${w.children[1].children.length}` : '—');
  const tail = box.children[box.children.length - 1];
  check('有后续节: 五节仍在容器之后', tail && tail.textContent === '政策风险', tail ? tail.textContent : '—');
  const h5 = root.querySelectorAll('h2').find(h => h.textContent === '五、风险提示');
  check('有后续节: 五节父级仍是正文容器', h5 && h5.parentNode === box);
}

// ── 5. 阿拉伯数字标题也认（3、/4、）──
{
  const { root, box } = buildDoc({ t3: '3、可信程度分析', t4: '4、综合结论与关注要点' });
  App._layoutDailyTwoCol(root);
  const wraps = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol');
  check('阿拉伯序号: 也生成两列', wraps.length === 1, `实际 ${wraps.length}`);
}

// ── 6. 两节不同父级: 不动（避免跨容器搬节点）──
{
  const root = new N('div');
  const a = new N('div'), b = new N('div');
  root.appendChild(a); root.appendChild(b);
  a.appendChild(new N('h2', '三、可信程度分析'));
  b.appendChild(new N('h2', '四、综合结论与关注要点'));
  App._layoutDailyTwoCol(root);
  const wraps = root.querySelectorAll('div').filter(d => d.className === 'rv-twocol');
  check('跨父级: 不重排', wraps.length === 0, `实际 ${wraps.length}`);
}

// ── 7. 标题顺序颠倒(四在三之前): 不动 ──
{
  const root = new N('div');
  root.appendChild(new N('h2', '四、综合结论与关注要点'));
  root.appendChild(new N('p', 'x'));
  root.appendChild(new N('h2', '三、可信程度分析'));
  App._layoutDailyTwoCol(root);
  check('顺序颠倒: 不重排', root.querySelectorAll('div').filter(d => d.className === 'rv-twocol').length === 0);
}

// ── 8. 空/无参: 不抛异常 ──
{
  let err = '';
  try { App._layoutDailyTwoCol(null); App._layoutDailyTwoCol(undefined); } catch (e) { err = String(e); }
  check('空参: 不抛异常', err === '', err);
}

// ── 9. 前面多一节(三/四不在下标 2/3): 仍按文本定位 ──
{
  const { root, box } = buildDoc({ prefixH2: true });
  App._layoutDailyTwoCol(root);
  const w = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol')[0];
  check('多一节: 容器存在', !!w);
  check('多一节: 左列仍是「三、可信程度分析」', w && w.children[0].children[0].textContent === '三、可信程度分析',
    w ? w.children[0].children[0].textContent : '—');
  check('多一节: 右列仍是「四、综合结论与关注要点」', w && w.children[1].children[0].textContent === '四、综合结论与关注要点',
    w ? w.children[1].children[0].textContent : '—');
  const h0 = root.querySelectorAll('h2').find(h => h.textContent === '〇、明日关注');
  check('多一节: 前面那节未被卷入', h0 && h0.parentNode === box);
}

// ── 10. ★ 切日期＝innerHTML 重渲染：容器被清掉后，新内容必须**重新**两列 ──
//   这是真实生命周期：loadDailySummary 每次 `el.innerHTML = ...` 只重建子节点，
//   #res-daily 自身的属性会留下来。若幂等闸门挂在 root.dataset 上，第二次渲染直接 return
//   ⇒ 两列只在首次生效、切一次日期就静默变回上下排列（页面不报错）。
{
  const { root, box } = buildDoc();
  App._layoutDailyTwoCol(root);
  const w = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol')[0];
  check('重渲染前: 已两列', !!w);
  // 模拟 el.innerHTML = ... 重建子节点：旧容器与旧内容一并消失，root 自身属性保留
  box.children.slice().forEach(() => {});
  box.children.forEach(c => { c.parentNode = null; });
  box.children = [];
  const h3n = new N('h2', '三、可信程度分析');
  box.appendChild(h3n);
  box.appendChild(new N('ul', '新的可信度列表'));
  box.appendChild(new N('h2', '四、综合结论与关注要点'));
  box.appendChild(new N('ul', '新的关注要点'));
  App._layoutDailyTwoCol(root);
  const w2 = box.querySelectorAll('div').filter(d => d.className === 'rv-twocol')[0];
  check('重渲染后: 仍然两列（未被 root 上的旧标记挡住）', !!w2);
  check('重渲染后: 左列是新内容', w2 && w2.children[0].children[0] === h3n);
  check('重渲染后: 仍是单一容器不叠套', box.querySelectorAll('div').filter(d => d.className === 'rv-twocol').length === 1);
}

console.log(`\n===== ${pass} passed / ${fail} failed =====`);
process.exit(fail ? 1 : 0);
