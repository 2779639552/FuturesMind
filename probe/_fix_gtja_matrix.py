# -*- coding: utf-8 -*-
"""
修复：国君数据页「覆盖矩阵」不在小模块中 + 吸顶遮挡其他模块。

根因
  · 覆盖矩阵卡片物理上被放在 <div id="tab-gtja-tools" class="page-tools"> **内部**
    （L3327 的注释本来就写着「21 行表格, 不能跟着控制条吸顶」—— 意图对，代码没做到）。
  · `.page-tools` 是 `position:sticky; top:56px` + 实色底 `--bg-card`，本意是给"矮控制条"
    吸顶常驻。塞进一个 21 行大表格后，整块被钉在顶部，实色底**盖住下方模块**。
  · 且 `_secScan` 对 `.page-tools` 是**整块跳过**（见其 L4674 注释：控制条永不参与折叠）
    ⇒ 覆盖矩阵既不在小模块索引里、也不会被折叠 = 用户报的"不在小模块中"。

修法
  把覆盖矩阵卡片**移出** `.page-tools`，落成 tab 顶层的一个普通 `.card`。
  这样它被 `_secScan` 的「③ 其它容器 / 直接是卡片」分支捕获 ⇒ 进索引 + 可折叠 + 不吸顶。
  ⚠️ 顺序放在控制条**之后**：索引条是 insertBefore(tab.firstChild)，若放控制条之前会被顶到更深；
     放之后则紧随控制条，语义也顺（先选品种 → 再看覆盖矩阵）。
"""
import io

P = 'web_template.html'
raw = io.open(P, 'rb').read()
orig = raw

OLD = (
    '  </div>\r\n'
    '  <!-- 覆盖矩阵(2026-09-30 由「① 查询与覆盖矩阵」卡拆出):\r\n'
    '       21 行表格, 不能跟着控制条吸顶。 -->\r\n'
    '  <div class="card" style="margin-bottom:16px">\r\n'
    '    <h3>覆盖矩阵</h3>\r\n'
    '    <div id="gt-matrix" style="overflow-x:auto;margin-top:10px">\r\n'
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\r\n'
    '    </div>\r\n'
    '  </div>\r\n'
    '\r\n'
    '  </div>\r\n'
)

NEW = (
    '  </div>\r\n'
    '\r\n'
    '  <!-- 覆盖矩阵(2026-09-30)\r\n'
    '       【为什么放在 .page-tools **外面**】\r\n'
    '       1) 吸顶遮挡：.page-tools 是 position:sticky(top:56px) + 实色底 --bg-card,\r\n'
    '          只适合装"矮控制条"。把 21 行大表格塞进去, 整块被钉在顶部**盖住下方模块**\r\n'
    '          (2026-09-30 用户报障: "遮挡到了其他小模块的内容")。\r\n'
    '       2) 不在小模块中：_secScan 对 .page-tools 是**整块跳过**(控制条永不参与折叠),\r\n'
    '          故它既进不了小模块索引、也收不起来(用户报障: "不在小模块中")。\r\n'
    '       移到这里 = tab 顶层普通 .card ⇒ 被 _secScan 的"直接是卡片"分支捕获,\r\n'
    '       自动进索引 + 可折叠 + 不吸顶。\r\n'
    '       ⚠️ 勿再挪回 .page-tools; 也勿在 .page-tools 里新增第二张 h3 卡片。 -->\r\n'
    '  <div class="card" style="margin-bottom:16px">\r\n'
    '    <h3>覆盖矩阵</h3>\r\n'
    '    <div id="gt-matrix" style="overflow-x:auto;margin-top:10px">\r\n'
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\r\n'
    '    </div>\r\n'
    '  </div>\r\n'
    '\r\n'
    '  </div>\r\n'
)

n = raw.count(OLD.encode('utf-8'))
assert n == 1, '[FAIL] anchor hits=%d (expected 1)' % n
raw = raw.replace(OLD.encode('utf-8'), NEW.encode('utf-8'))

assert raw != orig
bare = raw.count(b'\n') - raw.count(b'\r\n')
assert bare == 0, 'bareLF=%d' % bare
io.open(P, 'wb').write(raw)
print('OK 覆盖矩阵已移出 .page-tools')
print('CRLF', raw.count(b'\r\n'), 'bareLF 0')
