"""
Self-Evolution Memory Layer for TradingAgents Commodity Futures.

Persists user-agent debate sessions and a learned user profile across analysis
runs. On each subsequent run, the accumulated lessons are injected into the
analyst prompts so the system progressively adapts to the user's analytical
style, preferred factors, and correction patterns.

Storage layout::

    ~/.tradingagents/evolution_memory/
        user_profile.json          # cross-variety learned user profile
        {VARIETY}.json             # per-variety debate history

Follows the same pattern as ``external_data.py``: JSON files under
``~/.tradingagents/`` with staleness-agnostic reads (debates never expire).
"""

import contextlib  # 【调用包】上下文管理(抑制写回预测文件时的 OSError)
import json  # 【调用包】JSON 读写(进化记忆/用户画像/预测记录持久化)
import logging  # 【调用包】日志输出(存取/更新告警)
import os  # 【调用包】路径拼接与环境变量读取(数据目录覆盖)
import uuid  # 【调用包】生成辩论记录唯一 ID
from datetime import datetime, timezone  # 【调用包】UTC 时间戳生成(记录 updated 字段)
from pathlib import Path  # 【调用包】路径对象与文件操作(数据目录/临时文件)
from typing import Any  # 【调用包】任意类型注解(辩论/画像字典)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "evolution_memory")  # 【变量】进化记忆默认目录 = ~/.tradingagents/evolution_memory

MAX_DEBATES_PER_VARIETY = 50  # 【变量】每品种最多保留的辩论条数(超出裁剪最旧)
MAX_CRITICISMS_IN_PROFILE = 10  # 【变量】用户画像中最多保留的批评/争议条数


# 【功能】返回进化记忆目录,优先读取环境变量覆盖值。
# 【参数】无。
# 【返回】目录路径字符串。
# 【关键】环境变量 TRADINGAGENTS_EVOLUTION_MEMORY_DIR 未设置时回退默认目录。
def _get_data_dir() -> str:
    """Get evolution memory directory, respecting env override."""
    return os.environ.get("TRADINGAGENTS_EVOLUTION_MEMORY_DIR", DEFAULT_DATA_DIR)


# 【功能】确保进化记忆数据目录存在,并返回其 Path 对象。
# 【参数】无。
# 【返回】Path:数据目录。
# 【关键】目录不存在时自动递归创建(parents=True, exist_ok=True)。
def _ensure_dir() -> Path:
    """Create and return the data directory."""
    p = Path(_get_data_dir())
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Low-level file I/O (public so other modules can compose their own queries)
# ---------------------------------------------------------------------------


# 【功能】读取某品种的全部历史辩论会话列表。
# 【参数】variety: 品种代码(如 "RB")。
# 【返回】list[dict];文件不存在/JSON损坏/声明品种不匹配时返回空列表。
# 【关键】调用方永远拿到安全默认值,损坏的进化数据不会阻塞分析。
def load_debates(variety: str) -> list[dict[str, Any]]:
    """Load all debate sessions for a commodity variety.

    Returns an empty list when no file exists, JSON is malformed, or the
    declared ``variety`` field doesn't match — the caller always sees a safe
    default and the analysis never blocks on corrupt evolution data.
    """
    filepath = _ensure_dir() / f"{variety.upper()}.json"
    if not filepath.exists():
        return []

    try:
        raw = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Evolution memory for %s is corrupt: %s", variety, e)
        return []

    if not isinstance(raw, dict):
        return []

    declared = raw.get("variety", "").upper()
    if declared and declared != variety.upper():
        logger.warning(
            "Evolution memory file %s declares variety=%s, expected %s.",
            filepath,
            declared,
            variety,
        )
        return []

    return raw.get("debate_sessions", [])


