# 可产品化量化策略库完整调研：从“最近哪些股票可能涨”到可验证机会卡

调研日期：2026-06-21  
目标用户：希望系统主动推送“哪些股票可能上涨、为什么、如果买了后续看什么”的普通投资者或半专业投资者  
研究定位：产品与策略框架调研，不构成投资建议，不承诺收益  

---

## 0. 结论先行

我们需要一次性把策略调研做完整，而且应该把“策略调研”放在产品设计的核心位置。

原因很简单：用户并不想要一个新闻 agent，也不想要一个只会说“利好某某股票”的摘要工具。用户真正想要的是：

1. 今天有哪些值得看的机会；
2. 为什么这些机会可能涨；
3. 这个利好怎样传导到收入、利润、订单、估值或资金面；
4. 股价是不是已经提前反应；
5. 如果买了，未来几天或几周应该看什么；
6. 什么情况说明这条机会错了；
7. 系统过去同类机会的胜率、收益分布、回撤和失败原因是什么。

因此，我们不应该先做“消息流 agent”，也不应该把策略研究切成很多未来阶段。正确做法是：研究范围一次性覆盖完整，工程落地时再按数据可得性、可解释性、可回测性和用户风险做实现排序。

一句话定义：

> 好的量化 agent 不是预测器，也不是资讯流，而是把多个可验证策略族转化成少量、高解释度、可复盘的机会卡。

---

## 1. 我们到底要调研什么策略

这里的“策略”不是指让用户直接自动下单的交易系统，而是指一套结构化判断：

```text
触发事件
-> 候选股票
-> 策略归因
-> 支撑证据
-> 反证条件
-> 入场/观察区间
-> 风险监控
-> 结果复盘
```

对这个产品而言，策略要满足五个条件：

1. 可解释：用户能听懂“为什么可能涨”；
2. 可数据化：触发、评分、反证都能落到数据字段；
3. 可回测：至少能做历史事件复盘；
4. 可推送：每天能产生少量高质量机会，而不是几百条噪音；
5. 可纠错：如果失败，能记录失败类型，并改进策略。

所以我们不是要找“神奇预测模型”，而是要建立一套策略族库。

---

## 2. 完整策略地图

下面不是“分阶段调研表”，而是完整策略宇宙。所有方向都先研究清楚；区别只在于它们在产品中承担的角色不同：有些适合作为主机会卡，有些适合作为确认信号，有些只能做风险提示或情景推演。

| 策略族 | 用户问题 | 数据可得性 | 推送可信度 | 产品角色 | 核心风险 |
|---|---|---:|---:|---|---|
| 财报超预期后漂移 PEAD | 财报后还能不能继续涨 | 高 | 高 | 主机会卡 | 已提前定价、一次性收益 |
| 分析师预期上修 / 盈利动量 | 市场预期是否在变好 | 中高 | 高 | 主机会卡 / 确认 | 追认式上修、覆盖不足 |
| 管理层指引变化 | 公司未来几个季度是否变强 | 中 | 高 | 主机会卡 / 财报增强 | 指引口径不可比 |
| 成长股强势趋势 / Stage 2 | 哪些强势股走势健康 | 高 | 高 | 主机会卡 | 过热、趋势末端 |
| 健康趋势回调 | 强势股回调是不是机会 | 高 | 中高 | 主机会卡 | 回调变破位 |
| 突破 / VCP / 成交量确认 | 哪些股票正在形成新一轮动能 | 高 | 中 | 技术触发器 | 假突破 |
| 事件催化 + 财务传导 | 什么消息真正利好哪家公司 | 中 | 高 | 主机会卡 | 叙事强、财务弱 |
| 新闻情绪 + attention | 新闻是否被市场充分反应 | 中高 | 中 | 辅助证据 / 拥挤度 | 情绪噪声、反转 |
| 产业链主题扩散 | 一个热点会传导到谁 | 中 | 高 | 主机会卡 | 供应链映射错误 |
| 期权异动 / options flow | 有没有资金提前反应 | 中 | 中 | 确认信号 | 对冲/价差/平仓误读 |
| 期权结构 / IV 风险 | 如果用期权表达，风险收益如何 | 中 | 中 | 情景推演 / 风险提示 | IV crush、流动性 |
| 空头回补 / short squeeze | 高空头股票会不会被挤压 | 中 | 中 | 风险事件机会卡 | 拥挤、基本面恶化 |
| 内部人交易 | 管理层是否用真金白银投票 | 高 | 中 | 辅助确认 | 卖出原因复杂 |
| 13F / 机构持仓变化 | 机构是否在加仓 | 中 | 中 | 慢变量确认 | 披露滞后 45 天 |
| 回购 / buyback | 公司是否认为股价低估 | 中高 | 中 | 事件机会卡 | 授权不等于执行 |
| 并购 / merger arbitrage | 公告后价差是否有机会 | 中 | 中 | 专业事件卡 | 监管/融资/失败风险 |
| 医药临床 / FDA 催化 | 关键临床或审批会如何影响股价 | 中 | 中高 | 垂直事件卡 | 二元结果、波动巨大 |
| IPO lock-up / 解禁 | 供给冲击会不会压制股价 | 中 | 中 | 风险提示 / 事件卡 | 拥挤交易反向挤压 |
| Spin-off / 特殊情形 | 公司结构变化是否释放价值 | 低中 | 中 | 深度研究卡 | 样本少、周期长 |
| 板块轮动 / sector rotation | 资金是否从一个行业切到另一个行业 | 高 | 中 | 市场环境层 | 信号滞后 |
| 宏观 regime | 当前市场是否支持风险资产 | 高 | 中高 | 全局风控层 | 宏观解释过度 |
| 价值 / 质量 / 低波 / 多因子 | 哪些股票长期风险收益更好 | 高 | 中 | 基础筛选 / 估值约束 | 短期不一定驱动上涨 |
| 均值回归 / 超跌反弹 | 跌多了会不会反弹 | 高 | 中低 | 受限机会卡 | 接飞刀 |
| 黑箱 ML 涨跌预测 | 明天涨跌概率是多少 | 中 | 低 | 研究实验 | 过拟合、解释性弱 |

完整调研后的产品判断：

1. 主机会卡应该来自“可解释、可回测、有明确失效条件”的策略族；
2. 辅助信号不能单独推送，但可以提高或降低机会置信度；
3. 高风险策略不是不研究，而是要在卡片里明确标注“它只能支持什么、不能说明什么”；
4. 每个策略都必须有触发条件、数据字段、评分、反证、复盘口径；
5. 工程实现顺序可以排序，但研究范围不再留白。

---

## 3. 策略一：PEAD 财报超预期后漂移

### 3.1 策略含义

PEAD 是 Post-Earnings Announcement Drift，即财报公布后，如果公司释放了明显正向或负向的盈利信息，股价的异常收益可能在随后几周甚至几个月继续沿着财报方向漂移。

Quantpedia 对这个现象的描述是：正向盈利公告之后，股票累计异常收益可能继续漂移数周甚至数月；这个异常最早可追溯到 Ball and Brown 1968，并且被很多市场研究复核过。

它非常适合我们的产品，因为它可以回答用户最关心的问题：

> 财报已经涨了，还能不能继续涨？

或者：

> 财报没怎么涨，是不是市场还没反应完？

### 3.2 为什么适合机会卡

PEAD 的好处是它天然结构化：

```text
财报日期
-> EPS / Revenue / Margin / Guidance surprise
-> 公告日股价反应
-> 成交量反应
-> 后续 5/10/20/60 日漂移
-> 是否过度反应或反应不足
```

这比普通新闻更好做成机会卡。

新闻可能很模糊，比如“AI 需求强劲”；财报则有明确数据：

- EPS 是否超预期；
- 收入是否超预期；
- 毛利率是否改善；
- 指引是否上调；
- 管理层是否给出订单、产能、需求可见性；
- 股价当日是否大涨、平涨或下跌；
- 市场是否低估了财报质量。

### 3.3 可产品化触发条件

一个 PEAD 机会可以这样触发：

```text
earnings_date = 最近 1-3 个交易日
EPS surprise > 0
Revenue surprise > 0 或 revenue YoY growth 加速
Guidance sentiment >= neutral
Announcement day return 不过度夸张
Volume spike 明显
后续 1-3 日没有快速反转
```

更适合推送的情况：

1. 财报质量好，但公告日涨幅不大；
2. EPS 和收入同向超预期；
3. 指引上调，而市场反应温和；
4. 成交量放大，但价格没有过度远离均线；
5. 分析师随后开始上修 EPS 或目标价；
6. 公司市值中小，信息消化速度更慢；
7. 同行业对照股也开始反应。

