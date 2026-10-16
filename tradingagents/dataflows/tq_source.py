"""tq_source.py — 天勤(TqSdk / 信易科技)行情数据客户端(日K / 分钟K / 实时行情)

【模块角色】
  天勤 TqSdk 是专业期货行情源(www.shinnytech.com),相对免费 AKShare 的增强点:
    - **分钟 K 历史深度**:新浪免费源每个周期固定约 1023 根(1分≈4天/5分≈15天/60分≈8个月);
      天勤按 data_length 取,可要数千根,覆盖显著更长;
    - **官方主力连续**:天勤主连(KQ.m@交易所.品种)有官方换月规则,不依赖 8% 缺口启发式;
    - **字段完整**:成交量/持仓量(开收)/结算价/涨跌停等一次给全;
    - **实时推送**:WebSocket 推送(本模块按需短连接拉取最新一帧,不做常驻订阅)。

【可逆开关(照 gtja_api.py 同范式)】
  未配置账号 → configured()=False → 所有 fetch_* 直接返回 None → 调用方走原 AKShare 链路,
  **行为与接入前完全一致**。账号只从环境变量读取(.env,不 commit、不硬编码):
      TQ_USER / TQ_PASS        快期(信易)账户 —— **必需**
      TQ_ALLOW_ANON=1          兼容位:允许"未配账号也尝试启用"(见下方实测说明)
      TQ_TIMEOUT               单次取数超时秒数(默认 20)

  【实测事实(2026-10-12,tqsdk 3.10.2)】tqsdk 3.x **已不再支持匿名模式**,即使
  设了 TQ_ALLOW_ANON,不配账号调用 TqApi() 也会抛
  "请输入 auth(快期账户)参数…"。该异常被本模块捕获 → 返回 None → 调用方降级
  AKShare(不会静默出错,last_error() 可查原因)。因此:**要真正启用天勤,必须填
  TQ_USER/TQ_PASS**;TQ_ALLOW_ANON 仅作兼容位保留(旧版本/未来若恢复匿名时可用)。

【为什么是"短连接"】
  TqApi 内部跑 asyncio 事件循环,且不能跨线程共享一个实例。Web 服务是多线程的,常驻连接
  会带来"跨线程用同一 api"的崩溃风险与断线重连状态管理负担。因此本模块每次取数
  **新建 TqApi → 取完立即 close()**(用 with 语句保证异常路径也关闭),简单、线程安全、
  不残留后台连接。代价是每次约 1~3 秒建连开销 —— 由上层缓存(行情 60s / K线 5min)摊薄。

【本模块不做的事】
  - 不做交易(只用行情);不下单、不查账户、不碰资金;
  - 不写库、不落盘(纯取数返回列表,由上层决定如何消费);
  - 不常驻订阅、不占后台线程。
"""

from __future__ import annotations

import logging
import os
import threading  # 【调用包】取数加锁(串行化 TqApi 创建,避免并发建连被限流)
import time  # 【调用包】wait_update 的绝对超时时刻(deadline)
from datetime import datetime  # 【调用包】时间戳格式化

logger = logging.getLogger(__name__)

# 【变量】单次取数超时(秒):TqApi 建连 + 等数据的总预算
TQ_TIMEOUT = float(os.getenv("TQ_TIMEOUT") or 20)
# 【变量】K 线单次最多取多少根(防止误传超大值把内存打满)
MAX_BARS = 5000
# 【变量】建连串行锁:多个请求同时新建 TqApi 会被服务端限流/排队,串行化更稳
_connect_lock = threading.Lock()
# 【变量】最近一次错误(供诊断/前端"数据源边界"卡显示原因,不抛给调用方)
_last_error: str | None = None

