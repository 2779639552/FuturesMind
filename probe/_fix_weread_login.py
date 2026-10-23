# -*- coding: utf-8 -*-
"""修 `probe/_weread_login.mjs`：登录态**真验证** + `--force` + 探测疑似替代接口。

═══ 为什么必须修（2026-10-16 用户实跑踩到）═══
原脚本第 96 行判"已登录"的写法是：
    let already = (await ctx.cookies()).some((c) => c.name === 'wr_skey');
即**只看 cookie 名字在不在**，不看它有没有效。而旧 cookie 会被注入浏览器上下文
（第 85 行 ctx.addCookies），于是：
    脚本报「已处于登录态，跳过扫码」→ **一键重登永远走不到扫码那一步**
（用户 16:15 实跑即此；随后验证返 -2041，且 cookies.json 的 savedAt 被**假刷新**成 16:15）。

═══ 本轮查到的错误码语义（外部权威来源 + 本地实测一致）═══
  -2010  用户不存在 / 未登录（没带 cookie 或会话没建立）
  -2012  登录超时（cookie 过期）
  -2041  **认证 / 风控错误**（会弹腾讯验证码；也是"接口已废弃"讹传的来源 ——
         **换新鲜 cookie 往往就通了**）
  -2042  该文章未收录
  另：官方 Agent API 的 `/store/search` scope=2/4（公众号/公众号文章）**本就未对 API Key 开放**
      ⇒ 那两个 tab 返 -2041 属设计如此，不是账号问题。

═══ 本次改动 ═══
  E1 新增 checkSession()   —— 用 `/web/shelf/sync` **真验证**登录态（该接口不受那层验证码影响）
  E2 新增 fetchCover()     —— 探测 `/web/mp/cover`（疑似替代 /web/mp/articles 的轻量增量接口）
  E3 新增 --force          —— 用**干净上下文**（不注入旧 cookie）+ 跳过校验，强制走扫码
  E4 登录判据改用 checkSession()
  E5 验证段：每个号同时打 articles 与 cover，并打印错误码释义
⚠ LF 文件，不写字面转义。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE = "--write" in sys.argv
P = ROOT / "probe" / "_weread_login.mjs"
raw = P.read_bytes()
assert raw.count(b"\r\n") == 0, "本文件应为 LF"
t = raw.decode("utf-8")
report = []


# ── E1/E2 插入两个 helper（放在「主流程」之前）─────────────────
A1 = "// ── 主流程 ───────────────────────────────────────────────────────\n"
assert t.count(A1) == 1, ("A1", t.count(A1))
HELPERS = """// ── 登录态**真验证**：/web/shelf/sync ─────────────────────────────
// 【为什么必须真验证】原判据是"cookie 里有没有 wr_skey" —— 但 wr_skey 会超时失效，
//   名字还在、值已作废 ⇒ 误判"已登录"、跳过扫码 ⇒ **一键重登永远走不到扫码那一步**
//   （2026-10-16 实测踩到：脚本报"已处于登录态，跳过扫码"，紧接着验证却是 -2041）。
// 【为什么用这个接口】实测 `/web/shelf/sync` **不受** /web/mp/articles 那层风控验证码影响
//   ⇒ 能干净地区分"登录态有效 / 无效"，而不是被风控码带偏。
async function checkSession(cookieHeader) {
  const r = await fetch('https://weread.qq.com/web/shelf/sync', {
    headers: {
      'User-Agent': UA,
      Accept: 'application/json, text/plain, */*',
      Referer: 'https://weread.qq.com/',
      Cookie: cookieHeader,
    },
  });
  const text = await r.text();
  let body = null;
  try { body = JSON.parse(text); } catch { /* 非 JSON */ }
  if (!body || typeof body !== 'object') {
    return { ok: false, code: 'nonJSON', msg: text.slice(0, 80), status: r.status };
  }
  const code = body.errCode ?? body.errcode;
  return {
    ok: (code === undefined || code === null || code === 0),
    code: code === undefined ? '(无)' : code,
    msg: body.errMsg || body.errmsg || '',
    status: r.status,
  };
}