### 3.4 需要的数据

最小数据：

- 财报日期；
- 实际 EPS；
- 预期 EPS；
- 实际收入；
- 预期收入；
- 财报前后 OHLCV；
- 基准指数收益；
- 5/10/20/60 日后续收益；
- 市值、行业、成交额。

增强数据：

- 指引文本；
- 电话会 transcript；
- 分析师 EPS revision；
- 毛利率、经营利润率、FCF；
- 管理层订单/积压订单/产能口径；
- 新闻情绪；
- 期权 IV 和期权流。

### 3.5 评分框架

可以参考开源 `pead-tool` 的五柱评分思想，但需要避免把 BUY/HOLD 字样直接展示成荐股。

建议评分：

| 维度 | 权重 | 含义 |
|---|---:|---|
| Earnings surprise | 25% | EPS、收入、毛利率、指引是否超预期 |
| Initial reaction | 20% | 公告日是否反应不足或合理反应 |
| Drift confirmation | 25% | 1/5/10 日 CAR 是否继续确认 |
| Earnings quality | 15% | 增长质量、利润率、现金流、一次性因素 |
| Context | 15% | 行业、市场环境、分析师上修、新闻解释 |

机会卡不应输出“强买”，而应输出：

```text
机会类型：财报超预期后漂移
机会强度：高 / 中 / 低
市场反应：偏不足 / 合理 / 已过热
观察周期：5-20 个交易日
失效条件：跌破财报日低点、分析师下修、指引被证伪、市场转弱
```

### 3.6 最重要的反证

PEAD 不是“财报好就买”。

常见失败原因：

1. 公告日前已经大涨，财报只是兑现；
2. EPS 超预期来自一次性收益；
3. 收入没超预期，只有成本控制带来 EPS beat；
4. 指引下调，但 headline EPS 好看；
5. 开盘大幅跳空后冲高回落；
6. 大盘或行业风险偏好急剧下降；
7. 财报后分析师没有上修，甚至下修；
8. 小票流动性太差，交易成本吃掉收益；
9. 新闻热度过高，散户 FOMO 已经拥挤。

### 3.7 机会卡示例字段

```yaml
strategy_id: pead_earnings_drift
title: 财报超预期后漂移
symbol: XYZ
horizon: 5-20 trading days
why_now:
  - EPS beat
  - Revenue beat
  - Guidance raised
market_reaction:
  announcement_return: 3.2%
  volume_spike: 2.4x
  reaction_label: 反应偏温和
evidence:
  earnings_surprise_score: 82
  initial_reaction_score: 74
  drift_confirmation_score: 68
  quality_score: 77
risks:
  - 公告日前 20 日已上涨较多
  - 50 日均线乖离偏高
watch:
  - 未来 3 日是否守住财报日低点
  - 分析师是否上修 EPS
  - 成交量是否继续高于均值
invalid_if:
  - 跌破财报日低点
  - EPS revision 转负
  - 行业 ETF 跌破 50DMA
```

### 3.8 适合作为主推送策略的原因

PEAD 是最值得放进主推送层的策略之一，因为：

- 事件边界清晰；
- 数据相对容易获取；
- 用户容易理解；
- 可以历史复盘；
- 能解释“为什么不是新闻摘要”；
- 很适合和分析师上修、趋势健康、期权流叠加。

---

## 4. 策略二：分析师预期上修 / 盈利动量

### 4.1 策略含义

市场短期常常不是简单交易“现在的业绩”，而是交易“未来预期变化”。当分析师开始上调 EPS、收入、目标价或评级时，说明市场共识可能正在重估公司未来盈利。

这个策略回答的问题是：

> 这家公司是不是正在被市场重新定价？

和 PEAD 不同，分析师预期上修可以发生在财报后，也可以发生在产品发布、行业景气、订单变化、监管变化之后。

### 4.2 为什么重要

对成长股来说，股价最强的阶段往往来自：

```text
业绩超预期
-> 分析师上修未来 EPS / Revenue
-> 估值模型上移
-> 机构继续买入
-> 股价趋势强化
```

如果只有新闻，没有预期上修，这个利好可能只是情绪。

如果新闻之后出现连续上修，说明它开始进入模型。

### 4.3 可产品化触发条件

触发条件：

- 最近 1-10 个交易日 EPS estimate 上修；
- revenue estimate 上修；
- target price 上调；
- rating upgrade；
- 多家机构同向上修；
- 上修发生在财报、订单、产品发布或行业变化之后；
- 股价未完全透支。

更强的信号：

1. 本季度和下一财年同时上修；
2. 上修不是一家券商孤立行为；
3. 上修幅度超过过去一年常态；
4. 上修后股价没有过热；
5. 上修发生在行业景气早期；
6. 上修与实际基本面数据一致。

### 4.4 需要的数据

最小数据：

- EPS consensus；
- revenue consensus；
- revision date；
- analyst count；
- target price revision；
- rating change；
- 股价变化；
- 行业指数变化。

增强数据：

- 分析师报告标题或摘要；
- 上修原因分类；
- 历史 revision surprise；
- forecast dispersion；
- buy/sell/hold 分布；
- 管理层指引变化。

### 4.5 评分框架

```text
Revision score =
  上修幅度
  + 上修广度
  + 上修持续性
  + 上修覆盖未来周期
  + 与价格动量一致性
  - 已定价惩罚
  - 低覆盖度惩罚
```

建议维度：

| 维度 | 含义 |
|---|---|
| Revision magnitude | EPS / revenue 上修幅度 |
| Revision breadth | 多少分析师同向上修 |
| Revision persistence | 是否连续多日或多周上修 |
| Horizon quality | 只是本季度上修，还是下一财年也上修 |
| Price confirmation | 股价和相对强度是否确认 |
| Crowding risk | 是否已经过热 |

### 4.6 反证条件

1. 只有目标价上调，但 EPS 没上调；
2. 上修来自股价上涨后的追认；
3. 分析师覆盖很少，数据噪音大；
4. 上修集中在短期，长期盈利不变；
5. 上修幅度小，但股价已经大涨；
6. forecast dispersion 扩大，说明分歧上升；
7. 公司指引并未支持上修；
8. 同行业其他公司没有同步改善。

### 4.7 产品位置

这个策略更适合作为“确认层”：

```text
新闻催化
-> 是否进入分析师模型
-> 是否上修未来 EPS / Revenue
-> 是否支持继续上涨
```

它不一定单独生成机会，但可以显著提高机会卡可信度。

---

## 5. 策略三：成长股强势趋势 / Stage 2 领导股

### 5.1 策略含义

用户问“最近哪些股票会涨”，很多时候不是要短线预测，而是在问：

> 现在市场主线里的强势股是谁？

成长股强势趋势策略的核心不是低估值，而是寻找市场正在确认的领涨股：

- 股价强于市场；
- 中长期均线多头排列；
- 接近 52 周高点；
- 收入或 EPS 高增长；
- 成交量支持；
- 大盘环境不差；
- 风险收益比还没有恶化。

这类策略可以参考 Minervini Trend Template、CANSLIM、relative strength screener 和开源 growth-stock-screener 的做法。

### 5.2 开源实现观察

调研的 `RyanJHamby/stock-screener` 提供了一个很好的产品化方向：

- 扫描 3,800+ 美股；
- 只关注确认的 Phase 2 上升趋势；
- 使用 Minervini 8 条趋势模板；
- 叠加相对强度；
- 叠加基本面增长；
- 计算止损；
- 要求风险收益比；
- 用市场状态过滤。

`starboi-63/growth-stock-screener` 的设计更像一个清晰漏斗：

1. RS percentile；
2. 流动性过滤；
3. 均线趋势过滤；
4. SEC XBRL 收入增长过滤；
5. 机构持仓变化标记。

这个方向非常适合我们，因为用户不需要看 500 个指标，而需要少量“为什么它是强势成长股”的解释。

### 5.3 可产品化触发条件

基础条件：

```text
price > 50DMA
price > 150DMA
price > 200DMA
50DMA > 150DMA > 200DMA
200DMA 上行
price within 25% of 52-week high
price at least 30% above 52-week low
relative_strength >= 80/90
```

基本面增强：

```text
revenue YoY growth >= 20%/25%
EPS growth positive or accelerating
gross margin stable/improving
next-year revenue/EPS estimates up
```

风险过滤：

```text
distance_to_50DMA 不宜过高
recent_runup 不宜过高
average dollar volume 充足
SPY / QQQ regime 不应明显转弱
```

### 5.4 机会卡回答的问题

这种机会卡不是说“今天必涨”，而是说：

> 这只股票处在强势成长趋势中，当前回调/突破/整理可能值得观察。

