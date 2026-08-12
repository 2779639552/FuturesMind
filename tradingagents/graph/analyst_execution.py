from collections.abc import Iterable  # 【调用包】可迭代类型标注 (selected_analysts)
from dataclasses import dataclass  # 【调用包】数据类声明 (轻量结构体)
from time import monotonic  # 【调用包】单调时钟 (统计分析师墙钟耗时)


# 【功能】一位分析师的图节点规格: 描述该分析师在 LangGraph 中被拆成的三个节点
#     (agent_node / clear_node / tool_node) 以及报告写入最终状态用的字段名。
# 【关键】frozen=True 使实例不可变, 保证规格表可作为常量安全共享。
@dataclass(frozen=True)
class AnalystNodeSpec:
    key: str  # 【变量】分析师类型键, 与 ConditionalLogic 路由方法名后缀对应
    agent_node: str  # 【变量】分析师智能体节点名 (注册进 StateGraph 的名字)
    clear_node: str  # 【变量】清空消息节点名 (避免上下文无限累积)
    tool_node: str  # 【变量】工具节点名 (该分析师可调用的数据工具)
    report_key: str  # 【变量】最终状态里该分析师报告落盘的字段名


# 【功能】分析师执行计划: 按选中顺序保存一份有序的节点规格列表。
@dataclass(frozen=True)
class AnalystExecutionPlan:
    specs: list[AnalystNodeSpec]  # 【变量】有序节点规格列表, 顺序即主流程先后


# 【功能】全量分析师节点规格注册表: 键为分析师类型键, 值为对应节点规格。
# 【关键】'social' 的 wire key 保持 "social" 以兼容已保存配置; 但用户可见标签为
#     "Sentiment Analyst", 对应 v0.2.5 的情绪分析师改名 (现同时摄入新闻 +
#     StockTwits + Reddit, 不再只是社交媒体)。
ANALYST_NODE_SPECS: dict[str, AnalystNodeSpec] = {
    "market": AnalystNodeSpec(
        key="market",
        agent_node="Market Analyst",
        clear_node="Msg Clear Market",
        tool_node="tools_market",
        report_key="market_report",
    ),
    "social": AnalystNodeSpec(
        # Wire key stays "social" for saved-config back-compat; the
        # user-facing label is "Sentiment Analyst" to match the rename
        # that landed in v0.2.5 (sentiment_analyst now ingests news +
        # StockTwits + Reddit, not just social media).
        key="social",
        agent_node="Sentiment Analyst",
        clear_node="Msg Clear Sentiment",
        tool_node="tools_social",
        report_key="sentiment_report",
    ),
    "news": AnalystNodeSpec(
        key="news",
        agent_node="News Analyst",
        clear_node="Msg Clear News",
        tool_node="tools_news",
        report_key="news_report",
    ),
    "fundamentals": AnalystNodeSpec(
        key="fundamentals",
        agent_node="Fundamentals Analyst",
        clear_node="Msg Clear Fundamentals",
        tool_node="tools_fundamentals",
        report_key="fundamentals_report",
    ),
}


# 【功能】根据选中的分析师类型键构建执行计划 (供 setup_graph 决定注册哪些分析师节点)。
# 【参数】selected_analysts: 分析师类型键的可迭代对象, 顺序即图中执行先后。
# 【返回】AnalystExecutionPlan, 内含按传入顺序排列的节点规格列表。
# 【关键】未知键抛 ValueError (阻止脏配置进图); 空列表同样抛错 (至少需选中一位分析师)。
def build_analyst_execution_plan(
    selected_analysts: Iterable[str],
) -> AnalystExecutionPlan:
    specs: list[AnalystNodeSpec] = []
    for analyst_key in selected_analysts:
        spec = ANALYST_NODE_SPECS.get(analyst_key)  # 【调用函数】从全量注册表取节点规格
        if spec is None:
            raise ValueError(f"unknown analyst key: {analyst_key}")
        specs.append(spec)

    if not specs:
        raise ValueError("at least one analyst must be selected")

    return AnalystExecutionPlan(specs=specs)


# 【功能】返回计划中第一位分析师的 agent 节点名 (作为图的入口节点)。
def get_initial_analyst_node(plan: AnalystExecutionPlan) -> str:
    return plan.specs[0].agent_node


