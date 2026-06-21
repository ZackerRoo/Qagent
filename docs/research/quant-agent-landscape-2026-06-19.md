# 量化 Agent 调研备忘录

调研日期：2026-06-19  
目标：先调研第三方平台、论文/公开资料、GitHub 项目，再决定是否实现量化相关 agent。  
结论先行：最值得做的不是“自动荐股/自动下单 agent”，而是“把投研假设转成可复现量化实验的 research agent”。

## 范围与限制

- 已覆盖：公开网页/论文、YouTube 官方演示链接、GitHub 仓库 README 与 GitHub API 元数据快照。
- GitHub stars 是 2026-06-19 通过 GitHub API 抓取的快照，后续会变化。
- 小红书的公开搜索/网页抓取可用性较差，很多内容需要登录 App 或动态加载，无法作为可验证技术来源。本轮只把它作为“市场内容趋势”参考，不把它当架构依据。
- 本备忘录不是投资建议，也不是策略收益承诺。

## 一句话判断

量化 agent 的合理定位应该是：

> 让 LLM 负责研究编排、假设拆解、代码草拟、实验解释和审查；让确定性代码负责数据、回测、风控、统计检验和执行约束。

不要让 LLM 直接决定真实交易订单。成熟项目里最有价值的共识是：LLM 可以在研究链路里提速，但研究结论必须被可复现数据、回测和审查门槛约束。

## 第三方平台和公开资料观察

### Google/公开网页与论文

公开研究大致分成三类：

