# 模拟盘风险状态口径

账户风控、总持仓名额、现金和单票金额检查决定是否可以新增模拟记录。行业集中度与市场状态属于研究观察；`risk_off` 本身不会暂停新增或降低仓位倍率。账户允许新增不表示行业集中度已经通过安全评估。

候补池的 `status` 保留实际的数据、现金、账户风控或容量原因。行业信息单独返回：

| 字段 | 含义 |
| --- | --- |
| `industry_control_mode` | `advisory_only`，不参与新增拦截 |
| `industry_warning` | `unknown` 表示分类缺失；`threshold_exceeded` 表示已达到观察阈值；空值仅表示未触发该观察项 |
| `industry_warning_count` | 当前返回候补池条目中的行业观察项数量，不能解释为真实执行拦截数 |
| `industry_blocked` / `industry_blocked_count` | 保留兼容字段，当前观察模式返回 `false` / `0` |

缺少行业分类时不能完整评估集中度。界面显示未知，距观察阈值显示 `—`，不将其解释为有剩余安全容量。观察阈值也不是账户可新增名额。

代码依据：`backend/qagent/api/routes.py` 的 `_paper_candidate_pool_snapshot_items` 生成只读候补状态；`_paper_industry_capacity_filter` 保留全部未跟踪行业候选并报告观察计数；`_paper_merge_market_risk_gate` 和 `_paper_market_probe_snapshots` 保留市场状态作为研究维度。前端在已有 `PaperRiskGatePanel` 和 `PaperCandidatePoolPanel` 显示这些区别。

本次调整只修正候补状态投影及展示，不修改 seed、撮合、账户配置或历史账本。API 回归覆盖行业未知/达到阈值时的允许新增、账户暂停、满额等待及现金不足，并校验查询前后模拟交易记录和账户配置一致。