用户能看到：

- 它为什么强；
- 强在哪里；
- 是基本面强，还是只有价格强；
- 有没有过热；
- 回调到哪里仍然健康；
- 跌破哪里说明趋势坏了。

### 5.5 评分框架

| 维度 | 权重 | 解释 |
|---|---:|---|
| Trend structure | 30% | 均线排列、价格位置、52 周高点距离 |
| Relative strength | 20% | 相对 SPY/QQQ/行业的强度 |
| Fundamental growth | 25% | 收入、EPS、毛利率、预期上修 |
| Volume quality | 10% | 上涨放量、下跌缩量、突破量能 |
| Risk/reward | 15% | 离支撑和止损的距离，上方空间 |

### 5.6 反证条件

1. 离 50 日均线过远，短线过热；
2. 突破后快速跌回平台；
3. 下跌放量，上涨缩量；
4. 大盘转入风险规避；
5. 收入增长放缓，但股价还在涨；
6. 只有估值扩张，没有盈利上修；
7. 同行业龙头开始走弱；
8. 分析师预期下修。

### 5.7 产品位置

这是主推送层必须覆盖的策略族。

理由：

- 用户喜欢“强势股”；
- 数据可获得；
- 可解释性强；
- 和 GF-DMA Health Index 可以自然结合；
- 可以每天扫描；
- 可以产生“回调观察”和“突破观察”两类机会。

---

## 6. 策略四：事件催化 + 财务传导

### 6.1 策略含义

这是最接近用户表达的策略：

> 最近有什么消息利好什么股票？

但我们不能做成新闻摘要。必须把每条消息拆成：

```text
消息
-> 真实需求
-> 受益环节
-> 受益公司
-> 财务科目
-> 市值弹性
-> 价格是否已反应
-> 后续验证点
```

这和 Serenity Alpha 的思路一致：新闻必须转成投资假设。

### 6.2 哪些事件适合做机会

高价值事件：

- 大订单；
- 产品提价；
- 产能扩张；
- 客户导入；
- FDA / 政策 / 监管批准；
- 重大合作；
- AI 数据中心 capex 变化；
- 半导体供应链订单变化；
- 机器人量产进展；
- 行业库存周期反转；
- 竞争对手出问题；
- 上游价格下降；
- 下游需求爆发。

低价值事件：

- 泛泛的宏观评论；
- 已经重复很多次的主题；
- 没有公司映射的概念；
- 管理层空泛表态；
- KOL 情绪喊单；
- 单纯股价上涨后的解释性新闻。

### 6.3 财务传导模板

一条新闻只有进入财务科目，才有研究价值。

```text
AI 数据中心液冷需求增加
-> 服务器功率密度提升
-> 液冷部件需求增加
-> 公司 A 液冷收入占比 12%
-> 新订单可能进入 backlog
-> 未来 2-4 个季度收入确认
-> 毛利率可能高于传统业务
-> 若市值小，收入弹性更高
```

机会卡必须写清楚：

- 利好来自需求、价格、成本、份额、估值还是风险下降；
- 对应收入、毛利率、订单、现金流还是估值倍数；
- 是一阶受益者还是二阶受益者；
- 市场是否已经知道；
- 后续用什么数据验证。

### 6.4 可产品化触发条件

触发条件：

```text
news_event detected
entity mapped to ticker(s)
catalyst_type classified
financial_transmission_score >= threshold
market_reaction not overextended
related tickers show confirmation or underreaction
```

评分维度：

| 维度 | 含义 |
|---|---|
| Catalyst materiality | 事件是否真正重要 |
| Revenue exposure | 公司暴露度是否足够 |
| Timing | 何时可能进入财务报表 |
| Market awareness | 市场是否已充分反应 |
| Elasticity | 市值和收入体量是否有弹性 |
| Verification path | 后续能否验证 |
| Risk | 是否只是叙事或一次性事件 |

### 6.5 新闻不能直接打分

新闻情绪本身很容易误导。RavenPack 的研究强调，不应只做“正面新闻买入、负面新闻卖出”，而要结合 attention、media coverage、sentiment momentum、波动率和价格动量等条件。Fed 的文本研究也提示，新闻在不同频率上的预测效果不同，短期和季度级别的反应并不一样。

所以新闻策略必须从“情绪”升级到“事件 + 传导 + 市场确认”。

### 6.6 反证条件

1. 新闻没有明确公司受益者；
2. 公司收入暴露度太低；
3. 利好已在过去几周反复交易；
4. 股价已大幅上涨且成交量衰减；
5. 只有主题，没有订单或业绩路径；
6. 竞争格局会压缩利润；
7. 受益周期太长，短期无法验证；
8. 相关公司公告不支持；
9. 分析师没有上修；
10. 同产业链验证数据转弱。

### 6.7 产品位置

这是最能体现 agent 价值的策略，但也是最难做的策略。

产品可以从半结构化能力做起：

- 新闻分类；
- ticker 映射；
- 产业链关系；
- 财务传导字段；
- 市场反应；
- 人工可审核的机会卡；
- 后续结果复盘。

不要一开始就承诺“自动判断所有消息利好谁”。正确做法是先覆盖有限主题：

- AI 数据中心；
- 半导体；
- 电力设备；
- 液冷；
- 机器人；
- 云计算；
- 医疗科技；
- SaaS；
- 核电 / 电网；
- 国防科技。

---

## 7. 策略五：健康趋势中的回调机会

### 7.1 策略含义

用户经常遇到的问题是：

> 一只股票已经涨了，我现在还能不能买？

更好的问法是：

> 它的上涨趋势是否健康？当前回调是正常整理，还是趋势坏了？

这个策略和 GF-DMA Health Index 很契合。

### 7.2 可产品化触发条件

```text
stock in Stage 2 uptrend
price above 100DMA / 200DMA
50DMA above 200DMA
relative strength still positive
pullback to 20DMA or 50DMA area
pullback volume below up-day volume
fundamental/catalyst not broken
market regime not risk-off
```

### 7.3 评分框架

| 维度 | 含义 |
|---|---|
| Trend health | 均线结构是否健康 |
| Pullback quality | 回调是否缩量、是否守住关键位 |
| Fundamental support | 基本面增长是否仍支持趋势 |
| Catalyst integrity | 原催化是否仍成立 |
| Market regime | 大盘是否支持风险资产 |
| Overextension relief | 前期过热是否已释放 |

### 7.4 机会卡表达

用户不应看到“买入点”，而应看到：

```text
机会类型：强势股健康回调
当前状态：回调到 20/50 日均线附近
为什么可能重新走强：趋势结构未破、成交量缩小、基本面预期未下修
观察位：50DMA / 财报日低点 / 前平台上沿
失效条件：放量跌破 50DMA、EPS revision 转负、行业 ETF 破位
```

### 7.5 反证条件

1. 回调放量；
2. 跌破 50DMA 后无法收复；
3. 50DMA 开始走平或下行；
4. 行业 ETF 同步破位；
5. 分析师下修；
6. 新闻催化被证伪；
7. 股价反弹无量；
8. 市场从 risk-on 转为 risk-off。

### 7.6 产品位置

非常适合作为主推送策略，因为用户最容易理解，也最容易使用。

但它不应单独存在，必须绑定：

- 成长股强势趋势；
- 财报/新闻催化；
- 基本面预期；
- 市场 regime。

---

## 8. 策略六：突破 / VCP / 成交量确认

### 8.1 策略含义

突破策略寻找的是：

> 股票经过整理后，价格和成交量同时确认新一轮上行。

VCP 是 Volatility Contraction Pattern，即波动逐步收敛后突破。

### 8.2 适合产品化的地方

突破策略能给用户提供“什么时候值得关注”的触发点。

但突破本身噪音很高，必须叠加：

- 基本面；
- 相对强度；
- 行业趋势；
- 大盘环境；
- 成交量；
- 失败突破检测。

### 8.3 触发条件

```text
price breaks above recent pivot / 52-week high / base high
volume > 1.5x or 2x average
relative strength positive
price not too far above 50DMA
prior volatility contraction present
market regime supportive
```

### 8.4 反证条件

1. 低量突破；
2. 突破后当天收回平台内；
3. 次日放量下跌；
4. 突破前已经连续大涨；
5. 离 50DMA 太远；
6. 大盘当天走弱；
7. 没有基本面或催化支持。

### 8.5 产品位置

突破策略适合做 P1。

它应该是“触发器”，不是“独立推荐理由”。

机会卡写法：

```text
它不是因为突破而值得看；
它是因为基本面/催化/预期上修成立，并且今天出现突破确认。
```

---

## 9. 策略七：期权异动确认

### 9.1 策略含义

