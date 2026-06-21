# 量化机会 Agent 完整产品能力审计：补齐股票推荐、买卖点、持仓监控和竞品功能

调研日期：2026-06-21  
目的：修正前面调研偏“策略框架”的问题，补齐一个用户真正会用的量化股票 Agent 需要具备的完整能力  
定位：产品能力与实现范围调研，不构成投资建议，不承诺收益  

---

## 0. 先承认问题

前面的调研不够完整。

它把“策略地图”讲清楚了，但没有把“用户实际怎么用”讲完整。用户要的不是只有：

```text
为什么这只股票可能涨
```

用户还会要：

```text
现在能不能买？
如果不能买，什么位置可以等？
突破买还是回调买？
止损放哪里？
什么时候卖？
买了以后每天看什么？
如果错了怎么处理？
我的持仓今天有没有风险？
类似信号以前效果怎么样？
```

所以产品定义要升级。

不是：

```text
策略驱动的股票机会雷达
```

而应该是：

```text
股票机会雷达 + 买卖点决策卡 + 持仓监控 + 策略复盘系统
```

---

## 1. 完整产品一句话

这个 Agent 应该每天扫描市场，推荐值得关注的股票，并为每只股票生成可验证的交易决策卡：

```text
哪只股票
为什么值得看
什么时候可以买
什么位置不能追
止损/失效在哪里
什么情况下卖出
买了以后看什么
历史类似机会表现如何
```

它不是单纯研究报告，也不是自动下单机器人。

更准确的定义：

> 一个把市场扫描、事件研究、技术买点、卖出纪律、持仓监控和结果复盘合在一起的股票机会 Agent。

---

## 2. 用户视角的完整工作流

### 2.1 每天早上

用户打开后应该先看到：

1. 今日市场状态；
2. 隔夜新闻和财报影响；
3. 盘前异动；
4. 今天重要事件日历；
5. 今日 Top 机会；
6. 自选股/持仓风险；
7. 昨天机会卡的结果更新。

### 2.2 用户看到一只股票

他不会只问“为什么涨”，而会继续问：

```text
我现在能不能买？
如果回调，哪里买？
如果突破，什么价位确认？
如果买了，跌破哪里说明错？
涨到哪里先减仓？
有没有财报/消息/期权/分析师支持？
这类信号以前胜率多少？
```

因此每张机会卡必须包含：

- 股票基础信息；
- 推荐理由；
- 买点计划；
- 卖点计划；
- 风控计划；
- 持仓后监控；
- 历史同类表现；
- 数据来源；
- 失效条件。

### 2.3 用户已经持仓

产品还必须回答：

```text
我持有的股票今天发生了什么？
是否触发止损？
是否到达第一目标？
是否需要移动止损？
是否有坏消息改变基本面？
是否财报前应该降低风险？
是否期权 IV 过高？
```

这意味着我们不能只做“找机会”，还要做“持仓监控”。

---

## 3. 竞品功能重新梳理

### 3.1 Trade Ideas

公开页面显示，Trade Ideas 的 Holly AI 提供实时股票建议，包括 entry 和 exit signals。Trade Ideas 还强调 real-time scanning、customizable scans、backtesting、Market Explorer 和自动交易能力。

说明：

用户对这类产品的预期已经不是“新闻摘要”，而是：

- 自动找票；
- 给入场；
- 给出场；
- 有回测；
- 有实时扫描。

我们不一定直接做自动交易，但 entry/exit 决策信息必须有。

### 3.2 Tickeron

Tickeron 公开页面强调：

- AI Trading Agents；
- AI Stock Screener；
- AI Trend Prediction Engine；
- AI Real Time Patterns；
- Daily Buy/Sell Signals；
- entry/exit prices；
- confidence levels；
- AI robots；
- backtested algorithms；
- forward testing。

说明：

用户已经被市场教育为“AI 工具应该给买卖信号、信心等级和机器人式跟踪”。如果我们只给投资逻辑，会显得不够可用。

### 3.3 VectorVest

VectorVest 公开页面明确说它提供：

- Buy / Sell / Hold recommendations；
- market timing signal；
- recommended stop prices；
- when to buy；
- what to buy；
- when to sell。

