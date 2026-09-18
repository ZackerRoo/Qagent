# Financial 每日冻结基线与同源行业证据

2026-09-18 本地实施记录。用户已选择并批准每日冻结模型基线与可信行业证据，更新此前“每日 sidecar 或仅 G2 采样日”的待选状态。本项继续使用 G2-FQ3，不改变原 G2 每 10 交易日采样、冻结模型、历史信号、正式 Ranking 或唯一模拟账户。

## 唯一消费者与协议

消费者仍是原 Financial daily → forward 链路。新增 `financial-daily-frozen-industry-v1` prospective 输入契约，对应 `financial-rule-forward-v3` 信号；上线后新信号走这条链路，旧 v1/v2 只保留历史重放及到期评估，不长期双写。09-14、09-17 等旧信号不补写、不重封。

`scripts/financial_daily_baseline.py` 的 `collect(source_dir, frozen_dir, output_dir, signal_date=..., eligible_ids=..., now=...)` 复用已有 G2 完整扫描 source、冻结 manifest、预处理与 full_features 三个固定种子 7/19/42。三模型分数均值降序、instrument ID 固定打破平分，只供 Financial 同行业市值配平的排名 tie-break，不新增生产选股策略或训练。输出 `financial-daily-frozen-rank-v1`，在独立目录以信号日 first-ready 独占归档；保存源、模型、预处理、实现与结果摘要。`validate_archive` 检查源与模型身份、资格集合、时间及排名结构，不把摘要校验称为重新训练或收益验收。

每日 baseline 须与信号同交易日、15:30 后取得完整输入且覆盖全部 Financial eligible ID。候选 source 按 captured_at 与文件名选择首个合格完整集合。缺少合格 source 时 runner 为 `waiting_for_baseline`，等待后续原调度，不提前占用当日 signal first-seal；不得用生产 rank 或旧日 G2 信号代替。已有合格 sidecar 不因后续 source 到达而改写。原 G2 采样日协议及归档不受此每日研究 sidecar 影响。

## 行业证据与预算

`scripts/financial_industry_evidence.py` 暴露 `industry_request`、`build_industry_evidence`、`validate_industry_evidence`。collector 对原选中的最多 20 只逐只请求 `stock_basic`，固定 `ts_code`、`limit=2`、`offset=0`、`fields=ts_code,industry`。原七接口加一个行业接口，每批最多 160 请求；仍在原 600 秒预算、同日最多两批限制内，不增加重试、全市场扫描或备用长期链路。forward 仍使用原 300 秒总预算。

新 `industry_evidence` 与原候选池 `universe` 分开保存，不覆盖原 card 行业及选择顺序。全部候选必须使用同一供应方的同一 `stock_basic.industry` 字段口径，taxonomy 标识如 `datahubco.stock_basic.industry`，不宣称为申万、中信或历史 PIT。原始请求、完整返回、获取时间、缺失原因与摘要可重放；供应方原值保留，使用值仅去除首尾空白。明确标记 `current_observation_not_historical_pit`。

有效性要求包括：响应摘要有效、请求与供应方身份一致、恰好一行且股票身份正确、非截断、行业非空且非 unknown/未知/未分类/其他等无效占位值，时间包含时区且同上海交易日、fetched_at ≤ received_at。prospective evaluator 另把这两个时间限制在 daily.started_at 与 finished_at 内，并保留同日 15:30 后与 seal 时间门禁。缺失、冲突、错误身份或无法确认完整性时全部行业配平不可用，不把剩余成功行与启发式行业混合。

行业证据不可用时 daily 标为 `incomplete` 并保留完整审计，仍仅按原同日最多两批预算重试；用尽后当日停止，runner 不封存该 incomplete 日产物。需区分自然调度门禁与 evaluator 语义：单独调用 seal 对行业不可用证据保留 `control_unavailable`，不生成有效 control 或 paired lift，并非任何手工 seal 调用都抛异常。行业完整但同行业股票不足或无法形成可判别配对时，仍明确 `control_unavailable` 或 `control_not_discriminative`；不伪造五对、不跨行业补足、不重用 control、不计算虚假 paired lift。原五对唯一非 self control、至少两只 control 在 Financial Top5 外、市值为正、完整十股价格、固定成本及 5/10/20 交易日窗口条件继续保持。

## 供应方真实核验及部署差距

主任务在云端通过既有凭据配置只读核验供应方 `/stock-basic`，`000975.SZ` 返回 HTTP 200、code 0、字段 `ts_code,industry`、单行行业“黄金”。时间约为 2026-09-18 02:44 UTC（北京时间上午），仅证明该股票与路由当时可读，不是收盘后 Financial 信号，也不证明 20 只完整覆盖或历史 PIT。

随后主任务对09-17归档选中的原20只股票做同日早间有界诊断：20次请求均HTTP 200、code 0、恰好一行行业，耗时10.75秒，无fetch失败。原card有14只行业空值，但这20只在本次供应方查询均有行业，不能把原缺口归因为供应商无数据。返回分类如下，全部来自同一stock_basic字段口径：

