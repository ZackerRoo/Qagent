# Challenger 成熟结果与覆盖核验（2026-09-11）

结论：两条显式候选已有部分 5 日标签，但完整执行头配对仍为 0；不能据此决定换策略。与此同时，已有 Regularized LightGBM 研究 lane 的三个 5 日执行头完整配对确实存在，且平均净超额好于它自己的基线。这是值得保留的早期证据，不是策略有效或可接管账户的结论。

## 取证范围与时间

- 2026-09-11 07:16 UTC（北京时间 15:16）开始本轮只读查询云端 SQLite；独立覆盖查询事务起点为 `2026-09-11T07:17:17.829765+00:00`。在线自然任务仍可能继续更新，本文是快照。
- 使用 `sqlite3.connect("file:/var/lib/qagent/qagent.db?mode=ro", uri=True)`，每个连接执行 `PRAGMA query_only=ON`；独立 SQL 查询加 `BEGIN` 固定读事务。没有调用行情、补价、resolver、scheduler GET、训练、交易、部署或写云端数据库。
- 复用线上 release `94cf6f5057723460a88becd0c5e44f864a6cc53c` 的 `build_factor_shadow_roster`、`factor_shadow_outcome_dates`。实际 `provider_mode='free'`。独立 SQL 与标准库重算覆盖和三批执行头结果。
- 主口径固定已结束的上一交易日 `as_of_date=2026-09-10`，价格采用本次查询时缓存。另列 09-11 日期到期口径：当日收盘后不久尚无所需当日缓存，不能混进历史缺价归因。
- 所有收益均为研究标签：信号次交易日调整开盘价入场、固定观察窗口调整收盘价出场，相对 `CN:000300.IDX` 超额，减固定往返成本 10 bps（0.1 个百分点）。不是实际成交、账户累计收益或年化收益。

## 复用的候选与分母

|简称|实验 ID|身份|
|---|---|---|
|趋势|`factor-research-4b6806471bf94a5eb54eeee17be38332`|`trend-health-composite-v1` 显式 shadow|
|量能|`factor-research-a368cf7a899b4a94884aafcc9a06762f`|`turnover-volume-strength-v1` 显式 shadow|
|Regularized|`factor-research-b6a9a102cbbd4f1a9cd5ff4c1b504f07`|roster 保留的历史研究 lane；不是 paper legacy 账户|

趋势与量能各有 20 次原始扫描、110,707 条 scores，覆盖 09-01 至 09-10 的 8 个信号日。按现有协议，每个实验每个信号日只保留 `min(created_at)` 最早扫描，平局用 `scan_job_id` 排序；得到各 44,286 个股票×信号日评分槽位。重复扫描不能算新增独立样本，也不能把股票×日期数当作独立交易日数。数据库中更早的 `b60db925…` 实验不是当前 roster 的研究 lane，本报告不混合其标签。

|lane / 观察窗口|截至 09-10 到期日数|已算 / 已到期评分槽位|覆盖|完整执行头日对数|
|---|---:|---:|---:|---:|
|趋势 / 5 日|3|3,688 / 16,601|22.2155%|0|
|量能 / 5 日|3|3,688 / 16,601|22.2155%|0|
|Regularized / 5 日|3|16,623 / 16,646|99.8618%|3|
|Regularized / 10 日|1|4,739 / 5,548|85.4182%|0|

趋势、量能的 10/20 日尚无到期批次；其最早 10 日到期为 09-15，20 日为 09-30（既有交易日历）。Regularized 的 20 日也未到期。日期到期不保证当日标签已经解析完成。

如果 `as_of_date` 改为 09-11，趋势、量能各增加 09-04 信号的 5,535 个当天到期槽位，总分母 22,136，标签仍 3,688，覆盖 16.6606%；Regularized 的 10 日分母增加至 11,097，覆盖 42.7052%。本次缓存没有这些槽位所需的 09-11 行，也没有沪深 300 当日基准行；单列为当天待数据/待解析，不声称是历史补价失败。

## 未算不等于缺价

下表适用于趋势和量能各自的 5 日 canonical cohort，两者数据完全一致。三行已算合计 3,688，未算合计 12,913。

