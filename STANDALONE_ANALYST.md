# 独立分析师脚本 —— 交付说明(给对方/其他电脑使用)

`analyze_one.py` 是一个**单文件 CLI**,可以在**任何一台电脑**上单独调用
技术面 / 基本面 / 宏观 / 情绪分析师,输出该品种的分析报告。它不依赖本机
的 Web 服务、数据库或任何其他服务——对方只用自己电脑上的 Python、自己的
API Key,连上互联网就能跑。

## 一、给对方的是什么(打包清单)

给对方的包 = `AgentSense` 代码目录中的**这几样**(其他都可以不拷):

```
analyze_one.py            ← 入口脚本
min-requirements.txt      ← 最小依赖清单(10 个包)
tradingagents/            ← 整个包(分析师工厂 + 工具 + 数据层)
.env.example              ← 配置模板(让对方照抄成 .env)
STANDALONE_ANALYST.md     ← 本说明
```

> 不需要: `web_app.py`、`database.py`、`data/`、`~/.tradingagents`、`venv/`、
> `.git/`。不拷 `data/` 也没关系——行情由 akshare 实时联网取,取不到时工具
> 会返回 `DATA_ERROR` 文本,不会崩。

## 二、对方电脑需要什么(3 项前置)

1. **Python 3.10+**(建议 3.12):https://www.python.org/downloads/
2. **能访问互联网**:
   - LLM API(默认 DeepSeek:`https://api.deepseek.com`)
   - 国内行情站点(akshare 取数)
3. **自己的 API Key**(DeepSeek 免费申请:https://platform.deepseek.com)

## 三、对方电脑安装(3 步)

```bat
:: 1. 解压拿到 AgentSense 代码目录,进入该目录
cd AgentSense

:: 2. 建虚拟环境(可选但推荐)
python -m venv venv

:: 3. 装最小依赖(国内网络可加清华镜像)
venv\Scripts\pip install -r min-requirements.txt
:: 或: venv\Scripts\pip install -r min-requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

:: 4. 配置自己的 API Key
copy .env.example .env
::    用文本编辑器打开 .env,填入 DEEPSEEK_API_KEY, 其余保持默认
```

## 四、使用(3 个示例)

```bat
venv\Scripts\python analyze_one.py RB
venv\Scripts\python analyze_one.py CU --analyst technical
venv\Scripts\python analyze_one.py SA --date 2026-09-01 --analyst all --json
```

| 参数 | 说明 |
|---|---|
| `variety` | 品种代码,如 `RB` / `CU` / `SA`(必填) |
| `--analyst` | `all`(默认,技术+基本面+宏观) / `technical` / `fundamental` / `macro` / `sentiment` |
| `--date` | 交易日 `YYYY-MM-DD`,默认今天 |
| `--json` | 以 JSON 输出,便于被其他程序/脚本解析 |

情绪分析师(`--analyst sentiment`)需要该品种存在情绪数据文件,建议先跑核心
三个,或确保数据可达。

## 五、输出约定(接其他程序用)

- 每份报告第一行是机器可解析的方向/置信度行:
  `BIAS: 看多/偏多/中性/偏空/看空 | CONFIDENCE: 高/中/低`
- `--json` 输出结构:

```json
{
  "variety": "RB",
  "trade_date": "2026-09-01",
  "provider": "deepseek",
  "reports": {
    "technical": "# 螺纹钢(RB)...(报告全文)",
    "fundamental": "...",
    "macro": "..."
  }
}
```

## 六、常见问题

- **报 `[ERROR] 缺少 API Key`**: `.env` 没填 `DEEPSEEK_API_KEY`,或 `.env`
  与 `analyze_one.py` 不在同一目录。
- **报告里有 `DATA_ERROR` / `NO_DATA_AVAILABLE`**: 某类数据取不到(网络、
  该品种无此数据),属正常降级,报告其余部分仍有效。
- **换别的 LLM**: 编辑 `.env` 里 `TRADINGAGENTS_LLM_PROVIDER` 与对应 Key,
  支持 OpenAI / Anthropic / Google / 各类 OpenAI 兼容服务(见 `.env.example`)。
