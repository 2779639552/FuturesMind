# -*- coding: utf-8 -*-
r"""交叉验证：这三个号是"这几篇没进合集"，还是"整号没建合集"？

方法：换**该号的其他文章**（来自七禾网等转载站标注的原文链接）再测一遍，
      并用已知有合集的「多空逻辑」做对照组（换一篇，避开上次撞上的限流）。

判据（只看页面里的真实数据，不看 JS 引用）：
  · `var album_info_list = [  ]` 为空、且无 `public_tag_info:{tags:[...]}` 数据块
    ⇒ 该文确实不属于任何合集。
  · 多篇都如此 ⇒ 该号大概率没建合集（≠证明，见 skill 能力上限）。
只读；每请求间隔 6s 防限流。
"""
import importlib.util
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wac_x", ROOT / "wechat_album_collector.py")
wac = importlib.util.module_from_spec(spec)
sys.modules["wac_x"] = wac
spec.loader.exec_module(wac)

CASES = [
    ("对照·多空逻辑(已知2合集)", "http://mp.weixin.qq.com/s?__biz=MzIzNTQ3MDExMg==&mid=2247485798"
                                "&idx=1&sn=989806e0f9cd5773f9bae8d4800a87b9#rd"),
    ("混沌天成研究·棉花调研", "https://mp.weixin.qq.com/s/7outhEuF2CBOIbvBbDUyDA"),
    ("中信建投·疆棉调研", "https://mp.weixin.qq.com/s/RBxRNbrCJGLLr2yu9bnMHw"),
    ("中信建投·云南工业硅调研", "https://mp.weixin.qq.com/s/eOreGmdIIz0-slreZ-w5rQ"),
    ("中信期货·多晶硅调研", "https://mp.weixin.qq.com/s/AXGJQVoF-HNtO7ZEwasZkg"),
]

print("(先等 15s 避开上一轮触发的限流)\n")
time.sleep(15)

for label, url in CASES:
    print("=" * 80)
    print("▸ %s" % label)
    try:
        body, how = wac.get_html(url)
    except Exception as e:
        print("  ✗ 取页异常: %s" % e)
        continue
    if not body:
        print("  ✗ 取页为空")
        continue
    print("  how=%s len=%d" % (how, len(body)))

    m = re.search(r'id="js_name"[^>]*>\s*([^<]{1,40}?)\s*<', body)
    print("  号名(js_name) = %s" % (m.group(1) if m else "—"))

    # album_info_list 的**字面量内容**（真实数据）
    m2 = re.search(r"var\s+album_info_list\s*=\s*\[(.*?)\]", body, re.S)
    if m2:
        inner = m2.group(1).strip()
        print("  album_info_list 字面量: %s" % (("空数组 []" if not inner else inner[:200])))
    else:
        print("  album_info_list: 未找到该变量")

    # 数据定义形态的 public_tag_info
    defs = list(re.finditer(r"public_tag_info\s*:\s*\{", body))
    with_tag = [d for d in defs if "tag_name" in wac.brace_block(body, d.end() - 1)]
    print("  public_tag_info 数据块 %d 个（其中含 tag_name 的 %d 个）" % (len(defs), len(with_tag)))
    print("  ★ albums_of_page() = %s" % (wac.albums_of_page(body) or "[]"))
    time.sleep(6)

print("\n" + "=" * 80)
print("判读：对照组应能解析出合集；若三号多篇文章的 album_info_list 均为空数组")
print("      ⇒ 这三个号的文章不带合集归属（≠ 已证明该号没建合集，但通道无法使用）")
