# Moneyflow 有界可用性与单一候选假设（2026-09-18）

结论：现有采集已经保存资金流，当前六指标 Financial 排名没有消费它。最新真实归档满足单日字段完整性，可做离线敏感性检查；不能称连续资金流或选股收益增强。供应方单位及统计口径仍缺直接契约证据，本轮仅形成提案，不新增运行工具、数据调用、调度或排名接线。

## 当前证据

2026-09-18 通过只读 SSH 检查 `/var/lib/qagent-research/daily-financial` 根目录 JSON，最新含 `system_evidence` 的完整产物为：

- 路径：`/var/lib/qagent-research/daily-financial/20260917T111229-8ce862ff9092419ab5ce9d4cf4c004cb.json`。
- 文件 SHA256：`f9877c6431a8d6710ab4806a6268abd5f5d01e5c3c664b60469a73cc41e19598`。
- `result_digest`：`076a6accde341e40194f8e1ae37ed512291e7403aa04f02110c7a4fc4f59c595`。按仓库规范 JSON（sort_keys、紧凑 separators、默认 ASCII、拒绝 NaN）重算一致；20 个 moneyflow response digest 全部一致。
- source=`datahubco`，trade_date=`20260917`，period=`20260630`；采集 19:10:03.627534–19:12:29.166831（北京时间）。moneyflow 取得时间为 19:10:08.418233–19:12:27.755918。
- moneyflow 20/20 observed，每股恰好一行；股票身份与请求一致，日期均 20260917，字段集合一致。18 个数值字段全部有限，buy/sell 分项非负，大/特大单双边金额之和全部为正。Financial eligible 15/20，15 只均有资金流。
- 排除仍沿用 Financial：600028 财务修订冲突；601555、600928、002948 金融公司口径；002714 现金流/净利润不可用。资金流可用不恢复这些股票资格。

根目录共六份含 moneyflow 的产物：09-14 上午四份读取 09-11（8/20/20/20 股）、09-14 自然产物读取 09-14（20 股）、09-17 产物读取 09-17（20 股）。每股每份均一行。仅三个不同交易日期，集合也有变化；重复采集同日不计新增日期，09-15/16 不存在连续资金流证据，不据此构造连续净流入或历史 PIT。

## 字段、单位与消费缺口

原始字段共20个：`ts_code`、`trade_date`，以及 `buy/sell × sm/md/lg/elg × vol/amount` 共16项，加 `net_mf_vol`、`net_mf_amount`。`collect_daily_documented_research.py::batch_params` 当前按单股单日请求 moneyflow（limit 12），保存原始证据；`rank_financial_candidate.py::METRICS_V2/selected_metrics` 只取六个财务指标，未消费资金流。

