# Qagent 与外部量化工具的能力对比

日期：2026-09-10。目的：把可借鉴能力收敛为 Qagent 的有限增强项，而非替换系统。外部事实来自本日查阅的官方仓库和文档；Qagent 判断基于本日代码检查，未重新验收云端持续运行或策略收益。README 描述属于项目方能力声明，不是独立质量认证。

| 工具及官方来源 | 可以借鉴的能力 | 对 Qagent 的具体含义 |
| --- | --- | --- |
| [Qlib](https://github.com/microsoft/qlib) / [Experiment 与 Recorder](https://qlib.readthedocs.io/en/latest/component/recorder.html) | `qrun` 串联数据、训练、回测与评估，支持滚动训练；基于 MLflow 的实验/Recorder 记录参数、指标和产物。 | 对应 G2：在现有 Challenger 和实验产物之上固定协议、版本及复现入口。借鉴记录与工作流，不要求替换现有选股系统或立即引入 MLflow。 |
| [RD-Agent](https://github.com/microsoft/RD-Agent) | `fin_quant`、`fin_factor_report` 提供因子与模型迭代方向。 | 对应 G2：以亏损归因提出有界假设，保留失败实验和同口径比较。不得从项目宣传推断 Qagent 的收益，也不启用无界自动因子搜索。 |
| [QuantDinger](https://github.com/OpenByteInc/QuantDinger) | v5 README 描述 API、交易、调度分离，durable workers、监控和多市场能力。 | 仅在资源争用证据出现时评估 worker 隔离；多市场和平台化不属于当前目标，不由架构对比推导迁库需求。 |
| [VeighNa / vn.py](https://github.com/vnpy/vnpy) | 提供 gateway 适配体系，包括 XTP、TORA 等接口方向。 | 对应 G6：借鉴接口边界和订单生命周期。gateway 存在不代表目标券商可用或用户已有权限，先选接口并做隔离验证。 |
| [QuantConnect Reality Modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts) | 将成交、滑点、费用、购买力与结算等假设拆成模型；默认模型的流动性假设并不保证适配所有市场。 | 对应 G4：复用已有 A 股 rules/replay，显式列出成本与成交假设并做压力对照。不能直接视为精确 A 股成交模型。 |

## Qagent 已有能力与应补的证据

- 选股研究已有 `backend/qagent/research/ranking_head_challenger.py`、`factor_ablation.py`、`factor_ablation_stability.py` 和研究协议/产物，消融有 digest 与暴露窗口声明；`backtesting/matched_control.py` 与 `walk_forward.py` 已有对照、归因、多重检验控制、PBO 和成本敏感性能力。G2 是冻结一个候选假设，用未调参窗口跑现有流程并归档否定结果，不是新建实验库。
- `backend/qagent/monitoring/drift.py` 已有 `compare_feature_snapshots`、PSI、Jaccard 和行业 HHI；组合约束已有单股、行业、主题、ETF 重叠及市场状态控制。G5 是增量研究，不是补建“缺失的风控”。
- `backend/qagent/paper_trading/replay_readiness.py` 已调用真实 `replay_paper_evidence`，`backend/qagent/execution/paper_replay_sqlite.py` 已有 `replay_paper_sample`；`backend/qagent/execution/shadow.py` 的 `compare_execution_shadow` 是 synthetic 纯函数，本次检索仅见测试调用。G1 应核验真实重放链路的持续调度、观察产物和责任归属，不以接入 synthetic 函数作为默认目标。
- 已有 A 股规则、`execution/fees.py` 的 `VersionedAshareFeePolicy`、引擎提交/取消/过期幂等事件、补价及覆盖检查；G3/G4 复用这些能力。G4 的可执行净收益分解优先复用 `common_execution_delta` 和 `matched_control`，不重复建设；本轮未定位真实券商适配器，但未作全仓不存在证明。

## 推进顺序与验收

稳定目标 ID、状态及完整验收条件以 [PROJECT_GOAL.md](../PROJECT_GOAL.md) 为准：先 G1 执行观察取证与 G2 选股工作流；G3 仅支撑阻塞它们的关键数据，G4–G6 待排期。保持单一模拟账本、离线到 shadow 的隔离边界，升级由用户批准。

本对比不承诺收益，不新增生产门禁，不引入第二账户、实盘权限或全套替换。若实际复用外部模块，应固定版本并核验该版本许可；本报告不作未核验的许可结论。
