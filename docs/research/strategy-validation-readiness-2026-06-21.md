# 量化机会 Agent 策略验证调研：是否已经清楚到可以开始实现

调研日期：2026-06-21  
目标：在写代码前验证策略、数据源、产品输出和风险边界是否足够清楚  
结论口径：这是研究与验证文档，不构成投资建议，也不证明任何策略未来盈利  

---

## 0. 最终判断

我认为现在已经调研清楚到可以开始做“验证型实现”，但还不应该直接做“用户可用的荐股式产品”。

更准确地说：

```text
可以开始：
  数据接入 PoC
  策略信号计算
  历史事件回测
  机会卡生成
  策略健康面板

不应该直接开始：
  自动荐股
  自动交易
  黑箱涨跌概率
  只靠新闻或期权流的推送
```

原因：

1. 策略地图已经完整；
2. 每类策略的使用边界已经清楚；
3. 主推送策略和辅助确认信号已经能区分；
4. 数据源可得性已经验证出关键风险；
5. 后续最重要的不是继续泛泛找策略，而是做历史实证和数据源选择。

一句话：

> 研究方向清楚了，产品结构清楚了，策略边界清楚了；下一步应写验证型代码，不应直接写终端产品代码。

---

## 1. 这次验证回答什么

这次不是继续问“还有什么策略”，而是验证四个问题：

1. 这些策略有没有公开研究或市场实践支持；
2. 数据能不能拿到，是否需要商业 API；
3. 策略能不能转成可复盘的机会卡；
4. 哪些策略可以主推，哪些只能辅助，哪些必须做风险提示。

验证标准：

| 维度 | 通过标准 |
|---|---|
| 证据 | 有学术、公开研究、开源实现或成熟产品形态支持 |
| 数据 | 能明确列出字段、来源、延迟、授权限制 |
| 解释 | 能说清楚为什么可能上涨，以及什么情况说明错了 |
| 回测 | 能冻结信号时间点并评估 1/5/10/20/60 日结果 |
| 风险 | 能识别过热、已定价、流动性、数据误读和合规风险 |

---

## 2. 策略验证总表

| 策略族 | 证据强度 | 数据可得性 | 可解释性 | 推送角色 | 验证结论 |
|---|---:|---:|---:|---|---|
| PEAD 财报后漂移 | 高 | 中高 | 高 | 主机会卡 | 可以进入验证型实现 |
| 分析师预期上修 | 高 | 中 | 高 | 主机会卡 / 确认 | 可以实现，但需要商业数据 |
| 成长趋势 / 动量 | 高 | 高 | 高 | 主机会卡 | 可以进入验证型实现 |
| 健康回调 / 突破 | 中高 | 高 | 高 | 主机会卡 / 技术触发 | 可以进入验证型实现 |
| 事件催化 + 财务传导 | 中高 | 中 | 高 | 主机会卡 | 可以实现，但需要半结构化知识库 |
| 新闻情绪 + attention | 中 | 中 | 中 | 辅助确认 / 拥挤度 | 不能单独主推 |
| 期权异动 | 中 | 低中 | 中 | 辅助确认 | 需要商业逐笔期权数据，不能单独主推 |
| 空头回补 | 中 | 中 | 中 | 高风险事件卡 | 可以做，但必须强风险提示 |
| 内部人买入 | 中 | 高 | 中高 | 辅助确认 | 可以做确认，不宜单独主推 |
| 13F / 机构持仓 | 中 | 高 | 中 | 慢变量确认 | 可以做背景，不宜短线推送 |
| 回购 | 中高 | 中 | 高 | 事件卡 / 资本配置 | 可以做，但要验证执行而非授权 |
| 并购套利 | 中高 | 中 | 中 | 专业事件卡 | 不适合普通股票机会流 |
| FDA / 临床催化 | 中 | 中 | 高 | 垂直事件卡 | 可以研究，但风险等级要单独标注 |
| IPO lock-up / 解禁 | 中 | 中 | 中 | 风险/事件卡 | 可以做，但不应简单看空 |
| 板块轮动 / macro regime | 中高 | 高 | 中 | 全局加权 | 应作为所有策略的风控层 |
| 多因子约束 | 高 | 高 | 中 | 质量/估值/风险约束 | 适合做过滤和归因 |
| 均值回归 | 中 | 高 | 中 | 受限机会卡 | 只适合液体标的和短周期 |
| 黑箱 ML 涨跌预测 | 低中 | 中 | 低 | 研究实验 | 不适合当前产品主路径 |

