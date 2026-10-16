# -*- coding: utf-8 -*-
"""把微信读书技能从 1.0.3 升级到 1.0.5（网关强制要求）。

为什么必须升：gateway 每个回包都带 `upgrade_info`，而 skill 文档的规则 1 明确写着
「必须立即暂停当前操作，按 upgrade_info.message 指引完成升级」——带着旧版本号请求
会持续触发该字段（实测 1.0.3 有、1.0.5 无）。

安装策略（**不破坏本地已有结构**）：
  · 本地布局 = 根 `SKILL.md` + `references/*.md`；官方 zip 是**扁平**的
  · 所以：官方各 `X.md` → 覆盖 `references/X.md`；根 SKILL.md 单独合成
  · 根 SKILL.md 的合成方式 = **保留本地 frontmatter 的扩展字段**
    （description_zh/_en、display_name、visibility 等，这些是技能市场发现用的），
    只把 `version` 提到 1.0.5，正文换成官方 1.0.5 的正文
  · 新增的 `SERVICE_PROFILE.md` 也放进去
  · `_icon.png` / `_skillhub_meta.json` 原样保留

安全审计：官方 zip 内**只有 9 个 .md，无任何脚本/可执行文件**（已 `unzip -l` 核对）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# ⚠️ Windows 下 Python 不认 Git Bash 的 /tmp（那是 MSYS 虚拟路径），必须用真实路径。
#    实测 `/tmp` 在 Git Bash 里 = C:\Users\<user>\AppData\Local\Temp
TEMP_WIN = Path.home() / "AppData" / "Local" / "Temp"
SRC_DIR = TEMP_WIN / "weread_skill_up" / "ex" / "weread-skills"
DST = Path.home() / ".workbuddy" / "skills" / "weread-skills"

NEW_VERSION = "1.0.5"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def split_front(text: str) -> tuple[list[str], str]:
    """返回 (frontmatter 行列表, 正文)。无 frontmatter 时前者为空。"""
    if not text.startswith("---"):
        return [], text
    end = text.index("\n---", 3)
    fm = text[3:end].strip("\n").splitlines()
    body = text[end + 4:]
    return fm, body


def main() -> int:
    if not SRC_DIR.is_dir():
        print(f"✘ 未找到官方解压目录：{SRC_DIR}")
        return 2
    if not DST.is_dir():
        print(f"✘ 未找到本地技能目录：{DST}")
        return 2

    print("=" * 74)
    print("安装微信读书技能升级 1.0.3 → 1.0.5")
    print("=" * 74)
    print(f"官方源: {SRC_DIR}")
    print(f"安装到: {DST}\n")

    # ── 1. 安全审计：确认没有可执行文件 ────────────────────────────
    odd = [p.name for p in SRC_DIR.rglob("*")
           if p.is_file() and p.suffix.lower() not in (".md", ".txt")]
    print("[1] 安全审计")
    print(f"    非 markdown 文件: {odd or '无 ✓'}")
    if odd:
        print("    ✘ 存在非文档文件，中止（需人工审查）")
        return 3

    # ── 2. references/*.md 覆盖 ────────────────────────────────────
    print("\n[2] 覆盖 references/*.md")
    ref_dir = DST / "references"
    ref_dir.mkdir(exist_ok=True)
    n = 0
    for f in sorted(SRC_DIR.glob("*.md")):
        if f.name == "SKILL.md":
            continue
        target = ref_dir / f.name
        old = target.stat().st_size if target.exists() else 0
        shutil.copy2(f, target)
        print(f"    {f.name:22s} {old:6d} → {f.stat().st_size:6d} bytes")
        n += 1
    print(f"    共 {n} 个文件")

    # ── 3. 合成根 SKILL.md（保留本地 frontmatter 扩展字段）─────────
    print("\n[3] 合成根 SKILL.md")
    local_fm, _ = split_front(read(DST / "SKILL.md"))
    _, new_body = split_front(read(SRC_DIR / "SKILL.md"))

    out_fm: list[str] = []
    saw_ver = False
    for line in local_fm:
        if line.strip().startswith("version:"):
            out_fm.append(f"version: {NEW_VERSION}")
            saw_ver = True
        else:
            out_fm.append(line)
    if not saw_ver:
        out_fm.append(f"version: {NEW_VERSION}")
    print(f"    frontmatter 保留 {len(out_fm)} 行（version → {NEW_VERSION}）")

    merged = "---\n" + "\n".join(out_fm) + "\n---\n" + new_body
    # 自检：正文里不能再出现旧版本号
    bad = merged.count("1.0.3")
    (DST / "SKILL.md").write_text(merged, encoding="utf-8", newline="")
    print(f"    写入 SKILL.md: {len(merged)} 字符；残留 '1.0.3' 出现 {bad} 次"
          + ("（✘ 需检查）" if bad else "（✓）"))

    # ── 4. SERVICE_PROFILE.md（官方新增）───────────────────────────
    #   官方 zip 是扁平布局，所以放**根目录**（与官方一致）；
    #   第 2 步的 glob 已顺带在 references/ 留了一份，两处都在也无害。
    sp = SRC_DIR / "SERVICE_PROFILE.md"
    if sp.exists():
        shutil.copy2(sp, DST / "SERVICE_PROFILE.md")
        print(f"\n[4] SERVICE_PROFILE.md → 根目录 ({sp.stat().st_size} bytes)")

    # ── 5. 收尾自检 ────────────────────────────────────────────────
    print("\n[5] 自检")
    import re
    root = read(DST / "SKILL.md")
    m = re.search(r"^version:\s*(\S+)", root, re.M)
    print(f"    根 SKILL.md version = {m.group(1) if m else '(未找到)'}")
    refs = sorted(p.name for p in ref_dir.glob("*.md"))
    print(f"    references/ = {refs}")
    print(f"    SERVICE_PROFILE.md 存在 = {(DST / 'SERVICE_PROFILE.md').exists()}")
    print(f"    _icon.png 保留 = {(DST / '_icon.png').exists()}")
    print(f"    _skillhub_meta.json 保留 = {(DST / '_skillhub_meta.json').exists()}")

    ok = (m and m.group(1) == NEW_VERSION and bad == 0
          and (DST / "_icon.png").exists() and (DST / "SERVICE_PROFILE.md").exists())
    print("\n" + ("✔ 升级完成" if ok else "✘ 升级有问题，请检查"))
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