# 【功能】把一条辩论记录追加写入该品种的进化记忆文件,并原子替换写盘。
# 【参数】variety: 品种代码;debate_record: 结构化辩论记录 dict。
# 【返回】无。
# 【关键】1) 先读旧容器(损坏则重置为空)再追加新记录;
#        2) 超过 MAX_DEBATES_PER_VARIETY 时只保留最近 N 条;
#        3) 经临时文件 .tmp 写入后 rename 替换正式文件,实现原子写。
def save_debate(variety: str, debate_record: dict[str, Any]) -> None:
    """Append a debate session record for *variety*.

    Loads the existing file, appends the new session, prunes the oldest
    entries if over *MAX_DEBATES_PER_VARIETY*, and writes atomically.
    """
    filepath = _ensure_dir() / f"{variety.upper()}.json"

    # Load existing or create fresh container
    if filepath.exists():
        try:
            container = json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            container = {}
    else:
        container = {}

    if not isinstance(container, dict):
        container = {}

    container.setdefault("version", "1.0")
    container["variety"] = variety.upper()
    container["updated"] = datetime.now(timezone.utc).isoformat()

    sessions = container.setdefault("debate_sessions", [])
    sessions.append(debate_record)

    # Prune oldest if over limit
    if len(sessions) > MAX_DEBATES_PER_VARIETY:
        container["debate_sessions"] = sessions[-MAX_DEBATES_PER_VARIETY:]

    # Atomic write via temp file
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(container, ensure_ascii=False, indent=2), encoding="utf-8")  # 【调用函数】序列化容器为 JSON 写入临时文件
    tmp.replace(filepath)  # 【调用函数】临时文件原子替换正式文件(避免半写状态)
    logger.info("Saved debate session for %s (total: %d)", variety, len(sessions))


# 【功能】加载跨品种的用户画像,首次运行返回默认骨架。
# 【参数】无。
# 【返回】dict:用户画像(缺失键用默认骨架补齐)。
# 【关键】调用方无需判 None;文件损坏同样回退默认骨架。
def load_user_profile() -> dict[str, Any]:
    """Load the cross-variety user profile.

    Returns a default skeleton on first run so callers never have to
    guard against ``None``.
    """
    filepath = _ensure_dir() / "user_profile.json"
    if not filepath.exists():
        return _default_profile()

    try:
        raw = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_profile()

    if not isinstance(raw, dict):
        return _default_profile()

    # Fill in any missing keys from the default skeleton
    default = _default_profile()
    for k, v in default.items():
        raw.setdefault(k, v)
    return raw


# 【功能】把用户画像原子写盘。
# 【参数】profile: 要持久化的用户画像 dict(会被就地补 updated/version 字段)。
# 【返回】无。
# 【关键】先补时间戳与版本字段,再经临时文件替换写入,保证原子性。
def save_user_profile(profile: dict[str, Any]) -> None:
    """Persist the user profile atomically."""
    profile["updated"] = datetime.now(timezone.utc).isoformat()
    profile.setdefault("version", "1.0")

    filepath = _ensure_dir() / "user_profile.json"
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")  # 【调用函数】序列化画像为 JSON 写入临时文件
    tmp.replace(filepath)  # 【调用函数】临时文件原子替换正式文件


# 【功能】返回用户画像的默认骨架(首次运行/文件损坏时使用)。
# 【参数】无。
# 【返回】dict:含版本、创建/更新时间、关注因子、分析风格、风险偏好、
#        历史批评、争议观点、方向校准、品种专属信息等键。
def _default_profile() -> dict[str, Any]:
    """Return the skeleton used on first ever run."""
    return {
        "version": "1.0",
        "created": datetime.now(timezone.utc).isoformat(),
        "updated": datetime.now(timezone.utc).isoformat(),
        "preferred_factors": {},
        "analysis_style": "",
        "risk_profile": "moderate",
        "common_criticisms": [],
        "disputed_claims": [],
        "direction_calibration": {
            "bullish_bias_detected": False,
            "adjustment_note": "",
            "user_agent_disagreement_count": 0,
        },
        "variety_specific": {},
    }


