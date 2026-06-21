# 用户视角下的好量化工具：深度调研与原则

调研日期：2026-06-19  
目标：基于“机会、证据、风险、剧本、复盘”这组原则，继续充分调研什么样的量化/股票机会工具对用户真正有用。  
状态：调研与产品原则沉淀，不进入实现方案。

## 核心判断

站在用户角度，一个好的量化工具不是“更多数据 + 更复杂模型”，而是：

> 在信息噪音里筛出少数值得看的机会，解释为什么可能动，说明市场是否已经反应，给出买入后的情景和失效条件，并持续复盘自己的判断质量。

用户真正购买的不是“量化”，而是：

- 更快发现机会
- 更少被噪音误导
- 更清楚知道为什么
- 更冷静知道什么时候错了
- 更能复盘这个工具到底准不准

## 用户任务地图

一个普通用户每天使用这类工具，真实路径通常不是“我要跑一个因子回测”，而是：

```text
今天有什么值得看？
  -> 为什么是它？
  -> 利好来自哪里？
  -> 这个利好能不能传导到公司？
  -> 市场是不是已经涨完？
  -> 如果现在买，会发生哪些情况？
  -> 什么情况说明我错了？
  -> 这个工具以前类似判断准不准？
```

所以工具能力应该按用户任务组织，而不是按技术模块组织。

## 好工具的 9 个标准

### 1. 机会要少而准

差工具：

- 推 100 条新闻
- 列 50 个异动股
- 给 20 个“AI 推荐”
- 用户看完仍不知道该看哪几个

好工具：

- 每天只推少数高优先级机会
- 明确机会类型和时间窗口
- 给出为什么不是普通噪音
- 可以解释为什么没推某些热门股

调研对应：

- Benzinga Pro 强调 movers、signals、scanner、alerts，核心是从实时市场中筛出需要关注的东西。
- `daily_stock_analysis` 把输出包装成“决策仪表盘”，而不是简单新闻列表。
- `raymond` 的 live score engine 维护 top watchlist，并使用半衰期让旧信号衰减。

用户价值：

> 用户不缺信息，缺的是可信的压缩。

### 2. 必须解释“为什么可能涨”

只说“AI 看涨”没有价值。  
好的工具要能讲出链条：

```text
催化剂
  -> 谁受益
  -> 受益进入哪个财务项
  -> 市场原本预期是什么
  -> 现在是否产生预期差
  -> 股价/成交量是否确认
```

调研对应：

- Danelfin/Kavout/TipRanks/Seeking Alpha 这类产品都把“分数”包装成多维度评分，而不是纯结论。
- `raymond` 的 catalyst scorer 明确要求 catalyst 是具体近期新闻、SEC/EDGAR filing 或公告，不允许把技术条件/预设条件伪装成催化剂。
- `go-stock` 通过市场资讯、个股新闻、情绪词典、资金/财务工具来给 AI 提供上下文。

用户价值：

> 解释链条越清楚，用户越能判断“这是机会，还是故事”。

### 3. 判断市场是否已经反应

很多利好是真的，但已经涨完。  
好工具必须回答：

- 消息前股价有没有提前涨？
- 过去 1/5/20/60 日表现如何？
- 是否已经高开/放量/突破？
- 是否短线过热？
- 是否出现 FOMO？
- 当前是 early、mid，还是 late？

调研对应：

- `raymond` 系统 prompt 里明确惩罚过去 30 天已涨太多的股票：涨幅超过一定阈值时不能给高 conviction。
- `gf-dma-health-index` 的思路也适合这里：20/50/100/200 日均线、离均线距离、FOMO 逃逸风险。
- Trade Ideas/Holly 这类工具把实时信号和 entry/stops/targets 绑定，说明短线机会必须考虑反应速度。

用户价值：

> 好消息不等于好买点。工具要帮用户识别“还没反应”还是“已经拥挤”。

### 4. 买入后必须有剧本

用户最常见的问题不是“买不买”，而是买了以后不知道看什么。  
好工具应该给出：

```text
乐观情景：
  哪些事实继续出现，逻辑会增强。

中性情景：
  消息是真的，但股价不动或震荡，为什么。

悲观情景：
  哪些信号出现，说明逻辑错了。

失效条件：
  价格、新闻、财务、成交量、时间窗口上的失效信号。
```