用户很容易被 unusual options activity 吸引，因为它看起来像“聪明钱提前下注”。

但期权流不能直接等于看涨。

原因：

- 可能是对冲；
- 可能是价差组合；
- 可能是平仓；
- 可能是做波动率；
- 可能是跟股票仓位相反；
- 可能是媒体曝光后的拥挤交易；
- 买卖方向不一定准确；
- OTM call 异动很容易被散户 FOMO 放大。

### 9.2 学术和产品观察

Pan & Poteshman 的经典研究发现，期权交易量中确实包含未来股票价格信息，尤其是基于买方发起交易构建的 put-call ratio。但后续 unusual option activity 研究也提醒：媒体曝光的异动可能出现短期过度反应和随后反转。

因此，期权流最适合做确认信号：

```text
事件催化成立
基本面传导成立
股价尚未过热
期权流同步出现方向性异常
=> 机会可信度提高
```

不适合：

```text
只有期权异动
=> 推送用户买股票
```

### 9.3 可产品化触发条件

```text
option volume / open interest unusual
trade near ask or buyer-initiated probability high
call/put direction clear
DTE reasonable
strike not absurdly far OTM
underlying price confirms
news/catalyst exists
IV not already extreme
```

### 9.4 需要的数据

- options chain；
- trade-level options prints；
- bid/ask；
- volume；
- open interest；
- DTE；
- strike；
- implied volatility；
- delta；
- underlying price reaction；
- whether sweep/block/repeated hits；
- historical unusual baseline。

### 9.5 评分框架

| 维度 | 含义 |
|---|---|
| Unusualness | 相对历史成交量和 OI 是否异常 |
| Direction clarity | 方向是否清楚 |
| Moneyness | 行权价是否合理 |
| DTE quality | 到期日是否支持事件周期 |
| Underlying confirmation | 股票是否同步确认 |
| Catalyst alignment | 是否有新闻/财报/分析师上修配合 |
| IV risk | 是否 IV 过高 |
| Hedge ambiguity | 是否可能是对冲或组合腿 |

### 9.6 反证条件

1. 只有 OTM lottery calls；
2. 没有新闻或基本面事件；
3. IV 已极端高；
4. 股票价格没有同步确认；
5. 期权成交量高但 OI 变化不支持新仓；
6. 很可能是 spread；
7. 交易发生在媒体曝光之后；
8. 标的流动性不足；
9. DTE 太短，像纯投机。

### 9.7 产品位置

期权异动建议作为辅助确认信号，不建议作为独立主推送策略。

机会卡可以这样表达：

```text
资金面确认：中等
证据：近月 call volume/OI 异常，股票同步放量突破
风险：IV 偏高，不能判断是否为对冲，不能单独作为买入理由
```

---

## 10. 策略八：产业链主题扩散

### 10.1 策略含义

用户看到热点后最关心：

> 这个主题里真正有弹性的股票是谁？

产业链主题扩散策略要解决的是二阶受益：

```text
龙头公司利好
-> 上游供应商订单改善
-> 小市值供应商收入弹性更大
-> 市场初期只交易龙头
-> 二阶受益者后续补涨
```

### 10.2 适合主题

- AI 数据中心；
- 液冷；
- 光模块；
- HBM；
- 半导体设备；
- 电网；
- 核电；
- 机器人；
- GLP-1；
- 医疗 AI；
- 国防；
- 云基础设施；
- 自动驾驶。

### 10.3 需要的数据

- 产业链图谱；
- 公司 segment revenue；
- 客户/供应商关系；
- 订单公告；
- capex 计划；
- 行业价格；
- 库存周期；
- 市值；
- 分析师覆盖；
- 股价反应。

### 10.4 评分框架

| 维度 | 含义 |
|---|---|
| Theme strength | 主题是否真实升温 |
| Exposure | 公司收入暴露度 |
| Elasticity | 小市值/低基数带来的弹性 |
| Timing | 何时进入订单或收入 |
| Verification | 后续如何验证 |
| Market awareness | 是否还没充分定价 |
| Competition | 竞争是否会削弱利润 |

### 10.5 反证条件

1. 公司只是概念相关，没有收入暴露；
2. 市场已经交易过很多轮；
3. 订单无法确认；
4. 毛利率会被竞争压低；
5. 主题属于一次性政策刺激；
6. 龙头受益，但供应链议价能力弱；
7. 公司披露不足，无法验证；
8. 股价已经远超财务可解释范围。

### 10.6 产品位置

这是 agent 最有差异化的方向，但数据建设成本高。

产业链主题库可以先用人工维护或半自动维护保证准确性：

```yaml
theme: AI data center liquid cooling
drivers:
  - rack power density
  - GPU cluster capex
  - thermal constraints
beneficiaries:
  - direct equipment makers
  - component suppliers
  - coolant suppliers
verification:
  - orders
  - segment revenue
  - gross margin
  - customer capex
```

然后用新闻和价格数据触发。

---

## 11. 策略九：均值回归 / 超跌反弹

### 11.1 为什么不适合作为默认主推送

“跌多了会反弹”很吸引用户，但对普通用户很危险。

原因：

- 下跌可能来自基本面恶化；
- 跌破趋势后可能继续跌；
- 低估值可以更低；
- 超跌指标容易频繁误触发；
- 需要严格止损；
- 用户容易把短线反弹理解成长期机会。

### 11.2 什么时候可以做

均值回归只适合非常受限的场景：

- 高流动性大盘股或 ETF；
- 没有基本面坏消息；
- 市场 regime 稳定；
- 已经出现恐慌性偏离；
- 波动率回落；
- 有明确止损；
- 持有期很短。

### 11.3 产品位置

建议作为受限机会卡，不作为默认主动推送。

如果做，也必须叫：

```text
短线超跌反弹观察
```

而不是：

```text
价值机会
```

---

## 12. 策略十：期权组合策略

### 12.1 产品价值

期权组合策略不是“找会涨的股票”，而是回答：

> 如果我有某个方向判断，怎样用期权表达风险收益？

例如：

- covered call；
- cash secured put；
- call spread；
- put spread；
- collar；
- iron condor；
- strangle；
- butterfly。

### 12.2 为什么要设为专业能力边界

它对数据、风险和用户理解要求更高：

- Greeks；
- IV；
- bid/ask spread；
- assignment risk；
- liquidity；
- margin；
- event volatility；
- early exercise；
- expiry management。

`options_portfolio_backtester` 这种开源项目强调 contract-level inventory、Greeks-aware risk、fill/cost/slippage models，说明期权策略不能只靠简单 payoff 图。

### 12.3 产品位置

在普通股票机会卡里可以先保留为风险提示：

```text
该机会临近财报，IV 很高，直接买 call 面临 IV crush 风险。
```

不要直接推荐复杂期权结构。

---

## 13. 策略十一：空头回补 / Short Squeeze

### 13.1 策略含义

空头回补不是传统基本面上涨逻辑，而是交易结构驱动：

```text
高空头比例
-> 正向催化或价格突破
-> 空头亏损扩大
-> 回补买盘推高价格
-> 更多空头被迫回补
```

这个策略回答用户的问题是：

> 这只股票是不是可能因为空头拥挤而快速上涨？

它对“最近会不会涨”很有吸引力，但风险也很高，因为高空头本身往往意味着市场对公司有强烈负面判断。

### 13.2 需要的数据

核心数据：

- short interest；
- short interest as % of float；
- days to cover；
- float；
- borrow fee / borrow availability；
- recent short sale volume；
- 股价突破；
- 成交量；
- 正向催化；
- 期权 call activity；
- 社媒 attention。

数据注意：

FINRA 和 Nasdaq 都提供 short interest 或 short sale volume 相关数据，但 daily short sale volume 不是 short interest。FINRA 也明确区分短售成交量和双月度空头持仓数据，不能把每日 short volume 误读成市场当前空头仓位。

### 13.3 触发条件

```text
short_interest_float > 15% 或 20%
days_to_cover > 3
borrow_fee 上升
float 较小
positive catalyst 出现
price breaks key resistance
volume spike
call option activity confirms
```

更强情况：

1. 公司发布正向财报或订单；
2. 空头 thesis 被明显证伪；
3. 股价放量突破长期压力位；
4. 可流通股少；
5. 期权市场出现近月 call 异动；
6. 行业也出现风险偏好改善。

### 13.4 反证条件

1. 高空头是因为基本面持续恶化；
2. 公司现金流差，需要融资；
3. 股价只是短线拉升，没有成交量持续；
4. short interest 数据滞后；
5. call activity 可能只是投机 lottery ticket；
6. 社媒过热，交易已经拥挤；
7. 公司发布增发、ATM、可转债等稀释消息；
8. 借券费没有上升，说明挤压压力不足。

