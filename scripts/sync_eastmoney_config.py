"""把 PLATFORM_KEYWORDS[eastmoney_guba] 同步到项目内思路2 运行目录的 config.py（2026-08-26）。

运行目录 config.py 的 PLATFORM_KEYWORDS 只有 xhs/weibo/zhihu,缺东财股吧关键词 → 前端更新
勾选东财时 config 里无关键词兜底。补上与仓库副本一致的 eastmoney_guba 键。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from path_utils import resolve_think2_dir  # noqa: E402

target = resolve_think2_dir() / "config.py"
txt = target.read_text(encoding="utf-8")

if '"eastmoney_guba": [' in txt:
    print("SKIP: 已存在 eastmoney_guba 键")
    raise SystemExit(0)

block = (
    '    "eastmoney_guba": [\n'
    '        # 东财股吧帖子普遍带"期货"后缀(与 batch_collect 同步),覆盖21品种池+稀疏品种池尾部\n'
    '        # 黑色系\n'
    '        "螺纹钢期货", "铁矿石期货", "焦炭期货", "焦煤期货", "热卷期货", "硅铁期货", "锰硅期货",\n'
    '        # 有色\n'
    '        "沪铜期货", "沪铝期货", "沪锌期货", "沪镍期货", "黄金期货", "白银期货",\n'
    '        "碳酸锂期货", "工业硅期货",\n'
    '        # 能化\n'
    '        "原油期货", "PTA期货", "甲醇期货", "玻璃期货", "PVC期货", "纯碱期货", "尿素期货",\n'
    '        "短纤期货", "苯乙烯期货", "乙二醇期货",\n'
    '        # 农产品\n'
    '        "豆粕期货", "菜粕期货", "棕榈油期货", "豆油期货", "白糖期货", "棉花期货",\n'
    '        "苹果期货", "红枣期货", "花生期货",\n'
    '        # 稀疏品种补长尾(不用"苹果"裸词避免撞Apple)\n'
    '        "苹果冷库", "苹果套袋", "红枣库存", "花生库存", "玻璃库存", "浮法玻璃", "尿素出口", "短纤报价",\n'
    '        # 通用\n'
    '        "期货实战", "期货交易心得", "期货技术分析", "商品期货",\n'
    '    ],\n'
)

# 定位 PLATFORM_KEYWORDS 字典的结束(值为列表,不含 }),在结束前插入
idx = txt.find("PLATFORM_KEYWORDS = {")
if idx == -1:
    print("MISS PLATFORM_KEYWORDS")
    raise SystemExit(1)
end = txt.find("\n}\n", idx)
if end == -1:
    print("MISS dict end")
    raise SystemExit(1)
txt = txt[:end] + "\n" + block + txt[end:]
target.write_text(txt, encoding="utf-8")
print("config.py eastmoney_guba 键已补入")