调研对应：

- `daily_stock_analysis` 的 `DecisionSignalCreateRequest` 字段非常接近理想结构：`entry_low`、`entry_high`、`stop_loss`、`target_price`、`invalidation`、`watch_conditions`、`risk_summary`、`catalyst_summary`、`evidence`、`data_quality_summary`、`plan_quality`、`status`。
- `raymond` 的 outcomes tracker 用 `MAX_HOLD_DAYS`、`STOP_LOSS_PCT`、`TAKE_PROFIT_PCT` 跟踪买入后结果。
- TradingView/Trade Ideas 这类交易工具都强调 alerts、entry、stop、target，而不是只给观点。

用户价值：

> 工具必须从“推荐”进化成“交易/观察计划”。

### 5. 要支持不同用户风格

同一个信号，对不同用户含义不同：

| 用户类型 | 关注点 | 工具应该输出 |
| --- | --- | --- |
| 日内/短线 | 盘中异动、新闻速度、成交量、期权流 | 快速 alert、为什么动、风险位 |
| 波段 | 2-6 周趋势、回调、均线结构、预期上修 | 趋势健康度、观察位、失效条件 |
| 主题轮动 | 热点扩散、行业强弱、受益链条 | 主题地图、一阶受益、扩散状态 |
| 成长股 | TAM、业绩上修、估值是否透支 | 增长假设、估值隐含、情景推演 |
| 持仓用户 | 这只还能不能拿，跌了怎么办 | 持仓诊断、减仓/观察/失效信号 |

调研对应：

- Tickeron 把用户分为 beginner、DIY、copy trader，说明同一工具要有不同使用层级。
- `daily_stock_analysis` 支持问股策略：均线、缠论、波浪、趋势、热点、事件、成长、预期等。
- `WyckoffTradingAgent` 的 prompt templates 分成 daily、holding-risk、step3-audit、feishu-summary 等工作流。

用户价值：

> 好工具不是只有一个“AI 推荐”，而是知道用户现在处于哪种决策场景。

### 6. 信任来自证据，不来自分数

黑盒分数很容易让人不信。  
一个可信的机会卡片应该包含：

- 数据来源
- 新闻来源
- 时间戳
- 催化剂类型
- 股价反应
- 成交量/相对强弱
- 分项评分
- 风险扣分
- 失效条件
- 数据质量说明

调研对应：

- Seeking Alpha 的 Quant Ratings 用 factor grades 解释评分，而不是只显示最终结论。
- TipRanks Smart Score 也是多因子聚合思路。
- `xang1234/stock-screener` 的 explain payload builder 会返回 passed checks、failed checks、key levels、invalidation flags、derived_ready。
- `daily_stock_analysis` 在 signal schema 中单独保存 `evidence`、`data_quality_summary`、`plan_quality`。

用户价值：

> 分数可以排序，证据才建立信任。

### 7. 风险应该有否决权

差工具会把风险写在最后。  
好工具应该让风险能降级或 veto 买入信号。

调研对应：

- `daily_stock_analysis` 的 RiskAgent 明确检查减持、业绩预警、监管处罚、行业政策、解禁、估值极端、技术破位，并支持 `veto_buy`。
- 其 DecisionAgent 也写明高风险应下调整体信号，高严重风险 cap signal。
- `WyckoffTradingAgent` 有 compliance report，公开输出会去标的化并禁止直接“买入/卖出/目标价”等词，说明其把风险边界纳入产品流程。

用户价值：

> 好工具不是只会找机会，也要敢说“这个不要碰”。

### 8. 要持续复盘自己

这是决定工具长期价值的核心。  
好工具应该记录：

- 每次推送时的价格
- 1d/3d/5d/10d/20d 后表现
- 是否跑赢指数
- 最大回撤
- 是否触发失效条件
- 命中/错失/中性
- 哪类信号有效
- 哪类催化剂经常误报
- 用户是否觉得有用

调研对应：

- `daily_stock_analysis` 的 `DecisionSignalOutcomeItem` 包含 `hit/miss/neutral`、方向是否正确、起止价格、最大高点、最低点、收益率、holding_state。
- `DecisionSignalOutcomeStatsResponse` 支持 hit rate、avg return、unable reasons、breakdowns。
- `WyckoffTradingAgent` 有 signal_feedback、strategy_reflection、shadow score、review/promotion 流程。
- `raymond` 有 outcomes tracker 和 journal。