### 13.5 机会卡表达

不能写成“高空头，必然轧空”。

应该写成：

```text
机会类型：空头回补风险
为什么可能上涨：高空头仓位 + 正向催化 + 放量突破
为什么危险：高空头通常代表市场存在真实质疑
失效条件：突破失败、催化被证伪、公司融资稀释
```

---

## 14. 策略十二：内部人交易

### 14.1 策略含义

内部人交易指公司高管、董事、10% 以上持有人通过 SEC Form 3/4/5 披露的持股变化。它回答的问题是：

> 公司内部人是否用真金白银表达信心？

内部人买入通常比内部人卖出更有信息含量，因为卖出可能只是税务、流动性、资产配置或 10b5-1 计划。

### 14.2 需要的数据

- SEC Form 3/4/5；
- transaction code；
- open market purchase vs option exercise；
- insider role；
- shares bought/sold；
- dollar value；
- ownership change %；
- filing date；
- transaction date；
- 10b5-1 plan flag；
- 历史内部人交易模式。

### 14.3 触发条件

```text
open_market_purchase = true
role in [CEO, CFO, Chair, Director]
purchase_value meaningful
cluster_buying = true
stock near drawdown or after negative sentiment
no obvious option exercise artifact
```

更强情况：

1. 多位高管集中买入；
2. CEO/CFO 买入金额相对薪酬或持仓有意义；
3. 股价处在回撤后，而不是高位追涨；
4. 公司基本面没有明显恶化；
5. 买入后出现分析师上修或趋势修复。

### 14.4 反证条件

1. 只是期权行权，不是公开市场买入；
2. 买入金额很小，像象征性动作；
3. 只有单个董事小额买入；
4. 公司仍面临重大诉讼、债务或现金流风险；
5. 内部人历史交易没有信息含量；
6. 卖出来自预设交易计划，不能简单看空；
7. Form 4 披露有时间差。

### 14.5 产品位置

内部人交易适合作为辅助确认。

它可以提高机会卡可信度，但不应单独变成“会涨”推送。

---

## 15. 策略十三：13F / 机构持仓变化

### 15.1 策略含义

13F 披露可以帮助用户理解机构是否在持有或增加某家公司。SEC 规定符合条件的机构投资经理需要披露 13F 持仓；SEC 的 13F 数据集提供 flattened 格式的数据。

它回答的问题是：

> 有没有长期机构资金正在关注这个方向？

### 15.2 关键限制

13F 很有价值，但不能当成实时买入信号：

1. 披露通常有滞后；
2. 只显示多头持仓，不显示完整组合；
3. 不显示空头；
4. 不显示很多衍生品或海外资产；
5. 持仓可能已经变化；
6. 大机构持仓不代表短期会涨。

### 15.3 可用触发

```text
institutional_ownership_change positive
new_position_by_high_quality_manager
multiple_funds_accumulating
position_size meaningful
aligned_with_theme_or_earnings_revision
```

更适合的用途：

- 验证产业链主题是否被机构关注；
- 验证成长股是否有机构承接；
- 识别小市值公司被新机构发现；
- 作为长期置信度增强。

### 15.4 反证条件

1. 数据太旧；
2. 持仓来自指数基金，不代表主动观点；
3. 加仓金额相对基金规模很小；
4. 机构可能已在披露后卖出；
5. 公司短期催化和机构持仓无关。

---

## 16. 策略十四：回购 / Buyback

### 16.1 策略含义

回购公告通常被市场理解为管理层认为股价有吸引力，或者公司有充足现金流。学术事件研究中，回购公告往往伴随正向异常收益，但效果受公司规模、估值、执行力度和市场环境影响。

这个策略回答：

> 公司回购是否构成股价支撑或重估催化？

### 16.2 需要的数据

- board authorization；
- authorization size；
- market cap；
- cash balance；
- free cash flow；
- net debt；
- historical repurchase execution；
- 10-Q / 10-K repurchase table；
- management commentary；
- valuation multiple；
- share count trend。

### 16.3 触发条件

```text
new_buyback_authorization
authorization_size / market_cap meaningful
company has FCF support
valuation not extreme
management has history of executing buybacks
stock not overextended
```

### 16.4 反证条件

1. 授权不等于执行；
2. 公司用回购抵消股权激励稀释；
3. 现金流不足；
4. 债务压力大；
5. 高位回购毁灭价值；
6. 公告前股价已经大涨；
7. 回购规模相对市值太小；
8. 管理层历史上只宣布不执行。

### 16.5 机会卡表达

```text
机会类型：资本回报催化
为什么可能支撑股价：回购授权规模占市值 X%，FCF 覆盖较好
关键反证：后续季度没有实际回购、现金流下滑、股权激励稀释抵消
```

---

## 17. 策略十五：并购 / Merger Arbitrage

### 17.1 策略含义

并购套利不是传统“看涨股票”逻辑，而是交易公告价与当前价格之间的价差。

```text
收购公告
-> 目标公司股价接近收购价但保留折价
-> 折价反映交易失败、时间和融资风险
-> 若交易完成，价差收敛
-> 若失败，股价可能大跌
```

它回答的问题是：

> 公告后的价差是否补偿了交易失败风险？

### 17.2 需要的数据

- deal price；
- cash / stock / mixed consideration；
- target current price；
- spread；
- expected closing date；
- antitrust / regulatory risk；
- financing condition；
- shareholder vote；
- competing bidder；
- acquirer stock movement；
- termination fee；
- outside date。

### 17.3 评分框架

| 维度 | 含义 |
|---|---|
| Spread attractiveness | 年化价差是否足够 |
| Deal probability | 完成概率 |
| Time to close | 预计交割时间 |
| Regulatory risk | 监管风险 |
| Financing risk | 融资风险 |
| Downside if break | 失败后跌幅 |
| Liquidity | 交易流动性 |

### 17.4 反证条件

1. 监管阻力强；
2. 买方融资不稳；
3. 股东反对；
4. acquirer 股价大跌影响 stock deal；
5. spread 很窄但 downside 很大；
6. 交易时间过长，年化收益被稀释；
7. 用户不了解事件套利风险。

### 17.5 产品位置

并购套利应该作为专业事件卡，不适合混在普通“可能上涨”推送里。

---

## 18. 策略十六：医药临床 / FDA 催化

### 18.1 策略含义

生物医药股票的价格常被临床数据、PDUFA、FDA approval、CRL、合作授权和收购传闻驱动。PLOS One 对生物医药新闻的大样本事件研究显示，不同类型新闻对股价影响差异很大，收购类新闻正向影响最大，药物开发挫折往往带来显著负面影响。

这个策略回答：

> 临床或监管事件是否可能显著重估公司？

### 18.2 需要的数据

- catalyst calendar；
- trial phase；
- indication；
- primary endpoint；
- secondary endpoints；
- comparator；
- prior data；
- PDUFA date；
- FDA advisory committee；
- market size；
- cash runway；
- current enterprise value；
- pipeline concentration；
- partnership status；
- option IV。

### 18.3 触发条件

```text
upcoming_catalyst within window
event_materiality high
market_cap small/mid
pipeline_concentration high
prior_data supportive
cash_runway sufficient
price_runup not excessive
IV risk understood
```

### 18.4 反证条件

1. 事件结果二元，亏损可能极大；
2. 小样本 trial 容易误读；
3. endpoint 不清晰；
4. 数据公布前股价已大幅上涨；
5. 公司需要融资；
6. approval 不等于商业成功；
7. 竞争药物更强；
8. IV 极高，期权买方可能亏在波动率。

### 18.5 产品位置

医药/FDA 可以做完整垂直策略，但必须单独标注高波动和二元风险，不能和普通财报漂移放在同一个风险等级。

---

## 19. 策略十七：IPO Lock-up / 解禁与供给冲击

### 19.1 策略含义

IPO lock-up expiration 会释放潜在卖盘，常被市场视为供给冲击事件。研究和市场经验都显示，解禁日前后可能出现价格压力，但如果提前做空过于拥挤，也可能出现反向 squeeze。

它回答的问题是：

> 解禁会不会压制股价，或者形成反向挤压？

### 19.2 需要的数据

- IPO date；
- lock-up expiration date；
- shares unlocking；
- existing float；
- unlock / float ratio；
- insider / VC ownership；
- recent price runup；
- short interest；
- borrow fee；
- option activity；
- company fundamentals；
- post-IPO earnings history。

### 19.3 触发条件

供给压力信号：

```text
unlock_size / float high
stock overvalued or weak
insiders likely sellers
short_interest not too crowded
trend weak
```

反向 squeeze 信号：

