# 量化 Agent 代码级调研：从消息推送到机会操作系统

调研日期：2026-06-19  
目标：继续充分调研“什么样的量化工具站在用户角度才有用”，重点查看现有产品和开源项目如何处理推荐、评分、新闻、告警、解释、风控和复盘。  
状态：调研备忘录，不进入实现方案收敛。

## 核心结论

用户说“帮我推送最近哪些股票会涨、什么消息利好什么、如果买了会怎样”，表面上是要一个荐股 agent，但真正需求不是“更多新闻”和“更多结论”，而是一个能把市场噪音压缩成少数可验证机会的系统。

更准确的产品定义是：

> 股票机会操作系统：每天筛出少数值得看的机会，解释为什么可能动，判断市场是否已经反应，给出观察/入场/失效剧本，并持续复盘工具自己的判断质量。

这一轮源码调研后，我对“好工具”的判断更明确：

- 不能只做消息流，要做“机会卡”。
- 不能只给分数，要给证据链和失败条件。
- 不能只推今日热点，要记录事后结果。
- 不能只看上涨概率，要识别过热、拥挤、数据质量和风险否决。
- 不能只靠 LLM 判断，要把关键环节结构化。

## 本轮直接查看的项目和来源

开源项目：

- `ZhuLinsen/daily_stock_analysis`：LLM 股票分析系统，包含决策信号、告警、Outcome 追踪、多 agent 协作。
  - Repo: https://github.com/ZhuLinsen/daily_stock_analysis
  - Decision signal schema: https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/api/v1/schemas/decision_signals.py
  - Decision agent: https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/src/agent/agents/decision_agent.py
  - Alert worker: https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/src/services/alert_worker.py

- `ArvinLovegood/go-stock`：AI 赋能股票分析/选股工具，覆盖 A 股、港股、美股，支持新闻、情绪、资金、财务、推荐、提醒、本地数据。
  - Repo: https://github.com/ArvinLovegood/go-stock
  - AI 推荐记录: https://github.com/ArvinLovegood/go-stock/blob/dev/backend/data/ai_recommend_stocks_api.go
  - 数据模型: https://github.com/ArvinLovegood/go-stock/blob/dev/backend/models/models.go
  - 市场新闻: https://github.com/ArvinLovegood/go-stock/blob/dev/backend/data/market_news_api.go
  - 情绪分析: https://github.com/ArvinLovegood/go-stock/blob/dev/backend/data/stock_sentiment_analysis.go
  - 定时任务: https://github.com/ArvinLovegood/go-stock/blob/dev/backend/agent/cron_task_api.go
  - 工具注册: https://github.com/ArvinLovegood/go-stock/blob/dev/backend/data/tool_agent_extra.go

- `YoungCan-Wang/WyckoffTradingAgent`：Wyckoff 交易 agent 和 AI stock screener，强调量价结构、候选评分、信号确认、反馈和反思。
  - Repo: https://github.com/YoungCan-Wang/WyckoffTradingAgent
  - 候选评分: https://github.com/YoungCan-Wang/WyckoffTradingAgent/blob/main/core/candidate_selection_score.py
  - 信号确认: https://github.com/YoungCan-Wang/WyckoffTradingAgent/blob/main/core/signal_confirmation.py
  - 信号生命周期: https://github.com/YoungCan-Wang/WyckoffTradingAgent/blob/main/core/signal_lifecycle.py
  - 候选策略 guardrails: https://github.com/YoungCan-Wang/WyckoffTradingAgent/blob/main/core/candidate_policy.py
  - 信号反馈: https://github.com/YoungCan-Wang/WyckoffTradingAgent/blob/main/core/signal_feedback.py
  - 策略反思: https://github.com/YoungCan-Wang/WyckoffTradingAgent/blob/main/core/strategy_reflection.py

- `xang1234/stock-screener`：多条件股票筛选器，包含复合评分、评级降级、解释 payload、市场 telemetry 告警、主题发现。
  - Repo: https://github.com/xang1234/stock-screener
  - 评分策略: https://github.com/xang1234/stock-screener/blob/main/backend/app/domain/scanning/scoring.py
  - 解释构建: https://github.com/xang1234/stock-screener/blob/main/backend/app/analysis/patterns/explain_builder.py
  - 突破准备度: https://github.com/xang1234/stock-screener/blob/main/backend/app/analysis/patterns/readiness.py
  - 单股解释 use case: https://github.com/xang1234/stock-screener/blob/main/backend/app/use_cases/scanning/explain_stock.py
  - 告警评估: https://github.com/xang1234/stock-screener/blob/main/backend/app/services/telemetry/alert_evaluator.py

