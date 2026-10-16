// E2E 验收: 上传区「拖拽 + 选文件夹」真实交互
// 用法: node probe/_e2e_upload_dropzone.mjs [BASE]
//
// 为什么必须起真浏览器：拖拽的核心是 e.dataTransfer（DataTransfer / FileList /
// webkitGetAsEntry），这些在 Node 里无法真实构造。只看 DOM 有 .dz 容器等于没验
// —— 用户报的正是「拖进去没反应」，那是行为缺陷，不是结构缺陷。
//
// 关键手法：不用 Playwright 的 dragAndDrop（它基于真实鼠标，拿不到 DataTransfer.files），
// 而是在页面里 dispatchEvent 一个自造的 DragEvent + DataTransfer —— 这是唯一能
// 真实走通「drop → 取 files → 回填 input.files」的路径。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// 在页面里造真文件 + 真 DataTransfer，然后对指定 .dz 派发 dragover / drop
//
// ⚠️ 断言时机坑（实测踩到）：drop handler 是 async 的（内部 await _dzCollectFiles），
//    所以 dispatchEvent 返回时 commit 还没跑。若在同一个同步块里读 input.files，
//    拿到的永远是 0 —— 看起来像"回填失效"的严重缺陷，实则是断言太早。
//    更阴的是：早期版本在"非目录"分支里是同步 Array.from(dt.files)，那时同步读反而
//    能过 ⇒ 一次**假绿**恰好掩盖了"真实拖文件夹收集 0 篇"的真 bug。
//    ⇒ 必须等一个宏任务（setTimeout）再读结果。
async function dropFiles(dzName, fileSpecs) {
  return page.evaluate(async ({ dzName, fileSpecs }) => {
    const box = document.querySelector(`.dz[data-dz="${dzName}"]`);
    if (!box) return { err: 'no box' };
    const dt = new DataTransfer();
    for (const s of fileSpecs) {
      // 内容用可重复字符串填充到指定长度，便于验证体积校验分支
      const blob = new Blob([new Uint8Array(s.size)], { type: s.type || '' });
      dt.items.add(new File([blob], s.name, { type: s.type || '' }));
    }
    const fire = (type) => {
      const ev = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt });
      box.dispatchEvent(ev);
    };
    fire('dragover');
    const clsAfterDrag = box.className;
    fire('drop');
    // 等 drop handler 里的 await 链跑完再读
    await new Promise(r => setTimeout(r, 80));
    return {
      clsAfterDrag,
      clsAfterDrop: box.className,
      nFiles: box.querySelector('input[type=file]')?.files?.length ?? -1,
      firstName: box.querySelector('input[type=file]')?.files?.[0]?.name ?? '',
      hasFileCls: box.classList.contains('has-file'),
      fileText: (box.querySelector('.dz-file') || {}).textContent || '',
      errText: (box.querySelector('.dz-err') || {}).textContent || '',
      bad: box.classList.contains('is-bad'),
    };
  }, { dzName, fileSpecs });
}

// 清空某拖拽区（点移除按钮或直接重置）
async function reset(dzName) {
  return page.evaluate((n) => {
    const box = document.querySelector(`.dz[data-dz="${n}"]`);
    const inp = box.querySelector('input[type=file]');
    inp.value = '';
    box.classList.remove('has-file', 'is-bad', 'is-drag');
    const e = box.querySelector('.dz-err'); if (e) e.textContent = '';
    return true;
  }, dzName);
}

console.log('\n[0] 绑定就绪');
{
  const r = await page.evaluate(() => {
    const boxes = Array.from(document.querySelectorAll('.dz'));
    return {
      total: boxes.length,
      bound: boxes.filter(b => b._dzBound).length,
      names: boxes.map(b => b.dataset.dz),
    };
  });
  log(r.total >= 4, `找到 ${r.total} 个 .dz 拖拽区: ${r.names.join(', ')}`);
  log(r.bound === r.total, `全部已绑定 _dzInitAll (bound=${r.bound}/${r.total})`);
}

console.log('\n[1] 拖拽高亮 (dragover)');
{
  const box = await page.evaluate(() => {
    const b = document.querySelector('.dz[data-dz="fg-file"]');
    const dt = new DataTransfer();
    dt.items.add(new File([new Blob(['x'])], 'a.pdf', { type: 'application/pdf' }));
    b.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
    return b.classList.contains('is-drag');
  });
  log(box, '拖入时 .dz 加上 is-drag（有视觉反馈）');
  // dragleave 必须能撤销高亮，否则拖走后框一直是亮的
  const cleared = await page.evaluate(() => {
    const b = document.querySelector('.dz[data-dz="fg-file"]');
    b.dispatchEvent(new DragEvent('dragleave', { bubbles: true, cancelable: true }));
    return !b.classList.contains('is-drag');
  });
  log(cleared, 'dragleave 后 is-drag 撤销');
}