```text
pre-unlock selloff large
short_interest high
borrow crowded
actual selling limited
price holds support
```

### 19.4 反证条件

1. 解禁股东不卖；
2. 市场已提前下跌；
3. 空头过度拥挤；
4. 公司同时发布强业绩；
5. 解禁比例不大；
6. 流动性足以吸收供给；
7. 数据里的 lock-up 条款不完整。

---

## 20. 策略十八：特殊情形、板块轮动与多因子约束

### 20.1 特殊情形 / Spin-off

Spin-off、资产出售、重组、破产重整、指数纳入/剔除都可能带来机会。但这类策略样本少、事件复杂、持有期长，适合做深度研究卡。

需要重点看：

- transaction terms；
- pro-forma financials；
- forced selling；
- index impact；
- leverage；
- management incentives；
- standalone valuation；
- tax treatment。

### 20.2 板块轮动

板块轮动回答：

> 资金现在更偏好哪些行业？

可用数据：

- sector ETF relative strength；
- breadth；
- earnings revision breadth；
- fund flows；
- price momentum；
- macro sensitivity；
- rates / dollar / oil / volatility。

它不应该直接推某只股票，而应该作为机会卡的环境层：

```text
当前半导体板块相对强，行业内 PEAD / revision / breakout 信号权重上调。
```

### 20.3 宏观 Regime

宏观 regime 不是预测宏观，而是给所有股票信号加减权：

- risk-on / risk-off；
- high-vol / low-vol；
- rates rising / falling；
- dollar strong / weak；
- credit spread widening / tightening；
- liquidity improving / tightening。

AgentQuant 的 regime card 思路有参考价值：用 VIX percentile、价格动量、均线、回撤和宏观变量建立市场状态，避免在坏环境里机械推多头机会。

### 20.4 多因子约束

Fama-French、AQR 等长期因子研究说明，价值、动量、质量、低风险、规模、盈利能力、投资风格等因子能解释大量股票回报差异。

但在我们的产品中，多因子不应直接变成“明天会涨”，而应作为：

- 基础质量过滤；
- 估值约束；
- 风险暴露解释；
- 策略归因；
- 组合层面分散。

例如：

```text
这张机会卡来自财报漂移，但该股同时具备高质量和正动量，估值没有处于历史极端，因此不会被风险层降权。
```

### 20.5 反证条件

1. 宏观叙事过度解释个股；
2. 板块轮动信号滞后；
3. 因子拥挤导致反转；
4. 价值因子短期不催化；
5. 质量好但估值过高；
6. 低波股票在 risk-on 环境落后；
7. 只看因子忽略公司事件。

---

## 21. 高风险策略的使用边界

### 13.1 黑箱涨跌预测

不建议作为用户可见的核心推送形态：

```text
明天上涨概率 73%
```

除非背后有清晰的策略族、数据、样本外验证和校准曲线。

否则用户无法判断：

- 为什么是 73%；
- 它是否过拟合；
- 它在什么市场环境失效；
- 亏损时应该怎么办。

### 13.2 单纯新闻情绪

不建议做：

```text
新闻情绪正面，因此看涨
```

新闻要进入：

- 事件类型；
- 影响路径；
- 公司暴露；
- 财务传导；
- 市场反应；
- 验证路径。

### 13.3 社媒热度单独信号

社媒热度可以提示关注度，但也可能意味着拥挤和反转风险。

可作为：

- FOMO 风险；
- attention 指标；
- 异常关注；
- 反向风险提示。

不适合作为独立买入理由。

### 13.4 技术指标堆叠

不要做成：

```text
RSI + MACD + KDJ + Bollinger + 20 个指标投票
```

用户不需要指标堆叠，需要的是策略语义：

- 财报漂移；
- 健康回调；
- 领涨突破；
- 预期上修；
- 事件传导；
- 资金确认。

---

## 22. 机会卡统一结构

每个策略最终都应该输出同一种机会卡，而不是每类策略一个孤立页面。

### 22.1 机会卡最小字段

```yaml
opportunity_id:
symbol:
company:
strategy_family:
horizon:
created_at:
trigger:
why_it_may_rise:
evidence:
market_reaction:
already_priced_in_assessment:
risk:
invalid_if:
watch_next:
data_quality:
historical_base_rate:
outcome_tracking:
```

### 22.2 用户可见版本

用户看到的结构应该是：

```text
XYZ：财报超预期后市场反应偏温和

为什么可能涨：
1. EPS 和收入均超预期；
2. 指引上调；
3. 公告日涨幅不大，成交量放大；
4. 分析师开始上修下一财年 EPS。

现在风险：
1. 财报前已经上涨 18%；
2. 距离 50 日均线偏远；
3. 如果跌破财报日低点，漂移假设失效。

接下来观察：
1. 未来 3 日是否守住财报日低点；
2. 是否有更多分析师上修；
3. 行业 ETF 是否维持强势。

历史同类信号：
过去 3 年类似信号 126 次，20 日后上涨概率 58%，中位超额收益 3.1%，最大回撤中位数 -5.4%。
```

### 22.3 系统内部版本

内部要保留更多字段：

```yaml
signal_strength:
catalyst_strength:
financial_transmission_score:
fundamental_revision_score:
trend_health_score:
option_flow_confirmation:
valuation_room:
priced_in_penalty:
liquidity_penalty:
market_regime_penalty:
data_quality_score:
confidence_interval:
```

---

## 23. 策略验证框架

### 23.1 每个策略必须先定义样本

不能先看涨幅再解释。

必须先冻结：

- universe；
- rebalance frequency；
- signal timestamp；
- entry price；
- exit rule；
- holding window；
- benchmark；
- transaction cost；
- slippage；
- liquidity filter；
- survivorship bias 处理；
- lookahead bias 处理。

### 23.2 每张机会卡都要事后复盘

发布机会卡时就冻结：

```yaml
created_at:
signal_data_snapshot:
price_at_signal:
benchmark_at_signal:
expected_horizon:
invalid_if:
```

后续自动记录：

```yaml
return_1d:
return_5d:
return_10d:
return_20d:
return_60d:
excess_return:
max_favorable_excursion:
max_adverse_excursion:
hit_invalid_condition:
failure_reason:
```

### 23.3 指标不能只看胜率

必须看：

- hit rate；
- average return；
- median return；
- excess return；
- max drawdown；
- MAE；
- MFE；
- payoff ratio；
- turnover；
- signal decay；
- capacity；
- liquidity；
- performance by regime；
- performance by sector；
- performance by market cap；
- performance by catalyst type。

### 23.4 需要 base rate

用户最应该看到的是：

```text
类似情况历史上表现如何？
```

而不是：

```text
AI 认为会涨。
```

机会卡必须显示：

- 同类信号数量；
- 样本区间；
- 20 日胜率；
- 中位超额收益；
- 下行风险；
- 当前信号比历史均值强还是弱。

### 23.5 LLM 的角色

LLM 不应该直接决定“买入”。

LLM 适合做：

- 新闻分类；
- 事件解释；
- 财务传导推理；
- 风险反证生成；
- transcript 摘要；
- 策略结果解释；
- 机会卡文案生成；
- 用户问答。

确定性代码应该做：

- 数据拉取；
- 指标计算；
- 信号触发；
- 回测；
- 评分；
- 复盘；
- 风险规则；
- 去重排序。

AgentQuant 的设计值得借鉴：LLM 可以提出参数，但必须被限制在 ParameterGrid 中，并经过回测锦标赛、风险指标和 bootstrap 检验。

---

## 24. 产品化策略架构

建议架构：

```text
Data Layer
  -> price / volume / fundamentals / estimates / news / options / filings

Signal Layer
  -> PEAD
  -> estimate revisions
  -> growth leadership
  -> catalyst transmission
  -> pullback health
  -> breakout confirmation
  -> options confirmation

Evidence Layer
  -> market reaction
  -> priced-in check
  -> financial transmission
  -> trend health
  -> risk and invalidation

Ranking Layer
  -> opportunity score
  -> confidence score
  -> data quality score
  -> user relevance score

Opportunity Card Layer
  -> why now
  -> why may rise
  -> what to watch
  -> what breaks thesis
  -> historical base rate

Feedback Layer
  -> outcome tracking
  -> failure taxonomy
  -> strategy health dashboard
```

---

## 25. 策略配置模板

每个策略应该写成配置，而不是散落在 prompt 里。

