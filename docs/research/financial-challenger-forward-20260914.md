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

## 首个自然信号验收（2026-09-14）

云端16:40自然任务生成 `/var/lib/qagent-research/daily-financial/20260914T084431-fd1056e5f069451e88552f8e7556d221.json`，采集时间为上海16:40:01至16:44:31；协议v2、trade_date为20260914、顶层状态observed，20股中16只进入候选、4只按既有金融公司范围排除。文件SHA256为 `3cd50e2abbc0d27f99e0368e91a3e1e3bb173126a97d9c4cbae84a5e401802c3`，结果digest为 `086d76ca782ac01b52977517b2faba292b12786270c27e1c8279862628d959cd`。

主任务使用已测试的seal入口完成原始证据与候选重放，首个信号独占归档至 `/var/lib/qagent-research/financial-forward-signals/2026-09-14.json`，权限0400；文件SHA256为 `1466a90ab84a5cbed989ef187ecc0c37a6aa9cb055253a162aa81ac8d7315732`，结果digest为 `46fe1dc5e4a2ea9adcfc23604b59b596d45faa5f3a8b0d8f9f30e2c79c28b79a`。Top5为600519、603444、603259、002602、600398。当天没有满足同日完整集合要求的G2归档，因此 `baseline_unavailable`；可继续计算候选相对沪深300的净超额，但不能产生G2配对lift。

该信号的次交易日入场日为09-15，5/10/20交易日到期日分别为09-21、09-29、10-20；到期前不计算收益。当前仅完成首次自然采集与信号封存，尚未安装自动seal调度，真实成熟收益仍待相应收盘后验收。

## 自动封存与到期评估实现阶段（2026-09-14）

本轮新增 `scripts/run_financial_forward_research.py`，复用上文 seal/evaluate 协议，不增加数据库表、API、后端服务或交易入口。每次运行在上海当日的 daily-financial 目录中按 `finished_at`、文件名稳定排序，逐个重放，只取首个协议、摘要、原始证据、时序和排名均合法且顶层为 observed 的当日产物。已有同日 signal 时重放信号，并在当日 daily 仍存在时核对它确实来自首个合法产物；一致则幂等成功，任何既有文件都不覆盖。可选 G2 目录仅选择同日 ready 且同完整资格集合的最早合法基线；缺失或无效继续封存 `baseline_unavailable`，后到基线不得改写当天信号。

runner 在独立锁下遍历所有既有 signal，每个 signal 仍通过只读 SQLite URI、query-only 事务评估。5/10/20 日未成熟只进入当次不可覆盖 run 证据；已到期但缺价或质量拒绝的 partial 也完整保留逐股原因和价格证据于 run 归档，并在后续运行重试，**不占用最终窗口文件名**。只有完整 cohort 为 complete 时才独占发布 `financial-forward-evaluations/<signal_date>/<horizon>.json`；既有 complete 归档仅校验摘要、信号和窗口身份，不重新计算或覆盖。这样价格缓存后续补齐可从 partial 收敛到唯一 complete，同时已完成结果保持不可变。

运行固定 300 秒预算（CLI 只接受 30–900 秒）、最多扫描 2,000 个 JSON、单文件 128 MiB（覆盖既有约56 MiB的G2 ready归档）；无网络取数、补价、训练或写库。Linux cron主线程用 `SIGALRM/setitimer` 包住每次evaluate，超时在同一进程中断查询并经evaluate的finally关闭连接，不创建可能继续访问SQLite的工作线程；记录 `run_budget_exhausted` 后停止遍历。锁冲突退出75前也以无需取得该锁的唯一文件名独占归档冲突证据。无当日采集是正常 waiting；非法当日产物、损坏 signal/evaluation、缺数据库和预算耗尽均写入独占 run 证据并返回非零。evaluation 与 run/final 归档新增 evaluator SHA、实际解析后的 backend 根路径以及 `factor_shadow_outcomes.py` SHA，避免 `/opt/qagent/current` 后续切换后无法区分评估算法版本。

新增 `scripts/upgrade_financial_forward_research.py` 作为默认仅预览的独立升级器。它同时校验已部署 daily v2 cron/manifest、forward bundle manifest及每个文件摘要，拒绝额外文件、软链、可写文件或基线变化；显式 `--execute` 才以独占锁和硬链接原子安装新 cron，且不启动任务。发布前收据明确为 `prepared`，并记录唯一install id及本次pending文件的device/inode；只有cron发布及目录fsync成功后才原子改为 `installed`，回滚拒绝prepared或损坏收据。若收据晋级或fsync失败，持锁补偿仅在目标仍为本次pending的同device/inode且字节一致时撤销cron并fsync；若操作方已替换目标则绝不删除他人文件。显式 installed receipt 加 `--execute` 的回滚也核对device/inode与字节，并原子移至私密备份目录，保留可恢复 cron。默认未执行安装或回滚。

