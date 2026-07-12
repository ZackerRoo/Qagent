# A-share rules v1 sources

Validation window: 2023-01-03 through 2025-12-31. The checked-in schedule is
`backend/qagent/backtesting/a_share_rules_v1.json`.

## Price limits and order quantities

- SSE main-board and risk-warning rules: [SSE Trading Rules (2023 revision)](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779396.shtml)
- SSE STAR 20% limit and first five sessions without a limit: [SSE STAR trading Q&A](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20190719_4866745.shtml)
- SZSE main-board 10%, risk-warning 5%, 100-share buy lots, and the registered-IPO first-five-session rule: [SZSE main-board trading Q&A](https://www.szse.cn/www/investor/knowledge/t20230306_599093.html)
- SZSE ChiNext 20% limit and first five sessions without a limit: [SZSE ChiNext trading rules Q&A](https://investor.szse.cn/knowledge/t20200729_580056.html)
- BSE 30% limit, no limit on the first session, minimum 100 shares and one-share increments: [BSE trading-rules Q&A](https://www.bse.cn/important_news/200010675.html)
- ETF product-specific lot and price-limit metadata remain authoritative. The default schedule uses 100 shares and 10%; a product may explicitly select the exchange-published 20% rule.

## Settlement and fees

- Securities cannot be sold before settlement unless an exchange rule permits same-day turnover: [SSE Trading Rules, section 3.1](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779396.shtml)
- Supported same-day ETF categories include bond, gold, money-market, cross-border, and commodity-futures products: [SZSE fund trading rules](https://www.szse.cn/lawrules/rule/repeal/rules/P020231230545367191884.pdf)
- Stock stamp duty changed from 10 bps to 5 bps on the sell side from 2023-08-28: [State Taxation Administration announcement](https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448)
- Stock transfer fees are 0.001% (0.1 bps) on both sides from 2022-04-29: [Xinhua report of ChinaClear's notice](https://www.xinhuanet.com/2022-04/28/c_1128605983.htm)

Broker commission and minimum commission are not exchange constants. Callers must
provide them through `BrokerFeeRequest`; Qagent does not infer an account's negotiated
rate.

## Corporate actions

- Stock cash dividends, bonus shares, capitalization shares, record dates, ex dates,
  payment dates, and share-arrival dates use AKShare's documented CNInfo history
  interface: [AKShare stock data documentation](https://akshare.akfamily.xyz/data/stock/stock.html#id178).
- Rights issues use the same documentation's Sina rights-history interface. Rights
  events are retained as `rights` even though the current replay engine does not
  subscribe automatically.
- An empty successful response is recorded as `ready_none`; malformed or failed source
  evidence is `partial`; unsupported ETF action coverage is `unsupported`. Qagent does
  not manufacture missing announcement, payment, merger, or conversion dates.
- A delisted instrument without an authoritative cash or conversion settlement is
  reported as unresolved. It is not silently valued at the last close or at zero.
