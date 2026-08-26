"""把东财相关改动同步到桌面思路2 运行目录的 batch_collect.py（2026-08-26）。

web_app/scheduler 在桌面 THINK2_DIR 跑 batch_collect.py，桌面副本缺三处东财改造：
  1) GBK reconfigure guard —— 子进程 GBK 控制台打印 emoji 崩溃会掩盖真实错误；
  2) DEFAULT_KEYWORDS_EASTMONEY_GUBA 字典 —— 东财平台关键词(47 词)；
  3) DEFAULT_KEYWORDS 分发字典补 "eastmoney_guba" 项。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from path_utils import resolve_think2_dir  # noqa: E402

target = resolve_think2_dir() / "batch_collect.py"
txt = target.read_text(encoding="utf-8")
changed = []

# ---- 1) GBK guard ----
anchor = 'logger = logging.getLogger("batch.collect")\n'
guard = (
    'logger = logging.getLogger("batch.collect")\n'
    "\n"
    "# 2026-08-26 子进程在 GBK 控制台打印 emoji(⚠️) 会 UnicodeEncodeError 崩溃,掩盖真实错误。\n"
    "# 采集子进程(cwd=THINK2_DIR)由 scheduler/web_app 以 subprocess 拉起,stdout 为管道,\n"
    '# reconfigure(errors="replace") 把不可编码字符替换为 "?",保证日志可读、退出码真实。\n'
    'if hasattr(sys.stdout, "reconfigure"):\n'
    '    sys.stdout.reconfigure(errors="replace")\n'
    'if hasattr(sys.stderr, "reconfigure"):\n'
    '    sys.stderr.reconfigure(errors="replace")\n'
    "\n"
)
if anchor in txt and "reconfigure" not in txt:
    txt = txt.replace(anchor, guard, 1)
    changed.append("GBK guard")
else:
    print("SKIP GBK guard (anchor missing or already present)")

# ---- 2) DEFAULT_KEYWORDS_EASTMONEY_GUBA 字典 ----
block = (
    'DEFAULT_KEYWORDS_EASTMONEY_GUBA = [  # 东财股吧平台默认关键词列表(带"期货"后缀,覆盖21品种池+稀疏品种池尾部)\n'
    '    # 黑色系\n'
    '    "螺纹钢期货", "铁矿石期货", "焦炭期货", "焦煤期货", "热卷期货", "硅铁期货", "锰硅期货",\n'
    '    # 有色\n'
    '    "沪铜期货", "沪铝期货", "沪锌期货", "沪镍期货", "黄金期货", "白银期货", "碳酸锂期货", "工业硅期货",\n'
    '    # 能化\n'
    '    "原油期货", "PTA期货", "甲醇期货", "玻璃期货", "PVC期货", "纯碱期货", "尿素期货", "短纤期货", "苯乙烯期货", "乙二醇期货",\n'
    '    # 农产品\n'
    '    "豆粕期货", "菜粕期货", "棕榈油期货", "豆油期货", "白糖期货", "棉花期货", "苹果期货", "红枣期货", "花生期货",\n'
    '    # 稀疏品种补长尾(股吧帖普遍带后缀,AP/CJ/PK/FG/UR/PF 等自动覆盖;不用"苹果"裸词避免撞Apple)\n'
    '    "苹果冷库", "苹果套袋", "红枣库存", "花生库存", "玻璃库存", "浮法玻璃", "尿素出口", "短纤报价",\n'
    '    # 通用\n'
    '    "期货实战", "期货交易心得", "期货技术分析", "商品期货",\n'
    "]\n"
    "\n"
)
if "DEFAULT_KEYWORDS_EASTMONEY_GUBA" not in txt:
    marker = "\nDEFAULT_KEYWORDS = {\n"
    idx = txt.find(marker)
    if idx == -1:
        print("MISS DEFAULT_KEYWORDS marker")
    else:
        txt = txt[:idx] + block + txt[idx:]
        changed.append("EASTMONEY keywords dict")
else:
    print("SKIP eastmoney dict (already present)")

# ---- 3) 分发字典 ----
if '"eastmoney_guba": DEFAULT_KEYWORDS_EASTMONEY_GUBA' not in txt:
    old_dispatch = '    "xueqiu": DEFAULT_KEYWORDS_XUEQIU,\n'
    new_dispatch = (
        '    "xueqiu": DEFAULT_KEYWORDS_XUEQIU,\n'
        '    "eastmoney_guba": DEFAULT_KEYWORDS_EASTMONEY_GUBA,\n'
    )
    if old_dispatch in txt:
        txt = txt.replace(old_dispatch, new_dispatch, 1)
        changed.append("dispatch entry")
    else:
        print("MISS dispatch anchor")
else:
    print("SKIP dispatch (already present)")

target.write_text(txt, encoding="utf-8")
print("batch_collect.py 补丁完成:", changed)
