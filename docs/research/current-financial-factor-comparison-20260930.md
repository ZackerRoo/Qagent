# 当前财务输入的因子排名隔离对照

这是 G2-FQ1 的一次性、默认关闭研究入口，不接入正式 Ranking、G2 冻结模型、模拟账户、数据库或调度。唯一输入消费者为 `scripts/compare_current_financial_factors.py`。它复用现有 ProMax 当前基本面研究适配器和 `build_factor_rankings`，不增加采集器或长期并行链路。

## 固定比较口径

输入是最多 20 只 A 股、逐股最新行情日一致的 CSV，必需列为 `instrument_id,trade_date,open,high,low,close,volume,provider`；每只股票可有多日数据。显式开关启用后，脚本在当前北京时间日期请求 ProMax `daily_basic` 与 `fina_indicator`，保留原始查询参数、行、行摘要、取得时间、估值日期、财务公告日和报告期。当前取得不等于历史 PIT；拒绝历史观察日期和未来行情。

必须具有正 PE、正市值、ROE，以及至少一个收入或利润增长字段，并通过来源、日期与修订校验。缺字段的股票从两组共同排除，输出身份与原因；供应方错误、来源不符、分页触顶或少于两只合格股票时报告 `blocked`，不生成排名。两组使用完全相同的合格股票和行情，基线调用现有因子引擎但不传财务数据，增强组只把这次 ProMax 快照传入同一引擎。Top K、排名变化和重叠是行为结果。往返成本默认 10 bps，记录为两组相同的预设口径；本工具不读取未来价格、不计算换手或净收益，因此不能证明选股增益。

这里的 PE、市值、ROE、增长会通过引擎已有的估值、规模、质量评分影响股票排名；单列的盈利能力与增长研究曝光权重仍为零。现金流没有进入这个适配器，也没有足够的同源、同报告期字段契约，本次不拼接现金流指标。

## 运行和停用

在已配置 `QAGENT_TUSHARE_RELAY_RESEARCH_ENABLED=true` 和 ProMax 凭据的隔离环境，手动运行：

```bash
PYTHONPATH=backend:scripts backend/.venv/bin/python scripts/compare_current_financial_factors.py \
  --enable-current-financial-research \
  --bars-csv /absolute/path/current-bars.csv \
  --output /absolute/path/current-financial-factor-comparison.json
```

输出采用独占发布，包含输入文件 SHA256、引擎脚本 SHA256、原始供应方行与摘要、完整基线/增强排名及结果摘要；已有输出不会覆盖。该入口无 cron/API/DB 消费者，未被显式运行时不会请求数据。若供应方证据无法连续合格、同集合前向收益不支持继续观察，或没有研究消费者，保留已归档审计结果并停止使用此脚本；不改历史账本。正式接线、成本后收益和晋级都需要独立验收。

## 2026-09-30 云端只读 smoke

主任务从云端 `market_bar_cache` **只读**取得 2026-09-29 行情，作为 2026-09-30 当前观察的两组共同输入；隔离脚本和运行时输入/输出最初放在云端 `/tmp`，没有安装调度或变更服务。下面补录的私有持久归档更新了“产物仅在 `/tmp`”的初始状态。以下结果只属于对应批次，不代表全市场覆盖或前向收益。

- 三股批次：ProMax 付费查询 6/6 完成，三股均合格，报告 `status=compared`。基线与增强的排名顺序完全相同；结果摘要为 `977f6abc8da785bdf97d4a2bad03ba6aaabf6444286ed1416263d8e4c7f07771`。这确认隔离接线能运行，未观察到该批次的排序改善。
- 候选池十股批次：共发起 20 次查询，六股合格、四股排除；其中一次 `fina_indicator` 返回 ProMax HTTP 503 `upstream_pool_exhausted`，整批按协议 `status=blocked`，不生成可比较排名。结果摘要为 `b7818138bb25aa24d8fe8c08631d1e9d4ceeff02411e9ccb3bb4d019e449e316`。不能把六股合格误写为本批对照成功。

两次均未写模拟账本或数据库，也未修改正式 Ranking、交易规则及研究 cron。真实 smoke 不提供成熟收益或选股增益证据；本轮只补录文档，未改代码，未 commit、push 或部署。