# ---------------------------------------------------------------------------
# Prompt injection: build the evolution context string
# ---------------------------------------------------------------------------


# 【功能】把进化记忆格式化为可注入分析师/讨论/综述提示的 Markdown 文本。
# 【参数】variety: 品种代码(如 "RB")。
# 【返回】str;没有任何历史时返回空字符串(不改变提示结构)。
# 【关键】1) 依次拼接:延迟结果反思 → 品种历史经验(最近5次去重) → 用户因子权重
#           → 方向校准 → 历史批评 → 分析风格 → 争议观点;
#        2) 返回文本自带标题与说明,可直接 prepend 到 system_message。
def get_evolution_context(variety: str) -> str:
    """Format evolution memory for injection into analyst / discussion /
    synthesis prompts.

    Returns an empty string when there is no history, so the prompt
    structure is unaffected on first run.

    The returned string is self-contained markdown that can be
    prepended to any ``system_message`` or appended to any f-string
    prompt without further formatting.
    """
    debates = load_debates(variety)  # 【调用函数】读该品种历史辩论会话
    profile = load_user_profile()  # 【调用函数】读跨品种用户画像

    # Nothing to inject at all
    has_debates = bool(debates)
    has_criticisms = bool(profile.get("common_criticisms"))
    has_factors = bool(profile.get("preferred_factors"))
    outcome = get_outcome_reflection(variety)  # 【调用函数】结算历史预测并生成反思文本(可为空)
    has_outcome = bool(outcome)
    if not (has_debates or has_criticisms or has_factors or has_outcome):
        return ""

    parts: list[str] = []

    # --- Deferred outcome reflection (NEW v2.3.2) ---
    if outcome:
        parts.append(outcome)

    # --- Variety-specific lessons (most important, shown first) ---
    if debates:
        lessons_set: set[str] = set()
        for d in debates[-5:]:  # last 5 sessions
            for lesson in d.get("lessons_learned", []):
                lessons_set.add(lesson)
        if lessons_set:
            parts.append(f"### {variety} 品种历史经验")
            for lesson in sorted(lessons_set):
                parts.append(f"- {lesson}")
            parts.append("")

    # --- User preferred factors ---
    factors = profile.get("preferred_factors", {})
    if factors:
        sorted_factors = sorted(factors.items(), key=lambda x: x[1], reverse=True)
        parts.append("### 用户关注因子权重")
        parts.append(" > ".join(f"{k}({v})" for k, v in sorted_factors[:6]))
        parts.append("")

    # --- Direction calibration ---
    cal = profile.get("direction_calibration", {})
    if cal.get("bullish_bias_detected") and cal.get("adjustment_note"):
        parts.append(f"### 方向校准\n{cal['adjustment_note']}\n")

    # --- Common criticisms ---
    criticisms = profile.get("common_criticisms", [])
    if criticisms:
        parts.append("### 用户历史反馈要点")
        for c in criticisms[:MAX_CRITICISMS_IN_PROFILE]:
            parts.append(f"- {c}")
        parts.append("")

    # --- Analysis style ---
    style = profile.get("analysis_style", "")
    if style:
        parts.append(f"### 用户分析偏好\n{style}\n")

    if not parts:
        return ""

    # --- Disputed claims (user claims agent rejected) ---
    disputed = profile.get("disputed_claims", [])
    if disputed:
        parts.append("### 历史争议点 (Agent 持保留意见)")
        parts.append("以下用户观点在历史辩论中被系统质疑或拒绝，分析时应保持警惕但不预设其正确性：")
        for d in disputed[-5:]:
            parts.append(f"- {d}")
        parts.append("")

    if not parts:
        return ""

    header = (
        "## 自进化记忆 (Evolution Memory) — 基于历史用户反馈自动校准\n"
        "以下经验来自用户与系统的历史辩论。**已采纳**的观点用于优化分析；"
        "**争议**的观点仅供参考，不应覆盖当前数据。\n"
        "请用数据验证每一个历史经验是否仍然适用于当前市场情况。\n"
    )
    return header + "\n".join(parts)


