// 【护栏】研报「季报」口径的季度二级选择器 —— 行为测试(2026-09-29)
//
// 为什么需要: 季报口径的交互不是"下拉里多一项", 而是**三层联动**
//   ① 类型选「季报」→ 日期下拉隐藏、季度下拉出现(选错口径整卡空白)
//   ② 二级下拉固定第一~第四季度(需求原话"分为第一到第四四个季度"),
//      而不是像日报那样按数据长出日期项
//   ③ 选中季度 → 写回日期下拉(作 value 载体)→ 各卡统一读日期下拉取值
// 这三步任何一步断了, 现象都是"选了季报什么都没显示", 而页面不报错 ——
// 纯文本断言(grep 某函数名存在)抓不到这种失效, 必须真的执行代码。
//
// 做法: 从 web_template.html 抠出 _quarterLabel / _quarterPickerModel /
//   _syncQuarterPicker 三个方法的真实源码, 在桩 DOM 上直接调用(FakeEl 模拟
//   innerHTML/value/appendChild 与 style.display), 断言行为而非文本。
//
// 用法: node tests/js/quarterly_picker_harness.mjs [模板路径]
//        退出码 0 = 全部通过, 1 = 有断言失败, 2 = 环境/取源码失败
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = process.argv[2] || path.join(ROOT, 'web_template.html');

if (!fs.existsSync(TEMPLATE)) {
  console.error(`模板不存在: ${TEMPLATE}`);
  process.exit(2);
}
const html = fs.readFileSync(TEMPLATE, 'utf8');
// 【必剥 \r】模板是 CRLF;不剥的话下面所有 `^  xxx(` 锚点都会因行尾 \r 失配。
const LINES = html.split('\n').map(l => l.replace(/\r$/, ''));

/** 取一行起、到同级缩进的 `},` 止的方法体(按行切, 不做花括号配平)。 */
function bodyLines(startRe) {
  const s = LINES.findIndex(l => startRe.test(l));
  if (s < 0) { console.error('找不到方法定义: ' + startRe); process.exit(2); }
  for (let i = s + 1; i < LINES.length; i++) {
    if (/^  \},?$/.test(LINES[i])) return LINES.slice(s, i + 1);
  }
  console.error('找不到方法结束: ' + startRe);
  process.exit(2);
}