1. 多智能体投研/交易模拟  
   代表：[TradingAgents](https://github.com/TauricResearch/TradingAgents)、[ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)、[Agentic Trading Lab](https://github.com/Open-Finance-Lab/AgenticTrading)。  
   特点是把金融机构角色拆成 fundamentals、sentiment、news、technical、risk、portfolio manager 等。优点是交互直观，缺点是容易变成“角色扮演式解释”，如果没有回测和统计检验，研究可信度有限。

2. 自动化量化研发  
   代表：[microsoft/RD-Agent](https://github.com/microsoft/RD-Agent)、[microsoft/qlib](https://github.com/microsoft/qlib)。  
   RD-Agent/Qlib 方向更接近我们应该学习的范式：围绕因子挖掘、模型优化、实验反馈循环，而不是只让多个 agent 辩论。

3. Agent 回测 benchmark / 实验约束  
   代表：[BacktestBench / AutoBacktest](https://arxiv.org/abs/2605.17937)、[TradingAgents paper](https://arxiv.org/abs/2412.20138)、[RD-Agent-Quant paper](https://arxiv.org/abs/2505.15155)。  
   这类资料强调 agent 生成策略后必须经过代码执行、回测、交易成本、样本外验证、基准比较和失败解释。

### YouTube

YouTube 上可参考的内容主要来自项目官方演示，而不是散乱教程：

- Qlib README 链接了 RD-Agent 的量化因子挖掘和模型优化演示：[Quant Factor Mining](https://www.youtube.com/watch?v=X4DK2QZKaKY&t=6s)、[Quant Factor Mining from reports](https://www.youtube.com/watch?v=ECLTXVcSx-c)、[Quant Model Optimization](https://www.youtube.com/watch?v=dm0dWL49Bc0&t=104s)。
- TradingAgents README 链接了官方 demo：[TradingAgents Demo](https://www.youtube.com/watch?v=90gr5lwjIho)。
- QuantConnect Lean README 链接了 Lean CLI 教学视频列表：[Lean CLI videos](https://www.youtube.com/watch?v=QJibe1XpP-U&list=PLD7-B3LE6mz61Hojce6gKshv5-7Qo4y0r)。

可借鉴点：演示重点都落在“从想法到实验/回测/报告”的路径，而不是直接展示“今天买什么”。

### 小红书

本轮公开网页检索到的小红书内容不适合作为工程架构依据。原因：

- 很多笔记需要 App 登录或动态加载，公开网页很难稳定复核。
- 内容主题更偏“AI 量化赚钱”“Python 量化教程”“工具推荐”，可验证的系统架构、回测规范和数据偏差讨论较少。
- 后续如果要研究小红书内容生态，建议人工登录后做内容样本标注：标题、受众、承诺强度、是否展示回测、是否披露交易成本、是否有风险提示。

## GitHub 项目对照

### Agent / LLM 投研类

| 项目 | Stars | 定位 | 值得借鉴 | 风险/不足 |
| --- | ---: | --- | --- | --- |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 87,268 | 多智能体 LLM 金融交易框架 | 角色拆分清晰：基本面、情绪、新闻、技术、交易员、风控、组合经理 | 容易偏“讨论式决策”；必须加可复现实验和数据审查 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | 60,246 | AI hedge fund 概念验证 | Agent UX 很强，角色直观，适合学习产品表达 | 名人投资人 agent 更像叙事模板，不适合直接作为量化研究核心 |
| [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | 12,587 | 个人交易 agent，含 MCP/API/研究 autopilot | Hypothesis Registry -> Research Goal -> Backtest Config 的链路很有启发；安全和工具边界做得细 | 功能面很宽，MVP 不能直接照搬 |
| [Open-Finance-Lab/AgenticTrading](https://github.com/Open-Finance-Lab/AgenticTrading) | 232 | LLM trading agent playground | backtest -> API -> dashboard，强调 decision logs、paper trading、leaderboard | 实验平台属性强，需评估成熟度 |
| [OnePunchMonk/AgentQuant](https://github.com/OnePunchMonk/AgentQuant) | 117 | 自主量化研究 agent | analyze -> hypothesize -> backtest -> reflect -> SQLite memory；有 regime card 和 candidate table | 小项目但方向很贴近我们的 MVP |
| [LLMQuant/Magents](https://github.com/LLMQuant/Magents) | 49 | Multi-Agent Generative Trading System | 可作为轻量多 agent 参考 | 社区规模和验证材料有限 |

### 量化底座 / 回测 / 数据基础设施

| 项目 | Stars | 定位 | 值得借鉴 | 适合作为底座吗 |
| --- | ---: | --- | --- | --- |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | 69,401 | 金融数据平台，面向 analysts/quants/AI agents | “connect once, consume everywhere”；Python、API、MCP、Workspace 多表面 | 适合做数据接入层 |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | 51,594 | crypto trading bot | dry-run、backtesting、WebUI、参数优化和 FreqAI | 适合 crypto，股票研究不优先 |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 44,778 | AI-oriented quant research platform | 数据、模型、因子、回测、workflow 较完整；和 RD-Agent 结合紧 | 适合中长期做严肃因子研究 |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | 23,991 | Rust-native 生产级交易引擎 | research/live 同语义、事件驱动、确定性时间模型 | 太重，适合后期生产化 |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 22,042 | Python 回测库 | 成熟、简单、教程多 | 可以做 MVP，但维护活跃度需再看 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 19,966 | 专业算法交易引擎 | 事件驱动、回测/实盘、资产覆盖广、工程化强 | 功能强但集成成本高 |
| [UFund-Me/Qbot](https://github.com/UFund-Me/Qbot) | 17,685 | 中文 AI 自动量化平台 | 股票/基金/策略/回测/模拟/实盘闭环，中文生态友好 | 架构较杂，适合参考不适合直接复用 |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 7,968 | 向量化回测和参数搜索 | 快速批量试验、参数网格、Portfolio analytics | 很适合 MVP 的回测层 |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | 7,304 | 组合绩效分析 | 报告、Sharpe、drawdown 等绩效展示 | 适合作为报告层 |

### 金融 LLM / 分析平台

| 项目 | Stars | 定位 | 值得借鉴 |
| --- | ---: | --- | --- |
| [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | 20,522 | 金融大模型 | 金融语料、模型和情绪/文本处理参考 |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 15,461 | 金融强化学习 | RL trading 研究参考，不适合 MVP 起步 |
| [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) | 13,534 | 自动化数据/模型 R&D agent | agent loop、实验反馈、trace viewing、quant scenario |
| [AI4Finance-Foundation/FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | 7,313 | 金融分析 agent 平台 | 财报/公司分析型 agent 参考 |

## 架构共识

### 值得复制

1. Typed handoff  
   Agent 之间不要传自然语言长段落，而要传结构化对象：hypothesis、universe、signal_spec、backtest_config、risk_report、research_memo。

2. Hypothesis registry  
   每条假设都要有 ID、来源、资产池、变量、时间窗、预期方向、验证条件、失败条件。

3. Research loop  
   建议采用：

   ```text
   idea -> hypothesis schema -> data audit -> signal/formula -> backtest -> robustness -> critique -> memo -> memory
   ```

4. Deterministic backtest  
   回测必须由确定性代码执行，LLM 只能生成或修改研究配置，不能凭口头判断宣称策略有效。

5. Critic / bias checker  
   必须检查未来函数、幸存者偏差、交易成本、样本太小、多重检验、行业/市值/beta 暴露、换手率、拥挤度。

6. Research memory  
   保存失败实验和无效假设。量化研究里“哪些不 work”比只保存好结果更重要。

7. Human review gate  
   实盘之前必须有人类确认。MVP 甚至不要接实盘，只做研究和 paper/backtest。

### 不建议复制

1. 名人投资人角色扮演  
   适合营销和 demo，不适合严肃量化。

2. 直接从自然语言到下单  
   风险过高，也无法解释偏差来源。

3. 只看收益曲线  
   没有成本、换手、滑点、样本外、基准对照、暴露分解的收益曲线没有研究价值。

4. Agent 自己改策略、自己评估、自己宣布成功  
   至少需要独立 evaluator 和固定 benchmark。

5. 一开始做全市场、全资产、实盘执行  
   scope 太大，容易变成工具拼盘。

## 推荐 MVP

名称建议：`QResearch Agent` 或 `Hypothesis-to-Backtest Agent`

定位：

> 把投研假设转成可复现的量化实验，并输出失败原因、稳健性和下一步验证路径。

第一阶段不做自动交易，只做研究。

### MVP 工作流

1. 用户输入自然语言假设  
   例：“AI 数据中心液冷需求上升，液冷供应链小市值公司未来 3 个月可能跑赢。”

2. Agent 生成 hypothesis schema  
   包含 universe、event date、holding window、signal definition、benchmark、expected direction、data requirements。

3. Data auditor 检查数据  
   明确哪些数据可得，哪些缺失，是否存在 survivorship / lookahead 风险。

4. Signal builder 生成可执行信号  
   初期限制为少数模板：事件研究、均线/动量、财务增长、估值分组、主题篮子。

5. Backtest runner 执行回测  
   初期建议用 vectorbt 或轻量 pandas/vectorized engine；后续再考虑 Qlib/Lean。

6. Robustness critic 审查  
   强制输出交易成本后收益、不同窗口、不同市场阶段、行业/市值暴露、样本外结果。

7. Memo writer 输出研究备忘录  
   结论必须分成：有效证据、无效证据、风险、下一步数据需求。

### 推荐技术路线

短期：

- Python
- pandas / numpy / scipy / statsmodels
- yfinance 或 OpenBB 做美股数据起步
- vectorbt 做快速向量化回测
- quantstats 做绩效报告
- SQLite 保存 experiment registry
- Markdown/HTML 输出 research memo

中期：

- 接入 Qlib 做因子研究和模型训练
- 接入 OpenBB MCP/API 做统一数据层
- 加 local CSV/Parquet/DuckDB data bridge
- 加 paper trading 但仍保持 human approval

长期：

- 如果要生产级执行，再评估 Lean 或 NautilusTrader
- 接入 broker 前必须有 mandate、kill switch、audit ledger、order guard、position/risk limits

## Agent 设计草图

```text
User
  |
  v
Research Orchestrator
  |
  +--> Hypothesis Parser
  +--> Data Auditor
  +--> Signal Builder
  +--> Backtest Runner
  +--> Robustness Critic
  +--> Research Memo Writer
  +--> Experiment Memory
```

每个模块的输入输出都应该是结构化 schema，而不是自由文本。

## 后续实现前需要决定

1. 市场范围  
   - 美股成长股/AI 产业链：数据更容易，适合快速 MVP。
   - A 股/港股产业链：更贴合中文投研，但数据源、复权、停牌、涨跌停、财务口径更复杂。
   - 通用多市场：最好但不适合作为第一阶段。

2. 研究类型  
   - 事件研究：最贴合“新闻 -> 假设 -> 验证”。
   - 多因子研究：更系统，但需要更强数据底座。
   - 策略回测：最像交易系统，但最容易过拟合。

3. 是否先做 Codex Skill 还是独立应用  
   - Skill：快，适合把研究流程标准化。
   - CLI/Python package：适合真正跑数据和回测。
   - Web app：展示好，但实现成本更高。

## 我的建议

先做一个 Codex + Python CLI 混合方案：

- Codex Skill 负责研究流程和审查清单。
- Python CLI 负责数据拉取、信号构建、回测和报告生成。
- 第一批只支持事件研究和简单因子分组，不碰实盘。

这样可以直接复用你已有的投研 Skill：

- `serenity-alpha`：从新闻找假设。
- `tam-adj-peg`：判断成长股估值假设。
- `gf-dma-health-index`：把走势健康度转成可检验技术变量。
- `bayesian-intrinsic-growth-valuation`：把估值隐含增长转成场景假设。

新的量化 agent 则负责把这些假设落到数据和回测里。

## Sources

- [TradingAgents GitHub](https://github.com/TauricResearch/TradingAgents)
- [TradingAgents arXiv 2412.20138](https://arxiv.org/abs/2412.20138)
- [ai-hedge-fund GitHub](https://github.com/virattt/ai-hedge-fund)
- [Vibe-Trading GitHub](https://github.com/HKUDS/Vibe-Trading)
- [AgenticTrading GitHub](https://github.com/Open-Finance-Lab/AgenticTrading)
- [AgentQuant GitHub](https://github.com/OnePunchMonk/AgentQuant)
- [Qlib GitHub](https://github.com/microsoft/qlib)
- [RD-Agent GitHub](https://github.com/microsoft/RD-Agent)
- [RD-Agent-Quant arXiv 2505.15155](https://arxiv.org/abs/2505.15155)
- [BacktestBench / AutoBacktest arXiv 2605.17937](https://arxiv.org/abs/2605.17937)
- [OpenBB GitHub](https://github.com/OpenBB-finance/OpenBB)
- [QuantConnect Lean GitHub](https://github.com/QuantConnect/Lean)
- [NautilusTrader GitHub](https://github.com/nautechsystems/nautilus_trader)
- [vectorbt GitHub](https://github.com/polakowo/vectorbt)
- [quantstats GitHub](https://github.com/ranaroussi/quantstats)
- [Qbot GitHub](https://github.com/UFund-Me/Qbot)
- [FinGPT GitHub](https://github.com/AI4Finance-Foundation/FinGPT)
- [FinRL GitHub](https://github.com/AI4Finance-Foundation/FinRL)
- [FinRobot GitHub](https://github.com/AI4Finance-Foundation/FinRobot)