- `vitran75/stock-gapper-discord-bot`：盘前 gapper 扫描和 Discord 推送工具。
  - Repo: https://github.com/vitran75/stock-gapper-discord-bot
  - 评分: https://github.com/vitran75/stock-gapper-discord-bot/blob/main/scanner/scoring.py
  - 主流程: https://github.com/vitran75/stock-gapper-discord-bot/blob/main/scanner/main.py
  - 去重状态: https://github.com/vitran75/stock-gapper-discord-bot/blob/main/scanner/state.py
  - 推送: https://github.com/vitran75/stock-gapper-discord-bot/blob/main/scanner/notifications.py

产品和数据源：

- Benzinga Pro: https://www.benzinga.com/pro/
- Trade Ideas AI Signals: https://www.trade-ideas.com/features/ai-signals/
- TrendSpider AI Strategy Lab: https://trendspider.com/product/artificial-intelligence-ai-trading-strategy-lab/
- TrendSpider Sidekick: https://trendspider.com/product/sidekick-ai-trading-assistant/
- Unusual Whales API / MCP: https://unusualwhales.com/public-api
- Danelfin: https://danelfin.com/
- Tickeron: https://tickeron.com/
- Kavout K Score: https://www.kavout.com/k-score/

监管和风险边界：

- FINRA AI investment fraud: https://www.finra.org/investors/insights/artificial-intelligence-ai-investment-fraud
- CFTC AI trading bots advisory: https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/CustomerAdvisory_AITradingBots.html
- SEC investor alerts: https://www.sec.gov/oiea/investor-alerts-and-bulletins

## 一、为什么不能做“消息性 agent”

消息 agent 的问题是：它把信息搬给用户，但没有替用户完成决策压缩。

用户真正问的是：

```text
这条消息为什么重要？
它利好哪个环节？
会传导到哪家公司的订单、收入、毛利、利润或估值？
这家公司是否有足够弹性？
市场是不是已经反应？
如果现在介入，接下来要看什么？
什么情况说明这个逻辑错了？
这个系统过去类似判断准不准？
```

所以，一个好量化 agent 不是“实时新闻机器人”，而是“机会判断流水线”。新闻只是原材料。

源码调研也印证了这一点：

- `go-stock` 有新闻、情绪、热点、资金、财务和 AI 工具面，但如果没有强复盘，容易停留在资讯增强。
- `daily_stock_analysis` 的价值更高，因为它把观点落成 `DecisionSignal`，再追踪 outcome。
- `WyckoffTradingAgent` 不急着把信号变成操作，而是先放入 pending 池等待确认。
- `xang1234/stock-screener` 把“为什么入选”拆成 passed checks / failed checks / key levels / invalidation flags。
- `stock-gapper-discord-bot` 虽然简单，但至少把 gap、成交量、美元成交额、催化剂分开计分，并做日内去重。

这说明：消息不是产品，结构化机会才是产品。

## 二、最值得借鉴的 6 个底层机制

### 1. 决策信号必须结构化

`daily_stock_analysis` 的 `DecisionSignalCreateRequest` 是本轮最接近理想形态的结构。

它不是只保存一段 AI 文本，而是保存：

- 股票代码、市场、来源类型、来源 agent、trace id
- action、confidence、score、horizon
- entry_low、entry_high、stop_loss、target_price
- invalidation、watch_conditions
- reason、risk_summary、catalyst_summary、evidence
- data_quality_summary、plan_quality、status、expires_at

这个设计对用户很关键。

用户不是只需要“看涨”，而是需要：

```text
为什么看涨
什么时候有效
哪个价格区间更合理
错了在哪里止损或失效
接下来观察哪些事实
这个判断质量高不高
```

我的判断：

> 如果一个机会不能结构化成 entry / invalidation / watch conditions / evidence / risk / horizon，它就不应该被当成正式推送。

这条原则可以直接决定产品质量。

### 2. Outcome 追踪是信任核心

`daily_stock_analysis` 的 outcome 设计比大多数 AI 荐股产品更可信。

它会记录：

