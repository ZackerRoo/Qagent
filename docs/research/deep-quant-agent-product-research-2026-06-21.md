# 股票机会 Agent 深度调研：推荐股票、买卖点、持仓监控、提醒和验证闭环

调研日期：2026-06-21  
目标：在写代码前，把“我们到底要做什么”调研到足够清晰  
定位：产品、策略、数据和实现调研，不构成投资建议，不承诺收益  

---

## 0. 修正后的核心判断

前面调研已经覆盖策略地图，但还不够完整。真正要做的产品不是单纯“股票机会雷达”，也不是“新闻总结 agent”，而是：

```text
股票机会发现
+ 买卖点决策卡
+ 持仓监控
+ 条件提醒
+ 交易日志
+ 策略复盘
+ 数据可信度和合规边界
```

用户要的不是一句“某股票可能涨”，而是：

```text
买什么
为什么
什么时候买
哪里不能追
止损在哪里
目标在哪里
什么时候卖
买了以后每天看什么
历史同类信号表现如何
```

所以最终产品定义应改成：

> 面向股票投资者的 AI 量化机会 Agent。它每天扫描市场，找出值得关注的股票，解释上涨逻辑，生成买卖点计划，监控自选股和持仓，在条件触发时提醒，并持续复盘每条机会卡的真实结果。

---

## 1. 用户真正要完成的任务

### 1.1 发现机会

用户每天要知道：

- 今天哪些股票值得看；
- 哪些是财报驱动；
- 哪些是新闻催化；
- 哪些是趋势突破；
- 哪些是回调到买点；
- 哪些是期权/空头/内部人/13F 等资金面变化；
- 哪些是自选股或持仓相关。

### 1.2 判断能不能买

用户看到机会后不会立刻满足，他会问：

- 现在追是不是晚了；
- 如果回调，哪里比较合理；
- 如果突破，什么价格确认；
- 成交量是否够；
- 有没有过热；
- 风险收益比是否够；
- 大盘/板块环境是否支持。

### 1.3 买了以后怎么办

这部分是前面调研最容易漏掉的。

用户需要：

- 止损位；
- 失效位；
- 第一目标；
- 第二目标；
- 移动止盈规则；
- 时间止损；
- 基本面失效条件；
- 财报/FDA/事件前风险提示；
- 持仓是否过度集中。

### 1.4 复盘系统准不准

如果系统无法复盘，它只是一个会说话的资讯工具。

每张机会卡必须追踪：

- 1 日收益；
- 5 日收益；
- 10 日收益；
- 20 日收益；
- 60 日收益；
- 相对基准收益；
- 最大浮盈；
- 最大回撤；
- 是否触发买点；
- 是否触发止损；
- 是否触发失效；
- 失败原因。

---

## 2. 竞品深度结论

### 2.1 Trade Ideas

Trade Ideas 的 Holly AI 明确提供 real-time stock suggestions、entry signals、exit signals、实时扫描、策略验证和回测能力。

对我们的启发：

- 用户对 AI 股票工具的预期已经包含 entry/exit；
- 只给“为什么看好”不够；
- 每条机会必须有触发条件、退出条件和风险提示；
- 实时扫描和历史验证要连在一起。

我们不应照搬：

- 黑箱式 buy/sell；
- 不解释信号来源；
- 过度强调自动交易。

### 2.2 Tickeron

Tickeron 强调 AI Trading Agents、AI Screener、AI Trend Prediction Engine、AI Real Time Patterns、Daily Buy/Sell Signals、entry/exit prices、confidence levels、AI robots、backtested algorithms、forward testing。

对我们的启发：

- “买卖点 + 置信度 + 历史表现”是用户会期待的标准配置；
- 产品需要支持不同用户层级：新手、DIY、跟随信号、组合管理；
- 但置信度必须可解释，不能只显示一个神秘概率。

### 2.3 VectorVest

VectorVest 的核心表达非常直接：when to buy、what to buy、when to sell，并提供 Buy/Sell/Hold、market timing、recommended stop prices。

对我们的启发：

- 市场环境过滤必须在最外层；
- 每只股票要有独立 stop；
- 用户需要明确“现在是买、持有、观察、降低风险还是卖出”。

我们需要改写成更审慎的表达：

```text
不是“系统命令你买卖”，而是“当前计划状态：可观察 / 等触发 / 已触发 / 风险升高 / 失效”。
```

### 2.4 TrendSpider

