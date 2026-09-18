# Financial matched-control 同日 G2 依赖缺口（2026-09-18）

主任务刷新 09-17 自然运行证据：daily 七接口共 **140/140 observed**，财务规则 eligible **15/20**，extended candidate-pool 输入已存在；当日封存结果却为 `financial-rule-forward-v1`，原因 `baseline_not_supplied`。G2 归档只有 09-11 信号。财务任务按工作日运行，冻结 G2 每 10 个交易日采样，两者频率不一致；数据采集成功不能证明 matched-control 依赖完整。

根因是 `scripts/evaluate_financial_challenger.py` 仅在 extended 输入且有效 `baseline_rows` 存在时选择 v2，否则隐式回落到 v1。v2 的同行业、市值匹配仍需要同日 G2 `full_features` 排名作为固定 tie-break 证据；候选池生产顺序、旧日 G2 或人为生成的排序均不能替代它。

本轮安全修复使新的 extended candidate-pool 信号始终采用 `financial-rule-forward-v2`。缺失或无效 G2 时明确输出 `control_unavailable`、`g2_rank_incomplete`，保留原 `baseline_status` / `baseline_reasons`，不形成 control pairs 或 paired lift。同行业、市值距离、G2 rank、instrument ID 的匹配优先级及至少两只 Top5 外 control 的门槛均不变。runner 在 `sealed` 和 `already_sealed` 审计中记录协议及 baseline/control 状态与原因。

历史重放依据原协议兼容此前错误回落形成的 extended v1，旧 v1/v2 归档内容与摘要不修改，也不回填 09-17。每个信号日仍遵守 first-seal 不可变：首次因缺 G2 封存为 v2 unavailable 后，当日稍后出现的 G2 不会替换它。因此若后续选择每日冻结模型排名 sidecar，必须在首次 seal 前完成合格排名归档；若选择仅在 G2 采样日产生财务信号，则需明确新的财务采样约定。两种选择已由主任务向用户提出，当前尚未确定，本轮不修改冻结 G2 cadence、不新增基线生成、不代用生产排名。

本地验证：evaluator 与 runner 两组专项 **47 passed（2.59 秒）**，覆盖 absent/wrong-day/missing-cohort/invalid baseline、v2 unavailable 不计算 paired lift、旧 extended v1 不变重放、runner 重复封存不查询后来基线。Ruff 与 `git diff --check` 通过；未跑全量 backend 回归。已实现并完成上述测试，未提交、未 push、未部署。真实自然 v2 五对及 5/10/20 交易日成熟收益仍待验；不修改唯一模拟账户、账本、交易规则或正式 Ranking。

主任务随后独立复跑两组专项 **47 passed（2.35 秒）**。从云端只读下载的真实 09-17 归档 `/tmp/qagent-baseline-review.hy97kj/2026-09-17.json` 已通过新 `validate_archive`，文件哈希保持不变；仅在内存假设按新 seal 行为重算时，control reasons 为 `g2_rank_incomplete` 与 `industry_incomplete`。原 selection 20 只中 **14 只 industry/exposure_group 同为空**，因此补齐同日 G2 排名也不能单独解决本次 control 缺口。内存重算不是新前向信号、回填或部署验收。

只读代码追踪确认，归档的 `automation_seed_source` 为 `latest_signal_day`，API 此路径使用既有 opportunity snapshots。`routes.py` 的 `_paper_snapshot_industry` 从 snapshot.card 的 `market_context.industry`、`industry` 或 `sector` 取值；`_normalized_paper_industry` 把 unknown、综合、未知等标记转为 None，候选池再把同一值写入两个字段。`collect_daily_documented_research.py` 直接保存这两个字段，没有把已有行业映射丢失，也不会补取标准行业。

上游常规 card enrichment 调用 `cn_context.build_market_context`，使用固定 `KNOWN_CONTEXT` 和名称/代码前缀推断，未命中返回未知行业；该候选池取值路径不查询已有 PIT 行业库。此次非空值还包括 `成长制造`、`硬科技`，代码中存在对应前缀推断，不能由非空推断为供应方标准行业。当前可定位为候选卡行业覆盖/取值链路缺口；未读取云端逐只 snapshot.card 原始字段，不能进一步断言每只缺失的具体来源，也不能断言外部供应商无行业数据。本轮无新数据请求、无行业补值或生产逻辑修改；补齐方案仍需独立证据与范围确认。
