# Qagent Product Design

Date: 2026-06-21  
Status: Working design for implementation planning  
Scope: US stocks + China A-shares, daily scanning + key intraday alerts, free/low-cost data during development, web dashboard + agent conversation  

## 1. Product Definition

Qagent is a stock opportunity decision system for US equities and China A-shares. It is not a news bot, not a black-box price predictor, and not an auto-trading system.

The product turns market data, events, fundamentals, technical signals, and user holdings into verifiable opportunity cards:

- what stock is worth watching;
- why it may move;
- whether it is actionable now;
- where the entry trigger is;
- where not to chase;
- where the stop/invalidation level is;
- where targets and trailing exits are;
- what to monitor after entry;
- how similar historical signals performed.

The first implementation should provide a complete research and monitoring loop, not partial isolated features:

```text
market scan
-> signal generation
-> opportunity ranking
-> opportunity card
-> entry/exit/risk plan
-> watchlist/portfolio monitoring
-> alert lifecycle
-> outcome tracking and review
```

## 2. Explicit Non-Goals

The first implementation will not:

- place trades automatically;
- provide personalized financial advice;
- promise returns or guaranteed price moves;
- rely on black-box LLM-only buy/sell calls;
- run high-frequency intraday strategies;
- use options flow as a standalone recommendation signal;
- require paid data APIs to run in development.

These boundaries matter because the system must be testable, explainable, and safe to iterate.

## 3. Target User Workflow

### 3.1 Daily Workflow

Before or after the trading session, the user opens the dashboard and sees:

- market regime for US and A-share markets;
- top opportunity cards;
- stocks approaching buy triggers;
- stocks that are overextended and should not be chased;
- watchlist changes;
- holding alerts;
- newly invalidated ideas;
- outcome updates for previous opportunity cards.

### 3.2 Intraday Workflow

During market hours, the system only alerts on high-value events:

- price breaks an entry trigger;
- price violates a stop or invalidation level;
- unusual volume confirms a breakout;
- A-share涨停/跌停 or near-limit event occurs;
- major company news/announcement appears;
- important earnings/filing/公告 event arrives;
- a holding reaches target or elevated risk state.

Intraday alerts should be sparse and actionable. They should not stream every headline.

### 3.3 Research Conversation

The user can ask:

- why is this stock on the list;
- what needs to happen before entry;
- what would invalidate the thesis;
- how similar signals performed historically;
- what changed today for my holdings;
- compare this stock with another opportunity;
- show only A-share policy/industry catalyst ideas;
- show only US growth stocks with pullback setups.

The agent should answer from structured data first, then explain with market research context.

## 4. Market Model

Every instrument must be represented with explicit market metadata.

```yaml
instrument:
  id: US:AAPL
  market: US
  symbol: AAPL
  exchange: NASDAQ
  name: Apple Inc.
  asset_type: common_stock
  currency: USD
  timezone: America/New_York
  trading_calendar: XNYS
```

```yaml
instrument:
  id: CN:300750
  market: CN
  symbol: "300750"
  exchange: SZSE
  name: 宁德时代
  asset_type: a_share
  currency: CNY
  timezone: Asia/Shanghai
  trading_calendar: XSHG_XSHE
```

Do not treat ticker strings as globally unique. The same symbol-like value can mean different things across markets, providers, and exchanges.

## 5. Market-Specific Behavior

### 5.1 US Stocks

US-specific capabilities:

- regular, pre-market, and after-hours fields when available;
- SEC filings and 8-K/10-Q/10-K events;
- earnings dates and earnings surprises;
- analyst revisions when free sources allow it;
- sector/industry relative strength;
- optional options-flow confirmation later.

US-specific risks:

- free data may be delayed;
- analyst estimates may be incomplete without paid APIs;
- pre-market data availability differs by provider;
- news timestamps can be noisy.

### 5.2 China A-Shares

A-share-specific capabilities:

- 涨停/跌停 detection;
- near-limit alerts;
- trading suspension awareness when data allows;
- company公告 and 巨潮资讯/交易所 announcements;
- policy/sector catalyst tagging;
- 北向资金 and 龙虎榜 as later confirmation features;
- board/industry heat when data source supports it.

A-share-specific risks:

- 涨跌停 changes entry/exit realism;
- 公告 timestamps and effective trading windows must be handled carefully;
- free data sources can break or change schemas;
- topic/theme data is noisy and needs conservative scoring.