TrendSpider 公开强调 AI、automation、deep research、predictive signals、pattern recognition、technical + fundamental analysis、strategy tester、alerts。

对我们的启发：

- 用户需要把条件转成提醒；
- 图表买点要可视化；
- 策略要能回测；
- 自然语言创建规则是很强的用户体验。

例如用户应该能说：

```text
如果 NVDA 回踩 20 日线后重新放量站上，提醒我。
```

系统应转成结构化 alert rule。

### 2.5 TradingView

TradingView 的强项是 screeners、smart alerts、strategy alerts、Pine scripts、webhooks、社区指标。

对我们的启发：

- 条件提醒是核心能力，不是附属功能；
- 后续可导出 TradingView Pine Script 或 webhook；
- 策略规则必须结构化，不应只存在于 LLM 文案中。

### 2.6 Benzinga Pro

Benzinga Pro 强在 real-time news、movers、signals、scanner、alerts、options、earnings calendar、squawk。

对我们的启发：

- 盘前和盘中异动必须覆盖；
- “Why is it moving” 是强需求；
- 新闻速度重要，但不能停留在新闻标题；
- 异动后必须进入机会卡：是否有财务传导，是否已定价，买点是否还合理。

### 2.7 Finviz Elite

Finviz Elite 覆盖 real-time data、advanced screening、alerts、news、ratings、insider trading、SEC filings、price movement、portfolio/screener notifications、export/API。

对我们的启发：

- 很多基础能力不能漏：insider、filings、ratings、portfolio alert；
- 条件筛选和提醒要支持导出；
- 用户喜欢简单、密集、可扫视的信息架构。

### 2.8 Stock Rover / Seeking Alpha Quant / Simply Wall St

这一类偏基本面、估值和组合：

- Stock Rover 有 fair value、margin of safety、screening、portfolio analytics；
- Seeking Alpha Quant 有 value、growth、profitability、EPS revisions、price momentum；
- Simply Wall St 有 fair value、future growth、financial health、dividends、risks、portfolio tracker、smart updates。

对我们的启发：

- 不能只做短线；
- 每张机会卡要显示基本面质量和估值状态；
- 股票推荐要区分交易机会和长期投资质量；
- 用户持仓需要 smart updates，而不是只看当天异动。

### 2.9 Barchart / Market Chameleon / Unusual options 工具

这类工具提供 unusual options volume、options screener、IV rank、earnings options、volume vs open interest、expected move。

对我们的启发：

- 期权流可以做资金面确认；
- earnings 前必须提示 IV crush；
- expected move 可以帮助设定目标和风险；
- 但期权流不能单独等于看涨或看跌。

### 2.10 IBD / MarketSmith / TC2000

这类工具强调：

- relative strength；
- base pattern；
- buy point；
- watchlist；
- alerts；
- market trend；
- trading journal。

对我们的启发：

- 成长股买点必须有 base/pivot/RS/volume；
- 好消息不等于好买点；
- 用户要的买点常常是“突破确认”或“回踩支撑确认”。

### 2.11 Vibe-Trading / PKScreener / 开源项目

Vibe-Trading 值得借鉴：

- trade journal；
- shadow account；
- behavior diagnostics；
- rule extraction；
- shadow backtest；
- run card；
- hypothesis registry；
- research autopilot；
- data fallback；
- trust layer。

PKScreener 值得借鉴：

- 40+ scanners；
- VCP、volume breakout、ATR trailing stop、MA support；
- piped scanners；
- scheduled scans；
- Telegram alerts；
- on-demand bot；
- backtesting；
- morning vs close outcome analysis。

对我们的启发：

- 扫描器可以组合；
- 提醒应支持订阅；
- 交易日志和 shadow strategy 能显著提升产品深度；
- 数据质量和 run card 必须可审计。

---

## 3. 完整功能地图

### 3.1 市场扫描层

必须覆盖：

| 扫描域 | 信号 |
|---|---|
| 价格异动 | 盘前 gap、日内 movers、relative volume、52 周新高/低 |
| 趋势 | 20/50/100/200DMA、RS、stage 2、趋势健康 |
| 买点 | pivot breakout、pullback、retest、MA support、VCP |
| 财报 | earnings beat/miss、guidance、PEAD、财报日历 |
| 分析师 | upgrade/downgrade、target raise/cut、EPS/revenue revision |
| 新闻 | why moving、订单、政策、M&A、FDA、产品、回购 |
| 期权 | unusual volume、call/put ratio、IV rank、expected move |
| 空头 | short interest、days to cover、borrow stress |
| 内部人 | open-market buy、cluster buying、Form 4 |
| 机构 | 13F 新进、增持、机构集中度 |
| 产业链 | 主题、供应商、客户、收入暴露、capex |
| 板块 | sector ETF RS、breadth、资金流、regime |