- horizon
- hit / miss / neutral
- direction_correct
- start_price
- end_close
- max_high
- min_low
- stock_return_pct
- holding_state
- unable_reason
- market_phase
- source_agent
- plan_quality
- data_quality_level

这解决了用户长期最关心的问题：

```text
这个工具到底准不准？
是哪些信号准？
哪些 agent 经常误判？
短线准还是波段准？
数据缺失时是否误报更多？
```

很多 AI 股票产品会展示“今日推荐”，但不展示失败样本。对用户来说，这不够可信。

好的工具应该默认回答：

- 过去 30 天推过多少次？
- 1d / 3d / 5d / 10d 命中率分别是多少？
- 平均收益和最大回撤是多少？
- 哪些推荐无法评估，为什么？
- 哪些信号源贡献最大？
- 哪些催化剂经常失效？

这不是附加功能，而是产品信任的基础。

### 3. 信号应该有 pending / confirmed / expired 生命周期

`WyckoffTradingAgent` 的 `signal_confirmation.py` 很值得借鉴。

它不是发现信号就直接确认，而是把不同信号类型设置 TTL：

- SOS 类信号 2 天
- spring / LPS / compression 类信号 3 天
- 超过 TTL 没确认就 expired

确认逻辑也不是一句话，而是看：

- 是否跌破信号日低点或支撑
- 是否缩量确认
- 是否收回 MA20
- 是否异常放量
- 是否守住事件日低点
- 是否放量突破或放量下破

这对用户体验很重要。

很多用户不想每天收到一堆“可能涨”，他们想知道：

```text
这个机会是观察中，还是已经确认？
如果明天不确认，会不会自动失效？
如果已经过期，为什么过期？
```

这比单纯的 buy/sell 更符合真实交易过程。

机会可以分层：

| 状态 | 用户含义 |
| --- | --- |
| discovered | 发现线索，但证据不足 |
| watching | 值得观察，有关键确认条件 |
| confirmed | 条件满足，可以进入正式机会卡 |
| crowded | 逻辑成立，但已经过热 |
| invalidated | 触发失效条件 |
| expired | 时间窗口结束，未验证 |
| reviewed | 已完成事后复盘 |

这种生命周期比“今天 AI 推荐 10 只股票”更可信。

### 4. 风险必须能降级或否决

`daily_stock_analysis` 的 RiskAgent 和 DecisionAgent 都体现了这一点。

RiskAgent 关注：

- 减持
- 业绩预警
- 监管处罚
- 行业政策风险
- 解禁
- 估值极端
- 技术破位

DecisionAgent 的 prompt 明确要求高严重风险要下调整体信号，甚至 cap 到 hold。

`WyckoffTradingAgent` 的 `candidate_policy.py` 也有类似思想。它会根据市场 regime、触发类型、短期过热、纯趋势追涨等条件做 loss guard。

这对用户很重要，因为普通工具经常把风险写在最后，但真正有用的工具应该让风险参与决策。

好的结构应该是：

```text
机会分 = 正向信号 - 风险扣分
但某些风险不是扣分，而是否决条件
```

例子：

- 基本面利好，但财报造假调查：否决。
- 消息利好，但过去 5 天已经暴涨且放量衰竭：降级。
- 技术突破，但数据质量不足：降级。
- 催化剂强，但成交量没有确认：保持 watching。
- 催化剂强，期权流异常且量价确认：升级 confirmed。

风险否决权是工具从“营销型荐股”变成“研究型机会系统”的关键。

### 5. 评分必须可解释，并受数据质量约束

`xang1234/stock-screener` 的 `scoring.py` 很有工程参考价值。

它的复合评分是纯函数，支持：

- weighted_average
- maximum
- minimum

评级逻辑不是只看 composite score，还看通过条件数量：

- 分数高但没有 screener 通过，强制 PASS。
- 通过数量少于一半，评级降一级。
- 数据完整度太低，强制 PASS 或降级。

这非常重要。

许多 AI 产品的问题是“分数看起来很精密，但数据质量不透明”。一个好工具应该告诉用户：

```text
这个分数是不是基于完整数据？
有多少字段缺失？
哪些条件通过了？
哪些条件失败了？
为什么评分高但没有推荐？
```

`xang1234` 的 explain builder 也值得借鉴。它会输出：

- passed_checks
- failed_checks
- key_levels
- invalidation_flags
- derived_ready
- score_trace

