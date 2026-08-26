"""一次性修复:给 batch_*.jsonl 里缺失 platform 字段的记录补上正确平台。

根因:早期采集器的小红书记录没写 platform 字段,下游 web_app.py 统计时
`d.get("platform", "?")` 兜底为 "?"(显示"第5平台"),且 /api/sentiment_posts
会把 platform=="?" 的记录直接跳过(数据丢失)。经全量验证,269 条缺失记录
的 url 域名 100% 为 xiaohongshu.com,与思路2 dedupe.py「旧数据默认 xhs」一致。

做法:
  1. 先把 output 下所有 batch_*.jsonl 复制到 backup_before_platform_fix/;
  2. 逐行读取,仅对 platform 键缺失(或为空)的记录按 url 域名推断平台,
     在 JSON 行开头插入 "platform": "<推断值>",其余字节保持原样;
  3. 打印修复统计供核对。

运行: cd AgentSense && venv/Scripts/python.exe scripts/fix_missing_platform.py
"""
import glob
import json
import shutil
import sys
from urllib.parse import urlparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from path_utils import resolve_think2_dir  # noqa: E402

OUT = resolve_think2_dir() / "output"
BACKUP = OUT / "backup_before_platform_fix"

# url 域名 -> 平台代码(与 web_app 现有 5 平台一致)
_DOMAIN_TO_PLATFORM = [
    ("xiaohongshu", "xhs"),
    ("weibo", "weibo"),
    ("zhihu", "zhihu"),
    ("xueqiu", "xueqiu"),
    ("eastmoney", "eastmoney_guba"),  # 2026-08-26 补:东财股吧域名 guba.eastmoney.com
]


def infer_platform(url: str) -> str:
    dom = urlparse(url or "").netloc.lower()
    for kw, plat in _DOMAIN_TO_PLATFORM:
        if kw in dom:
            return plat
    return "?"


def main():
    files = sorted(glob.glob(str(OUT / "batch_*.jsonl")))
    if not files:
        print("No batch files found under", OUT)
        return

    # 1) 备份
    BACKUP.mkdir(parents=True, exist_ok=True)
    for bf in files:
        shutil.copy2(bf, BACKUP / Path(bf).name)
    print(f"[backup] {len(files)} files -> {BACKUP}")

    fixed_total = 0
    for bf in files:
        with open(bf, encoding="utf-8") as f:
            raw_lines = f.read().split("\n")
        fixed_here = 0
        new_lines = []
        for line in raw_lines:
            if not line.strip():
                new_lines.append(line)
                continue
            d = json.loads(line)
            # 只补缺失/空 platform,已有值的跳过
            if d.get("platform") not in (None, ""):
                new_lines.append(line)
                continue
            plat = infer_platform(d.get("url", ""))
            if plat == "?":
                new_lines.append(line)  # 推断不出就保持原样
                continue
            # 在首个 "{" 后插入 platform 字段,其余字节不动
            fixed_lines = line.split("{", 1)
            new_lines.append('{' + f'"platform": "{plat}", ' + fixed_lines[1])
            fixed_here += 1
        if fixed_here:
            with open(bf, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
            fixed_total += fixed_here
            print(f"[fix] {Path(bf).name}: +{fixed_here}")

    print(f"\nTotal fixed: {fixed_total} (backup kept at {BACKUP})")


if __name__ == "__main__":
    main()
