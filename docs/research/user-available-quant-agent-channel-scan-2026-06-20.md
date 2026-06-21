# 用户可用量化 Agent 多渠道调研：从信号推送到机会闭环

调研日期：2026-06-20  
目标：继续充分扫描 Google/Web、GitHub、商业产品、官方 API、社区/第三方渠道，判断“能帮用户发现最近哪些股票可能涨、什么消息利好什么、买了以后会怎样”的量化 Agent 应该长什么样。  
状态：调研备忘录，不进入代码实现，不构成投资建议。

## 一句话结论

用户想要的不是“消息型 agent”，也不是传统“量化研究平台”，而是：

> 以持仓和自选股为中心的股票机会雷达：每天筛出少数候选，解释上涨逻辑，判断是否已反应，给出买后剧本、触发提醒、失效条件，并持续记录系统自己的判断准不准。

这轮调研后，我对产品方向的判断更强：

- 商业产品强在实时新闻、异动、扫描和提醒，但多数缺少用户可审计的事后复盘。
- 开源项目强在可拆机制，比如多 agent 研究、价格告警、模拟交易、预测账本、回测日志，但单个项目很少同时具备用户可用闭环。
- 真正好的量化 Agent 不是“告诉你买什么”，而是“把每个推送变成可验证的机会卡”。
- 机会卡必须回答：为什么可能涨、利好传到哪里、市场反应到什么程度、如果买了后续看什么、什么情况说明错了、系统以前类似信号表现如何。

## 本轮调研渠道

### 1. Web / Google-style 搜索

搜索方向：

- AI stock picker / AI stock scanner / stock alerts
- AI trading assistant / stock opportunity radar
- market-moving news alerts / why is it moving
- options flow / dark pool / analyst revision / earnings catalyst
- stock market data API / news API / MCP server

重点查看：

- Benzinga Pro: https://www.benzinga.com/pro/
- Trade Ideas AI Signals: https://www.trade-ideas.com/features/ai-signals/
- TrendSpider AI Strategy Lab: https://trendspider.com/product/artificial-intelligence-ai-trading-strategy-lab/
- TrendSpider Sidekick: https://trendspider.com/product/sidekick-ai-trading-assistant/
- Alpha Vantage docs: https://www.alphavantage.co/documentation/
- Finnhub docs: https://finnhub.io/docs/api
- Massive / Polygon-style docs: https://massive.com/docs
- Benzinga APIs: https://www.benzinga.com/apis/
- Unusual Whales API / MCP: https://unusualwhales.com/public-api

### 2. GitHub 搜索

搜索方向：

- `ai stock trading agent`
- `LLM stock analysis agent`
- `stock screener LLM`
- `TradingAgents stock LLM`
- `MCP stock market quant agent`

重点项目：

- PanWatch: https://github.com/TNT-Likely/PanWatch
- TradingAgents-AShare: https://github.com/KylinMountain/TradingAgents-AShare
- llm-agent-trader: https://github.com/jason8745/llm-agent-trader
- Thesis OS: https://github.com/youngseongshin/thesis-investment-os
- AlphaAnalyst: https://github.com/kbhujbal/AlphaAnalyst-open-source-autonomous-equity-research-agent
- WyckoffTradingAgent: https://github.com/YoungCan-Wang/WyckoffTradingAgent
- AgentQuant: https://github.com/OnePunchMonk/AgentQuant
- trading-mcp: https://github.com/tohsaka888/trading-mcp
- quantcontext-mcp-server: https://github.com/zomma-dev/quantcontext-mcp-server

### 3. 本地源码深挖

本轮实际 clone 并查看了：

- `/tmp/qagent_research/PanWatch`
- `/tmp/qagent_research/TradingAgents-AShare`
- `/tmp/qagent_research/llm-agent-trader`
- `/tmp/qagent_research/thesis-investment-os`
- `/tmp/qagent_research/AlphaAnalyst`

重点看 README、核心服务、告警引擎、建议池、预测复盘、模拟交易、回测日志、预测账本、数据模型、引用校验。

### 4. 第三方/社区信号

搜索了 Reddit、Product Hunt、AI 投研工具目录等方向。社区信号只作为需求侧参考，不作为事实依据。原因是社区评论容易偏主观，且很多“AI stock picker”营销页缺少真实可审计的命中率和失败样本。