这说明最终机会卡应该能展开看到“判定路径”，不是只显示一个总分。

### 6. 告警要有 hysteresis 和去重

推送工具最容易变成噪音。

`xang1234/stock-screener` 的 telemetry alert evaluator 有清晰的 hysteresis：

- 突破阈值且无 active alert，打开新告警。
- 同一严重程度的 active alert，不重复触发。
- 严重程度升级时更新。
- 指标恢复后关闭。
- acknowledged alert 不重复打扰，但恢复时关闭。

`stock-gapper-discord-bot` 也有简单去重：

- 每个交易日记录 `alerted_symbols`
- 第一次运行发送完整结果
- 后续只发送新命中的股票
- 没有新命中就静默

这对用户体验非常关键。

一个股票机会 agent 如果每 10 分钟都推同一个 ticker，用户很快会失去信任。

好的提醒策略应该是：

```text
首次发现：弱提醒
满足确认：强提醒
风险升级：强提醒
失效触发：强提醒
同级别重复：不提醒
数据恢复或机会结束：归档提醒或日报中汇总
```

## 三、逐项目深度观察

### A. daily_stock_analysis：最接近“机会闭环”的结构

这个项目的价值不是它用了 LLM，而是它把 LLM 输出转成结构化决策对象。

关键模块：

- TechnicalAgent：技术结构、均线、支撑阻力、止损、趋势评分。
- IntelAgent：新闻、公告、资金流、催化剂、情绪、风险线索。
- RiskAgent：风险 flags 和 veto_buy。
- PortfolioAgent：持仓、仓位、集中度、相关性、再平衡。
- DecisionAgent：综合上述意见，输出 Decision Dashboard。
- AlertWorker / AlertService：规则告警、去重、冷却、触发记录。
- DecisionSignal API：创建、列表、状态更新、结果评估、反馈、统计。

最值得借鉴的点：

1. 多 agent 不是为了炫技，而是为了职责隔离。

```text
技术面看结构
情报面看催化剂
风险面看否决
组合面看仓位
决策面只做综合
```

这种结构比单个大 prompt 更可控。

2. DecisionSignal 是核心产品对象。

真正应该被推送的不是新闻，也不是 LLM 文本，而是一个信号对象。

3. Outcome 追踪让系统能自我校准。

没有 outcome，推荐永远无法被证明有效。

4. plan_quality 和 data_quality_summary 是信任层。

用户需要知道不是每条建议都同等可信。

局限：

- 如果最终 dashboard 依赖 LLM 生成 JSON，需要严格校验和容错。
- 多 agent 权重需要通过 outcome 复盘不断校准。
- 如果数据源不稳定，必须把数据质量暴露给用户，而不是隐藏。

对用户产品的启发：

> 每张机会卡都应该可追踪、可评估、可反馈，而不是一次性文本。

### B. go-stock：数据和工具面很宽，但闭环还不够强

`go-stock` 的优势是覆盖面广，尤其适合 A 股/港股/美股混合分析。

它包含：

- 财联社电报抓取
- 新浪等新闻抓取
- 主题标签和股票标签
- 中文金融情绪词典
- AI 推荐股票表
- 定时任务
- 本地桌面提醒
- 个股新闻、异动、资金、财务、估值、龙虎榜、研报、日历、热点、涨停梯队等工具

`AiRecommendStocks` 模型包含：

- modelName
- rating
- stockCode / stockName
- bkCode / bkName
- 推荐时价格
- 当前价格
- 当前价格时间
- 推荐理由
- 建议最低/最高买入价
- 建议最低/最高止盈价
- 建议止损价
- 风险提示
- enableAlert

这说明它已经不是纯聊天工具，而是有推荐记录和提醒开关。

`tool_agent_extra.go` 暴露的工具面非常宽，包括：

- FilterStocks
- QueryStockNews
- GetStockChanges
- GetAIAnalysisHistory
- GetHotStockList
- GetHotEventList
- GetIndustryMoneyRank
- GetLongTigerList
- GetInvestCalendar
- SearchReport
- GetStockLatestFinance
- GetStockOrgPredict
- GetStockValuationPercentile
- ComparableCompanyAnalysis
- HotspotDiscovery
- GetUplimitLadder
- GetDailyChangeStats

这个项目对我们最大的启发是：

