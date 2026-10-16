"""基本面截面表「切换合约 → 按该月重算该行」—— 一次性补丁（2026-10-16）。

【需求】⑨ 表每行的「合约」列下拉选中某月后，**该行**的基本面读数按该合约月重算。

【用户三项拍板】
  1. 重算结果**直接替换表格该行**（并在「合约」列标明是哪个月）。
  2. 基差的**历史分位**无法按选定月算（平台只有「现货−主力」序列）⇒ **如实标「—」并注明**，
     绝不用主力序列冒充。
  3. 「期限结构」票**原票保留**（整条曲线口径不变），另起一个「自选定月看」的读数。

【哪些随合约重算 / 不随 / 不可算】
  · 重算：基差 / 基差率（现货 − 该月价）· 虚实盘比（该月持仓÷2 ÷ 仓单折手数）· 综合结论
           · 期限结构「自该月看」读数（另起，不覆盖原票）
  · 不随合约变（沿用默认口径，**因为它们本就不依赖合约**）：
           库存双口径综合 / 仓单分位 / 仓单5日 / 社会库存分位 / 社会库存4期 / 利润档位
           / 库存周期票 / 利润票 / 期限结构票
  · ★ **不可按该月算**（如实置空 + 注明）：基差分位 · 二维四象票
           —— 两者都要**该月基差率的历史序列**，平台不提供。

【★ 口径纪律】本接口不新增任何判定口径：虚实盘比仍走 `_vr_ratio_of`、综合结论仍走
  `_strategy_verdict`、四票仍走 `_fund_vote_inputs` → `_fund_skill_votes` → `build_verdict`。

【★ EOL】fund_module.py / web_template.html(tab 区) / tests = 见各自；脚本归一后写回并断言。
【★ 不幂等】开头检测标记即拒绝重跑。

用法: python probe/_fund_month_patch.py [--write]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = (ROOT / "probe" / "_fund_month_helpers.py.txt").read_text(encoding="utf-8")

MARKERS = {"fund_module.py": "_fund_month_row", "web_template.html": "_fRecomputeRow"}

# ═══════════════════ fund_module.py ═══════════════════
# B1 把 4 票的**取数输入**抽成共享助手（判定口径仍在 _fund_skill_votes）
F_INPUTS_ANCHOR = "def _fund_skill_votes(code: str, bars: list,"

F_INPUTS_BLOCK = '''def _fund_vote_inputs(code: str, bars: list | None = None) -> tuple[dict, dict]:
    """装配 4 票所需的**取数输入**（纯取数，不含任何判定口径）。

    【为什么抽出来】「截面表默认口径」(`_fund_xsection_one`) 与「按指定合约月重算」
      (`_fund_month_row`) 都要这六样输入。若各写一遍, 哪天某处换了窗口(例如基差率从
      180 天改成 120 天), 另一处不会跟着变 ⇒ 同一品种在两个入口**四票不一致**。
      判定口径本身仍只在 `_fund_skill_votes` → `quant_verdict.build_verdict`。
    【返回】(inputs, errors)
      inputs = {bars, basis_rates, inv_series, inv_chg, term, profit_rows}
      errors = {term?: str, prof?: str, bars?: str}（取数失败的如实原因, 供上层标注）
    """
    q = _q()
    err: dict[str, str] = {}
    if bars is None:
        try:
            bars = q._load_daily_bars(code, 200)
        except Exception as e:  # noqa: BLE001
            err["bars"] = f"{type(e).__name__}: {e}"[:60]
            bars = []
    basis_rates: list = []
    try:
        br = q._fetch_basis(code, 180, batch=True)
        basis_rates = [r["basis_rate"] for r in (br.get("rows") or [])
                       if r.get("basis_rate") is not None] if br.get("ok") else []
    except Exception as e:  # noqa: BLE001
        err["basis"] = f"{type(e).__name__}: {e}"[:60]
    inv_series: list = []
    try:
        iv = q._fetch_inventory(code, 180, batch=True)
        inv_series = [r["qty"] for r in (iv.get("rows") or [])
                      if r.get("qty") is not None] if iv.get("ok") else []
    except Exception as e:  # noqa: BLE001
        err["inv"] = f"{type(e).__name__}: {e}"[:60]
    inv_chg = None
    pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]
    if len(pos) >= 4 and pos[-4]:
        inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2)
    term: dict = {}
    try:
        t = q._fetch_term_structure(code)
        if t.get("ok"):
            term = t
    except Exception as e:  # noqa: BLE001
        err["term"] = f"{type(e).__name__}: {e}"[:60]
    profit_rows: list = []
    try:
        pf = q._fetch_profit(code)
        if pf.get("ok"):
            profit_rows = pf.get("rows") or []
    except Exception as e:  # noqa: BLE001
        err["prof"] = f"{type(e).__name__}: {e}"[:60]
    return ({"bars": bars, "basis_rates": basis_rates, "inv_series": inv_series,
             "inv_chg": inv_chg, "term": term, "profit_rows": profit_rows}, err)


'''

# B2 `_fund_xsection_one` 里的内联取数块 → 调共享助手
F_INLINE_OLD = '''        # 基差率序列(与二维四象卡同源: 180 天窗口)
        br = q._fetch_basis(code, 180, batch=True)
        basis_rates = [r["basis_rate"] for r in (br.get("rows") or [])
                       if r.get("basis_rate") is not None] if br.get("ok") else []
        iv = q._fetch_inventory(code, 180, batch=True)
        inv_series = [r["qty"] for r in (iv.get("rows") or [])
                      if r.get("qty") is not None] if iv.get("ok") else []
        inv_chg = None
        pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]
        if len(pos) >= 4 and pos[-4]:
            inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2)
        term = {}
        try:
            t = q._fetch_term_structure(code)
            if t.get("ok"):
                term = t
        except Exception as e:  # noqa: BLE001
            err["term"] = f"{type(e).__name__}: {e}"[:60]
        profit_rows = []
        try:
            pf = q._fetch_profit(code)
            if pf.get("ok"):
                profit_rows = pf.get("rows") or []
        except Exception as e:  # noqa: BLE001
            err["prof"] = err.get("prof") or f"{type(e).__name__}: {e}"[:60]
        ev = _fund_skill_votes(code, bars, basis_rates, inv_series, inv_chg, term, profit_rows)'''

F_INLINE_NEW = '''        # 【2026-10-16】六样取数输入抽到 _fund_vote_inputs, 与"按合约月重算"共用同一份
        #   （各写一遍的话窗口口径会各自漂移）。判定口径仍在 _fund_skill_votes。
        ins, ierr = _fund_vote_inputs(code, bars)
        for _k, _v in ierr.items():
            err.setdefault(_k, _v)
        ev = _fund_skill_votes(code, ins["bars"], ins["basis_rates"], ins["inv_series"],
                               ins["inv_chg"], ins["term"], ins["profit_rows"])'''

# B3 末尾追加 ⑨ 表的"按合约月重算"
F_TAIL = '''        "card": "qihuo-duokong-logic.cap-warrant-basis",
    })'''

# ═══════════════════ web_template.html ═══════════════════
# W1 拉取时留基线快照(供"恢复默认口径"零请求还原)
W_BASE_OLD = '''   this._fRkData = d;
   this._renderFundRanking();'''
W_BASE_NEW = '''   this._fRkData = d;
   // 【变量】拉取时的**基线快照** —— 供"把某行恢复成默认(主力)口径"零请求还原。
   //   不用重新拉整张表(那要 ~40s), 也不靠"再算一次默认"（会与刚拉到的数不一致）。
   this._fRkBase = JSON.parse(JSON.stringify(d));
   this._renderFundRanking();'''

# W2 行渲染: 月份口径标记 + 分位说明 + 期限结构「自该月看」
W_ROW_OLD = '''   const warn = r.ck_zero ? ` <span title="${String(r.ck_warn || '').replace(/"/g, '')}" style="color:var(--amber);cursor:help">⚠</span>` : '';
   return `<tr onclick="App.fOpenDetail('${r.variety}')" title="点击进入 ${r.variety} 单品种明细" style="cursor:pointer;border-bottom:1px solid var(--border)">'''
W_ROW_NEW = '''   const warn = r.ck_zero ? ` <span title="${String(r.ck_warn || '').replace(/"/g, '')}" style="color:var(--amber);cursor:help">⚠</span>` : '';
   // 【2026-10-16】该行是否已切到"指定合约月"口径(m.month_mode 由 /api/fund/xsection 下发)
   const mm = r.month_mode || '';
   const anchor = r.term_anchor;
   const anchorCell = anchor
    ? `<br/><span style="font-size:0.7rem;color:var(--amber);cursor:help" title="${String(anchor.note || '').replace(/"/g, '')}">自 ${anchor.from_month} 看: ${String(anchor.shape || '').split('(')[0]}（价差 ${anchor.spread}）</span>`
    : '';
   const pctCell = r.basis_pct_note
    ? `<span style="color:var(--text-muted);cursor:help" title="${String(r.basis_pct_note).replace(/"/g, '')}">—</span>`
    : pr(r.basis_pct);
   return `<tr onclick="App.fOpenDetail('${r.variety}')" title="点击进入 ${r.variety} 单品种明细" style="${mm ? 'box-shadow:inset 3px 0 0 var(--amber);' : ''}cursor:pointer;border-bottom:1px solid var(--border)">'''
W_ROW_NEW_ANCHOR = 'W_ROW_NEW'

W_CELL_OLD = '''${td(r.basis_rate === null || r.basis_rate === undefined ? '—' : (+r.basis_rate * 100).toFixed(2) + '%')}${td(pr(r.basis_pct))}'''
W_CELL_NEW = '''${td(r.basis_rate === null || r.basis_rate === undefined ? '—' : (+r.basis_rate * 100).toFixed(2) + '%')}${td(pctCell)}'''

W_TERM_OLD = '''${td(vote(r.skill_vd, r.skill_vd_gap), 'center')}${td(vote(r.skill_term, r.skill_term_gap), 'center')}'''
W_TERM_NEW = '''${td(vote(r.skill_vd, r.skill_vd_gap), 'center')}${td(vote(r.skill_term, r.skill_term_gap) + anchorCell, 'center')}'''

W_SEL_OLD = '''      <option value="">—</option>${mOpts}${warn}</select>`
```
'''
W_SEL_NEW = None  # 见下方替换（用 simple 版本）

# W3 选月份 → 行内合约明细 + **按该月重算该行**
W_EXP_OLD = ''' async _fExpandContracts(code, month, el) {
  const tr = document.getElementById('f-rk-exp-' + code);
  if (!tr) return;
  const cell = tr.firstElementChild;
  if (!month) { tr.style.display = 'none'; cell.innerHTML = ''; return; }
  tr.style.display = '';
  cell.innerHTML = '<div style="padding:10px 12px;color:var(--text-muted)">加载合约明细…</div>';
  try {'''

W_EXP_NEW = ''' async _fExpandContracts(code, month, el) {
  const tr = document.getElementById('f-rk-exp-' + code);
  if (!tr) return;
  const cell = tr.firstElementChild;
  if (!month) {
   // 【2026-10-16】选「默认(主力)」⇒ 收起明细, 并把该行**恢复成默认口径**(零请求, 用基线快照)
   tr.style.display = 'none'; cell.innerHTML = '';
   this._fRestoreRow(code);
   return;
  }
  tr.style.display = '';
  await this._fRenderContracts(code, month);
  // ★ 用户 2026-10-16 要求: 切换合约时对该品种的基本面截面数据**重新计算**
  await this._fRecomputeRow(code, month);
 },

 // 【功能】把某品种那一行**恢复成默认(主力)口径**
 // 【为什么要基线快照】重算不是"对称可逆"的: 我方只算得出"按某月"的值, 算不回
 //   "平台默认口径"的原值(那是后端按主力算的)。故拉表时留一份快照, 恢复时零请求还原。
 _fRestoreRow(code) {
  if (!this._fRkData || !this._fRkBase) return;
  const i = (this._fRkData.rows || []).findIndex(x => x.variety === code);
  const b = (this._fRkBase.rows || []).find(x => x.variety === code);
  if (i < 0 || !b) return;
  this._fRkData.rows[i] = JSON.parse(JSON.stringify(b));
  this._renderFundRanking();
  const note = document.getElementById('f-rk-note');
  if (note) note.textContent = `${code} 已恢复默认（主力）口径`;
 },

 // 【功能】按指定合约月重算该品种的截面行并**就地替换表格该行**（⑨ 表「合约」列切换时）
 // 【后端】GET /api/fund/xsection?variety=&month=
 // 【口径】只替换**依赖合约**的字段; 库存族/利润/库存周期票/利润票/期限结构票**不动**
 //   （它们本就不依赖合约）。基差分位与二维四象票因缺"该月基差率历史序列"而置空并注明。
 async _fRecomputeRow(code, month) {
  const note = document.getElementById('f-rk-note');
  try {
   const r = await fetch(`/api/fund/xsection?variety=${encodeURIComponent(code)}&month=${encodeURIComponent(month)}`);
   const d = await r.json();
   if (!d.ok) {
    if (note) note.textContent = `${code} 按 ${month} 重算失败：${d.error || '未知原因'}`;
    return;
   }
   const row = ((this._fRkData || {}).rows || []).find(x => x.variety === code);
   if (!row) return;
   Object.assign(row, d.row || {});
   this._renderFundRanking();
   // 重渲染会重建整张表的 DOM ⇒ 刚才展开的合约明细被清掉了, 这里补回来
   await this._fRenderContracts(code, month);
   if (note) {
    note.textContent = `${code} 已切换到 ${month} 合约口径（基差 = 现货 − ${month} 价、`
     + `虚实盘比用 ${month} 持仓）；基差分位与二维四象票在该月无历史序列, 已如实置空；`
     + `点「合约」列选「默认(主力)」可恢复`;
   }
  } catch (e) {
   console.error('fund xsection month error:', e);
   if (note) note.textContent = `${code} 按 ${month} 重算失败：${e.message}`;
  }
 },

 // 【功能】渲染行内「跨月合约明细」表（_fExpandContracts / _fRecomputeRow 共用）
 // 【为什么拆出来】重算后要整表重渲染(DOM 全重建) ⇒ 明细必须能**再渲染一次**
 async _fRenderContracts(code, month) {
  const tr = document.getElementById('f-rk-exp-' + code);
  if (!tr) return;
  const cell = tr.firstElementChild;
  tr.style.display = '';
  cell.innerHTML = '<div style="padding:10px 12px;color:var(--text-muted)">加载合约明细…</div>';
  try {'''

W_TAIL_OLD = '''  } catch (e) {
   console.error('fund contracts error:', e);
   cell.innerHTML = `<div style="padding:10px 12px;color:var(--amber)">合约明细加载失败: ${e.message}</div>`;
  }
 },'''
W_TAIL_NEW = '''  } catch (e) {
   console.error('fund contracts error:', e);
   cell.innerHTML = `<div style="padding:10px 12px;color:var(--amber)">合约明细加载失败: ${e.message}</div>`;
  }
 },'''

# W4 「—」选项改名（现在它是"恢复默认口径"的入口）
W_OPT_OLD = '''      <option value="">—</option>${mOpts}${warn}</select>'''
W_OPT_NEW = '''      <option value="">默认(主力)</option>${mOpts}${warn}</select>'''

# W5 表头上方说明补一句"切换合约会重算该行"
W_HINT_OLD = '''  点任意一行进入该品种的<b>单品种明细</b>。'''
W_HINT_NEW = '''  点任意一行进入该品种的<b>单品种明细</b>；把「合约」列切到某个月 ⇒ <b>该行</b>的基差/基差率/虚实盘比
 与综合结论**按该月重算**（库存族与利润不随合约变化，故不动）。'''


def patch(name: str, ops: list, write: bool) -> bool:
    p = ROOT / name
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    bare = raw.count(b"\n") - crlf
    assert crlf == 0 or bare == 0, "%s EOL 混合" % name
    eol = "\r\n" if crlf else "\n"
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    ok = True
    print("  ── %s (%s) ──" % (name, "CRLF" if crlf else "LF"))
    for old, new, want, why in ops:
        got = txt.count(old)
        print("    %s %-46s 命中 %d (期望 %d)" % ("OK " if got == want else "★  ", why, got, want))
        if got != want:
            ok = False
            continue
        txt = txt.replace(old, new, want)
    if not ok:
        print("    ⇒ 有锚点未命中, 本文件未写入")
        return False
    if write:
        p.write_bytes(txt.replace("\n", eol).encode("utf-8"))
        chk = p.read_bytes()
        c2, b2 = chk.count(b"\r\n"), chk.count(b"\n") - chk.count(b"\r\n")
        assert (c2 > 0 and b2 == 0) if crlf else (c2 == 0), "写回后 EOL 破坏"
        print("    ✔ 已写入 (%d 字节, EOL 保持)" % len(chk))
    else:
        print("    ✔ 空跑通过")
    return True


def main() -> int:
    write = "--write" in sys.argv
    for name, mark in MARKERS.items():
        if mark in (ROOT / name).read_bytes().decode("utf-8", "ignore"):
            print("★ %s 已含标记 %r —— 本脚本不幂等, 请勿重跑。" % (name, mark))
            return 2
    print("=== %s ===" % ("写入模式" if write else "空跑模式(加 --write 落盘)"))
    ok = True
    ok &= patch("fund_module.py", [
        (F_INPUTS_ANCHOR, F_INPUTS_BLOCK + F_INPUTS_ANCHOR, 1, "F1 抽出 _fund_vote_inputs 共享助手"),
        (F_INLINE_OLD, F_INLINE_NEW, 1, "F2 截面行改用共享助手"),
        (F_TAIL, F_TAIL + "\n\n\n" + HELPERS.rstrip("\n"), 1, "F3 追加 _fund_month_row + 路由"),
    ], write)
    ok &= patch("web_template.html", [
        (W_BASE_OLD, W_BASE_NEW, 1, "W1 拉表时留基线快照"),
        (W_HINT_OLD, W_HINT_NEW, 1, "W2 表头说明补一句"),
        (W_ROW_OLD, W_ROW_NEW, 1, "W3 行渲染: 月份标记/分位说明/期限锚"),
        (W_CELL_OLD, W_CELL_NEW, 1, "W4 基差分位单元格用 pctCell"),
        (W_TERM_OLD, W_TERM_NEW, 1, "W5 期限结构单元格附锚定读数"),
        (W_OPT_OLD, W_OPT_NEW, 1, "W6 「—」改名为「默认(主力)」"),
        (W_EXP_OLD, W_EXP_NEW, 1, "W7 拓宽合约入口: 明细 + 重算 + 恢复 + 共用渲染"),
    ], write)
    print()
    print("=== %s ===" % ("全部通过" if ok else "★ 有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