```yaml
id: pead_earnings_drift
name: 财报超预期后漂移
family: earnings_momentum
universe:
  market: US
  min_price: 5
  min_market_cap: 300000000
  min_avg_dollar_volume: 10000000
horizon:
  primary: 20d
  tracking: [1d, 5d, 10d, 20d, 60d]
trigger:
  earnings_date_within_days: 3
  eps_surprise_min: 0
  revenue_surprise_min: 0
filters:
  max_gap_up: 0.15
  max_distance_to_50dma: 0.20
  exclude_if_guidance_negative: true
score_components:
  earnings_surprise: 0.25
  initial_reaction: 0.20
  drift_confirmation: 0.25
  earnings_quality: 0.15
  context: 0.15
invalid_if:
  - close_below_earnings_day_low
  - eps_revision_turns_negative
  - sector_etf_breaks_50dma
output:
  card_type: opportunity
  label: 财报漂移机会
```

---

## 26. 策略排序逻辑

用户每天不应该收到 100 条。

排序不应该只按 signal score，而应该按：

```text
final_score =
  signal_strength
  * data_quality
  * user_relevance
  * market_regime_multiplier
  - priced_in_penalty
  - liquidity_penalty
  - crowding_penalty
  - uncertainty_penalty
```

### 26.1 去重

同一个股票可能同时触发：

- 财报漂移；
- 分析师上修；
- 强势趋势；
- 期权异动；
- 突破。

不要推 5 条。应该合并成一张增强机会卡：

```text
主策略：财报超预期后漂移
增强证据：分析师上修 + 趋势健康 + 期权流确认
```

### 26.2 分层推送

建议每天分三层：

1. Top opportunities：最多 3-5 个；
2. Watchlist changes：自选股重要变化；
3. Strategy alerts：策略触发但证据不足，只进入观察池。

---

## 27. 用户最关心的“如果买了会怎样”

这部分不能承诺收益，但可以提供情景推演。

每张卡都应该有：

```text
如果买入，接下来可能有三种情况：

Bull case:
  分析师继续上修，股价守住关键位并放量突破。

Base case:
  股价震荡消化，等待下一条订单/财报验证。

Bear case:
  跌破失效位，说明市场不认可这条催化或已经提前定价。
```

更具体：

```yaml
scenario:
  bull:
    condition:
      - close_above_pivot
      - revision_continues
      - sector_strength_positive
    expected_behavior:
      - trend continuation
  base:
    condition:
      - price rangebound
      - no new revision
    expected_behavior:
      - wait for confirmation
  bear:
    condition:
      - close_below_invalidation
      - volume_distribution
    expected_behavior:
      - thesis invalidated
```

用户需要的是“持有后监控路径”，不是一句“看涨”。

---

## 28. 完整调研后的落地排序建议

### 28.1 不要把所有策略同时变成主推送

研究要一次覆盖完整，但主推送层一次放太多会导致：

- 数据源不稳定；
- 回测不完整；
- 用户不信任；
- 推送噪音大；
- 无法复盘。

建议主推送层优先放：

1. 财报超预期后漂移；
2. 成长股强势趋势；
3. 事件催化 + 财务传导；
4. 分析师上修叠加；
5. 健康回调/突破作为技术确认。

期权流先作为辅助，不作为主策略。

### 28.2 每天输出

每天输出三类：

```text
A. 今日新机会
最多 3-5 个，必须有明确策略归因。

B. 自选股变化
用户持仓或关注股票出现财报、上修、破位、回调、突破。

C. 复盘更新
过去机会卡的 5/10/20 日结果，告诉用户系统是否有效。
```

### 28.3 不直接作为主推送的能力边界

不直接作为主推送的能力：

- 自动交易；
- 直接荐股；
- 黑箱涨跌概率；
- 社媒热度喊单；
- 独立期权流买入；
- 复杂期权结构；
- 高频交易；
- 盘中秒级信号；
- 大而全的 Bloomberg 替代品。

---

## 29. 数据源完整清单

### 29.1 核心数据

| 数据 | 用途 |
|---|---|
| OHLCV | 趋势、回调、突破、反应幅度 |
| 财报日期和 EPS / revenue surprise | PEAD |
| SEC XBRL / fundamentals | 收入增长、利润率 |
| 新闻 | 催化事件 |
| 分析师预期 | 上修确认 |
| 行业 / ETF 映射 | 相对强弱和 regime |

SEC 官方 EDGAR API 支持 company submissions 和 XBRL companyfacts，而且不需要 API key，适合做美股基本面原型。

### 29.2 扩展数据

| 数据 | 用途 |
|---|---|
| transcript | 财报质量和管理层指引 |
| options chain / flow | 资金确认 |
| institutional ownership | 成长股机构吸筹 |
| supply chain mapping | 产业链主题扩散 |
| short interest | squeeze / crowded short |
| insider transactions | 辅助确认 |

### 29.3 完整数据能力矩阵

| 数据域 | 代表来源 | 覆盖策略 | 注意事项 |
|---|---|---|---|
| OHLCV / intraday / adjusted prices | Massive/Polygon、Finnhub、FMP、Yahoo、Stooq、OpenBB | 趋势、突破、回调、均值回归、事件反应 | 复权、盘前盘后、延迟、幸存者偏差 |
| 财报日历 / actual vs estimate | Finnhub、Benzinga、FMP、Koyfin、FactSet/IBES | PEAD、盈利动量、财报事件 | estimate 口径差异、non-GAAP 与 GAAP |
| 分析师评级 / 目标价 / 预期 | Benzinga、Finnhub、FMP、Koyfin、FactSet/IBES | 预期上修、催化确认 | 覆盖度、滞后、追认式上修 |
| SEC XBRL / companyfacts | SEC EDGAR APIs、OpenBB、FMP | 成长质量、收入增长、利润率 | taxonomy 不一致、公司自定义标签 |
| SEC filings / 8-K / 10-Q / 10-K | SEC EDGAR、sec-api、OpenBB | 事件催化、风险、财务验证 | 文本解析质量、披露滞后 |
| Earnings transcripts | FMP、Finnhub、Koyfin、Seeking Alpha、Quartr | 指引、管理层语气、PEAD 增强 | 版权、延迟、摘要误差 |
| 新闻 / press releases | Benzinga、FMP、Polygon/Massive、Finnhub、RavenPack | 事件催化、新闻 attention | 速度、去重、来源可信度 |
| 期权链 / 逐笔期权 | Polygon/Massive、Unusual Whales、Tradier、Cboe、OPRA vendors | 期权异动、IV 风险、事件波动 | 成本高、方向误读、bid/ask 质量 |
| Short interest | FINRA、Nasdaq、exchange data vendors | 空头回补、拥挤风险 | 双月度滞后，不能用 daily short volume 替代 |
| Daily short sale volume | FINRA Reg SHO files/API | 短售活动观察 | 是成交量，不是持仓 |
| Insider transactions | SEC Form 3/4/5、sec-api、OpenBB、Quiver | 内部人买卖确认 | option exercise、10b5-1、卖出解释复杂 |
| 13F institutional holdings | SEC 13F datasets、WhaleWisdom、Quiver、OpenBB | 机构关注、慢变量确认 | 45 天滞后，只含部分多头持仓 |
| Buyback data | 10-Q/10-K、8-K、company press release、FMP | 回购事件、资本配置 | 授权不等于执行 |
| M&A data | 新闻、8-K、S-4、proxy、InsideArbitrage、Dealogic 类数据 | 并购套利 | 交易条款复杂，法律/监管风险 |
| Biotech catalyst | FDA calendar、company IR、ClinicalTrials.gov、BPIQ 类产品 | FDA/临床事件 | 二元风险，专业解释要求高 |
| IPO / lock-up | S-1、424B、company prospectus、IPO calendar vendors | 解禁、供给冲击 | 条款可能分批或有豁免 |
| ETF / sector flows | ETF.com、fund flow vendors、Koyfin、OpenBB | 板块轮动、regime | 数据滞后和口径差异 |
| Macro / rates / volatility | FRED、Treasury、CBOE、Quandl/Nasdaq Data Link | regime、风险加权 | 宏观解释不能替代个股证据 |
| Alternative data | Quiver、RavenPack、Similarweb、App data、web traffic vendors | 产业链、attention、需求验证 | 授权和噪声是关键问题 |

### 29.4 竞品能力映射

| 产品 / 平台 | 值得借鉴 | 我们应该避免 |
|---|---|---|
| Benzinga Pro | 快速新闻、movers、signals、scanner、alerts、unusual options | 只做快讯会退化成资讯工具 |
| TrendSpider | 扫描器、策略测试、无代码条件、技术提醒 | 技术指标堆叠，缺少财务传导 |
| Koyfin | 财务、估值、分析师预期、新闻、filings、transcripts 一体化 | 更偏分析终端，不主动形成机会卡 |
| Quiver Quant | 国会交易、13F、内部人、替代数据、策略回测 | 替代数据容易被用户误读成确定性信号 |
| OpenBB | 开源数据整合、Python/API/Workspace | 数据入口强，但机会排序需要自己做 |
| TradingAgents / ai-hedge-fund | 多角色投研和风控讨论 | 讨论不能替代确定性信号和复盘 |
| Unusual Whales / options-flow 产品 | 期权流、暗池、异动提醒 | options flow 不能单独等于方向判断 |