说明：

这类老牌工具抓住的是用户最直接的需求：买什么、什么时候买、什么时候卖。我们可以不使用相同话术，但能力上必须覆盖：

- 买入条件；
- 卖出条件；
- 停损价格；
- 市场环境过滤。

### 3.4 TrendSpider

TrendSpider 的公开页面强调：

- real-time data；
- automation；
- AI agents；
- deep research；
- machine learning；
- predictive signals；
- pattern recognition；
- technical + fundamental analysis；
- strategy tester；
- alerts。

说明：

TrendSpider 的强项是把图表、扫描、策略测试和提醒连接起来。我们的机会卡不能只是文字，后面应该能对应到图表上的：

- pivot；
- entry zone；
- stop；
- target；
- moving average；
- breakout line；
- invalidation level。

### 3.5 TradingView

TradingView 官方功能包括：

- screeners；
- smart alerts；
- charting；
- strategy alerts；
- community scripts；
- webhook alerts。

说明：

用户已经习惯“条件触发提醒”。我们的 Agent 应该允许：

```text
当 XYZ 突破 52.8 且成交量超过 20 日均量 1.5x 时提醒我
```

而不是只在每天固定时间生成文本。

### 3.6 Benzinga Pro

Benzinga Pro 和相关介绍强调：

- real-time news；
- movers；
- scanner；
- signals；
- alerts；
- options；
- earnings calendar；
- squawk。

说明：

Benzinga 的价值是速度和异动发现。我们不能忽视：

- 盘前异动；
- 突发新闻；
- 分析师评级变化；
- earnings calendar；
- unusual volume；
- options flow。

### 3.7 Finviz Elite

Finviz Elite 公开页面提到：

- real-time data；
- advanced screening filters；
- custom alerts；
- news；
- ratings；
- insider trading；
- SEC filings；
- price movement；
- screener notifications；
- portfolio notifications；
- export/API。

说明：

Finviz 的能力清单提醒我们：基础但关键的东西不能漏。

我们需要：

- 股票筛选器；
- 条件提醒；
- 内部人；
- SEC filings；
- 评级变化；
- 组合/自选提醒；
- API/export 友好结构。

### 3.8 Stock Rover

Stock Rover 强调 screening、research、portfolio management、fair value、margin of safety 和深度财务数据。

说明：

我们不能只做短线信号，也要有：

- 基本面质量；
- fair value；
- margin of safety；
- portfolio risk；
- 长期投资视角。

### 3.9 Seeking Alpha Quant

Seeking Alpha Quant Ratings 使用：

- value；
- growth；
- profitability；
- EPS revisions；
- price momentum。

说明：

这其实是一个很实用的“量化评分层”。我们之前提到多因子，但产品上应该明确做：

```text
Value / Growth / Profitability / Revisions / Momentum 五维评分
```

并把它放在每张股票卡片里。

### 3.10 Simply Wall St

Simply Wall St 强调：

- portfolio tracker；
- stock insights；
- fair value；
- future growth；
- past performance；
- financial health；
- dividends；
- risks；
- smart updates。

说明：

它提醒我们：用户还需要“看得懂的基本面摘要”和“持仓更新”。不是所有用户都只做短线。

### 3.11 Barchart / Market Chameleon / Unusual Options 类工具

这类工具强调：

- unusual options volume；
- options screener；
- IV rank；
- earnings options；
- volume vs open interest；
- bullish/bearish sentiment。

说明：

期权不是一定要第一时间做成交易建议，但必须成为“资金面/事件波动”模块：

- options flow confirmation；
- expected move；
- IV crush risk；
- unusual call/put volume；
- OI change；
- earnings IV。

### 3.12 TC2000 / MarketSmith / IBD 类工具

这类工具核心在：

- watchlist；
- scanning；
- chart pattern；
- relative strength；
- buy point；
- base pattern；
- market phase；
- personal journal。

说明：

买点不是一句“可以买”。它通常来自：

- base breakout；
- cup with handle；
- flat base；
- moving average support；
- RS new high；
- volume confirmation；
- market trend confirmation。

### 3.13 OpenBB / Koyfin / Quiver

这类工具分别覆盖：

