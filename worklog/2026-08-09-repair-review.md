# FuturesMind 全量修理与再评估留档（2026-08-09）

> 一句话结论：**本地长期运行掩盖了一批"全新环境才暴露"的问题**。本次将 GitHub 版本全新克隆调试，
> 定位并修复了 6 类被掩盖问题 + 5 个被 lint 长期压制、只有版本升级才会炸的真 bug，最后做了一次全仓再评估。
> 当前状态：git 干净、ruff 全仓 0 错误、559 测试通过、CI lint 由红变绿。

---

## 1. 背景

本项目长期只在本地开发机调试运行。本地环境与全新克隆环境的差异会**掩盖**两类问题：

- **缺依赖 / 缺 import**：本地 venv 恰好装了，代码里却没声明 → 全新环境 import 即崩。
- **路径写死**：本地 `~/Desktop/思路2/validate` 恰好存在 → 换机器就找不到数据。
- **3.12 专属语法**：本地只有 Python 3.12，写出的 f-string 语法在 CI 的 3.10/3.11 矩阵上直接报语法错误（`ast.parse` 都过不了，而本地从来只跑 3.12）。

本次工作流程：`git clone` GitHub 版 → 用全新 venv 复现问题 → 逐类修复 → 全仓再评估 → 提交推送。

## 2. 被掩盖问题清单与闭环状态

| # | 问题 | 严重度 | 根因 | 修复 | 状态 |
|---|------|:--:|------|------|:--:|
| 1 | `price_fetcher.py` 模块级 NameError（缺 `import os`） | P0 | 该变量只在函数内延迟使用，测试从未触发 | 补 import（后统一走 `resolve_think2_dir`） | ✅ |
| 2 | `scheduler.py` 顶层 `from apscheduler` 但依赖未声明 | P0 | 本地从不运行 scheduler，venv 也没装 | `pyproject.toml` 补 `apscheduler>=3.10,<4` | ✅ |
| 3 | `main.py` 模块级实例化 `TradingAgentsGraph`，无 `.env` 即 ValueError | P0 | 顶层实例化导致 import 即执行 LLM 客户端构建 | 移入 `if __name__ == "__main__"` | ✅ |
| 4 | 硬编码路径 `~/Desktop/思路2/validate` 残留在 4 个模块 | P1 | 上次"零残留"声明不准确 | 新增 `path_utils.resolve_think2_dir()` 统一解析 | ✅ |
| 5 | 4 个过时测试（引用已删除的 `TestSentimentAnalystAgent`） | P1 | 测试未随代码重构更新 | 重写为 `TestCommoditySentimentAnalyst`（patch `_run_tool_loop`，RB 品种） | ✅ |
| 6 | 本地 venv 无 pytest（测试靠系统 Python）；运行依赖本地==全新无漂移 | P2 | 运维事实，非代码 bug | —（记录在案） | ℹ️ |

### 附加发现（被 812 个 ruff 错误长期压制的真 bug）

全仓 `ruff check` 原本 **812 个错误**（CI lint 长期是红的，pyproject 注释表明 `ruff format` 被有意延后）。
清理过程中揪出 5 个真实缺陷：

| bug | 位置 | 危害 |
|-----|------|------|
| **PEP 701 3.12 专属 f-string**（两处：换行替换字段 / 外层引号复用） | `web_app.py` | 在 CI 的 Python 3.10/3.11 上**语法错误**，import 即挂 |
| F601 重复 dict key（`sentiment_report` 死条目） | `cli/main.py` | 后定义的 key 静默覆盖前者 |
| F811 函数遮蔽（重复 `def filter_futures_notes`） | `data_collection/validate/xhs_scraper.py` | 早前定义被后定义静默覆盖，行为依赖定义顺序 |
| B023 闭包晚绑定（`_close` 引用循环变量 `var`） | `signal_analyzer.py` | 回调执行时 `var` 已变，拿到错误品种 |
| E741 变量名 `l` / `i` 与数字混淆 | 多处 | 可读性 + 潜在误用 |

