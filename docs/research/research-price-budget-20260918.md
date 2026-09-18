# 研究补价预算前置查询优化（2026-09-18）

## 触发与范围

主任务本轮提供的09-18自然周期快照：factor shadow exact price requested
102,623、cache hits 63,391、unresolved 39,232，其中confirmed_suspended 69、
work_budget_exhausted 39,163，provider requests为0，预算原因wall_clock_deadline。
这些字段是该次快照，不表示全部未完成标签永久缺价；本报告作者没有另行启动云端研究任务。

本轮仅优化既有`repair_exact_daily_prices`的前置缓存和结构元数据读取。
唯一消费者仍是现有Factor/Fuyao shadow结果解析器，不新增补价服务、数据源、持久化
schema、研究候选、调度或账户。保留90秒协作预算、provider批次预算、稳定cursor、
价格质量、已上市/停牌判定及模拟盘隔离。

## 已确认的代码原因

首次`claim_provider_batch`和cursor推进之前，会对全部requirements检查缓存，
再对全部missing检查结构原因。旧实现为每个日期的每个requirement重复过滤整张
DataFrame；结构检查对每个missing创建独立session，执行最多两条SELECT。
同一股票同日的open/close也重复查。39,232条缺口最多产生78,464条结构SELECT，
尚未开始provider调用就可能耗尽预算；下一轮仍必须重复此工作。

这证明存在高成本前置路径，可解释provider=0和cursor不推进，但尚无云端逐函数
profile，不能把该次周期全部耗时精确归给此两函数。已有cursor本身保留跨周期
公平续做，并非缺少cursor或需要简单扩大预算。

## 最小改动及语义

- `_missing_requirements`每日期建立一次`(instrument_id, trade_date)`索引；重复键
  仍拒绝，逐字段执行原数值及`_unsafe_exact_row`检查。每个provider批次前仍重查缓存，
  不复用可能陈旧的前置结果。
- `_structural_no_row_reasons`按日期、每500只股票分批读取。SQL `row_number`只返回
  每股票最新metadata，避免把全部历史快照加载成ORM对象；排序完全沿用原逻辑：
  tradability按revision降序及source升序，profile按snapshot_date/revision降序，
  且只看不晚于目标日期的snapshot。
- 原优先级不变：明确suspended优先，其次not_listed；有任何显式tradability时，
  外部停牌证据不能覆盖它；其他缺口仍可重试。不由no-row推断停牌。

SQL仍须在目标股票的历史记录中选最新版本，本轮降低网络/连接/查询次数及Python
对象数量，不声称数据库历史大小从此不影响耗时。不更改cursor scope、claim顺序、
provider请求集合或质量规则。

## 本地验证与可复现基准

同一Mac、同一`backend/.venv/bin/python`进程入口进行前后计时，使用`time.monotonic`。
缓存输入为单日2026-09-01的5,500只股票、每股一条adjusted_open=10、provider=fixture
的DataFrame，fake cache原样返回该frame；逐股一个adjusted_open requirement。
结构输入为同一集合前1,000条requirement，初始化的临时SQLite库内两张metadata表为空。
旧版本为逐项调用原单行helper，新版本一次调用批量helper；不计数据库初始化。

| 工作 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 5,500条缓存requirement检查 | 2.162917秒 | 0.109124秒 |
| 1,000条缺口结构检查 | 2.280063秒 | 0.026500秒 |

这是本地合成微基准，非云端吞吐保证或业务数据已补齐证明。确定性查询计数回归另外
证明1,001只股票、各open/close两个字段由原最多4,004条结构SELECT降为6条。

新增回归覆盖重复缓存键、其他日期、缺行、非法值/NaN/非正值、unsafe provenance；
metadata最新revision与source排序、未来snapshot隔离、上市/退市边界、其他provider
隔离及显式metadata与停牌证据优先级。既有预算到期不请求、跨周期cursor公平性、
已完成槽位保留、并发claim及缓存并发补齐重查测试继续运行。

五文件专项（shadow_price_repair、fuyao_shadow_outcomes、g2_risk_feature_freeze、
g2_risk_feature_forward、shadow_cached_label_diagnostics）**48 passed（5.30秒）**；
shadow_price_repair、factor_research、api_automation三文件扩展回归
**132 passed、1项既有warning（15.42秒）**，包含最后补充的NaN/负值场景。
Ruff及`git diff --check`通过。主任务全量验收另行记录。

## 源码身份与交付边界

G2冻结manifest的五个`source_sha256`文件不含`shadow_price_repair.py`；
`factor_shadow_scorer_identity`所哈希的函数集合也不含本次helper。walk-forward
`backtesting/experiment.py`的源码目录清单不含research，显式文件仅jobs/daily_scan.py。
本次优化不修改这些身份输入、冻结模型或manifest，也不放宽其验证。

另一个更宽的身份口径`recommendations/alignment_identity.py`会哈希整个qagent
package的Python文件，因此其`package_source_digest`随本次代码变动而变化；不能
将上述固定G2/walk-forward摘要不变外推为所有身份不变。原身份不一致检查继续保留，
不绕过旧校验或改写历史manifest。

截至本报告：本地已实现和完成上述专项，未commit、未push、未部署，未写云端。
部署后的验收应查看自然周期实际provider进度、cursor推进、预算原因和缺口覆盖，
不能用微基准或测试通过代替自然补价成功、标签成熟或策略有效。

主任务最终集成验收补录：backend全量 **2744 passed、3项既有warnings（235.03秒）**，
Ruff/diff通过；独立review对补价优化及财务新信号协议修复均未发现P1/P2。真实09-17
财务旧v1归档兼容校验通过，文件哈希不变。仍未commit、未push、未部署；每日同日G2
基线方案选择及真实selection的14/20行业缺失尚未解决，不构成自然补价成功或完整
matched control验收。