社区侧能确认的需求是：

- 用户确实要扫描、提醒、机会排序。
- 用户不信纯 AI 分数，想知道依据。
- 用户容易被 alerts 轰炸，所以需要少量高优先级推送。
- 用户更关心“如果我买了，之后怎么办”，而不是一篇长研报。

## 核心产品判断

### 不要做“消息 agent”

消息 agent 的输出是：

```text
今天有什么新闻。
```

用户真正要的是：

```text
这条新闻为什么可能推动股票？
利好传导到哪家公司？
会影响收入、毛利、订单、利润，还是只是情绪？
股价有没有提前反应？
如果现在买，后续有哪些情景？
什么事实出现说明逻辑错了？
系统过去类似判断准不准？
```

因此产品不应该以新闻流为中心，而应该以机会卡为中心。

### 好工具不是“推荐更多”，而是“压缩得更少”

差工具：

- 每天推 100 条新闻。
- 推 30 个异动股。
- 给 20 个 AI Buy。
- 用户看完仍然不知道该看哪几个。

好工具：

- 每天只给 3-7 个高优先级机会。
- 对每个机会明确时间窗：日内、1-5 天、2-6 周、3 个月。
- 给出机会类型：催化剂、趋势突破、财报预期差、分析师上修、产业链传导、期权异动、资金流、估值修复。
- 对每个机会给出“为什么不是噪音”的证据。
- 事后记录这条机会有没有兑现。

### 用户可用的第一屏

最合理的第一屏不是聊天框，也不是新闻流，而是：

```text
今日机会
  - Top 3-7 opportunity cards
  - 每张卡有 ticker、机会类型、时间窗、触发原因、市场反应、失效条件

我的持仓 / 自选变化
  - 哪些已有股票出现新催化剂、风险、技术位变化
  - 买了以后该看什么

系统复盘
  - 最近推送的信号命中率
  - 哪些失败来自消息错、反应过度、买点晚、数据延迟
```

如果第一屏仍然是“新闻列表”，用户会觉得没用。

## 商业产品扫描

### Benzinga Pro

来源：https://www.benzinga.com/pro/

产品形态：

- 实时市场新闻。
- Movers、signals、scanner、alerts、watchlist、options、calendar、squawk。
- 强调速度和市场异动。

对我们的启发：

- “为什么动”比“发生了什么”更接近用户需求。
- 盘中机会需要快速提醒，但不能只推新闻标题。
- 适合借鉴功能组合：newsfeed + movers + scanner + alerts + watchlist。

不足：

- 强依赖新闻速度和编辑能力。
- 对普通用户来说，如果没有机会压缩和复盘，仍然可能变成信息洪水。

### Trade Ideas / Holly AI

来源：https://www.trade-ideas.com/features/ai-signals/

产品形态：

- AI 交易信号。
- 扫描市场并生成实时 buy/sell 类型机会。
- 更偏交易员工作台。

对我们的启发：

- 用户喜欢“entry / exit / stop / target”这种具体结构，因为它回答了买后怎么办。
- 但如果只给交易信号，没有解释和复盘，容易变成黑箱。

不足：

- 对非专业用户门槛较高。
- 交易信号容易被误用为确定性荐股。

### TrendSpider

来源：

- AI Strategy Lab: https://trendspider.com/product/artificial-intelligence-ai-trading-strategy-lab/
- Sidekick: https://trendspider.com/product/sidekick-ai-trading-assistant/

产品形态：

- 技术分析、策略实验、扫描器、alerts/bots、AI 助手。
- 更偏“图表 + 自动化 + 无代码策略”。

对我们的启发：

- 用户愿意让系统帮他把技术条件变成策略和提醒。
- 但单纯技术条件不够，机会卡应该把技术面放在“是否确认”和“是否过热”位置。

不足：

- 对“消息利好什么”“财务传导”不是主轴。

### Unusual Whales

来源：https://unusualwhales.com/public-api

产品形态：

- Options flow、dark pool、congressional trading、institutional holdings、fundamentals、technical indicators、API / MCP。

对我们的启发：