主任务随后将两批 CSV 输入和 JSON 报告按原字节复制到云端私有持久目录 `/home/luozhenkun/qagent/research-data/current-financial-comparison-20260930`，目录权限 `0700`、文件权限 `0600`，并核对复制前后 SHA256 一致。以下是**文件 SHA256**，不同于上文报告内的 `result_digest`：

| 批次 | CSV 输入 | JSON 报告 |
| --- | --- | --- |
| 三股 | `de31a1b87147adc96fe0a4d35d7c8af6b52a498b73846ee0f801c208fede62e3` | `99ca33b01f005b679b5f23523eaa13ef1b56160944db3cd15b3e8812f66475fb` |
| 十股 | `5cc2020586472131cb4be73c9819c512215727b234eb5ead79deb45d0c4b09b3` | `01f2e1bd47394bf57b9eb2d55479fd6ecaae186c108862548b60d92201c35f1f` |

此归档只保存研究证据文件，不是对模拟盘数据库的写入，也不改变上述 `compared` / `blocked` 结论。

## 2026-10-01 Datahubco 当前观察适配（本地实现）

同一个隔离对比入口新增显式 `--source datahubco` 模式，继续复用同一个 `build_factor_rankings`、相同合格股票集合与相同行情行；ProMax 仍是原默认模式。Datahubco 模式要求 `--report-period YYYYMMDD` 和 `--valuation-trade-date YYYYMMDD`，每股固定查询一次带 `trade_date` 的 `daily_basic` 和一次带 `period` 的 `fina_indicator`，最多 20 股、40 次请求。不查询无报告期的全历史财报；云端只读探针显示无报告期请求的 `limit=100` 会触顶，不能视为完整数据。估值日必须等于 CSV 中所有股票共同的最新行情日，当前观察日仍必须是运行当日的北京时间日期。

```bash
PYTHONPATH=backend:scripts backend/.venv/bin/python scripts/compare_current_financial_factors.py \
  --enable-current-financial-research --source datahubco \
  --report-period 20260630 --valuation-trade-date 20260930 \
  --bars-csv /absolute/path/20260930-bars.csv \
  --output /absolute/path/current-datahubco-comparison.json
```

使用现有 `Settings` 的 Datahubco 开关、密钥及 HTTP 授权，不在脚本中写入凭据。每次请求的参数、原始行、来源、行摘要及错误均留在原报告 `source_queries`；报告另记报告期、估值日、输入文件及实现摘要。身份、精确交易日和报告期、`ann_date` 及存在时的 `f_ann_date` 均须在报告期至**估值交易日**之间，不能借次日观察时才公告的财务值搭配前一交易日行情；快照公告日取两者较晚者。数值格式、消费字段修订冲突与分页触顶也做拒绝校验。任一来源请求无行、错误或覆盖不全时整批 `blocked`，不生成两组排名；单股只有合法缺失值时按原 complete-case 规则共同排除。日期门禁只验证返回行上的日期，当前取得仍无历史 PIT、前向收益或成本后提升证据，不回填 G2 冻结信号，也不接正式 Ranking、模拟账户、数据库或 cron。

本地代码及聚焦测试已实现，尚未对修订后的 Datahubco 模式运行云端真实对比；未 commit、未 push、未部署。上文 09-30 ProMax 两批归档结论与文件摘要均是历史证据，不由本次实现改写。

## 2026-10-01 Datahubco 隔离对照实测

更新上段“尚未运行真实对比”的阶段快照：修订实现提交 `5a44fa4fbf8e0e00ee0fa1e86e236415257b396b` 已 push，本地相关 **141 passed**、backend 全量 **2948 passed、3 项 warnings**，Ruff 与 diff 检查通过。云端同 SHA release 仅在 `/home/luozhenkun/qagent/releases/` 暂存；只读核验时生产 `current` 仍为 `c8659cf4357eb3d0daa7bc7ea4b09ec1decb9967`，未切换服务或启用新研究调度。

两次比较均使用 2026-09-30 行情、2026-06-30 报告期与 2026-09-30 估值日，报告观察日为 2026-10-01；每股各请求一次 Datahubco `daily_basic` 和 `fina_indicator`，两组共用同一合格集合及行情。云端私有持久归档目录为 `/home/luozhenkun/qagent/research-data/current-financial-comparison-20261001`。下表摘要为**归档文件 SHA256**，与报告内 `result_digest` 分开：