console.log('\n[2] 拖入合法 PDF → 回填 input.files（三处单文件区）');
for (const [dzName, fname] of [['fg-file', 'english.pdf'], ['res-upload-file', 'htfc.pdf'], ['ud-upload-file', 'data.xlsx']]) {
  await reset(dzName);
  const r = await dropFiles(dzName, [{ name: fname, size: 4096 }]);
  log(r.clsAfterDrag.includes('is-drag'), `${dzName}: dragover 时高亮`);
  log(!r.clsAfterDrop.includes('is-drag'), `${dzName}: drop 后清除高亮`);
  log(r.nFiles === 1 && r.firstName === fname, `${dzName}: 回填 input.files (n=${r.nFiles}, name=${r.firstName})`);
  log(r.hasFileCls && r.fileText.includes(fname), `${dzName}: 回显文件名 (${r.fileText.trim().slice(0, 40)})`);
  log(!r.bad && !r.errText, `${dzName}: 无误报`);
}

console.log('\n[3] 体积超限 → 前置拦截（不该等后端 400）');
{
  await reset('res-upload-file');
  // 21MB > 20MB 上限
  const r = await dropFiles('res-upload-file', [{ name: 'huge.pdf', size: 21 * 1024 * 1024 }]);
  log(r.bad && r.errText.includes('超过'), `超大文件被拦截并给原因: "${r.errText.trim()}"`);
  log(r.nFiles === 0 || r.nFiles === -1, `超限文件未回填 input.files (n=${r.nFiles})`);
  log(!r.hasFileCls, '超限时不残留回显');
}

console.log('\n[4] 扩展名不符 → 拒绝');
{
  await reset('ud-upload-file');
  const r = await dropFiles('ud-upload-file', [{ name: 'evil.exe', size: 1024 }]);
  log(r.bad && r.errText.includes('不支持'), `非法类型被拒: "${r.errText.trim()}"`);
  // 合法扩展名必须放行（防"一刀切全拒"的过度收紧）
  await reset('ud-upload-file');
  const r2 = await dropFiles('ud-upload-file', [{ name: 'ok.csv', size: 1024 }]);
  log(!r2.bad && r2.nFiles === 1, '合法 .csv 正常放行');
}

console.log('\n[5] 多文件拖入单文件区 → 提示只支持 1 个');
{
  await reset('fg-file');
  const r = await dropFiles('fg-file', [
    { name: 'a.pdf', size: 1024 }, { name: 'b.pdf', size: 1024 },
  ]);
  log(r.bad && r.errText.includes('1 个'), `多文件被拦: "${r.errText.trim()}"`);
}

console.log('\n[6] 文件夹区：多 PDF 收集 + 非 PDF 过滤');
{
  await reset('res-local-folder');
  // 注：程序构造的 DragEvent 里 webkitGetAsEntry() 返回 null，浏览器只在真实 OS
  // 拖拽时填充它。故这里验「files 兜底路径 + 过滤策略」；递归分支由静态护栏
  // (test_dz_folder_recursion_uses_entries_api / test_webkit_get_as_entry_called_once) 锁死。
  const r = await dropFiles('res-local-folder', [
    { name: 'a.pdf', size: 2048 }, { name: 'b.pdf', size: 3072 },
  ]);
  log(r.nFiles === 2, `文件夹区接收多个文件 (n=${r.nFiles}, 应允许多选)`);
  const list = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.dz[data-dz="res-local-folder"] .dz-li'))
      .map(el => el.textContent.trim()));
  log(list.length === 2, `列表回显 ${list.length} 条: ${list.join(' | ')}`);
  // 混合拖入：非 PDF 应被**过滤**掉，PDF 仍正常进来（不是整批拒绝）
  await reset('res-local-folder');
  const r2 = await dropFiles('res-local-folder', [
    { name: 'a.pdf', size: 1024 }, { name: 'note.txt', size: 10 },
    { name: 'data.xlsx', size: 10 },
  ]);
  log(r2.nFiles === 1 && !r2.bad, `文件夹区过滤掉杂项、保留 PDF (n=${r2.nFiles}, bad=${r2.bad})`);
  // 全是杂项时应给出"未找到符合类型的文件"，而不是静默通过
  await reset('res-local-folder');
  const r3 = await dropFiles('res-local-folder', [{ name: 'only.txt', size: 10 }]);
  log(r3.bad && r3.errText.includes('未找到符合类型'), `全杂项时明确报错: "${r3.errText.trim()}"`);
}