- 数据集成；
- 终端式研究；
- 另类数据；
- 13F；
- 国会交易；
- 内部人；
- 新闻；
- 基本面；
- API。

说明：

我们的 Agent 后面需要的是“数据入口 + 解释层 + 决策卡”，不是自己从零造所有数据。

---

## 4. 这次补齐后的完整能力矩阵

### 4.1 市场扫描层

必须能扫：

| 扫描类型 | 示例 |
|---|---|
| 盘前异动 | pre-market gap up/down、盘前成交量 |
| 日内异动 | movers、relative volume、new high/low |
| 趋势强度 | RS、52 周新高、均线多头 |
| 财报事件 | earnings today、beat/miss、guidance |
| 分析师事件 | upgrade、downgrade、target raise、EPS revision |
| 新闻事件 | 订单、FDA、M&A、回购、裁员、产品发布 |
| SEC filing | 8-K、10-Q、Form 4、13F、S-1 |
| 期权异动 | unusual volume、call/put、IV rank、expected move |
| 空头风险 | short interest、days to cover、borrow stress |
| 产业链主题 | AI、半导体、液冷、机器人、GLP-1、电网 |
| 板块轮动 | sector ETF RS、breadth、资金流 |

### 4.2 股票推荐层

每个推荐股票必须有：

```yaml
symbol:
company:
sector:
market_cap:
avg_dollar_volume:
strategy_family:
opportunity_score:
confidence:
time_horizon:
why_now:
key_evidence:
main_risk:
```

推荐不能只有一个总分。必须能展开看到：

- 策略来源；
- 证据来源；
- 反证；
- 价格位置；
- 买点；
- 卖点；
- 历史表现。

### 4.3 买点计划层

这是之前遗漏最大的地方。

买点必须分类型：

| 买点类型 | 触发条件 | 适合场景 |
|---|---|---|
| 突破买点 | 突破 pivot / 前高，成交量确认 | 成长股、平台整理、VCP |
| 回调买点 | 回踩 20/50DMA 后转强 | 强趋势股 |
| 财报后确认买点 | 财报后 1-3 日守住关键位并继续放量 | PEAD |
| 事件后低吸买点 | 利好真实但股价回调到支撑 | 事件催化 |
| 均值回归买点 | 超跌后反转确认 | 大盘股/ETF，短周期 |
| 重新站回买点 | 跌破后重新收复关键均线 | 假跌破修复 |
| 期权确认买点 | 股票突破 + options flow 支持 | 辅助确认 |

每张机会卡应该输出：

```yaml
entry_plan:
  preferred_type: pullback / breakout / confirmation
  aggressive_entry:
    condition:
    price_zone:
    risk:
  conservative_entry:
    condition:
    price_zone:
    risk:
  do_not_chase_if:
    - distance_to_50dma > threshold
    - intraday_gap_too_large
    - volume_fades
```

### 4.4 卖点计划层

卖点不能只写“止损”。至少要有五类：

| 卖点类型 | 含义 |
|---|---|
| 失效卖点 | 原始假设被证伪 |
| 技术止损 | 跌破关键均线、平台、财报日低点 |
| 移动止盈 | 涨后抬高止损，保护利润 |
| 目标减仓 | 到达 1R/2R、前高、测量目标 |
| 时间止损 | 到了预期周期仍无反应 |
| 基本面卖点 | EPS revision 转负、指引下调、订单证伪 |
| 事件前降风险 | 财报/FDA 前降低仓位 |

机会卡应该输出：

```yaml
exit_plan:
  invalidation:
    condition:
    price_level:
  stop_loss:
    method: swing_low / atr / moving_average / event_low
    price:
  take_profit:
    first_target:
    second_target:
  trailing:
    method:
  time_stop:
    days:
```

### 4.5 风险收益层

用户需要知道“值不值得冒这个风险”。

必须计算：

```yaml
risk_reward:
  entry:
  stop:
  target_1:
  target_2:
  downside_pct:
  upside_pct:
  reward_to_risk:
  position_size_hint:
```

不一定给具体仓位，但至少要告诉：

- 风险太大，不建议追；
- R/R 小于 2:1，只观察；
- 如果要做，等回调更合理；
- 当前价离止损太远。

