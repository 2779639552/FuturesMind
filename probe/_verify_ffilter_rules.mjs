// 只读验收：⑨ 基本面截面表的**前端筛选/排序规则**（2026-10-16）
//
// 【为什么单独做这一层】浏览器 E2E 要 ~5 分钟、且依赖后端真实数据（今天哪些品种是"偏空"
//   会变）。而筛选/排序**规则本身**是纯函数，可以抽出模板里的源码、喂**合成行**在 node 里
//   确定性地跑 —— 秒级、可复现、且**能当变异自检的靶子**（改坏规则立刻红）。
//
// 【怎么抽】模板里 App 是对象字面量：常量写成 `_FRK_COLS: [ ... ],`、方法写成 `_fRkPass(r) { ... }`。
//   这里用花括号/方括号配平把需要的片段切出来，拼成一个新对象字面量求值。
//   `_FRK_DIMS` 里的 `short: v => App._fRkShortVd(v)` 引用的是 `App` ⇒ 求值器里先
//   `let App = null; App = { ... }`，箭头函数闭包到该作用域的 App（调用时已赋值）。
import fs from 'node:fs';

const ROOT = 'D:/AgentSense/AgentSense';
const S = fs.readFileSync(ROOT + '/web_template.html', 'utf8').replace(/\r\n/g, '\n');

// 取最长的 <script> 块（模板里有多个 script）
let js = '';
for (const m of S.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
  if (m[1].length > js.length) js = m[1];
}
if (js.length < 10000) { console.log('★ 内联 JS 抽取失败'); process.exit(1); }

function bal(src, from, open, close) {
  let d = 0;
  for (let i = from; i < src.length; i++) {
    const ch = src[i];
    if (ch === open) d++;
    else if (ch === close) { d--; if (d === 0) return i; }
  }
  throw new Error('unbalanced ' + open);
}
// ⚠ 属性/方法行首缩进不固定（补丁插入的常量块行首无空格、方法在 App 对象内 1 空格）
//   ⇒ 一律用正则容错，别写死 `'\n '`（第一版就这么写，报 prop not found）。
function grabProp(name, open, close) {
  const m = new RegExp('\\n[ \\t]*' + name + '\\s*:').exec(js);
  if (!m) throw new Error('prop not found: ' + name);
  const b = js.indexOf(open, m.index + m[0].length - 1);
  return name + ': ' + js.slice(b, bal(js, b, open, close) + 1);
}
function grabMethod(name) {
  const m = new RegExp('\\n[ \\t]*' + name + '\\s*\\(').exec(js);
  if (!m) throw new Error('method not found: ' + name);
  const b = js.indexOf('{', m.index + m[0].length);
  return name + js.slice(m.index + m[0].length - 1, bal(js, b, '{', '}') + 1);
}

const CONSTS = [
  grabProp('_FRK_COLS', '[', ']'),
  grabProp('_FRK_DIMS', '[', ']'),
  grabProp('_FRK_TEXT_RANK', '{', '}'),
  grabProp('_FRK_SORT_LABEL', '{', '}'),
  '_fRkFilter: { pool: "全部品种" }',
  '_fRkSort: { key: "net", order: "desc" }',
  '_fRkCols: null',
  '_fRkData: null',
].join(',\n');
const METHODS = ['_fRkShortVd', '_fRkShortStrategy', '_fRkNum', '_fRkSortVal', '_fRkTextFreq',
  '_fRkDimValues', '_fRkPass', '_fRkVisibleRows', '_fRkColHidden', '_fRkApplyCols']
  .map(grabMethod).join(',\n');

// 最小 DOM 桩（只覆盖这些方法真正用到的部分：搜索框取值 + 注入 <style>）
const styles = {};
const fakeSearch = { value: '' };
const doc = {
  getElementById: id => (id === 'f-rk-search' ? fakeSearch : (styles[id] || null)),
  createElement: () => ({ id: '', textContent: '' }),
  head: { appendChild: el => { styles[el.id] = el; } },
};
const ls = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

const factory = new Function('document', 'localStorage',
  'let App = null; App = {\n' + CONSTS + ',\n' + METHODS + '\n}; return App;');
const App = factory(doc, ls);