const want = [
  /^  _quarterLabel\(date\) \{$/,
  /^  _quarterPickerModel\(dates\) \{$/,
  /^  _syncQuarterPicker\(qId, dId, rtype, dates, sigKey\) \{$/,
];
const parts = want.map(re => bodyLines(re).join('\n').trim().replace(/,\s*$/, ''));
const src = '({' + parts.join(',\n') + '})';

let pass = 0, fail = 0;
const check = (name, ok, extra = '') => {
  ok ? pass++ : fail++;
  console.log(`--- ${name} --- ${ok ? 'PASS' : 'FAIL'}${ok ? '' : '  ' + extra}`);
};

// ── 桩 DOM:模拟 innerHTML 重建 / appendChild / value / style.display ──
class FakeSel {
  constructor(id) { this.id = id; this._opts = []; this.value = ''; this.style = { display: '' }; }
  set innerHTML(v) { if (v === '') this._opts = []; }
  get innerHTML() { return this._opts.map(o => `<option value="${o.value}">${o.textContent}</option>`).join(''); }
  appendChild(o) { this._opts.push(o); }
  get options() { return this._opts; }
}
const mkDoc = (ids) => {
  const map = {};
  ids.forEach(i => { map[i] = new FakeSel(i); });
  return {
    getElementById: (id) => map[id] || null,
    // 【必给】_syncQuarterPicker 会 document.createElement('option') 再 appendChild,
    //   桩里缺这个方法 → 季报分支一进去就 TypeError(前 24 个断言全过, 掩得极深)。
    createElement: (tag) => ({ tagName: String(tag).toUpperCase(), value: '', textContent: '', style: {} }),
    __map: map,
  };
};

let App, doc;
const setup = (ids) => { doc = mkDoc(ids); global.document = doc; App = eval(src); };

const DATE = '2026-09-29';

// ── 1. 季度标签 ──
setup(['res-daily-quarter', 'res-daily-date']);
check('标签_Q3区间', App._quarterLabel('2026-07-01') === '2026Q3(7.1-9.30)', App._quarterLabel('2026-07-01'));
check('标签_Q1区间', App._quarterLabel('2026-01-01') === '2026Q1(1.1-3.31)', App._quarterLabel('2026-01-01'));
check('标签_Q4区间', App._quarterLabel('2026-10-01') === '2026Q4(10.1-12.31)', App._quarterLabel('2026-10-01'));
check('标签_Q2闰年末日', App._quarterLabel('2028-04-01') === '2028Q2(4.1-6.30)', App._quarterLabel('2028-04-01'));
check('标签_空值原样', App._quarterLabel('') === '');
check('标签_脏值原样', App._quarterLabel('2026-xx') === '2026-xx');

// ── 2. 季度模型:固定 4 项 + 年份取数据最大年 ──
setup(['q', 'd']);
{
  const m = App._quarterPickerModel([{ date: '2026-07-01', generated: true }, { date: '2026-04-01' }]);
  check('模型_固定四项', m.opts.length === 4, String(m.opts.length));
  check('模型_顺序一二三四', m.opts.map(o => o.label).join(',') === '第一季度,第二季度,第三季度,第四季度', m.opts.map(o => o.label).join(','));
  check('模型_季度键为季首月1日', m.opts.map(o => o.key).join(',') === '2026-01-01,2026-04-01,2026-07-01,2026-10-01', m.opts.map(o => o.key).join(','));
  check('模型_有数据标记', m.opts.map(o => o.has).join(',') === 'false,true,true,false', m.opts.map(o => o.has).join(','));
  check('模型_已生成标记', m.opts.map(o => o.generated).join(',') === 'false,false,true,false', m.opts.map(o => o.generated).join(','));
  // 年份取数据最大年(跨年时不能写死 2026)
  const m2 = App._quarterPickerModel([{ date: '2027-04-01' }]);
  check('模型_年份随数据', m2.year === 2027 && m2.opts[1].key === '2027-04-01', JSON.stringify(m2.opts[1]));
  // 无数据 → 回退当年, 四项仍齐
  const m3 = App._quarterPickerModel([]);
  check('模型_无数据仍四项', m3.opts.length === 4 && m3.opts.every(o => !o.has));
}

// ── 3. 非季报口径:季度下拉必须隐藏, 日期下拉显示, 返回 null ──
for (const rt of ['', '日报', '周报']) {
  setup(['res-daily-quarter', 'res-daily-date']);
  const r = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', rt, [{ date: '2026-04-01' }], '_sigA');
  const q = doc.__map['res-daily-quarter'], d = doc.__map['res-daily-date'];
  check(`非季报(${rt || '空'})_季度下拉隐藏`, q.style.display === 'none', q.style.display);
  check(`非季报(${rt || '空'})_日期下拉显示`, d.style.display === '', d.style.display);
  check(`非季报(${rt || '空'})_返回null`, r === null, String(r));
}

// ── 4. 季报口径:显示季度下拉 / 隐藏日期下拉 / 写回季度键 ──
setup(['res-daily-quarter', 'res-daily-date']);
{
  const key = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date',
    '季报', [{ date: '2026-07-01', generated: true }], '_sigB');
  const q = doc.__map['res-daily-quarter'], d = doc.__map['res-daily-date'];
  check('季报_季度下拉显示', q.style.display === '', q.style.display);
  check('季报_日期下拉隐藏', d.style.display === 'none', d.style.display);
  check('季报_返回季度键', key === '2026-07-01', String(key));
  check('季报_下拉含四季度', q.options.length === 4, String(q.options.length));
  check('季报_有数据季度默认选中', q.value === '3', '选中=' + q.value);  // 只有 Q3 有数据
  check('季报_已生成打勾', q.options.find(o => o.value === '3').textContent.includes('✓'),
    q.options.find(o => o.value === '3').textContent);
  check('季报_无数据显示暂无', q.options.find(o => o.value === '1').textContent.includes('暂无'),
    q.options.find(o => o.value === '1').textContent);
}

// ── 4b. 【默认落点】多个季度有数据时,必须默认选中**最新**那个 ──
// 季报稀疏(一年 4 期),默认落"最早有数据的季度"常是空口径(Q2 那期本品种无条目)
// ⇒ 卡片空白,用户读成"选季报没反应"。回归护栏:取最新有数据季度(2026-09-29)。
{
  setup(['res-daily-quarter', 'res-daily-date']);
  const key = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报',
    [{ date: '2026-04-01' }, { date: '2026-07-01' }], '_sigLatest');
  const q = doc.__map['res-daily-quarter'];
  check('默认_取最新有数据季度(非最早)', q.value === '3', '选中=' + q.value);
  check('默认_返回最新季度键', key === '2026-07-01', String(key));
  // 只有 Q1 有数据 → 仍取 Q1
  setup(['res-daily-quarter', 'res-daily-date']);
  const k2 = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报',
    [{ date: '2026-01-01' }], '_sigQ1');
  check('默认_仅Q1有数据则选Q1', doc.__map['res-daily-quarter'].value === '1' && k2 === '2026-01-01',
    doc.__map['res-daily-quarter'].value + '/' + k2);
  // 横跨两年(只有 Q3 有数据且是 2026 年)→ 年份随数据,默认仍命中该季
  setup(['res-daily-quarter', 'res-daily-date']);
  const k3 = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报',
    [{ date: '2026-07-01' }], '_sigY');
  check('默认_跨年命中当年Q3', doc.__map['res-daily-quarter'].value === '3' && k3 === '2026-07-01',
    doc.__map['res-daily-quarter'].value + '/' + k3);
}

