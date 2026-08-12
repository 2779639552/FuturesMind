"""Append-only markdown decision log for TradingAgents."""

import re  # 【调用包】正则:编译 DECISION/REFLECTION 段匹配模式
from pathlib import Path  # 【调用包】路径对象:处理日志文件路径与 .tmp 临时文件

from tradingagents.agents.utils.rating import parse_rating  # 【调用包】五档评级解析:从决策文本提取评级写入日志标签


# 【功能】交易决策与反思的"只追加"markdown 日志:每次决策写一条,事后回填结果并追加反思。
class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"  # 【变量】条目硬分隔符(HTML 注释形式,LLM 正文不会产生,可安全切分)
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)  # 【变量】预编译 DECISION 段匹配(避免每次 load 重复编译)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)  # 【变量】预编译 REFLECTION 段匹配

    # 【功能】构造记忆日志;按配置初始化日志文件路径与条目上限。
    # 【参数】config: 配置字典,可含 memory_log_path(日志路径)与 memory_log_max_entries(已解决条目上限)。
    # 【关键】路径不存在时自动创建父目录;无路径则后续写入全部为空操作。
    def __init__(self, config: dict = None):
        cfg = config or {}  # 【变量】配置字典(允许传入 None)
        self._log_path = None  # 【变量】日志文件路径(Path 对象);未配置时为 None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()  # 【调用函数】展开 ~ 为用户目录后转为 Path
            self._log_path.parent.mkdir(parents=True, exist_ok=True)  # 【调用函数】确保日志目录存在(不存在则创建)
        # Optional cap on resolved entries. None disables rotation.
        self._max_entries = cfg.get("memory_log_max_entries")  # 【变量】已解决条目的数量上限,超过则淘汰最旧;None=不轮转

    # --- Write path (Phase A) ---

    # 【功能】在 propagate() 末尾追加一条"待定"决策记录(不做 LLM 调用)。
    # 【参数】ticker: 标的代码;trade_date: 交易日期;final_trade_decision: 最终决策文本。
    # 【关键】幂等保护:先做快速原始文本扫描,若同日同标的已存在 pending 记录则跳过,避免重复追加。
    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        if not self._log_path:
            return
        # Idempotency guard: fast raw-text scan instead of full parse
        if self._log_path.exists():
            raw = self._log_path.read_text(encoding="utf-8")  # 【变量】日志全文(用于幂等扫描)
            for line in raw.splitlines():
                if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("| pending]"):
                    return
        rating = parse_rating(final_trade_decision)  # 【调用函数】从决策文本启发式解析五档评级
        tag = f"[{trade_date} | {ticker} | {rating} | pending]"  # 【变量】条目标签:日期|代码|评级|状态,作为条目首行
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Read path (Phase A) ---

    # 【功能】解析日志全部条目为字典列表。
    # 【返回】条目字典列表(含标签字段 + DECISION/REFLECTION 正文);无日志文件时返回空列表。
    def load_entries(self) -> list[dict]:
        """Parse all entries from log. Returns list of dicts."""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")  # 【变量】日志全文
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]  # 【变量】按硬分隔符切出的原始条目块
        entries = []  # 【变量】解析后的条目字典列表
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    # 【功能】返回所有"结果待定"(outcome=pending)的条目,供 Phase B 回填。
    # 【返回】仅含 pending 条目的列表。
    def get_pending_entries(self) -> list[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    # 【功能】生成"历史上下文"文本,供注入代理提示词(同标的决策 + 跨标的教训)。
    # 【参数】ticker: 当前标的;n_same: 最多取同标的已解决条目数(默认 5);n_cross: 最多取跨标的条目数(默认 3)。
    # 【返回】格式化提示词上下文;无历史时返回空串。
    # 【关键】按最新优先遍历;同标的用完整条目,跨标的只取反思片段。
    def get_past_context(self, ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
        """Return formatted past context string for agent prompt injection."""
        entries = [e for e in self.load_entries() if not e.get("pending")]  # 【变量】仅取已解决(非 pending)条目
        if not entries:
            return ""

        same, cross = [], []  # 【变量】同标的 / 跨标的历史条目容器
        for e in reversed(entries):  # 从最新往旧遍历
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []  # 【变量】最终上下文段落列表
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    # 【功能】把第一条匹配的 pending 记录更新为已解决:替换标签填入收益并追加 REFLECTION 段。
    # 【参数】ticker/trade_date: 定位记录的键;raw_return/alpha_return: 原始收益与超额收益(小数);
    #        holding_days: 持有天数;reflection: 反思文本。
    # 【关键】用临时文件 + os.replace() 原子写,避免写一半崩溃损坏日志。
    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
    ) -> None:
        """Replace pending tag and append REFLECTION section using atomic write.

        Finds the first pending entry matching (trade_date, ticker), updates
        its tag with return figures, and appends a REFLECTION section.  Uses
        a temp-file + os.replace() so a crash mid-write never corrupts the log.
        """
        if not self._log_path or not self._log_path.exists():
            return

        text = self._log_path.read_text(encoding="utf-8")  # 【变量】日志全文
        blocks = text.split(self._SEPARATOR)  # 【变量】按硬分隔符切出的条目块

        pending_prefix = f"[{trade_date} | {ticker} |"  # 【变量】待更新条目的标签前缀(日期|代码|)
        raw_pct = f"{raw_return:+.1%}"  # 【变量】原始收益率百分比(带符号,1 位小数)
        alpha_pct = f"{alpha_return:+.1%}"  # 【变量】超额收益(alpha)百分比

        updated = False  # 【变量】是否已更新到目标记录的标志
        new_blocks = []  # 【变量】重写后的条目块列表
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()  # 【变量】条目首行标签(形如 [日期|代码|评级|pending])

            if (
                not updated
                and tag_line.startswith(pending_prefix)
                and tag_line.endswith("| pending]")
            ):
                # Parse rating from the existing pending tag
                fields = [f.strip() for f in tag_line[1:-1].split("|")]  # 【变量】按 | 拆分标签字段(去掉首尾方括号)
                rating = fields[2]  # 【变量】沿用原 pending 标签中的评级
                new_tag = (
                    f"[{trade_date} | {ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {holding_days}d]"
                )
                rest = "\n".join(lines[1:])
                new_blocks.append(f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}")
                updated = True
            else:
                new_blocks.append(block)

        if not updated:
            return

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")  # 【变量】同目录临时文件路径(如 memory_log.md.tmp)
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)  # 【调用函数】原子替换:用临时文件覆盖日志,防止写入中断损坏

    # 【功能】一次读取 + 一次原子写内批量应用多条结果更新(避免逐条读改写)。
    # 【参数】updates: 字典列表,每项必须含 ticker/trade_date/raw_return/alpha_return/holding_days/reflection。
    # 【关键】以 (trade_date, ticker) 为键建查找表,把每条 pending 记录 O(1) 分发到对应更新。
    def batch_update_with_outcomes(self, updates: list[dict]) -> None:
        """Apply multiple outcome updates in a single read + atomic write.

        Each element of updates must have keys: ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection.
        """
        if not self._log_path or not self._log_path.exists() or not updates:
            return

        text = self._log_path.read_text(encoding="utf-8")  # 【变量】日志全文
        blocks = text.split(self._SEPARATOR)  # 【变量】按硬分隔符切出的条目块

        # Build lookup keyed by (trade_date, ticker) for O(1) dispatch
        update_map = {(u["trade_date"], u["ticker"]): u for u in updates}  # 【变量】(日期,代码)→更新项 的查找表

        new_blocks = []  # 【变量】重写后的条目块列表
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            matched = False  # 【变量】当前块是否被某条更新匹配到的标志
            for (trade_date, ticker), upd in list(update_map.items()):
                pending_prefix = f"[{trade_date} | {ticker} |"
                if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]  # 【变量】沿用原 pending 标签中的评级
                    raw_pct = f"{upd['raw_return']:+.1%}"
                    alpha_pct = f"{upd['alpha_return']:+.1%}"
                    new_tag = (
                        f"[{trade_date} | {ticker} | {rating}"
                        f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                    )
                    del update_map[(trade_date, ticker)]  # 已消费该更新,避免重复匹配
                    matched = True
                    break

            if not matched:
                new_blocks.append(block)

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")  # 【变量】同目录临时文件路径
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)  # 【调用函数】原子替换:用临时文件覆盖日志,防止写入中断损坏

    # --- Helpers ---

    # 【功能】当已解决条目数超过上限时,淘汰最旧的已解决条目(轮转)。
    # 【参数】blocks: 全部条目块列表。
    # 【返回】轮转后的条目块列表;pending 块永不淘汰,未超上限或禁用时原样返回。
    def _apply_rotation(self, blocks: list[str]) -> list[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []  # 【变量】[(条目块, 是否已解决)] 二元组列表
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (  # 【变量】已解决判定:标签完整且不以 "| pending]" 结尾
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)  # 【变量】已解决条目总数
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries  # 【变量】需淘汰的最旧已解决条目数
        kept: list[str] = []  # 【变量】轮转后保留的条目块
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    # 【功能】把单条原始块解析为条目字典。
    # 【参数】raw: 分隔符切出的原始条目文本。
    # 【返回】条目字典(标签字段 + decision/reflection);标签不合法或字段过少时返回 None。
    def _parse_entry(self, raw: str) -> dict | None:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]  # 【变量】标签字段列表(去掉首尾方括号)
        if len(fields) < 4:
            return None
        entry = {  # 【变量】解析出的条目字典:标签字段 + DECISION/REFLECTION 正文
            "date": fields[0],  # 【变量】交易日期
            "ticker": fields[1],  # 【变量】标的代码
            "rating": fields[2],  # 【变量】评级
            "pending": fields[3] == "pending",  # 【变量】是否为待定状态
            "raw": fields[3] if fields[3] != "pending" else None,  # 【变量】原始收益率百分比文本(无则 None)
            "alpha": fields[4] if len(fields) > 4 else None,  # 【变量】超额收益百分比文本(无则 None)
            "holding": fields[5] if len(fields) > 5 else None,  # 【变量】持有天数文本(无则 None)
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""  # 【变量】DECISION 段正文
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""  # 【变量】REFLECTION 段正文
        return entry

    # 【功能】把已解决条目格式化为完整文本(标签 + DECISION + 可选 REFLECTION),用于同标的上下文。
    # 【参数】e: 条目字典。
    # 【返回】markdown 风格的条目文本。
    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"  # 【变量】原始收益率展示值(缺失显示 n/a)
        alpha = e["alpha"] or "n/a"  # 【变量】超额收益展示值(缺失显示 n/a)
        holding = e["holding"] or "n/a"  # 【变量】持有天数展示值(缺失显示 n/a)
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    # 【功能】把条目格式化为"仅反思/决策摘要"文本,用于跨标的上下文(更省 token)。
    # 【参数】e: 条目字典。
    # 【返回】标签 + 反思(有则),否则截断到 300 字符的决策摘要。
    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]  # 【变量】决策正文截断到 300 字符
        suffix = "..." if len(e["decision"]) > 300 else ""  # 【变量】截断省略号(超长时补 "...")
        return f"{tag}\n{text}{suffix}"