---

## 3. 可以作为主推送层的策略

### 3.1 PEAD 财报后漂移

验证结论：可以进入验证型实现。

证据：

- PEAD 是被长期研究的财报后异常收益现象。
- Quantpedia 汇总认为，正向盈利公告后的异常收益可能在数周到数月内继续漂移。
- PEAD 天然适合事件研究：财报日、公告日反应、后续 CAR 都能冻结。

需要验证的不是“PEAD 是否存在”，而是：

1. 我们的数据源能否稳定拿到 actual vs estimate；
2. 我们的事件时间戳是否准确；
3. 我们的过滤条件是否能减少“已定价”的信号；
4. 机会卡的 5/10/20/60 日结果是否有正向 base rate。

适合输出：

```text
财报超预期后，市场反应偏温和，后续存在漂移观察价值。
```

不适合输出：

```text
财报好，所以一定买。
```

实现关键：

- actual EPS；
- consensus EPS；
- revenue surprise；
- guidance；
- announcement timestamp；
- close/open/gap/volume；
- benchmark-adjusted return；
- analyst revision after earnings。

### 3.2 分析师预期上修

验证结论：策略逻辑清楚，但实现依赖商业数据。

证据：

- 盈利预期变化是成长股重估的重要驱动。
- 与 PEAD 结合时尤其有价值：财报好但没有上修，说明市场可能不认可持续性；财报后连续上修，说明信息进入模型。

关键验证：

1. EPS / revenue estimate revision 的时间戳；
2. 上修幅度；
3. 上修广度；
4. 覆盖未来几个季度；
5. 价格是否已经提前反应。

数据问题：

- 免费数据很难稳定提供高质量 analyst estimates。
- Finnhub、FMP、Benzinga、Koyfin、FactSet/IBES 等更现实。
- 后续实现需要先确定 API，否则策略不能可靠落地。

### 3.3 成长趋势 / 动量

验证结论：可以进入验证型实现。

证据：

- Jegadeesh and Titman 1993 记录了 3-12 个月中期动量的显著收益。
- AQR 的 time-series momentum 和 trend-following 研究也支持趋势类信号具有跨资产、长样本的稳健性。
- 开源 `stock-screener`、`growth-stock-screener` 说明这类策略很容易产品化成筛选漏斗。

需要验证：

1. 不同均线模板的信号密度；
2. 过热惩罚是否有效；
3. 市场 regime 对胜率的影响；
4. 基本面增长叠加后是否改善结果。

适合输出：

```text
这只股票处于强势成长趋势，当前回调/突破值得观察。
```

不适合输出：

```text
RS 高，所以必涨。
```

### 3.4 健康回调 / 突破确认

验证结论：可以作为主推送或主策略的触发条件。

理由：

- 用户最能理解；
- 数据容易；
- 风险位清楚；
- 能和财报、预期、催化、产业链结合；
- 可以做成“观察位”和“失效位”。

关键是不要让它变成单纯技术指标。

正确用法：

```text
基本面或催化成立 + 趋势健康 + 回调到关键均线
```

错误用法：

```text
价格碰到 50DMA，所以看涨
```

### 3.5 事件催化 + 财务传导

验证结论：可以进入验证型实现，但需要结构化知识库。

这是最能体现 agent 价值的策略，也是最容易做坏的策略。

验证重点：

1. 新闻是否能正确分类；
2. ticker 映射是否准确；
3. 受益公司是否有真实收入暴露；
4. 利好是否能传导到收入、利润、订单、毛利率或估值；
5. 市场是否已经定价；
6. 后续是否有可观察验证点。

这类策略不能只靠 LLM 文本推理，必须有结构化字段。

---

## 4. 只能作为辅助确认的策略

### 4.1 期权异动

验证结论：可以做，但不能单独主推。

证据：