用户价值：

> 工具必须能证明自己在进步，也必须能暴露自己在哪些场景不行。

### 9. 推送不能制造焦虑

金融 alert 很容易造成“通知疲劳”和追涨冲动。  
好工具的推送应该有：

- 优先级
- 冷却时间
- 去重
- 严重程度
- 用户可配置
- 触发原因
- 下一步动作
- 是否需要立即看

调研对应：

- `daily_stock_analysis` 的 alerts 设计有 `alert_rule`、`alert_trigger`、`alert_notification`、`alert_cooldown`，支持 severity、cooldown_policy、notification_policy、trigger history。
- `xang1234/stock-screener` 的 alert evaluator 有 hysteresis：同级别 breach 不重复触发，恢复后关闭，严重程度可升级。
- Benzinga/Trade Ideas/Unusual Whales 等实时产品都强调 alerts，但真正好的 alert 不是越多越好，而是能降低用户错过关键事件的概率。

用户价值：

> 好推送不是“多”，而是“该来的时候来，不该烦的时候闭嘴”。

## 产品类型对比

| 类型 | 用户价值 | 代表 | 强项 | 弱项 |
| --- | --- | --- | --- | --- |
| 新闻异动平台 | 快速知道发生什么 | Benzinga Pro, StockTitan | 快、覆盖广 | 容易信息过载 |
| AI Score 工具 | 快速排序候选 | Danelfin, Kavout, TipRanks | 简单、可排名 | 黑盒、解释不足 |
| 技术扫描器 | 找突破/回调/形态 | Trade Ideas, TrendSpider, TradingView | 可执行性强 | 容易忽略基本面催化 |
| 期权流工具 | 发现资金异动 | Unusual Whales, Cheddar Flow, Tradytics | 可能早于新闻 | 噪音大、解释难 |
| AI 股票分析系统 | 汇总资讯并推送报告 | daily_stock_analysis, go-stock | 贴近个人用户 | 数据/评分质量需审计 |
| 专业量化平台 | 因子/模型/回测 | Qlib, Lean, vectorbt | 严谨、可复现 | 对普通用户不直观 |

结论：

> 用户想要的好工具，不应只属于其中一类，而应把“机会发现 + 解释 + 风险 + 复盘”组合起来。

## 开源项目中的关键模式

### daily_stock_analysis

最值得学习的部分不是“AI 分析”，而是它已经有比较完整的信号对象。

关键字段：

- `source_type`
- `trigger_source`
- `action`
- `confidence`
- `score`
- `horizon`
- `entry_low` / `entry_high`
- `stop_loss`
- `target_price`
- `invalidation`
- `watch_conditions`
- `reason`
- `risk_summary`
- `catalyst_summary`
- `evidence`
- `data_quality_summary`
- `plan_quality`
- `status`
- `outcome`
- `feedback`

这说明好工具应该把“观点”变成可跟踪的 structured signal，而不是只生成 Markdown。

### WyckoffTradingAgent

关键模式：

- candidate shadow score
- signal observation
- signal feedback
- strategy reflection
- policy candidate
- compliance-safe report

最值得学习的是 shadow 模式：

> 新评分先观察，不直接影响生产选择；只有 outcome 证明有用后，才进入 review/promotion。

这比“LLM 觉得有效就上线”成熟得多。

### xang1234/stock-screener

关键模式：

- 多 screener composite score
- rating threshold
- pass-rate downgrade
- setup explain payload
- market telemetry alert hysteresis
- theme discovery pipeline

最值得学习的是 explain payload：

> 不只输出 ready/not ready，而是输出 passed checks、failed checks、key levels、invalidation flags。

### raymond

关键模式：

- preconditions + catalysts
- catalyst 必须来自真实新闻/EDGAR
- 已涨太多会被惩罚
- score decay
- watchlist 不是永久固定
- outcomes tracker 跟踪持仓结果

最值得学习的是对“追涨”的惩罚：

> 好工具不能只看利好，还要看这个利好是否已经被价格充分反应。

### go-stock

关键模式：