// ── 轻量增量接口探测：/web/mp/cover ───────────────────────────────
// 【为什么要试它】有活跃开源项目实测：新版微信读书**已废弃 /web/mp/articles**
//   （恒返 -2041），前端 JS 里也不再有列表接口；替代方案是
//     · /web/mp/cover?bookId=MP_WXS_xxx  → 该号**最新一篇**（reviewId + 标题 + 封面）
//     · /web/mp/content?reviewId=xxx     → 正文（#js_content）
//   ⇒ 若 cover 可用，就能用"**每号 1 个请求**"的轻量增量替代"整表翻页（127 个请求）"，
//     风控暴露面小一个数量级 —— 这对"会话撑不到下一次运行"的毛病也是好事。
async function fetchCover(cookieHeader, bookId) {
  const url = `https://weread.qq.com/web/mp/cover?bookId=${encodeURIComponent(bookId)}`;
  const r = await fetch(url, {
    headers: {
      'User-Agent': UA,
      Accept: 'application/json, text/plain, */*',
      Referer: 'https://weread.qq.com/',
      Cookie: cookieHeader,
    },
  });
  const text = await r.text();
  let body = null;
  try { body = JSON.parse(text); } catch { /* 非 JSON */ }
  return { status: r.status, text, body };
}

// ── 错误码释义（照抄服务端 errMsg + 外部实测，不推测）───────────
const CODE_MEANING = {
  '-2010': '未登录/用户不存在（没带 cookie 或会话没建立）',
  '-2012': '登录超时（cookie 过期 ⇒ 重新登录）',
  '-2041': '认证/风控错误（可能弹验证码；**换新鲜 cookie 往往就通**；也可能是该接口对新版已废弃）',
  '-2042': '该文章未收录',
};

"""
t = t.replace(A1, HELPERS + A1, 1)
report.append("  ✓ E1/E2 helper 已插入（checkSession / fetchCover / CODE_MEANING）")

# ── E3 --force 常量 ─────────────────────────────────────────────
A3 = "const CHECK_ONLY = argv.includes('--check-only');\n"
assert t.count(A3) == 1
t = t.replace(A3, A3 + "const FORCE = argv.includes('--force');   // 强制走扫码（用干净上下文，忽略已存 cookie）\n", 1)
report.append("  ✓ E3 --force 常量")

# ── E3b 注入 cookie 时尊重 --force ──────────────────────────────
A4 = """const saved = loadCookies();
if (saved) {
  console.log(`[cookie] 发现已存 cookie（存于 ${saved.savedAt}）：${[...saved.has].join(', ')}`);
  await ctx.addCookies(saved.cookies);
} else {
  console.log('[cookie] 无已存 cookie');
}
"""
assert t.count(A4) == 1, ("A4", t.count(A4))
N4 = """const saved = loadCookies();
if (saved && FORCE) {
  // ★ --force 必须用**干净上下文**：注入了旧 cookie 的话，目标站会认为"你还登录着"，
  //   登录入口不出现 ⇒ 二维码永远弹不出来（这就是"强制重登"要做到位的地方）。
  console.log(`[cookie] --force：**不注入**已存 cookie（存于 ${saved.savedAt}），用干净上下文以便出现登录入口`);
} else if (saved) {
  console.log(`[cookie] 发现已存 cookie（存于 ${saved.savedAt}）：${[...saved.has].join(', ')}`);
  await ctx.addCookies(saved.cookies);
} else {
  console.log('[cookie] 无已存 cookie');
}
// ★ 提示：savedAt 只是"文件被写的时刻"，**不代表那次真的登录过** ——
//   本脚本在 CHECK_ONLY=false 且判定"已登录"时也会重存一次 cookie（刷新 savedAt）。
//   ⇒ 别拿 savedAt 当"登录时刻"用（2026-10-16 实测被它误导过一次）。
"""
t = t.replace(A4, N4, 1)
report.append("  ✓ E3b 注入 cookie 时尊重 --force（干净上下文）")

# ── E4 登录判据改真验证 ────────────────────────────────────────
A5 = """  // 已经登录就不必再扫
  let already = (await ctx.cookies()).some((c) => c.name === 'wr_skey');