# ── 失败熔断(2026-10-14) ────────────────────────────────────────────────
# 【为什么】天勤不可用时,每次取数都要抢下面的 `_connect_lock`(全局串行)并等
#   ~6s 的 SSLError 重试;而调用方常按「品种 × 周期」批量取数(实测技术面总览
#   为 21 × 4 = 84 次) ⇒ 串行 84 × 6s ≈ 8 分钟**持续占锁**,把同一把锁后面的
#   交互式请求(分钟 K 线自动刷新)排到 35~50s 才返回 —— 而前端刷新周期仅 30s,
#   于是「请求还没返回就过期」,用户看到的是「K 线不更新、还是十分钟前的数据」。
# 【做法】连接类失败连续达阈值 ⇒ 冷却期内**直接返回 None,不抢锁不重试**,
#   让调用方立刻走 AKShare 回落;冷却结束放行一次"试探"(半开),失败则再冷却。
# 【为什么只记建连失败】当前故障发生在建连(auth token)阶段;把"某品种无数据"
#   之类的业务级空结果也计入,会让单个品种的问题误伤全站。
_FAIL_STREAK = 0            # 【变量】连续建连失败次数
_COOLDOWN_UNTIL = 0.0       # 【变量】冷却截止时间戳(<=now 表示可放行试探)
_FAIL_LOCK = threading.Lock()  # 【变量】熔断状态锁
# 阈值/冷却期可用环境变量覆盖(测试用极小值,避免真的等 2 分钟)
_FAIL_THRESHOLD = int(os.getenv("TQ_FAIL_THRESHOLD") or 2)
_COOLDOWN_SECONDS = float(os.getenv("TQ_COOLDOWN") or 120)


def _circuit_open() -> bool:
    """熔断是否生效(冷却期内)。生效时调用方应**立刻**回落其它数据源。"""
    with _FAIL_LOCK:
        return time.time() < _COOLDOWN_UNTIL


def _record_fail(exc: BaseException | str) -> None:
    """记一次建连失败;连续达阈值即开启冷却。"""
    global _FAIL_STREAK, _COOLDOWN_UNTIL
    with _FAIL_LOCK:
        _FAIL_STREAK += 1
        if _FAIL_STREAK >= _FAIL_THRESHOLD:
            _COOLDOWN_UNTIL = time.time() + _COOLDOWN_SECONDS


def _record_ok() -> None:
    """记一次建连成功 ⇒ 清零计数、解除熔断。"""
    global _FAIL_STREAK, _COOLDOWN_UNTIL
    with _FAIL_LOCK:
        _FAIL_STREAK = 0
        _COOLDOWN_UNTIL = 0.0


def circuit_state() -> dict:
    """熔断状态快照(供诊断接口 / 日志 / 测试断言,不改变状态)。"""
    with _FAIL_LOCK:
        return {
            "open": time.time() < _COOLDOWN_UNTIL,
            "streak": _FAIL_STREAK,
            "threshold": _FAIL_THRESHOLD,
            "cooldown_seconds": _COOLDOWN_SECONDS,
            "cooldown_remaining": max(0.0, _COOLDOWN_UNTIL - time.time()),
        }


def reset_circuit() -> None:
    """手动复位熔断(测试用;也让运维能在修好网络后立即恢复,不必等冷却)。"""
    global _FAIL_STREAK, _COOLDOWN_UNTIL
    with _FAIL_LOCK:
        _FAIL_STREAK = 0
        _COOLDOWN_UNTIL = 0.0

# ── 交易所代码映射:项目内部 → 天勤 ─────────────────────────────────────
# 项目 VARIETY_METADATA 的 exchange 用 ZCE 指郑商所,天勤用 CZCE;其余一致。
_EXCHANGE_MAP = {
    "ZCE": "CZCE",   # 郑商所(重点差异)
    "SHFE": "SHFE",  # 上期所
    "DCE": "DCE",    # 大商所
    "GFEX": "GFEX",  # 广期所
    "INE": "INE",    # 上海国际能源交易中心(SC原油/LU低硫燃油/NR20号胶)
    "CFFEX": "CFFEX",  # 中金所
}