## 6. Data Source Strategy

Development uses free or low-cost sources. The architecture must allow paid providers later.

### 6.1 Initial Development Providers

US:

- `yfinance` for daily/intraday price where available;
- `stooq` or similar fallback for historical daily bars;
- SEC EDGAR for filings;
- Alpha Vantage free tier where useful;
- Finnhub free tier if API key is available;
- public Nasdaq/company IR pages only when stable enough.

China A-shares:

- `akshare` for market data, announcements, fund flow-like public datasets where available;
- `baostock` for daily historical bars and fundamentals;
- 巨潮资讯/交易所公告 pages where accessible;
- 东方财富 public endpoints only behind provider adapters.

### 6.2 Provider Abstraction

All providers must implement narrow interfaces:

```python
class MarketDataProvider:
    def get_daily_bars(instruments, start, end): ...
    def get_intraday_bars(instruments, interval, start, end): ...
    def get_snapshot(instruments): ...

class FundamentalsProvider:
    def get_company_profile(instruments): ...
    def get_financial_metrics(instruments): ...

class EventProvider:
    def get_earnings_events(instruments, start, end): ...
    def get_news_events(instruments, start, end): ...
    def get_filings_or_announcements(instruments, start, end): ...
```

Provider adapters return normalized records and include:

- provider name;
- retrieval timestamp;
- source timestamp;
- delay estimate;
- confidence/data quality flags;
- raw provider symbol.

### 6.3 Data Quality Policy

Every opportunity card must show data caveats when relevant:

- delayed market data;
- missing estimates;
- missing intraday bars;
- stale fundamentals;
- incomplete announcement data;
- provider error or fallback.

Signals should degrade gracefully. Missing data should reduce confidence, not silently fabricate conclusions.

## 7. Core Domain Objects

### 7.1 Signal

A signal is a structured observation, not a recommendation.

```yaml
signal:
  id: sig_...
  instrument_id: US:NVDA
  signal_type: earnings_drift | analyst_revision | trend_strength | pullback | breakout | catalyst | limit_up | volume_anomaly
  direction: bullish | bearish | neutral
  observed_at: 2026-06-21T21:00:00Z
  horizon: 5d | 20d | 60d
  score: 0.0-1.0
  evidence:
    - metric: price_above_50dma
      value: true
    - metric: revenue_surprise
      value: 8.4%
  data_sources:
    - provider: yfinance
      delay: delayed
```

### 7.2 Opportunity Card

The opportunity card is the primary product artifact.

```yaml
opportunity_card:
  id: card_...
  instrument_id: US:NVDA
  status: new_idea | watch | setup_ready | triggered | extended | active | risk_elevated | invalidated | closed | postmortem_done
  market: US
  horizon: 2-8 weeks
  generated_at: ...
  thesis: ...
  catalyst_summary: ...
  signal_stack:
    - signal_id: sig_...
  scores:
    catalyst: 0.72
    fundamental: 0.66
    technical: 0.81
    valuation: 0.48
    timing: 0.74
    risk: 0.39
    historical_edge: 0.57
  entry_plan: ...
  exit_plan: ...
  risk_reward: ...
  monitoring_plan: ...
  alert_rules: ...
  historical_edge: ...
  audit: ...
```

### 7.3 Watchlist Item

```yaml
watchlist_item:
  instrument_id: CN:300750
  user_tags: [新能源, 核心资产]
  thesis: ...
  status: watching | setup_ready | triggered | invalidated
  linked_card_ids: [...]
  active_alert_ids: [...]
```

### 7.4 Position

Positions may be manually entered in the first implementation.

```yaml
position:
  instrument_id: US:AMD
  shares: 100
  entry_price: 164.20
  entry_date: 2026-06-10
  strategy_tag: breakout
  initial_stop: 152.80
  current_stop: 156.40
  target_1: 182.00
  target_2: 198.00
  thesis: ...
```

### 7.5 Outcome Record

```yaml
outcome:
  card_id: card_...
  signal_date: 2026-06-21
  triggered: true
  trigger_date: 2026-06-24
  return_1d: ...
  return_5d: ...
  return_10d: ...
  return_20d: ...
  return_60d: ...
  max_favorable_excursion: ...
  max_adverse_excursion: ...
  stop_hit: false
  target_1_hit: true
  invalidated: false
  failure_reason: null
```

## 8. Signal Families

### 8.1 First Implementation Signals

