# -*- coding: utf-8 -*-
"""web_template.html(CRLF): 数据仓库「走势图」→ 季节性图 + 高度减半。

改动清单
 1. #gt-chart 高度 340px → 170px(用户要求缩到一半)
 2. 子标题「走势图」→「季节性图（按年同比）」
 3. CHART 增加 seasonBlack 令牌(2021 年线在暗色主题下不能是纯黑, 否则看不见),
    并登记进 CHART_COLOR_KEYS 让切主题重染能换色
 4. 用季节性渲染替换 _renderGtjaChart, 新增 _seasonalFromSeries/_doyOf/_seasonColor/
    _drawGtjaSeasonal/_gtSeasonalNote 五个辅助方法
 5. _initChart 的高度兜底改为"保留已有内联高度"(否则折叠态下会把 170px 改成 320px)
"""
import io

P = "web_template.html"
src = io.open(P, encoding="utf-8", newline="").read()
assert "\r\n" in src and "\n" not in src.replace("\r\n", ""), "模板不是纯 CRLF, 中止"
orig_len = len(src)


def once(old, new, tag):
    global src
    n = src.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次(应为 1)" % (tag, n)
    src = src.replace(old, new, 1)


# ── 1) 高度减半 + 2) 子标题 ────────────────────────────────────────
once('<div id="gt-chart" style="height:340px"></div>',
     '<div id="gt-chart" style="height:170px"></div>', "gt-chart 高度")

once(' 走势图 <span class="badge badge-info" id="gt-chart-badge"></span>',
     ' 季节性图（按年同比） <span class="badge badge-info" id="gt-chart-badge"></span>',
     "子标题")

# ── 3) seasonBlack 令牌 + 登记重染键 ────────────────────────────────
once(' get down() { return __cssVar(\'--green\', \'#22c55e\'); }, // 跌 / 负向',
     ' get down() { return __cssVar(\'--green\', \'#22c55e\'); }, // 跌 / 负向\r\n'
     ' // 季节性图 2021 年线: 配色包给的是纯黑(#000000), 在暗色底上等于隐形 ⇒ 暗色改浅灰。\r\n'
     ' // 用 getter(而不是常量)是为了让 _repaintCharts 切主题时能按 CHART_COLOR_KEYS 换过来。\r\n'
     ' get seasonBlack() {\r\n'
     ' let t = \'\';\r\n'
     ' try { t = document.documentElement.getAttribute(\'data-theme\') || \'\'; } catch (e) {}\r\n'
     ' return __cssVar(\'--chart-season-black\', t === \'dark\' ? \'#b8c0cc\' : \'#000000\');\r\n'
     ' },',
     "seasonBlack getter")

once(" 'kUp','kDown','kMa1','kMa2','kMa3','kMa4','kBollMid','kBollSide','kVol','kAxis','kGrid','kCross','kYlab','kFont',\r\n 'up','down'];",
     " 'kUp','kDown','kMa1','kMa2','kMa3','kMa4','kBollMid','kBollSide','kVol','kAxis','kGrid','kCross','kYlab','kFont',\r\n 'up','down','seasonBlack'];",
     "CHART_COLOR_KEYS")

# ── 5) _initChart 高度兜底 ─────────────────────────────────────────
once(" if (el.clientHeight === 0) { el.style.height = '320px'; }",
     " // 【2026-10-19】保留已有内联高度: 原写死 320px 会把 #gt-chart 的 170px 顶掉\r\n"
     " // (折叠态 clientHeight=0 时触发 ⇒ 展开后变成旧尺寸, 用户会以为缩小没生效)。\r\n"
     " if (el.clientHeight === 0) { el.style.height = el.style.height || '320px'; }",
     "_initChart 高度兜底")

# ── 4) 替换 _renderGtjaChart ──────────────────────────────────────
START = " _renderGtjaChart(ind) {\r\n"
i = src.find(START)
assert i > 0, "找不到 _renderGtjaChart"