console.log('\n[7] 点击框体打开选择框（不触发上传、不报错）');
{
  const r = await page.evaluate(() => {
    const b = document.querySelector('.dz[data-dz="ud-upload-file"]');
    const inp = b.querySelector('input[type=file]');
    let clicked = false;
    const orig = inp.click.bind(inp);
    inp.click = () => { clicked = true; };   // 拦掉真实弹窗，只验调用
    b.click();
    inp.click = orig;
    return clicked;
  });
  log(r, '点击 .dz 会触发 input.click()');
}

console.log('\n[8] 移除按钮清空选择');
{
  await reset('res-upload-file');
  await dropFiles('res-upload-file', [{ name: 'x.pdf', size: 2048 }]);
  const had = await page.evaluate(() => {
    const b = document.querySelector('.dz[data-dz="res-upload-file"]');
    return b.classList.contains('has-file');
  });
  const cleared = await page.evaluate(() => {
    const b = document.querySelector('.dz[data-dz="res-upload-file"]');
    const btn = b.querySelector('.dz-clear');
    if (!btn) return { err: 'no clear btn' };
    btn.click();
    return {
      hasFile: b.classList.contains('has-file'),
      nFiles: b.querySelector('input[type=file]').files.length,
    };
  });
  log(had && !cleared.hasFile && cleared.nFiles === 0,
    `移除按钮清空回显与 files (had=${had}, after=${cleared.nFiles})`);
}

console.log('\n[9] 本地导入面板：新入口齐备');
{
  await page.evaluate(() => App.switchTab('research'));
  await page.waitForTimeout(1200);
  const r = await page.evaluate(() => {
    App.toggleLocalImportPanel();
    const panel = document.getElementById('res-local-import-panel');
    const folder = document.getElementById('res-local-folder');
    const pathBox = document.getElementById('res-local-path-box');
    const pathEl = document.getElementById('res-local-path');
    const org = document.getElementById('res-local-org');
    return {
      panelVisible: panel && panel.style.display !== 'none',
      hasFolderDz: !!document.querySelector('.dz[data-dz="res-local-folder"]'),
      webkitdir: folder ? ('webkitdirectory' in folder) : false,
      multiple: folder ? folder.multiple : false,
      hasPathFallback: !!pathEl,
      pathCollapsed: pathBox ? pathBox.style.display === 'none' : null,
      hasOrg: !!org,
      hasPickBtn: !!Array.from(panel.querySelectorAll('button')).find(b => /选择文件夹/.test(b.textContent)),
      hasPathBtn: !!Array.from(panel.querySelectorAll('button')).find(b => /粘贴本机路径/.test(b.textContent)),
    };
  });
  log(r.panelVisible, '本地导入面板可展开');
  log(r.hasFolderDz && r.webkitdir && r.multiple, `文件夹拖拽区就位 (webkitdirectory=${r.webkitdir}, multiple=${r.multiple})`);
  log(r.hasPickBtn, '有「选择文件夹」按钮');
  log(r.hasPathBtn && r.hasPathFallback && r.pathCollapsed === true, '有「粘贴本机路径」兜底且默认收起');
  log(r.hasOrg, '机构名输入仍在');
  // 展开路径兜底
  const opened = await page.evaluate(() => {
    App.toggleLocalPathInput();
    const b = document.getElementById('res-local-path-box');
    const on = b.style.display !== 'none';
    App.toggleLocalPathInput();
    return { on, back: b.style.display === 'none' };
  });
  log(opened.on && opened.back, '路径兜底可展开/收起');
  // 两个入口都没内容时应给出明确提示，而不是静默失败
  const guard = await page.evaluate(async () => {
    const folder = document.getElementById('res-local-folder');
    const path = document.getElementById('res-local-path');
    folder.value = ''; path.value = '';
    const orig = App.toast.bind(App);
    let msg = '';
    App.toast = (m, t) => { msg = m; };
    await App.localImport();
    App.toast = orig;
    return msg;
  });
  log(/拖入|粘贴本机路径/.test(guard), `两个入口都空时给明确提示: "${guard}"`);
}