The first implementation should include a small number of high-quality, explainable signals:

1. Trend strength and relative strength.
2. Healthy pullback to key moving averages.
3. Breakout setup and breakout trigger.
4. Earnings drift where data supports it.
5. Volume anomaly.
6. A-share limit-up/near-limit and high-volume breakout.
7. Event/catalyst tagging from structured public events.

These are enough to produce useful cards while keeping data dependencies realistic.

### 8.2 Deferred Signals

These should be designed for but not required initially:

- analyst estimate revisions;
- options-flow confirmation;
- insider transactions;
- 13F changes;
- buyback execution;
- 北向资金;
- 龙虎榜;
- advanced NLP event extraction;
- intraday VWAP/ORB setups.

## 9. Entry and Exit Rules

LLM output must not invent prices. Prices come from deterministic rule functions.

### 9.1 Entry Types

Breakout entry:

- trigger: close or intraday cross above pivot/resistance;
- confirmation: volume above recent average;
- no-chase level: trigger price plus ATR or percentage extension;
- invalidation: failed breakout below pivot or support.

Pullback entry:

- trigger: price returns to 20DMA/50DMA/support zone and reclaims short-term strength;
- confirmation: improving volume or reversal candle;
- invalidation: close below support/50DMA or recent swing low.

Earnings/event confirmation entry:

- trigger: post-event price holds gap/support and estimates/events do not reverse;
- confirmation: no immediate gap fill, volume support, sector strength;
- invalidation: gap failure or negative follow-up event.

A-share limit-up related entry:

- trigger: not buying blindly at涨停; card marks next-day watch conditions;
- confirmation: board/sector continuation, volume quality, no immediate limit-down reversal;
- invalidation: failed continuation, high open low close, or sector reversal.

### 9.2 Exit Types

Every card needs:

- initial stop;
- thesis invalidation;
- target 1;
- target 2 or measured move;
- trailing stop rule;
- time stop;
- event-risk handling.

### 9.3 Risk Reward

Minimum risk checks:

- reject or mark low quality if reward/risk < 2:1;
- warn if stop distance is too wide;
- warn if price is overextended from 20DMA/50DMA;
- warn if liquidity is weak;
- warn if market regime is defensive.

## 10. Alert System

Alerts are generated from cards, watchlist items, and positions.

Alert categories:

- entry trigger;
- no-chase/overextended;
- stop/invalidation;
- target reached;
- trailing stop update;
- volume anomaly;
- earnings/filing/公告 event;
- A-share涨跌停;
- watchlist status changed;
- holding risk changed.

Alert lifecycle:

```text
pending -> triggered -> acknowledged -> closed
pending -> expired
pending -> invalidated
```

The first implementation can store alerts locally and display them in the dashboard. External notifications can come later.

## 11. Dashboard Design

The dashboard should be an operational workspace, not a marketing page.

Primary layout:

- left navigation: Overview, Opportunities, Watchlist, Portfolio, Alerts, Review, Settings;
- top bar: market selector, date/session state, data health;
- main content: dense tables and cards;
- right-side agent panel: ask contextual questions about selected card/position.

Key pages:

1. Overview:
   - US market regime;
   - A-share market regime;
   - top opportunities;
   - triggered alerts;
   - holding risks;
   - data health.

2. Opportunities:
   - sortable opportunity table;
   - filters by market, status, signal type, sector, risk;
   - selected opportunity detail card.

3. Watchlist:
   - user tracked symbols;
   - linked opportunity cards;
   - active rules;
   - status changes.

4. Portfolio:
   - manually entered holdings;
   - PnL and risk state;
   - stop/target progress;
   - event risks.

5. Alerts:
   - pending/triggered/closed alerts;
   - reason and linked object;
   - acknowledgement.

6. Review:
   - historical outcomes;
   - signal performance;
   - failure reasons;
   - strategy scorecards.

7. Settings:
   - data provider config;
   - market universes;
   - alert thresholds;
   - risk preferences.

Visual style should follow mature trading/research tools: dense, calm, table-forward, minimal decoration, clear status colors, no landing-page hero.

## 12. Agent Design

The agent should operate as a research assistant over structured state.

It can:

- explain opportunity cards;
- compare candidates;
- summarize why an alert triggered;
- convert a user request into filters;
- create watchlist rules;
- summarize position risks;
- generate postmortem notes.

It cannot:

