"""原子 JSON 落盘 + 自愈读取（2026-10-23）。

【为什么需要这个模块】
2026-10-22 18:30 与 10-23 08:30，公众号读书通道**连续两轮**采集以"退出码 1"失败，
结果文件一直停在前一天。人工复跑 86 秒、26 篇、退出码 0 —— 数据源和凭据全都正常。

根因是 `_save_json` 用 `open(p, "w")`：**先把文件截断为 0 字节再写内容**。
「续期写 cookies → 立刻读 cookies」这条路径上，只要另一个进程/线程在截断窗口内读到，
就是空文件 ⇒ `json.loads` 抛 `JSONDecodeError: Expecting value: line 1 column 1`
⇒ 未捕获 ⇒ 进程以退出码 1 结束 ⇒ 结果文件不写 ⇒ 调度器读到旧 `new`。

这类缺陷的恶毒之处：**写方不报错，坏的是读方**。所以光"改成原子写"不够 ——
历史上已经损坏的文件、外部工具写入、旧版本进程留下的半截文件，都还要能被救回来。

【本模块给两件事】
  `save_json()` 原子写 + 保留上一版 `.bak`
      → 读方永远看到完整内容；且任何时刻都有一个可回滚的好版本
  `load_json()`  自愈读取：主文件坏 → 自动回滚 `.bak` → 把主文件修好
      → 不需要人工删文件，也不需要重新扫码/重登

【什么文件该用】
  跨进程/跨轮次读写的**状态文件**（cookie、水位线、采集结果、注册表）。
  纯导出文件（给人看的 json 报表）不需要，别滥用。
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_MISSING = object()
BAK_SUFFIX = ".bak"


def _atomic_write(p: Path, payload: bytes) -> None:
    """写临时文件 → fsync → 原子替换。失败清掉临时文件。"""
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        with io.open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())  # 落盘再替换：进程被杀也不留半截
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def save_json(
    path: str | Path,
    obj: Any,
    *,
    indent: int = 2,
    backup: bool = True,
) -> None:
    """原子写 JSON，并把上一版留成 `<name>.bak`。

    【顺序很重要】先备份旧文件、再写新文件。反过来的话备份到的就是新内容，
    等于没有可回滚的版本。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=indent).encode("utf-8")

    if backup and p.exists():
        try:
            _atomic_write(Path(str(p) + BAK_SUFFIX), p.read_bytes())
        except OSError as exc:
            # 备份失败不阻断主写入：宁可少一份回滚，也不能让状态写不进去
            logger.debug("备份 %s 失败（继续写主文件）: %s", p, exc)

    _atomic_write(p, data)


def load_json(
    path: str | Path,
    default: Any = _MISSING,
    *,
    heal: bool = True,
    on_heal: Callable[[Path, str], None] | None = None,
) -> Any:
    """读 JSON；主文件坏了就回滚 `.bak` 并顺手把主文件修好。

    【返回】
      成功 → 解析出的对象（可能来自主文件，也可能来自 `.bak`）
      失败且给了 default → default
      失败且没给 default → 抛原异常
    【on_heal】自愈发生时回调 `(path, reason)`，供调用方记告警/埋点。
    """
    p = Path(path)
    bak = Path(str(p) + BAK_SUFFIX)

    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        err: Exception = FileNotFoundError(f"无此文件: {p}")
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        err = exc

    # ── 主文件读不出来，尝试回滚 ──
    if heal and bak.exists():
        try:
            value = json.loads(bak.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            value = _MISSING
        if value is not _MISSING:
            reason = f"{type(err).__name__}: {str(err)[:120]}"
            logger.warning("状态文件损坏，已从 .bak 回滚：%s（%s）", p, reason)
            if p.exists():
                # 把坏文件留档，别直接覆盖 —— 万一 .bak 也不对，还有证据可查
                stamp = time.strftime("%Y%m%d%H%M%S")
                try:
                    os.replace(p, Path(f"{p}.corrupt-{stamp}"))
                except OSError:
                    pass
            try:
                save_json(p, value, backup=False)  # 修复主文件；不再滚 .bak，避免自我覆盖
            except OSError as exc:
                logger.warning("回滚后修复主文件失败（内存里的值仍可用）: %s", exc)
            if on_heal:
                try:
                    on_heal(p, reason)
                except Exception:  # noqa: BLE001 - 回调不许影响读取
                    pass
            return value

    if default is not _MISSING:
        return default
    raise err