# ---------------------------------------------------------------------------
# User profile auto-update
# ---------------------------------------------------------------------------


# 【功能】根据一次已完成的辩论更新用户画像并持久化。
# 【参数】variety: 品种代码;debate_record: 辩论记录 dict。
# 【返回】dict:更新后的用户画像(已写盘)。
# 【关键】客观性规则:
#        1) 仅当辩论确认用户观点有效(user_persuaded_agent / mutual_understanding)
#           时才把 lessons 并入 common_criticisms,否则记入 disputed_claims;
#        2) 因子权重增量仅在用户说服 agent 时提交,并夹在 [1,5] 区间;
#        3) 方向偏差不自动认定——需分歧次数>=3 且用户方向被验证才标记 bullish_bias;
#        4) 分析风格按"基本面/技术面"关键词出现频次做启发式推断。
def update_profile_from_debate(
    variety: str,
    debate_record: dict[str, Any],
) -> dict[str, Any]:
    """Update the user profile based on a completed debate session.

    Called after every debate. Returns the updated profile dict (which
    has already been persisted to disk).

    OBJECTIVITY RULES:
    1. Lessons are only applied when the debate CONFIRMED the user's point was valid
       (resolution = "user_persuaded_agent" or "mutual_understanding").
    2. Factor weight deltas marked "tentative" are only committed if the user
       persuaded the agent.
    3. Direction bias is NOT automatically assumed — the agent may be correct
       and the user overly pessimistic. Disagreement alone does not imply bias.
    4. When the agent maintained its position (resolution = "agent_maintained_position"
       or "agent_persuaded_user"), the user's claims are recorded as DISPUTED
       rather than accepted.
    """
    profile = load_user_profile()
    resolution = debate_record.get("resolution", "")

    # Only apply lessons if the debate confirmed user's points
    user_persuaded = resolution in ("user_persuaded_agent", "mutual_understanding")

    # ---- 1. Merge VERIFIED lessons into common criticisms ----
    if user_persuaded:
        existing_criticisms: set[str] = set(profile.get("common_criticisms", []))
        for lesson in debate_record.get("lessons_learned", []):
            existing_criticisms.add(lesson)
        profile["common_criticisms"] = list(existing_criticisms)[-MAX_CRITICISMS_IN_PROFILE:]
    else:
        # Store as "disputed" rather than "accepted"
        disputed = profile.setdefault("disputed_claims", [])
        for lesson in debate_record.get("lessons_learned", []):
            entry = f"[{debate_record.get('trade_date', '?')}] {lesson} (resolution: {resolution})"
            if entry not in disputed:
                disputed.append(entry)
        profile["disputed_claims"] = disputed[-MAX_CRITICISMS_IN_PROFILE:]

    # ---- 2. Factor weight adjustments (only if user persuaded agent) ----
    if user_persuaded:
        cal_delta = debate_record.get("calibration_delta", {})
        factor_deltas = cal_delta.get("factor_weights", {})
        if factor_deltas:
            factors: dict[str, int] = dict(profile.get("preferred_factors", {}))
            for factor, delta_str in factor_deltas.items():
                # Parse delta — skip "tentative" markers
                clean = delta_str.split("(")[0].strip()
                try:
                    delta = int(clean)
                except (ValueError, TypeError):
                    delta = 0
                current = factors.get(factor, 3)
                factors[factor] = max(1, min(5, current + delta))
            profile["preferred_factors"] = factors

    # ---- 3. Direction bias tracking (OBJECTIVE — don't assume user is right) ----
    user_dir = debate_record.get("user_feedback", {}).get("direction", "")
    is_user_more_bearish = any(
        w in user_dir.lower()
        for w in ("conservative", "bearish", "看空", "偏保守", "disagree", "不同意")
    )
    if user_persuaded and is_user_more_bearish:
        # User's bearish view was VALIDATED by debate → agent may have bullish bias
        cal = profile.setdefault("direction_calibration", {})
        cal["bullish_bias_detected"] = True
        prev_note = cal.get("adjustment_note", "")
        if "下调乐观程度" not in prev_note:
            cal["adjustment_note"] = prev_note + (
                "用户历史上比agent更偏保守/看空且被辩论验证为正确，综合判断时应考虑下调乐观程度。"
                if not prev_note
                else ""
            )
    elif not user_persuaded and is_user_more_bearish:
        # User was bearish but agent maintained position — record the disagreement
        # but don't assume agent is biased
        cal = profile.setdefault("direction_calibration", {})
        cal["user_agent_disagreement_count"] = cal.get("user_agent_disagreement_count", 0) + 1
        # Only flag bias if user has been RIGHT multiple times (not just disagreed)
        if cal.get("user_agent_disagreement_count", 0) >= 3:
            cal["bullish_bias_detected"] = True
            cal["adjustment_note"] = cal.get("adjustment_note", "") or (
                "用户多次与agent在方向上产生分歧。Agent应考虑检查是否存在系统性偏差。"
            )

    # ---- 4. Variety-specific knowledge (always store, but tag with resolution) ----
    variety_specific = profile.setdefault("variety_specific", {})
    if variety not in variety_specific:
        variety_specific[variety] = {}
    missing = debate_record.get("user_feedback", {}).get("missing_factors", [])
    if missing:
        existing_extras = set(variety_specific[variety].get("key_extra_factors", []))
        for m in missing:
            existing_extras.add(m)
        variety_specific[variety]["key_extra_factors"] = list(existing_extras)[-10:]

    # ---- 5. Analysis style inference ----
    # Simple heuristic: if user mentions 基本面 many times, tag style
    all_text = str(debate_record.get("user_feedback", {}))
    all_text += " ".join(
        r.get("content", "")
        for r in debate_record.get("debate_rounds", [])
        if r.get("speaker") == "user"
    )
    if "基本面" in all_text and "技术面" not in all_text:
        profile["analysis_style"] = "基本面为主，轻技术面"
    elif "技术面" in all_text and "基本面" not in all_text:
        profile["analysis_style"] = "技术面为主"
    elif "基本面" in all_text and "技术面" in all_text:
        profile["analysis_style"] = "基本面+技术面综合"

    # ---- Persist ----
    save_user_profile(profile)  # 【调用函数】把更新后的画像原子写盘
    logger.info(
        "Updated user profile: %d criticisms, %d factors, style=%s",
        len(profile.get("common_criticisms", [])),
        len(profile.get("preferred_factors", {})),
        profile.get("analysis_style", ""),
    )
    return profile


