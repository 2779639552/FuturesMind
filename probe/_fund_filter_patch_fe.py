# -*- coding: utf-8 -*-
"""前端补丁：⑨ 基本面截面表 —— 筛选 / 排序 / 列显隐（2026-10-16）。

【一次落盘的全部改动】
  H1  控制区整块替换（搜索 + 板块胶囊 + 维度筛选容器 + 排序下拉 + 列/清除按钮 + 面板容器）
  H2  19 个 <th> 加 `data-col`（供列显隐按序定位）、可排列再加 `data-metric` + onclick + caret
  J1  状态块：_fRkFilter / _fRkSort / _fRkCols / _FRK_COLS / _FRK_DIMS / _FRK_TEXT_RANK …
  J2  方法块：筛选、排序、列显隐、联动（约 20 个方法）
  J3  _renderFundRanking 改为 filter→sort→render（统计条仍按**全量**算）
  J4  loadFundRanking 读列可见性偏好
  J5  _SEC_IDX_VER 28 → 29（tab 内卡片有增删必须 +1）

【不幂等】重跑会重复插入 ⇒ 开头检测标记并拒绝。要从 backup 还原后再跑。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
P = ROOT / "probe"

MARK = "_fRkVisibleRows"


def _eol_of(raw: bytes) -> str:
    return "\r\n" if raw.count(b"\r\n") else "\n"


# ── 表头：列序表（**必须与 thead 严格一致**，列显隐靠 nth-child 定位）────────────
# (col_key, th 文本, 对齐, 是否可排)
THS = [
    ("idx",        "#",         "left",   False),
    ("variety",    "品种",       "left",   True),
    ("contract",   "合约",       "center", False),   # 控件列（月份下拉），不参与排序
    ("close",      "最新价",     "right",  True),
    ("basis",      "基差",       "right",  True),
    ("basis_rate", "基差率",     "right",  True),
    ("basis_pct",  "基差分位",   "right",  True),
    ("inv_grade",  "库存综合",   "center", True),
    ("ck_pct",     "仓单分位",   "right",  True),
    ("ck_chg5",    "仓单5日",    "right",  True),
    ("soc_pct",    "社库存分位", "right",  True),
    ("soc_chg4",   "社库存4期",  "right",  True),
    ("vr_ratio",   "虚实盘比",   "right",  True),
    ("prof_grade", "利润",       "center", True),
    ("skill_vd",   "二维四象",   "center", True),
    ("skill_term", "期限结构",   "center", True),
    ("skill_cyc",  "库存周期",   "center", True),
    ("skill_prof", "利润票",     "center", True),
    ("strategy",   "综合结论",   "left",   True),
]


def th_edit(key: str, label: str, align: str, sortable: bool):
    old = '<th style="padding:8px;text-align:%s">%s</th>' % (align, label)
    if sortable:
        new = ('<th data-col="%s" data-metric="%s" onclick="App._fRkClickHead(\'%s\')"'
               ' style="padding:8px;text-align:%s;cursor:pointer">%s<span class="f-rk-caret"></span></th>'
               % (key, key, key, align, label))
    else:
        new = '<th data-col="%s" style="padding:8px;text-align:%s">%s</th>' % (key, align, label)
    return (old, new, 1, "th %s" % key)


# ── 控制区：整块替换 ──────────────────────────────────────────────────────────
CTRL_START = '<div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">'
CTRL_END_ANCHOR = '<span id="f-rk-note" style="font-size:0.75rem;color:var(--text-muted);padding-bottom:8px"></span>'


def ctrl_old_from(txt_lf: str) -> str:
    """从模板里**运行时提取**控制区原文（不硬编码整段，且**限定在 tab-fund 内**）。

    【为什么要提取】第一版把整段抄进补丁，结果**命中 0** —— 内部控制行的前导空格数
    与我抄的不一致（这类差异肉眼极难发现，报错只说"命中 0"）。改用"首尾锚点定位 + 切片"。

    【★★ 为什么必须限定 tab】这个样式串 `<div style="display:flex;gap:16px;…">`
    在模板里出现 **7 次**（quant / quant-tools×3 / fund / foreign / gtja-tools 都用了同款布局）。
    第一版用 `txt.index(CTRL_START)` 取"**第一次**出现" ⇒ 拿到的是 **tab-quant 的控制区**，
    而结束锚点 `f-rk-note` 在 tab-fund ⇒ 替换区间**一路吃掉中间约 17KB**：
    tab-fund 的容器头 / `#f-overview-view` / 统计条 / badge 全没了（并且往 tab-quant 里
    塞了一整套 `f-rk-*` 控件）。
    ★ 抓到它的是**结构校验**（id 计数骤降为 0 + div 深度由 +7 变 +8），不是补丁自己。
    ⇒ 铁律：**在"多 tab 同款模板"里，任何按样式/文本定位都必须先限定 tab 范围**；
      且"提取类锚点"因为绕过了 count==1 检查，**必须额外做长度/边界自检**。
    """
    i_m = txt_lf.index('id="f-rk-metric"')
    i0 = txt_lf.rindex(CTRL_START, 0, i_m)          # ← 从 f-rk-metric **往回**找，落在 tab-fund 内
    i1 = txt_lf.index(CTRL_END_ANCHOR, i_m)
    i1 = txt_lf.index("\n", i1)
    i1 = txt_lf.index("</div>", i1) + len("</div>")
    seg = txt_lf[i0:i1]
    # 【硬约束】长度与边界自检 —— "提取过宽"会静默删内容（本次真踩过）
    assert 600 <= len(seg) <= 2000, "控制区切片长度异常(%d)，锚点可能跨 tab" % len(seg)
    assert 'id="tab-' not in seg, "切片跨过了 tab 边界"
    assert "f-rk-metric" in seg and "f-rk-order" in seg and "f-rk-note" in seg, "控制区提取失败"
    assert "chg_pct" in seg, "提取到的不是旧控制区（旧版含当日涨跌 chg_pct 选项）"
    return seg


# ── JS ───────────────────────────────────────────────────────────────────────
J1_OLD = ''' _fRkData: null,         // 【变量】基本面截面表最近一次响应(切排序维度/返回总览时复用)'''
J1_NEW = ''' _fRkData: null,         // 【变量】基本面截面表最近一次响应(切排序维度/返回总览时复用)
STATE_STATE_BLOCK'''  # STATE_STATE_BLOCK 由文件内容替换

J2_ANCHOR = '''// 【功能】⑨ 加载全品种基本面截面表(2026-10-16 新增; 与技术面「全品种总览」并列)'''

J3_EDITS = [
    # ① 行集来源：原始 → 筛选+排序后
    ('''  const rows = d.rows || [];
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="19" style="padding:18px;color:var(--text-muted)">无数据</td></tr>'; return; }''',
     '''  // 【2026-10-16】筛选与排序都在**渲染期**做(不改 _fRkData / _fRkBase) ⇒
  //   改条件或改排序只是重渲染, 零请求; 也保证"恢复默认口径"用的基线快照仍是原始顺序。
  const all = d.rows || [];
  const rows = this._fRkVisibleRows();''',
     1, "R1 行集改 filter+sort"),
    # ② 空结果提示要区分"无数据"与"被筛掉"
    ('''  }).join('');
  tbody.innerHTML = trs;''',
     '''  }).join('');
  tbody.innerHTML = trs || ('<tr><td colspan="19" style="padding:18px;color:var(--text-muted)">'
   + (all.length ? '没有符合条件的品种（共 ' + all.length + ' 个，当前条件把所有行都筛掉了）' : '无数据')
   + '</td></tr>');''',
     1, "R2 空结果区分 无数据/被筛掉"),
    # ③ 统计条按全量算（技术面同一纪律：不随筛选跳动）
    ('''   const c = f => rows.filter(f).length;
   st.innerHTML = [
    ['品种', rows.length, 'var(--text)'],''',
     '''   // 【按全量算】数字不随筛选跳动 —— 与技术面总览同一纪律。
   const c = f => all.filter(f).length;
   st.innerHTML = [
    ['品种', all.length, 'var(--text)'],''',
     1, "R3 统计条按全量"),
    # ④ 数据截至按全量算
    ('''  if (up) { const ds = rows.map(r => r.date).filter(Boolean).sort(); up.textContent = ds.length ? ('数据截至 ' + ds[ds.length - 1]) : ''; }''',
     '''  if (up) { const ds = all.map(r => r.date).filter(Boolean).sort(); up.textContent = ds.length ? ('数据截至 ' + ds[ds.length - 1]) : ''; }''',
     1, "R4 数据截至按全量"),
    # ⑤ badge 改读前端排序状态 + 末尾刷新周边控件
    ('''  const bd = document.getElementById('f-rk-badge');
  if (bd) bd.textContent = `${rows.length} 品种 · 按 ${
   {'net': '多空票净额', 'basis': '基差', 'basis_rate': '基差率', 'basis_pct': '基差分位',
    'inv_rank': '库存档位(低→高)', 'ck_pct': '仓单分位', 'ck_chg5': '仓单5日变化',
    'soc_pct': '社会库存分位', 'soc_chg4': '社会库存4期变化', 'vr_ratio': '虚实盘比',
    'chg_pct': '当日涨跌'}[d.metric] || d.metric} 排序`;
 },''',
     '''  // badge 用**前端排序状态**（不再读 d.metric —— 排序已归前端，且后端不认识文本列）
  const bd = document.getElementById('f-rk-badge');
  if (bd) {
   const col = this._FRK_COLS.find(c => c.key === this._fRkSort.key);
   const nm = col ? col.label : (this._FRK_SORT_LABEL[this._fRkSort.key] || this._fRkSort.key);
   bd.textContent = rows.length + ' 品种 · 按 ' + nm
    + (this._fRkSort.order === 'desc' ? ' 降序' : ' 升序')
    + (rows.length !== all.length ? '（已筛选）' : '');
  }
  // 【必须在最后】周边控件与摘要 —— 这样"筛到空"时胶囊/下拉/排序指示也照样刷新
  this._fRkBuildPools();
  this._fRkBuildDims();
  this._fRkSyncSortCtl();
  this._fRkApplyCols();
  const fc = document.getElementById('f-rk-fcount');
  if (fc) fc.textContent = this._fRkFilterSummary(rows.length, all.length);
 },''',
     1, "R5 badge 改前端状态 + 末尾刷新周边"),
]

J4_OLD = ''' async loadFundRanking(refresh) {
  const tbody = document.getElementById('f-rk-rows');
  if (!tbody) return;'''
J4_NEW = ''' async loadFundRanking(refresh) {
  const tbody = document.getElementById('f-rk-rows');
  if (!tbody) return;
  this._fRkColsLoad();   // 【2026-10-16】列可见性偏好(存 localStorage)在拉表时读入'''

J5_OLD = "_SEC_IDX_VER: '28',"
J5_NEW = "_SEC_IDX_VER: '29',"


def main(write: bool) -> int:
    raw = TPL.read_bytes()
    eol = _eol_of(raw)
    txt = raw.decode("utf-8")
    if MARK in txt:
        print("★ 改动已应用过 —— 本脚本不幂等, 勿重跑。要从 backup 还原后再跑。")
        return 2

    state = (P / "_ffilter_state.js.txt").read_text(encoding="utf-8").rstrip("\n")
    methods = (P / "_ffilter_methods.js.txt").read_text(encoding="utf-8").rstrip("\n")
    ctrl = (P / "_ffilter_ctrl.html.txt").read_text(encoding="utf-8").rstrip("\n")

    edits = []
    # HTML
    edits.append((ctrl_old_from(txt.replace("\r\n", "\n")), ctrl, 1, "H1 控制区整块替换"))
    for key, label, align, sortable in THS:
        edits.append(th_edit(key, label, align, sortable))
    # JS
    edits.append((J1_OLD, J1_NEW.replace("STATE_STATE_BLOCK", state), 1, "J1 状态块"))
    edits.append((J2_ANCHOR, methods + "\n\n" + J2_ANCHOR, 1, "J2 方法块"))
    for old, new, n, tag in J3_EDITS:
        edits.append((old, new, n, tag))
    edits.append((J4_OLD, J4_NEW, 1, "J4 读列可见性偏好"))
    edits.append((J5_OLD, J5_NEW, 1, "J5 _SEC_IDX_VER +1"))

    ok = True
    new = txt
    for old, rep, n, tag in edits:
        o = old.replace("\n", eol)
        r = rep.replace("\n", eol)
        got = new.count(o)
        flag = "OK " if got == n else "★  "
        print("  %s %-34s 命中 %d(期望 %d)" % (flag, tag, got, n))
        if got != n:
            ok = False
            continue
        new = new.replace(o, r, 1)
    if not ok:
        print("  ⇒ 有锚点未命中, **未写盘**")
        return 1
    # ══ 写盘前硬自检 ══════════════════════════════════════════════════════════
    # 【为什么必须有】本次 H1 的锚点跨了 tab，替换区间**吞掉约 17KB 内容**
    #   （tab-fund 容器头 / #f-overview-view / 统计条 / badge 全没了），
    #   而补丁自己"全绿"（因为它是"运行时提取"，绕过了 count==1 检查）。
    #   抓到它的是**结构校验**（id 计数骤降 + div 深度变化）⇒ 把校验前移到写盘前。
    def _depth(t: str) -> int:
        d = 0
        for m in re.finditer(r"<div\b|</div>", t):
            d += 1 if m.group(0) == "<div" else -1
        return d

    for key_id in ('id="tab-fund"', 'id="f-overview-view"', 'id="f-detail-view"',
                   'id="f-rk-stats"', 'id="f-rk-badge"', 'id="f-rk-updated"',
                   'id="f-rk-table"', 'id="f-rk-rows"', 'id="tab-quant"'):
        a, b = txt.count(key_id), new.count(key_id)
        if a != b:
            print("  ★ 写盘前自检失败: %s 计数 %d → %d（锚点打偏 ⇒ 拒绝写盘）" % (key_id, a, b))
            return 1
    d0, d1 = _depth(txt), _depth(new)
    if d0 != d1:
        print("  ★ 写盘前自检失败: div 深度 %+d → %+d（配平被打破 ⇒ 拒绝写盘）" % (d0, d1))
        return 1
    dl = len(new) - len(txt)
    # 【区间怎么定】本改动是"新增为主"（状态块 3.5KB + 方法块 9KB + 19 列表头 1.7KB +
    #   控制区换新 1.2KB ≈ +17KB）⇒ 上限放到 25KB。**下限才是最要紧的那条** ——
    #   本次事故的特征是"长度**大幅减少**"（锚点跨 tab 吞掉 17KB），靠它拦住。
    if not (-2000 <= dl <= 25000):
        print("  ★ 写盘前自检失败: 长度变化 %+d 字符, 超出合理范围 ⇒ 拒绝写盘" % dl)
        return 1
    print("  ✔ 写盘前自检: 关键 id 计数不变 / div 深度 %+d 不变 / 长度 %+d 字符" % (d0, dl))
    if not write:
        print("  ⇒ 空跑通过(未写盘)。加 --write 落盘。")
        return 0
    TPL.write_bytes(new.encode("utf-8"))
    b = TPL.read_bytes()
    assert b.count(b"\r\n") > 0 and (b.count(b"\n") - b.count(b"\r\n")) == 0, "模板 EOL 必须保持 CRLF"
    print("  落盘后 md5=%s (CRLF 保持)" % hashlib.md5(b).hexdigest()[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
