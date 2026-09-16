# Financial Challenger 同行业市值配平对照设计

## 目标与唯一消费者

本设计新增 `financial-rule-forward-v2`，唯一消费者为 `scripts/evaluate_financial_challenger.py`。v2 用同一候选池内的同行业、近似市值股票为 Financial Top5 建立 matched control，替代 v1 当前未配平的 G2 `full_features` Top5，作为后续 5/10/20 交易日收益差异的主归因基线。它不是新排名器、组合或交易入口。

旧 `financial-rule-forward-v1` 信号及结果保持不可变，评估器只为历史重放和到期补算保留 v1 兼容；新封存只生成 v2，同一信号日不并行生成 v1/v2，也不长期维护双轨主归因。

## 输入与时间语义

- 复用候选池响应中每只股票的 `industry` / `exposure_group`。两者要么同为空，此时该股票不能用于 control、信号按规则进入 `control_unavailable`；要么必须都是非空字符串，分别去除首尾空白后完全相等，才以该规范值作为行业组。只存在一个、任一去空白后为空或两者不相等均拒绝用于配平。匹配分组和值随原候选池响应及 digest 一并封存。
- 复用同次 `daily-documented-research-v2` 的 `daily_basic.total_mv`。仅接受有限、严格大于零的值；供应方原始值、来源和交易日保留在 `raw_evidence`，pair 保存同一供应方单位下的规范化数值字符串并据此计算，不自行乘以 `10000`、不改写币种或单位。
- 上述字段是信号日采集时的 current observation，不是历史 point-in-time 数据。v2 只用于事前自然封存的新信号，不回填旧日期，不把取得时间晚于信号日的观察追认为当日证据。
- 同一信号的候选和对照必须来自同一候选池响应、同一财务采集、同一交易日和同一供应方口径。摘要、日期或证据不一致时失败关闭。

## 确定性匹配

先按既有六指标规则固定 Financial Top5，再按候选 rank 1 到 5 逐一匹配。每名候选先从同一财务资格集合中选择同行业、尚未用作 control 且不在 Financial Top5 的股票；候选排序键固定为 `abs(log(candidate_total_mv) - log(control_total_mv))`、G2 rank、规范化 `instrument_id`，均按升序。若某行业的非 Top5 股票不足以完成该行业全部候选的匹配，才允许从同行业 Financial Top5 中选择另一只股票并按相同排序键补足。candidate 不得匹配自己，control 跨 pair 唯一、每只最多使用一次，任何阶段均不得跨行业匹配。

每一对封存 candidate/control 身份、industry、两者同供应方单位的规范化 `total_mv` 数值字符串、size distance、control G2 rank、配对完整性与可判别状态及 pair digest，并纳入 signal digest；供应方词法原值仍只由 `raw_evidence` 保存。重放必须得到完全相同的五对和摘要，否则拒绝信号。

行业与市值证据完整、同行业总候选足够且五名候选都取得未重复、非自身的合法 control 时，matched control 五对完整。若五对中 `control_id` 不在 Financial Top5 的配对少于 2 对，则标记 `control_not_discriminative`，保留五对证据但不能计算 paired lift；至少 2 对的 control 位于 Financial Top5 之外时才标记 `available`（可判别）。只有行业/市值不完整、字段冲突、非正/非有限市值、某 candidate 找不到尚未使用的同行业非 self control、同行业总候选不足或证据不一致时才标记 `control_unavailable`（不可用）。不可用时仍可计算 Financial Top5 相对沪深300的净超额，但不得用部分对照均值、跨行业替代、重复 control、self control、补值或事后换股生成 matched lift。

## 收益口径与输出

价格和到期逻辑原样复用现有评估器：信号后次交易日调整开盘入场，5/10/20 交易日窗口末日调整收盘退出，以同窗沪深300收益为市场基准，并扣固定往返 `10 bps`。不改变既有缺价、价格质量拒绝、未成熟和只读 SQLite 语义。

每个窗口分别输出 Financial Top5、五只 matched controls 的完整度和相对沪深300净超额；两组十只股票均有合法价格时，才输出组合 matched lift 和逐对差值并标记 `paired_complete=true`。任何一只缺失时保留逐股结果和原因，但不计算不完整组合均值。

