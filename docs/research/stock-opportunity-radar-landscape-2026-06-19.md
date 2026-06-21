# 股票机会雷达 Agent 深度调研

调研日期：2026-06-19  
目标：更充分扫描“哪些股票最近可能涨、为什么、什么消息利好什么、如果买了会发生什么”的产品、开源项目、数据源、视频/社媒趋势与风险边界。  
状态：调研，不进入实现方案收敛。

## 重新定义需求

用户想要的不是传统量化回测工具，也不是消息摘要工具，而是：

> 一个会主动发现近期上涨机会、解释催化剂、映射受益股票、推送候选、并给出买入后情景推演的股票机会雷达。

更接近的关键词不是 `quant research agent`，而是：

- stock opportunity radar
- AI stock picker / AI stock screener
- catalyst scanner
- pre-catalyst scanner
- why is it moving
- market-moving news alerts
- unusual options flow
- analyst revision scanner
- earnings surprise / earnings calendar alert
- theme discovery / emerging theme scanner
- watchlist decision dashboard

## 这类产品真正要解决的问题

1. 信息太多  
   用户不需要“今天新闻很多”，而是要“哪条新闻可能推动哪只股票，且市场是否已经反应”。

2. 传导链不清楚  
   一条新闻可能利好行业，但未必利好具体公司；可能利好收入，也可能只是估值情绪。

3. 机会窗口短  
   对短线/波段机会，速度比深度更重要；但没有风控解释又容易变成噪音。

4. 买入后不知道看什么  
   真正有用的不是一句“看涨”，而是买入后的乐观/中性/悲观剧本和失效信号。

5. 很多 AI 荐股产品可信度差  
   商业产品大量使用 AI Score、Buy/Sell Signals、AI Robots，但很多不披露完整数据、回测和失败样本。

## 产品/平台扫描

### 1. 实时新闻 + 股票异动平台

#### Benzinga Pro