### 3.2 推荐排序层

推荐不能只按一个总分。

需要至少四个分数：

```yaml
opportunity_score: 机会强度
tradeability_score: 当前是否有好买点
risk_score: 风险和失效概率
data_quality_score: 数据是否可靠
```

一个好的排序公式：

```text
final_rank =
  opportunity_score
  * data_quality_score
  * market_regime_multiplier
  * user_relevance_multiplier
  + tradeability_bonus
  - overextension_penalty
  - liquidity_penalty
  - event_risk_penalty
  - crowding_penalty
```

### 3.3 机会卡层

每张机会卡都必须有：

```yaml
symbol:
company:
strategy_family:
why_now:
evidence:
price_context:
entry_plan:
exit_plan:
risk_reward:
monitoring_plan:
alert_rules:
historical_base_rate:
data_quality:
compliance_label:
```

### 3.4 交易计划层

这是核心。

交易计划不等于“立刻买”。它应有状态机：

```text
idea        只是发现机会
watch       进入观察池
setup       买点接近
triggered   买点触发
active      用户已持有或策略已进入跟踪
risk        风险升高
invalidated 机会失效
closed      机会结束
reviewed    已复盘
```

### 3.5 持仓监控层

持仓监控必须区分：

- 用户自选；
- 用户持仓；
- 系统推荐但未买；
- 已触发买点；
- 已失效机会。

持仓的提醒等级应该更高。

例如：

| 情况 | 提醒 |
|---|---|
| 自选股出现新催化 | 中提醒 |
| 持仓跌破失效位 | 强提醒 |
| 持仓到达第一目标 | 中提醒 |
| 持仓财报前 IV 极高 | 强提醒 |
| 非持仓普通新闻 | 弱提醒或日报 |

---

## 4. 买点算法调研

### 4.1 突破买点

适合：

- 成长股；
- VCP；
- 平台整理；
- 财报后继续确认；
- 板块强势。

触发：

```text
price > pivot
volume > avg_volume_20d * 1.5
relative_strength > threshold
market_regime != risk_off
distance_to_50dma not excessive
```

风险：

- 假突破；
- 高开低走；
- 已经离支撑太远；
- 板块同步走弱。

### 4.2 回调买点

适合：

- 强趋势股；
- 财报/分析师/新闻逻辑仍成立；
- 用户不想追高。

触发：

```text
price pulls back near 20DMA or 50DMA
volume contracts on pullback
price reclaims short-term moving average
no negative revision/news
sector remains strong
```

反证：

- 回调放量；
- 跌破 50DMA；
- 反弹无量；
- EPS revision 转负。

### 4.3 财报后确认买点

适合 PEAD。

触发：

```text
earnings beat
guidance not negative
day0 reaction not overextended
price holds earnings-day low
analyst revisions begin improving
```

更稳健触发：

```text
close above post-earnings high
volume confirms
sector confirms
```

### 4.4 事件后低吸买点

适合：

- 真实利好；
- 股价初期没完全反应；
- 回调到支撑；
- 后续还有验证点。

不能用于：

- 只有主题，没有财务传导；
- 已经连续暴涨；
- 新闻重复炒作；
- 小票流动性差。

### 4.5 开盘突破 / ORB 买点

适合盘中事件和盘前异动。

触发：

```text
gap_up with news
opening_range_high breaks
relative_volume high
VWAP support holds
market/sector supportive
```

风险：

- 盘前消息已定价；
- 开盘冲高回落；
- 流动性不足；
- 盘中噪音大。

### 4.6 不追条件

每张卡必须有 do_not_chase。

典型条件：

```text
gap_up > 8-10%
distance_to_50dma > 15-20%
intraday volume fades
price below VWAP after gap
reward_to_risk < 2
news already widely circulated
IV extremely elevated
```

---

## 5. 卖点算法调研

### 5.1 初始止损

方法：

- swing low；
- earnings-day low；
- breakout pivot 下方；
- 50DMA 下方；
- ATR stop；
- support zone 下方。

选择原则：

```text
止损位必须对应原始假设。
如果跌破后原始假设仍成立，止损设置错了。
如果跌破后原始假设明显失效，止损才合理。
```

