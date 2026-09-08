# 推荐排名的前向观察

入口：历史验证页、模拟组合页的“推荐排名前向观察”；只读 API：
`GET /api/recommendations/forward-alignment?provider=free&start=2026-09-01&end=2026-09-07`。
可选 `historical_run_id` 读取已保存历史实验的身份 manifest，不接受调用方临时声明身份。

完成新的全市场扫描时，在原 `scan_runs.data_health.recommendation_forward_alignment_v1`
保存一次版本化 JSON：实际完整排名（截断展示前）、原名次、策略、信号日期、版本字段、
共享筛选实现 digest、市场准入 bool、冻结 trigger/stop/target/no_chase 等执行计划。
记录与原扫描同一事务；采集失败记录 capture_error，不中断原扫描保存。
不修改扫描卡片顺序、调用方 data_health、模拟账户或交易。无新账户、新调度或自动晋升。

协议 `persisted_cards_order_strategy_cap2_top10_v1` 复用历史 baseline 的
`baseline_eligible_cards`（状态与 governance paper eligibility）及
`select_strategy_diversified(max_per_strategy=2)`，市场不准入则空选。
历史仍由 `walk_forward._signals` 的 snapshot.top_5/top_10 产生，筛选统计原语义保留。
前向另外剔除异日 stale 卡片并保留剔除清单；存在这种差异时不能严格比较。
完整候选不足 10 是有效小样本；源截断、策略缺失、旧记录没有冻结事实则排除。
同一上海自然日取最早有效同日记录，run_id 打破时间并列；不拼接多个扫描，不事后选最好结果。
首次有效记录即 prospective_start，历史记录不会回填今天排名。

5/10/20 个交易日收益只读 market bar cache 并复用 compute_forward_returns。
缓存缺交易日不会把目标日顺延；组内所有实际入选项目成熟才显示均值，同时给成熟数/期望数。
Top5、Top10、rank6_10 分开展示，不足 10 不扩展补选。
这些是信号收盘到目标收盘的描述性百分比收益，不是可成交组合回报，也不能证明历史 9.68pp 差异。
`frozen_selection_signals` 只转换已冻结计划；GET 不执行回放。

相同筛选函数不等于同一模型。API 明示 expected_historical_protocol，逐批次核对历史身份
manifest 的内容摘要有效性、source、整个 Python 包源码、策略注册表、依赖版本、
实际配置、市场门禁和 stale 差异。摘要是内容一致性检查，不是密码学签名。
新的历史 run 在开始执行时产生 `recommendation_alignment_identity`，随 data_health 保存；
恢复运行的旧 snapshot 没有自己的身份时仍明确不可认证。线上也记录实际 batch/ETF/展示上限、
governance/feedback 摘要和同一套源码/依赖身份，但尚未完全冻结的实时增强与校准资产会明确标缺。
线上 source 与历史 point-in-time baseline 不同，因此共享选择函数不能使二者自动可比。
不得用当前代码版本补写旧历史身份；只有未来真实决策时捕获的对应身份才可能满足比较条件。

显式离线执行入口（不连接生产数据库、不创建账户）：

```sh
cd backend
.venv/bin/python -m qagent.backtesting.forward_replay /absolute/facts.json /absolute/isolated.sqlite /absolute/new-report.json
```

输入 JSON 是 `provider_mode`、`dataset_revision`、`start_date`、`end_date` 和
`cohorts: [{"run_id": "原扫描 ID", "fact": 原始冻结事实对象}]`。
使用从原记录导出的事实和人工准备的隔离数据库副本；不要重建历史排名。
入口拒绝 data 目录、qagent.db、已有输出、超过 366 天/未来的日期窗口、重复日期、
异日记录和混合模型配置。数据库以 SQLite mode=ro 和 query_only 打开。
同一适应性策略的逐日 governance/feedback 是单独冻结的 decision_inputs，可随观察变化；
报告保留各日摘要，稳定源码、策略和配置发生变化则需要另开报告。
Top5/Top10 均使用 portfolio 同一执行器、版本化 A 股规则、10 个仓位及相同资金费用参数；
报告保留逐信号 audit 和 provider_errors。实时模型等同性尚不可证明不妨碍对冻结计划做此实验，
但该报告不会宣称历史模型已对齐。截止日未成熟或执行证据缺失的结果仍受执行器审计约束。

验证覆盖：排名冻结与计划转换、完整小样本、市场门禁、同日 canonical、旧记录排除、空缓存、
SELECT-only 报告；历史 selection 回归和前端 TypeScript/构建。测试均使用隔离 SQLite。