// ── 5. 防打断:同一份数据重复调用不重建下拉(保住用户已选的季度) ──
setup(['res-daily-quarter', 'res-daily-date']);
{
  const dates = [{ date: '2026-04-01' }, { date: '2026-07-01' }];
  App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报', dates, '_sigC');
  const q = doc.__map['res-daily-quarter'];
  q.value = '4';                                   // 用户手选 Q4(无数据也要保得住)
  const optsBefore = q.options;
  const key2 = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报', dates, '_sigC');
  check('防打断_未重建', doc.__map['res-daily-quarter'].options === optsBefore);
  check('防打断_保住用户选择', doc.__map['res-daily-quarter'].value === '4', doc.__map['res-daily-quarter'].value);
  check('防打断_返回Q4键', key2 === '2026-10-01', String(key2));
  // 数据变了(新季度出现) → 允许重建, 但仍尽量保住用户当前选值
  const key3 = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报',
    dates.concat([{ date: '2026-10-01' }]), '_sigC');
  check('防打断_数据变更后重建', doc.__map['res-daily-quarter'].options.length === 4);
  check('防打断_重建后仍选Q4', doc.__map['res-daily-quarter'].value === '4', doc.__map['res-daily-quarter'].value);
  check('防打断_重建后键正确', key3 === '2026-10-01', String(key3));
}

// ── 6. 元素缺失 → 安全返回 null(不得抛错, 免得整卡白屏) ──
setup(['res-daily-quarter']);  // 故意缺日期下拉
{
  let threw = false, r = 'x';
  try { r = App._syncQuarterPicker('res-daily-quarter', 'res-daily-date', '季报', [], '_sigD'); }
  catch (e) { threw = true; }
  check('缺元素_不抛错', !threw);
  check('缺元素_返回null', r === null, String(r));
}

// ── 7. 模板接线:三张卡都必须真的调用同步函数(漏一处 = 该卡选季报无反应) ──
for (const [qid, did] of [['res-daily-quarter', 'res-daily-date'],
                          ['res-vsum-quarter', 'res-vsum-date'],
                          ['res-views-quarter', 'res-views-date']]) {
  check(`接线_${qid}`, html.includes(`'${qid}'`) && html.includes(`'${did}'`));
}
check('接线_调用次数>=4(定义1+调用3)', (html.match(/_syncQuarterPicker\(/g) || []).length >= 4,
  String((html.match(/_syncQuarterPicker\(/g) || []).length));
check('下拉_季报选项共5处', (html.match(/<option value="季报">季报<\/option>/g) || []).length === 5,
  String((html.match(/<option value="季报">季报<\/option>/g) || []).length));

console.log(`\nRESULT: ${fail === 0 ? 'ALL_PASS' : 'FAILED'}(${pass})  fail=${fail}`);
process.exit(fail === 0 ? 0 : 1);