- A/H/美股支持
- 本地数据保存
- 热点资讯
- 情绪分析
- AI 推荐历史记录
- 涨跌报警推送
- 自然语言选股工具
- MCP/agent tool 接入

最值得学习的是中文用户场景覆盖：

> 市场资讯、题材、资金、财务、情绪、预警推送都要接近用户日常说法。

## 好机会卡片应该长什么样

一个用户可用的机会卡片应包含：

```text
股票：XXX
机会类型：财报超预期 / 分析师上修 / 产业链催化 / 资金异动 / 技术突破
时间窗口：1-5 天 / 2-6 周 / 3 个月

核心判断：
一句话说明为什么值得看。

为什么可能涨：
1. 催化剂是什么
2. 谁受益、怎么传导
3. 市场是否还没充分反应

市场反应：
- 今日/盘前涨跌
- 5 日/20 日涨跌
- 成交量/相对成交量
- 离关键均线距离
- 是否过热

证据：
- 新闻/公告/财报/分析师来源
- 数据时间戳
- 价格数据来源

如果买了：
- 乐观情景
- 中性情景
- 悲观情景
- 失效信号

风险：
- 估值
- 流动性
- 财报窗口
- 竞争/政策/监管
- FOMO/拥挤

后续跟踪：
- 下一次检查时间
- 需要观察的新闻/价格/成交量条件
- 信号过期时间

工具自评：
- 类似信号过去命中率
- 平均最大回撤
- 样本量
```

## 好工具的底层对象

从调研看，应该把机会抽象成一个结构化对象：

| 字段 | 作用 |
| --- | --- |
| `ticker` | 标的 |
| `market` | 市场 |
| `opportunity_type` | 机会类型 |
| `horizon` | 时间窗口 |
| `trigger_source` | 触发来源 |
| `catalyst_summary` | 催化剂摘要 |
| `beneficiary_chain` | 受益链条 |
| `financial_transmission` | 财务传导 |
| `market_reaction` | 市场是否反应 |
| `technical_state` | 技术状态 |
| `score` | 总分 |
| `score_breakdown` | 分项评分 |
| `confidence` | 置信度 |
| `entry_zone` | 观察/入场区域 |
| `invalidation` | 失效条件 |
| `watch_conditions` | 买后观察条件 |
| `risk_summary` | 风险 |
| `evidence` | 证据 |
| `data_quality` | 数据质量 |
| `status` | active / expired / invalidated / closed |
| `outcomes` | 复盘结果 |

## 好工具的评分不应该只有一个数字

更合理的是分项评分：

| 分项 | 问题 |
| --- | --- |
| 催化剂强度 | 消息是否具体、新鲜、重要？ |
| 财务传导 | 能否进入收入、利润、订单、估值？ |
| 预期差 | 市场是否还没充分定价？ |
| 市场确认 | 价格、成交量、相对强弱是否确认？ |
| 风险扣分 | 是否有减持、监管、估值、破位等风险？ |
| 拥挤扣分 | 是否已经涨太多，FOMO 过热？ |
| 数据质量 | 来源是否可靠，时间是否新？ |
| 历史相似度 | 类似信号过去表现如何？ |

总分只用于排序，用户决策要看分项。

## 反模式

调研后可以明确避免这些：

1. 只给“买入/卖出”  
   用户不知道为什么，也不知道什么时候错。

2. 黑盒 AI 分数  
   没有证据、分项、时间窗，容易失去信任。

3. 新闻堆砌  
   用户要机会，不是新闻列表。

4. 没有市场反应判断  
   利好可能已经涨完。

5. 没有复盘  
   永远不知道工具有没有用。

6. 没有风险否决  
   只会乐观解释，会把用户带向高风险。

7. 推送过多  
   最终用户会关闭通知。

8. 把期权流当确定方向  
   期权大单可能是对冲、价差或平仓。

9. 把 AI 当交易执行者  
   对普通用户和合规边界都危险。

## 评价一个好工具的指标

不应该只看收益率。更完整的指标：

