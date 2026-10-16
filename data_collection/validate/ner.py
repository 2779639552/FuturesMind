"""
期货领域 NER (命名实体识别)
===============================

识别维度:
  1. 品种名     → 从别名映射到标准品种名
  2. 合约代码   → RB2501, I2505, IF2401 等
  3. 交易所     → 上期所/大商所/郑商所/CME/LME...
  4. 价格       → 3860元/吨, $82.5/桶, +2.3%
  5. 板块       → 黑色系/有色金属/农产品/能源化工/金融期货
  6. 机构       → 永安期货/中信期货/高盛...
  7. 市场术语   → 多头/空头/基差/升贴水...

使用:
  from ner import FuturesNER
  ner = FuturesNER()
  entities = ner.extract("螺纹钢2501合约今天大涨，永安期货看多到4000")
  # → {varieties:["螺纹钢"], contracts:["RB2501"], prices:[...], ...}


本文件在"情绪数据生产链"中的角色
--------------------------------
    这是【命名实体识别 (NER)】模块, 处于生产链的"文本结构化"环节:
      输入: 一条帖子文本 (title + desc)。
      输出: 结构化的实体清单, 包括:
        varieties(标准品种名) / contracts(合约代码) / exchanges(交易所) /
        sectors(板块) / prices(价格) / institutions(机构)。
    为什么 NER 重要: 情感打分需要知道"在说哪个品种", 聚合(如
    trend_aggregator.py)也要按品种分组, 所以采集后第一步就是 NER。

    NER 方法: 不是机器学习, 而是"基于品种知识库(VARIETY_KB) + 正则"的规则匹配:
      - 品种名: 用"别名→标准名"字典做最长子串匹配 + 位置去重。
      - 合约代码: 每个品种配一个正则, 如 RB2501 / I2505。
      - 交易所/机构: 直接查表 (EXCHANGES / INSTITUTIONS)。
"""

import re  # 【调用包】正则编译与匹配 (合约代码/价格模式)
from dataclasses import asdict, dataclass, field  # 【调用包】NERResult数据类 + asdict转字典

# ================================================================
# 品种知识库 — 标准名 + 全部别名 + 合约代码模式
# ================================================================
# 这是 NER 的"核心知识库", 结构为:
#   标准品种名(如 "螺纹钢") → {
#     aliases: 该品种的所有叫法 (中文别名/英文代码/大小写变体),
#     exchange: 上市交易所,  sector: 所属板块,
#     contract_pattern: 合约代码的正则, 如 r"[Rr][Bb]\d{2,4}" 匹配 RB2501,
#     price_unit: 常用计价单位,  related: 相关品种(用于关联分析),
#   }
# 注意: aliases 里包含"单字母"合约代码(如 "I" 表示铁矿石),
# 单字母匹配时会做词边界检查, 避免把 "IC"(中证500) 误认成 "I"。