# ---------------------------------------------------------------------------
# Convenience: build a debate record from raw feedback
# ---------------------------------------------------------------------------


# 【功能】把交互过程中收集的原始反馈组装成规范的辩论记录 dict。
# 【参数】variety: 品种代码;trade_date: 交易日期;agent_conclusion: agent 结论 dict;
#        user_direction: 用户方向选择文本;user_factors_text: 用户认同/不认同点;
#        user_missing_text: 用户补充的缺失因素;debate_rounds: 辩论轮次列表;
#        resolution: 结局标记;lessons: 经验教训列表。
# 【返回】dict:符合 save_debate/update_profile_from_debate 期望的规范结构。
# 【关键】按关键词解析一致度(full/disagree/partial);按行拆分认同点/不认同点/缺失因素。
def build_debate_record(
    variety: str,
    trade_date: str,
    agent_conclusion: dict[str, Any],
    user_direction: str,
    user_factors_text: str,
    user_missing_text: str,
    debate_rounds: list[dict[str, str]],
    resolution: str,
    lessons: list[str],
) -> dict[str, Any]:
    """Build a well-structured debate record dict.

    All the raw feedback strings collected during the interactive session
    are assembled into the canonical schema expected by ``save_debate()``
    and ``update_profile_from_debate()``.
    """
    # Parse agreement level from direction choice
    if "完全认同" in user_direction:
        agreement = "full"
    elif "不完全" in user_direction or "完全不同意" in user_direction:
        agreement = "disagree"
    elif "偏保守" in user_direction or "偏激进" in user_direction:
        agreement = "partial"
    else:
        agreement = "partial"

    # Split multi-line text into lists
    agreed_pts = (
        [p.strip() for p in user_factors_text.split("\n") if "认同" in p or "准确" in p]
        if user_factors_text
        else []
    )
    disagreed_pts = (
        [
            p.strip()
            for p in user_factors_text.split("\n")
            if "低估" in p or "高估" in p or "不足" in p
        ]
        if user_factors_text
        else []
    )
    missing_factors = (
        [m.strip() for m in user_missing_text.split("\n") if m.strip()] if user_missing_text else []
    )

    return {
        "id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "variety": variety.upper(),
        "agent_conclusion": agent_conclusion,
        "user_feedback": {
            "direction": user_direction,
            "agreement_level": agreement,
            "agreed_points": agreed_pts,
            "disagreed_points": disagreed_pts,
            "missing_factors": missing_factors,
            "full_text": user_factors_text + "\n---\n" + user_missing_text,
        },
        "debate_rounds": debate_rounds,
        "resolution": resolution,
        "lessons_learned": lessons,
        "calibration_delta": {
            "factor_weights": {},
            "direction_bias": "",
        },
    }