若进程在cron发布/fsync之后、收据晋级之前直接退出，下一次显式execute不会直接返回already installed：它先在持锁状态扫描私有目录中的 `before-install-*.json`。收据文件必须先通过root/private普通文件、0600、JSON、完整schema、固定目标路径及摘要格式校验；结构合法且forward manifest明确属于旧版本的收据分类为unrelated历史证据，不阻断当前恢复，畸形旧收据不能借版本差异绕过。唯一当前版本prepared还须匹配daily cron/manifest、安装字节摘要及当前cron的0644/device/inode/bytes，才原子晋级并返回 `recovered_installed`；唯一当前版本installed且同样完整匹配时才正常返回 `already_installed`。当前版本无有效收据、同字节不同inode、多个匹配prepared或多个installed均拒绝，避免把操作方重建的同内容文件误认成本次安装。

云端预安装手动run进一步确认父目录 `/var/lib/qagent-research` 为root:root 0755，既有signals为服务用户0700，但evaluations/runs尚不存在，服务用户不能直接创建。此前v1仅暂存、未安装cron，保留为失败候选证据且不覆盖；修订包常量改为 `financial-forward-20260914-v2`。v2 preview只严格验证signals为非软链、服务用户uid/gid、0700，并报告另外两目录的创建计划，不写磁盘；execute在持安装锁且发布cron前，以固定绝对路径创建evaluations/runs，使用目录fd设定服务用户uid/gid和0700、fsync并二次验证。任一目录缺失约束、owner/mode错误、软链或创建失败均不得安装cron；rollback不删除研究数据目录。runner无法在run目录本身不可创建时留证是预期边界，由该安装预检消除。

随后云端v2完成目录创建和cron安装，但安装后手动run失败：`research_financial_enrichment.analyze` 会从bundle root读取 `backend/qagent/providers/datahubco.py` 与 `tushare_relay.py` 计算实现摘要，v2 manifest/package未包含两文件。本地仓库运行曾从仓库根读取而未暴露缺口。主任务已安全rollback cron，安装收据为 `before-install-7biqeyhu`，撤回文件为 `rolled-back-9vxfg3ud.cron`；研究目录保留，任务未发生自然触发，未影响模拟盘。v3常量改为 `financial-forward-20260914-v3`，REQUIRED与manifest明确加入两个provider实现；隔离bundle测试仅复制manifest列出的完整文件，并在非仓库cwd实际完成seal/evaluate，防止再次隐式依赖仓库根。cron不存在时，BACKUPS中v2 installed/rollback历史收据不参与恢复扫描，不阻塞新v3安装。

预览模板为工作日 `11:37 UTC`（北京时间19:37）一次，同时 seal 与 evaluate。选择19:37是为了给16:40采集的600秒上限留出充分间隔，并错开既有 G2 每半小时检查点以及10分钟整点节奏；它仍是独立只读研究任务，固定使用：

```sh
PYTHONPATH=/opt/qagent/current/backend /opt/qagent/current/backend/.venv/bin/python -B \
  /opt/qagent-research/financial-forward-20260914-v3/scripts/run_financial_forward_research.py \
  --daily-dir /var/lib/qagent-research/daily-financial \
  --baseline-dir /var/lib/qagent-research/g2-forward-results/signals \
  --signal-dir /var/lib/qagent-research/financial-forward-signals \
  --evaluation-dir /var/lib/qagent-research/financial-forward-evaluations \
  --run-dir /var/lib/qagent-research/financial-forward-runs \
  --db /var/lib/qagent/qagent.db --provider-mode free --budget-seconds 300
```

最新专项与现有安装/升级回归共 **67 passed（2.54秒）**，正确虚拟环境全量 **2625 passed、3 warnings（230.77秒）**，Ruff及diff检查通过，最终审计无P1/P2。新增隔离manifest bundle实际seal/evaluate、cron缺失时v2历史收据不阻塞v3安装，以及v2 installed历史收据与v3同inode prepared并存时可恢复并回滚；当前v3多prepared/异inode仍拒绝。本阶段已实现并完成上述测试；v3未 commit、未 push、未打包、未安装cron、未部署或运行云端自动任务；v1仍仅暂存且未安装，v2 cron已回滚且未自然触发。首个自然 signal 的真实5/10/20日成熟验收仍按原日期等待，G2-FQ1/G2状态不提升。