> 好的 agent 需要广泛工具面，否则 LLM 只能凭文本讲故事。

但它也暴露出一个问题：

> 工具多不等于用户价值高，关键是能不能把工具调用结果合成少数结构化机会，并持续复盘。

局限：

- 情绪分析主要基于词典，容易误判复杂语境。
- AI 推荐记录有止盈止损和风险提示，但我没有看到同等强度的 outcome 追踪。
- 定时任务中的 stock_monitor 当前更像参数解析和日志，实际策略闭环较弱。
- 本地提醒只是通知通道，不等于高质量 alert policy。

对用户产品的启发：

- 可以借鉴它的工具面和本地数据思路。
- 不能停留在“AI 分析历史”和“推荐列表”。
- 必须补上 outcome、告警去重、数据质量、信号生命周期。

### C. WyckoffTradingAgent：最值得借鉴“观察池”和“策略反思”

这个项目的强点不是通用资讯，而是量价结构和信号生命周期。

关键机制：

1. 候选评分与真实选择分离

`candidate_selection_score.py` 里的 shadow score 明确是观察用途，不应该在没有 outcome 证明前直接改变 live selection。

这很重要。

一个量化 agent 不能每次调权重就直接影响真实推送。应该先 shadow run：

```text
新规则先旁路打分
记录如果使用它会推什么
过一段时间评估 outcome
效果稳定后再人工审核晋级
```

2. pending 信号二次确认

`signal_confirmation.py` 把信号拆成 pending、confirmed、expired。

这非常适合用户场景：

- 不确定的机会进入观察池。
- 满足确认条件再提醒。
- 超时不确认自动过期。
- 失效原因可解释。

3. 信号生命周期评估

`signal_lifecycle.py` 会在 1/3/5/10 天后评估收益和最大回撤。

这和 `daily_stock_analysis` 的 outcome 思路一致。

4. Regime 和 loss guard

`candidate_policy.py` 根据市场 regime、触发类型、短期过热、纯趋势追涨等做过滤。

这能解决一个用户痛点：

```text
同样的形态，牛市和熊市的含义不同。
同样的消息，低位和高位的赔率不同。
```

5. 策略反思需要人工审核

`strategy_reflection.py` 会总结不同 horizon / regime 下的表现，但策略晋级不是自动执行，而是 READY_FOR_REVIEW。

这对金融产品很重要。模型可以建议调参，但不应该未经验证直接改 live policy。

对用户产品的启发：

> 好工具不应该只说“发现机会”，还要告诉用户这个机会处于哪个生命周期阶段，以及规则是否经过历史验证。

### D. xang1234/stock-screener：最值得借鉴“解释和质量降级”

这个项目不是典型 AI agent，但它的评分和解释工程很扎实。

值得借鉴的点：

1. 评分函数纯净

`calculate_composite_score` 不依赖外部 I/O，方便测试、复现和回放。

金融场景里，打分逻辑如果混在 API 调用和 LLM 里，很难复盘。

2. 评级不是只看分数

`calculate_overall_rating` 还看 screener 通过数量：

- 没有任何 screener 通过，强制 PASS。
- 通过数量少于一半，评级降级。

这能避免“单项高分导致整体误导”。

3. 数据质量降级

`apply_quality_policy` 根据 field_completeness_score 强制 PASS 或降级。

这非常重要。

用户看到一个高分机会时，工具应该说明：

```text
这个高分基于完整数据
还是很多字段缺失下的估计
```

4. Explain payload

`explain_builder.py` 会输出 passed checks、failed checks、key levels、invalidation flags，并且只有所有 gate 通过时 `derived_ready` 才为 true。

5. Telemetry alert hysteresis

`alert_evaluator.py` 的告警状态机比很多股票 alert 工具更值得借鉴，因为它处理了重复提醒、升级、恢复、确认。

对用户产品的启发：

> 解释层和数据质量层不是锦上添花，而是决定用户是否愿意相信工具。

### E. stock-gapper-discord-bot：简单但抓住了短线扫描的基本骨架

这个项目很轻量：

- 从 Yahoo movers 或自定义 symbols 获取候选。
- 按 gap%、盘前量、美元成交额过滤。
- 查近期新闻。
- 用关键词判断催化剂。
- 根据 gap、volume、dollar_volume、catalyst points 打分。
- 发送 Discord。
- 每日记录已提醒股票，避免重复。