### 5.2 失效卖点

这是比技术止损更重要的概念。

失效可以来自：

- 财报日低点跌破；
- EPS revision 转负；
- 管理层指引下调；
- 订单被取消；
- FDA 失败；
- 行业 ETF 破位；
- 新闻证伪；
- 期权流转为负；
- 大盘 regime 转 risk-off。

### 5.3 目标卖点

目标不能随便写。

可选方法：

- 前高；
- measured move；
- 1R/2R/3R；
- ATR multiple；
- analyst target 作为背景，不作为唯一目标；
- options expected move；
- 估值区间。

### 5.4 移动止盈

适合趋势股。

方法：

- 10DMA/20DMA trailing；
- ATR trailing；
- higher low trailing；
- close below prior week low；
- 目标到达后抬止损到 breakeven。

### 5.5 时间止损

很多机会不是跌错，而是不动。

例如：

```text
PEAD 机会 10 个交易日内没有相对行业走强
突破后 3-5 日没有 follow-through
事件催化 20 日内没有后续验证
```

时间止损能防止资金被低质量机会占用。

---

## 6. 提醒系统设计

### 6.1 提醒类型

必须支持：

- 机会发现提醒；
- 买点接近提醒；
- 买点触发提醒；
- 止损接近提醒；
- 失效触发提醒；
- 第一目标提醒；
- 新闻/财报/filing 提醒；
- 分析师上修提醒；
- 期权异动提醒；
- 持仓风险提醒；
- 组合集中度提醒。

### 6.2 提醒去重

不能重复轰炸用户。

规则：

```text
同一 ticker + 同一 opportunity_id + 同一等级，不重复提醒
新证据显著增强，升级提醒
风险触发，强提醒
机会失效，归档提醒
每日摘要合并弱提醒
```

### 6.3 提醒等级

| 等级 | 含义 |
|---|---|
| L1 | 进入观察池 |
| L2 | 买点接近 |
| L3 | 买点触发 |
| L4 | 持仓风险 |
| L5 | 失效/止损/重大风险 |

---

## 7. 数据和实现架构

### 7.1 数据源分层

| 层级 | 数据源 | 用途 |
|---|---|---|
| 免费/研究 | SEC EDGAR、FINRA、Alpha Vantage limited、OpenBB、yfinance、Stooq | 原型和回测 |
| 生产价格 | Databento、Polygon/Massive、Tiingo、FMP、Finnhub | 稳定 OHLCV、实时/延迟行情 |
| 新闻/分析师 | Benzinga、Finnhub、FMP、Koyfin、FactSet/IBES | news、ratings、estimates、revisions |
| 期权 | Polygon/Massive options、Tradier、OPRA vendors、Unusual Whales、Barchart | options flow、IV、expected move |
| 另类数据 | Quiver、RavenPack、Similarweb、13F vendors | sentiment、Congress、13F、attention |

### 7.2 本地实测结论

前序验证发现：

- FINRA `regShoDaily` 和 `equityShortInterest` 能返回 CSV 样例数据；
- SEC 官方 API 文档确认可用，但当前本地命令行访问有 SSL EOF，需要实现时处理网络/证书/访问策略；
- Yahoo chart 返回 429，不适合当生产数据源；
- Stooq 返回 browser verification，不适合当前环境直接依赖；
- Finnhub/FMP demo key 无效，需要正式 API；
- 本机未预装 yfinance。

这说明：

```text
不能把免费源当生产基础。
必须设计数据 adapter + cache + provider fallback。
```

### 7.3 数据模型

核心表：

```text
symbols
price_bars
corporate_events
earnings_events
analyst_revisions
news_events
filing_events
option_activity
short_interest
insider_trades
institutional_holdings
opportunities
trade_plans
alerts
positions
watchlist
outcomes
journal_entries
strategy_health
```

### 7.4 Point-in-time 要求

任何回测必须避免未来函数。

要求：

- 财报用 announcement timestamp；
- 估值/财务用 filing date，不是 period end date；
- 13F 用 filing date；
- short interest 用 publication/settlement date；
- analyst revision 用真实发布时间；
- 新闻用 first seen timestamp；
- 价格用可交易时间后的价格。

---

## 8. Agent 角色设计

完整系统至少需要这些角色或模块：