# ---------------------------------------------------------------------------
# Module self-test (run directly: python -m tradingagents.dataflows.evolution_memory)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Evolution Memory Self-Test ===\n")

    test_variety = "TEST"

    # 1. Load (should be empty on first run)
    debates = load_debates(test_variety)
    print(f"1. Load '{test_variety}' debates: {len(debates)} entries (expect 0)")

    # 2. Save a debate
    record = build_debate_record(
        variety=test_variety,
        trade_date="2026-07-17",
        agent_conclusion={
            "direction": "震荡偏多",
            "weights": {"tech": 2, "fund": 4, "macro": 4},
            "summary": "测试结论",
        },
        user_direction="偏保守",
        user_factors_text="库存分析准确\n低估了出口数据影响",
        user_missing_text="出口退税政策变化",
        debate_rounds=[
            {"speaker": "agent", "content": "请问您对方向判断有何看法？"},
            {"speaker": "user", "content": "偏保守，出口数据在恶化"},
            {"speaker": "agent", "content": "您说得对，我没有充分考虑出口端"},
        ],
        resolution="agent_acknowledged_user_point",
        lessons=["出口数据权重应提升", "用户掌握更及时的贸易信息"],
    )
    save_debate(test_variety, record)
    print(f"2. Saved debate for '{test_variety}'")

    # 3. Load again
    debates = load_debates(test_variety)
    print(f"3. Load '{test_variety}' debates: {len(debates)} entries (expect 1)")
    if debates:
        print(f"   Resolution: {debates[0]['resolution']}")
        print(f"   Lessons: {debates[0]['lessons_learned']}")

    # 4. Update user profile
    profile = update_profile_from_debate(test_variety, debates[0])
    print(f"4. Profile criticisms: {len(profile['common_criticisms'])} (expect 2)")
    print(f"   Style: '{profile['analysis_style']}'")

    # 5. Get evolution context
    ctx = get_evolution_context(test_variety)
    print(f"5. Evolution context length: {len(ctx)} chars (expect > 100)")
    print(f"   Preview: {ctx[:120]}...")

    # 6. Cleanup test data
    import sys  # 【调用包】读取命令行参数(--keep 保留测试数据)

    if "--keep" not in sys.argv:
        test_file = _ensure_dir() / f"{test_variety}.json"
        test_file.unlink(missing_ok=True)
        prof_file = _ensure_dir() / "user_profile.json"
        prof_file.unlink(missing_ok=True)
        print("\n6. Cleaned up test files (use --keep to preserve)")

    print("\n=== All tests passed ===")