| 指标 | 为什么重要 |
| --- | --- |
| 推送数量 | 判断是否噪音过多 |
| 用户打开率 | 机会是否被认为值得看 |
| 保存/加入 watchlist 比例 | 是否真正进入用户决策 |
| 1d/5d/20d 命中率 | 不同时间窗有效性 |
| 平均最大回撤 | 用户体验中的痛感 |
| 跑赢 benchmark 比例 | 是否只是市场 beta |
| 失效条件触发率 | 风险剧本是否有效 |
| 数据缺失率 | 数据可靠性 |
| 解释满意度 | 用户是否理解为什么 |
| 误报分类 | 哪类信号最容易错 |

## 用户体验上的关键细节

### 首页

不应该是新闻 feed。  
应该是：

- 今日高优先级机会
- 过热/风险警报
- 持仓需要关注
- 主题扩散
- 已推送信号复盘

### 单股页

应该回答：

- 最近为什么动？
- 当前属于什么机会/风险状态？
- 过去推送过什么？
- 现在是 early/mid/late？
- 买后看什么？

### 推送

应该分等级：

- `Critical`: 持仓风险/失效条件触发
- `High`: 强催化 + 未充分反应
- `Medium`: 值得观察
- `Low`: 信息记录，不打扰

### 复盘页

应该能看：

- 本周推送了多少
- 哪些命中
- 哪些错
- 哪类信号最有效
- 哪些应该降权

## 风险与合规边界

调研中反复看到监管提醒：AI 投资诈骗、AI trading bot、保证收益、无风险高收益都是高风险表述。

好工具应遵守：

- 不承诺一定上涨
- 不承诺收益
- 不替用户自动下单
- 不隐藏风险
- 不把历史结果当未来保证
- 明确数据来源和不确定性
- 如果有模拟/回测，必须说明假设和限制

这不是让工具没用，而是让工具可持续。

## 当前更值得继续深挖的方向

如果继续调研，不应泛泛搜索，而应做模块拆解：

1. `daily_stock_analysis`  
   深挖 decision signal 生成、outcome 统计、alert worker、推送模板。

2. `go-stock`  
   深挖 AI 推荐股票、情绪分析、财联社/市场资讯、涨跌报警、本地数据结构。

3. `WyckoffTradingAgent`  
   深挖 shadow score、signal feedback、strategy reflection、compliance report。

4. `xang1234/stock-screener`  
   深挖 composite scoring、theme discovery、market breadth、alert hysteresis。

5. 商业产品  
   对比 Benzinga、Danelfin、Tickeron、Trade Ideas、TrendSpider、Unusual Whales 的用户工作流，而不是只看功能列表。

## Sources

- [Benzinga Pro](https://www.benzinga.com/pro/)
- [Benzinga APIs](https://www.benzinga.com/apis/)
- [Danelfin](https://danelfin.com/)
- [Tickeron](https://tickeron.com/)
- [Kavout K Score](https://www.kavout.com/k-score/)
- [Seeking Alpha Quant Ratings FAQ](https://help.seekingalpha.com/premium/quant-ratings-and-factor-grades-faq)
- [TipRanks Smart Score](https://www.tipranks.com/smart-score)
- [Zacks Rank](https://www.zacks.com/zacks-rank)
- [Trade Ideas Holly](https://www.trade-ideas.com/hollyguide/Who_is_Holly.html)
- [TrendSpider AI Strategy Lab](https://trendspider.com/product/artificial-intelligence-ai-trading-strategy-lab/)
- [Unusual Whales](https://unusualwhales.com/)
- [Unusual Whales Flow Alerts](https://unusualwhales.com/option-flow-alerts)
- [Cheddar Flow](https://www.cheddarflow.com/)
- [Tradytics](https://tradytics.com/)
- [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)
- [go-stock](https://github.com/ArvinLovegood/go-stock)
- [WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent)
- [stock-screener](https://github.com/xang1234/stock-screener)
- [raymond](https://github.com/smanderson721/raymond)
- [stock-gapper-discord-bot](https://github.com/vitran75/stock-gapper-discord-bot)
- [SEC Investor Alerts](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins)
- [FINRA AI and Investment Fraud](https://www.finra.org/investors/insights/artificial-intelligence-and-investment-fraud)
- [SEC/NASAA/FINRA AI Investment Fraud Alert](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/artificial-intelligence-fraud)
- [CFTC AI trading bot advisory](https://www.cftc.gov/PressRoom/PressReleases/8854-24)