---

## 30. 开源项目可借鉴点

### 30.1 pead-tool

可借鉴：

- PEAD 事件研究；
- AR / CAR；
- market model；
- 财报 surprise；
- 新闻和情绪；
- 五柱 composite score；
- 置信度和数据覆盖；
- API + UI + agent coordinator。

不要照搬：

- BUY/HOLD 这种容易被理解为荐股的标签；
- 印度 NSE 数据假设；
- 简化 sentiment 直接作为投资判断。

### 30.2 RyanJHamby/stock-screener

可借鉴：

- Phase 2 趋势；
- Minervini 8 条；
- 相对强度；
- 基本面增长；
- 风险收益比；
- 止损位；
- 市场 regime；
- 自动日扫。

特别适合做“成长领导股机会卡”。

### 30.3 growth-stock-screener

可借鉴：

- RS percentile；
- 流动性漏斗；
- 均线趋势漏斗；
- SEC XBRL 收入增长；
- 机构持仓变化；
- 简洁可解释。

它说明主推送层不需要复杂模型，简单但严格的漏斗可能更有产品价值。

### 30.4 AgentQuant

可借鉴：

- regime card；
- LLM 参数必须受 grid 约束；
- backtest tournament；
- Sharpe / Calmar / Sortino / drawdown；
- bootstrap Sharpe p5；
- 反思和重试；
- SQLite 记忆。

它更适合作为“策略研究工作台”参考，而不是直接做用户推送。

### 30.5 options_portfolio_backtester

可借鉴：

- 期权不能只看方向；
- 要有 contract-level inventory；
- 要考虑 Greeks；
- 要有 bid/ask、fill model、commission、slippage；
- 要有风险约束。

这说明期权流必须谨慎产品化。

### 30.6 TradingAgents / ai-hedge-fund

可借鉴：

- 多角色分析；
- 基本面、情绪、新闻、技术、风控分工；
- 正反方辩论；
- 风险官审查。

不要照搬：

- 多 agent 讨论本身不能替代策略验证；
- 如果没有确定性数据和回测，很容易变成“看起来很专业的聊天”。

---

## 31. 失败分类

机会卡失败后必须归因，否则系统不会进步。

建议失败类型：

| failure_type | 含义 |
|---|---|
| already_priced_in | 利好已提前反应 |
| weak_transmission | 新闻没有传导到财务 |
| bad_market_regime | 大盘环境变差 |
| false_breakout | 技术突破失败 |
| earnings_quality_issue | 财报质量不高 |
| revision_not_confirmed | 分析师没有上修 |
| liquidity_issue | 流动性差 |
| options_flow_misread | 期权流误读 |
| overextension | 短线过热 |
| thesis_invalidated | 原假设被新信息证伪 |

每次失败都要进入策略健康面板。

---

## 32. 策略健康面板

面向内部和高级用户，展示：

```text
策略名称
最近 30/90/180 天信号数
1/5/10/20 日胜率
中位超额收益
最大回撤
按行业表现
按市值表现
按市场 regime 表现
失败原因分布
当前是否降权
```

策略如果连续失效，系统应该自动降权，而不是继续推送。

---

## 33. 最终建议

### 33.1 战略判断

我们必须调研策略。

没有策略库，这个 agent 会退化成：

- 新闻摘要；
- 指标堆叠；
- 聊天式投研；
- 无法复盘的看涨看跌。

有策略库，它才会变成：

- 可解释机会发现；
- 可验证研究框架；
- 可持续改进的信号系统；
- 用户能理解和复盘的投资助手。

### 33.2 产品方向

产品定位：

```text
策略驱动的股票机会雷达
```

不是：

```text
新闻 agent
```

不是：

```text
自动荐股机器人
```

### 33.3 策略组合原则

主推送层策略组合建议：

1. PEAD 财报超预期后漂移；
2. 成长股强势趋势；
3. 事件催化 + 财务传导；
4. 分析师预期上修；
5. 健康回调 / 突破确认。

期权流做辅助确认。

产业链扩散、空头回补、内部人交易、13F、回购、并购、FDA、IPO 解禁、特殊情形、板块轮动和多因子约束都已纳入完整研究范围。它们在产品里按风险等级分别进入主推送、辅助确认、风险提示或专业事件卡，而不是留到未来再研究。

均值回归和复杂期权策略可以保留在策略库中，但默认不作为普通用户的主动推送。

### 33.4 一句话产品原则

> 每一条推送都必须是一张可验证机会卡，而不是一条消息、一个指标、一个情绪判断或一句看涨。

---

## 34. 主要参考来源

### 学术和策略研究

- Quantpedia, Post-Earnings Announcement Effect: https://quantpedia.com/strategies/post-earnings-announcement-effect/
- AQR, Time Series Momentum: https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum
- AQR Data Sets: https://www.aqr.com/Insights/Datasets
- Kenneth French Data Library: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
- Pan and Poteshman, The Information in Option Volume for Future Stock Prices: https://www.mit.edu/~junpan/5919.pdf
- RavenPack, Attention Conditions Stock Market Reaction to News Sentiment: https://www.ravenpack.com/research/stock-market-reaction-to-news-sentiment/
- Federal Reserve, News versus Sentiment: Predicting Stock Returns from News Stories: https://www.federalreserve.gov/econresdata/feds/2016/files/2016048pap.pdf
- PLOS One, How does news affect biopharma stock prices?: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0296927
- Quantpedia, Short Interest Effect: https://quantpedia.com/strategies/short-interest-effect-long-only-version
- Quantpedia, Value and Momentum Factors Across Asset Classes: https://quantpedia.com/strategies/value-and-momentum-factors-across-asset-classes

### 数据和回测基础设施

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Form 13F data sets: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
- SEC Form 13F FAQ: https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f
- SEC Forms 3, 4, 5 overview: https://www.sec.gov/files/forms-3-4-5.pdf
- FINRA Short Sale Volume Data: https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data
- FINRA Equity Short Interest Data: https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data
- Nasdaq Short Interest: https://www.nasdaqtrader.com/Trader.aspx?id=ShortInterest
- Finnhub API documentation: https://finnhub.io/docs/api
- Finnhub Earnings Calendar API: https://finnhub.io/docs/api/earnings-calendar
- Financial Modeling Prep developer docs: https://site.financialmodelingprep.com/developer/docs
- Benzinga APIs: https://www.benzinga.com/apis/
- Benzinga Analyst Ratings API: https://www.benzinga.com/apis/cloud-product/analyst-ratings-api/
- Massive/Polygon stocks docs: https://massive.com/docs/rest/stocks/overview
- Backtesting.py: https://kernc.github.io/backtesting.py/
- VectorBT: https://vectorbt.dev/
- QuantConnect LEAN: https://www.quantconnect.com/docs/v2/lean-engine/getting-started
- OpenBB: https://github.com/OpenBB-finance/OpenBB

### 产品和竞品

- Benzinga Pro: https://www.benzinga.com/pro/
- Benzinga Pro Signals: https://www.benzinga.com/pro/feature/signals
- TrendSpider: https://trendspider.com/
- TrendSpider Strategy Tester: https://help.trendspider.com/kb/strategy-tester/understanding-strategy-tester-from-trendspider
- Koyfin stocks coverage: https://www.koyfin.com/data-coverage/stocks/
- Quiver Quantitative: https://www.quiverquant.com/
- Quiver Congress Trading: https://www.quiverquant.com/congresstrading/

### 开源项目

- PEAD Tool: https://github.com/Yash-Bhanushali-21/pead-tool
- RyanJHamby Stock Screener: https://github.com/RyanJHamby/stock-screener
- Growth Stock Screener: https://github.com/starboi-63/growth-stock-screener
- AgentQuant: https://github.com/OnePunchMonk/AgentQuant
- Options Portfolio Backtester: https://github.com/lambdaclass/options_portfolio_backtester
- TradingAgents: https://github.com/tauricresearch/tradingagents
- AI Hedge Fund: https://github.com/virattt/ai-hedge-fund
- Awesome Systematic Trading: https://github.com/paperswithbacktest/awesome-systematic-trading
- Awesome Quant: https://github.com/wilsonfreitas/awesome-quant
- Awesome Quant AI: https://github.com/leoncuhk/awesome-quant-ai