| 模块 | 任务 |
|---|---|
| Market Scanner | 扫描异动、趋势、财报、新闻、期权、filings |
| Opportunity Classifier | 判断信号属于哪类机会 |
| Catalyst Analyst | 新闻和事件传导 |
| Fundamental Analyst | 财务、估值、预期、质量 |
| Technical Setup Analyst | 买点、支撑、阻力、过热 |
| Risk Manager | 止损、失效、仓位、组合暴露 |
| Exit Planner | 目标、移动止盈、时间止损 |
| Alert Manager | 条件提醒、去重、升级 |
| Portfolio Monitor | 自选股和持仓监控 |
| Backtest Analyst | 历史同类表现 |
| Journal Coach | 用户行为复盘 |
| Compliance Guard | 语言边界、数据披露、风险提示 |

LLM 适合做：

- 解释；
- 分类；
- 传导推理；
- 文案；
- 风险反证；
- 用户问答。

确定性代码必须做：

- 价格计算；
- 买卖点触发；
- 止损/目标；
- 回测；
- 排序；
- 提醒去重；
- outcome tracking；
- data quality。

---

## 9. 推荐卡最终结构

```yaml
opportunity:
  id:
  symbol:
  company:
  created_at:
  horizon:
  strategy_family:
  status: idea / watch / setup / triggered / active / risk / invalidated / closed

scores:
  opportunity_score:
  tradeability_score:
  risk_score:
  data_quality_score:
  confidence:

basic_info:
  sector:
  industry:
  market_cap:
  avg_dollar_volume:
  beta:
  next_earnings_date:

why_now:
  catalyst:
  financial_transmission:
  technical_confirmation:
  analyst_revision:
  option_flow:
  sector_context:

price_context:
  current_price:
  support:
  resistance:
  pivot:
  distance_to_20dma:
  distance_to_50dma:
  relative_strength:
  relative_volume:

entry_plan:
  preferred_entry_type:
  aggressive_entry:
  conservative_entry:
  confirmation_required:
  do_not_chase_if:

exit_plan:
  invalidation:
  stop_loss:
  target_1:
  target_2:
  trailing_rule:
  time_stop:

risk_reward:
  downside_pct:
  upside_pct:
  reward_to_risk:
  position_size_note:

monitoring_plan:
  bullish_follow_through:
  bearish_warning:
  upcoming_events:
  alert_rules:

history:
  similar_signal_count:
  win_rate_20d:
  median_return_20d:
  median_max_drawdown:

audit:
  data_sources:
  calculation_timestamp:
  known_data_gaps:
  compliance_label:
```

---

## 10. 合规和信任边界

这个产品天然接近投资建议，必须一开始设计边界。

参考 SEC/FINRA 对 AI 投资诈骗、AI washing、公众沟通和投资建议的监管提醒，产品需要避免：

- 保证收益；
- “稳赚”；
- “AI 必涨预测”；
- 未披露的 hypothetical performance；
- 黑箱推荐；
- 伪装成持牌投顾；
- 对用户风险承受能力一无所知却给仓位建议；
- 不披露数据延迟和来源。

建议产品语言：

```text
观察机会
买点触发
风险升高
机会失效
决策支持
不是个性化投资建议
```

比下面这些更安全：

```text
立即买入
必涨
稳赚
最佳股票
无风险
```

---

## 11. 可开工范围

### 11.1 现在可以直接做

这些不依赖昂贵数据：

1. 数据 adapter 抽象；
2. symbol universe；
3. price bars schema；
4. strategy registry；
5. opportunity schema；
6. entry_plan / exit_plan / risk_reward schema；
7. trend/pullback/breakout 策略；
8. alert rule engine；
9. outcome tracker；
10. strategy health report；
11. watchlist / portfolio 数据模型；
12. journal 数据模型。

### 11.2 需要 API 后做

1. PEAD actual vs estimate；
2. analyst revisions；
3. Benzinga/Finnhub/FMP news；
4. transcripts；
5. options chain/flow；
6. real-time intraday scanner；
7. short interest bulk；
8. insider/13F normalized import。

### 11.3 不应先做

1. 自动交易；
2. copy trading；
3. 复杂期权结构推荐；
4. 黑箱涨跌概率；
5. 社媒热度单独信号；
6. 高频盘中 scalping；
7. “AI 保证收益”式营销。

---

## 12. 实现优先级建议

第一批应该建立“骨架”，不是追求信号多：

```text
data adapter
-> price bars
-> strategy registry
-> opportunity card
-> trade plan
-> alert rule
-> outcome tracker
-> strategy health
```

第一批策略：