### 4.6 持仓监控层

用户买了以后，Agent 必须每天监控：

| 监控项 | 触发 |
|---|---|
| 价格失效 | 跌破止损/关键位 |
| 趋势变坏 | 20/50DMA 破坏、放量下跌 |
| 目标到达 | 1R/2R、前高、测量目标 |
| 基本面变坏 | 指引下调、分析师下修、财报 miss |
| 催化证伪 | 订单取消、FDA 失败、政策变化 |
| 过热 | 远离均线、RSI/ATR 异常、FOMO |
| 财报风险 | 临近 earnings，IV 高 |
| 期权风险 | IV crush、put flow 异常 |
| 板块风险 | 行业 ETF 破位 |
| 市场风险 | 大盘 regime 转 risk-off |

持仓监控输出：

```text
继续持有 / 降低风险 / 触发失效 / 到达第一目标 / 等待确认
```

### 4.7 提醒系统

提醒不只是价格提醒。

应该支持：

- 价格提醒；
- 突破提醒；
- 回踩提醒；
- 成交量提醒；
- 财报提醒；
- 新闻提醒；
- 分析师上修提醒；
- SEC filing 提醒；
- 期权异动提醒；
- 止损触发提醒；
- 目标到达提醒；
- 自选股机会提醒；
- 持仓风险提醒；
- 策略信号提醒；
- 组合暴露提醒。

示例：

```text
提醒我：NVDA 回踩 20DMA 后收盘重新站上，并且成交量高于 20 日均量。
```

### 4.8 回测和复盘层

产品必须记录：

```yaml
signal_time:
entry_condition_hit:
entry_price:
stop_price:
target_price:
return_1d:
return_5d:
return_20d:
max_drawdown:
max_favorable_excursion:
failure_reason:
```

每个策略要有：

- 信号数量；
- 胜率；
- 平均收益；
- 中位收益；
- 最大回撤；
- R 倍数分布；
- 不同市场 regime 表现；
- 不同行业表现；
- 失败原因。

### 4.9 交易日志层

之前也漏了。

如果用户真的根据机会卡操作，产品应该记录：

- 用户是否买入；
- 买入价；
- 买入原因；
- 是否按计划买；
- 是否追高；
- 是否触发止损；
- 是否提前卖；
- 是否违反策略；
- 交易后复盘。

这个能力在 TC2000、TradingView 社区和一些 agent 项目里都很重要，因为用户最终要改善自己的行为。

### 4.10 组合风控层

如果用户有持仓，Agent 应该知道：

- 单票仓位；
- 行业集中度；
- 主题集中度；
- market beta；
- factor exposure；
- earnings calendar risk；
- correlated positions；
- drawdown；
- stop-loss exposure；
- cash level。

输出：

```text
你当前 AI 半导体暴露过高，今天又有 3 张机会卡来自同一主题，建议只选最强一只，而不是重复加仓。
```

---

## 5. 买卖点决策卡标准模板

每张股票卡应长这样。

```yaml
symbol: XYZ
card_type: opportunity_with_trade_plan
time_horizon: swing_5_20d

basic:
  company:
  sector:
  market_cap:
  avg_volume:
  next_earnings_date:
  beta:

why_now:
  strategy_family:
  catalyst:
  evidence:
  score_breakdown:

price_context:
  current_price:
  distance_to_20dma:
  distance_to_50dma:
  distance_to_200dma:
  relative_strength:
  volume_context:
  support:
  resistance:

entry_plan:
  best_entry_type:
  aggressive_entry:
  conservative_entry:
  confirmation_needed:
  do_not_chase_if:

exit_plan:
  invalidation_level:
  stop_loss:
  target_1:
  target_2:
  trailing_rule:
  time_stop:

risk_reward:
  risk_pct:
  upside_pct:
  reward_to_risk:
  position_size_note:

monitoring:
  watch_next:
  bullish_follow_through:
  bearish_warning:
  upcoming_events:

history:
  similar_signal_count:
  win_rate_20d:
  median_excess_return_20d:
  median_max_drawdown:

disclaimer:
  decision_support_not_financial_advice
```

---

## 6. 用户可见卡片示例

