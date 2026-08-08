# 数据采集配置指南（密钥 / Cookie / 运行环境）

> 本文件说明 `data_collection/`（FuturesSentiment 采集子项目）运行**必需的个人密钥与配置**。
> 这些配置**均不提交到仓库**（`.env` 已被 `.gitignore` 排除），克隆后必须自行准备，否则采集与情感分析无法运行。

---

## 一、需要哪些密钥

| 环境变量 | 用途 | 必填？ | 获取方式 |
|---|---|---|---|
| `COOKIES` | 小红书登录态（小红书采集必须登录） | 采集小红书时**必填** | 见下方 [1. 小红书 Cookie](#1-小红书-cookie-cookies) |
| `WEIBO_COOKIE` | 微博 Cookie（免登录可采集部分内容，填了更稳定） | 可选 | 浏览器登录 weibo.com → `F12` → Network → 复制 `Cookie` |
| `OPENAI_API_KEY` | LLM 情感分析（OpenAI 引擎，`llm_sentiment.py`） | 情感分析二选一 | https://platform.openai.com 申请 |
| `ANTHROPIC_API_KEY` | LLM 情感分析（Claude 引擎，`llm_sentiment.py`） | 情感分析二选一 | https://console.anthropic.com 申请 |
| `HF_TOKEN` | HuggingFace 多模态/VLM 图片深度分析（`multimodal_analyzer.py`） | 可选（`--mode deep` 用） | https://huggingface.co/settings/tokens 创建 |
| `DEEPSEEK_API_KEY` | 主系统 LLM（FuturesMind 分析/辩论，也常被情感分析复用） | 主系统**必填** | https://platform.deepseek.com 申请 |

> **说明**：情感分析采用"双引擎"（LLM 引擎 + 规则引擎）。LLM 引擎至少需要 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 之一；都不填时自动降级为规则引擎（效果明显变差）。`--mode deep` 的图片深度分析需要 `HF_TOKEN`。

---

## 二、存放到哪里

### 1. 小红书 Cookie（`COOKIES`）
存放位置：**`data_collection/Spider_XHS/.env`**

```bash
# 方式 A：扫码自动获取（推荐，生成的 Cookie 最长效）
cd data_collection/Spider_XHS
python get_cookie.py
# 完成后会自动写入 Spider_XHS/.env

# 方式 B：手动复制（浏览器登录后临时可用）
# 浏览器登录 https://www.xiaohongshu.com → F12 → Network → 任意请求 → 复制 Cookie
# 写入 Spider_XHS/.env：
#   COOKIES=xxxxx
```

> ⚠️ 必须是**登录后的 Cookie**，未登录状态无效。Cookie 会过期，失效后重新执行 `get_cookie.py`。

### 2. 其余密钥（微博 / LLM / HF）
存放位置：**项目根目录 `.env`**（`FuturesMind/.env`，参考 `.env.example`）

```bash
# 从模板创建
cp .env.example .env
# 编辑填入：
#   DEEPSEEK_API_KEY=...
#   OPENAI_API_KEY=...       # 可选，情感分析 LLM 引擎
#   ANTHROPIC_API_KEY=...    # 可选，情感分析 LLM 引擎
#   WEIBO_COOKIE=...         # 可选，微博采集
#   HF_TOKEN=...             # 可选，VLM 深度分析
```

> **注意**：`web_app.py` / `commodity_demo.py` 会自动 `load_dotenv()` 加载根 `.env`；
> 但 `data_collection/validate/` 下的**独立脚本**（`run_validation.py`、`llm_sentiment.py` 等）不加载 `.env`，
> 需要先在 shell 中 export（`set DEEPSEEK_API_KEY=...`），或通过根项目入口运行。

---

## 三、安装依赖

```bash
# 1. 根项目（LLM 框架 + Web 前端）
pip install -e ".[dev]"

# 2. 采集验证框架
pip install -r data_collection/validate/requirements.txt

# 3. 小红书采集（签名引擎 + 浏览器自动化）
pip install -r data_collection/Spider_XHS/requirements.txt

# 4. Playwright 浏览器内核（小红书/抖音采集必须）
playwright install chromium

# 5. 可选：JS 逆向（生产环境高并发）需 Node.js
```

---

## 四、运行前自检

```bash
# 1) 根项目能否导入
python -c "from tradingagents import ..."     # 或按 README 运行 pytest

# 2) 小红书 Cookie 是否有效
cd data_collection/Spider_XHS && python get_cookie.py   # 重新扫码一次最稳妥

# 3) 情感分析 LLM 是否可用（以 deepseek 为例）
set DEEPSEEK_API_KEY=your_key   # Windows；Linux/macOS 用 export
python -c "import os; print('key set' if os.environ.get('DEEPSEEK_API_KEY') else 'MISSING')"
```

---

## 五、常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| 小红书采集返回空 / 风控 | Cookie 失效或未登录 | 重新 `python get_cookie.py` |
| 情感分析全部为中性 / 结果粗糙 | 未配置 LLM key，降级为规则引擎 | 配置 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` |
| `playwright` 报浏览器缺失 | 未安装浏览器内核 | `playwright install chromium` |
| validate 脚本找不到 key | 独立脚本不加载 `.env` | shell 中 export 后再运行 |
| 前端"情绪分析"无数据 | 未生成 sentiment JSON 或未上传样本数据 | 运行 `generate_tradingagents_sentiment.py`，或使用仓库内置 `data/external_data/` 样本 |

---

## 六、前端内置样本数据

仓库内置了**少量样本数据**（`data/` 目录，已提交），使全新克隆的前端无需任何密钥即可浏览分析界面：

```
data/
├── external_data/        # 各品种 *_sentiment.json（情绪分析）
└── think2_validate/
    └── output/
        ├── batch_*.jsonl # 近期帖子流（情绪动态）
        └── trends/       # 趋势 + 回测权重
```

`web_app.py` 的路径解析规则：优先读用户本地的 `~/.tradingagents/external_data` 与 `~/Desktop/思路2/validate`；**不存在时自动回退到仓库 `data/` 样本**。配置真实密钥并采集后，样本会被本地真实数据自然覆盖。

> 注意：样本数据仅为**展示用途**，不代表最新行情/情绪；如需真实分析请接入自己的数据源。
