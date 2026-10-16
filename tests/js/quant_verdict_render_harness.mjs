// 【护栏】方法论研判卡(_renderVerdict/_mdLite)渲染契约测试
//
// 为什么需要: 研判结论直接面向交易决策, 渲染层一旦把后端字段漏成字面量
//   (Python 的 None / False / True) 或漏掉四段式中的某段, 界面会"看起来正常但
//   实际缺依据" —— 静态检查与后端单测都抓不到(后端 JSON 是正确的)。
//
// 断言(每条都对应一类真实缺陷):
//   1) 五个区块齐全: 四段式 / 各体系方向票 / 冲突并列 / 依据 / 边界与缺口
//   2) 无 undefined、无 [object Object]
//   3) 无 Python 字面量 None / True / False  (2026-09-28 实测泄漏过)
//   4) 方向词存在(做多/做空/暂不操作三态之一)
//   5) 【交叉断言】几何块「双线同破」渲染 == 后端布尔
//   6) 【交叉断言】后端 desc 说"双线同破"时, 几何块必须显示"是"(防自相矛盾)
//
// 用法:
//   1) 先用后端生成 fixture(真实接口数据):
//      AGENTSENSE_AUTH_DISABLE=1 python -c "
//        import json, web_app
//        c = web_app.app.test_client()
//        out = {v: c.get(f'/api/quant/verdict?variety={v}').get_json()
//               for v in ('RU','TA','CF','EG','SC')}
//        open('_verdict_fixture.json','w',encoding='utf-8').write(
//            json.dumps(out, ensure_ascii=False))"
//   2) node tests/js/quant_verdict_render_harness.mjs [_verdict_fixture.json]
//   退出码 0 = 全通过。

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TPL = path.join(ROOT, 'web_template.html');
const FIXTURE = process.argv[2] || process.env.AGENTSENSE_VERDICT_FIXTURE
  || path.join(ROOT, '_verdict_fixture.json');

/** 用花括号配平从 `name(...)  {` 处截出完整方法体(含字符串/模板串跳过)。 */
function extractMethod(src, name) {
  // 【2026-10-14 修】原写死 `\\n\\s{2}`（恰好 2 空格缩进）—— 而模板已被全局压平为
  //   1 空格/层 ⇒ 命中 0，harness 直接抛 `method not found: _mdLite`（**改动前就已坏**）。
  //   与 gtja_module_harness.mjs 同一类问题：**提取器不得把缩进写进锚点**。
  const sig = new RegExp('\\n\\s+' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\([^)]*\\)\\s*\\{');
  const m = sig.exec(src);
  if (!m) throw new Error('method not found: ' + name);
  const start = src.indexOf('{', m.index + m[0].length - 1);
  let depth = 0, i = start, inS = null, esc = false;
  for (; i < src.length; i++) {
    const c = src[i];
    if (inS) {
      if (esc) { esc = false; continue; }
      if (c === '\\') { esc = true; continue; }
      if (c === inS) inS = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') { inS = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  return src.slice(m.index + 1, i);
}

const html = fs.readFileSync(TPL, 'utf8');
const srcParts = [extractMethod(html, '_mdLite'), extractMethod(html, '_renderVerdict')]
  .map(s => '  ' + s.trim().replace(/,\s*$/, ''));

// _escHtml 依赖 DOM(createElement); 测试里用等价纯字符串实现(与产品行为一致)
const escStub = `
  _escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  },
`;
const App = eval('({' + srcParts.join(',\n') + ',' + escStub + '})');

const fixtures = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
let fail = 0, total = 0;
for (const [v, d] of Object.entries(fixtures)) {
  total++;
  const out = App._renderVerdict(d);
  const db = (d.stages?.direction?.geometry || {}).double_break;
  const wantY = (db === true || db === 'True') ? '是' : (db === false || db === 'False') ? '否' : '未判定';
  const geoBlock = out.slice(out.indexOf('几何三件套'), out.indexOf('几何三件套') + 320);
  const descSays = String(d.verdict?.desc || '').includes('双线同破');

  const checks = {
    sec_four: out.includes('四段式'),
    sec_votes: out.includes('各体系方向票'),
    sec_conflict: out.includes('冲突并列'),
    sec_evidence: out.includes('依据（本次实际用到的能力卡）'),
    sec_gaps: out.includes('边界与缺口'),
    no_undefined: !out.includes('undefined'),
    no_objobj: !out.includes('[object Object]'),
    no_py_literal: !/\bNone\b|\bTrue\b|\bFalse\b/.test(out),
    has_dir_word: /做多|做空|暂不操作/.test(out),
    geo_double_break: geoBlock.includes('双线同破') && geoBlock.includes('>' + wantY),
    desc_geo_agree: !descSays || wantY === '是',
  };
  const bad = Object.keys(checks).filter(k => !checks[k]);
  if (bad.length) fail++;
  console.log(`--- ${v} --- ${bad.length ? 'FAIL: ' + bad.join(',') : 'PASS'}`);
  if (bad.length) {
    const flat = out.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    console.log('    TEXT:', flat.slice(0, 500));
  }
}

// ── 命名回归(2026-09-28 用户拍板改名: 五书研判 → 方法论研判 / 各书方向票 → 各体系方向票) ──
// 为什么单列: 改名容易被后续会话"顺手写回"旧名(旧名更短、更早出现), 而渲染断言抓不到。
total++;
const nameChecks = {
  no_old_card_title: !html.includes('<span>五书研判</span>'),
  no_old_section: !html.includes('各书方向票'),
  has_new_card_title: html.includes('<span>方法论研判</span>'),
  has_new_section: html.includes('各体系方向票'),
};
const badName = Object.keys(nameChecks).filter(k => !nameChecks[k]);
if (badName.length) fail++;
console.log(`--- naming --- ${badName.length ? 'FAIL: ' + badName.join(',') : 'PASS'}`);

console.log(fail ? `\nRESULT: FAIL(${fail}/${total})` : `\nRESULT: ALL_PASS(${total})`);
process.exit(fail ? 1 : 0);
process.exit(fail ? 1 : 0);