- Pan and Poteshman 的研究显示，期权成交量中包含未来股价信息，尤其是买方发起的 put-call ratio。
- 但 unusual options activity 容易被误读：可能是对冲、价差、平仓、波动率交易或媒体曝光后的拥挤交易。

数据要求高：

- trade-level options；
- bid/ask；
- volume；
- open interest；
- implied volatility；
- delta；
- DTE；
- moneyness；
- underlying price confirmation。

没有逐笔和 bid/ask，期权流只能做弱信号。

### 4.2 内部人交易

验证结论：适合做辅助确认。

证据：

- 公开研究通常认为内部人买入比卖出更有信息量。
- Form 4 数据可通过 SEC 获取。

使用边界：

- open market purchase 比 option exercise 更有价值；
- CEO/CFO 买入比普通董事小额买入更有价值；
- 多人集中买入比单人买入更强；
- 卖出不能直接看空。

### 4.3 13F / 机构持仓

验证结论：适合作为慢变量背景，不适合短线推送。

SEC 13F 数据公开可得，但有硬限制：

- 季度披露；
- 通常存在最多 45 天滞后；
- 不显示完整组合；
- 不显示大部分空头；
- 披露后机构可能已卖出。

适合用法：

```text
某主题中，小市值公司开始被主动机构持有，作为长期关注增强。
```

不适合用法：

```text
某基金上季度买了，所以今天看涨。
```

### 4.4 新闻情绪

验证结论：只能辅助，不能单独主推。

RavenPack 和 Fed 的文本研究都提示，新闻情绪有效性依赖 attention、覆盖度、时间尺度、市场环境和价格反应。

正确用法：

```text
事件分类 + 财务传导 + 市场反应 + 新闻 attention
```

错误用法：

```text
新闻正面 = 股票看涨
```

---

## 5. 高风险事件卡

### 5.1 空头回补

验证结论：可以做，但必须标记为高风险事件卡。

证据：

- Short interest 研究普遍认为高空头股票未来更容易出现负异常收益，说明空头往往是信息型交易者。
- 这意味着“高空头”本身不是利好。
- 只有在正向催化、价格突破、借券紧张、流通盘小、期权 call 确认同时出现时，才可能形成 squeeze。

数据验证：

- FINRA `regShoDaily` API 在本地返回了 CSV 样例数据；
- FINRA `equityShortInterest` 也返回了短空头持仓样例；
- Nasdaq short interest 官方页面显示 rolling 12 months，并且每月更新两次。

关键风险：

- daily short sale volume 不是 short interest；
- short interest 有披露滞后；
- 高空头可能代表公司真的差。

### 5.2 回购

验证结论：可以做事件卡，但必须区分授权和执行。

证据：

- Ikenberry、Lakonishok、Vermaelen 的 open-market repurchase 研究发现回购公告后有长期异常收益，尤其 value stocks 更明显。

实现重点：

- buyback authorization / market cap；
- FCF 覆盖；
- 真实回购执行；
- 股权激励稀释；
- 管理层历史执行记录。

### 5.3 FDA / 临床催化

验证结论：可以研究，但必须单独风险分层。

医药临床事件很适合机会卡，因为时间点明确、影响大、验证明确。但它也是二元事件：

- 成功可能大涨；
- 失败可能暴跌；
- IV 通常极高；
- approval 不等于商业成功。

适合做：

```text
高波动事件日历 + 情景推演 + 风险提示
```

不适合做：

```text
临床数据即将发布，所以看涨
```

### 5.4 IPO lock-up / 解禁

验证结论：可以做风险事件卡。

证据：

- lock-up expiration 研究显示解禁日前后可能存在异常价格压力和成交量上升。

但不能简单看空：

- 如果市场提前下跌，可能已经定价；
- 如果空头拥挤，可能反向 squeeze；
- 如果基本面强，供给冲击可能被吸收。

---

## 6. 不适合作为普通主推送的能力

### 6.1 并购套利

并购套利有成熟逻辑，但它不是普通“股票会涨”问题，而是 deal spread、完成概率、时间价值和失败损失的问题。

可以做专业事件卡，不适合混在普通机会流。

### 6.2 复杂期权结构

期权结构适合表达已有观点，不适合让 agent 主动推荐。

如果要做，必须处理：

