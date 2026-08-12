# verify_annotations —— 细粒度注释批次的一次性 QA 工具

> 2026-08-12 全代码库细粒度中文注释(import/调用点/变量/docstring)配套门禁。
> **只作核验用,不参与任何运行路径**,随当次注释提交一并入库。

| 脚本 | 作用 | 权威性 |
|------|------|--------|
| `ast_normalize_compare.py` | 归一化 AST 对比 HEAD:删 docstring 节点 + 字符串常量置空后 `ast.dump` 判等 | **主门禁**,零逻辑改动的权威判据 |
| `dup_block_scan.py` | tokenize 去注释、字符串归一,滑动窗口 k=5 对比 HEAD 找"新增重复代码块";另断言 `def _run_tool_loop` 恰 2 份 | 辅助门禁(防 agent 复制代码) |
| `comment_only_diff.py` | 逐条核验 diff 新增行 = 独立注释/文档字符串/行尾注释 | 辅助启发式(权威以 AST 为准) |
| `node_check_html.py` | 抽取 web_template.html 内联 `<script>` 跑 `node --check` | 前端批次门禁 |

## 用法

```bash
# 无参数 = 自动取 git 工作区相对 HEAD 改动的全部 .py
venv/Scripts/python.exe scripts/verify_annotations/ast_normalize_compare.py
venv/Scripts/python.exe scripts/verify_annotations/dup_block_scan.py

# 指定文件
venv/Scripts/python.exe scripts/verify_annotations/ast_normalize_compare.py file1.py file2.py
venv/Scripts/python.exe scripts/verify_annotations/node_check_html.py web_template.html
```

返回码: 0 = 通过,1 = 有红牌。

## AST 归一化为什么能判"零逻辑改动"

注释不进 AST,所以"只加注释"必然通过。归一化把三种情况拉平后比较:
- 已标注文件**修改 docstring 内容**(不删)与新文件**新增 docstring** → 归一化都删除首语句 docstring 节点,等价;
- 其余字符串字面量 → 置空,等价(两端的字面量差异不判为逻辑改动);
- f-string 的 `FormattedValue` 表达式是逻辑,原样保留,改了必红牌。
