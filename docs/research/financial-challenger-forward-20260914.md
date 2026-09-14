# 财务规则候选的独立前向收益验证

本轮把已存在的 `financial-candidate-equal-percentiles-v2` 接到独立信号归档和 5/10/20 交易日收益评估。采用纯归档，不向 `FactorResearchRepository`、模拟账本或正式 ranking 写入。自然有效信号、同日基线与真实成熟收益尚未验收；已采集但用 09-11 trade_date、09-14 取得的报告不能追认成 09-11 前向信号。

## 最小复用设计与实现

新增 `scripts/evaluate_financial_challenger.py`，两阶段明确分开：

1. `seal(document, baseline, now=...)`：读取 `daily-documented-research-v2`，校验 result/universe digest、嵌套原始数据与系统返回的一致性，调用既有 `rank_financial_candidate.rank_candidate` 和 `research_financial_enrichment.analyze` 重放规则。六指标、等权分位数、杠杆反向、金融公司排除和缺失语义沿用 v2，不新增收益输入。按当日固定资格集合封存排名和排除列表，完全不读取价格。
2. `evaluate(signal, db, provider_mode='free', as_of=...)`：重放验证归档，使用 `file:…?mode=ro`、`PRAGMA query_only=ON` 和同一只读事务读取缓存。复用 `MarketDataCacheRepository`、`factor_shadow_outcome_dates`、`_adjusted_price` 和 `_return_pct`，输出独立收益快照。没有调用带写入/补价行为的 `resolve_factor_shadow_outcomes`。

为何不直接登记现有 Challenger：`FactorResearchRepository.model_bundle/model_bundles` 需要成功实验及模型 artifact，旧 `factor_experiments.FactorExperimentConfig` / `get_explicit_shadow_candidate` / `_shadow_lane_kind` 还限定服务端旧候选 allowlist。财务 v2 是规则排序，没有 LightGBM model_text。仅创建实验或写 scores 无法让 resolver/roster发现此规则；伪造模型 artifact 会破坏原语义。此轮复用价格与日期内核，通过新归档协议承载规则信号，不扩旧模型注册入口，不增加数据库 schema/API。

## 输入与输出协议

信号 `financial-rule-forward-v1` 包含：完整 daily 原始 artifact及摘要、基线原始 artifact、signal_date、sealed_at、固定 policy/policy_digest、implementation_sha256、股票规范 ID 与原始 `score_exact`/rank、覆盖/排除、baseline_status/reasons、隔离标志。基准固定沪深 300，Top5，往返成本固定 10 bps。股票 ID 从 `600519.SH` 映射为 `CN:600519`，拒绝映射冲突；观察集合保持 5–20 只、至少 5 只有效候选。

信号日必须是现有交易日历的交易日；trade_date、采集开始/结束、分区 received_at（以及提供时的 fetched_at）、派生 retrieved_at、封存时间均处于该日，且采集和封存不早于上海 15:30，与既有 G2 完整收盘缓冲一致。先后关系必须成立，禁止跨日、无时区、封存早于采集。CLI使用实际当前时间，不能指定过去封存时钟。日期较旧的财务报告期是正常特征语义；较旧的 trade_date 配上当前采集时间不满足本前向协议。

这是从取得时点起的前向观察，仍不等于历史财务 PIT。摘要证明完整性而非第三方时间真实性，旧公告、修订覆盖和页数上限的已有限制保留。后续财务报告期滚动或规则变更应创建新协议版本，不能覆盖已封存信号。

信号按日期命名、原子独占发布，不覆盖现有同日文件：当天第一次封存决定资格与基线；后到基线或补全财务不能重写当天信号。同日第二次运行被明确拒绝，不增加独立样本。

评估 `financial-rule-forward-evaluation-v1` 保存信号摘要、as_of、provider、各窗口入场/到期日期、固定 Top5 名单、每股计算/未解决状态和原因、实际使用价格、标准化缓存证据及摘要、组合净超额与配对 lift。收益口径为次交易日调整开盘入场、窗口末日调整收盘出场，减同窗沪深300收益再减0.1个百分点。到期日15:30之前仍标记 waiting_for_maturity；不读取未来日期价格来提前成熟。

## 同集合基线与结果解释

仅接受既有 `compare_g2_selections.validate_signal` 验证通过的同日 ready G2 artifact，且其采集完成不晚于本次封存。取 `full_features` 研究模型原排名，限制到本财务资格集合，必须覆盖所有资格股票，不因价格缺失改变集合或选股。基线来源清楚标记 `G2_full_features_research_not_production`，不称为已验证的生产选择。

输入观察顺序完全不作为收益基线。没有同日 ready G2 artifact、日期不一致、股票少一只、时序/摘要无效时，信号仍可封存候选，但 `baseline_unavailable`、baseline_order=null，不计算基线或lift。G2现有10交易日采样会导致很多每日财务信号没有同日基线；本轮不为凑配对调整旧采样协议。`rank_g2_consensus` / `compare_g2_selections` 原有行为解释入口保留，财务规则不伪装成这两个冻结变体。

每股没有精确日期行标记 missing_price_row；调整价缺失、非正或既有质量规则拒绝标记 invalid_adjusted_price；基准不可用单列 benchmark_unavailable。不做插值、后备填充或事后换股。候选Top5全部有合法价格才能给候选组合均值，基线亦然；双方完整才给 paired_complete=true/lift，未解决股票不参与不完整组合平均。完整 cohort 可仍有未解决非Top5股票，报告同时保留其分母与标签覆盖，不能以Top5完整掩盖全体覆盖。

同一天最多一个信号，跨天5/10/20日窗口相互重叠；文件数不是独立行情周期数。不输出升级或统计显著性结论，不修改旧 promotion 门槛。现有 `ranking_head_challenger.evaluate_ranking_head_challenger` 比较同一 challenger_rank 的Top5/Top10，语义不同，本轮不把它拿来冒充财务候选与full_features的比较。

## 运行与验收

同日收盘后人工或后续获授权的调度运行（本轮没有安装新调度）：

```sh
backend/.venv/bin/python scripts/evaluate_financial_challenger.py seal \
  --daily /absolute/path/today-daily.json \
  --baseline-g2 /absolute/path/same-day-ready-g2.json \
  --output-dir /absolute/path/financial-forward-signals
backend/.venv/bin/python scripts/evaluate_financial_challenger.py evaluate \
  --signal /absolute/path/financial-forward-signals/2026-09-14.json \
  --db /absolute/path/qagent.db --provider-mode free \
  --output /absolute/path/financial-evaluation-unique.json
```

`--baseline-g2` 可省略；省略即明确无比较基线。输入文件、已有输出与数据库均不覆盖，评估重新运行需新输出路径。合法但缺数据/未到期是可归档状态；非法信号/路径或试图覆盖输出返回 blocked、退出2。

新增测试覆盖历史回填、盘前/跨日/无时区、休市日、摘要重封后的排名篡改、原始系统返回不一致、伪基线/错日/缺股、固定集合完整配对、周末交易日偏移、固定10bps净超额独立算术、未收盘成熟阻断、缺行/不安全调整价/基准缺失不换股、不完整Top5不给均值、数据库字节不变且无额外表、独占输出。子任务专项首次14 passed（1.78秒），后续扩大到16项，最终相关结果由主任务复跑补录。

不做：新行情调用/补价、训练/调参、旧信号历史回填、重写G2采样或资格、在线模型登记、数据库迁移/API、生产rank/交易权重/账户/账本更改、自动升级、cron安装、服务重启或部署。当前本地实现与测试不代表自然信号或真实成熟收益验收。