# 【变量】品种代码 → 天勤合约符号段(兜底补丁:项目元数据里 inv_code 为空的品种)
#   主力连续统一走 KQ.m@交易所.品种,天勤自动解析主力,无需自己判换月。
_SYMBOL_PATCH = {
    "BZ": "bz",   # 纯苯(项目元数据 inv_code 缺失)
    "PS": "ps",   # 多晶硅(项目元数据 inv_code 缺失)
}


def configured() -> bool:
    """本数据源是否可用(可逆开关)。

    【返回】True = 已配账号 或 显式允许匿名;False = 未配置 → 调用方应走原 AKShare 链路。
    【为什么这样设计】与 gtja_api.configured() 同语义:未配置时行为与"接入前"完全一致,
                      新人/新机器拉下代码直接能跑,不需要先有账号。
    """
    if (os.getenv("TQ_USER") or "").strip() and (os.getenv("TQ_PASS") or "").strip():
        return True
    return (os.getenv("TQ_ALLOW_ANON") or "").strip() in ("1", "true", "True", "yes")


def last_error() -> str | None:
    """最近一次取数失败原因(None = 无错误);供诊断与前端展示,不抛异常。"""
    return _last_error


# 【变量】线程局部:记录"本线程最近一次取数实际用的源"(供前端标注)。
#   用 threading.local 而非模块级变量:Flask 每请求一线程,并发请求的来源不会互相串。
_tls = threading.local()


def mark_source(src: str) -> None:
    """记录本线程最近一次取数实际使用的数据源('tq' | 'akshare' | 'tq+akshare')。

    【为什么需要】前端要如实标注"这张 K 线图的数据来自哪个源",否则用户无法判断
              天勤是否真的生效。由取数方在成功返回前调用,读取方用 last_source()。
    """
    _tls.source = src


def last_source() -> str:
    """本线程最近一次取数的数据源;未记录返回 'unknown'。"""
    return getattr(_tls, "source", "unknown")


def _set_error(msg: str | None) -> None:
    """记录最近一次错误(模块级,供 last_error() 读)。"""
    global _last_error
    _last_error = msg


def to_tq_symbol(code: str) -> str | None:
    """项目品种代码 → 天勤**主力连续**合约符号(如 'RB' → 'KQ.m@SHFE.rb')。

    【返回】天勤符号;品种不在项目元数据里则返回 None(调用方降级)。
    【关键逻辑】交易所取 VARIETY_METADATA[code]['exchange'] 并做 ZCE→CZCE 归一;
              品种段优先用元数据的 inv_code,缺失时查 _SYMBOL_PATCH。
    【为什么用 KQ.m 主连】天勤主连有官方换月规则(按持仓量/成交量切换),比自己按缺口
                      判换月可靠;且省去"主力合约代码是哪个月份"的维护。
    """
    code = (code or "").strip().upper()
    if not code:
        return None
    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

        meta = VARIETY_METADATA.get(code) or {}
    except Exception:  # 【异常】元数据读不到时用内置补丁兜底
        meta = {}
    ex = _EXCHANGE_MAP.get((meta.get("exchange") or "").strip().upper())
    if not ex:
        return None
    sym = (meta.get("inv_code") or "").strip() or _SYMBOL_PATCH.get(code, "")
    if not sym:
        return None
    return f"KQ.m@{ex}.{sym}"


