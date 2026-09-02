"""FuturesMind Database Layer — SQLite persistence for posts, sentiment, alerts, users.

Usage:
    from database import get_db
    db = get_db()
    db.insert_posts(posts)
    stats = db.get_collection_stats()
"""

# =============================================================================
# 【模块角色】
#   database.py 是整个项目的"数据持久化层"——所有需要"记住"的数据(采集到的
#   帖子、每日情感统计、告警、采集日志、用户账号、自选列表、交易信号)最终都
#   落进本地 SQLite 数据库 agentsense.db,由本模块统一读写。
#
#   SQLite 是单文件数据库,无需单独安装服务端;数据库文件默认存放在
#   ~/.tradingagents/agentsense.db。全项目其他模块(采集器、调度器、Web 前端、
#   情感分析、回测)都通过 get_db() 拿到同一个 AgentSenseDB 实例来操作数据。
#
#  本文件定义了一张"表清单"与对应操作函数,共 8 张表:
#     1. posts            采集到的帖子(微博/知乎原文、作者、情感、点赞等)
#     2. sentiment_daily  每个品种每日的情感统计汇总(多空比例、平均分等)
#     3. alerts           告警/消息中心(采集失败、管道事件、健康检查警告)
#     4. collection_log   每次采集任务的日志(平台、条数、成功/失败)
#     5. users            登录用户(用户名 + SHA256 密码哈希 + 是否管理员)
#     6. watchlist        用户自选品种清单(关注哪些品种的行情/情感)
#     7. trade_signals    交易信号及其回测结果(信号方向、入场/出场价、盈亏)
#     8. research_reports 研报上传与 LLM 提取结果(方向/置信度/结构化数据,运行分析高优先级数据源)
#
# 【工程实践】
#   - 线程局部单例(get_db):SQLite 连接不能跨线程共享,这里按线程各持一个实例。
#   - WAL 模式(journal_mode=WAL):允许多个连接并发读写,读写互不阻塞。
#   - 默认管理员:首次启动时自动创建 admin / agentsense2026(见 ensure_default_user)。
# =============================================================================

import hashlib  # 【调用包】SHA256 密码哈希(用户密码安全存储)
import json  # 【调用包】JSON 序列化(存 varieties/platform_breakdown/data 等复杂字段)
import os  # 【调用包】用户主目录定位(数据库默认路径)
import sqlite3  # 【调用包】SQLite 数据库连接与操作
import threading  # 【调用包】线程局部变量(每线程独立数据库连接)
from contextlib import contextmanager  # 【调用包】上下文管理器装饰器(实现 _conn 自动提交/回滚)
from datetime import datetime, timedelta  # 【调用包】日期时间计算(时间序列回溯)
from pathlib import Path  # 【调用包】跨平台路径对象(数据库路径/目录创建)

DB_DIR = Path(os.path.expanduser("~/.tradingagents"))  # 【变量】数据库所在目录(~/.tradingagents)
DB_PATH = DB_DIR / "agentsense.db"  # 【变量】SQLite 数据库文件完整路径

# ── Singleton connection with thread-local ──────────────────────────────

# 线程局部存储对象:每个线程访问 _local.db 时都会得到"只属于该线程"的属性副本。
# 因为 SQLite 连接对象默认不允许跨线程使用,用线程局部变量保证线程安全。
_local = threading.local()  # 【变量】线程局部存储:每个线程独立的 DB 实例槽位(保证线程安全)


def get_db() -> "AgentSenseDB":
    """获取当前线程的数据库实例(线程局部单例)。

    【功能】返回当前线程唯一的 AgentSenseDB 对象,供全项目统一读写数据库。
    【参数】无。
    【返回】AgentSenseDB:数据库操作对象(每线程只有一个实例)。
    【关键逻辑】利用 threading.local():第一次调用时创建实例并存入 _local.db,
               后续同一线程内的调用直接复用,避免重复建连与重复建表。
    """
    if not hasattr(_local, "db"):
        _local.db = AgentSenseDB()  # 【变量】_local.db:当前线程首次创建的 DB 实例(后续复用)
    return _local.db