- Greeks；
- IV crush；
- bid/ask；
- margin；
- assignment；
- liquidity；
- fill model。

### 6.3 黑箱 ML 涨跌预测

不建议作为当前主路径。

原因：

- 解释性弱；
- 容易过拟合；
- 用户难以理解失败原因；
- 没有策略语义；
- 合规风险更高。

可以保留为研究实验，但不能作为产品核心。

---

## 7. 数据源验证结果

### 7.1 本地命令行实测

| 数据源 | 实测结果 | 结论 |
|---|---|---|
| FINRA regShoDaily | 返回 CSV 样例数据 | 可接入，但要按文档处理过滤、分页和语义 |
| FINRA equityShortInterest | 返回 CSV 样例数据 | 可用于 OTC / short interest 方向验证 |
| Nasdaq short interest 页面 | 官方页面确认 rolling 12 months、每月两次更新 | 可作为 listed short interest 来源，但批量可能需要订阅 |
| SEC EDGAR API | 官方文档确认无 key；本地命令行出现 SSL EOF | 官方可用，但当前环境需要处理 SSL/网络访问 |
| Yahoo chart endpoint | 返回 429 Too Many Requests | 不适合作为稳定生产源 |
| Stooq CSV | 返回 JS/browser verification 页面 | 当前环境不适合作为稳定自动化源 |
| yfinance | 本机未安装 | 可安装用于原型，但不应作为生产依赖 |
| Finnhub demo | 返回 invalid API key | 需要正式 API key |
| FMP demo | 返回 invalid API key | 需要正式 API key |

### 7.2 数据源结论

价格数据：

- 原型可以用 yfinance/OpenBB/免费源，但当前环境需要安装或修复；
- 生产更适合 Massive/Polygon、Finnhub、FMP、Tiingo、Nasdaq Data Link 或券商数据。

财报和分析师：

- PEAD 的关键数据是 actual vs estimate；
- 分析师上修必须要历史 consensus 和 revision timestamp；
- 这类数据大概率需要商业 API。

新闻：

- 新闻不是难在获取，而是难在去重、分类、ticker 映射和财务传导。

期权：

- 期权流必须用高质量数据；
- 没有 bid/ask、OI、DTE、IV、逐笔方向，就不应做方向判断。

SEC / filings：

- 官方数据足够支撑 XBRL、Form 4、13F、8-K 等方向；
- 实现时要优先使用 bulk ZIP 和缓存，避免频繁打 API。

---

## 8. 机会卡可验证性

每张机会卡都必须在生成时冻结一份信号快照：

```yaml
opportunity_id:
symbol:
strategy_family:
signal_time:
signal_price:
benchmark_price:
data_snapshot_hash:
trigger_fields:
score_components:
invalid_if:
watch_next:
expected_horizon:
```

然后自动追踪：

```yaml
return_1d:
return_5d:
return_10d:
return_20d:
return_60d:
excess_return_20d:
max_favorable_excursion:
max_adverse_excursion:
invalidated:
failure_type:
```

没有这个闭环，agent 只是在写研究报告。

有这个闭环，agent 才能变成可进化的策略系统。

---

## 9. 实现前的验收门槛

开始写代码前，下面这些已经足够明确：

1. 策略宇宙完整；
2. 主推送、辅助确认、风险提示、专业事件卡边界明确；
3. 数据源清单明确；
4. 部分官方数据源已实测；
5. 机会卡 schema 明确；
6. 结果复盘 schema 明确；
7. 不应该做什么已经明确。

还需要在实现中继续验证：

1. 选定具体商业数据源；
2. 建立统一 symbol、calendar、timezone 和 timestamp 处理；
3. 做历史信号回测；
4. 计算每个策略的 base rate；
5. 验证每天推送数量是否可控；
6. 验证机会卡文案是否不会被理解成直接荐股。

---

## 10. 建议的开工顺序

不是产品分期，而是验证工程顺序。

### 10.1 第一批验证代码

1. Universe builder；
2. Price/OHLCV adapter；
3. Opportunity card schema；
4. Outcome tracker；
5. Strategy registry；
6. Trend / momentum strategy；
7. Pullback / breakout strategy；
8. Strategy health dashboard。