优点：

- 结构简单，容易理解。
- 对短线用户直接有用。
- 把价格异动和新闻催化剂结合，而不是单看新闻。
- 有日内去重状态。

局限：

- 新闻催化剂是关键词分类，容易误判。
- 没有判断“已经涨完”。
- 没有 entry / invalidation / outcome。
- 没有区分 gap up 和 gap down 后的策略语境。
- 没有风险否决和数据质量说明。

对用户产品的启发：

> 最小可用的短线机会扫描至少要有异动、成交量、资金强度、催化剂和去重。但要成为好工具，还必须加上情景和复盘。

## 四、商业产品给出的产品形态启发

### Benzinga Pro：实时性和工作台

Benzinga Pro 的公开页面强调实时新闻、alerts、movers、newsfeed、options、scanner、signals、squawk。

它的启发不是“我们也做新闻平台”，而是：

- 盘中机会需要实时提醒。
- 用户需要 news + movers + signals 放在一个工作台。
- “为什么动”比新闻正文更重要。
- 高频提醒需要强过滤，否则会过载。

对我们来说，实时新闻速度很难正面追平。更现实的差异化是：

```text
多源信息
更好的传导链解释
更清楚的已反应/未反应判断
更强的复盘
```

### Trade Ideas：信号必须带 entry / exit

Trade Ideas 的 AI Signals 页面强调 Holly、实时 buy/sell signals、entry and exit points、模拟交易和风险提示。

这说明短线工具不能只推“看涨”，而要把计划说清楚。

但它也说明了一个风险：如果产品过度像“交易指令”，合规风险会上升。

更稳妥的表达方式是：

- 机会类型
- 观察区间
- 失效条件
- 情景推演
- 不承诺收益
- 不替用户作最终投资决定

### TrendSpider：自然语言和自动化组合

TrendSpider 的 AI Strategy Lab / Sidekick / alerts / bots 给出一个方向：

用户不一定想写代码或写扫描器，他们希望用自然语言描述策略：

```text
找出突破前收缩、相对强度创新高、成交量未过热的股票
给我设置如果突破前高且成交量放大就提醒
这张图为什么没进入 ready 状态
```

这说明量化 agent 的交互应该允许：

- 自然语言建筛选器
- 自然语言解释筛选结果
- 自然语言设置提醒
- 自动把提醒转成结构化规则

### Unusual Whales：数据源可以 agent 化

Unusual Whales 的公开页面明确提供 API 和 MCP Server，覆盖 options flow、dark pool、stock data 等。

这类数据源对“机会雷达”很有价值：

- 异常期权流
- 大单成交
- 暗池数据
- 国会交易
- 机构持仓
- 技术指标
- 基本面数据

但数据源本身不是产品。

用户不想看 500 条期权流，而是想知道：

```text
这条 flow 是方向性还是对冲？
它和新闻/技术结构/成交量是否共振？
是否只是流动性噪音？
是否已经反映在股价里？
```

所以数据源 agent 化后，仍要进入机会卡框架。

## 五、好工具应该输出什么：机会卡而不是消息流

一个真正有用的机会卡至少应该包含这些字段。

### 1. 基本信息

```text
ticker
company_name
market
sector
theme
time_window
signal_status
generated_at
expires_at
```

### 2. 机会类型

```text
catalyst_driven
earnings_revision
theme_rotation
technical_breakout
pullback_retest
unusual_options_flow
valuation_repricing
small_cap_elasticity
short_squeeze
event_calendar
```

机会类型必须清楚，因为不同类型的验证方式不同。

### 3. 催化剂链条

```text
news_or_event
source
timestamp
why_market_moving
beneficiary_chain
financial_transmission
expected_time_to_verify
```

例如：

```text
AI 数据中心液冷需求上升
-> 数据中心资本开支上修
-> 液冷设备/材料订单增加
-> 某小市值公司订单弹性高
-> 未来 1-2 个季度看订单、收入、毛利率
```

这就是 serenity-alpha 思路应该发挥的地方。

### 4. 市场反应

```text
pre_news_return_1d_5d_20d
post_news_return
gap_pct
volume_vs_avg
relative_strength
distance_to_ma20_ma50_ma200
fomo_risk
crowding_score
```

这里对应 gf-dma-health-index 的思路。

用户需要知道：