class AgentSenseDB:
    """AgentSense 数据库封装类。

    【功能】集中封装对 SQLite 数据库的全部读写操作:建表、帖子写入、情感统计、
            告警、采集日志、用户认证、自选列表、交易信号、研报等。
    【关键逻辑】实例化时自动确保数据库目录存在并初始化 8 张表(_init_tables);
               每次数据库操作都通过 _conn() 上下文管理器获取新连接,用完即关。
    """

    def __init__(self, path=None):
        """初始化数据库连接路径并确保表结构存在。

        【功能】设定数据库文件路径,自动创建父目录,并初始化数据表。
        【参数】path: 数据库文件路径;为 None 时使用全局默认 DB_PATH
                     (~/.tradingagents/agentsense.db)。
        【返回】无。
        【关键逻辑】self.path 为实际连接使用的路径;Path 对象用于目录创建。
        """
        self.path = str(path or DB_PATH)  # 【变量】实际连接路径(未传参时用全局默认)
        self.path_obj = Path(self.path)
        self.path_obj.parent.mkdir(parents=True, exist_ok=True)  # 【调用函数】确保数据库父目录存在(不存在则递归创建)
        self._init_tables()
        self._migrate()

    def _migrate(self):
        """对旧库执行增量迁移(新列 ALTER TABLE),新库无需改动。

        【功能】CREATE TABLE IF NOT EXISTS 不会给已存在的表补新列;这里按需
                ALTER TABLE 补列,保证升级后旧库也能用上新字段。
        【关键逻辑】用 PRAGMA table_info 探测列是否存在,缺哪列补哪列;缺列
                判定已覆盖,重复调用安全(幂等)。
        """
        with self._conn() as c:
            cols = {row[1] for row in c.execute("PRAGMA table_info(research_reports)").fetchall()}
            if cols and "varieties" not in cols:
                c.execute("ALTER TABLE research_reports ADD COLUMN varieties TEXT DEFAULT ''")

    @contextmanager
    def _conn(self):
        """数据库连接上下文管理器(自动提交/回滚/关闭)。

        【功能】为 with 语句提供一条 SQLite 连接:正常结束时自动 commit,
                异常时自动 rollback,最后总是关闭连接释放资源。
        【参数】无(使用 self.path 连接数据库)。
        【返回】生成器,产出 sqlite3 连接对象 conn。
        【关键逻辑】
            - row_factory=sqlite3.Row:让查询结果支持按列名访问(如 row["id"])。
            - PRAGMA journal_mode=WAL:开启 WAL 日志模式,读写可并发,性能更好。
            - PRAGMA foreign_keys=ON:开启外键约束(本项目表间关联较弱,为健壮性保留)。
        """
        conn = sqlite3.connect(self.path)  # 【调用函数】建立 SQLite 连接(每次操作新建,用完即关)
        conn.row_factory = sqlite3.Row  # 【变量】row_factory:让结果行支持列名访问(row["id"])
        conn.execute("PRAGMA journal_mode=WAL")  # 【调用函数】开启 WAL 日志模式(支持并发读写)
        conn.execute("PRAGMA foreign_keys=ON")  # 【调用函数】开启外键约束(为健壮性保留)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        """创建 8 张数据表及索引(若不存在)。

        【功能】执行建表 SQL,确保数据库结构就绪;重复调用不会报错(IF NOT EXISTS)。
        【参数】无。
        【返回】无。
        【关键逻辑】executescript 一次性执行整段建表语句;每张表都尽量带上
                   常用查询索引以加速按平台/时间/情感/品种的检索。

        【8 张表用途速览】
        (1) posts —— 帖子主表:采集到的每一条社区帖子/评论。
            note_id 唯一,是去重依据;varieties 存该帖子涉及的品种 JSON 数组;
            sentiment / sentiment_score 存情感分析结果。
        (2) sentiment_daily —— 每日情感统计:每个品种每个自然日一行,
            汇总简单平均分、加权平均分、多/空/中性占比、帖子数与作者数等;
            (variety, date) 联合唯一,upsert 时按此冲突更新。
        (3) alerts —— 告警中心:采集失败、低数据量、超时、管道事件、健康检查
            等系统消息;acknowledged 标记是否已被用户确认。
        (4) collection_log —— 采集任务日志:每次运行 batch_collect.py 记一行,
            含平台、关键词数、采集/过滤后条数、状态(running/success/error)。
        (5) users —— 用户表:用户名唯一,password_hash 存 SHA256 哈希,
            is_admin 标记是否管理员。
        (6) watchlist —— 自选清单:user_id + variety 唯一,记录用户关注品种。
        (7) trade_signals —— 交易信号:情感/动量等策略产出的买卖信号,
            记录方向、入场/出场价、预测周期、实际盈亏 pnl_pct 与结果 outcome。
        (8) research_reports —— 研报:用户上传研报(文本/PDF/图片)后,LLM 提取
            结构化数据(方向/置信度/关键数据点)与观点结论;运行分析时作为
            高优先级数据源供基本面/宏观分析师消费。
        """
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT UNIQUE NOT NULL,
                    platform TEXT NOT NULL DEFAULT '?',
                    author_name TEXT DEFAULT '',
                    author_fans REAL DEFAULT 0,
                    title TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    sentiment TEXT DEFAULT 'neutral',
                    sentiment_score REAL DEFAULT 0,
                    publish_time TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    likes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    varieties TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
                CREATE INDEX IF NOT EXISTS idx_posts_publish_time ON posts(publish_time);
                CREATE INDEX IF NOT EXISTS idx_posts_sentiment ON posts(sentiment);

                CREATE TABLE IF NOT EXISTS sentiment_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    variety TEXT NOT NULL,
                    date TEXT NOT NULL,
                    simple_avg REAL DEFAULT 0,
                    avg_score REAL DEFAULT 0,
                    bullish_ratio REAL DEFAULT 0,
                    bearish_ratio REAL DEFAULT 0,
                    neutral_ratio REAL DEFAULT 0,
                    total_notes INTEGER DEFAULT 0,
                    author_count INTEGER DEFAULT 0,
                    platform_breakdown TEXT DEFAULT '{}',
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(variety, date)
                );
                CREATE INDEX IF NOT EXISTS idx_sent_variety_date ON sentiment_daily(variety, date);

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL DEFAULT 'info',
                    variety TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    message TEXT DEFAULT '',
                    severity TEXT DEFAULT 'info',
                    data TEXT DEFAULT '{}',
                    acknowledged INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

                CREATE TABLE IF NOT EXISTS collection_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    keywords_count INTEGER DEFAULT 0,
                    posts_collected INTEGER DEFAULT 0,
                    posts_after_filter INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    error_msg TEXT DEFAULT '',
                    started_at TEXT DEFAULT (datetime('now')),
                    finished_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER DEFAULT 1,
                    variety TEXT NOT NULL,
                    added_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, variety)
                );

                CREATE TABLE IF NOT EXISTS trade_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    variety TEXT NOT NULL,
                    signal_date TEXT NOT NULL,
                    signal_type TEXT NOT NULL DEFAULT 'author_weighted',
                    signal_value REAL DEFAULT 0,
                    direction TEXT DEFAULT 'hold',
                    entry_price REAL,
                    exit_price REAL,
                    horizon_days INTEGER DEFAULT 3,
                    pnl_pct REAL,
                    outcome TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_trade_variety ON trade_signals(variety);
                CREATE INDEX IF NOT EXISTS idx_trade_outcome ON trade_signals(outcome);

                CREATE TABLE IF NOT EXISTS research_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    variety TEXT NOT NULL,
                    varieties TEXT DEFAULT '',  -- LLM 识别出的全部品种(逗号分隔,如 "RB,CU"),用于多品种研报按品种过滤/拆分
                    title TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    filename TEXT DEFAULT '',
                    file_path TEXT DEFAULT '',
                    status TEXT DEFAULT 'processing',  -- processing / done / error
                    extracted_text TEXT DEFAULT '',
                    structured_data TEXT DEFAULT '',   -- LLM 提取的结构化数据 JSON
                    conclusion_md TEXT DEFAULT '',     -- LLM 观点分析结论(markdown)
                    direction TEXT DEFAULT '',         -- 看多 / 看空 / 中性
                    confidence REAL,
                    error TEXT DEFAULT '',
                    uploaded_at TEXT DEFAULT (datetime('now')),
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_research_variety ON research_reports(variety);
            """)

    # ── Posts ──────────────────────────────────────────────────────────

    def insert_posts_batch(self, posts: list[dict]) -> int:
        """批量插入帖子(重复 note_id 自动忽略)。

        【功能】把一批采集到的帖子写入 posts 表;已存在的帖子(note_id 相同)
                会被忽略,函数返回"真正新增"的条数。
        【参数】posts: 帖子字典列表,字段见 posts 表结构(如 note_id、platform、
                author_name、sentiment、varieties 等)。
        【返回】int: 本次成功插入的新帖子数量。
        【关键逻辑】
            - INSERT OR IGNORE + note_id 唯一索引实现天然去重。
            - author_name/title/publish_time 会被截断到合理长度,避免脏数据过长。
            - varieties 列表经 json.dumps 存成 JSON 字符串。
            - 单条失败仅跳过该条(except Exception: pass),不影响整批写入。

        Insert or ignore posts. Returns count of new posts inserted.
        """
        count = 0  # 【变量】count:本次真正新增的帖子条数(重复 note_id 不计)
        with self._conn() as c:
            for p in posts:
                try:
                    c.execute(
                        """
                        INSERT OR IGNORE INTO posts (note_id, platform, author_name, author_fans,
                            title, content, sentiment, sentiment_score, publish_time, url,
                            likes, comments, shares, varieties)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            p.get("note_id", ""),
                            p.get("platform", "?"),
                            (p.get("author_name", "") or "")[:50],
                            p.get("author_fans", 0) or 0,
                            (p.get("title", "") or "")[:200],
                            p.get("content", "") or "",
                            p.get("sentiment", "neutral"),
                            p.get("sentiment_score", 0) or 0,
                            (p.get("publish_time", "") or "")[:19],
                            p.get("url", "") or "",
                            p.get("like_count", 0) or 0,
                            p.get("comment_count", 0) or 0,
                            p.get("share_count", 0) or 0,
                            json.dumps(p.get("varieties", []), ensure_ascii=False),  # 【调用函数】把品种列表序列化为 JSON 文本存库
                        ),
                    )
                    if c.rowcount > 0:
                        count += 1
                except Exception:
                    pass
        return count

    def get_posts(
        self, platform=None, variety=None, sentiment=None, since=None, limit=200
    ) -> list[dict]:
        """按条件查询帖子列表。

        【功能】支持按平台、品种、情感、发布时间起点的多条件组合过滤查询。
        【参数】
            platform   : 平台名(如 "weibo"/"zhihu"),可选。
            variety    : 品种代码(如 "RB"),可选;用 LIKE '%"RB"%' 匹配 JSON。
            sentiment  : 情感值("positive"/"negative"/"neutral"),可选。
            since      : 起始时间字符串(>=),可选,如 "2026-01-01"。
            limit      : 最多返回条数,默认 200。
        【返回】list[dict]: 匹配的帖子字典列表,按发布时间倒序。
        【关键逻辑】动态拼接 WHERE 条件;品种按 JSON 数组文本模糊匹配。
        """
        conditions = []  # 【变量】conditions:动态拼接的 WHERE 条件列表
        params = []  # 【变量】params:与 conditions 一一对应的 SQL 参数
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if variety:
            conditions.append("varieties LIKE ?")
            params.append(f'%"{variety}"%')
        if sentiment:
            conditions.append("sentiment = ?")
            params.append(sentiment)
        if since:
            conditions.append("publish_time >= ?")
            params.append(since)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM posts {where} ORDER BY publish_time DESC LIMIT ?", params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

    def get_platform_stats(self) -> dict:
        """统计各平台帖子数量与时间范围。

        【功能】按平台分组统计帖子数量、最早与最晚发布时间。
        【参数】无。
        【返回】dict: {平台名: {"count":数量, "earliest":最早日期, "latest":最晚日期}}。
        【关键逻辑】GROUP BY platform 分组;earliest/latest 截取前 10 位(YYYY-MM-DD)。
        """
        with self._conn() as c:
            rows = c.execute("""
                SELECT platform, COUNT(*) as cnt, MIN(publish_time) as earliest, MAX(publish_time) as latest
                FROM posts GROUP BY platform ORDER BY cnt DESC
            """).fetchall()
        result = {}
        for r in rows:
            result[r["platform"]] = {
                "count": r["cnt"],
                "earliest": r["earliest"][:10] if r["earliest"] else "?",
                "latest": r["latest"][:10] if r["latest"] else "?",
            }
        return result

    def get_total_posts(self) -> int:
        """统计帖子总数。

        【功能】返回 posts 表中的总记录数,用于展示数据规模。
        【参数】无。
        【返回】int: 帖子总数。
        """
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    # ── Sentiment Daily ────────────────────────────────────────────────

    def upsert_sentiment_daily(self, variety: str, date: str, data: dict):
        """写入或更新某个品种某一天的每日情感统计。

        【功能】将计算好的每日情感汇总 upsert 进 sentiment_daily 表;
                当天数据已存在则覆盖更新,否则插入新行。
        【参数】
            variety : 品种代码(如 "RB")。
            date    : 日期字符串(YYYY-MM-DD)。
            data    : 统计字典,含 simple_avg/avg_score/bullish_ratio/
                      bearish_ratio/neutral_ratio/total_notes/author_count/
                      platform_breakdown 等字段。
        【返回】无。
        【关键逻辑】利用 SQLite 的 ON CONFLICT(variety, date) DO UPDATE 语法实现
                   "存在则更新、不存在则插入";platform_breakdown 存为 JSON 字符串。
        """
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO sentiment_daily (variety, date, simple_avg, avg_score,
                    bullish_ratio, bearish_ratio, neutral_ratio, total_notes, author_count, platform_breakdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(variety, date) DO UPDATE SET
                    simple_avg=excluded.simple_avg, avg_score=excluded.avg_score,
                    bullish_ratio=excluded.bullish_ratio, bearish_ratio=excluded.bearish_ratio,
                    neutral_ratio=excluded.neutral_ratio, total_notes=excluded.total_notes,
                    author_count=excluded.author_count, platform_breakdown=excluded.platform_breakdown,
                    updated_at=datetime('now')
            """,
                (
                    variety,
                    date,
                    data.get("simple_avg", 0),
                    data.get("avg_score", 0),
                    data.get("bullish_ratio", 0),
                    data.get("bearish_ratio", 0),
                    data.get("neutral_ratio", 0),
                    data.get("total_notes", 0),
                    data.get("author_count", 0),
                    json.dumps(data.get("platform_breakdown", {}), ensure_ascii=False),  # 【调用函数】把平台分布字典序列化为 JSON 文本存库
                ),
            )

    def get_sentiment_series(self, variety: str, days: int = 180) -> list[dict]:
        """获取某品种最近 N 天的每日情感时间序列。

        【功能】按时间升序返回指定品种近 days 天的每日情感统计,供图表展示。
        【参数】variety: 品种代码;days: 回溯天数,默认 180 天。
        【返回】list[dict]: sentiment_daily 表中的记录列表(按日期升序)。
        【关键逻辑】先计算起始日期(since),再按 variety 与 date 范围查询。
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")  # 【变量】since:回溯起始日期(YYYY-MM-DD,近 days 天)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sentiment_daily WHERE variety=? AND date>=? ORDER BY date",
                (variety, since),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Alerts ─────────────────────────────────────────────────────────

    def create_alert(
        self,
        alert_type: str,
        title: str,
        message: str = "",
        variety: str = "",
        severity: str = "info",
        data: dict = None,
    ) -> int:
        """新增一条告警记录。

        【功能】向 alerts 表插入一条系统告警/消息(采集失败、管道事件等)。
        【参数】
            alert_type : 告警类型(如 collection_failed/pipeline_started)。
            title      : 告警标题。
            message    : 告警详情文本。
            variety    : 关联品种(可选)。
            severity   : 严重程度(info/warning/error)。
            data       : 附加数据字典,序列化为 JSON 存储。
        【返回】int: 新告警的自增 id(c.lastrowid)。
        """
        with self._conn() as c:
            c.execute(
                "INSERT INTO alerts (alert_type, variety, title, message, severity, data) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    alert_type,
                    variety,
                    title,
                    message,
                    severity,
                    json.dumps(data or {}, ensure_ascii=False),  # 【调用函数】把附加数据字典序列化为 JSON 文本存库
                ),
            )
            return c.lastrowid

    def get_alerts(self, limit=50, unacknowledged_only=False) -> list[dict]:
        """查询告警列表。

        【功能】获取告警记录,可只看未确认的告警,按创建时间倒序。
        【参数】limit: 最多返回条数(默认 50);unacknowledged_only: 仅返回未确认。
        【返回】list[dict]: 告警字典列表。
        【关键逻辑】unacknowledged_only=True 时拼接 WHERE acknowledged=0 条件。
        """
        where = "WHERE acknowledged=0" if unacknowledged_only else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM alerts {where} ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def acknowledge_alert(self, alert_id: int):
        """把指定告警标记为"已确认/已读"。

        【功能】将 alerts 表中对应 id 的 acknowledged 置为 1。
        【参数】alert_id: 告警自增 id。
        【返回】无。
        """
        with self._conn() as c:
            c.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))

    def get_unacknowledged_count(self) -> int:
        """统计未确认告警的数量。

        【功能】返回 acknowledged=0 的告警条数,常用于界面红点提示。
        【参数】无。
        【返回】int: 未确认告警数。
        """
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged=0").fetchone()[0]

    # ── Collection Log ─────────────────────────────────────────────────

    def start_collection(self, platform: str, keywords_count: int) -> int:
        """开始一次采集任务:写入一条状态为 running 的日志。

        【功能】在 collection_log 表插入一行"采集进行中"记录。
        【参数】platform: 平台名;keywords_count: 使用的关键词数量。
        【返回】int: 本次采集日志的自增 id,后续 finish_collection 用它更新结果。
        【关键逻辑】status 初始为 'running',结束时由 finish_collection 改写。
        """
        with self._conn() as c:
            c.execute(
                "INSERT INTO collection_log (platform, keywords_count, status) VALUES (?, ?, 'running')",
                (platform, keywords_count),
            )
            return c.lastrowid

    def finish_collection(
        self, log_id: int, posts_collected: int, posts_after_filter: int = 0, error: str = ""
    ):
        """结束一次采集任务,更新结果状态。

        【功能】把 start_collection 创建的日志行更新为最终状态(成功/失败)及条数。
        【参数】
            log_id            : start_collection 返回的日志 id。
            posts_collected   : 采集到的帖子条数。
            posts_after_filter: 过滤后保留的条数;未传则默认等于采集数。
            error             : 错误信息;非空时状态记为 'error'。
        【返回】无。
        【关键逻辑】status 由 error 是否为空决定;finished_at 由 SQL 写为当前时间。
        """
        status = "error" if error else "success"  # 【变量】status:结束状态(error=有错误,否则 success)
        with self._conn() as c:
            c.execute(
                "UPDATE collection_log SET status=?, posts_collected=?, posts_after_filter=?, error_msg=?, finished_at=datetime('now') WHERE id=?",
                (status, posts_collected, posts_after_filter or posts_collected, error, log_id),
            )

    def get_collection_history(self, limit=20) -> list[dict]:
        """查询最近的采集任务历史。

        【功能】按开始时间倒序返回最近 limit 次采集日志。
        【参数】limit: 返回条数,默认 20。
        【返回】list[dict]: 采集日志字典列表。
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM collection_log ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Users / Auth ───────────────────────────────────────────────────

    def create_user(self, username: str, password: str, is_admin: bool = False) -> bool:
        """创建新用户。

        【功能】向 users 表插入一个用户,密码以 SHA256 哈希存储。
        【参数】username: 用户名;password: 明文密码(内部立即哈希);
                is_admin: 是否为管理员。
        【返回】bool: 创建成功返回 True;用户名重复时捕获 IntegrityError 返回 False。
        【关键逻辑】密码绝不明文存储;sha256 哈希后落库。
        """
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()  # 【调用函数】SHA256 哈希明文密码(绝不存明文);【变量】pwd_hash:哈希后的密码摘要
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                    (username, pwd_hash, 1 if is_admin else 0),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def verify_user(self, username: str, password: str) -> bool:
        """校验用户名与密码是否匹配。

        【功能】将输入密码哈希后与库中记录比对,判断登录是否成功。
        【参数】username: 用户名;password: 明文密码。
        【返回】bool: 匹配返回 True,否则 False。
        """
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()  # 【调用函数】SHA256 哈希输入密码,与库中记录比对
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM users WHERE username=? AND password_hash=?", (username, pwd_hash)
            ).fetchone()
        return row is not None

    def ensure_default_user(self):
        """确保存在默认管理员账号(首次启动时自动创建)。

        【功能】若 users 表为空,则创建默认管理员 admin / agentsense2026。
        【参数】无。
        【返回】无。
        【关键逻辑】只在用户表为空时创建,避免覆盖已有账号;默认管理员可登录管理。

        Create default admin if no users exist.
        """
        with self._conn() as c:
            count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]  # 【变量】count:当前用户总数(判断是否需要建默认管理员)
        if count == 0:
            self.create_user("admin", "agentsense2026", is_admin=True)

    # ── Watchlist ──────────────────────────────────────────────────────

    def get_watchlist(self, user_id=1) -> list[str]:
        """获取用户的自选品种列表。

        【功能】按 user_id 返回 watchlist 表中的品种代码列表。
        【参数】user_id: 用户 id,默认 1(当前单用户/默认用户)。
        【返回】list[str]: 品种代码列表(如 ["RB","J","AU"])。
        """
        with self._conn() as c:
            rows = c.execute("SELECT variety FROM watchlist WHERE user_id=?", (user_id,)).fetchall()
        return [r["variety"] for r in rows]

    def add_to_watchlist(self, variety: str, user_id=1):
        """添加品种到用户自选列表。

        【功能】向 watchlist 插入 (user_id, variety);已存在则忽略。
        【参数】variety: 品种代码;user_id: 用户 id,默认 1。
        【返回】无。
        【关键逻辑】INSERT OR IGNORE + (user_id, variety) 唯一约束,避免重复自选。
        """
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, variety) VALUES (?, ?)",
                (user_id, variety),
            )

    def remove_from_watchlist(self, variety: str, user_id=1):
        """从用户自选列表移除品种。

        【功能】删除 watchlist 中指定的 (user_id, variety) 记录。
        【参数】variety: 品种代码;user_id: 用户 id,默认 1。
        【返回】无。
        """
        with self._conn() as c:
            c.execute("DELETE FROM watchlist WHERE user_id=? AND variety=?", (user_id, variety))

    # ── Trade Signals ──────────────────────────────────────────────────

    def save_trade_signal(
        self,
        variety: str,
        date: str,
        signal_value: float,
        direction: str,
        entry_price: float,
        horizon_days: int = 3,
    ):
        """保存一条新的交易信号。

        【功能】把策略产出的交易信号写入 trade_signals 表,供后续回测/评价。
        【参数】
            variety     : 品种代码。
            date        : 信号日期。
            signal_value: 信号强度数值。
            direction   : 方向("buy"/"sell"/"hold" 等)。
            entry_price : 入场价。
            horizon_days: 预测持有周期(天数),默认 3。
        【返回】无。
        【关键逻辑】outcome 初始为 'pending',等待回测结算后更新。
        """
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO trade_signals (variety, signal_date, signal_value, direction, entry_price, horizon_days)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (variety, date, signal_value, direction, entry_price, horizon_days),
            )

    def resolve_trade_signal(self, signal_id: int, exit_price: float, pnl_pct: float, outcome: str):
        """结算/更新一条交易信号的结果。

        【功能】回测结束后,把出场价、盈亏比例、结果(win/loss)写回该信号记录。
        【参数】
            signal_id : 信号自增 id。
            exit_price: 出场/结算价。
            pnl_pct   : 盈亏百分比。
            outcome   : 结果('win'/'loss' 等)。
        【返回】无。
        """
        with self._conn() as c:
            c.execute(
                "UPDATE trade_signals SET exit_price=?, pnl_pct=?, outcome=? WHERE id=?",
                (exit_price, pnl_pct, outcome, signal_id),
            )

    def get_trade_signals(self, variety=None, outcome=None, limit=100) -> list[dict]:
        """按条件查询交易信号。

        【功能】可按品种与结果过滤交易信号,按创建时间倒序返回。
        【参数】variety: 品种代码(可选);outcome: 结果(可选);limit: 条数上限。
        【返回】list[dict]: 交易信号字典列表。
        【关键逻辑】动态拼接 WHERE 条件,类似 get_posts。
        """
        conditions = []
        params = []
        if variety:
            conditions.append("variety = ?")
            params.append(variety)
        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM trade_signals {where} ORDER BY created_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trade_stats(self) -> dict:
        """汇总交易信号的统计指标(胜率、平均盈亏等)。

        【功能】计算已结算信号的总数、胜/负场数、胜率与平均盈亏。
        【参数】无。
        【返回】dict: {total_trades, wins, losses, win_rate, avg_pnl_pct}。
        【关键逻辑】只统计 outcome != 'pending' 的已结算信号;胜率=胜场/总场。
        """
        with self._conn() as c:
            total = c.execute(  # 【变量】total:已结算信号总数(不含 pending)
                "SELECT COUNT(*) FROM trade_signals WHERE outcome != 'pending'"
            ).fetchone()[0]
            wins = c.execute("SELECT COUNT(*) FROM trade_signals WHERE outcome='win'").fetchone()[0]
            avg_pnl = c.execute(  # 【变量】avg_pnl:已结算信号的平均盈亏百分比
                "SELECT AVG(pnl_pct) FROM trade_signals WHERE outcome != 'pending'"
            ).fetchone()[0]
        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "avg_pnl_pct": round(avg_pnl or 0, 3),
        }

    # ── Research Reports ────────────────────────────────────────────────

    def insert_research_report(
        self,
        variety: str,
        title: str = "",
        source: str = "",
        filename: str = "",
        file_path: str = "",
        status: str = "processing",
    ) -> int:
        """新增一条研报记录(状态默认 processing,由后台线程处理后更新)。

        【功能】向 research_reports 表插入一行"待处理研报",返回自增 id。
        【参数】variety: 品种代码;title/source/filename/file_path: 元信息;
                status: 初始状态('processing' 由上传接口写入)。
        【返回】int: 新研报的自增 id(后台线程据此处理并回写)。
        """
        with self._conn() as c:
            cur = c.execute(  # 【变量】cur:插入游标(lastrowid 取新记录自增 id)
                "INSERT INTO research_reports (variety, title, source, filename, file_path, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (variety, title, source, filename, file_path, status),
            )
            return cur.lastrowid

    def update_research_report(self, report_id: int, **fields):
        """更新一条研报记录的部分字段(白名单过滤,防注入)。

        【功能】按字段名白名单把传入的字段写入指定研报行;
                不在白名单里的键会被忽略,不报错。
        【参数】report_id: 研报自增 id;fields: 可更新字段(如 status/
                extracted_text/structured_data/conclusion_md/direction/confidence/error)。
        【返回】无。
        """
        allowed = {
            "title", "source", "variety", "filename", "file_path", "status",
            "extracted_text", "structured_data", "conclusion_md",
            "direction", "confidence", "error", "varieties",
        }
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            params.append(value)
        if not sets:
            return
        params.append(report_id)
        with self._conn() as c:
            c.execute(
                f"UPDATE research_reports SET {', '.join(sets)} WHERE id=?", params
            )

    def list_research_reports(self, variety: str | None = None, limit: int = 50) -> list[dict]:
        """按品种(可选)查询研报列表,按上传时间倒序。

        【功能】获取研报列表;可按品种过滤,默认返回最近 50 条。
        【参数】variety: 品种代码(可选);limit: 条数上限,默认 50。
        【返回】list[dict]: 研报记录字典列表(按 uploaded_at 倒序)。
        【关键逻辑】多品种研报的 varieties 列是逗号分隔列表;过滤时用
                   ','||varieties||',' 包裹后做 LIKE 精确匹配(避免 "RB"
                   误匹配到 "IRB")。旧行 varieties 为空时回退匹配主品种列。
        """
        if variety:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM research_reports "
                    "WHERE (',' || varieties || ',' LIKE '%,' || ? || ',%' "
                    "       OR (varieties = '' AND variety = ?)) "
                    "ORDER BY uploaded_at DESC LIMIT ?",
                    (variety, variety, limit),
                ).fetchall()
        else:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM research_reports ORDER BY uploaded_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_research_report(self, report_id: int) -> dict | None:
        """按 id 查询单条研报(含提取文本/结构化数据/结论全文)。

        【功能】返回指定研报的完整记录;不存在时返回 None。
        【参数】report_id: 研报自增 id。
        【返回】dict | None: 研报记录(详情查看用)或 None。
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM research_reports WHERE id=?", (report_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_research_report(self, report_id: int) -> bool:
        """按 id 删除一条研报记录。

        【功能】删除指定研报;删除成功返回 True,不存在返回 False。
        【参数】report_id: 研报自增 id。
        【返回】bool: 是否真的删掉了记录。
        """
        with self._conn() as c:
            cur = c.execute("DELETE FROM research_reports WHERE id=?", (report_id,))
            return cur.rowcount > 0