|信号日 → 到期日|评分槽位|已算|未算且入/出日任一无缓存行|未算且两行存在但所需调整价非正/空|未算且两项调整价均正|
|---|---:|---:|---:|---:|---:|
|09-01 → 09-08|5,533|2,293|11|5|3,224|
|09-02 → 09-09|5,534|703|11|2|4,818|
|09-03 → 09-10|5,534|692|12|8|4,822|
|合计|16,601|3,688|34|15|12,864|

所需基准日期的调整开盘/收盘价均存在且为正。可确认多数未计算标签对应的两项股票价格数值已经在缓存；不能把 12,913 全称为缺价。这里的“均正”只是 SQL 数值检查，没有替代 `_unsafe_exact_row` 的完整 OHLC/调整口径质量检查，也未核查 resolver 当轮预算和处理日志，因此不能进一步断言 12,864 条均可直接解析或都是调度故障。34 条无行也不能推断停牌。

Regularized 三批 5 日缺少的 23 条全部落在入/出日无缓存行（6、8、9）；其已到期 10 日缺少的 809 条为 12 无行、2 调整价不足、795 两项价格为正但未计算。未成熟标签不进入上述缺失分母。

### 09:13–09:14 UTC 完整价格质量复核

保留上面的 07:17 UTC 数值快照。本轮随后通过新增只读脚本 `scripts/diagnose_shadow_cached_labels.py`，在云端 release 的 `PYTHONPATH` 下调用真实 `_unsafe_exact_row`：趋势快照起点为 2026-09-11 09:13:22 UTC，量能为 09:14:00 UTC。两次均使用 `mode=ro`、`PRAGMA query_only=ON`、`BEGIN`，只查指定候选 canonical 扫描中截至 09-10 已成熟的 5 日未算标签及所需股票、基准缓存；没有调用行情、补价或 resolver，没有写数据库。

两条候选得到完全相同的下列分类。每个槽位按“任一无行 → 所需价格非正/空或非有限 → 真实质量规则拒绝 → 全部可用”的优先级归类，分类互斥；各批基准入场和出场价格均为 `ready_cached`。

|信号日|无缓存行 `missing`|所需价不足 `nonpositive`|两项价正但质量拒绝 `unsafe`|完整质量通过 `ready_cached`|
|---|---:|---:|---:|---:|
|09-01|11|5|3,223|1|
|09-02|11|2|4,805|13|
|09-03|12|8|4,809|13|
|合计|34|15|12,837|27|

因此，原先两价均正的 12,864 个槽位中，12,837 个未通过既有价格质量规则，只有 27 个在这次快照可直接使用缓存。多数未算标签已有明确的质量拒绝原因，不能解释为 resolver 遗漏。实例 `CN:000408` 的 09-02 入场缓存为 `fuyao_realtime`、`qfq`，但 `adjusted_source_provider` 为空；09-08 出场缓存已有可信配对，不能用出场质量替代入场来源证明。该实例说明一种实际拒绝原因，不代表所有 unsafe 槽位都只有同一问题。

源码中 `resolve_factor_shadow_outcomes` 先执行补价，再遍历所有未算槽位读取缓存；补价预算正常耗尽返回后，缓存结算仍会执行，并无按剩余网络预算跳过缓存标签的分支。因此本轮未证实“预算耗尽导致缓存标签被跳过”的代码缺陷。27 个 `ready_cached` 保留为待自然周期验证，不把查询时缓存状态当作此前 resolver 执行时状态，也不提前宣称标签已生成。

下一步只核验自然周期是否处理这 27 个槽位，以及既有补价是否带来可信配对；不放宽 `_unsafe_exact_row`，不把无来源的正价强行转成收益，不重训或更改冻结候选。新增能力仅为只读诊断，实际模型和交易保持不变。诊断脚本已实现，两个隔离专项测试通过；主任务综合回归 32 项通过（2.71 秒），Ruff 与 diff 检查通过。脚本已通过云端 stdin 只读执行验证，但未安装到生产 release，未提交、未 push、未部署。

## 已成熟成绩能支持什么

### 新候选：只能看部分标签上的诊断

既有 Top10% 评估先按信号时排名固定名单，再取已存在标签计算。每个信号日名单 554，三日总名单槽位 1,662。基线已算 459，趋势已算 290，量能已算 514，选择完成不代表收益完成。