# ── 第三方库对 stdlib logging 的全局劫持：导入前快照 / 导入后还原 ──────────
# 【★ 为什么需要】tqsdk 依赖的 shinny_structlog 在 **import 时无条件**重写 logging：
#     logging.setLoggerClass(ShinnyLogger)
#     logging.Logger.root    = ShinnyLogger(WARNING)
#     logging.Logger.manager = logging.Manager(logging.Logger.root)   # ← 注册表被换成新的空表
#     logging.getLogger      = getLogger                              # ← 连取用入口都替换
#   第 3 行是本项目的**真问题**：Logger.manager 一换，注册表 dict 就是一张新表，于是
#   **此前所有模块级 `logger = logging.getLogger(__name__)` 全部孤儿化** ——
#   同名再 getLogger 拿到的是**另一个对象**。后果：
#     · 本项目各处 logger 与新 logger 脱钩 ⇒ 日志串台/按名字配置的 handler 失效；
#     · 按名字取 logger 的断言（unittest 的 assertLogs）收不到任何记录 ⇒ 假抖动。
#   实测（2026-10-13）：tests/test_rag_ask_route.py 一调 /api/research/ask 就触发
#   （该路由链路懒导入到 tqsdk），此后 tests/test_vendor_routing.py 的 assertLogs 用例
#   **单跑全绿、跟在全量里必红**，报 "no logs of level WARNING or higher triggered"。
# 【做法】在导入 tqsdk 前后做快照/还原。宿主的日志基础设施不该被第三方库改写。
def _run_isolated_from_logging_hijack(fn):
    """执行 fn()，并确保其内部（首次导入 tqsdk）不会永久改写 stdlib logging 的全局状态。"""
    import logging as _lg

    snap = (_lg.getLoggerClass(), _lg.Logger.root, _lg.Logger.manager, _lg.getLogger)
    try:
        return fn()
    finally:
        cls, root, mgr, get_logger = snap
        if _lg.getLoggerClass() is not cls:
            _lg.setLoggerClass(cls)
        if _lg.Logger.root is not root:
            _lg.Logger.root = root
        if _lg.Logger.manager is not mgr:
            _lg.Logger.manager = mgr
        if _lg.getLogger is not get_logger:
            _lg.getLogger = get_logger


# ── 让 stdlib Logger 容忍第三方库的"日志方言"多余字段 ─────────────────────
# 【★ 为什么必须有】上面的快照/还原保护了宿主日志不被改写，但**副作用**是：
#   tqsdk 内部随后**新建**的 logger 变回 stdlib `Logger`，而 tqsdk 记录日志时会带
#   structlog 方言字段（`logger.info(..., product=xxx)`）—— stdlib 的
#   `Logger._log()` 签名里没有 `product` ⇒ 抛 TypeError。
#   实测（2026-10-14）：一个进程里**只有第一次天勤取数成功**，第 2 次起每次都在
#   日志层炸掉；更糟的是**失败时 `last_source()` 仍报 'tq'**（返回 0 根却标"天勤"）
#   ⇒ 调用方无从判断这次到底有没有真数据（静默失败）。
#   `_kline_minute` 是天勤优先，于是**分钟K 从第 2 次起全空**，
#   依赖多个周期的功能（多周期共振）会直接失效。
# 【为什么改 _log 而不是改 getLoggerClass】把 logger 类设回 ShinnyLogger 会重新
#   引入"宿主 logger 被孤儿化"的老问题；只让 `_log` 丢弃未知 kwargs 是**最小改动**：
#   宿主 logger 与 tqsdk logger 都能正常工作，且不改变任何业务语义。
# 【为什么幂等】模块可能被重复导入/重入，用哨兵属性防重复包装（重复包装虽无害，
#   但会让调用栈叠层、排障时难以辨认）。
def _install_kwarg_tolerant_logger() -> None:
    """让 `logging.Logger._log` 丢弃未知关键字参数（幂等）。"""
    import logging as _lg

    if getattr(_lg.Logger, "_agentsense_tolerant_log", False):
        return
    _orig_log = _lg.Logger._log

    def _tolerant_log(self, level, msg, args, exc_info=None, extra=None,
                      stack_info=False, stacklevel=1, **unknown):
        # `product=` 之类的 structlog 方言字段在这里被**安静丢弃** ——
        # 它们只是日志的附加维度，丢掉不影响业务，也不该让取数失败。
        return _orig_log(self, level, msg, args, exc_info, extra, stack_info, stacklevel)

    _lg.Logger._log = _tolerant_log
    _lg.Logger._agentsense_tolerant_log = True


