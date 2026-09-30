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