[Tushare moneyflow 官方字段参考](https://tushare.pro/document/2?doc_id=170)定义 amount 为万元、vol 为手；sm/md/lg/elg 为小、中、大、特大单，并指出 net_mf 是主动买卖单净值，不能简单把大小单总和相减。这里只将其作为同名接口参考：Datahubco 当前归档没有单位元数据，没有直接证明转发与该契约完全同义，不把官方参考当作供应商实测单位。不得把 net_mf 称为机构真实资金、账户资金或用分档差验证其必然相等。

## 唯一固定提案

假设：同一 Financial 合格集合中，单日大/特大单买卖金额偏向能补充财务质量信息。固定研究特征为

`M = (buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount) / (buy_lg_amount + buy_elg_amount + sell_lg_amount + sell_elg_amount)`。

这是自定义分档金额差比，不是 `net_mf_amount`，也不是多日持续性。分子分母必须来自同源同股同日、单位一致的四个非负有限数值且分母正；缺失不填零。即使公共单位在比值中抵消，也须确认四项统计口径一致。

只提议一个固定候选：复用当前同集合百分位公式，把 M 作为 higher-is-better 的第七项，七项各1/7，精确分数排序、同分按股票ID。原六指标作为消融对照；双方使用完全相同的财务和资金流完整集合，保留共同排除及覆盖，不拿不同集合收益作改善证据。不搜索权重、窗口或多种资金因子。

唯一预期消费者为已有 Financial lane，后续若实施应版本化替换其候选协议、复用原 daily/forward 存储、first-seal、行业市值配平及到期 evaluator，不增加长期候选服务或模拟账户。原 G2 冻结模型与每10交易日采样不变。

## 一次性敏感性检查（非前向信号）

内存中对09-17归档的同15股，以归档 `score_exact` 恢复六项总分，加入上述唯一固定百分位；未读取收益或挑选权重，未写远端文件：

| 排序 | Top5（顺序） |
| --- | --- |
| 原六指标 | 600368、000975、002648、603369、600900 |
| 七指标示意 | 000975、600368、600900、603369、002648 |

Top5 集合重叠5/5，只改变内部顺序；七项前三名精确分数均为65/98，股票ID决定顺序。这是观察数据后的 illustrative sensitivity，不是当日事前冻结协议、合法 forward、收益结论或供应方语义验收。不得回写09-17归档。

主任务随后独立在云端以Fraction重算，Top5顺序及前三65/98均一致；独立复核仍不赋予历史前向信号资格。

可复核的15股精确结果如下；原分数来自归档 `financial_candidate.rankings[].score_exact`，不重新改变六项指标资格。

| 股票 | M | 六项score_exact | 七项score_exact |
| --- | --- | --- | --- |
| 600368.SH | -91907/973423 | 59/84 | 65/98 |
| 000975.SZ | 94791/4001717 | 55/84 | 65/98 |
| 002648.SZ | -749557/6290005 | 55/84 | 59/98 |
| 603369.SH | -74539/669313 | 55/84 | 30/49 |
| 600900.SH | 8709/328129 | 9/14 | 65/98 |
| 300760.SZ | -257393/1959984 | 17/28 | 53/98 |
| 603979.SH | -75547/1816405 | 43/84 | 26/49 |
| 600795.SH | 54343/694388 | 10/21 | 27/49 |
| 603871.SH | -32629/271701 | 10/21 | 43/98 |
| 605507.SH | -54169/580004 | 10/21 | 47/98 |
| 002215.SZ | 58809/938446 | 5/12 | 47/98 |
| 300079.SZ | 32429/435631 | 5/14 | 43/98 |
| 688309.SH | -218168/924037 | 5/14 | 31/98 |
| 600547.SH | -1939461/30517109 | 1/3 | 18/49 |
| 002746.SZ | -34738/108315 | 5/28 | 15/98 |

最小重现伪代码（仅读取上述归档到内存，不发请求或输出文件）；摘要算法复用 `research_cashflow_quality.digest`，百分位复用 `rank_financial_candidate.rank_candidate` 的定义：

```python
from fractions import Fraction as F
# d = json.loads(已核验SHA的归档原文)
# 先校验d去掉result_digest后的规范JSON摘要及20个response摘要。
rows = d["financial_candidate"]["rankings"]
xs = {}
for row in rows:
    s = row["stock_id"]
    m = d["system_evidence"][s]["moneyflow"]["response"]["rows"][0]
    a, b, c, e = (F(str(m[k])) for k in (
        "buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"))
    xs[s] = (a + b - c - e) / (a + b + c + e)
result = []
for row in rows:
    s = row["stock_id"]
    x = xs[s]
    p = F(2 * sum(v < x for v in xs.values())
          + sum(v == x for v in xs.values()) - 1, 2 * (len(xs) - 1))
    score = (6 * F(row["score_exact"]) + p) / 7
    result.append((score, s))
result.sort(key=lambda pair: (-pair[0], pair[1]))
print(result[:5])
```

## 验收与停止边界

实施前确认供应方字段契约并冻结新协议/代码摘要；只使用未来首次自然合格采集，同日盘后封存。复用原次交易日调整开盘、5/10/20交易日调整收盘、沪深300同窗和固定往返10bps口径；价格质量、完整Top5、行业市值配平和可判别要求不放宽。

先验收同集合覆盖、确定性重放、Top5变化、自然可判别配对，再等首个完整5日结果决定是否值得继续，10/20日保持原观察窗口。报告六/七指标净收益差、原matched lift、换手与行业暴露；部分样本或仅重排不算收益改善，不自动晋级。

供应方语义未确认时停在离线提案；未来若连续五个自然信号不合格、无可判别对照，或首个可判别完整5日结果不支持继续，停止该增强的新观察，不调整权重追逐结果。唯一 Financial 消费者不采用时不增加永久工具/调度，只保留此报告和原审计证据；不停止或删除现有 Financial 基础链路与历史账本。

本轮已完成只读归档核验和一次性内存计算；仅新增本文，未新增代码/测试、未提交、未push、未部署，未调用供应商数据API、未写云端、未改正式Ranking或交易权限。

## 同轮运行快照（主任务独立核验）

主任务于09-18 06:41–06:45 UTC只读核验 current=`edbd9fa`、health ok。06:40 slot实际06:41:12.262763启动（迟到72.262763秒）、06:41:49.620261完成，attempts14/completed13、error null。最新06:20/06:30/06:40三个slot各9 checked/9 resolved，minute_rows分别10534/10602/10676、daily fallback均0；9个active持仓latest_date均09-18。日期更新和分钟行数不能证明逐只分钟无延迟，也不消除单次启动迟到事实。

同次金融研究目录计数为daily12（含调度/历史产物，最新09-17）、signals2（最新09-17）、financial-daily-ranks0。此时北京时间14:45，尚未到16:40收盘后研究时段，不能把上午尚无新baseline视为当日自然采集失败。本轮无生产写操作；本节数字来自主任务独立审计，与本文资金流归档计算分别取证。

## 同轮最小实现与验证（主任务集成记录）

分钟行情新增观测字段以记录逐只时间与延迟，不改变交易语义；实施子任务专项61 passed。provider-status读取最近20条health的实现收敛读取范围；实现子任务云端单次只读比较、主任务复核结果为32,715,801→1,397,149字节，SQL 0.271318→0.031141秒、parse 0.665141→0.023810秒、health_equal。真实API返回200/0.419146秒，本轮未复现15秒超时，因此不能确证此前超时唯一根因，也不把单次样本当长期性能保证。

主任务联合回归109 passed、1项warning（7.69秒）；最终backend全量2818 passed、3项既有warnings（238.22秒、exit0），Ruff与diff检查通过。本节代码由其他实施子任务完成，本文作者未修改运行代码。本轮新增实施已实现、已完成上述本地测试，未commit、未push、未部署；自然逐股分钟时效仍待新观测能力部署后验收。

子任务生命周期：主任务已消费两个代码实施子任务结果并执行interrupt关闭；会话终态task_complete已核验，但lsof仍显示PID41183打开会话，因此没有删除会话存储。文档子任务结束后同样先核验终态和打开状态，仅在安全条件满足后清理；不能把关闭任务写成已删除session，cleanup hook保持启用。