- 对美股机会雷达，期权异动和暗池可以作为“市场是否提前反应”的重要信号。
- 但 options flow 不能直接等于看涨，需要结合新闻、股价、成交量和历史表现。

不足：

- 单独看异动流容易误导。
- 数据解释门槛高，必须转成用户能理解的情景。

## 开源项目深挖

### 1. PanWatch：最接近“用户可用盯盘助手”的开源样本

Repo: https://github.com/TNT-Likely/PanWatch

定位：

- 自托管 AI 盯盘助手。
- 覆盖 A/H/US。
- 支持组合、自选、实时监控、TradingAgents 集成、全渠道推送。

#### 用户可用点

PanWatch 是本轮最接近“可直接给用户用”的开源形态。它不是单纯 research notebook，而是完整应用：

- Docker 一行启动。
- 首次启动设置账号密码。
- 支持多市场。
- 支持多 AI provider，包括 OpenAI-compatible、DeepSeek、Ollama 等。
- 支持 Telegram、企业微信、钉钉、飞书、Bark、自定义 Webhook。
- 有前端页面：Dashboard、Opportunities、PaperTrading、PriceAlerts、Stocks、History、Agents。

#### 价格告警机制

核心文件：

- `src/core/price_alert_engine.py`
- `src/core/price_alert_scheduler.py`
- `src/core/notify_policy.py`
- `src/core/notify_dedupe.py`

可借鉴机制：

- 条件类型包括 price、change_pct、turnover、volume、volume_ratio。
- 支持 AND / OR 条件组。
- 支持市场时间过滤，也支持全天监控。
- 支持冷却时间。
- 支持每日最大触发次数。
- 支持 once / repeat。
- 支持过期时间。
- 支持每条规则选择不同通知渠道。
- 有去重 bucket，避免同一分钟重复轰炸。

这很重要，因为真实用户最怕两件事：

- 错过关键变化。
- 被重复提醒烦死。

#### 建议池机制

核心文件：

- `src/core/suggestion_pool.py`

可借鉴机制：

- 不同 agent 的建议有不同有效期：盘前、盘中、日报、新闻摘要。
- 有去重窗口，避免重复推同一观点。
- 对 flip-flop 有保护，避免短时间内从买入变卖出。
- `should_alert` 不只是 buy，也包括 alert、avoid、sell、reduce。

这个设计非常接近用户需要的“机会池”：

```text
不是所有分析都推送。
只有达到一定重要性的建议进入提醒。
过期建议自动清理。
重复建议自动合并。
相互冲突的建议需要稳定性检查。
```

#### 模拟交易和结果追踪

核心文件：

- `src/core/paper_trading_engine.py`
- `src/core/paper_trading_scheduler.py`
- `src/core/paper_trading_notifier.py`
- `src/core/prediction_outcome.py`

可借鉴机制：

- 支持自动模拟买入/卖出。
- 按市场分配资金预算。
- 根据信号分数确定仓位比例。
- 默认止损和止盈。
- 支持 trailing stop、signal reversal、time stop。
- 记录账户指标、胜率、最大回撤、交易次数。
- 预测 outcome 评估会在后续天数检查是否兑现。

这说明一个好工具不能只推机会，还要问：

```text
如果按这个信号模拟买入，后续表现如何？
最大回撤多少？
多久触发止损？
有没有达到目标？
```

#### PanWatch 的不足

- 更像盯盘和自动化应用，机会解释深度还可以继续增强。
- 预测复盘偏价格结果，未必能解释“为什么错”。
- 如果用于美股成长股，还需要更强的财务传导、分析师预期、产业链和估值层。

#### 对 Qagent 的启发

PanWatch 可以作为产品外壳参考：

```text
自选/持仓
  -> 条件告警
  -> agent 分析
  -> 建议池
  -> 推送
  -> 模拟交易
  -> outcome 评估
```

这套结构比纯聊天 agent 更接近用户会长期使用的工具。

### 2. TradingAgents-AShare：多 agent 投研委员会

Repo: https://github.com/KylinMountain/TradingAgents-AShare

定位：

- A 股智能投研多智能体系统。
- 模拟机构投研决策流程。
- 输出结构化交易建议。

#### 用户可用点

