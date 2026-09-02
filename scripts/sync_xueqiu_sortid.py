"""把 xueqiu search sortId=1 → sortId=2 同步到项目内思路2 运行目录（2026-08-26）。

运行目录 platforms/xueqiu_adapter.py 的 search URL 曾硬编码 sortId=1(相关性排序)，
实测返回 2021-2023 旧帖 → 每日管道 --since 7天 窗口过滤成 0 条 → xueqiu 每日恒 0。
仓库副本已修(sortId=2 时间排序返回当天帖),这里给运行目录打同样的最小补丁。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from path_utils import resolve_think2_dir  # noqa: E402

target = resolve_think2_dir() / "platforms" / "xueqiu_adapter.py"
txt = target.read_text(encoding="utf-8")

old_url = "sortId=1"
n = txt.count(old_url)
if n == 0:
    print("未发现 sortId=1,已是最新,无需修改")
    raise SystemExit(0)

# 只替换 search URL 里的那一处(加注释说明,不碰其他 sortId 出现)
old_line = "const url = '/query/v1/search/status.json?sortId=1&q={q_enc}&count={per_page}&page={page}';"
if old_line in txt:
    new_line = (
        "        // 2026-08-26 sortId=2 时间排序(实测 sortId=1 相关性排序返回旧帖,"
        "每日 --since 窗口过滤成 0; sortId=2 返回当天帖)\n"
        "        const url = '/query/v1/search/status.json?sortId=2&q={q_enc}&count={per_page}&page={page}';"
    )
    txt = txt.replace(old_line, new_line, 1)
    target.write_text(txt, encoding="utf-8")
    print("OK: search URL sortId=1 → sortId=2 (含注释)")
else:
    # 退路:整体替换 sortId=1 → sortId=2
    txt = txt.replace(old_url, "sortId=2")
    target.write_text(txt, encoding="utf-8")
    print(f"OK: 整体替换 {n} 处 sortId=1 → sortId=2")
