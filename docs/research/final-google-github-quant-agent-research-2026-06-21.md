# 最后一轮 Google + GitHub 调研：股票机会量化 Agent 应该做成什么

调研日期：2026-06-21  
调研范围：Google/公开网页、GitHub 开源项目、数据 API、合规与用户可用性  
定位：产品与实现调研，不构成投资建议，不承诺收益，不建议直接自动交易  

---

## 0. 最终结论

这轮调研后，我认为我们要做的不是“消息推送 Agent”，也不是“只会说某股票可能涨的聊天机器人”，而是：

```text
股票机会雷达
+ 买卖点决策卡
+ 自选股/持仓监控
+ 条件提醒
+ 历史同类信号验证
+ 交易日志/复盘
+ 数据可信度和合规边界
```

用户真正想要的不是“新闻利好什么”，而是：

```text
哪只股票值得看
为什么可能涨
现在能不能买
如果不能追，等哪里
突破买还是回调买
止损/失效在哪里
目标/减仓在哪里
买了以后看什么
什么情况说明逻辑错了
历史类似信号表现如何
```

所以产品一句话应定义为：

> 一个面向美股投资者的 AI 量化机会决策系统。它每天扫描股票、新闻、财报、技术形态、资金和事件催化，生成可验证的机会卡，并持续跟踪买点、卖点、止损、持仓风险和真实结果。

核心修正：

- 不做单纯新闻 Agent，因为用户觉得“消息本身没用”。
- 不做黑箱荐股，因为用户很难信任，合规风险也高。
- 不先做自动交易，因为没有实盘验证和责任边界。
- 先做“可验证的推荐候选 + 买卖点计划 + 结果复盘”。

---

## 1. 本轮新增调研样本

### 1.1 AI 股票信号/选股产品

本轮重点看了这些产品：