# ==============================================================================
# Deferred Outcome Resolution (v2.3.2)
# ==============================================================================
# Mirrors the original TradingAgents' Memory Log pattern: store a structured
# prediction at analysis time, then resolve it on the next same-variety run
# by fetching actual price data and generating an AI reflection.
# ==============================================================================


# 【功能】存储一次结构化的方向预测,供延迟回测使用。
# 【参数】variety: 品种代码;trade_date: 分析日期(YYYY-MM-DD);
#        rating: 方向评级(强烈看多/偏多/中性/偏空/强烈看空);
#        confidence: 置信度(高/中/低);score: 0-10 数值评分;
#        key_levels: 关键支撑/阻力位文本(可选)。
# 【返回】无。
# 【关键】仅保留最近 20 条预测;任何异常被捕获并记日志,不影响主流程。
def store_prediction(
    variety: str,
    trade_date: str,
    rating: str,
    confidence: str,
    score: int,
    key_levels: str = "",
) -> None:
    """Store a structured prediction for deferred backtesting.

    Called after each analysis completes. On the NEXT run for the same variety,
    resolve_past_predictions() fetches actual price data and generates a
    reflection comparing prediction vs reality.

    Args:
        variety: Variety code (e.g. "RB").
        trade_date: Analysis date in YYYY-MM-DD.
        rating: One of 强烈看多/偏多/中性/偏空/强烈看空.
        confidence: 高/中/低.
        score: 0-10 numeric score.
        key_levels: Optional key support/resistance levels string.
    """
    try:
        filepath = _ensure_dir() / f"{variety.upper()}_predictions.json"

        # Load existing predictions
        predictions: list[dict] = []
        if filepath.exists():
            try:
                predictions = json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                predictions = []

        # Add new prediction
        predictions.append(
            {
                "trade_date": trade_date,
                "rating": rating,
                "confidence": confidence,
                "score": score,
                "key_levels": key_levels,
                "stored_at": datetime.now(timezone.utc).isoformat(),
                "resolved": False,
            }
        )

        # Keep last 20 predictions
        predictions = predictions[-20:]

        filepath.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "Stored prediction for %s on %s: %s (score=%d)", variety, trade_date, rating, score
        )

    except Exception as e:
        logger.warning("Failed to store prediction for %s: %s", variety, e)