|三日部分样本指标|共同基线|趋势|量能|
|---|---:|---:|---:|
|已算 Top10% 标签平均净超额|+0.419147%|+0.638028%|-1.074740%|
|已算交集上的日均 rank IC|0.039628|0.075866|-0.032751|
|候选日净超额高于基线|—|2/3|1/3|
|新增减移除的日 selection-lift 中位数|—|-0.498380 pp|-2.865125 pp|
|加行业最多 3 只约束的 Top10 已算槽位|10/30|9/30|19/30|

趋势呈现部分排序相关性改善，但选入/移除的中位数 lift 为负；量能当前部分成绩较弱。这些结果可用于确定下一步核查重点，不能用不完整的 290 对 459 或 514 对 459 个收益槽位做完整组合的公平优劣结论。三日收益窗口彼此重叠，不能当作三个独立行情周期。

独立 SQL 对两候选已算标签按扫描/股票配对：3,688 对、baseline score 不一致 0、net excess 不一致 0。同股票同日同成本的标签一致性成立；候选各自选中的名单覆盖不同，完整执行头与 Top5 对 Top10 对照仍均为 0 对，相关结果应保持空值。

### 已有 Regularized 研究 lane：三批完整执行头可比较

这里的同口径执行头是：基线按 `baseline_rank`，候选按 `challenger_rank`，各取 10 只、同一行业最多 3 只，同信号日/入出场日/基准/10 bps 成本；固定信号名单后查标签。以下均为 10/10 已完成，不用事后有价格的股票补位。

|信号日|到期日|基线净超额|候选净超额|候选减基线|
|---|---|---:|---:|---:|
|2026-08-27|2026-09-03|-5.001834%|+0.862177%|+5.864011 pp|
|2026-08-28|2026-09-04|+0.743011%|-0.040480%|-0.783491 pp|
|2026-08-31|2026-09-07|-0.636317%|-0.282198%|+0.354119 pp|
|三日等权均值|—|-1.631714%|+0.179833%|+1.811547 pp|

候选胜 2/3，lift 中位数 +0.354119 pp；均值明显受首批基线弱表现影响。可以确认这三个完整窗口上候选改善，不能外推到新候选、其他市场阶段或实际账户。

同一 lane 还存在另一个不同问题的现成对照：按 **同一个 challenger_rank** 取 Top5 对 Top10（不附加上述行业 cap），三批完整，平均净超额 +0.433683% 对 +0.379423%，lift 仅 +0.054261 pp；逐批 lift 为 -0.556228、+0.297386、+0.421624 pp。这个结果检验缩小排名头部，不能当作 challenger 模型对原基线的 +1.811547 pp。

现有 promotion 仍为 `collecting / keep_shadow_only`，三个到期批次不足既有 20 批要求，长窗口也未齐。这里只记录既有门槛，不增加门禁。没有足够证据给出长期样本外净超额、账户回撤或组合实际换手结论；保留已有排名/名单换手诊断，不冒充成交换手。paper legacy 亏损属于另一条账本证据链，未参与本报告任何 Challenger 收益计算。

## 可复现查询

以下 SQL 在上述只读连接上执行。数据库会继续变化，重跑应同时记录 UTC 时间。日期生成复用 `factor_shadow_outcome_dates(date.fromisoformat(signal_date), horizon)`；5 日的三组参数为 `(09-02,09-08)`、`(09-03,09-09)`、`(09-04,09-10)`，完整日期均为 2026 年。

canonical 扫描与评分槽位：

```sql
WITH runs AS (
  SELECT experiment_id,scan_job_id,signal_date,MIN(created_at) created_at,COUNT(*) n
  FROM factor_shadow_scores GROUP BY 1,2,3
), ranked AS (
  SELECT *,ROW_NUMBER() OVER (
    PARTITION BY experiment_id,signal_date ORDER BY created_at,scan_job_id
  ) rn FROM runs
)
SELECT experiment_id,scan_job_id,signal_date,n FROM ranked WHERE rn=1 ORDER BY 1,3;
```

每批覆盖（参数 `:experiment_id, :scan_job_id, :horizon, :entry_date, :outcome_date`；三类未算互斥）：