它的产品化程度也比较高：

- 有 Web 应用和 API。
- 支持自然语言意图，比如“调研某股票短线”。
- 支持 watchlist 和定时分析。
- 支持组合导入和跟踪。
- 报告历史可持久化。
- 决策卡包含方向、信心、目标、止损等结构。
- 支持多 LLM provider。
- 支持 Docker。

#### 多 agent 结构

它的核心不是一个 LLM 直接给结论，而是多角色参与：

- 市场分析。
- 新闻分析。
- 基本面分析。
- 社交/情绪。
- 宏观。
- smart money。
- 多空辩论。
- 风险管理。
- 交易员决策。
- 反思。

这类结构适合解决用户的关键问题：

```text
这是不是单一视角幻觉？
有没有反方意见？
风险经理是否否决？
交易员给出的执行条件是什么？
```

#### 风控与交易员提示的启发

源码 prompt 里有几个值得借鉴的点：

- 风险经理不只是说利空，而是给仓位、止损、前提条件和去风险触发器。
- 交易员需要给方向、仓位、入场区、止损/减仓条件。
- 买入确认要有技术趋势/突破、smart money 流入或基本面催化。
- HOLD 不是默认搪塞，而是在技术、资金、新闻/基本面都没有足够方向时才成立。
- 反思模块会判断成功/失败，并提出信息收集、信号权重、仓位、风险控制改进。

#### 局限

- 系统说明里明确分析耗时可到 1-5 分钟，不适合分钟级盘中交易。
- 曾移除热点榜选股能力，原因涉及外部数据稳定性和合规风险。这一点很重要：越接近“实时荐股”，越需要数据可靠性和边界设计。
- 更偏 A 股投研，对美股期权流、新闻 API、分析师上修等还需要另建数据层。

#### 对 Qagent 的启发

它适合作为“深度分析层”，不适合作为唯一实时推送层。

合理组合是：

```text
扫描器先发现机会
  -> TradingAgents 式多 agent 做研究/反方/风险
  -> 形成机会卡
  -> 只推高优先级
  -> 后续用 outcome ledger 复盘
```

### 3. llm-agent-trader：LLM 决策回测实验台

Repo: https://github.com/jason8745/llm-agent-trader

定位：

- AI-powered stock trading backtesting system。
- FastAPI 后端 + Next.js 前端。
- 使用 yfinance 获取行情。
- 用 LLM 做逐日交易决策分析。

#### 用户可用点

它不是机会雷达，但有一个很有价值的能力：把 LLM 的交易判断放进回测流水线。

关键机制：

- `/llm-backtest-stream` 通过 SSE 实时返回回测进度。
- 每天生成技术上下文、触发事件、LLM 决策。
- 记录 BUY / SELL / HOLD、信心、理由、风险等级。
- 前端图表展示信号、LLM 决策、收益、交易次数、胜率、最大回撤。
- SQLite 保存每日分析日志和事件日志。
- 支持按日期查看历史决策。
- 支持用户对某天决策给反馈，再让 LLM 生成策略改进建议。

#### 源码启发

核心文件：

- `backend/app/api/v1/endpoints/llm_stream.py`
- `backend/app/api/v1/endpoints/backtest_analysis.py`
- `backend/app/api/v1/endpoints/daily_feedback.py`
- `backend/app/llm/strategies/llm_strategy.py`
- `backend/app/utils/backtest_logger.py`

值得借鉴：

- LLM 决策不应该只显示在聊天里，而应该进入结构化日志。
- 每个信号要保存当时的市场上下文和触发事件。
- 用户事后可以对某天决策提出反馈，系统把反馈转为策略文件修改建议。
- 回测过程要实时可视化，不然用户很难相信模型。

#### 局限

- 主要是单 ticker 回测，不是全市场扫描。
- 没有新闻催化剂和财务传导层。
- 数据源是 yfinance，生产级可靠性不足。
- 交易逻辑里有固定买入股数等实验性假设。
- 更像研究实验台，不是日常推送产品。

#### 对 Qagent 的启发

它适合借鉴为“信号实验室”：

```text
机会卡策略
  -> 历史回放
  -> 记录每次 LLM 判断
  -> 用户反馈某次错在哪里
  -> 系统改进规则和提示词
```

