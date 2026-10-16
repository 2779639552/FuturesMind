import re

backup = open(r"D:\AgentSense\AgentSense\backup\web_template_pre_remove_trading_20260911.html", encoding="utf-8").read()
main_p = r"D:\AgentSense\AgentSense\web_template.html"
main = open(main_p, encoding="utf-8").read()

# 提取: 从"标签页 8: 数据看板"注释到"标签页 10: 数据仓库"注释之前
m = re.search(
    r"(<!-- =+ 标签页 8: 数据看板.*?)(?=<!-- =+ 标签页 10: 数据仓库)",
    backup, re.S,
)
assert m, "未找到区块"
block = m.group(1)
assert 'id="tab-dashboard"' in block and 'id="tab-research"' in block
assert "tab-trading" not in block
print(f"提取: {len(block)} 字符")

# 插入到当前文件的数据仓库注释前
m2 = re.search(r"<!-- =+ 标签页 10: 数据仓库", main)
assert m2
main = main[:m2.start()] + block.rstrip() + "\n\n" + main[m2.start():]
open(main_p, "w", encoding="utf-8").write(main)

chk = open(main_p, encoding="utf-8").read()
for tab in ["tab-run", "tab-sentiment", "tab-warehouse", "tab-dashboard", "tab-research"]:
    print(f"  {tab}: {'OK' if f'id=\"{tab}\"' in chk else '缺失!'}")
print("已恢复")