_install_kwarg_tolerant_logger()


# ── 短连接会话 ─────────────────────────────────────────────────────────

class _Session:
    """TqApi 短连接会话(上下文管理器):进入建连、退出必关。

    【用法】
        with _Session() as api:
            ... api.get_kline_serial(...) ...
    【关键逻辑】建连串行化(_connect_lock):并发新建 TqApi 会被服务端排队/限流;
              退出时无论正常/异常都 close(),不留后台连接。
    """

    def __init__(self) -> None:
        self.api = None

    def __enter__(self):
        def _connect():
            # 【调用包】懒导入(未装 tqsdk 时不影响其他链路)。
            # ⚠️ 首次导入会同带进 shinny_structlog —— 它会重写 stdlib logging 的全局状态,
            #    必须包在 _run_isolated_from_logging_hijack 里(原因见上方长注释)。
            from tqsdk import TqApi, TqAuth

            user = (os.getenv("TQ_USER") or "").strip()
            pwd = (os.getenv("TQ_PASS") or "").strip()
            with _connect_lock:
                if user and pwd:
                    return TqApi(auth=TqAuth(user, pwd))
                # 兼容位:tqsdk 3.x 实测会抛"请输入 auth"(被上层捕获后降级 AKShare)
                return TqApi()

        try:
            self.api = _run_isolated_from_logging_hijack(_connect)
        except Exception as exc:  # 【异常】建连失败 = 数据源级故障 ⇒ 计入熔断后原样上抛
            _record_fail(exc)
            raise
        _record_ok()  # 建连成功 ⇒ 数据源健康,清零失败计数
        return self.api

    def __exit__(self, exc_type, exc, tb):
        if self.api is not None:
            try:
                self.api.close()
            except Exception:  # 【异常】关闭失败仅记日志,不掩盖原始异常
                logger.debug("TqApi close 失败(忽略)", exc_info=True)
            self.api = None
        return False  # 不吞异常


# ── 取数: 日K ──────────────────────────────────────────────────────────

def _ns_to_str(ns) -> str:
    """天勤纳秒时间戳 → 'YYYY-MM-DD HH:MM:SS'(解析失败返回原始字符串)。"""
    try:
        return datetime.fromtimestamp(int(ns) / 1e9).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(ns)