```text
消息是真的，但是否已经涨完？
当前是早期、中段、末段，还是过热？
```

### 5. 估值和定价

```text
valuation_multiple
growth_expectation
implied_growth
tam_support
margin_quality
revision_trend
overpricing_risk
```

这里对应 TAM-Adj-PEG 和 Bayesian Intrinsic Growth Valuation。

对成长股，用户最需要知道：

```text
这个上涨是在修正低估，还是已经提前透支未来？
```

### 6. 操作剧本

不是直接写“买入”，而是写：

```text
watch_zone
confirmation_trigger
entry_reference
invalid_if
stop_reference
target_scenarios
next_check_time
```

核心是把用户从“情绪决策”拉回“条件决策”。

### 7. 情景推演

```text
bull_case
base_case
bear_case
what_to_watch
what_would_change_view
```

用户真正想知道的是“如果买了会怎样”。这不应该被回答成单一路径，而应该是概率情景。

### 8. 风险

```text
fundamental_risks
technical_risks
event_risks
liquidity_risks
valuation_risks
data_quality_risks
veto_flags
```

风险必须能影响最终评级，而不是文本附录。

### 9. 复盘字段

```text
anchor_price
benchmark_price
outcome_1d
outcome_3d
outcome_5d
outcome_10d
max_drawdown
max_favorable_excursion
hit_miss_neutral
reason_if_miss
source_agent
score_version
```

没有这些字段，工具无法自我进化。

## 六、评分应该怎么理解：不是预测涨跌，而是排序注意力

用户会说“哪些会涨”，但工具不应该承诺确定性上涨。

更准确的输出是：

```text
哪些股票在未来某个时间窗内，具备更高的正向预期差和更好的风险回报，值得优先关注。
```

分数应该用于排序注意力，而不是给确定答案。

一个可解释的机会分可以拆成：

```text
Opportunity Score
= Catalyst Strength
+ Financial Transmission
+ Market Confirmation
+ Technical Readiness
+ Estimate Revision / Fundamental Momentum
+ TAM / Growth Support
- Overreaction / Crowding
- Valuation Overpricing
- Risk Veto / Risk Penalty
- Data Quality Penalty
```

每一项都应该能展开解释。

用户看到的不是公式，而是：

```text
为什么排第一？
为什么不是另一个热门股？
这个机会最强证据是什么？
最大反证是什么？
什么条件会让它升级或降级？
```

## 七、推送应该怎么设计

好推送不是“多快”，而是“该推的时候推”。

### 推送分级

| 级别 | 场景 | 用户动作 |
| --- | --- | --- |
| L1 线索 | 新消息/新异动，证据不足 | 放入观察池 |
| L2 候选 | 催化剂、资金、技术至少两项共振 | 加入机会列表 |
| L3 确认 | 到达确认条件，如突破、缩量回踩、公告验证 | 强提醒 |
| L4 风险 | 跌破失效位、风险新闻、财报雷 | 强提醒 |
| L5 复盘 | 时间窗结束或信号关闭 | 汇总到日报 |

### 去重和冷却

规则应该类似：

- 同一 ticker 同一机会类型，同级别不重复提醒。
- 新证据显著增强，可以升级提醒。
- 风险触发必须打断冷却。
- 机会过期后归档，不再盘中打扰。
- 日报总结所有未推送但有变化的观察项。

### 用户可控项

用户应该能设置：

- 只看美股 / A 股 / 港股
- 只看短线 / 波段 / 成长股
- 排除小市值 / 低流动性
- 只看自己 watchlist
- 只看 AI、半导体、机器人、电力、医疗科技等主题
- 只看 confirmed，不看 early signals
- 是否接收风险提醒

## 八、坏工具和好工具的差别

| 维度 | 坏工具 | 好工具 |
| --- | --- | --- |
| 输出 | 新闻列表、AI 总结、买卖词 | 少数机会卡 |
| 解释 | 因为有利好 | 催化剂到财务项到股价反应 |
| 时间窗 | 不清楚 | intraday / 1d / 3d / 10d / swing |
| 买后剧本 | 没有 | 乐观/中性/悲观/失效 |
| 风险 | 写在最后 | 能降级或 veto |
| 数据质量 | 不展示 | 明确字段缺失和可信度 |
| 推送 | 重复轰炸 | 状态机、去重、升级、关闭 |
| 评分 | 黑盒总分 | 分项、权重、通过/失败条件 |
| 复盘 | 展示成功案例 | 展示全样本 outcome |
| 自我进化 | 手动改 prompt | shadow score、review gate、版本化 |