# 【功能】结算该品种所有未解析的历史预测,对照实际价格生成 AI 反思文本。
# 【参数】variety: 品种代码;symbol: 同品种代码(传给行情接口)。
# 【返回】str:反思文本(供注入进化上下文);无未解析预测或取不到行情时返回空串。
# 【关键】1) 自 trade_date 向后看 14 天,取 10 个交易日内最新收盘价比较方向;
#        2) 涨跌 >0.2% 判定 CORRECT/WRONG,否则 FLAT(方向不明);
#        3) 已结算的预测标记 resolved/resolved_at 并写回文件。
def resolve_past_predictions(variety: str, symbol: str) -> str:
    """Resolve any unresolved past predictions for this variety.

    Fetches actual price data since each prediction date, compares direction,
    and generates an AI reflection on what was correct/wrong.

    Returns a reflection string for injection into the evolution context,
    or empty string if no unresolved predictions exist.

    Args:
        variety: Variety code (e.g. "RB").
        symbol: The same variety code, passed to price fetcher.
    """
    filepath = _ensure_dir() / f"{variety.upper()}_predictions.json"
    if not filepath.exists():
        return ""

    try:
        predictions = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    unresolved = [p for p in predictions if not p.get("resolved", False)]
    if not unresolved:
        return ""

    # Try to fetch price data for resolution
    try:
        from tradingagents.dataflows.commodity_futures import get_futures_price  # 【调用包】行情接口(拉取实际价格用于结算;导入失败则放弃结算)
    except ImportError:
        return ""

    reflections: list[str] = []
    newly_resolved: list[int] = []

    for i, pred in enumerate(unresolved):
        trade_date = pred["trade_date"]
        rating = pred["rating"]
        score = pred["score"]

        # Fetch price data from trade_date to now
        try:
            from datetime import datetime as dt, timedelta  # 【调用包】日期解析与推算(预测结算的 14 天窗口)

            target_dt = dt.strptime(trade_date, "%Y-%m-%d")
            end_dt = target_dt + timedelta(days=14)  # Look ahead 2 weeks
            price_result = get_futures_price(symbol, trade_date, end_dt.strftime("%Y-%m-%d"))  # 【调用函数】跨模块拉取 trade_date 起14天实际行情(用于结算)
        except Exception:
            continue

        if price_result.startswith("NO_DATA") or price_result.startswith("DATA_ERROR"):
            continue

        # Parse closes
        lines = price_result.strip().split("\n")
        closes = []
        for line in lines:
            if not line or line.startswith("#") or not line[0].isdigit():
                continue
            parts = line.split(",")
            if len(parts) >= 5:
                closes.append((parts[0], float(parts[4])))

        if len(closes) < 2:
            continue

        entry_close = closes[0][1]
        # Find the furthest available close within 10 trading days
        last_close = closes[min(len(closes) - 1, 10)][1]
        pct_change = (last_close - entry_close) / entry_close * 100

        # Determine if prediction was correct
        is_bullish = "多" in rating and "空" not in rating
        is_bearish = "空" in rating and "多" not in rating
        price_up = pct_change > 0.2
        price_down = pct_change < -0.2

        if is_bullish and price_up or is_bearish and price_down:
            outcome = "CORRECT"
        elif abs(pct_change) <= 0.2:
            outcome = "FLAT (方向不明)"
        else:
            outcome = "WRONG"

        reflection = (
            f"[{trade_date}] 预测: {rating}(信心={pred['confidence']}, 分数={score}/10) → "
            f"实际: {pct_change:+.2f}% → {outcome}"
        )

        # Add key levels context
        if pred.get("key_levels"):
            reflection += f"\n  当时关键价位: {pred['key_levels'][:200]}"

        reflections.append(reflection)
        newly_resolved.append(i)

    # Mark resolved
    for i in newly_resolved:
        unresolved[i]["resolved"] = True
        unresolved[i]["resolved_at"] = datetime.now(timezone.utc).isoformat()

    # Write back
    with contextlib.suppress(OSError):
        filepath.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")

    if not reflections:
        return ""

    parts = [
        "### 历史预测回测 (Deferred Outcome Resolution)",
        "以下是系统在上次分析中的预测 vs 实际走势：",
        "",
    ]
    parts.extend(reflections)
    parts.append("")
    parts.append(
        "**请反思**: 上次预测中准确和错误的部分分别是什么原因？当前分析是否需要调整判断框架？"
    )
    parts.append("")

    return "\n".join(parts)


# 【功能】获取结果反思文本(供 get_evolution_context 调用)。
# 【参数】variety: 品种代码。
# 【返回】str:反思文本或空串。
# 【关键】直接转发 resolve_past_predictions(variety, variety),品种与行情代码相同。
def get_outcome_reflection(variety: str) -> str:
    """Get the outcome reflection for injection into evolution context.

    Shortcut function called by get_evolution_context().
    """
    return resolve_past_predictions(variety, variety)