来源：[Benzinga Pro](https://www.benzinga.com/pro/)

定位：快速新闻流、movers、signals、scanner、alerts、squawk。  
调研摘录：

- 页面强调其价值在于“实时市场新闻”和“catch breakout stocks before they rally”。
- 功能包括 alerts、calendar、movers、newsfeed、options、scanner、screener、signals、watchlist。
- 提到 sentiment indicators、exclusive market-moving stories、stock scanner、stock alerts。

可借鉴：

- “为什么动”的解释比纯新闻列表更重要。
- 推送应该绑定 ticker、催化剂类别、可能影响、反应速度。
- 声音/即时提醒适合盘中，但第一版可以先做文本/Telegram/飞书/企业微信。

局限：

- 商业平台强依赖自有新闻速度和编辑团队。
- 对我们来说，实时性很难直接追平，应该通过多源交叉和解释质量补位。

#### StockTitan / StockTwits / Finviz / Yahoo Finance 类

本轮没有逐一深挖，因为它们更像新闻、社区或基础行情入口。它们的价值主要是：

- 股票新闻 feed
- 热门 ticker
- 社区情绪
- gappers / movers
- earnings calendar

对 agent 的启发是：不要只抓新闻，还要抓“新闻后的市场反应”：价格、成交量、相对强弱、盘前/盘后异动。

### 2. AI Stock Picker / AI Score 产品

#### Danelfin

来源：[Danelfin](https://danelfin.com/)、[Danelfin US stocks](https://danelfin.com/us-stocks)、[How it works](https://danelfin.com/how-it-works)

定位：AI stock picker / AI Score。  
调研摘录：

- Danelfin 把股票按 AI Score 排名。
- AI Score 目标是评估未来约 3 个月跑赢市场的概率。
- 覆盖美股、欧洲股票、ETF，并提供 top AI score stocks。

可借鉴：

- 一个统一机会分数很有用，但必须拆成分项：催化剂、基本面、技术面、情绪、风险。
- 时间窗要明确。Danelfin 是 3 个月；我们的机会雷达也应该明确是日内、1-5 天、2-6 周，还是 3 个月。

局限：

- 黑盒分数不可解释。
- 用户要的是“为什么这只可能涨”，不只是 1-10 分。

#### Tickeron

来源：[Tickeron](https://tickeron.com/)

定位：AI trading agents、AI robots、AI stock screener、daily buy/sell signals。  
调研摘录：

- 页面展示 AI Virtual Agents、AI Trending Robots、AI Trading Bots。
- DIY 工具包括 AI Pattern Search Engine、AI Trend Prediction Engine、AI Stock Screener、AI Real Time Patterns。
- Daily Buy/Sell Signals 支持 alerts/notifications。

可借鉴：

- 产品形态非常接近“主动推 trade ideas”。
- 它把用户分成 beginner、DIY、copy trader，这说明机会雷达应该支持不同“使用姿势”：只看推送、自己筛选、跟踪持仓。

局限：

- 有很强“信号服务”色彩，容易过度承诺。
- 我们应该保留解释、证据、反例和情景，而不是只做 Buy/Sell。

#### Kavout / Kai Score

来源：[Kavout K Score](https://www.kavout.com/k-score/)、[Kai Score](https://www.kavout.com/market-lens/kai-score-is-here-create-ai-stock-picks-your-way)

定位：Quantamental AI stock rating / natural-language AI stock picks。  
调研摘录：

- K Score 是 1-9 的 quantamental stock rating score。
- Kai Score 允许用户用自然语言创建 AI stock picks / AI stock screener。

可借鉴：

- 自然语言筛选是关键交互：用户可以说“找 AI 电力链中财报上修、但还没过热的小盘股”。
- “quantamental”概念适合：基本面 + 技术面 + 情绪 + 估值 + 风险，不要单因子。

局限：

- 商业黑盒，无法知道权重和验证方式。

### 3. 技术面扫描 / AI 策略实验平台

#### Trade Ideas / Holly AI

来源：[Trade Ideas Holly AI Guide](https://www.trade-ideas.com/hollyguide/Who_is_Holly.html)、[AI signals](https://www.trade-ideas.com/features/ai-signals/)

定位：日内/短线 stock scanner + AI trade signals。  
调研摘录：

- Holly 是 real-time stock suggestions。
- 信号包括 entry prices、stops、targets。
- 使用 60+ algorithms。

可借鉴：

- 如果做“会涨推送”，必须至少提供入场参考、止损/失效、目标/观察位。
- 机会不是单点结论，而是一个 trade plan：entry、invalid、target、watch.

局限：

- 偏日内交易，数据/执行要求高。
- 真实复现需要高速行情和严格回测，不适合第一步完全照搬。

#### TrendSpider AI Strategy Lab / Sidekick

来源：[TrendSpider](https://trendspider.com/)、[AI Strategy Lab](https://trendspider.com/product/artificial-intelligence-ai-trading-strategy-lab/)、[AI Strategy Lab blog](https://trendspider.com/blog/introducing-trendspiders-ai-strategy-lab/)

定位：AI strategy lab、scanner、alerts、bots、chart assistant。  
调研摘录：

- AI Strategy Lab 训练自定义 ML 模型，生成预测交易信号。
- Sidekick AI 是 conversational assistant，用于分析图表、构建 scans/alerts、获取实时 insights。
- AI models 可用于 charts、scans、alerts、bots。

可借鉴：

- “聊天建扫描器”是好方向：用户说条件，系统保存为监控任务。
- AI 不只是分析单股，还可以生成扫描规则和告警条件。

局限：

- 图表/技术面强，新闻/产业链传导不是重点。

### 4. Options Flow / Dark Pool / Smart Money

这类数据非常接近“近期会不会动”的短线雷达，但噪音和误判也大。

#### Unusual Whales

来源：[Unusual Whales](https://unusualwhales.com/)、[Flow Alerts](https://unusualwhales.com/option-flow-alerts)

定位：options flow、dark pool、market analysis、real-time flow alerts。  
调研摘录：

- Flow Alerts 识别 unusual options activity，包括 RepeatedHits、Sweeps、Blocks、Golden Sweeps。
- 提供 API、OpenAPI、MCP、skill.md 等面向 agent 的集成提示。

可借鉴：

- options flow 可以作为“潜在催化/资金提前反应”的一个维度。
- 规则化 alert 比 LLM 主观判断更可靠。
- 提供 MCP/skill 是非常值得参考的 agent 接入方式。

局限：

- 期权流不能直接等于方向；大单可能是 hedge、spread、closing trade。
- 必须配合价格确认、新闻、IV、open interest、earnings window。

#### Cheddar Flow / Tradytics / Market Chameleon / Barchart

来源：[Cheddar Flow](https://www.cheddarflow.com/)、[Tradytics](https://tradytics.com/)、[Market Chameleon unusual option volume](https://marketchameleon.com/Reports/UnusualOptionVolumeReport)、[Barchart unusual options](https://www.barchart.com/options/unusual-activity)

共同能力：

- unusual options volume
- real-time order flow
- dark pool data
- AI/power alerts
- alerts/Discord/community
- historical analysis/backtesting in some products

可借鉴：

- 对“最近可能涨”的预测，不应该只看新闻，期权/成交量/暗池/异常流也可以提供 early signal。
- 推送卡里可以有“资金确认”字段：成交量、期权流、暗池、涨跌幅、相对强弱。

局限：

- 数据收费且解释门槛高。
- 适合后续增强，不适合第一轮作为唯一核心。

### 5. 中文/开源股票分析与推送项目

#### ZhuLinsen/daily_stock_analysis

来源：[daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)

GitHub API 快照：

- Stars: 43,129
- Forks: 40,795
- Language: Python
- Updated: 2026-06-19

README 重点：

- A股/港股/美股自选股智能分析系统。
- 每日自动分析并推送“决策仪表盘”到企业微信/飞书/Telegram/Discord/Slack/邮箱。
- 功能包括 AI 决策报告、评分、趋势、买卖点位、风险警报、催化因素、操作检查清单。
- 数据包括行情、K线、技术指标、资金流、筹码、新闻、公告和基本面。
- Agent 策略问股支持均线、缠论、波浪、趋势、热点、事件、成长、预期等内置策略。
- 新闻源可接 Anspire、SerpAPI、Tavily、Bocha、Brave、MiniMax、SearXNG；社交舆情可接 Stock Sentiment API。

这是本轮最贴近用户目标的开源参考之一。  
它的产品关键词就是“每天自动分析 + 推送 + 决策仪表盘 + 催化因素 + 风险警报”。

可重点参考：

- 多市场支持：A/H/美股。
- 推送渠道：企业微信、飞书、Telegram、Discord、Slack、邮件。
- 报告结构：评分、趋势、买卖点位、风险警报、催化因素、检查清单。
- GitHub Actions 零成本定时运行。

需要警惕：

- Stars/Forks 异常接近，可能存在 fork 部署驱动，不代表代码质量一定最高。
- 需要进一步代码审计其数据源质量、提示词、推送格式、失败处理。

#### ArvinLovegood/go-stock

来源：[go-stock](https://github.com/ArvinLovegood/go-stock)

GitHub API 快照：

- Stars: 6,421
- Forks: 1,143
- Language: Go
- Updated: 2026-06-19

README 重点：

- AI 赋能股票分析/选股工具。
- 支持 A股、港股、美股。
- 支持行情获取、AI 热点资讯分析、AI 资金/财务分析、涨跌报警推送。
- 支持市场整体/个股情绪分析、AI 辅助选股。
- 数据全部保留在本地。
- 支持 DeepSeek、OpenAI、Ollama、LMStudio、AnythingLLM、硅基流动、火山方舟、阿里云百炼等。
- 近期更新包括股票异动数据、AI 推荐股票历史记录、AI 情感分析、研究报告工具函数、MCP 工具调用。

这是另一个非常贴近需求的参考。  
它的价值在于“本地化 + 多模型 + 三市场 + 涨跌报警推送 + AI 选股”。

可重点参考：

- 本地数据保存。
- 多模型兼容。
- 市场整体情绪与个股情绪分开。
- 涨跌报警/异动数据/AI 选股历史记录。
- MCP 工具调用。

需要警惕：

- README 明确说“仅供娱乐/学习研究”，说明风险边界必须保留。
- Go 桌面应用形态未必适合我们先做。

#### YoungCan-Wang/WyckoffTradingAgent

来源：[WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent)

GitHub API 快照：

- Stars: 505
- Language: Python
- Updated: 2026-06-19

README 重点：

- A股/港股/美股威科夫量价分析智能体。
- 支持 AI 研报、持仓风控、形态复盘、通知推送。
- 支持 React Web、CLI、MCP、GitHub Actions。
- Web 有“漏斗选股、形态复盘、持仓管理、单股分析”。

可重点参考：

- 量价结构识别 + AI 解释。
- CLI/Web/MCP/GitHub Actions 组合。
- 持仓管理和风控不是后期才想，而是从产品上就存在。

局限：

- 方法论偏 Wyckoff/量价结构，不覆盖基本面、催化剂和产业链。

#### xang1234/stock-screener

来源：[stock-screener](https://github.com/xang1234/stock-screener)

GitHub API 快照：

- Stars: 156
- Language: Python
- Updated: 2026-06-18

README 重点：

- 覆盖美国、香港、印度、日本、韩国、台湾、A股、德国、加拿大、新加坡、马来西亚、澳洲。
- 80+ filters。
- Minervini、CANSLIM、IPO、Volume Breakthrough、Setup Engine。
- 市场宽度、行业组排名、RRG rotation。
- AI chatbot、theme discovery from RSS/Twitter/X/news feeds。

可重点参考：

- Theme Discovery Pipeline：从新闻/社媒发现 trending 和 emerging themes，再跟踪 constituent stocks。
- Market Breadth：机会雷达不应孤立看股票，要知道市场环境支持不支持。
- 多市场架构。

局限：

- 系统复杂度高，不适合直接照搬。

#### bklieger-groq/stockbot-on-groq

来源：[stockbot-on-groq](https://github.com/bklieger-groq/stockbot-on-groq)

GitHub API 快照：

- Stars: 1,470
- Language: TypeScript
- Updated: 2026-06-14

README 重点：

- AI chatbot + live interactive stock charts, financials, news, screeners。
- TradingView widgets。
- 包含 market overview、top stories、stock screener、trending stocks、ETF heatmap。

可重点参考：

- 交互体验：聊天回答 + 动态组件 + 图表。
- 可以作为后续 UI 灵感，不是机会识别核心。

### 6. 低 star 但方向贴近的 catalyst scanner 项目

这些项目 star 很低，但关键词高度相关，值得观察需求形态：

| 项目 | 方向 | 借鉴点 |
| --- | --- | --- |
| [signal-hunter](https://github.com/UdayKumarVeera/signal-hunter) | React pre-catalyst stock scanner powered by agentic AI web search | “pre-catalyst” 关键词很准 |
| [stock-scanner-app](https://github.com/hilaln2210/stock-scanner-app) | real-time intelligence, FDA catalysts, social trending, AI briefings | 生物医药/FDA 催化剂是高价值垂直场景 |
| [raymond](https://github.com/smanderson721/raymond) | daily Gemini-scored news + weekly precondition scoring | 每日新闻评分 + 周度前置条件评分 |
| [cinder-vault-catalyst-2](https://github.com/rasgibbons21/cinder-vault-catalyst-2) | why stock is moving, catalyst strength 0-100 | “why moving + catalyst strength score” 很贴近 |
| [stock-gapper-discord-bot](https://github.com/vitran75/stock-gapper-discord-bot) | gappers scanner + news catalyst + Discord push | gapper + 新闻解释 + 自动推送 |
| [PennyRadar](https://github.com/JbourJabber/PennyRadar) | FDA, SEC filings, clinical trials, earnings catalyst scanner | penny stock / biotech catalyst 模板 |
| [TickTracker](https://github.com/banerjee-souvik/TickTracker) | FinBERT sentiment, materiality scoring, real-time dashboard | materiality scoring 这个词很关键 |

这些项目说明一个事实：市场上很多人想做“催化剂雷达”，但多数项目弱在数据质量、验证、持仓情景和风控。

## 数据源/接口扫描

### OpenBB

来源：[OpenBB GitHub](https://github.com/OpenBB-finance/OpenBB)、[OpenBB](https://openbb.co/)、[OpenBB MCP](https://openbb.co/products/odp/)、[openbb-mcp-server](https://pypi.org/project/openbb-mcp-server/)

定位：金融数据统一接入层，面向 analysts、quants、AI agents。  
可借鉴：

- “connect once, consume everywhere”。
- Python、REST API、Workspace、MCP 都可接。
- 对 agent 友好，尤其是 MCP。

适用性：

- 如果我们后续做 agent，OpenBB 适合做统一数据入口之一。

### Finnhub

来源：[Finnhub](https://finnhub.io/)、[Finnhub docs](https://finnhub.io/docs/api)

覆盖：

- 实时市场数据
- 全球公司基本面
- 经济数据
- alternative data
- earnings calendar
- company news sentiment

适用性：

- 适合做美股/全球股票轻量数据源。
- 需要对 earnings 日期、新闻质量做校验，不能盲信单一数据源。

### Financial Modeling Prep

来源：[FMP developer docs](https://site.financialmodelingprep.com/developer/docs)、[Stock News API](https://site.financialmodelingprep.com/developer/docs/stable/stock-news)、[Upgrades/Downgrades API](https://site.financialmodelingprep.com/developer/docs/stable/upgrades-downgrades-consensus-bulk)、[Press Releases API](https://site.financialmodelingprep.com/developer/docs/stable/press-releases)

覆盖：

- stock news
- financial statements
- historical prices
- analyst ratings / upgrades / downgrades
- press releases
- earnings/financial calendar 类数据

适用性：

- 很适合做 analyst revision / price target / upgrade-downgrade scanner。
- 也适合做公告/新闻/财务的一体化入口。

### Benzinga API / Massive

来源：[Benzinga APIs](https://www.benzinga.com/apis/)、[Stock News API](https://www.benzinga.com/apis/cloud-product/stock-news-api/)、[Massive Benzinga](https://massive.com/partners/benzinga)、[Massive news API](https://massive.com/docs/rest/stocks/news)

覆盖：

- real-time news
- earnings
- consensus ratings
- ticker news with summaries/source/sentiment analysis

适用性：

- 适合做“快新闻 + market-moving event”。
- 成本和授权需要后续评估。

### Options Flow 数据

来源：[Unusual Whales API/MCP 提示](https://unusualwhales.com/)、[Cheddar Flow](https://www.cheddarflow.com/)、[Barchart unusual activity](https://www.barchart.com/options/unusual-activity)

适用性：

- 适合做“资金是否提前反应”的增强信号。
- 第一阶段可以先只记录为外部观察，不作为核心判断。

## 视频/社媒扫描

### YouTube

本轮和上一轮结合，较有参考价值的视频主要来自产品官方或项目官方：

- [TrendSpider AI Strategy Lab](https://www.youtube.com/watch?v=S5DH_cEyLT4)
- [TradingAgents Demo](https://www.youtube.com/watch?v=90gr5lwjIho)
- [RD-Agent Quant Factor Mining](https://www.youtube.com/watch?v=X4DK2QZKaKY&t=6s)
- [QuantConnect Lean CLI videos](https://www.youtube.com/watch?v=QJibe1XpP-U&list=PLD7-B3LE6mz61Hojce6gKshv5-7Qo4y0r)
- [Cheddar Flow unusual options flow tutorial](https://www.youtube.com/watch?v=173KG47qw5c)
- [Unusual Whales API/custom flow alerts](https://www.youtube.com/watch?v=jlYo2536gPQ)

观察：

- 视频内容更偏使用教程和产品展示。
- 对我们有用的是交互形态：scanner、alerts、AI explanation、chart component、flow alerts。

### 小红书/中文内容平台

公开网页搜索可复核性仍然较差。可以观察到几个方向：

- “AI 选股”
- “AI 量化”
- “股票自动推送”
- “涨停/主升浪筛选”
- “热点题材 + 个股映射”

但本轮不建议把小红书内容当可靠技术依据。更适合后续人工样本标注：

| 标注项 | 目的 |
| --- | --- |
| 标题是否承诺收益 | 判断风险话术 |
| 是否展示数据来源 | 判断可信度 |
| 是否展示交易成本/失败样本 | 判断研究质量 |
| 是否诱导入群/付费 | 判断诈骗风险 |
| 是否解释为什么涨 | 判断是否有真实分析价值 |

## 风险与监管边界

这类产品很容易滑向“AI 荐股”。监管和投资者教育资料反复提醒：

- SEC Investor.gov 提醒不要仅依赖群聊/陌生人投资建议。
- FINRA 警告不法分子利用 AI 热度进行投资诈骗。
- SEC/NASAA/FINRA 联合提醒称“高收益低风险”是典型诈骗红旗。
- CFTC 明确提醒“AI 不会把交易机器人变成印钞机”。

来源：

- [SEC Investor Alerts](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins)
- [FINRA: AI and Investment Fraud](https://www.finra.org/investors/insights/artificial-intelligence-and-investment-fraud)
- [SEC/NASAA/FINRA AI investment fraud alert](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/artificial-intelligence-fraud)
- [CFTC: AI Won't Turn Trading Bots into Money Machines](https://www.cftc.gov/PressRoom/PressReleases/8854-24)
- [FTC Investment Scams](https://consumer.ftc.gov/articles/investment-scams)

这对产品设计的含义：

- 不能承诺“必涨”。
- 不能输出“无风险高收益”。
- 不能把历史命中率包装成未来保证。
- 必须保留来源、风险、失效条件和不确定性。
- 如果未来接入实盘，必须有人类确认、审计日志、额度限制和熔断。

## 从调研中抽象出的能力地图

### A. 机会发现输入

| 输入 | 例子 | 价值 |
| --- | --- | --- |
| 快新闻 | Benzinga/FMP/Finnhub/press release | 发现催化剂 |
| 财报日历 | earnings calendar | 提前监控窗口 |
| 财报超预期 | EPS/revenue surprise | 基本面催化 |
| 分析师上修 | upgrades/price target/revisions | 预期变化 |
| 价格异动 | gapper、breakout、relative strength | 市场反应 |
| 成交量异动 | relative volume, volume breakout | 资金确认 |
| 期权流 | sweeps/blocks/repeated hits | 潜在 smart money |
| 社媒/社区 | StockTwits/Reddit/X | 情绪与题材扩散 |
| 产业链新闻 | 客户 capex、订单、政策、供应短缺 | 受益映射 |

### B. 机会判断维度

| 维度 | 问题 |
| --- | --- |
| 新鲜度 | 这是不是新消息，还是旧闻重复？ |
| 重要性 | 对公司收入/利润/估值是否重要？ |
| 一阶受益 | 谁直接收钱？谁只是概念受益？ |
| 市场反应 | 股价/成交量/期权是否已经反应？ |
| 预期差 | 市场是否还没充分定价？ |
| 财务传导 | 利好进入收入、订单、毛利率、现金流还是估值？ |
| 技术状态 | 走势健康、刚突破、回调中、过热还是破位？ |
| 拥挤度 | 是否已经 FOMO？ |
| 风险 | 估值、财报窗口、流动性、竞争、宏观、事件失效 |

### C. 推送卡片形态

调研后更合理的卡片不应该是新闻摘要，而应该是：

```text
标的 / 代码
机会类型
触发时间
催化剂摘要
为什么可能涨
受益链条
证据来源
市场是否已反应
机会评分拆解
如果买了：
  乐观情景
  中性情景
  悲观情景
  失效信号
观察位 / 风险位 / 事件窗口
不确定性与反证
```

### D. 分类标签

为了避免“什么都推”，机会应分层：

- `breakout`: 股价/成交量突破
- `news-catalyst`: 突发新闻
- `earnings-surprise`: 财报超预期
- `analyst-revision`: 分析师上修
- `supply-chain`: 产业链受益
- `policy`: 政策催化
- `options-flow`: 期权流异动
- `pre-catalyst`: 事件前埋伏
- `theme-emerging`: 新兴主题扩散
- `mean-reversion`: 超跌反弹
- `crowded-risk`: 过热警报

## 当前最值得深入拆代码的参考项目

如果继续调研代码层，优先级建议：

1. [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)  
   最贴近“每天自动分析并推送决策仪表盘”。

2. [ArvinLovegood/go-stock](https://github.com/ArvinLovegood/go-stock)  
   最贴近“A/H/美股 + AI 选股 + 情绪 + 涨跌报警 + 本地数据”。

3. [xang1234/stock-screener](https://github.com/xang1234/stock-screener)  
   适合研究多市场 scanner、theme discovery、market breadth。

4. [YoungCan-Wang/WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent)  
   适合研究量价结构、持仓风控、CLI/Web/MCP/GitHub Actions 组合。

5. [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)  
   适合研究 hypothesis registry、research autopilot、安全边界和 MCP 工具化。

6. [Unusual Whales](https://unusualwhales.com/) / [Unusual Whales MCP/API](https://unusualwhales.com/public-api/mcp)  
   适合研究 options flow 如何被 agent 消费。

## 还需要继续补的调研

如果要再扩一轮，不应该泛搜，而应该按模块深挖：

1. 代码层拆解  
   重点看 `daily_stock_analysis` 和 `go-stock` 的数据源、评分、prompt、推送格式、错误处理。

2. 数据源可用性  
   对比 Finnhub / FMP / Benzinga / OpenBB / yfinance / AkShare / Tushare / TickFlow 的覆盖、成本、稳定性。

3. “为什么涨”数据集  
   看是否有公开数据能把新闻、异动、股价反应做成可训练/可评估样本。

4. 中文市场特殊性  
   A股要补：涨跌停、停牌、龙虎榜、公告、概念板块、北向/融资、游资/涨停梯队。

5. 推送体验  
   对比企业微信、飞书、Telegram、Discord、邮件、Web dashboard 的信息密度。

6. 风险合规  
   明确输出不能变成“保证涨/无风险/代客决策”。

## Sources

- [Benzinga Pro](https://www.benzinga.com/pro/)
- [Benzinga APIs](https://www.benzinga.com/apis/)
- [Danelfin](https://danelfin.com/)
- [Danelfin US stocks](https://danelfin.com/us-stocks)
- [Tickeron](https://tickeron.com/)
- [Trade Ideas Holly](https://www.trade-ideas.com/hollyguide/Who_is_Holly.html)
- [TrendSpider AI Strategy Lab](https://trendspider.com/product/artificial-intelligence-ai-trading-strategy-lab/)
- [Kavout K Score](https://www.kavout.com/k-score/)
- [Unusual Whales](https://unusualwhales.com/)
- [Unusual Whales Flow Alerts](https://unusualwhales.com/option-flow-alerts)
- [Cheddar Flow](https://www.cheddarflow.com/)
- [Tradytics](https://tradytics.com/)
- [Market Chameleon unusual options](https://marketchameleon.com/Reports/UnusualOptionVolumeReport)
- [Barchart unusual options](https://www.barchart.com/options/unusual-activity)
- [OpenBB](https://openbb.co/)
- [OpenBB GitHub](https://github.com/OpenBB-finance/OpenBB)
- [OpenBB MCP Server](https://pypi.org/project/openbb-mcp-server/)
- [Finnhub](https://finnhub.io/)
- [Financial Modeling Prep docs](https://site.financialmodelingprep.com/developer/docs)
- [FMP stock news API](https://site.financialmodelingprep.com/developer/docs/stable/stock-news)
- [FMP upgrades/downgrades consensus API](https://site.financialmodelingprep.com/developer/docs/stable/upgrades-downgrades-consensus-bulk)
- [Massive stock news API](https://massive.com/docs/rest/stocks/news)
- [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)
- [go-stock](https://github.com/ArvinLovegood/go-stock)
- [WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent)
- [stock-screener](https://github.com/xang1234/stock-screener)
- [stockbot-on-groq](https://github.com/bklieger-groq/stockbot-on-groq)
- [EOD-GPT](https://github.com/austin-starks/EOD-GPT)
- [signal-hunter](https://github.com/UdayKumarVeera/signal-hunter)
- [stock-scanner-app](https://github.com/hilaln2210/stock-scanner-app)
- [raymond](https://github.com/smanderson721/raymond)
- [cinder-vault-catalyst-2](https://github.com/rasgibbons21/cinder-vault-catalyst-2)
- [stock-gapper-discord-bot](https://github.com/vitran75/stock-gapper-discord-bot)
- [PennyRadar](https://github.com/JbourJabber/PennyRadar)
- [TickTracker](https://github.com/banerjee-souvik/TickTracker)
- [SEC Investor Alerts](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins)
- [FINRA AI and Investment Fraud](https://www.finra.org/investors/insights/artificial-intelligence-and-investment-fraud)
- [SEC/NASAA/FINRA AI Investment Fraud Alert](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/artificial-intelligence-fraud)
- [CFTC AI trading bot advisory](https://www.cftc.gov/PressRoom/PressReleases/8854-24)
- [FTC Investment Scams](https://consumer.ftc.gov/articles/investment-scams)