但它不能直接变成用户要的“最近哪些会涨”工具，除非补上扫描层和推送层。

### 4. Thesis OS：预测账本和复盘层

Repo: https://github.com/youngseongshin/thesis-investment-os

定位：

- 投资 agent 的 accountability layer。
- 把 thesis、evidence、action、prediction、feedback 分开。
- 不是荐股机器人，而是可审计的判断系统。

#### 为什么重要

用户最后会问：

```text
你之前推的到底准不准？
哪些类型准？
哪些类型经常错？
错是因为消息错、反应过度、买点晚，还是市场环境变了？
```

如果没有预测账本，agent 很容易变成“今天说今天的，明天忘昨天的”。

#### 可借鉴机制

核心文件：

- `thesis_os/models.py`
- `thesis_os/lattice/prediction_ledger.py`
- `thesis_os/lattice/feedback_interpreter.py`
- `thesis_os/alpha/quant_screener.py`
- `thesis_os/alpha/intraday_monitor.py`
- `docs/screeners-and-feedback.md`

关键设计：

- `Evidence`：证据有来源、时间、置信度、解释边界。
- `Thesis`：投资假设有 assumptions、evidence_ids、invalidation、risk、native_horizon。
- `Prediction`：预测有方向、时间窗、到期评估日、置信度、失效条件。
- `ScreenerCandidate`：筛选候选有分数、特征快照、理由。
- `Feedback`：把 process_score 和 result_score 分开。

最值得借鉴的是这句话背后的系统设计：

```text
register prediction -> wait without rewriting the thesis -> grade process and outcome
```

这正好解决 AI 投研工具最大的问题：事后合理化。

#### 局限

- 不是实时推送工具。
- 公共版本以框架和样例为主。
- 不直接解决“今天有哪些股票可能涨”。

#### 对 Qagent 的启发

Qagent 如果想让用户长期信任，必须内置类似机制：

```text
每张机会卡发布时就冻结：
  - 触发时间
  - 原因
  - 方向
  - 时间窗
  - 预期路径
  - 失效条件
  - 参考价格

到期后自动评估：
  - 绝对收益
  - 相对指数收益
  - 最大有利波动
  - 最大不利波动
  - 是否触发止损/失效
  - 失败类型
```

没有这个，产品会变成又一个“看起来很聪明但不可验证”的 AI 工具。

### 5. AlphaAnalyst：研究报告和引用校验层

Repo: https://github.com/kbhujbal/AlphaAnalyst-open-source-autonomous-equity-research-agent

定位：

- 开源 autonomous equity research agent。
- 输入美股 ticker，输出 research memo。
- 覆盖 executive summary、financial snapshot、recent catalysts、DCF、comps、earnings call tone、bull/bear、risks。

#### 用户可用点

它最有价值的不是“给结论”，而是研究证据链：

- SEC EDGAR。
- Polygon。
- FMP。
- Finnhub。
- MarketAux。
- Google News。
- FRED。
- sec-api XBRL。
- FMP transcripts。
- pgvector 长文索引。
- 多 agent + Devil's Advocate。
- DCF 和 comparable multiples。
- citation validator。

#### 关键设计

README 里强调一个重要原则：

```text
LLM 是 writer，不是 knower。
```

这对 Qagent 非常重要。好的投资 agent 不能让 LLM 直接编数字、编财务、编估值。正确做法是：

```text
数字来自 API / filings / 数据库
计算来自确定性代码
LLM 负责组织、解释、提出假设和反方
每个关键数字能追到来源
```

#### 局限

AlphaAnalyst 自己也列了限制：

- US tickers only。
- 没有实时连续更新。
- 没有回测。
- 早期 DCF 仍有单期/多期数据限制。
- API key 和基础设施要求较高。

#### 对 Qagent 的启发

它适合作为“机会卡背后的深度证据层”：

```text
机会卡先告诉用户为什么值得看。
用户点击后进入 evidence view：
  - 财报/filing 证据
  - 新闻催化剂
  - 估值假设
  - 同业比较
  - earnings call tone
  - bull/bear
  - 引用来源
```

## GitHub 搜索结果摘要

本轮 GitHub API 搜索中，值得关注的项目类型如下：