- fabricate missing prices;
- override rule-engine stops;
- claim certainty;
- execute trades;
- hide data caveats.

Agent responses must reference the structured object behind the answer when possible.

## 13. Data Flow

Daily scan:

```text
load universe
-> fetch daily bars/fundamentals/events
-> normalize data
-> compute indicators
-> generate signals
-> rank opportunities
-> generate/update cards
-> update alerts
-> update outcome records
-> render dashboard
```

Intraday scan:

```text
load active cards/watchlist/positions
-> fetch snapshots/intraday bars
-> evaluate trigger rules
-> create/update alerts
-> update selected card status
```

Agent query:

```text
user question
-> identify selected context or search objects
-> retrieve structured records
-> optionally retrieve research docs
-> generate constrained explanation
```

## 14. Persistence

Development can use SQLite for simplicity. The schema should be designed so PostgreSQL can replace it later.

Core tables:

- instruments;
- market_bars_daily;
- market_bars_intraday;
- fundamentals_snapshot;
- events;
- signals;
- opportunity_cards;
- watchlist_items;
- positions;
- alert_rules;
- alerts;
- outcomes;
- agent_notes;
- data_quality_logs.

Raw provider payloads should be cached when practical for reproducibility, but product logic must use normalized tables.

## 15. Error Handling

Provider failure:

- mark provider unavailable;
- use fallback if available;
- mark affected cards with data caveat;
- avoid generating high-confidence cards from incomplete data.

Missing fields:

- compute partial signals;
- lower score or mark not enough evidence;
- surface missing field in audit section.

Market calendar mismatch:

- use market-specific calendars;
- avoid US/A-share date assumptions;
- do not evaluate intraday alerts outside active sessions unless using extended-hours data.

LLM failure:

- cards remain valid from deterministic data;
- explanation may be unavailable;
- dashboard should still show raw signal and plan.

## 16. Testing Strategy

Unit tests:

- instrument normalization;
- moving averages and indicators;
- entry/exit rule calculations;
- score aggregation;
- alert lifecycle;
- market calendar behavior;
- A-share limit detection;
- US breakout/pullback detection.

Integration tests:

- provider adapter returns normalized data;
- daily scan creates cards;
- intraday scan triggers alerts;
- outcomes update after future bars;
- dashboard API returns expected payloads.

Golden-data tests:

- fixed small US sample;
- fixed small A-share sample;
- expected card states;
- expected alert triggers.

Backtest/event-study validation:

- no future data leakage;
- signal timestamp preserved;
- 1/5/10/20/60 day returns computed from subsequent bars;
- benchmark-relative returns computed per market.

## 17. Implementation Slices

Although the product is designed as a complete system, implementation should be sliced by working verticals:

1. Foundation:
   - repo scaffold;
   - database schema;
   - provider abstraction;
   - instrument model;
   - market calendars.

2. Data ingestion:
   - US daily bars;
   - A-share daily bars;
   - snapshots where available;
   - basic company profile.

3. Signal engine:
   - trend strength;
   - pullback;
   - breakout;
   - volume anomaly;
   - A-share limit status.

4. Opportunity cards:
   - scoring;
   - card generation;
   - entry/exit/risk plan;
   - audit metadata.

5. Monitoring:
   - watchlist;
   - manual positions;
   - alert rules;
   - intraday/key trigger scan.

6. Review:
   - outcome tracking;
   - historical signal result table;
   - failure tags.

7. Dashboard and agent:
   - web dashboard;
   - contextual agent panel;
   - API endpoints.

## 18. Acceptance Criteria

The first complete implementation is acceptable when it can:

- load at least one US universe and one A-share universe;
- fetch daily bars for both markets from free providers;
- compute trend, pullback, breakout, volume, and A-share limit signals;
- generate opportunity cards with status, entry, stop, target, no-chase, and risk/reward fields;
- display opportunity cards in a web dashboard;
- allow manual watchlist and position input;
- evaluate key intraday or latest-snapshot alerts;
- record outcomes for 1/5/10/20/60 trading-day horizons;
- show data source and caveat metadata;
- answer basic agent questions against card/position data;
- run tests for core rule logic and alert lifecycle.

## 19. Open Decisions for Later

These do not block implementation planning:

- exact paid data provider for production;
- external notification channels;
- broker import integrations;
- auto-refresh deployment environment;
- full NLP event extraction;
- options-flow vendor;
- mobile UI.