这批不依赖昂贵商业数据，可以先把框架跑通。

### 10.2 第二批验证代码

1. Earnings calendar adapter；
2. EPS / revenue surprise adapter；
3. PEAD strategy；
4. Analyst revision adapter；
5. Revision confirmation strategy。

这批需要明确数据 API。

### 10.3 第三批验证代码

1. News ingestion；
2. Event classifier；
3. Financial transmission mapper；
4. Theme/supply-chain knowledge base；
5. Catalyst opportunity card。

这批最体现 agent 价值，但要先保证前两批的结构稳定。

### 10.4 第四批验证代码

1. Options flow adapter；
2. Short interest adapter；
3. Insider Form 4 adapter；
4. 13F adapter；
5. Buyback event adapter；
6. FDA / lock-up / M&A event adapters。

这些进入辅助确认、风险提示或专业事件卡。

---

## 11. 明确的 Go / No-Go 结论

### Go

可以开始写：

- 策略注册框架；
- 数据 adapter 接口；
- 机会卡 schema；
- outcome tracker；
- price/trend 策略；
- PEAD 策略接口；
- strategy health dashboard；
- 回测与复盘框架。

### Conditional Go

可以开始设计，但需要先拿到 API key 或样本数据：

- analyst revisions；
- earnings surprise；
- transcripts；
- options flow；
- real-time news；
- commercial fundamentals。

### No-Go

当前不应写成用户主功能：

- 自动荐股；
- 自动交易；
- 黑箱涨跌概率；
- 社媒热度单独推送；
- 期权异动单独推送；
- 复杂期权结构推荐。

---

## 12. 参考来源

### 策略证据

- Quantpedia, Post-Earnings Announcement Effect: https://quantpedia.com/strategies/post-earnings-announcement-effect/
- Jegadeesh and Titman, Returns to Buying Winners and Selling Losers: https://www.bauer.uh.edu/rsusmel/phd/jegadeesh-titman93.pdf
- AQR, Time Series Momentum: https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum
- AQR, A Century of Evidence on Trend-Following Investing: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
- Pan and Poteshman, The Information in Option Volume for Future Stock Prices: https://www.mit.edu/~junpan/5919.pdf
- Ikenberry, Lakonishok, Vermaelen, Market Underreaction to Open Market Share Repurchases: https://www.nber.org/system/files/working_papers/w4965/w4965.pdf
- Quantpedia, Short Interest Effect: https://quantpedia.com/strategies/short-interest-effect-long-only-version
- NYU archive, IPO Lock-Up Period: https://archive.nyu.edu/bitstream/2451/27072/2/wpa99054.pdf
- PLOS One, How does news affect biopharma stock prices?: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0296927

### 官方数据源

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Form 13F Data Sets: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
- SEC Forms 3, 4, 5 overview: https://www.sec.gov/files/forms-3-4-5.pdf
- FINRA Short Sale Volume Data: https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data
- FINRA Equity Short Interest Data: https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data
- Nasdaq Short Interest: https://www.nasdaqtrader.com/Trader.aspx?id=ShortInterest

### 数据和产品

- Finnhub API docs: https://finnhub.io/docs/api
- Financial Modeling Prep docs: https://site.financialmodelingprep.com/developer/docs
- Benzinga APIs: https://www.benzinga.com/apis/
- Massive/Polygon docs: https://massive.com/docs/rest/stocks/overview
- Benzinga Pro: https://www.benzinga.com/pro/
- TrendSpider: https://trendspider.com/
- Koyfin stock data coverage: https://www.koyfin.com/data-coverage/stocks/
- Quiver Quantitative: https://www.quiverquant.com/

### 开源实现参考

- PEAD Tool: https://github.com/Yash-Bhanushali-21/pead-tool
- RyanJHamby Stock Screener: https://github.com/RyanJHamby/stock-screener
- Growth Stock Screener: https://github.com/starboi-63/growth-stock-screener
- AgentQuant: https://github.com/OnePunchMonk/AgentQuant
- Options Portfolio Backtester: https://github.com/lambdaclass/options_portfolio_backtester
- TradingAgents: https://github.com/tauricresearch/tradingagents
- AI Hedge Fund: https://github.com/virattt/ai-hedge-fund
