"""技术面总览「进页面不再重新计算」—— 一次性补丁（2026-10-16）。

【诊断：两处根因，都不是"缓存没生效"】
  实测后端缓存在命中时确实秒回（连打 4 次总览均 0.00s / done=True / 21/21）。
  真正的原因是：

  ① **离开量化页时没有停「总览」的定时器。** `switchTab` 开头只停了 K 线定时器
     (`if (t !== 'quant') this._clearKlineRefresh();`)，而 `_stopQuantOverview()`
     **只在"进单品种详情"(`qOpenDetail`) 时被调用**。于是用户切到别的 tab 后，
     15 分钟定时器仍在跑；每次触发都请求总览，而那一刻后端 TTL(900s) 恰好到期
     ⇒ **没人在看也会重算一轮(~105s)**，且 3s 渐进轮询跟着空转。
     用户切回来时经常正好撞在重算中间 ⇒ 表现就是"每次进这个页面都要重新加载计算"。

  ② **TTL 到期后，旧结果被清空 ⇒ 只能干等一整轮。** `_ov_start` 判定不新鲜时执行
     `_OV_STATE[key] = {"rows": {}, "done": False, ...}` ⇒ 前端 `done=False` ⇒
     回到"计算中 x/21"并等 ~105 秒。所以只要距上次计算超过 15 分钟
     (关掉/刷新浏览器再回来)，**必然**经历一轮完整等待。
     用户对"缓存"的预期是"进页面秒开"。

【修法】
  ① `switchTab` 离开 quant 时调 `_stopQuantOverview()`（与 K 线同款处理）。
  ② **stale-while-revalidate**：需重算时**保留旧 rows**（`carry`），全程 `done=True`
     + `refreshing=True` ⇒ 前端**秒开旧表**，后台算好的品种逐个覆盖（"就地刷新"）；
     只有"从无结果"的首次计算才显示进度条。
     同时修正 `cached` 的语义（原为 `done and not err`，只要算完就报"缓存"，是假的）。

【★ EOL】quant_module.py / tests = LF；web_template.html = CRLF。脚本归一后写回并断言。
【★ 不幂等】开头检测标记即拒绝重跑。

用法: python probe/_ov_cache_patch.py [--write]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MARKERS = {"quant_module.py": "stale-while-revalidate", "web_template.html": "_QOV_REFRESH_POLL_MS"}

# ═══════════════════ quant_module.py ═══════════════════
Q_WORKER_OLD = '''def _ov_worker(key: str, codes: list[str]) -> None:
    """后台线程主体：阶段 A(日线层) → 阶段 B(多周期)，每完成一个品种就发布一次。

    【为什么逐品种发布】用户希望"越早看到东西越好"。阶段 A 每完成一个品种就把
    rows 快照写回 `_OV_STATE`，前端下一次轮询即可看到该行 —— 不必等 21 个全好。
    【失败不致命】单品种异常写入兜底行(带 `error` 字段)，整轮永远会走到 done=True，
    否则前端会无限轮询。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: dict[str, dict] = {}

    def _publish(done: bool = False, err: str = "") -> None:
        with _OV_LOCK:
            st = _OV_STATE.get(key)
            if st is None:      # 被显式 refresh 清掉了 ⇒ 放弃本轮
                return
            st["rows"] = dict(rows)
            st["done"] = done
            st["ts"] = time.time()
            if err:
                st["err"] = err'''

Q_WORKER_NEW = '''def _ov_worker(key: str, codes: list[str], carry: dict | None = None) -> None:
    """后台线程主体：阶段 A(日线层) → 阶段 B(多周期)，每完成一个品种就发布一次。

    【为什么逐品种发布】用户希望"越早看到东西越好"。阶段 A 每完成一个品种就把
    rows 快照写回 `_OV_STATE`，前端下一次轮询即可看到该行 —— 不必等 21 个全好。
    【失败不致命】单品种异常写入兜底行(带 `error` 字段)，整轮永远会走到 done=True，
    否则前端会无限轮询。
    【★ carry = 上一轮结果（stale-while-revalidate, 2026-10-16）】TTL 到期重算时把旧
      rows 带进来 ⇒ ① 前端**秒开旧表**(而不是回到"计算中 0/21"干等一整轮 ~105s);
      ② 逐品种覆盖 ⇒ 表里每一行算好一个就换成新的, 用户看到的是"就地刷新"。
      ⚠ 有旧结果时 `done` 必须**全程保持 True** —— 否则前端会把已显示的表格换成进度条,
      又把"秒开"毁掉(这是本次修复的核心)。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: dict[str, dict] = dict(carry or {})   # 【变量】带上旧结果, 逐品种覆盖
    had_old = bool(carry)                       # 【变量】有旧结果 ⇒ 全程 done=True
    prog = {"n": 0}                             # 【变量】本轮已刷新的品种数(供前端显示进度)

    def _publish(done: bool = False, err: str = "") -> None:
        with _OV_LOCK:
            st = _OV_STATE.get(key)
            if st is None:      # 被显式 refresh 清掉了 ⇒ 放弃本轮
                return
            st["rows"] = dict(rows)
            st["done"] = bool(done or had_old)
            st["refreshing"] = not done
            st["stale"] = bool(had_old and not done)
            st["refreshed"] = prog["n"]
            st["ts"] = time.time()
            if err:
                st["err"] = err'''

# 阶段 A / B 的计数（用日志文案定位，两处不同）
Q_A_OLD = '''                except Exception as e:  # noqa: BLE001
                    logger.warning("总览-阶段A失败(%s): %s", c, e)
                    rows[c] = _ov_fallback_row(c, str(e))
                _publish()'''
Q_A_NEW = '''                except Exception as e:  # noqa: BLE001
                    logger.warning("总览-阶段A失败(%s): %s", c, e)
                    rows[c] = _ov_fallback_row(c, str(e))
                prog["n"] += 1
                _publish()'''

Q_B_OLD = '''                except Exception as e:  # noqa: BLE001
                    logger.warning("总览-阶段B失败(%s): %s", c, e)
                _publish()'''
Q_B_NEW = '''                except Exception as e:  # noqa: BLE001
                    logger.warning("总览-阶段B失败(%s): %s", c, e)
                prog["n"] += 1
                _publish()'''

Q_START_OLD = '''def _ov_start(codes: list[str], force: bool = False) -> str:
    """确保后台计算已启动（幂等）。返回 state key。"""
    key = _ov_state_key(codes)
    with _OV_LOCK:
        st = _OV_STATE.get(key)
        fresh = bool(st and st.get("done") and time.time() - st.get("ts", 0) < _TTL_OVERVIEW)
        if st is not None and not st.get("done") and not force:
            return key                     # 正在跑, 不重复起线程
        if fresh and not force:
            return key                     # 结果还新鲜
        _OV_STATE[key] = {"rows": {}, "done": False, "ts": time.time(), "err": ""}
    t = threading.Thread(target=_ov_worker, args=(key, list(codes)),
                         name="quant-overview", daemon=True)
    t.start()
    return key'''

Q_START_NEW = '''def _ov_start(codes: list[str], force: bool = False) -> str:
    """确保后台计算已启动（幂等）。返回 state key。

    【★ stale-while-revalidate（2026-10-16 修）】此前 TTL 一到就把 rows **清空**再重算
      ⇒ 用户每次回到页面(距上次 >15min) 都得对着进度条等一整轮(~105s) ——
      这正是"为什么每次进入该页面还是要重新加载计算"那个投诉的直接来源。
    现改为：
      · 已有旧结果 ⇒ **保留**它并在后台重算：全程 `done=True` + `refreshing=True`
        ⇒ 前端**秒开旧表**，算好的品种逐个替换；从无结果(首次)才走空表 + 渐进轮询。
      · 真正决定"要不要重算"的仍然是 `_TTL_OVERVIEW`，只是重算期间**不再剥夺**
        用户已经能看到的数据。
    【状态字段】rows / done(有可用全量结果) / refreshing(后台正在算) /
      stale(当前 rows 是上一轮的) / refreshed(本轮已刷新品种数) / ts / err。
    """
    key = _ov_state_key(codes)
    with _OV_LOCK:
        st = _OV_STATE.get(key) or {}
        refreshing = bool(st.get("refreshing"))
        done = bool(st.get("done"))
        # 新鲜 = 算完过、当前不在刷新、不是旧数据、且未过期
        fresh = (done and not refreshing and not st.get("stale")
                 and time.time() - st.get("ts", 0) < _TTL_OVERVIEW)
        if refreshing:
            # 已有后台刷新在跑（显式 force 也不再起一个线程 —— 那一轮马上就会给出新数据）
            return key
        if fresh and not force:
            return key                     # 结果还新鲜, 命中
        has_old = bool(st.get("rows"))
        carry = dict(st["rows"]) if has_old else None
        _OV_STATE[key] = {"rows": dict(carry or {}), "done": has_old, "refreshing": True,
                          "stale": has_old, "refreshed": 0, "err": "",
                          "ts": st.get("ts", 0)}
    t = threading.Thread(target=_ov_worker, args=(key, list(codes), carry),
                         name="quant-overview", daemon=True)
    t.start()
    return key'''

Q_SNAP_OLD = '''def _ov_snapshot(key: str, codes: list[str]) -> tuple[list[dict], bool, str]:
    """取当前快照（可能是部分结果）。返回 (rows_按 codes 序, done, err)。"""
    with _OV_LOCK:
        st = _OV_STATE.get(key) or {}
        rows_map = st.get("rows") or {}
        done = bool(st.get("done"))
        err = st.get("err") or ""
    return ([rows_map[c] for c in codes if c in rows_map], done, err)'''

Q_SNAP_NEW = '''def _ov_snapshot(key: str, codes: list[str]) -> tuple[list[dict], bool, str, dict]:
    """取当前快照（可能是部分结果）。返回 (rows_按 codes 序, done, err, meta)。

    【meta（2026-10-16 新增）】{refreshing, stale, refreshed, ts}
      —— 供前端区分三态，而不是只有"算完/没算完"两种：
        · 首次计算中(done=False)          → 显示进度条
        · 有旧数据+后台刷新中(refreshing)  → **旧表继续显示** + 短轮询等新数据落地
        · 完成且不再刷新                   → 排 15 min 定时器
    """
    with _OV_LOCK:
        st = _OV_STATE.get(key) or {}
        rows_map = st.get("rows") or {}
        done = bool(st.get("done"))
        err = st.get("err") or ""
        meta = {"refreshing": bool(st.get("refreshing")),
                "stale": bool(st.get("stale")),
                "refreshed": int(st.get("refreshed") or 0),
                "ts": float(st.get("ts") or 0)}
    return ([rows_map[c] for c in codes if c in rows_map], done, err, meta)'''

Q_API_OLD = '''    key = _ov_start(codes, force=force)
    rows, done, err = _ov_snapshot(key, codes)'''
Q_API_NEW = '''    key = _ov_start(codes, force=force)
    rows, done, err, meta = _ov_snapshot(key, codes)'''

Q_RET_OLD = '''    return jsonify(_json_safe({
        "ok": True, "count": len(rows), "ready": len(rows), "total": len(codes),
        "done": done, "cached": done and not err, "err": err,'''
Q_RET_NEW = '''    return jsonify(_json_safe({
        "ok": True, "count": len(rows), "ready": len(rows), "total": len(codes),
        "done": done,
        # 【★ cached 语义修正 2026-10-16】原为 `done and not err` —— 只要算完就报"缓存",
        #   哪怕就是**刚刚现算出来**的(实测第一轮就 `cached=true`)。现改为如实反映
        #   "本次拿到的是一份**没在刷新**的新鲜结果"。
        "cached": bool(done and not meta["refreshing"] and not meta["stale"] and not err),
        "refreshing": meta["refreshing"], "stale": meta["stale"],
        "refreshed": meta["refreshed"], "ts": meta["ts"], "err": err,'''

# ═══════════════════ web_template.html ═══════════════════
W_SWITCH_OLD = '''if (t !== 'quant') this._clearKlineRefresh();'''

W_SWITCH_NEW = '''if (t !== 'quant') this._clearKlineRefresh();
 // 【2026-10-16 修】同理, 离开量化页还必须停掉**总览**的定时器与轮询 ——
 //   此前 `_stopQuantOverview()` **只在"进单品种详情"时**被调用(qOpenDetail),
 //   切到别的 tab 时 15 分钟定时器仍在跑: 每次触发都请求总览, 而那一刻后端 TTL(900s)
 //   恰好到期 ⇒ **没人在看也重算一轮(~105s)**, 3s 渐进轮询也跟着空转。
 //   用户切回来时经常正好撞在重算中间 ⇒ 表现就是"每次进这个页面都要重新加载计算"。
 if (t !== 'quant') this._stopQuantOverview();'''

W_CONST_OLD = ''' _QOV_REFRESH_MS: 15 * 60 * 1000,   // 【变量】总览自动刷新周期 = 15 min（与后端 _TTL_OVERVIEW 对齐）'''
W_CONST_NEW = ''' _QOV_REFRESH_MS: 15 * 60 * 1000,   // 【变量】总览自动刷新周期 = 15 min（与后端 _TTL_OVERVIEW 对齐）
 // 【变量】"已有旧结果、后台正在刷新"时的短轮询间隔(5s) —— 见 loadQuantOverview 的 refreshing 分支
 _QOV_REFRESH_POLL_MS: 5000,'''

W_LOAD_OLD = '''  this._qovData = d;
  this._renderQuantOverview();
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
  if (!d.done) {
   // 后台还在算 ⇒ 3s 渐进轮询(每完成一个品种 ready+1)
   this._qovNextAt = 0;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), 3000);
  } else {
   // 【2026-10-14 用户要求】「每隔 15 分钟刷新一次并缓存」。
   //   到点重取 ⇒ 后端缓存刚过期 ⇒ 自动重算 ⇒ done=False ⇒ 上面那条 3s 渐进轮询接管,
   //   用户看到的是"又算了一遍"的进度, 而不是一个卡死的旧表。
   this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_MS);
  }
  this._renderQuantOverview();   // 重画一次: 让"下次刷新 HH:MM"立刻反映 _qovNextAt'''

W_LOAD_NEW = '''  this._qovData = d;
  this._renderQuantOverview();
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
  if (!d.done) {
   // ①【首次计算】还没有任何结果可看 ⇒ 3s 渐进轮询(每完成一个品种 ready+1)
   this._qovNextAt = 0;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), 3000);
  } else if (d.refreshing) {
   // ②【有旧结果 + 后台重算(2026-10-16)】**旧表继续显示, 不再回到进度条**;
   //   用 5s 短轮询等新数据逐个落地 —— 这就是"进页面秒开"的关键分支。
   //   ★ 这里**不设** _qovNextAt: 此刻显示"下次刷新的时刻"是过期信息(正在刷新的这一轮
   //     结束前, 下次时刻还没确定)。
   this._qovNextAt = 0;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_POLL_MS);
  } else {
   // ③【完成且空闲】「每隔 15 分钟刷新一次并缓存」。
   //   到点重取 ⇒ 后端缓存刚过期 ⇒ 起后台刷新 ⇒ 走上面 ② 分支(旧表仍在, 就地刷新)。
   this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_MS);
  }
  this._renderQuantOverview();   // 重画一次: 让"下次刷新 HH:MM"立刻反映 _qovNextAt'''

W_PROG_OLD = '''  const pg = document.getElementById('q-ov-progress');
  if (pg) {
   let _next = '';
   if (d.done && this._qovNextAt) {
    const _t = new Date(this._qovNextAt);
    _next = ' · 每 15 分钟自动刷新（下次 '
     + String(_t.getHours()).padStart(2, '0') + ':'
     + String(_t.getMinutes()).padStart(2, '0') + '）';
   }
   pg.textContent = d.done
    ? ('计算完成 · ' + all.length + ' 个品种 · ' + (d.cached ? '缓存' : '实时') + _next)
    : ('计算中 ' + d.ready + '/' + d.total + ' 个品种…');
   pg.style.color = d.done ? 'var(--text-muted)' : 'var(--amber)';
  }'''

W_PROG_NEW = '''  const pg = document.getElementById('q-ov-progress');
  if (pg) {
   let _next = '';
   if (d.done && !d.refreshing && this._qovNextAt) {
    const _t = new Date(this._qovNextAt);
    _next = ' · 每 15 分钟自动刷新（下次 '
     + String(_t.getHours()).padStart(2, '0') + ':'
     + String(_t.getMinutes()).padStart(2, '0') + '）';
   }
   if (!d.done) {
    // ① 首次计算: 表里还没有数据
    pg.textContent = '计算中 ' + d.ready + '/' + d.total + ' 个品种…';
    pg.style.color = 'var(--amber)';
   } else if (d.refreshing) {
    // ② 有旧结果 + 后台重算: 必须让用户知道"表里是上一轮的数, 不用等"
    //    (否则他会以为数字是新的; 也会以为页面卡住了)
    pg.textContent = '数据截至 ' + (dates && dates.length ? dates[dates.length - 1] : '—')
     + ' · 后台刷新中 ' + (d.refreshed || 0) + '/' + d.total
     + ' 个品种（表格显示上一轮结果, 算好的行会就地更新, 无需等待）';
    pg.style.color = 'var(--amber)';
   } else {
    pg.textContent = '计算完成 · ' + all.length + ' 个品种 · ' + (d.cached ? '缓存' : '实时') + _next;
    pg.style.color = 'var(--text-muted)';
   }
  }'''

# ═══════════════════ tests/test_quant_overview_refresh.py ═══════════════════
T_OLD = '''        st = qm._OV_STATE[key]
        st["done"] = True
        st["ts"] = time.time() - 60
        qm._ov_start(codes)
        assert len(started) == 1, "★ TTL 内（完成且 <900s）⇒ 命中缓存，不得重算"

        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        qm._ov_start(codes)
        assert len(started) == 2, "★ 超过 TTL ⇒ 必须重算（否则「每 15 分钟刷新」是假的）"'''

T_NEW = '''        st = qm._OV_STATE[key]
        # 【2026-10-16】本用例**手工伪造** state ⇒ 必须把新字段一并伪造到位:
        #   `refreshing` 现在也是一条"别起新线程"的理由(有后台刷新在跑)。
        #   不置 False 的话, 下面两条断言会因为"仍在刷新"而通过 —— 看着绿, 实际**没测到
        #   TTL 新鲜度语义**(本用例存在的唯一理由)。
        st["done"] = True
        st["refreshing"] = False
        st["stale"] = False
        st["ts"] = time.time() - 60
        qm._ov_start(codes)
        assert len(started) == 1, "★ TTL 内（完成且 <900s）⇒ 命中缓存，不得重算"

        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        qm._ov_start(codes)
        assert len(started) == 2, "★ 超过 TTL ⇒ 必须重算（否则「每 15 分钟刷新」是假的）"
        # 【新增】★ stale-while-revalidate: 重算时**必须保留旧结果**, 不能清空 ——
        #   清空就是"每次进页面都得干等一整轮(~105s)"那个投诉的直接成因。
        qm._OV_STATE[key]["rows"] = {"TA": {"variety": "TA", "state": "偏多"}}
        st = qm._OV_STATE[key]
        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        st["refreshing"] = False
        st["stale"] = False
        qm._ov_start(codes)
        assert len(started) == 3, "超过 TTL 必须再起一轮"
        st = qm._OV_STATE[key]
        assert st.get("refreshing") is True, "重算中必须标 refreshing（前端据此保留旧表）"
        assert st.get("done") is True, (
            "★ 有旧结果时重算期间 done 必须为 True —— 否则前端会把已显示的表格换成进度条"
        )
        assert st.get("rows"), "★ 重算时不得清空旧 rows（stale-while-revalidate）"'''

T2_OLD = '''def test_stop_overview_clears_timer_and_next_at():'''
T2_NEW = '''def test_switchtab_stops_overview_when_leaving_quant():
    """★ A5（2026-10-16 新增）：切走 tab 必须停总览的定时器/轮询。

    【为什么必须有这条】`_stopQuantOverview()` 原本**只在进单品种详情时**被调用,
    切到别的 tab 时 15 分钟定时器仍在跑 ⇒ 每次触发都请求总览, 而那一刻后端 TTL 恰好到期
    ⇒ **没人在看也重算一轮**, 且 3s 轮询空转。这正是用户"每次进这个页面都要重新加载计算"
    的直接成因。判据要落在**switchTab 真的调了它**, 而不是"函数本身写得对"。
    """
    s = _tpl()
    i = s.index("switchTab(t) {")
    seg = s[i:i + 900]
    assert "if (t !== 'quant') this._stopQuantOverview();" in seg, (
        "switchTab 离开量化页时没有停总览（后台空转 + 撞 TTL 边界重算）"
    )
    # 与 K 线定时器同一处处理（形态一致性）—— 别哪天只改了一边
    assert "if (t !== 'quant') this._clearKlineRefresh();" in seg, "K 线定时器那处被改动了"


def test_ov_start_keeps_old_rows_when_revalidating():
    """★ A6：`_ov_start` 的 stale-while-revalidate 语义（行为级）。

    只断言常量/文本是测不到的 —— 判据要证明"重算时旧 rows 还在"以及
    "刷新中不会重复起线程""新鲜时不重算"。
    """
    codes = ["TA"]
    key = qm._ov_state_key(codes)
    started: list = []

    class _FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started.append((args[0] if args else None, args[2] if len(args) > 2 else None))

        def start(self):
            pass

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(threading, "Thread", _FakeThread)
    monkeypatch.setattr(qm, "_ov_worker", lambda *a, **k: None)
    try:
        qm._OV_STATE.pop(key, None)
        qm._ov_start(codes)
        assert qm._OV_STATE[key]["refreshing"] is True, "首次计算也要标 refreshing"
        qm._ov_start(codes)
        assert len(started) == 1, "刷新中不得重复起线程"
        # 造一个"过期但有过结果"的状态
        st = qm._OV_STATE[key]
        st["rows"] = {"TA": {"variety": "TA"}}
        st["done"] = True
        st["refreshing"] = False
        st["stale"] = False
        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        qm._ov_start(codes)
        assert len(started) == 2, "过期必须重算"
        assert started[1][1], "★ 重算时要把旧 rows 作为 carry 传给 worker（否则旧表会被清空）"
        st = qm._OV_STATE[key]
        assert st["rows"] == {"TA": {"variety": "TA"}}, "★ 重算时旧 rows 被清空了"
        assert st["done"] is True and st["refreshing"] is True, "应处于'有数据+刷新中'"
        # 新鲜时不得重算
        st["refreshing"] = False
        st["stale"] = False
        st["ts"] = time.time() - 10
        qm._ov_start(codes)
        assert len(started) == 2, "TTL 内不得重算"
    finally:
        qm._OV_STATE.pop(key, None)
        monkeypatch.undo()


def test_stop_overview_clears_timer_and_next_at():'''


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
        print("    %s %-44s 命中 %d (期望 %d)" % ("OK " if got == want else "★  ", why, got, want))
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
    ok &= patch("quant_module.py", [
        (Q_WORKER_OLD, Q_WORKER_NEW, 1, "Q1 worker 支持 carry + 发布 refreshing/stale"),
        (Q_A_OLD, Q_A_NEW, 1, "Q2 阶段A 计数"),
        (Q_B_OLD, Q_B_NEW, 1, "Q3 阶段B 计数"),
        (Q_START_OLD, Q_START_NEW, 1, "Q4 _ov_start stale-while-revalidate"),
        (Q_SNAP_OLD, Q_SNAP_NEW, 1, "Q5 _ov_snapshot 返回 meta"),
        (Q_API_OLD, Q_API_NEW, 1, "Q6 路由接收 meta"),
        (Q_RET_OLD, Q_RET_NEW, 1, "Q7 修正 cached 语义 + 暴露 refreshing"),
    ], write)
    ok &= patch("web_template.html", [
        (W_SWITCH_OLD, W_SWITCH_NEW, 1, "W1 switchTab 离开时停总览"),
        (W_CONST_OLD, W_CONST_NEW, 1, "W2 新增短轮询常量"),
        (W_LOAD_OLD, W_LOAD_NEW, 1, "W3 三分支排期(首算/刷新中/空闲)"),
        (W_PROG_OLD, W_PROG_NEW, 1, "W4 进度条三态文案"),
    ], write)
    ok &= patch("tests/test_quant_overview_refresh.py", [
        (T_OLD, T_NEW, 1, "T1 伪造 state 补新字段 + 新增 stale 断言"),
        (T2_OLD, T2_NEW, 1, "T2 新增两条行为级用例"),
    ], write)
    print()
    print("=== %s ===" % ("全部通过" if ok else "★ 有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