```sql
SELECT COUNT(*) expected,COUNT(o.instrument_id) completed,
 SUM(o.instrument_id IS NULL AND (e.instrument_id IS NULL OR x.instrument_id IS NULL)) no_row,
 SUM(o.instrument_id IS NULL AND e.instrument_id IS NOT NULL AND x.instrument_id IS NOT NULL
   AND (COALESCE(e.adjusted_open,0)<=0 OR COALESCE(x.adjusted_close,0)<=0)) insufficient_adjusted,
 COALESCE(SUM(o.instrument_id IS NULL AND e.adjusted_open>0 AND x.adjusted_close>0),0) positive_uncomputed
FROM factor_shadow_scores s
LEFT JOIN factor_shadow_outcomes o ON o.experiment_id=s.experiment_id
 AND o.scan_job_id=s.scan_job_id AND o.instrument_id=s.instrument_id AND o.horizon_sessions=:horizon
LEFT JOIN market_bar_cache e ON e.provider_mode='free' AND e.instrument_id=s.instrument_id
 AND e.trade_date=:entry_date
LEFT JOIN market_bar_cache x ON x.provider_mode='free' AND x.instrument_id=s.instrument_id
 AND x.trade_date=:outcome_date
WHERE s.experiment_id=:experiment_id AND s.scan_job_id=:scan_job_id;
```

三批完整执行头独立 SQL（行业内前 3，再整体前 10，等价于排名顺序扫描时跳过已满行业；用 SQL 窗口从信号评分选股后才连接标签）：

```sql
WITH runs AS (
 SELECT scan_job_id,signal_date,MIN(created_at) t FROM factor_shadow_scores
 WHERE experiment_id='factor-research-b6a9a102cbbd4f1a9cd5ff4c1b504f07' GROUP BY 1,2
), canonical AS (
 SELECT *,ROW_NUMBER() OVER(PARTITION BY signal_date ORDER BY t,scan_job_id) rn FROM runs
), score AS (
 SELECT s.* FROM factor_shadow_scores s JOIN canonical c USING(scan_job_id,signal_date)
 WHERE c.rn=1 AND s.experiment_id='factor-research-b6a9a102cbbd4f1a9cd5ff4c1b504f07'
), lanes AS (
 SELECT *, 'baseline' lane, baseline_rank r FROM score
 UNION ALL SELECT *, 'challenger' lane, challenger_rank r FROM score
), industry_order AS (
 SELECT *,ROW_NUMBER() OVER(PARTITION BY scan_job_id,lane,
 COALESCE(NULLIF(TRIM(industry),''),'unknown') ORDER BY r,instrument_id) ir FROM lanes
), head_order AS (
 SELECT *,ROW_NUMBER() OVER(PARTITION BY scan_job_id,lane ORDER BY r,instrument_id) hr
 FROM industry_order WHERE ir<=3
)
SELECT h.signal_date,h.lane,COUNT(*) selected,COUNT(o.instrument_id) completed,
 CASE WHEN COUNT(*)=10 AND COUNT(o.instrument_id)=10 THEN AVG(o.net_excess_return_pct) END net_excess
FROM head_order h LEFT JOIN factor_shadow_outcomes o
 ON o.experiment_id=h.experiment_id AND o.scan_job_id=h.scan_job_id
 AND o.instrument_id=h.instrument_id AND o.horizon_sessions=5
WHERE h.hr<=10 GROUP BY 1,2 ORDER BY 1,2;
```

复用评估时用 SQLAlchemy `create_engine('sqlite://', creator=只读连接函数)` 与 `sessionmaker`，调用 `build_factor_shadow_roster(..., provider_mode='free', as_of_date=date(2026,9,10))`。不是 outcome resolver，也不需要创建数据库 schema。

本轮仅完成只读复核与文档；未提交、未 push、未部署、未改交易权重。父任务专项测试历史在本轮刷新为 28 passed（paper loss attribution / ranking head challenger / matched control），随后综合 58 项测试通过（3.11 秒），Ruff 与 diff 检查通过。父任务独立运行上面的执行头 SQL，六行日期、10 个选择、10 个已完成与均值全部一致；不以单元测试替代当前云端取证。