| 类型 | 项目 | 观察 |
|---|---|---|
| 用户级盯盘助手 | `TNT-Likely/PanWatch` | 自托管、A/H/US、组合、自选、告警、推送、模拟交易，最接近产品外壳 |
| A 股多 agent 投研 | `KylinMountain/TradingAgents-AShare` | 多 agent、定时分析、组合跟踪、结构化报告，适合深度研究层 |
| LLM 回测 | `jason8745/llm-agent-trader` | 把 LLM 决策放进回测日志和前端图表，适合信号实验室 |
| 预测账本 | `youngseongshin/thesis-investment-os` | thesis、prediction、feedback 分离，适合复盘和信任层 |
| 美股研报 agent | `kbhujbal/AlphaAnalyst...` | 强调引用、DCF/comps、Devil's Advocate，适合深度证据层 |
| Wyckoff 策略 agent | `YoungCan-Wang/WyckoffTradingAgent` | 候选评分、信号确认、生命周期、反馈，适合技术形态确认 |
| 量化研究平台 | `OnePunchMonk/AgentQuant` | 从股票列表到回测策略，适合研究平台方向 |
| MCP 数据工具 | `tohsaka888/trading-mcp`、`zomma-dev/quantcontext-mcp-server` | 说明金融数据 MCP 化是趋势，方便 agent 调用结构化行情、因子、回测 |

一个明显趋势是：

```text
GitHub 上很多项目在做“agent 分析”；
少数项目在做“告警/推送”；
更少项目在做“预测账本和事后复盘”；
几乎没有项目把三者完整组合成用户级产品。
```

这正是 Qagent 的机会。

## 数据源和 API 层扫描

### 数据层应该按“用户问题”组织

不要先想“接哪个 API”，要先想用户问什么：

```text
为什么可能涨？
  -> 新闻、公告、财报、分析师上修、产业链数据

市场是否已反应？
  -> 价格、成交量、盘前盘后、相对强弱、期权流、暗池

会传导到财务吗？
  -> 收入项、毛利率、订单、capex、inventory、guidance、consensus

如果买了后续看什么？
  -> 关键价格位、事件日期、财报日、管理层口径、分析师修正、资金流变化

系统准不准？
  -> 历史信号、forward return、最大回撤、失败类型
```

### 官方/商业 API 能力矩阵

| 需求 | 可参考数据源 | 说明 |
|---|---|---|
| 行情、OHLCV、技术指标 | Alpha Vantage、Finnhub、Massive/Polygon、Yahoo/Stooq、OpenBB | 免费源适合原型，生产要关注延迟、限流、复权、稳定性 |
| 实时新闻 / 市场异动 | Benzinga API、Finnhub Company News、Alpha Vantage NEWS_SENTIMENT、MarketAux、Google News | 新闻要做去重、重要性评分、ticker 映射 |
| Why is it moving | Benzinga Why Is It Moving、新闻 + 价格异动自建解释 | 这是用户最强需求之一 |
| 分析师上修/评级 | Benzinga Analyst、Finnhub upgrade/downgrade、Alpha Vantage earnings estimates | 适合寻找预期差和估值重定价 |
| 财报 / earnings | Alpha Vantage earnings、Finnhub transcripts、FMP transcripts、SEC EDGAR、sec-api | 需要结构化摘要和历史对比 |
| 估值 / 基本面 | SEC EDGAR、FMP、Finnhub fundamentals、Alpha Vantage fundamentals | 关键数字不能让 LLM 编 |
| 期权流 / 暗池 | Unusual Whales、Massive options | 可以作为“资金是否提前反应”的证据，不应单独变成买入 |
| 宏观 | FRED、央行、统计局、海关、行业协会 | 对产业链和主题机会有用 |
| A/H 股 | AkShare、Tushare、Eastmoney/Tencent/yfinance 等 | 需要单独验证稳定性和授权 |
| MCP 化接入 | Alpha Vantage MCP、Benzinga MCP、Unusual Whales MCP、社区 trading MCP | Agent 工具调用会越来越标准化 |

### 数据层的关键不是多，而是健康度

每条机会卡应该显示数据健康：

