"""库存双口径(社会库存 + 仓单) 一次性补丁 —— 2026-10-16。

【为什么写成一个脚本】改动跨 3 个文件、共 12 处, 且三个文件 **EOL 不同**
  (quant_module.py / fund_module.py = LF, web_template.html = CRLF)。
  ⇒ 统一策略: 读字节 → 判 EOL → **归一成 LF 后匹配** → 写回时还原原 EOL。
  这样彻底绕开"多行锚点在 CRLF 文件里永远命中 0"这个静默坑。

【★ 本脚本**不幂等**, 拒绝重跑】改动里有"在锚点前插行"这类操作, 重跑会重复插入。
  开头检测标记, 命中即明确拒绝并提示从 backup 还原 —— 不指望下游断言碰巧拦住。

用法:
    python probe/_inv_dual_patch.py            # 空跑: 只报每处命中数, 不写盘
    python probe/_inv_dual_patch.py --write    # 真正落盘
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPERS = (ROOT / "probe" / "_inv_dual_helpers.py.txt").read_text(encoding="utf-8")

# 重跑护栏: 这些标记一旦出现就说明改动已应用过
MARKERS = {
    "quant_module.py": "_inv_dual",
    "fund_module.py": "_inv_dual",
    "web_template.html": "dualLine",
}

# ===========================================================================
# quant_module.py
# ===========================================================================
Q_INSERT_BEFORE_CYCLE = "def _inventory_cycle(code: str, inv_rows: list[dict], price_bars: list[dict]) -> dict:"

Q_CYCLE_RETURN_OLD = '''    return {"ok": True,
            # chg 可为 None(前值为 0, 百分比无定义) → 前端必须按"无定义"展示而非当 0
            "inv_chg5_pct": (round(chg, 2) if chg is not None else None),
            "inv_dir": inv_dir,
            "inv_latest": now, "price_dir": price_dir, "phase": phase[0], "note": phase[1],
            "inv_pct_rank": round(sum(1 for v in qty if v <= now) / len(qty) * 100, 1)}'''

Q_CYCLE_RETURN_NEW = '''    sc = _inv_social_block(code)
    return {"ok": True,
            # chg 可为 None(前值为 0, 百分比无定义) → 前端必须按"无定义"展示而非当 0
            "inv_chg5_pct": (round(chg, 2) if chg is not None else None),
            "inv_dir": inv_dir,
            "inv_latest": now, "price_dir": price_dir, "phase": phase[0], "note": phase[1],
            "inv_pct_rank": round(sum(1 for v in qty if v <= now) / len(qty) * 100, 1),
            # ── 2026-10-16 双口径: 社会库存方向 + 与仓单方向的交叉验证 ──
            # 【为什么只做交叉验证、不动四象限】两者**频率不同**(仓单日频 / 社会库存
            #   周频), 合并成一个方向会掩盖"库存向交割库转移"这一关键背离信号。
            #   四象限仍以"仓单方向 × 价格方向"定相位 —— 该口径未变。
            "social": sc, "cross": _inv_cross(inv_dir, sc)}'''

Q_ERR1_OLD = '''        return {"ok": False, "error": "仓单或价格序列不足"}'''
Q_ERR1_NEW = '''        # 仓单不足时仍把社会库存抛出去: 该品种的社会库存可能是唯一可用口径
        return {"ok": False, "error": "仓单或价格序列不足",
                "social": _inv_social_block(code)}'''

Q_ERR2_OLD = '''        return {"ok": False, "error": "该品种仓单近期已全部注销(序列为 0), 无法判定库存周期"}'''
Q_ERR2_NEW = '''        # 同上: 仓单全 0 时社会库存往往是唯一能说明"库存到底低不低"的口径
        return {"ok": False, "error": "该品种仓单近期已全部注销(序列为 0), 无法判定库存周期",
                "social": _inv_social_block(code)}'''

Q_API_OLD = '''    return jsonify(_json_safe({"ok": True, "variety": code, "name": meta.get("name", code),
                               "unit": meta.get("unit", ""), "days": days,
                               "inventory": summary, "cycle": cycle}))'''

Q_API_NEW = '''    # ── 2026-10-16 双口径(社会库存 + 交易所仓单) ──
    # 【★ 原字段 inventory / cycle **一个不改**】既有护栏与前端按原字段断言;
    #   双口径信息走**增量字段** social / composite, 老消费端零影响。
    _ck_rows = (inv.get("rows") or []) if inv.get("ok") else []
    _dual = _inv_dual(code, _ck_rows, days, inv.get("source", "") if inv.get("ok") else "")
    return jsonify(_json_safe({"ok": True, "variety": code, "name": meta.get("name", code),
                               "unit": meta.get("unit", ""), "days": days,
                               "inventory": summary, "cycle": cycle,
                               "social": _dual.get("social") or {},
                               "composite": {_k: _dual.get(_k) for _k in
                                             ("grade", "mode", "note", "warn", "available")}}))'''

# ===========================================================================
# fund_module.py
# ===========================================================================
F_OLD = '''    # ── 库存维度(仓单分位; 与 quant 用同一 _fetch_inventory 保证口径一致) ──
    inv_out: dict[str, Any] = {"ok": False}
    try:
        inv = q._fetch_inventory(code, 180)
        if inv.get("ok") and inv.get("rows"):
            qty = [r["qty"] for r in inv["rows"] if r.get("qty") is not None]
            if len(qty) >= 10:
                cur = qty[-1]
                rank = _pct_rank(qty, cur)
                inv_out = {"ok": True, "latest": cur, "pct_rank": rank, "grade": _grade(rank),
                           "q30": sorted(qty)[int(len(qty) * 0.3)],
                           "q70": sorted(qty)[int(len(qty) * 0.7)],
                           "count": len(qty), "source": inv.get("source", "")}
            else:
                inv_out = {"ok": False, "error": "仓单序列不足 10 个点, 分位不可靠"}
        else:
            inv_out = {"ok": False, "error": inv.get("error") or "无仓单数据"}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 库存取数失败(%s): %s", code, e)
        inv_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}'''

F_NEW = '''    # ── 库存维度(★ 2026-10-16 双口径: 国君社会库存 + 交易所仓单 综合) ──
    # 【为什么不再只用仓单】仓单是**交割库口径**(可交割量), 社会库存是**产业链口径**;
    #   二者背离 = 库存向交割库转移。只用仓单会把"无注册仓单"错读成"库存极低"
    #   (实测 CJ 仓单归零 → 分位 0.042 → 判"低" ⇒ 三指标系统输出凭空偏多)。
    # 【★ 判定统一走 quant._inv_dual】两口径量纲不可比(实测 EG 社会库存 13 vs 仓单
    #   6226), 故**不在本文件另起一套比较逻辑** —— 只消费后端给的 grade/mode。
    inv_out: dict[str, Any] = {"ok": False}
    try:
        inv = q._fetch_inventory(code, 180)
        ck_rows = (inv.get("rows") or []) if inv.get("ok") else []
        dual = q._inv_dual(code, ck_rows, 180,
                           inv.get("source", "") if inv.get("ok") else "")
        ck = dual.get("ck") or {}
        soc = dual.get("social") or {}
        if dual.get("grade") != "不明":
            inv_out = {"ok": True,
                       "grade": dual["grade"], "mode": dual.get("mode", ""),
                       "dual_note": dual.get("note", ""), "warn": dual.get("warn"),
                       "available": dual.get("available", []),
                       # 下面几个键是**仓单口径**原值(口径与 ④ 卡一致), 保留是因为
                       # 既有消费端按它们取值; 判定请只看 grade/mode。
                       "latest": ck.get("latest"), "pct_rank": ck.get("pct_rank"),
                       "count": ck.get("count"), "source": ck.get("source", ""),
                       "ck": ck, "social": soc}
        else:
            inv_out = {"ok": False,
                       "error": ((ck.get("error") if not ck.get("ok") else "")
                                 or (soc.get("error") if not soc.get("ok") else "")
                                 or "库存双口径均无数据"),
                       "ck": ck, "social": soc, "warn": dual.get("warn")}
    except Exception as e:  # noqa: BLE001
        logger.warning("fund 库存取数失败(%s): %s", code, e)
        inv_out = {"ok": False, "error": f"{type(e).__name__}: {e}"}'''

# _strategy_verdict 的入参档位语义说明(行为不变, 只补"矛盾"这一档)
F_VD_OLD = '''    【输入】inv_pos: 低/中/高/不明(仓单分位三档)'''
F_VD_NEW = '''    【输入】inv_pos: 低/中/高/矛盾/不明
            —— 2026-10-16 起由**双口径综合**给出(quant._inv_dual):
               低/中/高 = 社会库存与仓单同档或单口径;
               **矛盾 = 两口径背离(一低一高, 库存转移迹象)** ⇒ 下面 low_i/high_i
               两票都不成立, 自然**不会进单边判定**(这是刻意的, 勿加特例绕过)。'''

# ===========================================================================
# web_template.html
# ===========================================================================
W_DESC_OLD = ''' 库存(仓单分位) + 利润(加工/进口利润分位) + 基差(期货升贴水) → 单边或跨期。'''
W_DESC_NEW = ''' 库存(<b>社会库存 + 交易所仓单</b> 双口径综合分位) + 利润(加工/进口利润分位) + 基差(期货升贴水) → 单边或跨期。'''

W_SUB_OLD = ''' <b>库存维度明细</b> —— 交易所注册仓单序列 · 库存周期四象限 · 与价格的统计关联。'''
W_SUB_NEW = ''' <b>库存维度明细</b> —— 交易所注册仓单序列 · 库存周期四象限 · <b>社会库存双口径交叉验证</b>。'''

W_LABEL_OLD = '''仓单库存 <span class="badge badge-info" id="f-inv-badge"></span>'''
W_LABEL_NEW = '''仓单库存 · 社会库存双口径 <span class="badge badge-info" id="f-inv-badge"></span>'''

W_BLOCK_OLD = ''' // 库存块
 const invHtml = inv.ok
 ? dim(true, inv.grade, `库存(仓单) 分位 ${inv.pct_rank == null ? '—' : (inv.pct_rank * 100).toFixed(0) + '%'}`,
 `最新 ${Number(inv.latest).toLocaleString()} · ${inv.source || ''} · ${inv.count} 点`)
 : dim(false, '', '库存(仓单)', inv.error || '无仓单数据');'''

W_BLOCK_NEW = ''' // 库存块(★ 2026-10-16 双口径: 社会库存 + 交易所仓单 综合)
 // 【为什么改】库存不能只用交易所注册仓单表示 —— 仓单是**交割库口径**(可交割量),
 //   社会库存是**产业链口径**; 二者背离 = 库存向交割库转移。
 // 【★ 前端不得自行比较数值】两口径量纲不可比(实测 EG 社会库存 13 vs 仓单 6226),
 //   综合档位与口径关系全部由后端 quant_module._inv_dual 判定, 前端只负责呈现。
 const soc = inv.social || {}, ckv = inv.ck || {};
 const dualLine = (label, o) => `<div style="font-size:0.75rem;color:var(--text-muted);line-height:1.7">`
 + `<b style="color:var(--text-secondary)">${label}</b>: `
 + ((o && o.ok)
 ? `分位 <b>${o.pct_rank == null ? '—' : (o.pct_rank * 100).toFixed(0) + '%'}</b>(${o.grade}) · 最新 ${Number(o.latest).toLocaleString()} · ${o.latest_date || ''} · ${o.count} 点`
 : `无数据${o && o.error ? '（' + o.error + '）' : ''}`)
 + `</div>`;
 // 仓单 0 值警示(后端给文本, 含"连续 N 日 / 历史 0 占比 / 上次非零值")
 const ckWarn = ckv.warn
 ? `<div style="font-size:0.75rem;color:var(--amber);line-height:1.7">${ckv.warn}</div>` : '';
 const invSub = dualLine('社会库存', soc) + dualLine('交易所仓单', ckv) + ckWarn;
 // 档位 → 方向词: 低库存→偏多(现货偏紧) / 高库存→偏空(现货宽松) / 矛盾·中→中性(不下注)
 const invWord = inv.grade === '低' ? '偏多' : (inv.grade === '高' ? '偏空' : '中性');
 const invHtml = inv.ok
 ? dim(true, inv.grade,
 `库存 综合 ${inv.grade}(${invWord})${inv.mode ? ' · ' + inv.mode : ''}`, invSub)
 : dim(false, '', '库存', (inv.error || '无数据') + invSub);'''

W_BADGE_OLD = ''' if (badge) {
 const s = d.inventory || {};
 badge.textContent = s.ok
 ? `最新 ${s.latest === null ? '—' : (+s.latest).toLocaleString()} · 来源 ${s.source || '—'}`
 : (s.error || '无数据');
 }'''

W_BADGE_NEW = ''' if (badge) {
 const s = d.inventory || {}, so = d.social || {};
 badge.textContent = s.ok
 ? `仓单最新 ${s.latest === null ? '—' : (+s.latest).toLocaleString()} · 来源 ${s.source || '—'}`
 + (so.ok ? ` ｜ 社会库存 ${Number(so.latest).toLocaleString()} @ ${so.latest_date || ''}` : '')
 : (s.error || '无数据');
 }'''

W_CYCLE_GUARD_OLD = ''' if (!cyc.ok) return this._emptyState(cyc.error || '无法判定库存周期(数据不足)');'''

W_CYCLE_GUARD_NEW = ''' if (!cyc.ok) {
 // 仓单不可算时仍把社会库存抛出来 —— 实测 CJ/EB/BZ 等品种仓单常年为 0 或被集中
 // 注销, 此刻社会库存是唯一能说明"库存到底低不低"的口径, 静默丢掉会误导。
 const _so0 = cyc.social || {};
 const _tail = _so0.ok
 ? ` ｜ 社会库存 ${Number(_so0.latest).toLocaleString()} @ ${_so0.latest_date || ''} · 处历史 ${_so0.pct_rank == null ? '—' : (_so0.pct_rank * 100).toFixed(0) + '%'} 分位（${_so0.grade}）`
 : '';
 return this._emptyState((cyc.error || '无法判定库存周期(数据不足)') + _tail);
 }'''

W_CYCLE_DEFS_OLD = ''' const q = `${cyc.price_dir}·${cyc.inv_dir}`;'''

W_CYCLE_DEFS_NEW = ''' // ── 2026-10-16 双口径: 社会库存方向 + 与仓单方向的交叉验证 ──
 // 【为什么不合并成一个方向】两者频率不同(仓单日频 / 社会库存周频, 取近 4 期≈1 月),
 //   合并会掩盖"库存向交割库转移"这一背离信号 ⇒ 只做一致性判定, 四象限口径未变。
 const so = cyc.social || {}, xc = cyc.cross || {};
 const socLine = so.ok
 ? `<div style="font-size:0.8125rem;margin-top:4px;color:var(--text-muted)">社会库存（${so.label}） 近 4 期 <b>${so.chg_pct === null || so.chg_pct === undefined ? '—' : (so.chg_pct > 0 ? '+' : '') + so.chg_pct + '%'}</b>（${so.dir || '—'}） · 处历史 <b>${so.pct_rank == null ? '—' : (so.pct_rank * 100).toFixed(0) + '%'}</b> 分位（${so.grade}） · 最新 ${Number(so.latest).toLocaleString()} @ ${so.latest_date || ''}</div>`
 : `<div style="font-size:0.8125rem;margin-top:4px;color:var(--text-muted)">社会库存: ${so.error || '无数据'}</div>`;
 const crossLine = xc.mode
 ? `<div style="font-size:0.8125rem;margin-top:4px;color:${/背离/.test(xc.mode) ? 'var(--amber)' : 'var(--text-muted)'}">双口径交叉验证: <b>${xc.mode}</b>${xc.note ? ' —— ' + xc.note : ''}</div>`
 : '';
 const q = `${cyc.price_dir}·${cyc.inv_dir}`;'''

W_CYCLE_BODY_OLD = ''' · 价格 20 日方向 <b>${cyc.price_dir}</b>
 · 仓单处窗口 <b>${pct}%</b> 分位
 </div>'''

W_CYCLE_BODY_NEW = ''' · 价格 20 日方向 <b>${cyc.price_dir}</b>
 · 仓单处窗口 <b>${pct}%</b> 分位
 </div>
 ${socLine}${crossLine}'''


def patch_file(name: str, ops: list, write: bool) -> bool:
    """对一个文件应用 [(old, new, 期望命中数, 说明), ...]; 返回是否全部命中。"""
    p = ROOT / name
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    bare = raw.count(b"\n") - crlf
    assert crlf == 0 or bare == 0, "%s 是混合 EOL, 拒绝改动" % name
    eol = "\r\n" if crlf else "\n"
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    ok = True
    print("  ── %s (%s) ──" % (name, "CRLF" if crlf else "LF"))
    for old, new, want, why in ops:
        got = txt.count(old)
        flag = "OK " if got == want else "★  "
        print("    %s %-46s 命中 %d (期望 %d)" % (flag, why, got, want))
        if got != want:
            ok = False
            continue
        txt = txt.replace(old, new, want)
    if not ok:
        print("    ⇒ 有锚点未命中, 本文件**未写入**")
        return False
    if write:
        out = txt.replace("\n", eol).encode("utf-8")
        p.write_bytes(out)
        chk = p.read_bytes()
        c2, b2 = chk.count(b"\r\n"), chk.count(b"\n") - chk.count(b"\r\n")
        assert (c2 > 0 and b2 == 0) if crlf else (c2 == 0), "写回后 EOL 被破坏"
        print("    ✔ 已写入 (%d 字节, EOL=%s 保持)" % (len(chk), "CRLF" if crlf else "LF"))
    else:
        print("    ✔ 空跑通过(未写盘)")
    return True


def main() -> int:
    write = "--write" in sys.argv

    # ★ 重跑护栏: 改动不幂等("在锚点前插行"重跑会重复插入), 命中标记即拒绝
    for name, mark in MARKERS.items():
        if mark in (ROOT / name).read_bytes().decode("utf-8", "ignore"):
            print("★ 改动已在 %s 中出现(标记 %r) —— 本脚本**不幂等**, 请勿重跑。" % (name, mark))
            print("  要从 backup/*_pre_invdual_*.{py,html} 还原后再跑。")
            return 2

    print("=== %s ===" % ("写入模式" if write else "空跑模式(加 --write 才落盘)"))
    all_ok = True

    all_ok &= patch_file("quant_module.py", [
        (Q_INSERT_BEFORE_CYCLE, HELPERS.rstrip("\n") + "\n\n\n" + Q_INSERT_BEFORE_CYCLE,
         1, "Q1 插入双口径判定层(6 函数)"),
        (Q_CYCLE_RETURN_OLD, Q_CYCLE_RETURN_NEW, 1, "Q2 _inventory_cycle 返回加 social/cross"),
        (Q_ERR1_OLD, Q_ERR1_NEW, 1, "Q3 序列不足分支也抛 social"),
        (Q_ERR2_OLD, Q_ERR2_NEW, 1, "Q4 仓单全 0 分支也抛 social"),
        (Q_API_OLD, Q_API_NEW, 1, "Q5 api_quant_inventory 加 social/composite"),
    ], write)

    all_ok &= patch_file("fund_module.py", [
        (F_OLD, F_NEW, 1, "F1 库存维度改用双口径综合档位"),
        (F_VD_OLD, F_VD_NEW, 1, "F2 _strategy_verdict 入参档位补'矛盾'说明"),
    ], write)

    all_ok &= patch_file("web_template.html", [
        (W_DESC_OLD, W_DESC_NEW, 1, "W1 ①卡说明文字改双口径口径"),
        (W_SUB_OLD, W_SUB_NEW, 1, "W2 库存维度明细副标题"),
        (W_LABEL_OLD, W_LABEL_NEW, 1, "W3 仓单图标签改双口径"),
        (W_BLOCK_OLD, W_BLOCK_NEW, 1, "W4 ①卡库存块 → 双行明细 + 综合"),
        (W_BADGE_OLD, W_BADGE_NEW, 1, "W5 仓单徽标加社会库存读数"),
        (W_CYCLE_GUARD_OLD, W_CYCLE_GUARD_NEW, 1, "W6 四象限 !ok 分支抛社会库存"),
        (W_CYCLE_DEFS_OLD, W_CYCLE_DEFS_NEW, 1, "W7 四象限加 socLine/crossLine 定义"),
        (W_CYCLE_BODY_OLD, W_CYCLE_BODY_NEW, 1, "W8 四象限正文插入双口径两行"),
    ], write)

    print()
    print("=== %s ===" % ("全部通过" if all_ok else "★ 有失败项, 见上"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