```text
XYZ：财报超预期 + 分析师上修 + 回调到 20DMA

为什么值得看：
EPS 超预期 18%，收入超预期 7%，管理层上调全年指引。
财报后涨幅只有 4.2%，成交量放大 2.1 倍，随后分析师开始上修下一财年 EPS。

当前价格状态：
股价仍在 20DMA 上方，距离 50DMA 约 8%，没有明显过热。
相对强度高于行业 ETF。

买点：
激进：回踩 20DMA 后重新放量转强。
稳健：突破财报后高点 52.8，且成交量大于 20 日均量 1.5 倍。
不追：如果单日跳空超过 8%，且盘中成交量衰减。

卖点：
失效：跌破财报日低点。
技术止损：收盘跌破 50DMA 且放量。
目标：第一目标前高，第二目标按 2R 计算。
时间止损：10 个交易日内没有继续强于行业 ETF。

买了以后看什么：
1. 后续分析师是否继续上修；
2. 股价是否守住 20DMA；
3. 行业 ETF 是否继续走强；
4. 是否出现放量下跌。

历史类似信号：
过去 3 年类似信号 126 次，20 日上涨概率 58%，中位超额收益 3.1%，中位最大回撤 -5.4%。
```

---

## 7. 需要补进系统的 Agent 角色

之前只提了投研 agent，不够。

完整系统至少要有：

| Agent | 任务 |
|---|---|
| Market Scanner | 扫描异动、趋势、财报、新闻、期权 |
| Strategy Classifier | 判断属于哪类机会 |
| Fundamental Analyst | 财务、估值、预期、质量 |
| Catalyst Analyst | 新闻、订单、政策、产业链传导 |
| Technical Setup Analyst | 买点、支撑、阻力、趋势、过热 |
| Risk Manager | 止损、失效、仓位、组合暴露 |
| Exit Planner | 卖点、目标、移动止盈、时间止损 |
| Portfolio Monitor | 监控用户持仓和自选股 |
| Backtest Analyst | 历史同类信号表现 |
| Alert Agent | 条件提醒和推送 |
| Journal Coach | 交易复盘和行为偏差 |

---

## 8. 完整功能清单

### 8.1 必须有

- 股票扫描；
- 机会排序；
- 机会卡；
- 买点计划；
- 卖点计划；
- 风险收益比；
- 止损/失效位；
- 持仓监控；
- 自选股监控；
- 条件提醒；
- 历史同类表现；
- 策略复盘；
- 数据来源展示。

### 8.2 很应该有

- 图表标注 entry/stop/target；
- 策略健康面板；
- 交易日志；
- 用户风险偏好；
- 组合集中度；
- 财报日历；
- 期权 IV / expected move；
- 分析师 revision；
- SEC filing diff；
- 内部人和 13F；
- sector regime。

### 8.3 可以有但要谨慎

- 自动交易；
- copy trading；
- 期权结构推荐；
- 盘中秒级高频信号；
- 社媒情绪；
- 黑箱 AI 涨跌概率；
- 杠杆建议；
- meme / squeeze 专门推送。

---

## 9. 和竞品相比，我们应该做出差异

竞品很多，但常见问题是：

| 竞品类型 | 优点 | 缺口 |
|---|---|---|
| 新闻工具 | 快 | 不告诉买卖计划 |
| 图表工具 | 强大 | 用户要自己解释 |
| 信号工具 | 给买卖 | 黑箱、解释弱 |
| 数据终端 | 信息全 | 不主动形成决策卡 |
| 期权流工具 | 资金面强 | 容易误读 |
| 量化平台 | 可回测 | 普通用户难用 |
| AI 聊天投研 | 会解释 | 缺少确定性信号和复盘 |

我们的差异应该是：

```text
不是信息更多，而是把信息变成可执行、可监控、可复盘的交易决策卡。
```

---

## 10. 修正后的开工范围

之前我说先写 validation core，这仍然对，但要扩展对象。

不能只写：

```text
opportunity_card
```

必须同时写：

```text
opportunity_card
entry_plan
exit_plan
risk_plan
monitoring_plan
alert_rule
outcome_tracking
trade_journal
portfolio_watch
```

### 10.1 第一批验证工程

应该做：