| 批次 | 报告与请求 | 合格 / 排除 | 排名行为 | CSV SHA256 | JSON SHA256 |
| --- | --- | --- | --- | --- | --- |
| 五股 | `datahubco-five-20260930.json`；`compared`，10/10 查询、供应方错误 0 | 5 / 0 | Top5 重合 5/5，五股排名变化均为 0 | `baa4c0d8b0ff9fad8c923c65dc573e82d836b187f17227954edb11437b654d69` | `02abab2b81b8ea7ce70f9d1976393388b7a094afcc9e6a952a63c2b44bcae739` |
| 20 股固定分位集合 | `datahubco-twenty-quantiles-20260930.json`；`compared`，40/40 查询、供应方错误 0 | 9 / 11 | Top5 重合 3/5，9 股排名均变化，最大绝对名次变化 4 | `e049c5674c0288d3774052574c1a3d39a3822e497c7b6c408ae622c98c53a450` | `21f60b92f2b9c6b98d046ab9eeab61d0f3e7e9854b42a4b7343ae402581fb1c5` |

20 股报告的 11 个排除项均标记 `positive_pe_unavailable`；逐条原始查询中的 `pe_ttm` 为 `null` 或缺失，当前只能确认 **PE_TTM 缺失 11/20**，不能推断其为负 PE 或亏损股，也未判明缺失原因。这 11 股的 `ps_ttm` 与市值字段均有值，仅 2 股有普通 `pe`；单日替代字段可用性不构成改用 P/S 估值规则的依据。20 股集合关联的 09-30 G2 捕获 `source_digest` 为 `3002de20e0d8f716582ad828fbf9b8ea2764a238cf56c12c1cf0ceb2c1c7532f`；该来源身份不把本次当前观察变为历史 PIT。

五股与九股的名次结果仅说明这个小型、受选择影响的集合中因子输入改变了多少排序；没有后续收益、换手或净超额，不能称选股提升。两报告均标记 `research_only=true`、`activation_allowed=false`。按冻结 G2 代码日程，下一个采样日为 **2026-10-19**；历史 PIT pilot 仅暂存、未运行。本次研究不回填冻结信号，也未改变正式 Ranking、唯一模拟账户、交易规则或实盘权限。只读核验时 SQLite `quick_check=ok`，账本为 **16 笔交易、626 条事件、78 个 paper update slot**；这些是当前状态检查，不是本研究的绩效证据。

## PE 缺失敏感性模式（2026-10-01，本地实现）

原有默认模式仍按正 PE 完整案例筛选，报告字段与筛选口径不变。仅显式增加 `--include-missing-pe-sensitivity` 时，ProMax 和 Datahubco 当前观察才允许 PE 缺失或非正、但其余条件全部合格的股票进入**基线和增强组同一个集合**。正市值、ROE、至少一个增长字段，以及原有行情、来源、日期、修订、分页与请求覆盖门禁继续生效；任一供应方错误仍阻断整批排名。PE 保持供应方原值或 `null`，不以 `ps_ttm`、普通 `pe` 或其他数据填补。`build_factor_rankings` 沿用现有缺失估值处理：同组有有效 PE 时，该股票的估值回退分数为 `0.35`；不改引擎权重。

例如在上述手动命令中额外加入 `--include-missing-pe-sensitivity`。仅此模式的报告增加 `sensitivity.mode=include_missing_pe`、`affected_instrument_ids`、两组共用股票与行情的标记及无填补说明；受影响身份是**仅因 PE 门槛才在默认模式被排除、在本模式实际进入集合**的股票。原有 `cohort` 仍逐项列出其他排除原因，`research_only=true`、`activation_allowed=false` 不变。

这是同一批当前可取得财务值的行为敏感性计算，PE 缺失原因未知，纳入后仍有样本选择偏差。它不是历史 PIT、未回填冻结 G2 信号，也不计算换手、前向收益或净绩效；本节仅记录本地实现口径，未在云端运行或部署，也不改正式 Ranking、唯一模拟账户、数据库或调度。