```text
source_count: 多少独立来源支持
source_freshness: 最新更新时间
source_type: news / filing / price / options / analyst / fundamentals
source_conflict: 是否存在冲突信息
latency: 是否延迟
license_status: 是否可用于当前用途
```

否则用户不知道这个机会是来自可靠数据，还是来自过期缓存。

## 机会卡应该长什么样

一张用户可用的机会卡最少应该包含：

```text
Ticker / Company
机会类型
时间窗
机会分数
上涨逻辑一句话

触发事件：
  来源、时间、新闻/公告/数据摘要

传导链：
  事件 -> 需求/价格/成本/订单 -> 公司财务项 -> 估值/利润弹性

市场反应：
  盘前/日内/1d/5d/20d 表现
  成交量
  是否过热
  是否已经 price in

买后剧本：
  乐观情景
  中性情景
  悲观情景

操作边界：
  观察位
  失效条件
  风险触发
  复查时间

系统记录：
  类似信号历史表现
  本信号到期日
  后续 outcome 评估
```

这比“AI 评分 87，看涨”更像一个真正能用的工具。

## 推荐的机会评分拆解

不要给一个不可解释的总分。可以拆成 7 个子分：

```text
Opportunity Score =
  Catalyst Novelty
  + Financial Transmission
  + Estimate Revision Potential
  + Market Confirmation
  + Technical Health
  + Valuation Room
  - Crowding / FOMO Risk
  - Data Quality Risk
```

每个子分要能解释：

- Catalyst Novelty：是不是新信息，还是旧闻重复。
- Financial Transmission：能不能传到收入、毛利、订单、利润。
- Estimate Revision Potential：是否可能推动分析师上修或市场预期调整。
- Market Confirmation：价格、量、相对强弱、期权流是否确认。
- Technical Health：上涨是否健康，是否远离均线过热。
- Valuation Room：估值是否已经透支。
- Crowding / FOMO Risk：是否已经暴涨、拥挤、短线追高。
- Data Quality Risk：来源是否可靠、是否冲突、是否延迟。

这样用户可以看到：

```text
不是“系统看涨”。
而是“催化剂强、传导中等、技术确认强，但估值和 FOMO 风险高”。
```

## “买了这个会有什么情况”的产品化答案

用户问“如果买了这个会有什么情况”，不能回答成玄学预测。应该回答成情景树：

### 乐观情景

- 催化剂继续被验证。
- 新闻/公告进入订单、收入、毛利率或 guidance。
- 分析师上修。
- 股价突破关键位且量能确认。
- 类似主题继续扩散。

### 中性情景

- 消息是真的，但传导慢。
- 股价震荡等待财报或订单验证。
- 技术面没有破坏，但也没有继续确认。
- 需要等待下一事件。

### 悲观情景

- 消息已被 price in。
- 股价高开低走或放量不涨。
- 财务传导弱于预期。
- 同业/供应链数据不支持。
- 分析师没有上修，甚至下修。

### 自动跟踪

系统应该在买后自动监控：

- 价格是否触发关键位。
- 是否跌破失效条件。
- 是否出现新利好/利空。
- 是否出现分析师上修/下修。
- 是否有期权/暗池异常。
- 是否到复查时间。

这比一句“可能上涨 10%”更实用。

## 用户视角的好工具标准

### 1. 少量高优先级

每天 3-7 个机会，比 50 个 ticker 更好。

### 2. 解释链必须清楚

每个机会必须能从：

```text
消息 -> 受益环节 -> 公司财务项 -> 市场预期差 -> 价格确认
```

走通。

### 3. 自选和持仓优先

用户最关心的是：

- 我持有的发生了什么？
- 我关注的有没有机会？
- 今天新出现的机会是否值得加入自选？

### 4. 不只推买入，也推风险

有用提醒包括：

- buy watch。
- avoid。
- reduce。
- sell/risk alert。
- thesis changed。
- data conflict。
- already priced in。

### 5. 有失效条件

没有失效条件的推荐不可信。

### 6. 有复查时间

每条机会都应该知道什么时候复查：

- 今天收盘。
- 3 个交易日。
- 财报日。
- analyst day。
- guidance 更新。
- 政策落地。

### 7. 有事后评分

系统必须知道自己准不准。