| 股票 | 行业 | 股票 | 行业 |
| --- | --- | --- | --- |
| 600028.SH | 石油加工 | 601555.SH | 证券 |
| 605507.SH | 化学制药 | 002746.SZ | 农业综合 |
| 600928.SH | 银行 | 300760.SZ | 医疗保健 |
| 002948.SZ | 银行 | 603871.SH | 仓储物流 |
| 002215.SZ | 农药化肥 | 603369.SH | 白酒 |
| 000975.SZ | 黄金 | 002648.SZ | 化工原料 |
| 600547.SH | 黄金 | 600795.SH | 火力发电 |
| 600900.SH | 水力发电 | 002714.SZ | 农业综合 |
| 600368.SH | 路桥 | 300079.SZ | IT设备 |
| 603979.SH | 铜 | 688309.SH | 环境保护 |

该诊断仍是09-18上午当前观察，不回填09-17信号，也不代替收盘后自然采集。20只行业有值不等于能形成五对：旧Top5涉及多个在该20只集合内只有单只股票的行业。保持原20只候选池、行业标签与配平门槛，不扩大池、不合并行业、不声称真实配对覆盖已通过。

当前已部署的 loopback documented-research API 对 `stock_basic` 返回 `unknown_api`：原 80 项 allowlist 未包含它。本地补充用户已提供的 stock-basic 示例，并将逻辑 API `stock_basic` 显式映射到 `/stock-basic`；目录因此为 81 项、80 项可调用，原 pro_bar 继续不可经 HTTP 调用。该变更不会在后端部署前自动对云端生效；不回退混用另一供应方行业。

02:51 UTC 主任务另在云端以内存加载新客户端、DocumentedResearch 与行业模块，真实单股查询经三层得到 `observed` / `available`、行业“黄金”。本次没有修改云端文件或环境、没有部署，验证的是新代码的隔离单股链路；运行中的原 loopback API 仍待发布。该上午样本同样不作为收盘后信号。

当前行业模块、供应方及 API 相关 **101 passed、1 项既有 warning（2.19 秒）**，Ruff 通过；每日 baseline 初始专项 **28 passed**，真实冻结模型推理有覆盖。集成、旧信号重放、完整回归及发布包验收由主任务继续记录，不能把上述专项称为整项或云端运行验收。本轮代码未提交、未 push，生产后端发布与新 daily/forward bundle 尚未部署。

后续主任务相关集成回归 **119 passed（2.80 秒）、1 项既有 warning**，完整 backend 回归仍进行中，尚无本轮全量通过结论。升级器 `upgrade_financial_daily_dependencies.py` 复用原双 cron 状态机，固定从 daily-v6/forward-v8 到 daily-v7/forward-v9，保持原13/4个时间槽、consumer-first、默认 preview、显式 execute、独立备份及恢复/回滚；只安装研究 bundle/cron，不发布后端、不启动任务。安装前必须由主任务确认新后端 loopback stock_basic 路由已可用。

代码身份边界已按实现核对：`backtesting/experiment.py` 的 `RESEARCH_SOURCE_DIRECTORIES` 不包含 `providers` 或 `research`，本轮两个 provider 文件的变更本身不改变该 walk-forward 文件集摘要；G2 固定 scorer/预处理源码也未修改。但 `recommendations/alignment_identity.py` 对整个 qagent 包的 Python 源码取 `package_source_digest`，会纳入 provider 及此前补价修改而改变身份。继续保留既有身份失配检查，不把旧 alignment 结果冒充新代码证据、不放宽兼容规则。

主任务首轮全量为 **2805 passed、1 failed**；唯一失败是 CLI 目录数量仍断言80，新增stock_basic后应为81。已修正该确切数量断言、该文件 **18 passed、1项既有warning（0.26秒）**，未放宽API校验；最终全量重跑中。另有合成输入端到端 **8 passed**，实际执行冻结 baseline collect → seal → validate_archive，不 mock baseline；这是代码链路验证，不是自然运营数据或收益证据。

双研究包已在本地 `/tmp/qagent-daily-dependencies.JJIRYE` 打包、解压，并从 `/tmp` 对两消费者执行 `--help`，新参数均可解析。daily manifest SHA256为 `3edf4fa5b867bfe2cd452f2f4a8504bec47fe458bfa697abf14154a971daf793`，forward manifest SHA256为 `b09e7e639493eb8cda5e9a2da321956dded47bbbb7a202c4342c93de149dd097`。本地包依赖与CLI检查不等于云端安装；尚未提交、push、后端发布或研究包部署。

验收仍需：更新后真实 loopback API；自然当日最多 20 股同源行业；同日冻结 baseline 在首次 seal 前完成；至少一份自然完整且可判别的五对信号；随后真实 5/10/20 交易日到期收益。若连续五份新配平信号仍不可用或不可判别、首个完整可判别 5 日结果否定继续观察，或唯一消费者不再使用该能力，沿用 G2-FQ3 停用条件，仅保留必要历史证据，不自动晋级或改变交易权限。

## 最终本地验收

主任务最终 backend 全量 **2807 passed、3 项既有 warnings（243.69 秒，exit 0）**，覆盖本轮当前最终代码；涉及脚本与端到端测试的 Ruff 复检及 `git diff --check` 通过。上文首轮2805 passed/1 failed及专项结果保留为过程快照，最终结论以本次完整通过为准。

当前已实现、已完成上述本地验收，仍未 commit、未 push、未部署新后端或研究包。自然同日 baseline、行业与完整可判别配对，以及真实5/10/20交易日成熟收益仍待验，不以测试通过提升G2-FQ3状态或交易权限。