VARIETY_KB = {
    # ============ 黑色系 ============
    "螺纹钢": {
        "aliases": ["螺纹钢", "螺纹", "RB", "螺", "钢筋", "rebar", "罗纹", "罗纹钢"],
        "exchange": "上期所",
        "sector": "黑色系",
        "contract_pattern": r"[Rr][Bb]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["热卷", "铁矿石", "焦炭", "线材"],
    },
    "铁矿石": {
        "aliases": [
            "铁矿石",
            "铁矿",
            "I",
            "矿石",
            "iron ore",
            "澳矿",
            "巴矿",
            "PB粉",
            "纽曼粉",
            "麦克粉",
            "超特粉",
        ],
        "exchange": "大商所",
        "sector": "黑色系",
        "contract_pattern": r"[iI]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["螺纹钢", "焦炭", "热卷"],
    },
    "热卷": {
        "aliases": ["热卷", "热轧卷板", "HC", "热轧", "卷板"],
        "exchange": "上期所",
        "sector": "黑色系",
        "contract_pattern": r"[Hh][Cc]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["螺纹钢", "铁矿石"],
    },
    "焦炭": {
        "aliases": ["焦炭", "焦", "J", "冶金焦", "准一级焦", "一级焦"],
        "exchange": "大商所",
        "sector": "黑色系",
        "contract_pattern": r"[jJ]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["焦煤", "铁矿石", "螺纹钢"],
    },
    "焦煤": {
        "aliases": ["焦煤", "JM", "主焦煤", "肥煤", "瘦煤"],
        "exchange": "大商所",
        "sector": "黑色系",
        "contract_pattern": r"[jJ][mM]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["焦炭", "铁矿石"],
    },
    "硅铁": {
        "aliases": ["硅铁", "SF", "硅铁合金"],
        "exchange": "郑商所",
        "sector": "黑色系",
        "contract_pattern": r"[Ss][Ff]\d{2,4}",
        "price_unit": "元/吨",
    },
    "锰硅": {
        "aliases": ["锰硅", "SM", "锰硅合金"],
        "exchange": "郑商所",
        "sector": "黑色系",
        "contract_pattern": r"[Ss][Mm]\d{2,4}",
        "price_unit": "元/吨",
    },
    "线材": {
        "aliases": ["线材", "WR", "盘螺"],
        "exchange": "上期所",
        "sector": "黑色系",
        "contract_pattern": r"[Ww][Rr]\d{2,4}",
        "price_unit": "元/吨",
    },
    # ============ 有色金属 ============
    "铜": {
        "aliases": ["铜期货", "沪铜", "伦铜", "CU", "电解铜", "国际铜", "阴极铜", "铜价"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Cc][Uu]\d{2,4}",
        "price_unit": "元/吨",
    },
    "铝": {
        "aliases": ["铝期货", "沪铝", "伦铝", "AL", "电解铝", "铝锭", "铝价"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Aa][Ll]\d{2,4}",
        "price_unit": "元/吨",
    },
    "锌": {
        "aliases": ["锌期货", "沪锌", "伦锌", "ZN", "锌锭", "锌价"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Zz][Nn]\d{2,4}",
        "price_unit": "元/吨",
    },
    "铅": {
        "aliases": ["铅期货", "沪铅", "PB", "铅锭", "铅价"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Pp][Bb]\d{2,4}",
        "price_unit": "元/吨",
    },
    "镍": {
        "aliases": ["镍期货", "沪镍", "伦镍", "NI", "电解镍", "镍价"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Nn][Ii]\d{2,4}",
        "price_unit": "元/吨",
    },
    "锡": {
        "aliases": ["锡期货", "沪锡", "SN", "锡价"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Ss][Nn]\d{2,4}",
        "price_unit": "元/吨",
    },
    "黄金": {
        "aliases": [
            "黄金期货",
            "沪金",
            "COMEX黄金",
            "AU",
            "金价",
            "现货黄金",
            "伦敦金",
            "纽约金",
            "纸黄金",
        ],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Aa][Uu]\d{2,4}",
        "price_unit": "元/克",
    },
    "白银": {
        "aliases": ["白银期货", "沪银", "COMEX白银", "AG", "银价", "现货白银", "伦敦银"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Aa][Gg]\d{2,4}",
        "price_unit": "元/千克",
    },
    "氧化铝": {
        "aliases": ["氧化铝", "AO", "铝土矿"],
        "exchange": "上期所",
        "sector": "有色金属",
        "contract_pattern": r"[Aa][Oo]\d{2,4}",
        "price_unit": "元/吨",
    },
    "碳酸锂": {
        "aliases": ["碳酸锂", "LC", "锂", "电池级碳酸锂"],
        "exchange": "广期所",
        "sector": "有色金属",
        "contract_pattern": r"[Ll][Cc]\d{2,4}",
        "price_unit": "元/吨",
    },
    "工业硅": {
        # 2026-09-18 拆品种: 原 aliases 含 "多晶硅", 导致多晶硅全被标成工业硅。
        # 广期所 SI(工业硅) 与 PS(多晶硅) 是两个独立品种, 白名单里两者并存。
        "aliases": ["工业硅", "SI", "光伏硅"],
        "exchange": "广期所",
        "sector": "有色金属",
        "contract_pattern": r"[Ss][Ii]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["多晶硅", "碳酸锂"],
    },
    "多晶硅": {
        # 2026-09-18 新增: 原挂在 工业硅 名下(0% 标注率)。
        # 标准名与下游品种元数据(PG/BR/BZ/PS)一致 => "多晶硅"。
        # 刻意不加裸别名 "PS": 该缩写易与英文 postscript / 图片格式混淆,
        # 合约识别交给 contract_pattern(要求尾部跟数字)。
        "aliases": ["多晶硅", "硅料"],
        "exchange": "广期所",
        "sector": "有色金属",
        "contract_pattern": r"[Pp][Ss]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["工业硅", "碳酸锂"],
    },
    # ============ 能源化工 ============
    "原油": {
        "aliases": [
            # 2026-09-18 补裸词: 标准名"原油"此前未入自己的别名表, 裸词全部漏标
            # (实测标注率仅 41.7%, 只认 原油期货/油价/WTI/SC 等变体)
            "原油",
            "原油期货",
            "SC",
            "WTI",
            "布伦特",
            "油价",
            "上海原油",
            "美油",
            "布油",
            "西德克萨斯",
            "Brent",
            "OPEC",
        ],
        "exchange": "上期能源",
        "sector": "能源化工",
        "contract_pattern": r"[Ss][Cc]\d{2,4}",
        "price_unit": "元/桶",
    },
    "PTA": {
        "aliases": ["PTA", "精对苯二甲酸", "TA", "PTA期货", "对苯二甲酸"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Tt][Aa]\d{2,4}",
        "price_unit": "元/吨",
    },
    "甲醇": {
        "aliases": ["甲醇", "MA", "甲醇期货", "工业甲醇", "精甲醇"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Mm][Aa]\d{2,4}",
        "price_unit": "元/吨",
    },
    "PVC": {
        "aliases": ["PVC", "聚氯乙烯", "V", "PVC期货"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[vV]\d{2,4}",
        "price_unit": "元/吨",
    },
    "PP": {
        "aliases": ["PP", "聚丙烯", "PP期货", "聚丙烯期货"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[Pp][Pp]\d{2,4}",
        "price_unit": "元/吨",
    },
    "塑料": {
        "aliases": ["塑料", "LLDPE", "PE", "L", "聚乙烯", "线性低密度聚乙烯"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[lL]\d{2,4}",
        "price_unit": "元/吨",
    },
    "橡胶": {
        # 2026-09-18 摘出 "BR": 原把它当橡胶别名, 导致丁二烯橡胶(上期所 BR)
        # 的帖子全被并入"橡胶"(0% 标注率)。BR 是独立上市品种。
        "aliases": ["橡胶", "天然橡胶", "RU", "橡胶期货", "天胶", "合成橡胶"],
        "exchange": "上期所",
        "sector": "能源化工",
        "contract_pattern": r"[Rr][Uu]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["20号胶", "丁二烯橡胶"],
    },
    "丁二烯橡胶": {
        # 2026-09-18 新增: 原文本含 "橡胶" 子串 => 全部被橡胶吞掉。
        # 标准名对齐下游品种元数据(BR = 丁二烯橡胶)。
        "aliases": ["丁二烯橡胶", "顺丁橡胶", "丁二烯", "BR"],
        "exchange": "上期所",
        "sector": "能源化工",
        "contract_pattern": r"[Bb][Rr]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["橡胶", "20号胶"],
    },
    "沥青": {
        "aliases": ["沥青", "BU", "石油沥青", "道路沥青"],
        "exchange": "上期所",
        "sector": "能源化工",
        "contract_pattern": r"[Bb][Uu]\d{2,4}",
        "price_unit": "元/吨",
    },
    "燃料油": {
        "aliases": ["燃料油", "FU", "燃油", "高硫燃料油", "船用油"],
        "exchange": "上期所",
        "sector": "能源化工",
        "contract_pattern": r"[Ff][Uu]\d{2,4}",
        "price_unit": "元/吨",
    },
    "低硫燃料油": {
        "aliases": ["低硫燃料油", "LU", "低硫油", "船用低硫燃料油", "LSFO"],
        "exchange": "上期能源",
        "sector": "能源化工",
        "contract_pattern": r"[Ll][Uu]\d{2,4}",
        "price_unit": "元/吨",
    },
    "LPG": {
        # 2026-09-18 新增: 词典此前完全没有该品种(0% 标注率)。
        # 标准名对齐下游品种元数据(PG = "LPG"), 而非"液化石油气"
        # (下游 price_fetcher 两种写法都认, 取元数据口径最稳)。
        "aliases": ["LPG", "液化石油气", "液化气", "PG"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[Pp][Gg]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["原油", "燃料油", "纯苯"],
    },
    "20号胶": {
        "aliases": ["20号胶", "NR", "20号橡胶", "20号标准胶", "泰标"],
        "exchange": "上期能源",
        "sector": "能源化工",
        "contract_pattern": r"[Nn][Rr]\d{2,4}",
        "price_unit": "元/吨",
    },
    "尿素": {
        "aliases": ["尿素", "UR", "化肥"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Uu][Rr]\d{2,4}",
        "price_unit": "元/吨",
    },
    "纯碱": {
        "aliases": ["纯碱", "SA", "碳酸钠", "轻碱", "重碱"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Ss][Aa]\d{2,4}",
        "price_unit": "元/吨",
    },
    "玻璃": {
        "aliases": ["玻璃期货", "FG", "平板玻璃", "浮法玻璃"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Ff][Gg]\d{2,4}",
        "price_unit": "元/吨",
    },
    "乙二醇": {
        "aliases": ["乙二醇", "EG", "MEG"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[Ee][Gg]\d{2,4}",
        "price_unit": "元/吨",
    },
    "苯乙烯": {
        # 2026-09-18 摘出 "SM": SM 是锰硅(郑商所)的代码, 苯乙烯是 EB。
        # 原写法让二者争同一个别名, 且写覆盖语义下后者(苯乙烯)取胜
        # => 所有 "SM" 帖子被误标成苯乙烯(白名单品种), 污染其情绪信号。
        "aliases": ["苯乙烯", "EB"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[Ee][Bb]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["纯苯", "乙二醇"],
    },
    "纯苯": {
        # 2026-09-18 新增: 词典此前完全没有该品种(0% 标注率, 只被"苯乙烯"蹭到)。
        # 标准名对齐下游品种元数据(BZ = 纯苯)。
        "aliases": ["纯苯", "BZ"],
        "exchange": "大商所",
        "sector": "能源化工",
        "contract_pattern": r"[Bb][Zz]\d{2,4}",
        "price_unit": "元/吨",
        "related": ["苯乙烯", "原油"],
    },
    "短纤": {
        "aliases": ["短纤", "PF", "涤纶短纤"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Pp][Ff]\d{2,4}",
        "price_unit": "元/吨",
    },
    "对二甲苯": {
        "aliases": ["对二甲苯", "PX", "二甲苯"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Pp][Xx]\d{2,4}",
        "price_unit": "元/吨",
    },
    "烧碱": {
        "aliases": ["烧碱", "SH", "氢氧化钠", "液碱"],
        "exchange": "郑商所",
        "sector": "能源化工",
        "contract_pattern": r"[Ss][Hh]\d{2,4}",
        "price_unit": "元/吨",
    },
    # ============ 农产品 ============
    "豆粕": {
        "aliases": ["豆粕", "M", "豆粕期货", "豆粕价格", "豆粕现货", "饲料粕"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[mM]\d{2,4}",
        "price_unit": "元/吨",
    },
    "豆油": {
        "aliases": ["豆油", "Y", "豆油期货", "大豆油"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[yY]\d{2,4}",
        "price_unit": "元/吨",
    },
    "棕榈油": {
        "aliases": ["棕榈油", "P", "棕榈", "棕油", "马棕", "马来棕榈油", "印尼棕榈油"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[pP]\d{2,4}",
        "price_unit": "元/吨",
    },
    "菜粕": {
        "aliases": ["菜粕", "RM", "菜籽粕", "菜粕期货"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Rr][Mm]\d{2,4}",
        "price_unit": "元/吨",
    },
    "菜油": {
        "aliases": ["菜油", "OI", "菜籽油", "菜油期货"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Oo][Ii]\d{2,4}",
        "price_unit": "元/吨",
    },
    "白糖": {
        "aliases": ["白糖", "SR", "白糖期货", "白砂糖", "糖价", "原糖"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Ss][Rr]\d{2,4}",
        "price_unit": "元/吨",
    },
    "棉花": {
        "aliases": ["棉花", "CF", "棉花期货", "郑棉", "美棉", "棉价", "棉纱"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Cc][Ff]\d{2,4}",
        "price_unit": "元/吨",
    },
    "玉米": {
        "aliases": ["玉米", "C", "玉米期货", "玉米价格", "东北玉米"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[cC]\d{2,4}",
        "price_unit": "元/吨",
    },
    "淀粉": {
        "aliases": ["淀粉", "CS", "玉米淀粉"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[Cc][Ss]\d{2,4}",
        "price_unit": "元/吨",
    },
    "鸡蛋": {
        "aliases": ["鸡蛋期货", "JD", "鸡蛋"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[Jj][Dd]\d{2,4}",
        "price_unit": "元/500千克",
    },
    "生猪": {
        "aliases": ["生猪", "LH", "猪肉期货", "猪价", "生猪期货", "毛猪"],
        "exchange": "大商所",
        "sector": "农产品",
        "contract_pattern": r"[Ll][Hh]\d{2,4}",
        "price_unit": "元/吨",
    },
    "苹果": {
        "aliases": ["苹果期货", "AP", "苹果", "红富士"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Aa][Pp]\d{2,4}",
        "price_unit": "元/吨",
    },
    "红枣": {
        "aliases": ["红枣期货", "CJ", "红枣", "灰枣"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Cc][Jj]\d{2,4}",
        "price_unit": "元/吨",
    },
    "花生": {
        "aliases": ["花生期货", "PK", "花生", "油料花生"],
        "exchange": "郑商所",
        "sector": "农产品",
        "contract_pattern": r"[Pp][Kk]\d{2,4}",
        "price_unit": "元/吨",
    },
    # ============ 金融期货 ============
    "沪深300股指期货": {
        "aliases": ["沪深300股指期货", "IF", "沪深300期货", "沪深300", "IF期货", "300股指"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Ii][Ff]\d{2,4}",
        "price_unit": "点",
    },
    "上证50股指期货": {
        "aliases": ["上证50股指期货", "IH", "上证50期货", "上证50", "IH期货"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Ii][Hh]\d{2,4}",
        "price_unit": "点",
    },
    "中证500股指期货": {
        "aliases": ["中证500股指期货", "IC", "中证500期货", "中证500", "IC期货", "500股指"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Ii][Cc]\d{2,4}",
        "price_unit": "点",
    },
    "中证1000股指期货": {
        "aliases": ["中证1000股指期货", "IM", "中证1000期货", "中证1000", "IM期货", "1000股指"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Ii][Mm]\d{2,4}",
        "price_unit": "点",
    },
    "10年期国债期货": {
        "aliases": ["10年期国债期货", "T", "十债", "10年国债", "十年期国债", "T合约"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[tT]\d{2,4}",
        "price_unit": "元",
    },
    "5年期国债期货": {
        "aliases": ["5年期国债期货", "TF", "五债", "5年国债", "五年期国债"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Tt][Ff]\d{2,4}",
        "price_unit": "元",
    },
    "2年期国债期货": {
        "aliases": ["2年期国债期货", "TS", "二债", "2年国债", "两年期国债"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Tt][Ss]\d{2,4}",
        "price_unit": "元",
    },
    "30年期国债期货": {
        "aliases": ["30年期国债期货", "TL", "三十债", "30年国债"],
        "exchange": "中金所",
        "sector": "金融期货",
        "contract_pattern": r"[Tt][Ll]\d{2,4}",
        "price_unit": "元",
    },
}

# ================================================================
# 交易所
# ================================================================
# 交易所知识库: 规范名(如 "上期所") → 一组别名 (中文全称/英文代码)。
# __init__ 里会把别名反向映射回规范名, 文本中出现任意别名即可识别。

EXCHANGES = {
    "上期所": ["上期所", "上海期货交易所", "SHFE", "上期"],
    "上期能源": ["上期能源", "上海国际能源交易中心", "INE", "上海能源"],
    "大商所": ["大商所", "大连商品交易所", "DCE", "大商"],
    "郑商所": ["郑商所", "郑州商品交易所", "CZCE", "郑商", "郑交所"],
    "中金所": ["中金所", "中国金融期货交易所", "CFFEX", "金融期货交易所"],
    "广期所": ["广期所", "广州期货交易所", "GFEX", "广州期货"],
    "CME": ["CME", "芝加哥商品交易所", "芝加哥商业交易所"],
    "LME": ["LME", "伦敦金属交易所"],
    "COMEX": ["COMEX", "纽约商品交易所"],
    "CBOT": ["CBOT", "芝加哥期货交易所"],
    "ICE": ["ICE", "洲际交易所"],
    "SGX": ["SGX", "新加坡交易所", "新交所"],
    "TOCOM": ["TOCOM", "东京商品交易所"],
}

# ================================================================
# 知名期货机构
# ================================================================
# 机构/公司名单, 识别"谁在说话"。extract() 里直接做子串匹配。

INSTITUTIONS = [
    "永安期货",
    "中信期货",
    "国泰君安期货",
    "银河期货",
    "华泰期货",
    "海通期货",
    "广发期货",
    "招商期货",
    "光大期货",
    "申银万国期货",
    "南华期货",
    "瑞达期货",
    "方正中期期货",
    "宏源期货",
    "徽商期货",
    "中粮期货",
    "五矿期货",
    "东海期货",
    "格林大华",
    "金瑞期货",
    "高盛",
    "摩根士丹利",
    "摩根大通",
    "花旗",
    "美银美林",
    "野村",
    "瑞银",
    "巴克莱",
    "法兴银行",
    "荷兰国际",
    "中信建投期货",
    "东证期货",
    "中泰期货",
    "浙商期货",
    "宝城期货",
    "前海期货",
    "国信期货",
    "中辉期货",
    "一德期货",
    "美尔雅期货",
]


# ================================================================
# NER 引擎
# ================================================================


@dataclass
class NERResult:
    """
    【功能】单条文本的 NER 识别结果容器 (数据类)。
    【字段说明】
      varieties: 识别到的品种, 每项为
        {"name": 标准名, "matched": 实际命中的别名, "sector": 板块, "exchange": 交易所, "position": 出现位置}。
      contracts: 合约代码, 每项为 {"code", "variety", "exchange"}。
      exchanges / sectors: 去重后的交易所 / 板块列表。
      prices: 价格, 每项为 {"value", "context"} (value 保留原文数字串)。
      institutions: 识别到的机构列表。
      variety_count / contract_count: 品种与合约的数量。
    """

    varieties: list = field(
        default_factory=list
    )  # [{"name":"螺纹钢","matched":"螺纹","sector":"黑色系","exchange":"上期所"}]
    contracts: list = field(
        default_factory=list
    )  # [{"code":"RB2501","variety":"螺纹钢","exchange":"上期所"}]
    exchanges: list = field(default_factory=list)  # ["上期所", "大商所"]
    sectors: list = field(default_factory=list)  # ["黑色系", "有色金属"]
    prices: list = field(
        default_factory=list
    )  # [{"value":3860,"unit":"元/吨","context":"3860元/吨"}]
    institutions: list = field(default_factory=list)  # ["永安期货", "中信期货"]
    variety_count: int = 0
    contract_count: int = 0


# 【常量】ASCII 字母/数字集合: 供"拉丁别名上下文"判定用
#   ⚠️ 必须用 ascii 判定, 不能用 str.isalpha(): 汉字 .isalpha() 也是 True,
#      若拿它判边界, "跟pta"/"看多sc" 这类最常见的中文紧邻写法会被误拒。
_ASCII_ALPHA = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_ALPHA_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
_ASCII_DIGIT = frozenset("0123456789")


def _latin_alias_ok(hay: str, idx: int, alias: str) -> bool:
    """【判定】一次别名命中是否可信。中文别名一律通过, 拉丁别名要过三道闸。

    【背景 2026-09-18】品种代码别名是 1~3 个拉丁字母, 天生容易撞进英文串和其他编码。
    实测存量 3.1 万条帖子里误标高发形态:
      - `VLCC`(超大型油轮) -> LC 碳酸锂 ; `SHFE` -> SH 烧碱 ; `APP` -> PP 聚丙烯
      - `(SH601015)` 股票代码 -> SH 烧碱 ; `MA5` 均线 -> MA 甲醇
      - `深V` / `M头` / `10Y国债` / `见P1` / `L.M.S` -> V,M,Y,P,L 等单字母
      - `GB/T4008` 国标号 -> T 十年期国债 ; `Al brooks`(人名) -> AL 铝

    【三道闸】(hay 可能是小写副本, 但长度与原文一致, 故 idx 可直接用)
      1. 前后不紧邻 ASCII 字母 —— 挡 VLCC / SHFE / APP 这类"更长英文串的子串"
      2. 后跟数字时, 数字必须是 3~4 位合约格式 —— 挡 SH601015(6位股票) / MA5(1位均线)
      3. 短别名只认"合约代码"位置:
         - 单字母: 仅当后跟 3~4 位数字(如 M2601 / C1200 / T2603); 且前邻 `/` 时拒绝(GB/T4008)
         - 双字母: 数字位数同上; 无数字时拒绝"后跟空格+小写拉丁"(英文词/人名 Al brooks)
    """
    if not (alias.isascii() and alias.isalpha()):
        return True  # 中文别名: 不存在"夹在英文单词里"的问题, 不设约束

    before = hay[idx - 1] if idx > 0 else ""
    end = idx + len(alias)
    after = hay[end] if end < len(hay) else ""

    # 闸1: 前后紧邻 ASCII 字母 => 是更长英文串的子串
    if before in _ASCII_ALPHA or after in _ASCII_ALPHA:
        return False

    # 取后跟的连续数字(期货合约月份, 如 RB2501 的 "2501")
    d = end
    while d < len(hay) and hay[d] in _ASCII_DIGIT:
        d += 1
    ndigits = d - end

    if len(alias) == 1:
        # 闸3a: 单字母只在"合约代码"位置成立
        if before == "/":
            return False  # 国标号 GB/T4008 里的 T
        return 3 <= ndigits <= 4

    if len(alias) == 2:
        if ndigits:
            return 3 <= ndigits <= 4  # 闸2
        nxt = hay[end + 1] if end + 1 < len(hay) else ""
        if after == " " and nxt in _ASCII_ALPHA_LOWER:
            return False  # 英文词/人名: "Al brooks"
        return True

    return True  # 3 字母以上: 闸1 足够(PTA/PVC/LPG 等)


class FuturesNER:
    """
    【功能】期货领域命名实体识别引擎。
    【关键逻辑】基于"知识库 + 正则"的规则匹配 (非机器学习), 三步:
      1. 品种名: 别名 → 标准名映射, 按别名长度降序匹配 (最长优先), 并做位置去重。
      2. 合约代码 / 价格: 用每个品种预编译的正则去 finditer。
      3. 交易所 / 机构: 直接查表匹配。
    """

    def __init__(self):
        # 构建别名→标准名映射 (长别名优先覆盖短别名)
        # 每个别名都记下对应的标准名和完整信息(板块/交易所/正则等)
        # ⚠️ 写覆盖语义: 同一别名挂在多个品种下时, 后遍历到的品种取胜(KB 书写顺序决定)。
        #    2026-09-18 即因此让 "SM"(锰硅代码) 被 "苯乙烯" 抢走, 已摘除该别名。
        self._alias_to_variety = {}
        self._alias_to_info = {}
        for std_name, info in VARIETY_KB.items():
            for alias in info["aliases"]:
                if alias not in self._alias_to_variety or len(alias) >= len(alias):
                    self._alias_to_variety[alias] = std_name
                    self._alias_to_info[alias] = info

        # 按长度降序排列（优先匹配长别名）
        # 例: "铁矿石" 应优先于 "铁矿"/"铁", 避免短的先把位置占掉
        self._sorted_aliases = sorted(self._alias_to_variety.keys(), key=len, reverse=True)

        # 2026-09-18 大小写不敏感检索表: (原别名, 检索用别名, 是否纯拉丁)
        #   中文别名(螺纹钢/原油...)原样检索; 2 字母及以上纯拉丁别名(PTA/SC/RB/MEG...)
        #   统一按小写检索, 修复 "pta"/"sc2501"/"meg" 等小写写法漏标。
        #   ⚠️ 单字母别名(V/L/C/M/T/Y/J/I)刻意不参与小写化: 它们同时是
        #      升/吨/米/百万等计量单位缩写, 小写化会把 "10t"/"m2"/"500l" 标成品种。
        self._search_aliases = [
            (a, a.lower(), a.isascii() and a.isalpha() and len(a) > 1)
            for a in self._sorted_aliases
        ]

        # 交易所映射: 别名 → 规范名 (如 "SHFE" → "上期所")
        self._exchange_to_canonical = {}
        for canonical, aliases in EXCHANGES.items():
            for a in aliases:
                self._exchange_to_canonical[a] = canonical
        self._sorted_exchanges = sorted(self._exchange_to_canonical.keys(), key=len, reverse=True)

        # 合约代码正则（聚合所有品种）
        # 例: "螺纹钢" → r"[Rr][Bb]\d{2,4}" 可匹配 RB2501 / rb2501
        self._contract_patterns = {}
        for name, info in VARIETY_KB.items():
            pat = info.get("contract_pattern")
            if pat:
                self._contract_patterns[name] = re.compile(pat)

        # 价格正则
        self._price_patterns = [  # 【变量】价格识别正则库 (元/吨、$/桶、涨跌幅等, 每项带注释示例)
            re.compile(r"(\d{2,5})\s*元\s*/\s*吨"),  # 3860元/吨
            re.compile(r"(\d{2,5})\s*元\s*/\s*桶"),  # 82.5元/桶
            re.compile(r"(\d{2,5})\s*元\s*/\s*克"),  # 450元/克
            re.compile(r"(\d{2,5})\s*元\s*/\s*千克"),  # 5200元/千克
            re.compile(r"\$\s*(\d{1,5}\.?\d*)\s*/桶"),  # $82.5/桶
            re.compile(r"\$\s*(\d{1,5}\.?\d*)\s*/盎司"),  # $1950/盎司
            re.compile(r"[+＋-]\s*(\d{1,3}\.?\d*)\s*%"),  # +2.3%, -1.5%
            re.compile(r"(\d{2,5})\s*点"),  # 3980点 (股指)
            re.compile(r"涨\s*(\d{1,5}\.?\d*)\s*%"),  # 涨2.3%
            re.compile(r"跌\s*(\d{1,5}\.?\d*)\s*%"),  # 跌1.5%
        ]

    def _dedup_varieties(self, varieties: list) -> list:
        """【功能】品种去重: 同一标准品种可能出现多次匹配, 合并成一条, 保留最长匹配文本。
        【参数】varieties: 识别到的品种列表。
        【返回】去重后的列表 (每个标准品种最多一条)。"""
        by_name = {}
        for v in varieties:
            name = v["name"]
            if name not in by_name or len(v["matched"]) > len(by_name[name]["matched"]):
                by_name[name] = v
        return list(by_name.values())

    def extract(self, text: str) -> dict:
        """
        【功能】从一段文本中提取所有期货相关实体 (品种/合约/交易所/板块/价格/机构)。
        【参数】text: str, 待识别的文本。
        【返回】dict: 可 JSON 序列化的实体字典 (与 NERResult 字段一致)。
        【关键逻辑】
          1. 品种名: 按别名长度降序逐词查找, 用 matched_positions 集合
             防止重叠匹配; 单字母别名做词边界检查。
          2. 合约代码: 对每个品种的正则在全文 finditer。
          3. 交易所/机构: 查表匹配。
          4. 最后去重并统计 variety_count / contract_count。
        """
        result = NERResult()

        if not text:
            return asdict(result)

        # 1. 品种名识别（长别名优先，防重叠；2 字母及以上纯拉丁别名大小写不敏感）
        matched_positions = set()  # 【变量】已匹配位置集合 (防止多个别名重叠占用同一段文本)
        text_lower = text.lower()  # 【变量】小写副本(仅供纯拉丁别名检索)
        # 某些 Unicode 字符 lower() 后长度会变(如 'İ'→'i̇'), 那位置索引就不可复用, 直接放弃小写检索
        use_lower = len(text_lower) == len(text)

        for alias, alias_lower, casefold in self._search_aliases:
            # 【变量】本次检索用的"干草堆/针": 纯拉丁且长度>1 时用小写副本, 否则按原文
            hay = text_lower if (casefold and use_lower) else text
            needle = alias_lower if (casefold and use_lower) else alias
            if needle not in hay:
                continue

            # 查找所有出现位置
            idx = 0
            while True:
                idx = hay.find(needle, idx)
                if idx < 0:
                    break

                # 【校验】拉丁别名的上下文合法性(中文别名直接通过), 三道闸见
                #  模块级 _latin_alias_ok() 的说明。例: "I" 不匹配 "IC"、
                #  "si" 不匹配 "sina"; 且单字母只在合约代码位置(P 后跟 3~4 位数字)成立。
                # ⚠️ 传 alias(原别名) 而非 needle —— 长度与大小写语义以原别名为准。
                if not _latin_alias_ok(hay, idx, alias):
                    idx += 1
                    continue

                # 检查是否与已匹配位置重叠
                positions = set(range(idx, idx + len(alias)))
                if not positions & matched_positions:
                    info = self._alias_to_info[alias]
                    std_name = self._alias_to_variety[alias]
                    result.varieties.append(
                        {
                            "name": std_name,
                            "matched": alias,
                            "sector": info["sector"],
                            "exchange": info["exchange"],
                            "position": idx,
                        }
                    )
                    result.sectors.append(info["sector"])
                    matched_positions |= positions

                idx += len(alias)

        # 2. 合约代码识别
        for variety_name, pattern in self._contract_patterns.items():
            for m in pattern.finditer(text):
                code = m.group(0)
                info = VARIETY_KB[variety_name]
                result.contracts.append(
                    {
                        "code": code,
                        "variety": variety_name,
                        "exchange": info["exchange"],
                    }
                )

        # 3. 交易所识别
        for alias in self._sorted_exchanges:
            if alias in text:
                canonical = self._exchange_to_canonical[alias]
                if canonical not in result.exchanges:
                    result.exchanges.append(canonical)

        # 4. 价格识别
        price_context_window = 15  # 【变量】价格上下文窗口: 截取价格前后各15字符作为语境
        for pat in self._price_patterns:
            for m in pat.finditer(text):
                start = max(0, m.start() - price_context_window)
                end = min(len(text), m.end() + price_context_window)
                result.prices.append(
                    {
                        "value": m.group(0),
                        "context": text[start:end].strip(),
                    }
                )

        # 5. 机构识别
        for inst in INSTITUTIONS:
            if inst in text:
                result.institutions.append(inst)

        # 去重 varieties 和 sectors
        result.varieties = self._dedup_varieties(result.varieties)  # 【调用函数】按标准品种合并重复匹配, 每个品种保留最长匹配文本
        result.sectors = list(set(result.sectors))
        result.variety_count = len(result.varieties)
        result.contract_count = len(result.contracts)

        return asdict(result)  # 【调用函数】dataclass转dict (便于JSON序列化/下游写盘)

    def extract_per_variety_context(self, text: str, window: int = 80) -> list[dict]:
        """
        【功能】为每个品种提取"上下文片段", 供品种级情感分析使用
                (sentiment.SentimentAnalyzer.analyze_aspects 也做类似的事)。
        【参数】
          text: 完整原文。
          window: 品种名前后各取多少字符作为上下文 (默认 80)。
        【返回】list[dict], 每项为:
          {"variety": 标准名, "matched_alias": 命中的别名, "context": 上下文片段,
           "sector": 板块, "position": 出现位置}。
        【关键逻辑】
          - 与 extract() 相同的"最长别名优先 + 拉丁别名三道闸(_latin_alias_ok) + 2 字母以上拉丁大小写不敏感"匹配逻辑。
          - 每个品种只取第一次出现的位置记录, 避免重复 (seen_names 去重)。
          - context 取窗口片段后在句号处截断, 让片段尽量是完整句子。
        """
        results = []
        seen_names = set()  # 【变量】已记录品种名集合 (每个品种只取第一次出现, 避免重复)
        text_lower = text.lower()  # 【变量】小写副本(仅供纯拉丁别名检索)
        use_lower = len(text_lower) == len(text)  # 长度不等则放弃小写检索(位置索引不可复用)

        for alias, alias_lower, casefold in self._search_aliases:
            hay = text_lower if (casefold and use_lower) else text
            needle = alias_lower if (casefold and use_lower) else alias
            if needle not in hay:
                continue

            idx = 0
            while True:
                idx = hay.find(needle, idx)
                if idx < 0:
                    break

                # 【校验】拉丁别名上下文合法性, 与 extract() 共用同一判定函数
                if not _latin_alias_ok(hay, idx, alias):
                    idx += 1
                    continue

                info = self._alias_to_info.get(alias, {})
                std_name = self._alias_to_variety.get(alias, alias)

                # 只在每个品种第一次出现时记录（避免重复）
                if std_name not in seen_names:
                    seen_names.add(std_name)
                    start = max(0, idx - window)
                    end = min(len(text), idx + len(alias) + window)
                    context = text[start:end].strip()
                    # 尝试在标点处截断
                    if start > 0:
                        first_period = context.find("。")
                        if 0 < first_period < window:
                            context = context[first_period + 1 :]
                    if end < len(text):
                        last_period = context.rfind("。")
                        if last_period > window:
                            context = context[: last_period + 1]

                    results.append(
                        {
                            "variety": std_name,
                            "matched_alias": alias,
                            "context": context.strip(),
                            "sector": info.get("sector", ""),
                            "position": idx,
                        }
                    )

                idx += len(alias)

        return results

    def enrich_notes(
        self, notes: list[dict], text_field: str = "desc", title_field: str = "title"
    ) -> list[dict]:
        """
        【功能】批量丰富笔记数据: 对每条笔记的 title+desc 做 NER,
                并把识别结果字段直接合并进原 dict (原地修改)。
        【参数】
          notes: list[dict], 笔记列表。
          text_field / title_field: 正文与标题字段名 (默认 desc/title)。
        【返回】list[dict]: 同一列表, 每条被追加 varieties/contracts/exchanges/
                sectors/prices/institutions/variety_count/contract_count。
        【关键逻辑】对每条笔记拼出 title+" "+desc 后调用 extract()。
        """
        for note in notes:
            text = (note.get(title_field, "") or "") + " " + (note.get(text_field, "") or "")
            entities = self.extract(text)  # 【调用函数】对拼接文本做NER, 得到品种/合约/交易所等实体

            note["varieties"] = entities["varieties"]
            note["contracts"] = entities["contracts"]
            note["exchanges"] = entities["exchanges"]
            note["sectors"] = entities["sectors"]
            note["prices"] = entities["prices"]
            note["institutions"] = entities["institutions"]
            note["variety_count"] = entities["variety_count"]
            note["contract_count"] = entities["contract_count"]

        return notes


# ================================================================
# CLI 测试
# ================================================================

if __name__ == "__main__":
    ner = FuturesNER()

    tests = [
        "螺纹钢2501合约今天大涨到3860元/吨，永安期货建议加仓",
        "铁矿石09合约暴跌，空头砸盘，I2505跌到750元/吨以下",
        "PTA和甲醇今天窄幅震荡，郑商所品种整体偏弱",
        "黄金AU2412突破450元/克，COMEX黄金也创了新高",
        "焦煤09进入交割预演阶段，基差走强，关注大商所焦炭联动",
        "50w期货实盘记录第68天，今天做多螺纹和铁矿，可惜被洗出去了[笑哭R]",
    ]

    print("=" * 70)
    print("期货领域 NER — 实体识别测试")
    print("=" * 70)

    for text in tests:
        r = ner.extract(text)
        print(f"\n  Text: {text[:80]}...")
        if r["varieties"]:
            names = [f"{v['name']}(matched:{v['matched']})" for v in r["varieties"]]
            print(f"    Varieties: {names}")
        if r["contracts"]:
            codes = [c["code"] for c in r["contracts"]]
            print(f"    Contracts: {codes}")
        if r["exchanges"]:
            print(f"    Exchanges: {r['exchanges']}")
        if r["sectors"]:
            print(f"    Sectors: {r['sectors']}")
        if r["prices"]:
            print(f"    Prices: {r['prices']}")
        if r["institutions"]:
            print(f"    Institutions: {r['institutions']}")

    print(f"\n{'=' * 70}")
    print("NER 模块就绪。使用方式:")
    print("  from ner import FuturesNER")
    print("  ner = FuturesNER()")
    print("  entities = ner.extract('螺纹钢今天大涨')")
    print("  ner.enrich_notes(deep_notes)  # 批量丰富")