## 九、从用户角度，我认为最好的形态

如果站在用户每天打开工具的角度，最好的首页不是聊天框，也不是新闻流，而是：

```text
今日机会雷达

1. 已确认机会
   少数最值得看的机会，带关键触发和风险位。

2. 观察中机会
   还差一两个确认条件，告诉用户等什么。

3. 过热/拥挤机会
   消息好但不适合追，解释为什么。

4. 持仓风险
   如果用户持有某票，告诉他今天是否有失效/降级风险。

5. 昨日/上周复盘
   哪些信号命中，哪些失败，为什么。
```

每张卡都能展开：

```text
为什么是它
什么消息利好
传导到哪里
市场反应如何
当前是不是过热
如果参与要看什么
错了的信号是什么
过去类似信号表现如何
```

这才是用户会反复使用的工具。

## 十、这轮调研后的产品判断

### 1. 不要做纯新闻 agent

新闻 agent 很容易被替代，也无法建立长期信任。

应该做：

```text
新闻 / 异动 / 资金 / 技术 / 基本面 / 期权流
-> 机会候选
-> 结构化机会卡
-> 状态机
-> 推送
-> outcome 复盘
```

### 2. 第一性目标不是“预测”，而是“提高用户决策质量”

用户会问“哪些会涨”，但工具应该回答：

```text
这些股票具备更好的正向催化剂和验证路径。
这些已经过热，不适合追。
这些还在观察，等确认。
这些风险太高，不纳入。
```

这比直接说“买 A、买 B”更有价值，也更稳。

### 3. 必须有可验证闭环

只要做推送，就必须记录 outcome。

否则用户迟早会问：

```text
你上周推的那些后来怎么样？
```

这个问题回答不上来，工具就不可信。

### 4. 开源项目可借鉴但不能直接照搬

`daily_stock_analysis` 的 DecisionSignal 和 outcome 结构非常值得借鉴。  
`go-stock` 的工具面和中文市场数据处理值得借鉴。  
`WyckoffTradingAgent` 的 pending / confirmed / expired 和 shadow score 值得借鉴。  
`xang1234/stock-screener` 的评分、解释、数据质量和告警 hysteresis 值得借鉴。  
`stock-gapper-discord-bot` 的轻量短线扫描和去重值得借鉴。

但没有一个项目完整解决了：

```text
多源催化剂
财务传导
市场反应
过热识别
情景推演
个性化推送
结果复盘
合规边界
```

所以我们需要的是组合式设计，而不是复制某个 repo。

## 十一、仍需继续调研的问题

后续如果继续调研，最值得看的不是更多 AI 荐股产品，而是这些具体问题：

1. 数据源可用性

- 美股新闻、公告、财报、分析师上修、期权流、盘前异动分别用哪些源？
- 免费源和付费源的延迟差异多大？
- A 股、港股、美股的工具面是否要分开？

2. 信号标签体系

- 催化剂类型怎么分？
- 机会 horizon 怎么分？
- 技术确认条件怎么标准化？
- 风险 veto 类型怎么标准化？

3. 回测和 outcome

- 对事件驱动机会，如何定义命中？
- 对波段机会，如何定义失效？
- 如何避免幸存者偏差和盘后回填数据污染？

4. 推送体验

- 每天推几条用户不会烦？
- 盘中强提醒和日报弱提醒怎么区分？
- 用户持仓和非持仓的提醒逻辑是否不同？

5. 合规表达

- 哪些词不能用？
- 如何表达“机会”和“情景”，而不是“明确投资建议”？
- 如何展示风险和不确定性？

## 最后判断

站在用户角度，好的量化工具不是一个聊天机器人，也不是一个更快的新闻爬虫。

它应该像一个冷静的研究助理：

```text
少推
推得有理由
告诉你什么时候错
事后承认错
长期让你知道它哪里准、哪里不准
```

如果要做一个“帮我推哪些股票可能涨”的 agent，核心竞争力不在“AI 会说”，而在这套闭环：

```text
发现线索
解释传导
判断反应
生成剧本
控制推送
追踪结果
复盘改进
```

这才是和普通消息 agent、黑盒荐股工具拉开差距的地方。