1. 股票 universe；
2. price adapter；
3. strategy registry；
4. opportunity card schema；
5. entry plan schema；
6. exit plan schema；
7. risk/reward calculator；
8. outcome tracker；
9. trend/pullback/breakout strategy；
10. alert rule engine；
11. strategy health report。

### 10.2 第二批验证工程

应该做：

1. earnings adapter；
2. PEAD strategy；
3. analyst revision adapter；
4. news/catalyst classifier；
5. financial transmission mapper；
6. watchlist monitor；
7. portfolio monitor。

### 10.3 第三批验证工程

应该做：

1. options flow adapter；
2. short interest adapter；
3. insider Form 4 adapter；
4. 13F adapter；
5. SEC filing diff；
6. trading journal；
7. chart annotation。

---

## 11. 现在真正完整的产品定义

最终我们要做的东西是：

```text
一个面向股票投资者的 AI 量化机会 Agent。

它每天扫描市场，找出值得关注的股票；
用策略解释为什么值得看；
给出买点、卖点、止损、目标、风控；
监控用户自选股和持仓；
在条件触发时提醒；
事后追踪每张机会卡表现；
不断用历史结果校准策略。
```

它的核心对象不是新闻，也不是聊天回答，而是：

```text
带交易计划的机会卡
```

---

## 12. 修正后的结论

你说“调研不够完整”是对的。

完整调研必须覆盖三层：

1. 策略层：为什么这只股票可能涨；
2. 交易层：什么时候买、什么时候卖、怎么止损；
3. 运营层：怎么扫描、提醒、监控、复盘、改进。

前面的文档主要完成了第 1 层，这份文档补齐第 2 层和第 3 层。

现在再判断是否能开始写代码：

```text
可以开始写验证工程。
但工程范围要从“机会卡”升级为“带买卖点和持仓监控的机会卡系统”。
```

---

## 13. 参考来源

- Trade Ideas Holly AI: https://www.trade-ideas.com/ti-ai-virtual-trade-assistant/
- Trade Ideas AI signals: https://www.trade-ideas.com/features/ai-signals/
- Tickeron: https://tickeron.com/
- Tickeron AI trading bots: https://tickeron.com/bot-trading/
- VectorVest: https://www.vectorvest.com/
- VectorVest buy/sell recommendations: https://www.vectorvest.com/blog/stockmarket/vectorvest-buy-and-sell-recommendations/
- TrendSpider: https://trendspider.com/
- TrendSpider Strategy Tester: https://help.trendspider.com/kb/strategy-tester/understanding-strategy-tester-from-trendspider
- TradingView features: https://www.tradingview.com/features/
- TradingView strategy alerts: https://www.tradingview.com/support/solutions/43000481368-strategy-alerts/
- Benzinga Pro: https://www.benzinga.com/pro/
- Finviz Elite: https://finviz.com/elite
- Stock Rover fair value and margin of safety: https://www.stockrover.com/blog/product-features/fair-value-and-margin-of-safety-come-to-stock-rover/
- Seeking Alpha Quant Ratings FAQ: https://help.seekingalpha.com/premium/quant-ratings-and-factor-grades-faq
- Seeking Alpha Quant sell ratings: https://about.seekingalpha.com/quant-sell-ratings
- Simply Wall St: https://simplywall.st/
- Market Chameleon unusual option volume: https://marketchameleon.com/Reports/UnusualOptionVolumeReport
- Barchart unusual options: https://www.barchart.com/options/unusual-activity
- Barchart options screener: https://www.barchart.com/options/options-screener
- TC2000: https://www.tc2000.com/
- TC2000 alerts help: https://help.tc2000.com/m/69401/c/226282
- Yahoo Finance screeners: https://finance.yahoo.com/research-hub/screener/
- Yahoo Finance price alerts: https://help.yahoo.com/kb/SLN35070.html
- Databento real-time stock screener tutorial: https://databento.com/blog/how-to-build-a-blazing-fast-real-time-stock-screener-with-python
- GitHub stock-screener topic: https://github.com/topics/stock-screener?l=python&o=desc&s=forks
- PKScreener: https://github.com/pkjmesra/PKScreener
- TradingAgents: https://tradingagents-ai.github.io/