### 8. 有数据来源

关键数字和关键新闻必须能追到来源。

### 9. 明确边界

工具可以说：

```text
这是一个高优先级观察机会。
这是一个可验证的上涨假设。
这是买入后应该监控的条件。
```

但不应该包装成保证收益。

## 最值得组合的参考架构

从这轮调研看，Qagent 可以借鉴这几个项目各自最强的部分：

```text
PanWatch
  -> 产品外壳：盯盘、告警、推送、建议池、模拟交易

TradingAgents-AShare
  -> 多 agent 研究层：多空辩论、风险经理、交易员、反思

Thesis OS
  -> 预测账本：每个机会冻结、到期复盘、过程分和结果分

llm-agent-trader
  -> 回测实验室：把 LLM 决策放进历史行情逐日回放

AlphaAnalyst
  -> 深度证据层：filings、transcripts、估值、引用校验

Benzinga / Trade Ideas / TrendSpider / Unusual Whales
  -> 商业产品基准：实时新闻、异动、扫描、提醒、期权流、策略实验
```

组合后的理想形态：

```text
数据源
  -> 新闻 / filings / 行情 / 技术指标 / 期权流 / 分析师 / 基本面

扫描层
  -> 全市场候选压缩
  -> 自选/持仓优先
  -> 机会类型识别

研究层
  -> 催化剂解释
  -> 财务传导
  -> 市场反应
  -> 多空辩论
  -> 风险否决

机会卡
  -> 分数拆解
  -> 证据链
  -> 买后剧本
  -> 失效条件
  -> 推送策略

执行辅助
  -> 价格/事件/风险提醒
  -> 模拟交易
  -> 持仓跟踪

复盘层
  -> 预测账本
  -> outcome 评估
  -> 类型命中率
  -> 失败原因
```

## 我对“Qagent 应该做什么”的当前判断

如果目标是用户真的觉得有用，Qagent 不应该从“量化模型平台”开始，也不应该从“新闻总结机器人”开始。

更好的定义是：

> 股票机会雷达 + 持仓副驾驶。

它每天主动告诉用户：

```text
今天最值得看的 3-7 个股票机会是什么？
为什么它们可能上涨？
消息利好传导到哪里？
市场有没有已经反应？
如果我买了，接下来会发生哪些情景？
什么情况说明这条逻辑错了？
系统过去类似机会准不准？
```

这是用户愿意每天打开的产品。

## 不要踩的坑

### 1. 只做新闻摘要

新闻摘要不会让用户觉得你是量化工具。

### 2. 只做聊天问答

用户每天不会主动问 20 个问题。工具应该主动扫描和推送。

### 3. 只给 AI 分数

没有解释和来源的分数不可信。

### 4. 只展示成功案例

没有失败样本，用户迟早不信。

### 5. 只看技术面

技术面能判断是否确认，但不能解释消息为什么利好公司。

### 6. 只看消息

消息是真的也可能已经涨完。

### 7. 忽略通知疲劳

推送太多比不推更糟。

### 8. 忽略合规边界

越接近“买这个会涨”，越需要清楚表达为研究假设、情景和风险，而不是保证收益。

## 下一步如果继续调研

还可以继续深挖三个方向：

1. 竞品 UI 和信息架构  
   重点看 Benzinga Pro、TrendSpider、Trade Ideas、Unusual Whales、Danelfin、Tickeron 的机会页、提醒页、得分页、复盘页。

2. 数据源稳定性和成本  
   重点比较 Alpha Vantage、Finnhub、Benzinga、Massive、Unusual Whales、FMP、sec-api、OpenBB、AkShare/Tushare 的覆盖、延迟、费用、限制和授权。

3. 第一版机会评分 schema  
   把机会卡字段、信号类型、评分公式、失败类型、outcome 评估字段定下来，方便之后进入产品原型。

## 最后判断

用户不是不想要消息，而是不想要“没有决策压缩的消息”。

用户不是单纯想要荐股，而是想要：

```text
少数机会
清楚原因
买后剧本
风险边界
事后验证
```

如果 Qagent 能把这五件事做顺，它就不是普通消息 agent，也不是普通量化工具，而是一个真正用户可用的投资机会操作系统。