def _nan_to_none(v):
    """NaN → None(天勤未收盘 bar 的字段可能是 nan,直接给上层会污染计算)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN != NaN


def _drain_updates(api, rounds: int = 3, per_round_s: float = 0.8) -> None:
    """推进 TqApi 事件循环若干轮,让**当前未完成的 bar** 吃到最新推送。

    【为什么需要(2026-10-13)】原先只调一次 `wait_update()`,它只保证"数据已填充"——
      短连接场景下首轮拿到的最后一根 K 线可能是**上一次推送的快照**,其 close 落后
      真实最新价若干秒(用户观感:"K 线能做到分钟级但最后一根不够跳")。
      多推几轮把未完成 bar 刷新到最近一次 tick。
    【为什么是有界的】每轮带 deadline 超时,收到任何更新会立即返回 ⇒ 有行情时几乎不额外耗时;
      无行情(休市/冷门品种)时最多约 rounds×per_round_s 秒后放弃,不会把请求挂死。
    【异常处理】TqTimeoutError 等一律 break —— 加固是"锦上添花",绝不能让它把已取到的数据弄丢。
    """
    for _ in range(max(1, int(rounds))):
        try:
            api.wait_update(deadline=time.time() + per_round_s)
        except Exception:  # noqa: BLE001  超时/断连即停,保留已填充的数据
            break


def fetch_daily_bars(code: str, length: int = 300) -> list[dict] | None:
    """天勤取**日 K**(主力连续)。

    【参数】code: 项目品种代码(如 'RB');length: 要多少根(默认 300)。
    【返回】[{date:'YYYY-MM-DD', open, high, low, close, volume, open_interest}, ...] 升序;
            未启用/取数失败 → None(调用方降级 AKShare)。
    【关键逻辑】duration=60*60*24 取日线;最后一根为**当日未收盘** bar,一并返回
              (实时行情需要它),由上层按需处理。
    """
    if not configured():
        return None
    # 【失败熔断 2026-10-14】冷却期内**直接返回**,不抢 _connect_lock 也不重试 ——
    # 否则批量取数(如总览 21×4=84 次)会串行占锁数分钟,把交互式 K 线请求拖到几十秒。
    if _circuit_open():
        _set_error("天勤连接熔断中(近期连续失败) → 本轮直接回落其它数据源")
        return None
    sym = to_tq_symbol(code)
    if not sym:
        _set_error(f"{code}: 无天勤合约映射")
        return None
    length = max(1, min(int(length or 300), MAX_BARS))
    try:
        with _Session() as api:
            klines = api.get_kline_serial(sym, 60 * 60 * 24, data_length=length)
            _drain_updates(api)  # 【2026-10-13】多轮推进,让"当日未收盘 bar"跟上最新 tick
            rows = []
            for i in range(len(klines)):
                k = klines.iloc[i]
                rows.append({
                    "date": _ns_to_str(k["datetime"])[:10],
                    "open": _nan_to_none(k["open"]),
                    "high": _nan_to_none(k["high"]),
                    "low": _nan_to_none(k["low"]),
                    "close": _nan_to_none(k["close"]),
                    "volume": _nan_to_none(k["volume"]),
                    "open_interest": _nan_to_none(k.get("close_oi")),
                })
            rows = [r for r in rows if r["close"] is not None]
            _set_error(None)
            mark_source("tq")
            logger.info("天勤日K %s(%s) %d 根", code, sym, len(rows))
            return rows
    except Exception as e:  # 【异常】任何失败都降级,不抛出
        _set_error(f"{code}: {type(e).__name__}: {e}")
        logger.warning("天勤日K取数失败 %s(%s): %s", code, sym, e)
        return None


# ── 取数: 分钟K ────────────────────────────────────────────────────────

# 【变量】分钟周期 → 天勤 duration 秒数
_MINUTE_SECONDS = {"1": 60, "3": 180, "5": 300, "15": 900, "30": 1800, "60": 3600}


def fetch_minute_bars(code: str, period: str = "5", length: int = 1000) -> list[dict] | None:
    """天勤取**分钟 K**(主力连续,不复权)。

    【参数】code: 品种代码;period: '1'/'3'/'5'/'15'/'30'/'60'(分钟);length: 根数。
    【返回】[{date:'YYYY-MM-DD HH:MM', open, high, low, close, volume}, ...] 升序;
            未启用/周期不支持/取数失败 → None(调用方降级 AKShare 新浪)。
    【相对 AKShare 的价值】新浪每周期固定约 1023 根封顶;天勤可按 length 取更多,
                        分钟级回测/形态分析的可用历史显著更长。
    """
    if not configured():
        return None
    # 【失败熔断 2026-10-14】冷却期内**直接返回**,不抢 _connect_lock 也不重试 ——
    # 否则批量取数(如总览 21×4=84 次)会串行占锁数分钟,把交互式 K 线请求拖到几十秒。
    if _circuit_open():
        _set_error("天勤连接熔断中(近期连续失败) → 本轮直接回落其它数据源")
        return None
    secs = _MINUTE_SECONDS.get(str(period))
    if not secs:
        _set_error(f"不支持的天勤分钟周期: {period}")
        return None
    sym = to_tq_symbol(code)
    if not sym:
        _set_error(f"{code}: 无天勤合约映射")
        return None
    length = max(1, min(int(length or 1000), MAX_BARS))
    try:
        with _Session() as api:
            klines = api.get_kline_serial(sym, secs, data_length=length)
            _drain_updates(api)  # 【2026-10-13】多轮推进,让"当前未完成 bar"跟上最新 tick
            rows = []
            for i in range(len(klines)):
                k = klines.iloc[i]
                rows.append({
                    "date": _ns_to_str(k["datetime"])[:16],
                    "open": _nan_to_none(k["open"]),
                    "high": _nan_to_none(k["high"]),
                    "low": _nan_to_none(k["low"]),
                    "close": _nan_to_none(k["close"]),
                    "volume": _nan_to_none(k["volume"]),
                })
            rows = [r for r in rows if r["close"] is not None]
            _set_error(None)
            mark_source("tq")
            logger.info("天勤分钟K %s(%s) %s分 %d 根", code, sym, period, len(rows))
            return rows
    except Exception as e:
        _set_error(f"{code}: {type(e).__name__}: {e}")
        logger.warning("天勤分钟K取数失败 %s(%s): %s", code, sym, e)
        return None


# ── 取数: 实时行情 ─────────────────────────────────────────────────────

def fetch_realtime(codes: list[str]) -> dict | None:
    """天勤取**实时行情**(一次建连批量取多品种,避免逐品种重复建连)。

    【参数】codes: 品种代码列表。
    【返回】{code: {price, change_pct, volume, open_interest, name, timestamp}};
            未启用/全部失败 → None(调用方降级 AKShare)。
    【关键逻辑】(1) **一次** TqApi 会话内批量 get_quote 所有品种(建连开销只付一次);
              (2) change_pct 由 最新价 vs 昨结算 算(天勤 quote 直接给 pre_settlement);
              (3) 单个品种取不到只跳过该品种,不影响其余。
    """
    if not configured():
        return None
    # 【失败熔断 2026-10-14】冷却期内**直接返回**,不抢 _connect_lock 也不重试 ——
    # 否则批量取数(如总览 21×4=84 次)会串行占锁数分钟,把交互式 K 线请求拖到几十秒。
    if _circuit_open():
        _set_error("天勤连接熔断中(近期连续失败) → 本轮直接回落其它数据源")
        return None
    symbols = {}
    for c in (codes or []):
        s = to_tq_symbol(c)
        if s:
            symbols[c] = s
    if not symbols:
        _set_error("实时行情: 无可映射品种")
        return None
    try:
        out: dict[str, dict] = {}
        with _Session() as api:
            quotes = {c: api.get_quote(s) for c, s in symbols.items()}
            _drain_updates(api, rounds=2, per_round_s=0.6)  # 【2026-10-13】多轮推进,确保取到最近一帧报价
            for c, q in quotes.items():
                last = _nan_to_none(getattr(q, "last_price", None))
                if last is None:
                    continue
                pre_set = _nan_to_none(getattr(q, "pre_settlement", None))
                chg = ((last - pre_set) / pre_set * 100) if (pre_set not in (None, 0)) else 0.0
                out[c] = {
                    "price": last,
                    "change_pct": round(chg, 4),
                    "volume": _nan_to_none(getattr(q, "volume", None)) or 0,
                    "open_interest": _nan_to_none(getattr(q, "open_interest", None)),
                    "name": (symbols[c].split(".", 1)[-1].upper()),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
        if not out:
            _set_error("实时行情: 全部品种无数据")
            return None
        _set_error(None)
        mark_source("tq" if len(out) >= len(symbols) else "tq+akshare")
        logger.info("天勤实时行情 %d/%d 个品种", len(out), len(symbols))
        return out
    except Exception as e:
        _set_error(f"实时行情: {type(e).__name__}: {e}")
        logger.warning("天勤实时行情取数失败: %s", e)
        return None
