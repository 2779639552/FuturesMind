"""LangGraph checkpoint support for resumable analysis runs.

Per-ticker SQLite databases so concurrent tickers don't contend.
"""

from __future__ import annotations  # 【调用包】前向引用类型标注支持

import hashlib  # 【调用包】thread_id 的 SHA-256 哈希生成
import sqlite3  # 【调用包】SQLite 连接 (断点数据库读写)
from collections.abc import Generator  # 【调用包】contextmanager 返回类型标注
from contextlib import contextmanager  # 【调用包】上下文管理器装饰器
from pathlib import Path  # 【调用包】路径对象操作

from langgraph.checkpoint.sqlite import SqliteSaver  # 【调用包】LangGraph 的 SQLite 断点持久化

from tradingagents.dataflows.utils import safe_ticker_component  # 【调用包】ticker 路径安全清洗


# 【功能】计算某 ticker 对应的 SQLite 断点数据库路径, 并确保 checkpoints 目录存在。
# 【参数】data_dir: 数据缓存根目录; ticker: 股票代码。
# 【返回】Path: <data_dir>/checkpoints/<大写安全ticker>.db。
# 【关键】ticker 先经 safe_ticker_component() 清洗, 防止路径逃逸; 每 ticker 独立
#     一个库, 避免并发 ticker 相互争抢同一数据库。
def _db_path(data_dir: str | Path, ticker: str) -> Path:
    """Return the SQLite checkpoint DB path for a ticker."""
    # Reject ticker values that would escape the checkpoints directory.
    safe = safe_ticker_component(ticker).upper()  # 【变量】清洗后的大写 ticker (作为文件名)
    p = Path(data_dir) / "checkpoints"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{safe}.db"


# 【功能】为 ticker+date (+可选签名) 生成确定性线程 ID。
# 【参数】
#     ticker: 股票代码; date: 分析日期; signature: 图形状签名 (影响图结构的运行参数)。
# 【返回】SHA-256 十六进制摘要的前 16 位。
# 【关键】签名把"会改变图结构的运行参数"折进 ID: 换分析师组合/轮数/资产类型后 ID
#     随之变化, 续跑时会被当作全新运行, 避免复用旧断点 (issue #1089); 省略签名则
#     保持旧版 ID 兼容。
def thread_id(ticker: str, date: str, signature: str = "") -> str:
    """Deterministic thread ID for a ticker+date pair.

    ``signature`` folds in graph-shape-affecting run choices so a resume under a
    different graph can't reuse this checkpoint (#1089); omitting it keeps the
    legacy ID.
    """
    base = f"{ticker.upper()}:{date}"
    if signature:
        base = f"{base}:{signature}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]


# 【功能】以上下文管理器形式提供 SqliteSaver 断点持久化对象。
# 【参数】data_dir: 数据缓存根目录; ticker: 股票代码。
# 【返回】yield SqliteSaver (已 setup); 退出上下文时自动关闭 SQLite 连接。
# 【关键】check_same_thread=False 允许跨线程使用同一连接, 配合图运行时多线程调用。
@contextmanager
def get_checkpointer(data_dir: str | Path, ticker: str) -> Generator[SqliteSaver, None, None]:
    """Context manager yielding a SqliteSaver backed by a per-ticker DB."""
    db = _db_path(data_dir, ticker)
    conn = sqlite3.connect(str(db), check_same_thread=False)  # 【调用函数】SQLite 连接 (跨线程可用)
    try:
        saver = SqliteSaver(conn)  # 【调用函数】以 SQLite 连接构造 LangGraph 断点持久化器
        saver.setup()  # 【调用函数】建表/初始化断点存储
        yield saver
    finally:
        conn.close()


# 【功能】判断某 ticker+date 是否存在可续跑的断点。
def has_checkpoint(data_dir: str | Path, ticker: str, date: str, signature: str = "") -> bool:
    """Check whether a resumable checkpoint exists for ticker+date."""
    return checkpoint_step(data_dir, ticker, date, signature) is not None  # 【调用函数】复用 checkpoint_step 判定


# 【功能】查询某 ticker+date 最近断点对应的步骤号。
# 【参数】data_dir/ticker/date/signature: 含义同 thread_id。
# 【返回】int 步骤号; 无断点/库不存在则 None。
# 【关键】按 thread_id 取状态快照后读取 metadata["step"]; 该字段由 trading_graph
#     在每个节点写入, 用于续跑时定位"上次成功跑到哪一步"。
def checkpoint_step(
    data_dir: str | Path, ticker: str, date: str, signature: str = ""
) -> int | None:
    """Return the step number of the latest checkpoint, or None if none exists."""
    db = _db_path(data_dir, ticker)
    if not db.exists():
        return None
    tid = thread_id(ticker, date, signature)
    with get_checkpointer(data_dir, ticker) as saver:
        config = {"configurable": {"thread_id": tid}}  # 【变量】按 thread_id 定位断点线程
        cp = saver.get_tuple(config)  # 【调用函数】LangGraph 取该线程最近一次状态快照
        if cp is None:
            return None
        return cp.metadata.get("step")


# 【功能】清空所有 ticker 的断点数据库文件 (整个 checkpoints 目录)。
# 【返回】删除的文件数量。
def clear_all_checkpoints(data_dir: str | Path) -> int:
    """Remove all checkpoint DBs. Returns number of files deleted."""
    cp_dir = Path(data_dir) / "checkpoints"
    if not cp_dir.exists():
        return 0
    dbs = list(cp_dir.glob("*.db"))
    for db in dbs:
        db.unlink()  # 【调用函数】删除单个断点库文件
    return len(dbs)


# 【功能】按 ticker+date+signature 删除对应断点的数据行 (只删该线程, 不删整个库)。
# 【参数】data_dir/ticker/date/signature: 含义同 thread_id。
# 【关键】分别 DELETE writes 与 checkpoints 两表 (LangGraph 的断点数据分存两表);
#     表不存在时捕获 OperationalError 静默忽略, 保证清理行为幂等。
def clear_checkpoint(data_dir: str | Path, ticker: str, date: str, signature: str = "") -> None:
    """Remove checkpoint for a specific ticker+date by deleting the thread's rows."""
    db = _db_path(data_dir, ticker)
    if not db.exists():
        return
    tid = thread_id(ticker, date, signature)
    conn = sqlite3.connect(str(db))  # 【调用函数】打开断点库做删除
    try:
        for table in ("writes", "checkpoints"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (tid,))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