## 3. 提交记录（本次修理，均已 push）

| commit | 内容 |
|--------|------|
| `3dbf92e` | P0 修复批：price_fetcher `import os`、apscheduler 依赖、4 个过时测试重写 |
| `e8ba0dc` | 全量修理批（145 文件 / +8154 −4604）：全仓 ruff 812→0（format + 规则修复）、main.py 模块 guard、`path_utils.py` 新建并接入 4 模块 |

> `e8ba0dc` 体量大主要来自 `ruff format` 全仓重排（一次性格式化收益，此后 diff 干净）。

## 4. 再次评估结果（2026-08-09 全量验证）

| 检查项 | 结果 |
|--------|------|
| git 工作树 | 干净；本地 `main` 与远端同步，无 ahead/behind |
| ruff check 全仓 | ✅ `All checks passed!`（0 错误） |
| ruff format --check | ✅ 200 files already formatted |
| 测试（CI 同命令） | ✅ **559 passed / 1 skipped / 1 deselected**，69 subtests，10.4s |
| `main.py` 无 `.env` import | ✅ 假 USERPROFILE/HOME 下 import 不崩，`main()` 存在 |
| 4 个核心模块 import | ✅ price_fetcher / scheduler / signal_analyzer / web_app 全部成功 |
| 路径解析 | ✅ 本机优先命中真实 `思路2/validate`（output/trends 存在） |
| 残留 TODO/FIXME | ✅ 项目内 0（排除 venv/第三方） |
| 残留绝对路径硬编码 | ✅ 0（排除 venv/第三方） |
| CI 命令对齐 | ✅ ruff-action@v3 + `pytest -ra --strict-markers -m "not integration" --timeout=120` 与本地完全一致 |
| 样本数据/文档 | ✅ `data/`（external_data + think2_validate）、`data_collection/CONFIG_GUIDE.md` + `.env.example` 均在仓 |

### 闭环后的剩余风险（非阻塞，记录在案）

1. **本地 venv 无 pytest / ruff**：测试跑系统级 Python（9.1.1）；ruff 跑 `github_debug/FuturesMind/venv_fresh`。改动后记得用这两套验证。
2. **CI 跑 Python 3.10/3.11/3.12 矩阵**：写 f-string 时避免 PEP 701 专属语法（替换字段换行、引号复用）——本地只有 3.12 发现不了。
3. **`data_collection/Spider_XHS` 是 vendored 第三方签名引擎（Node.js）**：已通过 pyproject `extend-exclude` 排除出 lint/format，**不要手改、不要格式化**。
4. **调试镜像 `github_debug/FuturesMind`**：ghproxy 只读克隆，已落后于真实仓库，仅供调试；**真实 git 仓库是 `AgentSense`**。

## 5. 环境与操作备忘（下次直接照抄）

```bash
# 推送（github.com 直连被墙，必须先启动 Clash 代理 127.0.0.1:7890）
cd C:/Users/19168/Desktop/project4/AgentSense
git -c http.proxy=http://127.0.0.1:7890 push origin main

# 拉取/克隆（只读镜像）
git clone https://ghproxy.net/https://github.com/2779639552/FuturesMind.git

# ruff（全仓 + 单文件）
C:/Users/19168/Desktop/project4/github_debug/FuturesMind/venv_fresh/Scripts/python.exe -m ruff check .
C:/Users/19168/Desktop/project4/github_debug/FuturesMind/venv_fresh/Scripts/python.exe -m ruff format .

# 测试（与 CI 完全一致）
./venv/Scripts/python.exe -m pytest -ra --strict-markers -m "not integration" --timeout=120
```

- **git commit 不带 "Co-Authored-By: Claude" 页脚**（用户要求，已重写历史）。
- AgentSense 自带 `venv/`（flask/apscheduler/langchain 齐全，无 pytest）；`venv_fresh` 未入 .gitignore 时勿误提交。