"""
assert t.count(A5) == 1, ("A5", t.count(A5))
N5 = """  // ★【2026-10-16 修】原写法：`let already = (await ctx.cookies()).some(c => c.name === 'wr_skey')`
  //   —— 只看**名字在不在**，不看有没有效 ⇒ 旧 cookie 还在时永远误判"已登录"、
  //   跳过扫码 ⇒ **一键重登永远走不到扫码那一步**（用户实跑踩到）。
  //   ⇒ 改为**真验证**：拿 cookie 打 /web/shelf/sync，按 errCode 定登录态。
  let already = false;
  if (FORCE) {
    console.log('\\n[0] --force：跳过登录态校验，强制走扫码流程');
  } else {
    const ckHdr = (await ctx.cookies()).map((c) => `${c.name}=${c.value}`).join('; ');
    const chk = await checkSession(ckHdr);
    already = chk.ok;
    console.log(`\\n[0] 登录态真验证（/web/shelf/sync）：${chk.ok ? '有效' : '**无效**'}`
      + `  errCode=${chk.code}  ${chk.msg || ''}`);
    if (!chk.ok) console.log('    ⇒ 需要重新扫码登录');
  }
"""
t = t.replace(A5, N5, 1)
report.append("  ✓ E4 登录判据改为真验证")

# ── E5 验证段：同时打 articles 与 cover ─────────────────────────
A6 = "console.log('\\n' + '='.repeat(74));\nconsole.log(allOk\n"
assert t.count(A6) == 1, ("A6", t.count(A6))
N6 = """// ── [3b] 疑似替代接口 /web/mp/cover（每号 1 个请求）──────────────
console.log('\\n' + '='.repeat(74));
console.log('[3b] 探测 /web/mp/cover（疑似替代 /web/mp/articles 的轻量增量接口）');
console.log('     一次只返回该号**最新一篇**；若可用，就能把"整表翻页"换成"每号 1 个请求"');
console.log('='.repeat(74));
for (const t of TARGETS) {
  const { status, body, text } = await fetchCover(cookieHeader, t.bookId);
  const code = body ? (body.errCode ?? body.errcode) : 'nonJSON';
  console.log(`\\n── ${t.name}  (${t.bookId})`);
  console.log(`   HTTP ${status}   errCode=${code === undefined ? '(无)' : code}`);
  if (body && typeof body === 'object' && (code === undefined || code === null || code === 0)) {
    const rid = body.reviewId || (body.review && body.review.reviewId) || '';
    const token = String(rid).includes('_') ? String(rid).split('_').pop() : String(rid);
    console.log(`   ★ 最新一篇: ${String(body.title || body.review?.title || '').slice(0, 46)}`);
    console.log(`      reviewId=${String(rid).slice(0, 50)}`);
    if (token && token.length > 10) console.log(`      原文直链 https://mp.weixin.qq.com/s/${token}`);
    console.log(`      回包: ${JSON.stringify(body).slice(0, 260)}`);
  } else if (code !== undefined && CODE_MEANING[String(code)]) {
    console.log(`   ✘ ${CODE_MEANING[String(code)]}`);
  } else {
    console.log(`   ⚠ 回包: ${String(text).slice(0, 200)}`);
  }
}

console.log('\\n' + '='.repeat(74));
console.log('错误码释义（照抄服务端 errMsg + 外部实测）：');
for (const [k, v] of Object.entries(CODE_MEANING)) console.log(`   ${k}  ${v}`);
console.log('='.repeat(74));
console.log(allOk
"""
t = t.replace(A6, N6, 1)
report.append("  ✓ E5 验证段新增 cover 探测 + 错误码释义")

out = t.encode("utf-8")
if WRITE:
    P.write_bytes(out)
print("=== %s ===" % ("落盘" if WRITE else "空跑"))
print("\n".join(report))
b = P.read_bytes()
print("  %d → %d 字节（写盘后实为 %d）；EOL 纯 LF=%s"
      % (len(raw), len(out), len(b), b.count(b"\r\n") == 0))