- trend leadership；
- pullback to 20/50DMA；
- breakout/pivot；
- overextension/do-not-chase；
- ATR/swing-low stop；
- 1R/2R target；
- time stop。

原因：

- 数据最容易；
- 买卖点最直观；
- 能把产品闭环跑通；
- 不依赖商业 analyst/news/options 数据。

第二批再接：

- PEAD；
- analyst revision；
- news catalyst；
- financial transmission；
- earnings calendar。

第三批接：

- options flow；
- short interest；
- insider；
- 13F；
- buyback；
- FDA；
- IPO lock-up；
- M&A。

---

## 13. 这轮调研后的最终定义

我们要做的不是：

```text
新闻 agent
策略聊天机器人
简单 stock screener
黑箱买卖信号
```

而是：

```text
AI 股票机会决策系统。

它扫描市场，推荐股票，解释机会，
给出买点和卖点，监控持仓，
触发提醒，并复盘每条建议的真实表现。
```

核心对象不是新闻，也不是聊天，而是：

```text
带交易计划的机会卡
```

这张卡必须同时回答：

1. 为什么值得看；
2. 什么时候买；
3. 哪里不能追；
4. 跌破哪里错了；
5. 涨到哪里处理；
6. 买了以后看什么；
7. 历史同类信号表现如何。

---

## 14. 主要参考来源

### 竞品和产品

- Trade Ideas Holly AI: https://www.trade-ideas.com/ti-ai-virtual-trade-assistant/
- Trade Ideas AI Signals: https://www.trade-ideas.com/features/ai-signals/
- Tickeron: https://tickeron.com/
- Tickeron AI Trading Bots: https://tickeron.com/bot-trading/
- VectorVest: https://www.vectorvest.com/
- VectorVest Buy/Sell Recommendations: https://www.vectorvest.com/blog/stockmarket/vectorvest-buy-and-sell-recommendations/
- TrendSpider: https://trendspider.com/
- TrendSpider Strategy Tester: https://help.trendspider.com/kb/strategy-tester/understanding-strategy-tester-from-trendspider
- TradingView Features: https://www.tradingview.com/features/
- TradingView Strategy Alerts: https://www.tradingview.com/support/solutions/43000481368-strategy-alerts/
- Benzinga Pro: https://www.benzinga.com/pro/
- Finviz Elite: https://finviz.com/elite
- Stock Rover Fair Value and Margin of Safety: https://www.stockrover.com/blog/product-features/fair-value-and-margin-of-safety-come-to-stock-rover/
- Seeking Alpha Quant Ratings FAQ: https://help.seekingalpha.com/premium/quant-ratings-and-factor-grades-faq
- Simply Wall St: https://simplywall.st/
- Barchart Unusual Options: https://www.barchart.com/options/unusual-activity
- Barchart Options Screener: https://www.barchart.com/options/options-screener
- TC2000: https://www.tc2000.com/
- Yahoo Finance Screeners: https://finance.yahoo.com/research-hub/screener/

### 数据源

- Databento Equities: https://databento.com/equities
- Databento real-time stock screener example: https://databento.com/docs/examples/algo-trading/live-stock-screener
- Massive/Polygon Stocks Docs: https://massive.com/docs/rest/stocks/overview
- Alpha Vantage API Documentation: https://www.alphavantage.co/documentation/
- Finnhub API Docs: https://finnhub.io/docs/api
- Financial Modeling Prep Docs: https://site.financialmodelingprep.com/developer/docs
- Benzinga APIs: https://www.benzinga.com/apis/
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- FINRA Short Sale Volume Data: https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data

### 开源实现

- Vibe-Trading: https://github.com/HKUDS/Vibe-Trading
- PKScreener: https://github.com/pkjmesra/PKScreener
- AI-Trader: https://github.com/HKUDS/AI-Trader
- Options Flow Predictor: https://github.com/NavnoorBawa/Options-Flow-Predictor
- TradingAgents: https://tradingagents-ai.github.io/

### 合规和风险

- SEC/NASAA/FINRA Investor Alert on AI Investment Fraud: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/artificial-intelligence-fraud
- FINRA Regulatory Notice 24-09 on GenAI: https://www.finra.org/rules-guidance/notices/24-09
- SEC AI washing enforcement release: https://www.sec.gov/newsroom/press-releases/2024-36
- FINRA Rule 2210 Communications with the Public: https://www.finra.org/rules-guidance/rulebooks/finra-rules/2210