# 【功能】按分析师维度统计"墙钟耗时"的追踪器, 用于在日志/汇总里展示每位分析师耗时。
class AnalystWallTimeTracker:
    # 【功能】初始化耗统计器。
    # 【参数】plan: 分析师执行计划, 用于遍历所有分析师规格。
    def __init__(self, plan: AnalystExecutionPlan):
        self.plan = plan  # 【变量】执行计划 (遍历全部分析师用)
        self._started_at: dict[str, float] = {}  # 【变量】分析师类型键 → 开始时间
        self._wall_times: dict[str, float] = {}  # 【变量】分析师类型键 → 已结算耗时 (秒)

    # 【功能】记录某位分析师开始分析的墙钟时间。
    # 【参数】analyst_key: 分析师类型键; started_at: 可选自定义起始时间 (默认 monotonic())。
    # 【关键】未知键抛 ValueError; setdefault 保证重复标记不会覆盖首次开始时间。
    def mark_started(self, analyst_key: str, started_at: float | None = None) -> None:
        if analyst_key not in ANALYST_NODE_SPECS:
            raise ValueError(f"unknown analyst key: {analyst_key}")
        self._started_at.setdefault(analyst_key, monotonic() if started_at is None else started_at)

    # 【功能】结算某位分析师的耗时 (结束时间 − 开始时间)。
    # 【参数】analyst_key: 分析师类型键; completed_at: 可选自定义结束时间 (默认 monotonic())。
    # 【关键】已结算过的键直接返回 (不覆盖); 没有开始记录则忽略; 耗时下限钳制为 0。
    def mark_completed(
        self,
        analyst_key: str,
        completed_at: float | None = None,
    ) -> None:
        if analyst_key not in ANALYST_NODE_SPECS:
            raise ValueError(f"unknown analyst key: {analyst_key}")
        if analyst_key in self._wall_times:
            return
        started_at = self._started_at.get(analyst_key)
        if started_at is None:
            return
        finished_at = monotonic() if completed_at is None else completed_at
        self._wall_times[analyst_key] = max(0.0, finished_at - started_at)

    # 【功能】返回各分析师耗时的拷贝字典 (只读快照, 避免外部改动内部状态)。
    def get_wall_times(self) -> dict[str, float]:
        return dict(self._wall_times)

    # 【功能】生成人类可读的耗时汇总字符串。
    # 【返回】如 "Analyst wall time: Market 1.23s | Sentiment 0.98s"; 无任何记录时
    #     返回 "Analyst wall time: pending"。
    def format_summary(self) -> str:
        parts = []
        for spec in self.plan.specs:
            duration = self._wall_times.get(spec.key)
            if duration is not None:
                label = spec.agent_node.removesuffix(" Analyst")  # 【变量】去掉 " Analyst" 后缀的展示名
                parts.append(f"{label} {duration:.2f}s")
        if not parts:
            return "Analyst wall time: pending"
        return "Analyst wall time: " + " | ".join(parts)


# 【功能】从 stream() 的一个 chunk 中同步各分析师的"开始/完成"计时 (流式驱动的计时)。
# 【参数】
#     tracker: 待同步的耗时追踪器; chunk: 图某一步吐出的状态增量 (含各报告字段);
#     now: 可选自定义当前时间 (默认 monotonic())。
# 【关键】若某位分析师的报告已出现在 chunk, 视为"已完成", 同时标记开始+完成;
#     否则把"第一个尚未有报告"的分析师当作当前活跃者标记为开始 (用于度量进行中的耗时)。
def sync_analyst_tracker_from_chunk(
    tracker: AnalystWallTimeTracker,
    chunk: dict[str, str],
    now: float | None = None,
) -> None:
    current_time = monotonic() if now is None else now
    active_found = False

    for spec in tracker.plan.specs:
        has_report = bool(chunk.get(spec.report_key))

        if has_report:
            tracker.mark_started(spec.key, started_at=current_time)
            tracker.mark_completed(spec.key, completed_at=current_time)
            continue

        if not active_found:
            tracker.mark_started(spec.key, started_at=current_time)
            active_found = True