| 产品 | 关键能力 | 对我们的启发 |
|---|---|---|
| [Danelfin](https://danelfin.com/) | AI Score、Top Stocks、Trade Ideas、历史买入信号胜率 | 用户会期待“分数 + 历史胜率 + 持续跟踪”，但必须解释概率不是确定性 |
| [Danelfin How it Works](https://danelfin.com/how-it-works) | AI Score 1-10，表示未来约 3 个月跑赢市场的概率；强调可解释 AI | 我们的分数必须拆成可解释因子，不应只给神秘概率 |
| [Danelfin Trade Ideas](https://danelfin.com/trade-ideas) | 按 1M/3M/6M/1Y 历史信号表现筛选 Trade Ideas | 每条机会卡应带历史相似样本表现 |
| [Tickeron](https://tickeron.com/) | Daily Buy/Sell Signals、entry/exit prices、confidence levels、AI bots | 买卖点和置信度已经是用户预期，不是高级功能 |
| [LevelFields](https://www.levelfields.ai/next-gen-stock-screener) | 事件驱动筛选、24 类事件、历史表现、买卖时机、提醒 | “事件 + 历史相似走势 + 何时买卖”比新闻摘要更有价值 |
| [Kavout](https://www.kavout.com/ai-stock-picker) | 9000+ 美股、Kai Score、Stock Rank、Technical Rating | 机会雷达需要覆盖大股票池，且分数要拆为多个维度 |
| [Trade Ideas](https://www.trade-ideas.com/features/ai-signals/) | Holly AI、实时买卖信号、entry/exit points、策略验证 | 机会卡必须有明确入场、出场和回测痕迹 |
| [Amsflow Lisa](https://amsflow.com/investment-research-assistant) | 自然语言研究助手、筛选、图表、仪表盘 | 自然语言筛选是好交互，但底层必须结构化 |
| [Amsflow Features](https://amsflow.com/features) | 多时间框架支撑/阻力、基本面异常、评分卡 | 支撑阻力、异常检测和基本面评分应进入卡片 |

### 1.2 传统/成熟股票工具

这些工具不是都靠 AI，但用户对股票工具的预期来自它们：

| 产品 | 关键能力 | 对我们的启发 |
|---|---|---|
| [TradingView Pine Screener](https://www.tradingview.com/support/solutions/43000742436-tradingview-pine-screener-key-features-and-requirements/) | 用 Pine 脚本扫描 watchlist | 后续规则应能导出成 Pine/策略脚本 |
| [TradingView Watchlist Alerts](https://www.tradingview.com/support/solutions/43000739708-watchlist-alerts-your-trading-edge/) | 对多个标的设置 watchlist 条件提醒 | 自选股/持仓提醒是核心，不是附加功能 |
| [TradingView Pricing](https://www.tradingview.com/pricing/) | 价格提醒、技术提醒、watchlist alerts、webhook | 提醒系统要支持多条件和 webhook |
| [Stock Rover](https://www.stockrover.com/) | 筛选、研究、组合管理 | 用户不仅要找机会，还要管组合 |
| [Stock Rover Alerts](https://www.stockrover.com/help/alerts/alerts-overview/) | 技术/基本面事件自动提醒 | 基本面提醒必须和技术提醒并列 |
| [Seeking Alpha Quant](https://about.seekingalpha.com/quant-sell-ratings) | Quant Rating、Factor Grades、筛选 | 基础评分应至少覆盖估值、成长、盈利质量、动量、EPS 修正 |

### 1.3 GitHub 开源项目

本轮重点看了这些项目：

| 项目 | 关键能力 | 对我们的启发 |
|---|---|---|
| [RyanJHamby/stock-screener](https://github.com/RyanJHamby/stock-screener) | 扫描 3800+ 美股、买卖信号、趋势阶段、基本面过滤、止损、2:1 R/R | 一个可用工具必须同时有扫描、排名、止损和风险收益比 |
| [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | 多 Agent 投研、回测、交易日志、Shadow Account、报告、策略导出 | 机会卡之后必须有日志、复盘和“如果按规则执行会怎样” |
| [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) | 市场复盘、风险管理、交易计划、日志、postmortem | 好的交易 Agent 是流程管理工具，不是替用户下判断 |
| [Lumiwealth/lumibot](https://github.com/Lumiwealth/lumibot) | 回测、paper/live 同代码、AI agents、SEC/FRED/券商接入 | 架构上要区分研究、回测、模拟、实盘，初期只做研究/模拟 |
| [EthanAlgoX/LLM-TradeBot](https://github.com/EthanAlgoX/LLM-TradeBot) | 多 Agent、市场状态、风险审计、多时间框架 | 决策引擎要有 risk audit veto，不能让 LLM 直接输出买卖 |
| [pkjmesra/PKScreener](https://github.com/pkjmesra/PKScreener) | 扫描器、突破值、回测、Telegram 提醒、ATR trailing stop | 扫描器链路、提醒和动态止盈应产品化 |
| [chand1012/stonks](https://github.com/chand1012/stonks) | 趋势股回调、仓位大小、bracket orders、止损止盈、移动止损 | 买点/卖点规则可以先从简单明确的公式开始 |

GitHub 结论很明确：

```text
真正能跑起来的项目，都不是只做“看涨/看跌”。
它们都包含：筛选、规则、止损、仓位、回测、提醒、日志、风控。
```

---

## 2. 用户视角：什么才是好的量化工具

站在用户角度，一个好的量化股票工具不应该只是回答：

```text
今天有什么消息？
```

而应该回答：

```text
今天有什么可以行动的机会？
我能不能买？
如果买，怎么控制风险？
如果不买，等什么条件？
如果已经买了，今天要不要处理？
系统过去推荐类似机会时表现如何？
```

因此产品要满足 9 个标准。

### 2.1 能主动扫描，而不是等用户提问

用户不想每天自己输入 100 个 ticker。

系统应该每天自动扫描：

- 全市场强势股；
- 财报后异动；
- 分析师上修；
- 产业链/新闻催化；
- 异常成交量；
- 技术突破；
- 健康回调；
- 期权异动；
- 内部人/13F/回购；
- 自选股/持仓变化。

输出不应该是大列表，而应该是分层：

```text
今日最值得看的 5-10 个机会
观察名单
触发买点名单
风险升高名单
已失效名单
```

### 2.2 每条推荐必须变成“交易计划”

只说“NVDA 受益 AI”没有用。

每条机会卡必须包含：

```text
ticker
当前价格
推荐状态
上涨逻辑
入场条件
不能追的位置
止损/失效位
目标/减仓位
风险收益比
观察期限
历史相似信号表现
需要继续验证的数据
```

状态不能只有 Buy/Sell。更合理的是：

| 状态 | 含义 |
|---|---|
| `new_idea` | 新发现，逻辑待验证 |
| `watch` | 值得观察，但未到买点 |
| `setup_ready` | 形态和基本面准备好，等触发 |
| `triggered` | 入场条件触发 |
| `extended` | 已过度拉升，不建议追 |
| `active` | 已进入持仓监控 |
| `risk_elevated` | 风险升高，需要处理 |
| `invalidated` | 逻辑失效 |
| `closed` | 机会结束 |
| `postmortem_done` | 已复盘 |

### 2.3 要给买点，但不能胡乱喊买

用户要买卖点，产品必须给。但买点要以条件形式表达：

```text
不是：现在买。
而是：如果价格放量突破 128.5 且收盘站稳，则触发突破入场。
```

可支持的入场类型：

| 入场类型 | 适合场景 | 需要显示 |
|---|---|---|
| 突破买点 | 强趋势/新高/平台突破 | pivot、确认价格、放量要求、失败回撤 |
| 回踩买点 | 趋势健康但短线过热 | 20/50 日线、支撑区、反转确认 |
| 财报确认买点 | 财报超预期但未完全定价 | 财报 gap、成交量、上修、后续漂移 |
| 事件回调买点 | 事件利好但价格已冲高 | 事件影响、回调区、风险收益比 |
| VWAP/ORB 买点 | 盘中短线 | VWAP、开盘区间、成交量确认 |
| 均值回归买点 | 大盘股短期超跌 | 超跌程度、流动性、时间止损 |

每个买点都必须配套：

```text
entry_trigger
entry_zone
no_chase_above
confirmation
initial_stop
position_size_hint
risk_reward
```

### 2.4 要给卖点和失效条件

多数工具容易漏掉“买了以后怎么办”。这恰恰是用户最需要的。

卖出逻辑要分 6 类：

| 卖出类型 | 示例 |
|---|---|
| 初始止损 | 跌破买点下方 5%-8%，或跌破关键均线/前低 |
| 技术失效 | 放量跌破 50DMA，突破失败回到箱体 |
| 基本面失效 | 指引下修、订单取消、毛利率恶化 |
| 目标减仓 | 到达 1R/2R/前高/测量目标后部分减仓 |
| 移动止盈 | ATR trailing stop、10/20 EMA、前一日低点 |
| 时间止损 | 10/20/60 日内没有按预期发展则退出观察 |

每张卡要明确：

```text
stop_loss
invalidation
target_1
target_2
trailing_rule
time_stop
event_exit
```

### 2.5 要跟踪持仓，而不只是找新票

一个用户买入后，系统要每天问：

```text
是否触发止损？
是否接近第一目标？
是否需要上移止损？
是否有坏消息改变逻辑？
是否财报/FOMC/FDA 前风险过高？
是否和其他持仓高度相关？
是否仓位过大？
```

因此需要两张表：

```text
watchlist_monitor
portfolio_monitor
```

持仓监控字段：

```text
ticker
entry_price
entry_date
current_price
unrealized_return
max_gain_since_entry
max_drawdown_since_entry
initial_stop
current_stop
target_1
target_2
days_held
thesis_status
next_event
alert_state
```

### 2.6 要能解释“为什么可能涨”

解释不能停在新闻标题。

正确链路是：

```text
事件/财报/数据
→ 真实需求或预期变化
→ 财务传导项
→ 市场是否低估
→ 哪家公司最有弹性
→ 当前价格是否已反映
→ 未来几个季度如何验证
```

对应字段：

```text
catalyst
beneficiary_path
revenue_line
margin_impact
estimate_revision
valuation_context
confirmation_metrics
```

### 2.7 要显示“历史同类信号表现”

这是区分“可验证研究系统”和“聊天机器人”的核心。

每条机会卡都应该显示：

```text
similar_signal_count
win_rate_5d
win_rate_20d
median_return_20d
avg_max_drawdown
best_case
worst_case
sample_period
benchmark
```

例如：

```text
类似信号：财报 EPS/Revenue 双超预期 + 当日涨幅 3%-8% + 次日未回补缺口 + 分析师上修
历史样本：N=146
20 日相对收益中位数：+3.2%
胜率：58%
最大回撤中位数：-5.6%
```

### 2.8 要有“不要买”的判断

用户不是只想听机会，也想避免追高。

必须能输出：

```text
逻辑好，但价格已过热，等待回调。
事件好，但财务传导弱，只适合观察。
技术突破失败，不再跟踪。
期权异动明显，但无新闻/基本面确认，高风险。
```

过热/不追字段：

```text
distance_from_20dma
distance_from_50dma
gap_extension
volume_climax
rsi
atr_extension
fomo_risk
no_chase_above
```

### 2.9 要有复盘和学习闭环

如果系统每天推 10 只，但不记录结果，它永远不会变好。

每条机会卡必须在 1/5/10/20/60 日后更新：

```text
是否触发买点
触发后收益
未触发但上涨/下跌
是否触发止损
是否达到目标
最大浮盈
最大回撤
相对 SPY/QQQ 表现
失败原因
```

失败原因分类：

```text
signal_false_positive
late_entry
market_regime_turn
earnings_reversal
news_misread
liquidity_trap
overextended_entry
stop_too_tight
no_follow_through
```

---

## 3. 最终产品模块

完整系统应拆成 12 个模块。

### 3.1 Market Regime 市场环境层

目的：决定今天是否适合积极找买点。

输入：

- SPY/QQQ/IWM 趋势；
- 20/50/100/200 日均线；
- 广度；
- VIX；
- 利率/美元；
- 成长股 vs 价值股；
- 半导体/AI/软件/医疗等强弱；
- distribution day / follow-through day。

输出：

```text
risk_on
selective_long
neutral
defensive
cash_priority
```

这层必须影响所有股票机会的权重。

### 3.2 Universe 股票池层

初期建议：

```text
美股普通股 + ADR + ETF 可选
市值 > 300M 或 500M
3 个月平均成交额 > 5M 美元
排除极低价、极低流动性、异常停牌
```

后续可以分：

- 大盘成长股；
- 中小市值弹性股；
- AI/半导体/数据中心；
- SaaS；
- 医疗科技；
- 机器人/工业自动化；
- 能源/电力；
- 消费；
- 金融。

### 3.3 Signal Scanner 信号扫描层

主扫描器：

| 信号 | 用途 |
|---|---|
| PEAD 财报后漂移 | 财报后继续上涨机会 |
| 分析师预期上修 | 基本面预期改善 |
| 成长趋势/相对强度 | 强者恒强 |
| 健康回调 | 好股票回到可买区 |
| 突破确认 | 新趋势开始 |
| 事件催化 | 新闻转化为财务影响 |
| 异常成交量 | 市场关注度变化 |
| 期权异动 | 资金/预期辅助确认 |
| 内部人/回购/13F | 慢变量确认 |

### 3.4 Opportunity Ranking 排名层

每只股票至少评分：

```text
catalyst_score
fundamental_score
technical_score
revision_score
valuation_score
liquidity_score
risk_score
timing_score
historical_edge_score
```

最终不是一个黑箱总分，而是：

```text
conviction = reasoned weighted score
```

并展示拆分。

### 3.5 Opportunity Card 机会卡

核心输出格式：

```yaml
opportunity_card:
  ticker: NVDA
  company: NVIDIA
  sector: Semiconductors
  market_cap: ...
  price: ...
  avg_volume: ...
  status: setup_ready
  horizon: 2-8 weeks
  thesis: ...
  catalyst:
    type: earnings_revision / event / breakout / pullback
    source: ...
    timestamp: ...
  fundamentals:
    revenue_growth: ...
    eps_growth: ...
    gross_margin: ...
    next_earnings_date: ...
    valuation: ...
  technicals:
    trend: ...
    rs_rank: ...
    dma_20: ...
    dma_50: ...
    dma_200: ...
    support: ...
    resistance: ...
  entry_plan:
    entry_type: breakout
    trigger_price: ...
    entry_zone: ...
    confirmation: ...
    no_chase_above: ...
  exit_plan:
    initial_stop: ...
    invalidation: ...
    target_1: ...
    target_2: ...
    trailing_stop: ...
    time_stop: ...
  risk_reward:
    risk_per_share: ...
    upside_to_target_1: ...
    reward_risk_ratio: ...
  monitoring:
    watch_items:
      - price closes below 50DMA
      - estimate revisions turn negative
      - volume dries up after breakout
  historical_edge:
    sample_count: ...
    win_rate_20d: ...
    median_return_20d: ...
    max_drawdown_median: ...
  audit:
    data_sources: ...
    data_delay: ...
    confidence: ...
    caveats: ...
```

### 3.6 Entry/Exit Engine 买卖点引擎

不能让 LLM 自由编价格。价格必须来自规则：

```text
pivot
previous high
gap high/low
20/50DMA
ATR
VWAP
support/resistance
recent swing low
earnings gap level
```

LLM 负责解释，规则引擎负责数值。

### 3.7 Alert Engine 提醒系统

提醒类型：

- 价格触发；
- 均线触发；
- 放量触发；
- 突破/跌破；
- 财报日期；
- 分析师上修/下修；
- 新闻事件；
- 期权异动；
- 目标价/止损；
- 持仓集中风险。

提醒状态：

```text
pending
triggered
acknowledged
expired
invalidated
closed
```

### 3.8 Portfolio Monitor 持仓监控

支持用户输入或导入：

```text
ticker
shares
entry_price
entry_date
strategy_tag
thesis
stop
target
```

每日输出：

```text
需要处理
继续持有
上移止损
到达目标
风险升高
逻辑失效
```

### 3.9 Trade Journal 交易日志

记录：

```text
计划价格
实际成交价格
是否按计划执行
提前卖出/追高/犹豫
实际结果
错误类型
```

这是 Vibe-Trading 和 claude-trading-skills 给我们的关键启发：好的 Agent 应该让用户的交易过程变得可复盘。

### 3.10 Backtest / Event Study 回测和事件研究

初期必须支持：

```text
信号发生日冻结
未来 1/5/10/20/60 日收益
相对 SPY/QQQ 收益
最大浮盈
最大回撤
是否触发止损
是否触发目标
```

要避免未来函数：

- 使用 point-in-time 数据；
- 新闻/财报按发布时间处理；
- 财务数据按可用日处理；
- 分析师修正按时间戳处理。

### 3.11 Research Memory 研究记忆

系统应记住：

```text
哪些行业最近有效
哪些策略最近失效
哪些股票反复出现
哪些新闻类型容易误判
用户哪些交易行为亏钱
```

### 3.12 Trust Layer 可信层

每个结论显示：

```text
数据来源
数据延迟
样本数
回测区间
是否模拟
是否真实成交可执行
主要风险
不能用于什么
```

---

## 4. 数据源建议

### 4.1 价格和成交量

候选：

- [Databento Stocks](https://databento.com/stocks)：实时/历史 tick、intraday、market feeds；
- [Massive/Polygon Stocks](https://massive.com/docs/rest/stocks/overview)：REST/WebSocket、snapshot、trades、quotes；
- [Finnhub](https://finnhub.io/docs/api)：实时价格、基本面、经济与另类数据；
- [Alpha Vantage](https://www.alphavantage.co/)：价格、技术指标、基本面、新闻情绪，适合 PoC；
- FMP：基本面和估值字段相对方便。

建议：

```text
PoC 可以用 Alpha Vantage / FMP / Finnhub。
真实产品至少需要一个稳定的美股行情 API。
盘中信号不能依赖免费慢数据。
```

### 4.2 新闻、财报和分析师

候选：

- [Benzinga APIs](https://www.benzinga.com/apis/)：低延迟新闻、历史新闻、分析师评级；
- Finnhub：新闻、earnings、estimates；
- FMP：earnings calendar、analyst estimates、ratings；
- Alpha Vantage：News & Sentiment、Earnings Estimates；
- SEC EDGAR：10-K/10-Q/8-K；
- Nasdaq/公司 IR：财报日历。

关键字段：

```text
event_time
headline
source
ticker_mapping
event_type
eps_actual
eps_estimate
revenue_actual
revenue_estimate
guidance
analyst_revision
price_target_change
rating_change
```

### 4.3 期权数据

候选：

- [Databento Options](https://databento.com/options)：OPRA 覆盖；
- [Databento OPRA](https://databento.com/datasets/OPRA.PILLAR)：US options trades/NBBO；
- [OPRA](https://www.opraplan.com/)：美国期权官方汇总源；
- Massive/Polygon Options；
- Barchart、Unusual Whales、Market Chameleon 等商业工具。

建议：

```text
期权异动初期只能作为辅助确认。
不要把 unusual options flow 单独作为主推荐信号。
```

### 4.4 合规与风险资料

参考：

- [SEC/NASAA/FINRA AI Investment Fraud Alert](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/artificial-intelligence-fraud)
- [FINRA Regulatory Notice 24-09](https://www.finra.org/rules-guidance/notices/24-09)
- [FINRA AI Topic](https://www.finra.org/rules-guidance/key-topics/artificial-intelligence)
- [FINRA Rule 2210 FAQ](https://www.finra.org/rules-guidance/guidance/faqs/advertising-regulation)
- [SEC AI Washing Enforcement](https://www.sec.gov/newsroom/press-releases/2024-36)
- [FINRA Reg BI Overview](https://www.finra.org/rules-guidance/key-topics/regulation-best-interest)

产品表达必须避免：

```text
保证上涨
稳赚
确定买入
AI 已预测
历史胜率必然复现
无风险
替用户做个性化投资建议
```

建议使用：

```text
候选机会
触发条件
观察状态
风险收益计划
历史样本表现
失效条件
用户自行确认
```

---

## 5. 和已有四个投资研究 Skill 的关系

你已有的 4 个 Skill 很适合作为机会卡的“解释层”和“验证层”，但不能单独完成产品。

### 5.1 Serenity Alpha

用途：

```text
新闻/事件 → 投资假设 → 财务传导 → 受益公司 → 验证路径
```

在产品中对应：

```text
catalyst_analysis
beneficiary_mapping
financial_transmission
verification_plan
```

### 5.2 TAM-Adj-PEG

用途：

```text
成长股估值是否贵
增长空间是否足够
质量是否支撑估值
```

在产品中对应：

```text
valuation_context
growth_duration
quality_score
overpricing_risk
```

### 5.3 GF-DMA Health Index

用途：

```text
走势是否健康
是否过热
是否可以回调买
```

在产品中对应：

```text
technical_health
entry_timing
no_chase_above
pullback_zone
trend_risk
```

### 5.4 Bayesian Intrinsic Growth Valuation

用途：

```text
市场是否过度定价
估值隐含的增长是否合理
```

在产品中对应：

```text
implied_growth
valuation_expectation_gap
fomo_detection
thesis_probability_update
```

最终组合：

```text
Serenity Alpha 找线索
TAM-Adj-PEG 看估值
GF-DMA 找买点/判断过热
Bayesian Intrinsic Growth Valuation 判断是否已透支
Backtest/Event Study 验证历史基准
Alert/Portfolio Monitor 跟踪执行结果
```

---

## 6. 最终能力清单

如果要一次性做完整方向，不拆“后面增强”，能力边界应如下。

### 6.1 必须有

- 全市场股票扫描；
- 自选股扫描；
- 持仓监控；
- 今日机会榜；
- 今日风险榜；
- 股票基础信息；
- 新闻/财报/事件摘要；
- 上涨逻辑；
- 入场计划；
- 不追高线；
- 初始止损；
- 失效条件；
- 目标位；
- 移动止盈；
- 时间止损；
- 风险收益比；
- 历史相似信号表现；
- 数据来源；
- 提醒规则；
- 机会卡生命周期；
- 复盘结果。

### 6.2 应该有

- 分析师上修/下修；
- EPS/Revenue surprise；
- 财报后漂移；
- 技术突破/回踩；
- 期权异动辅助；
- 内部人/回购/13F；
- 资金面/相对强度；
- 行业强弱；
- 市场 regime；
- 自然语言创建筛选条件；
- 导出 TradingView/Pine；
- Telegram/邮件/桌面提醒；
- 交易日志；
- 策略表现面板。

### 6.3 不建议第一版做

- 自动下单；
- copy trading；
- 纯 LLM 黑箱涨跌预测；
- 无样本数的胜率展示；
- 高频盘中交易；
- 期权流单独荐股；
- 个性化理财建议；
- 保证收益式推送。

---

## 7. 最终系统架构建议

```text
Data Providers
  ├─ price/volume
  ├─ fundamentals
  ├─ earnings/estimates
  ├─ news/events
  ├─ options
  └─ filings/insider

Data Normalization
  ├─ ticker master
  ├─ point-in-time timestamps
  ├─ data quality checks
  └─ cache

Signal Engine
  ├─ earnings drift
  ├─ analyst revision
  ├─ trend/momentum
  ├─ pullback/breakout
  ├─ event catalyst
  ├─ options confirmation
  └─ risk filters

Decision Engine
  ├─ market regime
  ├─ opportunity ranking
  ├─ entry/exit rules
  ├─ risk reward
  └─ risk audit veto

AI Research Layer
  ├─ Serenity Alpha
  ├─ TAM-Adj-PEG
  ├─ GF-DMA Health Index
  ├─ Bayesian valuation
  └─ explanation generator

User Product Layer
  ├─ daily brief
  ├─ opportunity cards
  ├─ watchlist monitor
  ├─ portfolio monitor
  ├─ alerts
  ├─ journal
  └─ postmortem
```

关键原则：

```text
规则引擎算价格和风险
AI 解释逻辑和生成研究框架
回测引擎验证历史表现
提醒系统跟踪触发
日志系统记录真实结果
```

---

## 8. 代码实现前的最终判断

我现在的判断是：

```text
调研已经足够清楚，可以开始写验证型产品实现。
```

但要注意，不是直接写“荐股机器人”，而是写：

```text
机会发现 + 买卖点计划 + 持仓监控 + 复盘闭环
```

第一版代码即使一次性按完整方向设计，也应该把风险级别控制在：

```text
研究/提醒/复盘系统
```

而不是：

```text
自动交易/个性化投资顾问
```

最合理的初始实现目标：

1. 建立统一数据模型；
2. 接入至少一个价格/基本面数据源；
3. 实现股票池扫描；
4. 实现 3-5 个高质量信号；
5. 生成机会卡；
6. 生成入场/出场/止损计划；
7. 记录 1/5/10/20/60 日结果；
8. 支持自选股和持仓监控；
9. 支持提醒规则；
10. 形成复盘面板。

---

## 9. 最终产品不该长什么样

不要做成：

```text
今天 NVDA 有新闻，可能上涨。
今天 TSLA 期权异动，可能上涨。
今天 AMD 被上调评级，利好。
```

这就是用户说“消息性的 Agent 没用”的原因。

应该做成：

```text
NVDA
状态：setup_ready
逻辑：AI 数据中心资本开支继续上修，供应链订单和分析师 EPS 预期同步改善
买点：放量突破 128.5 后可触发；若未突破，观察 20DMA 附近回踩
不追：高于 134 且距离 20DMA > 8% 不追
止损：跌破 119 或跌破 50DMA 且放量
目标：第一目标 142，第二目标 156
风险收益比：2.3:1
历史类似信号：N=xx，20D 中位数 xx%，最大回撤 xx%
监控：下次财报、分析师上修是否延续、半导体板块强弱、成交量跟随
```

这才是用户会觉得“能用”的量化 Agent。

---

## 10. Source List

产品与竞品：

- Danelfin: https://danelfin.com/
- Danelfin How it Works: https://danelfin.com/how-it-works
- Danelfin Trade Ideas: https://danelfin.com/trade-ideas
- Tickeron: https://tickeron.com/
- LevelFields: https://www.levelfields.ai/next-gen-stock-screener
- Kavout AI Stock Picker: https://www.kavout.com/ai-stock-picker
- Trade Ideas AI Signals: https://www.trade-ideas.com/features/ai-signals/
- Amsflow Lisa: https://amsflow.com/investment-research-assistant
- Amsflow Features: https://amsflow.com/features
- TradingView Pine Screener: https://www.tradingview.com/support/solutions/43000742436-tradingview-pine-screener-key-features-and-requirements/
- TradingView Watchlist Alerts: https://www.tradingview.com/support/solutions/43000739708-watchlist-alerts-your-trading-edge/
- Stock Rover: https://www.stockrover.com/
- Seeking Alpha Quant Ratings: https://about.seekingalpha.com/quant-sell-ratings

GitHub：

- RyanJHamby/stock-screener: https://github.com/RyanJHamby/stock-screener
- HKUDS/Vibe-Trading: https://github.com/HKUDS/Vibe-Trading
- tradermonty/claude-trading-skills: https://github.com/tradermonty/claude-trading-skills
- Lumiwealth/lumibot: https://github.com/Lumiwealth/lumibot
- EthanAlgoX/LLM-TradeBot: https://github.com/EthanAlgoX/LLM-TradeBot
- PKScreener: https://github.com/pkjmesra/PKScreener
- chand1012/stonks: https://github.com/chand1012/stonks
- GitHub backtesting topic: https://github.com/topics/backtesting

数据 API：

- Databento Stocks: https://databento.com/stocks
- Databento Options: https://databento.com/options
- Databento OPRA: https://databento.com/datasets/OPRA.PILLAR
- OPRA: https://www.opraplan.com/
- Massive/Polygon Stocks: https://massive.com/docs/rest/stocks/overview
- Finnhub API: https://finnhub.io/docs/api
- Alpha Vantage: https://www.alphavantage.co/
- Benzinga APIs: https://www.benzinga.com/apis/

合规与风险：

- SEC/NASAA/FINRA AI Investment Fraud Alert: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/artificial-intelligence-fraud
- FINRA Regulatory Notice 24-09: https://www.finra.org/rules-guidance/notices/24-09
- FINRA AI Topic: https://www.finra.org/rules-guidance/key-topics/artificial-intelligence
- FINRA Rule 2210 FAQ: https://www.finra.org/rules-guidance/guidance/faqs/advertising-regulation
- SEC AI Washing Enforcement: https://www.sec.gov/newsroom/press-releases/2024-36
- FINRA Regulation Best Interest: https://www.finra.org/rules-guidance/key-topics/regulation-best-interest