let pass = 0, fail = 0;
const ck = (ok, name, extra = '') => {
  if (ok) { pass++; console.log('  OK  ' + name + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★   ' + name + (extra ? '  [' + extra + ']' : '')); }
};

// ── 合成行：覆盖 有值 / 空值 / 未知取值 / 各分支 ──────────────────────────────
const mk = (o) => Object.assign({
  variety: 'XX', name: '某某', sector_bucket: '能化', basis: 0, basis_pct: 0.5, vr_ratio: 1,
  skill_net: 0, inv_grade: '中', inv_rank: 0, inv_mode: '口径分歧', skill_vd: '套利/对冲(适合正套)',
  skill_term: '偏多', skill_cyc: '做多', skill_prof: '做多', strategy: '未共振', prof_grade: '中',
  ck_pct: 0.5, ck_chg5: 0, soc_pct: 0.5, soc_chg4: 0, basis_rate: 0, close: 100,
}, o);
const ROWS = [
  mk({ variety: 'A1', name: '甲', sector_bucket: '能化', skill_term: '偏多', skill_cyc: '做多', vr_ratio: 5, skill_net: 2, strategy: '未共振' }),
  mk({ variety: 'A2', name: '乙', sector_bucket: '能化', skill_term: '偏空', skill_cyc: '做空', vr_ratio: null, skill_net: -1, strategy: '未共振' }),
  mk({ variety: 'B1', name: '丙', sector_bucket: '农产品', skill_term: '偏空', skill_cyc: '做空', vr_ratio: 2, skill_net: -2, strategy: '降级' }),
  mk({ variety: 'B2', name: '丁', sector_bucket: '农产品', skill_term: '偏多', skill_cyc: null, vr_ratio: 0.5, skill_net: 0, strategy: '未共振' }),
  mk({ variety: 'C1', name: '戊', sector_bucket: '能源', skill_term: '偏多', skill_cyc: '做多', vr_ratio: 9, skill_net: 3, strategy: '单边做多',
       inv_grade: '低', inv_rank: 1, inv_mode: '口径背离(库存转移迹象)' }),
  mk({ variety: 'C2', name: '己', sector_bucket: '能源', skill_term: '偏多', skill_cyc: '', vr_ratio: 0.1, skill_net: -3, strategy: '降级',
       inv_grade: '高', inv_rank: -1, inv_mode: '双口径共振', skill_vd: '单边做空' }),
  mk({ variety: 'D1', name: '庚', sector_bucket: '能源', skill_term: '偏多', skill_cyc: '做多', vr_ratio: 3, skill_net: 1, strategy: '未共振',
       inv_grade: '矛盾', inv_rank: null, inv_mode: '仅仓单口径' }),
];
App._fRkData = { rows: ROWS, pools: ['全部品种', '能化', '农产品', '能源'] };
App._fRkFilter = { pool: '全部品种' };
App._fRkSort = { key: 'net', order: 'desc' };
const ids = rs => rs.map(r => r.variety).join(',');

console.log('=== A. 筛选 ===');
ck(ids(App._fRkVisibleRows()).split(',').length === 7, '无筛选 ⇒ 全部 7 行');
App._fRkFilter.pool = '能化';
ck(ids(App._fRkVisibleRows()) === 'A1,A2', '板块筛选只按 sector_bucket', ids(App._fRkVisibleRows()));
App._fRkFilter = { pool: '全部品种', skill_term: '偏空' };
ck(ids(App._fRkVisibleRows()) === 'A2,B1', '维度筛选（期限结构=偏空）', ids(App._fRkVisibleRows()));
// ★ 口径关系是"取前缀"匹配：行里存的是 "口径背离(库存转移迹象)"
App._fRkFilter = { pool: '全部品种', inv_mode_kind: '口径背离' };
ck(ids(App._fRkVisibleRows()) === 'C1', '口径关系按**前缀**匹配（行里是"口径背离(库存转移迹象)"）',
  ids(App._fRkVisibleRows()));
// 多条件叠加（AND）
App._fRkFilter = { pool: '能源', skill_term: '偏多', skill_cyc: '做多' };
ck(ids(App._fRkVisibleRows()) === 'C1,D1', '多条件按 AND 叠加', ids(App._fRkVisibleRows()));
// 搜索（桩 search 元素）
App._fRkFilter = { pool: '全部品种' };
fakeSearch.value = 'C';
ck(ids(App._fRkVisibleRows()) === 'C1,C2', '搜索按 代码/名称 匹配', ids(App._fRkVisibleRows()));
fakeSearch.value = '';
// 筛到空
App._fRkFilter = { pool: '能化', skill_term: '偏多', skill_cyc: '做空' };
ck(App._fRkVisibleRows().length === 0, '条件互斥时返回 0 行（前端据此显示"没有符合条件的品种"）');
App._fRkFilter = { pool: '全部品种' };

console.log('=== B. 排序 ===');
App._fRkSort = { key: 'vr_ratio', order: 'desc' };
let r = App._fRkVisibleRows();
ck(r[0].variety === 'C1' && r[1].variety === 'A1', '数值列降序（9 > 5 在前）', ids(r));
ck(r[r.length - 1].variety === 'A2', '★ 空值（null）恒排最后（与后端 sort_key 同纪律）', ids(r));
App._fRkSort = { key: 'vr_ratio', order: 'asc' };
r = App._fRkVisibleRows();
ck(r[0].variety === 'C2', '数值列升序（0.1 在前）', ids(r));
ck(r[r.length - 1].variety === 'A2', '★ 升序时空值**仍在最后**（不是排到最前）');
App._fRkSort = { key: 'skill_cyc', order: 'desc' };
r = App._fRkVisibleRows();
const cyc = r.map(x => x.skill_cyc || '—');
ck(cyc.indexOf('做多') < cyc.indexOf('做空'), '文本列按多空方向：做多在做空之前', cyc.join(','));
ck(cyc.indexOf('做空') < cyc.indexOf('—'), '未知（无该票）恒最后', cyc.join(','));
App._fRkSort = { key: 'inv_grade', order: 'desc' };
r = App._fRkVisibleRows();
// ⚠ 别断言"第 2 个必是中"—— 数据里有 4 个"中"(第一版就这么写错了)。
//   要守的性质是**相对次序**: 低 在最前、高 在「矛盾」之前、矛盾 在最后。
const iv = r.map(x => x.inv_grade);
ck(iv[0] === '低', '库存档位降序：低 在最前（读行内 inv_rank）', iv.join(','));
ck(iv.indexOf('中') > 0 && iv.indexOf('高') > iv.indexOf('中'),
  '中 在低之后、高在中之后', iv.join(','));
ck(iv[iv.length - 1] === '矛盾', '「矛盾」（inv_rank 为 null）排最后', iv.join(','));
App._fRkSort = { key: 'strategy', order: 'desc' };
r = App._fRkVisibleRows();
// 【这条同时是"实现与注释是否一致"的判据】第一版实现写成 `v = -freq` ⇒ desc 之下
//   变成"低频在前"（只有 1 行的"单边做多"排最上面），而注释写的是"高频在前"。
const st = r.map(x => x.strategy);
ck(st[0] === '未共振', '★ 分组聚集：**高频在前**（未共振 4 行在最上）', st.join(','));
ck(st.indexOf('降级') > st.lastIndexOf('未共振'), '★ 同类相邻且按频次分块（降级 2 行成块在后）', st.join(','));
App._fRkSort = { key: 'net', order: 'desc' };
ck(App._fRkVisibleRows()[0].variety === 'C1', '按多空票净额降序（净票 3 在前）',
  String(App._fRkVisibleRows()[0].skill_net));
App._fRkSort = { key: 'variety', order: 'asc' };
r = App._fRkVisibleRows();
ck(ids(r).startsWith('A1,A2'), '文本排序键（品种代码）升序可用', ids(r));
App._fRkSort = { key: 'net', order: 'desc' };

console.log('=== C. 取值域派生 + 标签精简 ===');
const dimTerm = App._FRK_DIMS.find(d => d.key === 'skill_term');
ck(App._fRkDimValues(dimTerm).join('/') === '偏多/偏空', '取值从数据派生（不含空值）',
  App._fRkDimValues(dimTerm).join('/'));
const dimMode = App._FRK_DIMS.find(d => d.key === 'inv_mode_kind');
ck(App._fRkDimValues(dimMode).indexOf('口径背离') >= 0, '口径关系已归一为前缀（无括号长串）',
  App._fRkDimValues(dimMode).join('/'));
ck(App._FRK_DIMS.length === 8, '维度共 8 个', String(App._FRK_DIMS.length));
ck(App._fRkShortVd('套利/对冲(适合反套, 结合仓单有效期)') === '套利反套', '二维四象标签精简（反套）',
  App._fRkShortVd('套利/对冲(适合反套, 结合仓单有效期)'));
ck(App._fRkShortVd('套利/对冲(适合正套)') === '套利正套', '二维四象标签精简（正套）');
ck(App._fRkShortStrategy('部分共振(2/3 偏多)') === '部分共振·偏多', '综合结论标签精简',
  App._fRkShortStrategy('部分共振(2/3 偏多)'));

console.log('=== D. 列显隐 ===');
ck(App._fRkColHidden('idx') === false && App._fRkColHidden('variety') === false,
  '「#」「品种」固定不可隐藏（_fRkCols = null 时全部可见）');
App._fRkCols = ['idx', 'variety', 'close'];
ck(App._fRkColHidden('basis') === true, '未在可见清单里的列判为隐藏');
ck(App._fRkColHidden('idx') === false, '★ 即便清单里没写，fixed 列也不隐藏');
App._fRkApplyCols();
const css = (styles['f-rk-hidestyle'] || {}).textContent || '';
const basisIdx = App._FRK_COLS.findIndex(c => c.key === 'basis') + 1;
ck(css.indexOf('nth-child(' + basisIdx + ')') >= 0, '隐藏规则按**列序**生成 nth-child（basis = 第 ' + basisIdx + ' 列）',
  'nth-child(' + basisIdx + ')');
ck(css.indexOf('vr_ratio') < 0, 'CSS 里不含列名（只按序位，避免类名漂移）');
ck(/f-rk-exp-\]?[^}]*td\s*\{\s*display:\s*table-cell\s*!important/.test(css),
  '★ 行内展开行的 td 有兜底规则（防第 1 列隐藏时连带隐藏展开行）');
App._fRkCols = null;
App._fRkApplyCols();
ck(((styles['f-rk-hidestyle'] || {}).textContent || '').indexOf('nth-child') < 0,
  '恢复全显示后没有任何隐藏规则');

console.log('\nPASS ' + pass + ' / FAIL ' + fail);
process.exit(fail ? 1 : 0);