# 从方法开始处做花括号配平, 找到方法结束
depth = 0
started = False
j = i
while j < len(src):
    ch = src[j]
    if ch == '{':
        depth += 1
        started = True
    elif ch == '}':
        depth -= 1
        if started and depth == 0:
            k = src.find('\r\n', j)
            tail = src[i:j + 1]
            break
    j += 1
else:
    raise SystemExit("花括号未配平")

# 结束行必须是 ` }`(方法尾) —— 与 harness 的判据一致
line_end = src.find('\r\n', j)
end_line = src[src.rfind('\r\n', 0, j) + 2:line_end]
assert end_line.strip() == "},", "方法末行可疑: %r" % end_line

NEW = ''' // 【功能】渲染单指标「季节性图」(按年同比) —— 2026-10-19 用户要求把走势图改为季节性图表,
 // 绘图口径照搬桌面「季节性图绘制包」(聚酯周报 PDF 那套): x=年内天数、每年一条线、
 // 当年粗线、历史年细线半透明、最新点标注「值 (±变化)」、Y=0 粉色基准线、月份轴。
 // 【为什么分两步画】ind.series 只有最近 250 点(默认 175 天) ⇒ 天生只有 1 个年份。
 // ① 先用它立刻出图(点「查看」马上有反馈); ② 再拉 /api/gtja/seasonal 拿多年历史重画。
 // 弱网/接口失败时至少留一张单年图, 不会白屏(这是本项目"空态优于假数据"的一贯口径)。
 // 【输入】ind: 指标对象(含 id/series) 【渲染】#gt-chart 【后端】GET /api/gtja/seasonal
 _renderGtjaChart(ind) {
 const badge = document.getElementById('gt-chart-badge');
 const noteEl = document.getElementById('gt-chart-note');
 const c = this._initChart('gt-chart');
 if (!c) return;
 const esc = s => this._escHtml(s == null ? '' : String(s));
 if (badge) badge.textContent = `${ind.group} · ${ind.label}`;
 const series = ind.series || [];
 if (!series.length) {
 c.clear();
 c.setOption({ backgroundColor: 'transparent',
 title: { text: '该指标无序列点', left: 'center', top: 'middle',
 textStyle: { color: CHART.axis, fontSize: 13, fontWeight: 'normal' } } }, true);
 if (noteEl) noteEl.innerHTML = '<span style="color:var(--amber)">该指标没有可绘制的数值点'
 + '(与"取数失败"不同: 上游正常应答, 但这条口径没有数值)</span>';
 return;
 }
 // ① 本地单年图(立即反馈)
 const local = this._seasonalFromSeries(series);
 this._drawGtjaSeasonal(c, ind, local);
 const nLocal = Object.keys(local.years || {}).length;
 if (noteEl) {
 noteEl.innerHTML = `${esc(ind.label)}${ind.unit ? `(${esc(ind.unit)})` : ''}`
 + ` · 季节性图 · 当前窗口 ${series.length} 点 · ${nLocal} 个年份`
 + ` · 分位 ${ind.pct == null ? '—' : ind.pct + '%'}(${esc(ind.pct_scope || '')})`
 + ' · 正在补取多年历史…';
 }
 // ② 多年历史(异步升级; 序号防串: 期间切了指标就丢弃本次结果)
 const variety = (this._gtState.overview || {}).variety || '';
 if (!ind.id || !variety || typeof fetch !== 'function') return;
 const seq = (this._gtState.chartSeq = (this._gtState.chartSeq || 0) + 1);
 fetch(`/api/gtja/seasonal?variety=${encodeURIComponent(variety)}&id=${encodeURIComponent(ind.id)}`)
 .then(r => r.json())
 .then(d => {
 if (seq !== this._gtState.chartSeq) return; // 已切到别的指标 ⇒ 本次结果作废
 if (!d || d.ok !== true || !d.years || !Object.keys(d.years).length) {
 if (noteEl) noteEl.innerHTML += `<br><span style="color:var(--amber)">多年历史未取到: `
 + `${esc((d && d.error) || '无数据')} —— 上图仅当前窗口</span>`;
 return;
 }
 this._drawGtjaSeasonal(c, ind, d);
 this._gtSeasonalNote(ind, d, noteEl);
 })
 .catch(() => {
 if (noteEl) noteEl.innerHTML += '<br><span style="color:var(--amber)">'
 + '多年历史请求失败 —— 上图仅当前窗口</span>';
 });
 },

 // 【功能】把 [[日期, 值], ...] 转成季节性结构(与后端 to_seasonal_years 同口径)
 // 【输入】series: [['2026-10-16', 7000], ...] 【返回】{years:{年:[[doy,val]]}, latest:{doy,val,diff}}
 // 【为什么前端也要一份】后端多年数据到位前先用本地窗口出图(gitja 基差默认只有 175 天)。
 _seasonalFromSeries(series) {
 const flat = [];
 (series || []).forEach(p => {
 const m = /^(\\d{4})-(\\d{2})-(\\d{2})/.exec(String((p && p[0]) || ''));
 const v = Number(p && p[1]);
 if (m && isFinite(v)) {
 flat.push({ y: +m[1], doy: this._doyOf(+m[1], +m[2], +m[3]), v: v });
 }
 });
 flat.sort((a, b) => (a.y - b.y) || (a.doy - b.doy));
 const by = {};
 flat.forEach(p => { (by[p.y] = by[p.y] || {})[p.doy] = p.v; }); // 同日重复: 后值覆盖
 const years = {};
 Object.keys(by).sort().forEach(y => {
 years[y] = Object.keys(by[y]).map(Number).sort((a, b) => a - b)
 .map(d => [d, by[y][d]]);
 });
 let latest = null;
 if (flat.length) {
 const a = flat[flat.length - 1];
 latest = { doy: a.doy, val: a.v,
 diff: flat.length > 1 ? +(a.v - flat[flat.length - 2].v).toFixed(6) : null };
 }
 return { years: years, latest: latest };
 },

 // 【功能】日期 → 年内天数(0 基), 闰年对齐到非闰年轴(2/29 之后 -1)
 // 【为什么】季节性图是"不同年份同一天并排看"; 不对齐的话闰年 3/1 会落到平年 2/29 的位置,
 // 整条曲线从 3 月起错位一格(与后端 _doy 同一口径, 前端本地出图不能算成另一套)。
 _doyOf(y, mo, da) {
 const d0 = Math.round((Date.UTC(y, mo - 1, da) - Date.UTC(y, 0, 1)) / 86400000);
 const leap = (y % 4 === 0 && y % 100 !== 0) || (y % 400 === 0);
 return Math.max(0, Math.min(365, (leap && d0 > 59) ? d0 - 1 : d0));
 },

 // 【功能】年份 → 线色(与「季节性图绘制包」season_colors.py 的 6 色映射一致)
 // 【说明】2021 的纯黑在暗色主题下不可见 ⇒ 走 CHART.seasonBlack(主题跟随)。
 _seasonColor(y) {
 const k = String(y);
 if (Object.prototype.hasOwnProperty.call(this._SEASON_YCOL, k)) {
 return (k === '2021') ? CHART.seasonBlack : this._SEASON_YCOL[k];
 }
 const i = Math.abs(parseInt(k, 10) || 0) % this._SEASON_YCOL_EXTRA.length;
 return this._SEASON_YCOL_EXTRA[i];
 },

 // 【功能】按季节性结构绘制 ECharts 图(纯渲染; 不取数)
 // 【输入】c: 图表实例; ind: 指标; data: {years:{年:[[doy,val]]}, latest:{doy,val,diff}}
 // 【渲染】#gt-chart 【后端】无
 _drawGtjaSeasonal(c, ind, data) {
 if (!c) return;
 const textColor = CHART.axis;
 const gridColor = CHART.grid;
 const yearsObj = (data && data.years) || {};
 const ys = Object.keys(yearsObj).filter(y => (yearsObj[y] || []).length).sort();
 if (!ys.length) {
 c.clear();
 c.setOption({ backgroundColor: 'transparent',
 title: { text: '该指标无序列点', left: 'center', top: 'middle',
 textStyle: { color: textColor, fontSize: 13, fontWeight: 'normal' } } }, true);
 return;
 }
 const curY = ys[ys.length - 1];
 // 月份轴: 与包里一致, 以非闰年为基准的月首天数, 标签 1月~12月
 const M_START = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
 const xLabels = new Array(366).fill('');
 M_START.forEach((d, i) => { xLabels[d] = (i + 1) + '月'; });
 const mdLabel = idx => {
 const i = Math.max(0, Math.min(365, idx | 0));
 let mo = 11;
 for (let k = 0; k < 12; k++) { if (i >= M_START[k]) mo = k; }
 return (mo + 1) + '月' + (i - M_START[mo] + 1) + '日';
 };
 const fmt = v => {
 const n = Number(v);
 if (!isFinite(n)) return '—';
 return Math.abs(n) >= 1000
 ? n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
 : String(+n.toFixed(2));
 };
 // ── Y 范围: 聚焦当年(与包内 "当年 span < 全量 45% 时聚焦" 同一思路) ──
 const allVals = [], curVals = [];
 ys.forEach(y => (yearsObj[y] || []).forEach(p => {
 const v = Number(p[1]);
 if (isFinite(v)) { allVals.push(v); if (y === curY) curVals.push(v); }
 }));
 let ymin = null, ymax = null;
 if (allVals.length) {
 const lo = Math.min.apply(null, allVals), hi = Math.max.apply(null, allVals);
 const span = hi - lo;
 if (curVals.length && span > 0) {
 const clo = Math.min.apply(null, curVals), chi = Math.max.apply(null, curVals);
 const cspan = chi - clo;
 if (cspan > 0 && cspan < span * 0.45) {
 const pad = Math.max(cspan * 0.18, Math.abs(chi - clo) * 0.02);
 ymin = clo - pad; ymax = chi + pad;
 }
 }
 if (ymin == null) {
 const pad = span > 0 ? span * 0.06 : Math.max(Math.abs(hi) * 0.06, 0.4);
 ymin = lo - pad; ymax = hi + pad;
 }
 // 给最新值标注框留顶部空间, 否则贴着画布上缘会被裁
 const lv = (data && data.latest) ? Number(data.latest.val) : null;
 if (lv != null && isFinite(lv)) ymax = Math.max(ymax, lv + (ymax - ymin) * 0.22);
 }
 const spansZero = allVals.length
 && Math.min.apply(null, allVals) <= 0 && Math.max.apply(null, allVals) >= 0;
 const series = [];
 ys.forEach(y => {
 const isCur = (y === curY);
 const color = this._seasonColor(y);
 const pts = (yearsObj[y] || [])
 .filter(p => Array.isArray(p) && isFinite(Number(p[1])))
 .map(p => [+p[0], Number(p[1])]).sort((a, b) => a[0] - b[0]);
 if (!pts.length) return;
 const s = { name: String(y), type: 'line', data: pts, showSymbol: false,
 smooth: 0.32, smoothMonotone: 'x', symbolSize: 5,
 lineStyle: { width: isCur ? 2.4 : 1.2, color: color, opacity: isCur ? 1 : 0.6 },
 itemStyle: { color: color }, z: isCur ? 6 : 3 };
 if (isCur && data && data.latest && data.latest.val != null) {
 const lv = Number(data.latest.val), df = data.latest.diff;
 const txt = fmt(lv) + (df == null ? '' : ` (${df >= 0 ? '+' : ''}${fmt(df)})`);
 s.markPoint = { symbol: 'circle', symbolSize: 8, silent: true,
 itemStyle: { color: '#ffffff', borderColor: '#E11D1D', borderWidth: 1.6 },
 label: { show: true, position: 'top', distance: 6, formatter: () => txt,
 color: '#E11D1D', fontSize: 10, fontWeight: 'bold',
 backgroundColor: 'rgba(255,255,255,0.88)',
 borderColor: '#E8B4B4', borderWidth: 1, borderRadius: 3, padding: [2, 4] },
 data: [{ coord: [Number(data.latest.doy), lv] }] };
 }
 if (isCur && spansZero) {
 s.markLine = { silent: true, symbol: 'none', z: 1,
 lineStyle: { color: '#FFB3B3', width: 1 }, data: [{ yAxis: 0 }] };
 }
 series.push(s);
 });
 c.setOption({
 backgroundColor: 'transparent',
 animationDuration: 260,
 tooltip: { trigger: 'axis', confine: true,
 axisPointer: { type: 'line', lineStyle: { color: gridColor, type: 'dashed' } },
 formatter: ps => {
 if (!ps || !ps.length) return '';
 const idx = (ps[0].value && ps[0].value[0] != null) ? ps[0].value[0] : 0;
 return mdLabel(idx) + ps.map(p => `<br>${p.marker}${p.seriesName}: ${fmt(p.value[1])}`).join('');
 } },
 legend: { bottom: 0, left: 'center', itemGap: 10, itemWidth: 12, itemHeight: 3,
 icon: 'roundRect', textStyle: { color: textColor, fontSize: 9 },
 data: ys.map(String) },
 grid: { left: 46, right: 14, top: 10, bottom: 44 },
 xAxis: { type: 'category', data: xLabels, boundaryGap: false,
 axisLine: { lineStyle: { color: gridColor } }, axisTick: { show: false },
 axisLabel: { color: textColor, fontSize: 9, interval: 0,
 showMinLabel: true, showMaxLabel: true },
 splitLine: { show: false } },
 yAxis: { type: 'value', scale: true,
 min: ymin == null ? undefined : ymin, max: ymax == null ? undefined : ymax,
 axisLabel: { color: textColor, fontSize: 9 }, splitNumber: 3,
 splitLine: { lineStyle: { color: gridColor } } },
 series: series,
 }, true);
 },

 // 【功能】季节性图的脚注(年份跨度/点数/分位/取数告警)
 // 【输入】ind 指标; d 后端 payload; noteEl 容器 【渲染】#gt-chart-note 【后端】无
 _gtSeasonalNote(ind, d, noteEl) {
 if (!noteEl) return;
 const esc = s => this._escHtml(s == null ? '' : String(s));
 const keys = Object.keys(d.years || {}).sort();
 const first = keys[0] || '', last = keys[keys.length - 1] || '';
 noteEl.innerHTML = `${esc(ind.label)}${ind.unit ? `(${esc(ind.unit)})` : ''}`
 + ` · 季节性对比 ${keys.length} 个年份（${esc(first)}–${esc(last)}）`
 + ` · ${d.point_count || 0} 点`
 + ` · 分位 ${ind.pct == null ? '—' : ind.pct + '%'}(${esc(ind.pct_scope || '')})`
 + (d.cached ? ' · 缓存' : '')
 + (ind.note ? `<br>${esc(ind.note)}` : '')
 + (d.warning ? `<br><span style="color:var(--amber)">⚠ ${esc(d.warning)}</span>` : '');
 },'''.replace('\n', '\r\n')

src = src[:i] + NEW + src[line_end:]

# ── 6) 年份配色常量(插在 _renderGtjaChart 前) ─────────────────────
CONST = ''' // 季节性图年份配色 —— 与「季节性图绘制包」season_colors.py 一致:
 // 2021 黑 / 2022 橙 / 2023 紫 / 2024 蓝 / 2025 绿 / 2026 红(当年)。
 // 超出 2021~2026 的年份用 _SEASON_YCOL_EXTRA 轮转(包里只有 6 色, 直接复用会让两年同色)。
 _SEASON_YCOL: { 2021: '#000000', 2022: '#FFC000', 2023: '#70449F',
 2024: '#00B0F0', 2025: '#00B050', 2026: '#FF0000' },
 _SEASON_YCOL_EXTRA: ['#8C8C8C', '#C0504D', '#4F81BD', '#9BBB59', '#8064A2', '#4BACC6'],

'''.replace('\n', '\r\n')
assert src.count(NEW[:200]) == 1
src = src.replace(' ' + '// 【功能】渲染单指标「季节性图」', CONST + ' // 【功能】渲染单指标「季节性图」', 1)

io.open(P, "w", encoding="utf-8", newline="").write(src)
print("OK ->", P, "| chars", orig_len, "->", len(src))