## 验收与停用条件

实现验收必须覆盖：v1 历史信号可重放且不改摘要；v2 新信号不再以未配平 G2 Top5 作为收益基线；行业/市值字段篡改被拒绝；先非 Top5、再允许另一只 Top5 重合补足，且按 log 市值差、G2 rank、instrument ID 的固定顺序确定重放；candidate 不匹配自己、control 跨 pair 唯一且不跨行业；`available`、按 Top5 外 control 数判定的 `control_not_discriminative`、无同行业非 self control 时的 `control_unavailable` 三种语义正确；5/10/20、沪深300、10 bps 和价格质量规则保持一致；数据库、唯一模拟盘、账本及正式 Ranking 字节/状态不变。

运行验收至少取得一份自然 v2 信号的完整五对及其首个真实 5 交易日成熟结果，报告覆盖、候选/对照净超额和 matched lift；同一信号的 10/20 日结果按期补齐。接口成功、排名差异、合成测试或未成熟窗口不能替代收益验收，也不自动触发晋级。

以下情况失败关闭或停用：数据语义/单位无法确认、摘要或日期不一致、连续五个自然 v2 信号均为 `control_unavailable` 或 `control_not_discriminative`、首个可判别且完整的 5 日 matched 结果否定继续观察，或 `evaluate_financial_challenger.py` 不再消费该能力。停用时停止新 v2 调度并移出运行链路，只保留历史信号、结果和审计证据；不回退为长期 v1/v2 双轨，不删除模拟账本。

## 当前状态（2026-09-16）

- 已实现：collector 已保存并校验候选池 `industry` / `exposure_group`，enrichment 已从 `daily_basic` 提取正 `total_mv` 并保留 raw evidence，evaluator 已实现 `financial-rule-forward-v2` 的确定性匹配、三态判定、per-pair digest 及 5/10/20 评估；旧 v1 信号继续兼容重放和到期补算。新增独立 `daily-v5` / `forward-v7` 不可变 bundle 升级器，固定从当前 `daily-v4` / `forward-v6` 升级，默认 preview、显式 execute，按 consumer-first 顺序切换，支持幂等、中断续做和可恢复回滚，且不会启动任务；两个目标 bundle 使用相同完整依赖闭包，并拒绝额外文件、软链和可写文件。
- 已测试：父级本轮相关 8 组回归共 **186 passed**，升级器联合回归 **65 passed**，完整 backend 回归 **2692 passed、3 warnings**；Ruff 通过，`git diff --check` 通过。
- 已 commit：是，源提交 `87d8590`。
- 已 push：是，源提交 `87d8590` 已 push。
- 已部署：是。`daily-v5` manifest SHA 为 `551edc0ce38c5e7892ab3158ef4bb337db9f41b6bbb265853aff91fd2e68bf2f`，`forward-v7` manifest SHA 为 `ec9dfd26617eda1b037d433b5eb5f0e5f51e0526786f9b4c044ab72c64a8186e`。执行前 preview 为 `planned` 且旧 cron 未变；execute 为 `upgraded/new`、`started_job=false`，receipt 为 `/var/backups/qagent-financial-matched-control/before-install-4eu899i5.json`；安装后 preview 为 `already_installed/new`。新 daily cron SHA 为 `45ee481081dd3e3ba06f6df02bbd55f07944d1b896ff54e08f8ffcff10ff62f8`，新 forward cron SHA 为 `1305eb71acc82ecb3730433044f218815c40ec23aff004a602db3f622cc9dec3`，两者均为 `root:root 0644`；health 为 ok。

升级过程未手动启动 daily 或 forward 任务，只把既有唯一研究链路切换到不可变新 bundle；账户、数据库/schema、API、正式 Ranking 和模拟盘权重均未修改，也未创建第二模拟账户或账本。唯一模拟盘保持不变。自然 v2 信号及其真实 5/10/20 交易日成熟证据仍待后续自然运行验收，不能由部署成功或本地测试替代。