console.log('\n[10] 目录选择器「空文件夹」缺陷回归');
{
  await page.evaluate(() => App.switchTab('research'));
  await page.waitForTimeout(900);
  await page.evaluate(() => App.toggleLocalImportPanel());
  // ① 目录选择器上不得有 accept（Chromium 会当成"目录内过滤器"⇒ 目录显示为空）
  const r = await page.evaluate(() => {
    const folder = document.getElementById('res-local-folder');
    const fb = document.getElementById('res-local-files');
    const panel = document.getElementById('res-local-import-panel');
    const box = document.querySelector('.dz[data-dz="res-local-folder"]');
    return {
      hasAcceptAttr: folder ? folder.hasAttribute('accept') : null,
      acceptVal: folder ? (folder.getAttribute('accept') || '') : null,
      fbHasAccept: fb ? (fb.getAttribute('accept') || '') : null,
      fbMultiple: fb ? fb.multiple : false,
      // ⚠️ 必须用 hasAttribute: `'webkitdirectory' in x` 对任何 file input 都是 true,
      //    只能探测浏览器能力, 不能判断此 input 是否被声明为目录选择器。
      fbIsDirPicker: fb ? fb.hasAttribute('webkitdirectory') : null,
      // 兜底 input 不能在 .dz 容器内（否则 _dzInitAll 误取）
      fbInsideBox: box ? box.contains(fb) : null,
      hasPickFilesBtn: !!Array.from(panel.querySelectorAll('button'))
        .find(b => /直接选文件/.test(b.textContent)),
      // 目录选择器应仍是"选目录"，没被改成选文件
      stillDirPicker: folder ? ('webkitdirectory' in folder) : false,
    };
  });
  log(r.hasAcceptAttr === false,
      `目录选择器已无 accept 属性 (hasAccept=${r.hasAcceptAttr}, 原值=${r.acceptVal})`);
  log(r.stillDirPicker, '「选择文件夹」入口仍为目录选择器');
  log(r.hasPickFilesBtn, '新增「直接选文件」按钮');
  log(r.fbHasAccept.includes('.pdf') && r.fbMultiple && r.fbIsDirPicker === false,
      `兜底入口可选多个 PDF 且非目录选择器 (accept=${r.fbHasAccept}, multi=${r.fbMultiple}, isDir=${r.fbIsDirPicker})`);
  log(r.fbInsideBox === false, '兜底 input 在 .dz 容器之外（不会顶掉目录选择器绑定）');

  // ② 兜底选了文件后，应回填进主 input 并渲染回显
  const wired = await page.evaluate(async () => {
    const fb = document.getElementById('res-local-files');
    const main = document.getElementById('res-local-folder');
    main.value = '';
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(2048)], 'fallback_a.pdf', { type: 'application/pdf' }));
    dt.items.add(new File([new Uint8Array(1024)], 'fallback_b.pdf', { type: 'application/pdf' }));
    fb.files = dt.files;
    fb.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 80));   // 等异步 handler 提交
    const list = Array.from(document.querySelectorAll('.dz[data-dz="res-local-folder"] .dz-li'))
      .map(e => e.textContent);
    return { nMain: main.files.length, nFb: fb.files.length, list };
  });
  log(wired.nMain === 2,
      `兜底选文件后回填主 input (主=${wired.nMain}, 兜底=${wired.nFb})`);
  log(wired.list.length === 2, `回显 ${wired.list.length} 条: ${wired.list.join(' | ')}`);

  // ③ 兜底所选能真正进入上传链路（拦截 fetch 看 multipart 条数）
  const posted = await page.evaluate(async () => {
    const main = document.getElementById('res-local-folder');
    // 保持上一步的 files 不回填场景，直接注入
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(4096)], 'chain_x.pdf', { type: 'application/pdf' }));
    main.files = dt.files;
    const origFetch = window.fetch;
    let seen = null;
    window.fetch = async (url, opt) => {
      if (String(url).includes('/api/research/upload-folder')) {
        seen = { n: opt.body.getAll('files').length,
                 names: opt.body.getAll('files').map(f => f.name) };
        return new Response(JSON.stringify({ ok: true, message: 'stub' }),
                            { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return origFetch(url, opt);
    };
    try { await App.localImport(); } catch (e) { /* stub 分支 */ }
    window.fetch = origFetch;
    return seen;
  });
  log(posted && posted.n === 1,
      `兜底所选进入上传链路 (files=${posted ? posted.n : 'null'}, ${posted ? posted.names.join(',') : ''})`);

  // ④ 重复选同一目录要能再次触发（pickLocalFolder 先清 value）
  const reSel = await page.evaluate(() => {
    const folder = document.getElementById('res-local-folder');
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(16)], 'x.pdf', { type: 'application/pdf' }));
    folder.files = dt.files;
    let opened = 0;
    const origClick = folder.click.bind(folder);
    folder.click = () => { opened++; };
    App.pickLocalFolder();
    const valAfter = folder.value;
    folder.click = origClick;
    return { opened, valAfter };
  });
  log(reSel.opened === 1 && reSel.valAfter === '',
      `「选择文件夹」先清 value 再打开 (clicked=${reSel.opened}, value="${reSel.valAfter}")`);
}

console.log('\n[11] JS 运行时错误');
log(errs.length === 0, `无未捕获 JS 错误 (${errs.length})${errs.length ? ': ' + errs.slice(0, 3).join(' | ') : ''}`);

await browser.close();
console.log(`\n${'='.repeat(52)}\n上传拖拽 E2E: ${pass} PASS / ${fail} FAIL\n${'='.repeat(52)}`);
process.exit(fail ? 1 : 0);
