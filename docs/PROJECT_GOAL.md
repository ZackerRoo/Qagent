# Qagent 长期目标与推进约定

本文件保存项目方向、已认可边界和下一步验收条件，避免后续对话丢失目标。历史状态快照截至 **2026-09-10 14:17（北京时间）**，来自当时的对话记录，**不是实时检查结果**。目标清单于 **2026-09-10** 结合当前代码检查和外部工具能力对比补充；本次文档更新不代表重新核验云端运行、策略效果或部署状态。

## 长期目标

让 Qagent 在云端持续运行 A 股研究、选股和**单一模拟账户**，通过可复核证据增强选股能力与执行可靠性；条件达到后，由用户批准渐进进入实盘。目标不是保证盈利，也不是把无限数据治理当作终点。云端部署成功不等于策略有效。

## 已完成与当前阶段（历史快照）

- `f1dd6d2` 已 push 并部署；部署时 14 项健康检查通过，8 张账本表的行数与哈希一致，16 项 settings 保留。此后运行状态须重新核验。
- 亏损归因报告、补价公平 cursor 修复、离线消融和 Challenger 已存在；后续先检查已有实现与产物，避免重复建设。
- 执行 V2 的 buy `5/5`、sell `5/3` 已达到 `ready_for_shadow`。无需等待更多样本才推进只读 shadow；是否已由自动任务持续运行，仍需先查代码和运行证据。
- 两条 Challenger 各完成 `2,620 / 11,067` 条 5 日收益结果，各有 2 个到期批次，执行头完整配对为 `0`；5 日是持有观察窗口。选股验证仍为 `collecting`，不能据此宣称策略有效。

## 下一步与验收条件

以下 ID 保持稳定，完成后保留证据和日期，不删除或重新编号。状态描述区分已有能力与剩余工作；仅代码存在不等于端到端验收完成。**先推进 G1 和 G2；G3 仅针对阻塞 G1/G2 关键验收的数据有界推进，G4–G6 待排期，不同时开工。** 对比依据见 [工具能力对比](research/tool-capability-comparison-20260910.md)。

| ID / 优先级 | 工作与当前状态（2026-09-10） | 完成条件与复用边界 |
| --- | --- | --- |
| G1 / P0 | 执行 V2 只读观察入口已实现并测试；2026-09-10 主任务完成云端单次验证，14 条源事件/14 个审计样本，V2 buy matched `5/5`、sell `5/3`、unknown `0`，`ready_for_shadow`。同日新增独立归档 wrapper 与 opt-in cron 模板，6 项归档专项测试通过。实现 `979af5b` 与文档 `94cf6f5` 已 push，云端暂存 release 构建及隔离启动通过；主任务从暂存 release 完成只读归档调用（退出 0、产物已保留）。09:00 UTC 已安装固定指向 `94cf6f5` 的独立观察 cron，并以服务用户最小环境手动归档退出 0，仍为 14 条事件/14 个样本。同日 11:56:12 UTC 空闲核验后 guarded helper 退出 0，生产已切换 `94cf6f5`；12:03:39 UTC 新生产手动归档退出 0，仍为 14 个样本且 digest 不变。G1 自然 cron 触发仍待验，持续观察未完成；见[发布暂存记录](research/g1-g2-release-staging-20260910.md)。 | 复用真实 replay/readiness、严格只读 SQLite 连接及既有审计解析，`execution/paper_observation.py` 输出逐样本差异/digest、源 high-water/digest，`scripts/archive_paper_observation.py` 按次原子归档时间、代码摘要、退出码与完整报告，保留 blocked 产物并锁定并发；[单次产物摘要及运行说明](research/g1-execution-observation-20260910.md)。此前未见专用观察 cron 的历史缺口已于同日 09:00 UTC 补齐；下一步核验 cron 自然触发、多次归档及覆盖，重复旧样本不计为新增证据。`execution/shadow.py` synthetic 不能替代真实取证。只读对照不写账本、不建第二账户、不自动接管、不进入实盘；保留原有门槛。 |
| G2 / P1 | 推进选股迭代工作流。已有 Challenger、消融、matched control、walk-forward、漂移和实验产物；2026-09-10 已冻结全特征/移除三个风险预测输入的六模型（各三个固定种子），本地归档且摘要与旧模型完全一致。冻结阶段八项专项测试、六模型加载推理及相关回归 70 项通过（12.56 秒）的历史证据保留。同日补充默认关闭的完整研究输入捕获和隔离前向 collector，10 项专项测试通过、真实六模型没有跳过；主任务此前相关回归 112 项通过（9.48 秒），Ruff 与 diff 检查通过。随后补充带锁的当日 collector 调度入口及 opt-in UTC cron 模板，8 项调度专项测试通过；主任务本轮综合回归 120 项通过（8.94 秒、零跳过），diff 检查通过。实现 `979af5b` 与文档 `94cf6f5` 已 push，云端暂存 release 构建及隔离启动通过；六模型已分发至独立研究目录，摘要核验和六模型实际加载通过。09:00 UTC 已安装固定指向 `94cf6f5` 的独立 collector cron，服务用户最小环境手动验证退出 0、`skipped_unscheduled_day`，随后 09:30、10:00、10:30、11:00 UTC 四份按期产物均退出 0、`skipped_unscheduled_day`，已见 G2 周期运行证据。同日自然扫描成功结束、11:56:12 UTC 空闲核验后生产已切换 `94cf6f5`，env 仅追加 `QAGENT_G2_CAPTURE_DIR=/var/lib/qagent-research/g2-forward-sources` 且 run 脚本确认加载该 env；进程 environ 因权限限制未直接核验。8 张账本表及 16 项 settings 保留，前后端健康；本地全量回归 2051 passed、3 项既有 warnings（312.57 秒）。源目录仍空，首份自然全量 source 待验；未注册线上策略，前向样本 0。见[发布暂存记录](research/g1-g2-release-staging-20260910.md)。见[冻结协议](research/g2-risk-feature-freeze-20260910.md)与[采集入口](research/g2-forward-collector-20260910.md)。 | 从亏损归因冻结一个候选假设，复用现有协议、digest 和工作流，固定同口径基线、未用于调参的样本/时间窗口、数据与代码版本、配置及成本假设；可从归档记录复现一次实验，报告样本外净超额、回撤、换手、行业暴露及支持/否定假设的证据，失败结果也归档。成功扫描会删除逐批 checkpoint，最终缓存只含 card 子集，历史 08:58 UTC 扫描 running、9/36 的状态保留于发布记录；后续 `full-scan-20260910104100-63a4ef30` 于 11:55:57 UTC succeeded、36/36，空闲后已完成生产切换及源捕获配置。下一步核验首份自然全量 source、采样日真实 collector 产物及前向覆盖；非采样日跳过不计为真实前向样本。两组使用相同可用股票集合，共同排除旧行情/缺失日期及全特征缺失，归档排除身份和覆盖比例。独立信号窗口 2026-09-11 至 12-31，每 10 交易日采样且同日 first-ready 去重；若首日实际归档合格信号，20交易日标签最早 10-19 到期。已有登记能力先复用，不另建实验库。离线通过后进入 shadow，升级由用户决定，不自动接管账户；本候选不放宽真实风控。尚未成熟的选股窗口不得阻塞 G1。 |
| G3 / P1 支撑项 | 有界治理关键到期价格和 fuyao 缺失。已有补价、cursor 与覆盖检查；历史 `30 no-row / 3 error` 的当前影响待刷新。 | 仅处理阻塞 G1/G2 关键验收的数据：记录受影响窗口、原因证据、重试/补价预算与停止条件；补齐项有来源、时间和对账结果，不能补齐的保留缺失及影响说明。复用既有补价与覆盖检查，不另建全量数据治理工程；不得由 no-row 推断停牌，不得编造收益。 |
| G4 / 后备 | 执行边界与成本压力研究，待排期。已有 A 股规则、版本化费用、成本敏感性与 replay 能力。 | 优先复用 `common_execution_delta`、`matched_control`、现有规则及真实重放做可执行净收益分解；对适用的涨跌停、停牌、T+1、流动性、滑点和费用假设做有界压力对照，记录来源、差异与无法建模的限制。形成可复核报告即可，不凭外部框架默认模型宣称 A 股成交精确，不新增生产门禁。 |
| G5 / 后备 | 相关性与组合构建研究，待排期。已有单股、行业、主题、ETF 重叠和市场状态约束，以及漂移监测。 | 先复用既有 constraints 和漂移指标，在固定样本/成本口径下评估相关性或组合构建改动对回撤、集中度、换手和净收益的影响；形成支持或否定改动的离线证据，未获用户批准不改变账户规则。不得把现状描述成“没有风控”。 |
| G6 / 实盘前置研究 | 券商适配、断连重连和委托成交对账，待排期；尚未选择并验证可用接口。 | 用户确定目标券商/接口及权限后，形成接口能力映射、订单状态机、重连恢复和幂等/成交对账方案，并以隔离测试证据验证关键场景。开源 gateway 存在不代表用户具备权限；本项目项只形成方案与测试证据，实际接入实盘仍须用户明确批准。 |

可选架构项：只有当前运行证据表明 API、调度或研究任务存在资源争用时，才评估 worker 隔离；先证明瓶颈和最小修复收益，不因此立即迁移数据库或重构部署。

工具借鉴边界：选择模块时固定版本并核验许可；不做整套替换，也不将多租户 SaaS、计费、多市场或无界自动因子/RL 搜索加入当前目标。

## G2 研究诊断补充（2026-09-11，只读快照）

已刷新 [Challenger 成熟结果与覆盖](research/challenger-maturity-20260911.md)：以上一完整交易日 09-10 为到期截止，两条显式候选各完成 3,688 / 16,601 个 5 日标签（22.2155%），完整执行头配对仍为 0。未算 12,913 个槽位中，12,864 个的两项调整价格数值已在缓存，不能全部归为缺价；完整价格质量与解析过程仍待核验。已有 Regularized 研究 lane 的三个 5 日执行头完整配对成立，候选平均净超额 +0.179833%、基线 -1.631714%，但短窗样本不足以证明策略有效或升级。09-11 当天到期但无当日缓存的槽位另列；上述研究 lane 与 paper legacy 账户业绩严格区分。本补充仅记录只读研究和文档，不改变既有 G2 门槛、冻结候选或交易权重；未提交、未 push、未部署。

同轮 [G2 选股行为对照](research/g2-selection-behavior-20260911.md) 的离线比较脚本已实现，12 项专项测试通过；原历史输入不存在，具体股票差异尚未计算，不提升 G2 状态。主任务综合回归 58 项通过，Ruff 与 diff 检查通过；这些验证不替代自然前向样本和成熟标签验收。

09-11 09:13:22 / 09:14:00 UTC 主任务分别只读复核两条候选的真实缓存质量：各自未算 12,913 个槽位为无行 34、所需价不足 15、两价正但 `_unsafe_exact_row` 拒绝 12,837、完整质量通过 27，基准均通过。上段“完整价格质量待核验”保留为此前快照，现已确认多数未算来自质量拒绝，不能归为 resolver 遗漏；27 个可用缓存槽位待自然周期验证。未证实预算跳过缓存的代码缺陷，禁止放宽价格来源及完整质量规则。只读诊断脚本已实现、2 项专项测试通过并在云端 stdin 执行成功；未提交、未 push、未部署，未修改生产逻辑或冻结模型。详见上述成熟结果报告的后续质量小节。

同日运行证据更新：G1 已核验首次自然 cron，08:10 UTC 归档退出 0，V2 buy 6 / sell 9 matched、unknown 0；这更新了上表 09-10 的“自然触发仍待验”历史状态，持续观察、多次归档和覆盖仍待验。G2 09:00 UTC collector 为 `waiting_for_source`，09:03 UTC 本轮自然扫描为 running、10/36；首份自然全量 source 和真实前向样本仍待验，不能用运行中的扫描或等待状态宣称采集完成。

09-11 09:30:44 UTC 主任务后续只读质量快照：未算槽位分类为 missing 34、unsafe 12,826、nonpositive/所需价不足 15、ready 38。此前 27 是 09:13–09:14 的历史快照；此次 ready 增加 11、unsafe 减少 11，只能说明通过完整价格质量检查的缓存槽位增加，不能证明已经自然写入标签或收益成熟。此前未提交、未 push、未部署的表述保留为当次交付状态，本轮提交由主任务后续集成记录，尚不提前确认 push 或部署。

同日 09:30:57 UTC 的 [G2 自然前向信号验收](research/g2-live-acceptance-20260911.md) 更新运行证据：当日扫描仍 running、15/36，完整研究 source 0、真实 ready signal 0，四份自然 collector 均为 `waiting_for_source`。真实选股行为对照仍待验，不能用运行进度、合成测试或退出 0 代替真实对照与成熟收益。

## G7 / P1：模拟盘持仓更新及时性评估（2026-09-11）

本项来自用户本轮有界授权，不改变 G1–G6 的历史记录与验收门槛。[持仓更新及时性方案](research/paper-update-timing-20260911.md) 已完成静态调查：分钟 getter 失败/空表可降级日线，日线能影响模拟成交与估值；整个 automation cycle 结束后再等待 interval，研究同步阶段可能扩大更新间隔。09-11 主任务两次云端观测到 Sina 分钟超时，09:31:18 UTC 最新 paper_update stage 为 error；未证实错误成交或具体漏单，不宣称修复。

状态：方案已完成，生产拆分实施待方案确认；没有直接切频率。完成条件为先获取交易时段内逐阶段和逐股票有效行情时间的延迟基线，再在隔离环境验证研究长阻塞下独立到期、所有账户写入口共享唯一 writer/fence、失租拒写、稳定 slot 幂等与部分完成恢复，保留单账本和既有降级/执行语义。具体场景及证据见报告。本轮不启用新生产调度、不重写历史交易、不增加账户或实盘权限；测试、push、部署与线上效果分别记录。

## 固定边界

- 保留唯一 ledger、已有历史和当前规则；研究与 shadow 隔离，不得未经授权自动提权、接管模拟账户或进入实盘。
- 子代理承担有明确范围的代码和文档实施，主代理负责拆解、只读调查、风险边界、review、测试验收与集成；遵守 `AGENTS.md` 的生命周期及会话清理要求。
- 可选择性复用成熟开源内核，避免为了复用而替换整套系统。
- 系统默认保持非冗余：新增能力必须明确唯一主流程消费者或隔离研究消费者、与现有实现的复用或替代关系、可衡量验收指标、上线或升级条件，以及失败或长期无消费时的停用或删除条件；默认不并行长期维护同类实现。Challenger 未晋级时仅保留必要证据，停止调度并移出运行链路；停用或删除实现不得删除历史账本和审计证据。
- 用户认可的既有门槛达标后推进下一阶段。若确需新增门槛，说明新增风险、依据和为何现有验收不足，避免无限加门禁。

## 每次接续工作

1. 每次本仓库对话或接续任务先完整阅读本文件；文件缺失或不可读时明确提醒。再刷新与本轮工作相关的当前代码、调度、产物及云端状态；历史快照不替代实时证据。
2. 保持长期目标、用户已认可门槛和未完成项验收条件连续；先核验是否已有实现，再确定缺口。
3. 接续前盘点候选能力的消费者、调度和同类实现；未满足上述非冗余条件的能力不得进入或继续留在长期运行链路，未晋级 Challenger 按约停止调度并移出运行链路，只保留必要证据且不删除历史账本或审计证据。
4. 完工后更新证据与日期，分别报告**已实现、已测试、已 push、已部署**四个状态；未执行的环节明确写未执行，不能互相替代。
5. 新的未完成项必须有稳定 ID、状态和明确验收条件；更新时保留历史快照与完成证据。等待应对应具体尚未成熟的窗口，不能泛化为整个项目停止推进。
6. 比较其他工具是确定候选增强的依据，不代表自动扩大本轮 scope；先复用已做能力，再推进用户本轮指定目标。

## G7-R / P1：因子影子阶段运行可靠性（2026-09-23）

`factor_shadow` 的 90 秒 ExactPriceRepairBudget 原先只在 provider 调用之间协作检查，不能限制卡住的 BaoStock 网络调用。本轮将该研究阶段隔离到一次性 spawned worker：超时由父自动化终止 worker，并以 `partial`、`hard_timeout` 和 `worker_terminated` 记录为 deferred issue；未收到完整结果不得写为成功，后续周期仍可重试。该隔离只覆盖研究补价/结果解析，不改变唯一 paper ledger、交易执行、Ranking 权重或策略选择。发布提交 `f6ab404d4c5bd398ba67b965b1e41729fafca4bf` 已部署，云端当前 release 为该 SHA；2026-09-23 部署证据 `result.json` 显示 `ledger_equal=true`、`settings_equal=true`、`scheduler_enabled=true`。部署后 `/api/health` 正常，且无 running cycles/stages。该部署仅确认运行可靠性修复已上线，不替代后续自然运行证据，也不改变目标结论或交易规则。

## 20260911 集成验收

主任务全量回归 **2065 passed、3 项既有 warnings（252.35 秒）**，Ruff 与 diff 检查通过。本轮 2 个离线脚本、2 个测试文件及研究报告待主任务提交；提交和 push 成功状态尚未记录。生产逻辑、冻结模型与唯一模拟账户未修改，纯离线工具无需部署。G2 真实选股行为对照与成熟收益验收、G7 生产调度分离上线仍未完成，测试通过不能替代这些结果。

## G3 有界行情来源验证补充（2026-09-13）

按本轮范围新增隔离 Tushare Relay 检查脚本，未接入生产。主任务实测 `000001.SZ` / `20260911` 日线 HTTP 200、code 0、1 行，并追加同范围请求确认 OHLC 与现有云端缓存一致；不证明上游独立。复权因子为 transport_error，历史分钟 HTTP 202 未验收，实时分钟 HTTP 503；周日不能验证交易时段实时性。能力声明中的 300 秒缓存仍待交易时段核验。G3 保持未完成，不改变 G1/G2、价格质量规则、冻结候选或模拟账户。脚本后续加强数据校验，26 项 mocked 专项测试及 Ruff/diff 检查通过；首次实测不替代修订版实测。已实现、已测试，未 push、未部署。详见[验证记录](research/tushare-relay-validation-20260913.md)。

## G3 / G7 双服务目录与接入进展（2026-09-13 后续快照）

用户本轮要求扩展为双服务全目录验证和有用能力接入；[接入报告](research/tushare-relay-integration-20260913.md) 已完整盘点基础手册 80 项及 ProMax 实际表格 271 项，保留前段四接口历史快照。主任务新读到认证机器目录 298 项、259 启用、39 禁用；这是能力声明，不是业务成功数。基础服务 HTTPS 443 本次连接失败，未向 HTTP 传密钥，待供应方提供可验证 HTTPS 入口后再验。

后续 17 项正式 GET 中，daily/adj_factor 各取得 1 行并通过业务单样本校验；moneyflow、daily_basic、stock_basic、trade_cal、income、balancesheet、cashflow、fina_indicator、get_industries 共 9 项取得非空且结构通过的返回，业务语义及完整覆盖未验。get_index_stocks 空表，index_daily/a_share_mins/rt_min_daily 为 503，stk_mins 为 202 minute_data_pending；rt_min 取到 09-11 上交易日 1 行，查询当天为周日 09-13，实时性未验。本轮复权因子成功更新了此前传输失败的证据，不能外推分钟成功。完整只读 probe 已结束，数量与限制见下段。

本轮扩展只读客户端及研究适配已实现，完成主任务本地验收、默认关闭；最终全量及隔离验证见下段，未 commit、未 push、未部署、未启用默认源。不修改模拟账本、冻结模型或价格信任规则；G3、G7 均未完成，G1/G2 门槛和历史状态保留。下一步验收为基础服务 HTTPS、真实历史分钟、交易时段数据时间与缓存延迟，以及有用业务数据的语义和覆盖；不以接口数量或进程退出 0 替代这些条件。

主任务在 17 项套件之外又正式请求 rt_k，HTTP 503、upstream_pool_exhausted；a_share_mins 的 5 分钟小窗口复查也返回同类机器错误。累计正式尝试 18 种 API，实时/历史分钟仍待验；服务商可据错误码检查上游池，具体原因未确证。

后续全目录 probe 已结束：原安全筛选 252 项加补查 fund_portfolio，共 253 个独立 API 全部 HTTP 200、no_rows，仅说明本地 probe 无样本，不能算正式业务失败或成功验收。目录剩余 6 项启用接口未请求（4 项组合管理，以及因名称含 account 保守排除的 stk_account/stk_account_old；后两者业务含义和可用性未判定），39 项禁用接口未请求。probe 退出 1 表示样本未验，不是传输失败。归一化日线真实取得 1 条；基本面映射字段一致但未消费字段不同的修订处理已完成 smoke，成功结果见下段。前版相关回归 126 passed 保留为历史证据；最终本地验收已通过，未 commit、未 push、未部署、默认关闭，G3/G7 状态不提升。

最终实现隔离与 smoke 更新：只读研究入口位于 `backend/qagent/providers/tushare_relay_research.py`、显式 opt-in，原 strategy_data 默认工厂未接线且目录无改动。主任务核对 144 个研究源文件/依赖当前与 HEAD 摘要一致（`309625c7be262e81bc22c35f40f563f7c497d78252ac6b4dfabd0e168642c848`），保留冻结研究指纹。基本面 smoke 已成功返回 1 snapshot，growth/valuation 均有，asof 09-13、估值日期 09-11、财报期 06-30；消费字段一致的修订保留 unused_field_revision_difference 提示。迁移后 134 项相关回归通过（2.23 秒）。首轮全量 2168 passed、1 manifest 失败保留为历史快照；隔离调整后最终全量 **2171 passed、3 项既有 warnings（237.88 秒、exit 0）**，Ruff 与 diff 检查通过。已实现、已完成本地测试验收，未 commit、未 push、未部署、未启用默认源；基础 HTTPS、历史分钟、实时性和全业务覆盖未验，G3/G7 状态不提升。

## G3 / G7 分钟研究与数据源缺口补充（2026-09-13 后续快照）

本轮新增单股单日分钟研究归一化 `providers/tushare_relay_minutes.py` 与统一 stdout JSON 采集入口 `scripts/collect_tushare_research.py`，复用既有配对日线/当前基本面研究适配；不连接数据库或接线默认消费者。分钟校验身份、上海时区、日期、交易窗口、OHLC 和重复时间，明确频率、成交量单位、完整覆盖及实时性未验；rt_min 日期仅校验响应，不提供历史查询保证。[数据源缺口报告](research/data-source-gaps-20260913.md) 已按分钟时效、配对复权覆盖、财务 PIT、基准/行业后备、生产消费者验收排序，并记录现有多源行情、历史证据和风控能力，不新增目标 ID 或扩大交易权限。

主任务真实统一 CLI 请求 `CN:000001` / `2026-09-11`，包含当前基本面及 rt_min，退出 1、incomplete：日线 http_error、基本面 no_rows 且 transport_error、分钟 transport_error，均无行。随后 15 秒超时、零重试定向复查，daily 仍 transport_error、无 HTTP 状态；rt_min 经归一化器真实成功 1 行，最新时间 `2026-09-11 15:00:00+08:00`、`realtime_freshness=not_current_session`。此单样本补齐归一化实测，不改变原 CLI 批次失败事实，也不证明周日当日实时性、历史分钟覆盖、上游长期稳定或完整业务链路成功；此前成功/失败样本继续保留。

本轮最终相关回归 **133 passed、1 项既有 warning（2.03 秒）**，Ruff 与 diff 检查通过，冻结研究摘要仍为 `309625c7be262e81bc22c35f40f563f7c497d78252ac6b4dfabd0e168642c848`。本轮未重跑全量，前段 2171 passed 是上一轮历史结果。已实现、已完成相关本地测试，未 commit、未 push、未部署、未启用默认源，唯一模拟账本与冻结模型不变；G3/G7 保持未完成，G1/G2 原门槛保留。

## G3 / G7 模拟盘与日常任务日线后备接线（2026-09-13，本地验收通过）

用户本轮明确授权将新 Relay 用于模拟盘和日常任务。本轮实施范围为默认关闭的日线后备：`QAGENT_TUSHARE_RELAY_MARKET_ENABLED=true` 且配置 `QAGENT_TUSHARE_RELAY_KEY` 后，原有 CN 行情来源全部尝试后仍缺失的整只股票才进入 Relay；不补已有股票内部的缺失日期。研究专用开关不代替该市场开关。每次最多补 2 只股票、单批调用，5 秒请求超时、零重试、30 秒启动预算；首次上游或结构失败停止该批并熔断 300 秒。详细接入契约见[接入报告](research/tushare-relay-integration-20260913.md)。

主任务 review 发现逐请求选择最新因子锚点会使增量缓存混入不同前复权口径，因此运行范围收敛为原始 `daily` 后备：不请求因子，调整价留空、类型为 `raw`，来源 `tushare_relay_promax_daily_raw`；显式锚点的日线/因子配对仍属独立研究入口。成交量由手换算为股，成交额由千元换算为元；上海时间 15:00 前排除当日日线。它可服务模拟盘原始日线及基本日线消费者，不能补 G2 复权价格缺口，不接基本面或分钟。分钟的完整性、单位和交易时段实时性仍未验，原分钟/快照路径保留。后备可能影响未来日线输入和既有降级估值/模拟执行；不修改冻结模型、唯一账本的历史或交易规则，不新增账户。

主任务本轮只读云端核验健康正常，当前 release 为 `94cf6f5057723460a88becd0c5e44f864a6cc53c`，backend/frontend runit 均运行；这是旧 release 当前健康证据，不是本轮代码部署或 Relay 启用证据。发布只读 preflight 退出 1，原因为 `automation scheduler is enabled`，未停止调度、重启或部署，受控发布待完成。本轮原始日线实现已通过主任务本地验收，未 commit、未 push、未部署、未配置云端启用。G3 仍需阻塞窗口覆盖及自然运行验收，G7 仍需分钟时效与原定调度验收，均不提升为完成；保留上文历史结果与 G1/G2 门槛。

主任务以新运行适配器真实请求 `CN:000001` / `2026-09-11`，5 秒超时、零重试，结果 0 行、`transport_error`；脚本退出 0 表示安全处理失败，不是数据可用性通过。本次不据此启用云端数据源，云端保持原状，可用性仍待验。

主任务全量后端回归 **2215 passed、3 项既有 warnings（231.82 秒）**；该次收集发生在新运行测试文件定稿前，不能将其描述为覆盖最终全部新增测试。最终相关回归 **107 passed、1 项 warning（2.42 秒）**，包含 18 项运行适配专项，reviewer 单独复核同 18 项通过；Ruff/diff 检查通过。研究摘要仍为 `309625c7be262e81bc22c35f40f563f7c497d78252ac6b4dfabd0e168642c848`。已实现、已完成上述本地测试，未 commit、未 push、未部署、未启用云端；真实可用性和自然运行不由本地测试替代。

## G3 / G7 实际启用与次日观察（2026-09-13，已部署启用）

最终运行复核：主任务确认 `current` 指向 `8d4f204`，前后端 runit 均为 run，cron 进程 PID 21 存在；这不替代下一次自然研究采集验收。

用户后续明确授权实际启用并于次日观察，更新此前未启用的历史快照。提交 `8d4f204e62e9092014fe9ea3d63daad1bf81de71` 已包含原始日线后备、独立研究归档和受控发布 helper；push 状态尚未确认。生产新 release 暂存构建进行中，尚未切换生产，不能提前称运行后备已生效。

独立研究包已复制至 `/opt/qagent-research/tushare-relay-20260913`，归档脚本及安装器 SHA 已核验；`/etc/cron.d/qagent-relay-research` 已安装，周一至周五 08:30 UTC（北京时间 16:30）采集固定股票 `000001`、`600519` 的日线、当前基本面及 `rt_min` 研究数据，归档至 `/var/lib/qagent-research/tushare-relay`。独立凭据 env 权限为 `0640`、属主组 `root:luozhenkun`，文档不含密钥。研究归档不写模拟账本；安装 cron 不等于自然触发或数据可用验收。

单股、09-11 日期的手动研究归档正在运行，产物及退出状态待主任务补录；生产暂存构建、受控切换、测试和运行状态也分别待验。次日检查实际后备配置、健康及自然任务产物，按接口记录行数、错误和行情时间；G3/G7 仍未完成，分钟仅供研究，既有账户、规则和历史保留。

后续验收：本轮后端全量 **2246 passed、3 项 warnings（239.71 秒）**，收集早于最终 helper 和新增归档用例；最终专项 **49 passed（1.96 秒）**，包括发布 helper 17、归档 14、市场适配 18 项。Ruff 与 `bash -n` 通过。云端 `uv --frozen` 依赖安装、`npm ci`/构建及隔离启动均通过。手动归档最终退出 1、`collector_timeout`（180 秒），已保留 `/var/lib/qagent-research/tushare-relay/20260913T104714.826464Z-5546f1e60b224b4bbe0593f5a75824c8.json`，无有效数据；cron 安装状态不变。生产空闲 preflight 已通过并核验 16 项 settings，受控发布执行中，尚未取得切换成功证据。

最终切换验收：受控发布退出 0，`/var/tmp/qagent-rollout-relay-orqnnd2o/result.json` 确认 release 为 `8d4f204e62e9092014fe9ea3d63daad1bf81de71`、`ledger_equal=true`、`settings_equal=true`、`scheduler_enabled=true`，8 张账本表稳定、16 项 settings 保留。API health 正常，provider-status 的 Relay market 为 `configured`，确认运行服务已读取市场开关和凭据；进程 environ 因权限限制未读取，不据此声称已直接核验。原始日线后备已部署启用，未来缺股请求可触发，但尚无实际 Relay bar 成功证据。后端研究开关仍关闭；独立 cron 采集器显式启用研究功能，两者不混同。下一工作日北京时间 16:30 将按已安装 cron 采集两只股票，自然触发与数据有效性待验；不代表全市场、全接口启用或选股能力提升。已实现、已完成上述测试、已部署启用，尚未 push；本段文档待主任务 review 后提交，G3/G7 原有未完成验收继续保留。

## G2 等待窗口内的共识选股候选（2026-09-13）

主任务本轮已确认此前提交 `75beb65` push 至远端，更新上文未 push 的历史状态；这不代表本段新候选已 push。按用户推进选股增强的要求，新增隔离 `scripts/rank_g2_consensus.py`，复用现有 ready signal 校验，在两变体相同股票集合上固定按较差名次、等权平均名次、股票 ID 排序，生成 Top5/10/前10% 及分歧解释。协议 `g2-consensus-minimax-v1` 不调参、不使用收益标签，输入/实现/协议摘要可追溯，输出原子发布且不覆盖。新旧脚本相关测试由实现子任务运行 **23 passed（0.26 秒）**、Ruff 通过，主任务最终 review 和真实候选运行待验。

主任务已确认 09-11 原始真实 G2 信号存在且通过现有校验，配对股票 5,541 个；此前首份信号待验的文字保留为历史快照。该配对集合不等于全特征完整集合。已观察两变体当日排名，新增规则不能追溯视为当日前预注册；两变体同源相关，共识不证明收益改善。该候选属于 G2 研究，未修改冻结模型/实验、旧信号、生产排名或单一模拟账户，无自动升级。后续同集合、同成本、同窗口完整匹配收益仍待验，G2 不提升为完成。详见[共识选股候选报告](research/g2-consensus-ranking-20260913.md)。本候选已实现、完成上述专项测试，未 push、未部署。

主任务后续复跑上述 23 项测试通过，以真实 5,541 行 signal 生成共识名单退出 0，并用独立排序复核 Top10 一致。新 Top10 与两条原名单分别重叠 2/10、1/10，最大行业权重 40%；完整名单片段和源/协议/产物摘要已归档至上述报告。本轮完成真实选股行为对照，不代表成熟收益验收；该次候选仍未 push、未部署，未改冻结实验和模拟账户。

## G7 独立持仓更新实施进展（2026-09-13，本地验证）

用户已批准独立 10 分钟 tick 与测试后受控部署，本轮新增默认关闭的独立交易时段调度、同一 SQLite 账户的跨线程/进程 writer、成功 slot 重放和异常重试。账户相关读写在不超时 flock 所有权内完成；进程死亡释放，暂停 owner 不会被 lease 超时接管。master stop 同时停止 tick，行情等待和 writer 等待仍可能超过 10 分钟目标；过期 slot 不制造补跑。窗口外旧 automation 更新保留，单账户、规则、历史和研究隔离不变。详见[实施补充](research/paper-update-timing-20260911.md)。

子任务已验证原引擎/协调 84 项、API automation 79 项（1 项既有 warning）；新增专项及主任务全量结果另行补录。当前已实现、完成上述本地测试，未 commit、未 push、未部署；不能由锁或测试推断行情实时性。G7 正常交易时段逐股票有效行情延迟基线、自然 10 分钟运行及切换对账仍待验，保持未完成，不改变 G1/G2 门槛和此前历史快照。

主任务最终稳定代码全量回归 **2319 passed、3 项既有 warnings（238.62 秒）**，Ruff 与 diff 检查通过。首次边实施边测试的快照为 2291 passed、9 failed，包含测试替身缺少 writer 工厂绑定以及运行期间源文件摘要变化；修正后由上述稳定全量确认通过，保留首次结果作为历史，不绕过 manifest 校验。

发布前主任务只读检查云端 24 项 walk-forward 记录，状态均为 none/rejected，未发现符合条件的 V3。API/storage 被既有宽范围研究指纹覆盖，本轮变化会使旧缓存指纹失效；G2 指定文件未变化，不据此修改冻结输入或放宽准入。当前已实现、已通过上述本地验收，提交与受控部署正在准备，尚未 push、尚未部署本轮 G7；自然运行、行情延迟基线与切换对账继续待验。

### G7 受控部署验收（2026-09-13，周日）

主任务确认提交 `fba984a1aec84eb0099d5710291cb1a87072513a` 已 push 并部署；受控 helper 退出 0，云端 `/var/tmp/qagent-rollout-relay-gsnz3wg1/result.json` 的 `ledger_equal=true`、`settings_equal=true`、`scheduler_enabled=true`，8 张账本表与 16 项 settings 保留，研究周期仍为 1800 秒。前后端 runit 均为 run，writer 锁文件属主为 `luozhenkun`、权限 `0600`。

独立 tick 已启用，目标间隔 600 秒；主任务只读状态 API 返回 `configured=true`、`enabled=true`、`status=outside_session`、`attempts=0`、`completed=0`、`last_error=null`，符合此次周日观测。上述证据更新此前未部署的历史状态：已实现、已测试、已 push、已部署启用；不能据此宣称周一自然 cadence 或行情新鲜度已通过。交易时段实际触发、有效行情时间与延迟基线继续待验，G7 保持未完成。本段为部署证据补录，未修改代码或历史账本。

## G2 完整特征子集诊断与运行续验（2026-09-13）

复用已有两模型及共识排序，新增独立原始特征完整性审计；从 09-11 真实 5,541 个配对股票核验出 3,518 个共同完整样本（63.4903%），三路均过滤既有完整集合排序，不重算子集 minimax。该集合仅为附加完整案例诊断，不新增资格门禁、不从冻结实验排除样本；完整案例选择偏差、缺失值不能归因为排名差异的边界见[审计报告](research/g2-complete-cohort-audit-20260913.md)。原冻结配对集合、模型、生产排名及模拟账户规则和历史保留，成熟收益仍未验，G2 不提升为完成。

主任务复跑相关测试 **40 passed（1.33 秒）**、Ruff 通过；真实 CLI 退出 0，独立原始特征 ID 集合及三路原排序复核一致，来源与产物摘要归档于报告。完整结果已独占归档至云端 `g2-consensus-candidates/2026-09-11-complete-cohort-75d7b0bb18c9.json`、权限 `0400`，本地/云端 SHA 一致，绝对路径及摘要见报告。本轮未重跑全量，已实现、已完成上述本地验收，未 commit、未 push；研究结果已归档，脚本未部署到生产，纯离线工具无需生产部署。主任务同步只读续验当前服务健康，tick 仍 `outside_session`、`attempts=0`，Relay 研究归档无新增；周日状态不替代交易时段自然触发、分钟行情时效或新研究数据验收，G3/G7 原有待验项保留。

## G2-FQ1 / G2 有界子项：财务缺失与现金流质量（2026-09-13）

用户本轮授权推进财务质量研究，保持 G1–G7 历史与原有门槛。[研究报告](research/financial-quality-research-20260913.md) 记录真实 09-11 信号的财务完整 3,522/5,541（63.5625%）、市场完整 5,454/5,541（98.4299%），全部完整仍为 3,518。财务缺失 2,019 只，earnings_yield 缺失 1,883；现有规则仅正 PE 计算倒数，因此缺失不能直接归为上游缺数。原 Top10 三路各有一只仅缺 earnings_yield 的股票，身份及完整缺失组合已归档；未重排或改变冻结资格。

状态：覆盖诊断已完成，缺失审计扩展已实现、子任务相关 **42 项测试通过**且真实 CLI 退出 0，主任务复跑 **42 passed（0.30 秒）**及 diff 检查通过；现金流隔离入口由子任务实施，最终验收待主任务补录。主任务对两只股票各两个财务接口的四次真实只读请求均 transport_error、无行，现金流当前有效样本待上游恢复。本轮未 commit、未 push、未部署，不能称已补齐或提升选股。

完成条件：先区分非正 PE 与真实缺数，保留原始缺失及影响；在最多两只股票预算内核验同报告期现金流/利润的来源、公告及修订语义，保留错误和不可计算原因；再以预先固定的同集合、同成本、同窗口隔离对照检验利润质量假设。当前数据不回填 09-11 冻结信号，不变更冻结模型、生产排名或唯一模拟账户。自然样本和成熟收益继续待验，G2-FQ1 与 G2 均不标记完成。

主任务最终本地验收补录：现金流隔离工具已实现，严格匹配 `comp_type=1`、`report_type=1` 的同报告期数据，以正总净利润 `n_income` 或正总收入 `total_revenue` 为分母，保留来源、取得时间及摘要，不接 Challenger 正式权重、不生成排名。组合相关测试 **57 passed（0.58 秒）**，含现金流 15 项专项，Ruff/diff 通过；四次真实 transport_error、无行的事实不变，真实有效样本待验。本轮未跑全量、未 commit、未 push、未部署，不据本地验证提升 G2-FQ1 或 G2 状态。

## G3 文档调用方式与代理差异核验（2026-09-14）

按用户本轮要求核对供应方原始文档，主任务确认云端存在 HTTPS_PROXY；文档 requests 调用 cashflow、period=20241231 成功 HTTP 200、2 行，而直连 ConnectTimeout。客户端原 trust_env=False 跳过了环境代理，本轮最小修正为真实请求尊重环境配置，保留固定 HTTPS、密钥请求头、证书校验、禁止重定向与显式 MockTransport 隔离。市场 5 秒超时和既有预算不增加；无向基础服务 HTTP 地址发送密钥。

文档方式单次实测仍有 Mac/云端不一致：daily 为 Mac 200/158 行、云端 502；cashflow 为 Mac 502、云端 200/2 行；rt_min(freq=1min) 为 Mac 503、云端 200/1 行但时间仍为 09-11 15:00。不将网络配置差异外推为所有失败的根因，不声明稳定或实时性通过。详见[调用方式核验](research/relay-document-method-20260914.md)。本轮已实现，最终相关回归 **125 passed（1.62 秒）**、Ruff/diff 通过，未跑全量；主任务云端隔离客户端仅内存改变 trust_env=True、30 秒超时零重试，经目录查询和表格解析取得 cashflow/600519.SH/20241231 两行（总耗时 7.03 秒），未改服务文件。未 commit、未 push、未部署；市场 5 秒可用性、现金流配对及自然覆盖仍待验。G3/G7、G2-FQ1 保持未完成，原冻结模型、账本与门槛保留。

## G3 双文档来源接入补充（2026-09-14）

用户本轮明确接受基础服务 HTTP 明文风险并要求两个文档来源接入，更新此前未授权向基础 HTTP 发送密钥的历史边界。[双来源接入报告](research/documented-data-integration-20260914.md) 记录基础目录 80 项、其中 79 项可经 HTTP 通用研究查询，`pro_bar` 显式拒绝；这不表示 79 项已验证或全部接入业务消费者。独立日线后备接在现有来源及 ProMax 之后，默认关闭、独立凭据及 HTTP 显式开关，保留每次两股、5 秒、零重试、30 秒预算和熔断；原始价格不补 G2 调整价、不接分钟或快照。ProMax 代理修正与既有研究边界保留。

主任务按基础文档在 Mac/云端分别成功取得同一条 20260825 日线，开盘 11.57、收盘 11.59，各 1 行、约 0.40/0.23 秒；仅为单样本调用证据。旧 09-11 分钟不能作为 09-14 实时交易价格。后备/发布 helper 相关 **71 passed、1 项既有 warning（2.92 秒）**、Ruff/diff 通过；主任务后端全量进行中，暂存发布及受控切换待验。已实现并完成上述相关测试，尚未 commit、未 push、未部署本轮改动；不宣称自然覆盖、财务配对或选股增益通过。G3/G7、G2-FQ1 未完成，既有目标、冻结模型、唯一账本及门槛保留。

主任务后续本地以新增 DatahubcoMarketDataProvider、5 秒运行参数请求 CN:000001/2026-08-25，真实取得 raw 日线，收盘 11.59、成交量 99,488,115 股、来源 datahubco_daily_raw、errors=[]，补齐归一化适配单样本；不等于云端自然运行验收。云端服务 env 已有代理配置名称，无需追加网络配置，具体值不输出。全量和发布结果仍待主任务续补。

### 双来源最终部署验收（2026-09-14）

主任务补录全量 **2,412 passed、3 项 warnings（241.07 秒）**，收集早于最终 installer 测试；最终相关 **158 passed（1.77 秒）**，installer 路径修正后 **8 passed（0.04 秒）**。提交 `57fc24c00cda4ecd510989cc54a964841647d8b4` 已受控部署，云端 `/var/tmp/qagent-rollout-relay-4sxprzxh/result.json` 的 ledger_equal/settings_equal/scheduler_enabled 均 true，8 张账本表和 16 项 settings 不变。API health ok，datahubco 与 tushare_relay_market configured，基础 HTTP 已按明确授权启用；更新前文尚未部署的阶段快照。

云端新 release 加生产 env 的真实 collector 查询 daily/000001.SZ/20260825 成功 1 行，开盘 11.57、收盘 11.59，observed_at 为 `2026-09-14T02:47:28.688988+00:00`，digest `53627c6b0b694553678e3902d25e692492dc887b861fee0a89dc9b2455af4090`。tick enabled/waiting、attempts 1/completed 1、last_error null，但恢复首 slot 迟到 422 秒，不能声称无中断或准点验收。

研究 cron 更新至 20260914 新 bundle 与 qagent.env，原时间、两股、180 秒不变；旧 cron 私密备份及新 SHA 详见[接入报告](research/documented-data-integration-20260914.md)。首次 installer 因原备份目录安全约束拒绝且未改 cron，独立目录修复提交 `0d0f055` 临时复制执行成功，无需重新服务发布。已实现、已完成上述测试、已 commit、已受控部署启用，未 push；本段文档补录未提交。分钟实时性与自然采集仍未验，无选股有效性结论，G3/G7、G2-FQ1 原门槛与未完成状态保留。

主任务同云端 collector 再验最新完整交易日 000001.SZ/20260911，成功 1 行，开盘 11.82、收盘 11.74；observed_at `2026-09-14T02:48:19.191712+00:00`，digest `b73c9b905f1a945c1f082965863629e57443ed36a5b1a57bebda8da33db6ea88`。补齐最新完整交易日日线单样本，不替代当日分钟或全市场自然覆盖验收。

## G2-FQ1 财务质量、每日估值与预告有界阶段（2026-09-14）

用户批准三类增强，继续沿用 G2-FQ1。[财务增强方案](research/financial-enrichment-20260914.md) 已核对现有共识排序、行为比较、候选队列及 FactorResearchRepository，明确 current-observation 四接口采集并不等于新候选登记、正式权重接入或选股提升。拟实施最多两股、cashflow/income/daily_basic/forecast 四接口、每请求 30 秒零重试，独立错误与原始证据摘要；不回填旧冻结信号、不修改模型或唯一账户。

本阶段验收为真实两股四接口证据及日期、数值、修订和缺失语义校验。随后在新协议事前固定规则、相同股票集合/窗口/成本下做独立前向比较，复用既有存储与到期结果能力；原 Top10/Top5 冻结 Challenger 不是任意财务重排入口，剩余拼接、候选登记和排名接线必须单列，不伪装成已完成。当前方案已完成，采集实现、测试和真实结果待主任务补录；本阶段未 commit、未 push、未部署，G2-FQ1/G2 未完成，原历史和验收门槛保留。

主任务直接查询云端基础服务 600519.SH 四 API 均非空：20260630 现金流/利润各 2 行、消费字段同值；20260911 估值可见；forecast 12 行触顶，最新公告仍 20250113/报告期20241231，为旧预告，不能当作当前事件。详细原值见方案。仅供应方单股返回，财报真实性、完整覆盖、新脚本重现和排名增益均未据此确认。

本阶段最终采集验收：主任务相关 **136 passed（2.84 秒）**、含新 18 项专项，未跑全量；新 CLI 在云端隔离临时目录真实请求 600519.SH/603259.SH、period20260630、trade_date20260911，八分区 observed、退出 0，原始返回重放和 result digest 均复核通过。取得时间 `2026-09-14T11:05:16.820170+08:00`；两股 cashflow/income/daily_basic/forecast 分别 2/2/1/12 和 2/1/1/11 行，现金流/总净利润分别 1.5356427451、0.8717284990，现金流/总收入分别 0.7660622781、0.3356397450。最新预告公告分别 2025-01-13、2026-01-13，均旧公告，前者触顶，不称当日事件或完整覆盖。

产物独占归档 `/var/lib/qagent-research/financial-enrichment/20260914-235cdb3305b8.json`、权限0400，result_digest `235cdb3305b8253ec235e10958806cbb66d1d0ac5ae89f43eaff2cd5e25b4652`、文件 SHA256 `f3ccffbb1f16e24d53b6730d91543cafc541c4c910f869186e8883942d5995b7`。已实现并完成上述采集测试与真实验收，未 commit、未 push、未部署到生产消费者；本轮不改生产代码/cron/10分钟tick/冻结模型/账本。新候选协议和同集合前向对照仍未实现，无新增排名或选股有效性结论，G2-FQ1/G2 保持未完成。

## G3 / G2-FQ1 双文档只读路由系统接入（2026-09-14）

用户本轮要求两个来源全部只读数据路由接入系统，[系统查询报告](research/documented-research-system-20260914.md) 定义统一catalogue/query API及仅loopback访问的单页采集CLI。基础80项含非HTTP pro_bar；ProMax取实际目录，主任务本轮刷新298项/259启用/39禁用，数量是能力声明，不表示全部业务适配或成功验收。行情、复权、财务、资金、事件、行业指数及基金特色数据均通过其可用只读路由通用查询，未知、禁用及写操作拒绝，不自动扫描数百API。

CLI与发布helper子任务相关 **58 passed（0.33秒）**、Ruff通过；显式 `--enable-documented-research` 复用环境原子备份回滚，只启用ProMax研究开关，不新建调度或改交易。系统后端由并行子任务实施，最终集成、真实调用和受控部署待主任务补录。本轮未commit、未push、未部署；G3/G2-FQ1原验收持续，通用查询不等于新增排名、历史PIT、分钟实时性或选股有效性。

子任务最终相关 **60 passed、1项既有warning（0.47秒）**、Ruff通过，包含CLI经过真实FastAPI路由/service的替身供应方测试和incomplete证据/退出码验证。主任务最终集成与部署继续待验，不将此测试称为公网取数或全接口稳定。

主任务后端全量 **2,456 passed、3项warnings（242.91秒）**，收集早于部分最终新测试；最终相关 **105 passed、1项warning（2.27秒）**，Ruff/diff通过。提交 `5b72362b43b1401b742e1cea710f48e101eb64b1` 已暂存云端构建成功，preflight通过并核验16项settings；受控部署执行中，最终切换、对账与真实API验收结果待补，不提前称部署成功。

### 系统只读查询部署验收（2026-09-14）

主任务确认release `5b72362b43b1401b742e1cea710f48e101eb64b1` 已受控部署，证据 `/var/tmp/qagent-rollout-relay-pu3u1eba/result.json` 的ledger_equal/settings_equal/scheduler_enabled均true，研究开关显式启用；已commit、未push，未改cron、交易权重或历史。系统CLI真实目录成功：基础80/79callable，ProMax298/255callable（259enabled排除4操作接口），不将255称为实测成功数。

经真实系统API查询基础moneyflow/600519.SH/20260911返回1行observed，digest `400eb551f21b5734dcb8bb79a76f387c604dd23c3bd663b3db7a0a0576eb95e2`；ProMax daily/000001.SZ/20260911/limit3为transport_error、CLI退出1，fetched_at `2026-09-14T03:21:27.878469+00:00`，digest `2c87f7bcc59c5d27793ad679d7ea4038274851a440e8f2dfbc48746c5fe592ad`。目录、资金流和失败产物均已归档至 `/var/lib/qagent-research/financial-enrichment/`，文件名见[系统报告](research/documented-research-system-20260914.md)。目录可读和数据失败并列，不声明ProMax稳定或全部业务适配完成。

tick本次北京时间11:20:00.410开始、11:20:47.175完成，attempts2/completed2、错误null，仅证明该次运行；保留此前首slot迟到422秒的历史，不替代分钟时效。系统接线已实现、测试并部署，G3/G2-FQ1原有数据覆盖、业务语义及选股有效性验收继续保留；本段文档待主任务review提交。

## G2-FQ1 每日财务采集与独立排名验收（2026-09-14）

用户授权的有界观察集合扩至8股，复用系统只读API采集五接口并生成独立候选；[实施和证据报告](research/daily-financial-candidate-20260914.md)保留固定集合、协议边界和完整产物路径。主任务相关 **103 passed、1项warning（3.28秒）**、Ruff/diff通过；首次102 passed、1 failed为测试替身缺失文件异常不一致，修正后复跑通过。本轮未跑全量、未commit、未push。

独立包 `/opt/qagent-research/daily-financial-20260914` 已部署，root manifest SHA256 `a60212b96de7e83b7e34ea29ad27b3304368a53dcf2e9c0ba94e74d555947142`。首包因Mac `._`元数据被安装器拒绝、未安装，保留失败包后重打纯文件包通过。主任务真实运行 `period20260630/trade_date20260911`，上海时间11:39:46.068308至11:40:07.619686，八股40请求全部observed；归档 `/var/lib/qagent-research/daily-financial/20260914T034007-b1d9680836b14f78a0d91b08e52a7915.json`，digest `e4cb89ba8f183e69ef6ff786c4f8594b6c2a4322542ee737438c4958924b4cf2`，digest、原始重放及独立Fraction排序复核均通过。

eligible8/8，Top5为600519.SH、300750.SZ、603766.SH、603259.SH、688002.SH，与显式观察顺序Top5重叠3/5；这是固定观察集合的行为对照，不是生产排名基线或收益。forecast旧公告及每页12行上限仍限制新鲜度和完整覆盖，固定20260630报告期须后续显式更新，不回填旧冻结信号。

新 `/etc/cron.d/qagent-daily-financial-research` 已安装为周一至周五08:40 UTC（北京时间16:40），SHA256 `c9582b6d90d9a70e9be2cd02760312bff8104b37e458fae17820df6e53878bba`，收据 `/var/backups/qagent-daily-financial-research/before-install-fwi06squ.json`。旧16:30任务保留，首次自然触发待验。后端current仍5b72362、本轮无重启；tick快照3/3、无error、午休outside，不替代分钟时效验收。本轮已实现、已完成相关测试及真实单次验收、独立包已部署和新cron已安装，未commit、未push；未修改生产权重、冻结模型和唯一账本。三个实施子任务完成、清理hooks保持开启，session实际删除未核验。G2-FQ1/G2自然运行、覆盖及同集合成熟收益验收继续，原目标和门槛不变。

主任务最终只读核验cron进程PID21、新cron属主root权限0644、归档属主luozhenkun权限0400；归档文件SHA256为 `c8124a0554bf91a1fb5ddf97d7450c75a030d7ce24015b8055e8671e29725077`，与上述结果digest分开保存。

## G2-FQ1 七接口、20股版本验收与调度升级（2026-09-14）

在前述八股历史结果基础上，用户授权观察集合扩至20股、七接口，完整集合及边界见[每日财务候选报告](research/daily-financial-candidate-20260914.md)。主任务最终专项 **112 passed**、Ruff/diff通过；使用 `backend/.venv` 最终全量 **2579 passed、3 warnings，无errors**。中间误用系统Python的2558 passed、7 skipped、3 warnings、7 errors由该环境缺lightgbm导致，已由正确环境全量通过更新，不再列为未解决缺口。Decimal等值修订P2已修复并增加7个参数化用例，真实冲突仍拒绝处理。

最终独立包manifest SHA256 `4549c4af411f1f3cd212d154f5b340a9671cd3e54291a79f204bee91b4326a14`。真实20260911、period20260630采集为20股×7接口 **140/140 observed**，最终报告status observed，归档 `/var/lib/qagent-research/daily-financial/20260914T041222-bf75194a3fb14a0692b6b7fedbb65ea9.json`；result digest `dad7f8b5a2c38e2d25ad5d4a40c489d28536d5cc088c7cff5a3d8e73109e8c28`，文件SHA256 `10536793a1e4892ad5fdb0fc5778c69f5ad0c3d69f4d6cf70eba2d61884fe08f`。16只eligible、4只金融股明确排除，数据成功与排名适用范围分开；首次报告把有意排除计为incomplete的问题已修正，保留排除事实。Top5为600519.SH、603444.SH、603259.SH、002602.SZ、600398.SH，不代表生产排名基线或排名效果已证明。

独立包已部署到 `/opt/qagent-research/daily-financial-20260914-v2`，cron由c958…升级为4a80…，固定模板重算完整目标SHA为 `4a80db491109badc90190fe9bdd45bf39160118ebe17733a1e934a8cf9b88b9d`；旧cron备份0600、幂等复核already_installed。升级过程未启动任务、未重启后端，首次自然16:40运行仍待观察。`/api/health` ok，release仍5b72362；模拟盘同一session、active，128 total/9 active/remaining1；十分钟更新outside_session、3/3、last_error null，此状态快照不替代账本全量对账或分钟时效验收。

本轮已实现、专项及正确环境全量测试通过、真实采集验收通过、独立包与cron已升级；commit/push状态未在本次验收中新增确认。不修改模拟盘、账本、交易规则或冻结输入。固定period20260630后续须显式滚动，预告和分页覆盖限制保留；G2-FQ1/G2自然运行、数据覆盖及同集合成熟收益验收继续待验，原目标与门槛不变。

## G2-FQ1 独立前向验证链路（2026-09-14）

新增 `scripts/evaluate_financial_challenger.py`，把既有六指标规则候选接到独立的 seal/evaluate 两阶段前向验证；详细协议、输入输出和限制见[财务规则候选前向验证报告](research/financial-challenger-forward-20260914.md)。它只接受交易日当日15:30后采集并在同日封存的 `daily-documented-research-v2`，复算原始证据和候选排名；旧09-11数据在09-14采集的人工报告明确拒绝追认为09-11前向信号。

收益评估以次交易日调整开盘为入场、5/10/20交易日窗口末日调整收盘为退出，使用沪深300同窗收益和固定10bps往返成本；只读SQLite缓存，缺价、质量拒绝、未成熟分别保留，不补价、不换股、不把不完整Top5计算成组合均值。基线仅接受同日、同完整资格集合且摘要有效的G2 `full_features`研究归档；观察输入顺序不得作为收益基线，无合格归档时明确 `baseline_unavailable`。

新增专项 **16 passed**，相关回归 **104 passed**；正确虚拟环境全量 **2595 passed、3 warnings**，Ruff和diff检查通过。本轮实现不写 `FactorResearchRepository`、模拟盘、唯一账本或正式Ranking，也没有新增数据库/API/调度或部署。自然有效信号、同日基线和5/10/20日真实成熟结果仍待后续时间窗口验收，因此G2-FQ1选股增益尚未证明，不提升正式交易权限。

同日16:40自然财务任务已生成首份当日observed报告，20股中16只eligible；主任务完成证据重放并将首个合法信号独占归档至云端 `financial-forward-signals/2026-09-14.json`，文件SHA256 `1466a90ab84a5cbed989ef187ecc0c37a6aa9cb055253a162aa81ac8d7315732`、结果digest `46fe1dc5e4a2ea9adcfc23604b59b596d45faa5f3a8b0d8f9f30e2c79c28b79a`。当天无合格同日G2归档，基线明确 unavailable；5/10/20日结果最早于09-21、09-29、10-20收盘后验收。自动seal调度仍未安装，选股增益仍未证明；完整证据见上述前向验证报告。

### G2-FQ1 前向自动归档实现阶段（2026-09-14）

自动 seal 与到期 evaluate runner、独立 bundle 校验/预览/原子安装及可恢复回滚 helper 已实现；详细不变式和命令见[财务规则候选前向验证报告](research/financial-challenger-forward-20260914.md)。runner 复用既有协议、独立锁和固定300秒预算，只读 `qagent.db`；已有同日 signal 重放并核对首个合法 daily，缺同日G2继续 baseline unavailable。未成熟和到期 partial 写每次run证据并重试，只有 complete 才独占写最终5/10/20窗口，避免缺价首次评估永久锁死结果；归档记录 evaluator 与实际 backend/factor实现身份。

最新专项及既有安装升级相关 **68 passed（2.62秒）**、Ruff及diff检查通过；此前正确虚拟环境全量 **2625 passed、3 warnings（230.77秒）** 早于新增main序列化测试，本次未重跑全量。v3云端手动runner成功，但安装后第二次execute因公开result含Path而在JSON输出失败；cron已用 `before-install-lu0mzlde` 安全rollback为 `rolled-back-6yn8r4qa.cron`，未自然触发，daily cron、health和模拟账户正常。v4将recovered/already receipt统一转字符串，并对installed/recovered/already/rollback递归JSON序列化，实际main安装、幂等和回滚测试通过。已实现、已完成上述测试；v4未 commit、未 push、未打包、未安装、未部署。v1未安装，v2/v3 cron均已回滚且未自然触发。cron仍拟工作日11:37 UTC（北京时间19:37）；显式安装后仍需自然触发、已有手工signal幂等及首个真实成熟窗口验收。未修改模拟盘、正式Ranking、数据库schema/API、后端服务、冻结模型或交易权限；G2-FQ1/G2选股增益仍未证明。

### G2-FQ1 前向自动归档 v4 部署验收（2026-09-14）

最终实现已commit/push至 `bb365349aa7b25befc745cbfdce4d7d8d9a67195`；v4 bundle `/opt/qagent-research/financial-forward-20260914-v4` manifest SHA为 `01bd17e086321a7b1cd990858383992fa62270608a66bb6ef7aa2c6029f03a28`。cron `/etc/cron.d/qagent-financial-forward-research` 为root:root 0644、SHA `a570099924da2e4540a58aa7fe3952c689fc609ce821a13df21de569fe29d81f`，工作日11:37 UTC；root:root 0600收据为 `/var/backups/qagent-financial-forward-research/before-install-bt_wnxro.json`。首次installed、第二次already_installed均exit 0。v1未安装以及v2/v3失败与rollback历史保留。

安装后手动runner exit 0、complete、already_sealed；既有signal SHA前后均为 `1466a90ab84a5cbed989ef187ecc0c37a6aa9cb055253a162aa81ac8d7315732`，5/10/20均waiting。最新run `/var/lib/qagent-research/financial-forward-runs/20260914T220445.472354+0800-7117a87bf70a47c688653ca98723ba84.json` 为0400，digest `206eec9ded3f79c9d5a68a7d7343c3346e2f3827635426991681799f9135b5be`，runtime为v4 evaluator。最终相关 **68 passed（3.92秒）**、全量 **2625 passed、3 warnings（230.77秒）**，Ruff/diff通过，审计无P1/P2。

daily cron仍为 `4a80db491109badc90190fe9bdd45bf39160118ebe17733a1e934a8cf9b88b9d`，backend current仍5b72362、health ok。模拟账户保持同一 `paper-session-69470ca6b12c` active，total128 / active9 / remaining1；未修改账本、正式Ranking或交易规则。本阶段已实现、测试、commit/push、打包、安装和部署；首次自然cron仍待下一工作日北京时间19:37，真实5/10/20成熟仍待09-21、09-29、10-20，G2-FQ1/G2选股增益尚未证明。

## G2-FQ2 候选池驱动的 Financial Challenger 观察集合（2026-09-15）

稳定子目标：现有 Financial Challenger 是本能力的唯一研究消费者。每日财务采集器新增默认关闭的候选池输入模式，只读复用既有 `/api/paper-trades/candidate-pool`，从当日有序的最多100条来源记录中保留前20只合格A股、对明确基金类型留证排除，以此替代固定观察集合，再复用原七接口采集和 `rank_financial_candidate`；不新增排名、组合、数据库、账户或长期并行链路。显式 `--symbol`/`--symbols-file` 默认行为保留。设计和实现见[候选池研究适配](research/financial-candidate-pool-adapter-20260915.md)；当前提交、push和部署事实见本节末尾验收记录。

验收条件：先以同一候选池集合、同一信号日、同一财报期和成本口径封存固定集合路径与候选池路径的可复现对照；候选池模式须保存原响应摘要/digest、数据健康、选择顺序及限制。至少等待首个真实5交易日结果成熟，再比较覆盖、Top5变化及同集合5日净收益，不能用排名差异或接口成功代替收益增益。只有数据健康和日期证据连续合格、同集合基线存在且5日结果支持继续观察时，才由用户决定是否替代固定集合调度；不长期并行运行两套同类输入。

失败与停用条件：总体响应、日期或数据健康不合格，股票类型但非合法沪深北A股、后缀错误或股票重复，基金类型但不是合法CN基金代码，或排除基金后少于5只股票时均失败退出，且不请求七个财务接口；明确 `etf/fund/index_fund` 且基金代码合法的记录只做有摘要的排除，不伪装成股票。自然运行连续失败、无法形成同集合基线、首个5日结果否定候选或长期无 Financial Challenger 消费时，停止候选池模式调度并移出运行链路，只保留必要研究和审计证据，不删除历史账本。任何上线、替换或恢复调度均需单独授权和验收；保持固定边界中的非冗余规则。

上线前兼容与升级门禁：forward seal保留旧 `explicit_observation_order` 及原digest校验，同时严格接受可复现的 `paper_candidate_pool_order`，不降低同日15:30后、raw evidence和eligible>=5门禁。上线采用consumer-first原子顺序：先升级financial-forward v4->v5，再升级daily v2->v3，因此不存在动态daily被旧forward拒绝的窗口；两步只改批准的bundle/候选池参数，固定校验当前manifest及cron，具备锁、私有备份、幂等和rollback且不启动任务。若daily第二步失败，统一helper自动把forward回滚至v4；首次升级使用私有备份，重试时已处于v5则用严格验证的固定旧cron恢复。本地覆盖collector、runner/evaluator及升级/回滚的专项 **143 passed（3.35秒）**，Ruff与diff检查通过；未修改模拟盘、数据库、后端服务或策略。

本机集合验收：2026-09-15 经 loopback 真实 GET，期望信号日为 2026-09-14，来源 shown/total 均为90；验证出56只合法股票、34只明确 fund/ETF，按源顺序选前20只，首3只为 `002746.SZ`、`600025.SH`、`688581.SH`，共留证排除70只（34 `explicit_fund_asset_type`、36 `selected_limit`）。`include_etfs=false` 仍混入ETF，已由适配层留证排除。本次只验证候选集合，未调用后续七接口；主任务相关 **99 passed（1.89秒）**，Ruff与diff检查通过。未 commit、未 push、未 deploy，未改调度和模拟盘；同集合基线及5日结果仍待验。

隔离端到端补跑：临时产物 `/tmp/qagent-fin-pool.UlhQIW/20260915T105253-612f840dffc645e7a196e929cc7dc7db.json` 使用 `trade_date=20260914`、`observation_day=2026-09-15`，20股×7接口共140项 classification 全部 observed，报告 status observed，result digest `cb59...` 校验有效。19只 eligible；`600028.SH` 因 `fina_indicator` 存在 ambiguous consumed revision 被明确排除。候选 Top5 为 `603565.SH`、`600398.SH`、`688581.SH`、`601919.SH`、`601857.SH`，与原候选顺序 Top5 重合2/5。该结果是次日补跑，不得追认为09-14前向信号，未进入正式归档，也不形成收益结论；未部署、未改 cron 或模拟盘。前述99项测试证据保留。

最终测试验收：专项 **143 passed（3.35秒）** 保留；主任务使用正确backend虚拟环境完成全量 **2660 passed、3项既有warnings（256.45秒）**。此前一次从backend工作目录误写 `backend/.venv` 路径，命令立即exit 127且未执行测试，已由上述正确命令的完整通过结果取代。

部署验收：源提交 `d599a91` 已 push 至 `features/automation-backtest`。consumer-first 两步升级均成功且 `started_job=false`；随后两次preview均为 `already_installed`，health为ok。daily v3位于 `/opt/qagent-research/daily-financial-20260914-v3`，manifest SHA为 `1a6855501e14e4d54b2eca0f59afc618971135b932e74d612de835399307efbe`，cron SHA为 `701215372bdb050190ab1df52910e74040ec6545b3691978575d7de823b19aa7`，工作日北京时间16:40运行；forward v5位于 `/opt/qagent-research/financial-forward-20260914-v5`，manifest SHA为 `386ea1dbc0c427a4693e0109161145c89b73df83a9820e6ffefa35d7333711ae`，cron SHA为 `04040c5f48a42865c5f43289c5012fdfadd0dcd6f0f50500505397e783ad2016`，工作日北京时间19:37运行。

唯一模拟盘仍为 `paper-session-69470ca6b12c`、active；current_model为total 58、pending 1、open 9、closed 39、active 10，规则仍为max_positions 10、allocation 10%、cost 5bps、slippage 5bps、take_profit 50%，未创建第二账本。隔离端到端的140/140 observed、19 eligible及动态适配已验证，但今日自然daily/forward尚未发生，绩效与晋级仍未验证，不能据此宣称选股收益提升。

### G2-FQ2 扫描依赖有界重试（2026-09-16，部署验收）

09-15 自然 daily 在北京时间16:40早于18:51完成的全市场扫描，因候选池仍是旧日期而 fail-closed；19:37 forward因无当日daily产物无法封存。最小修复保留现有唯一daily/forward链路：daily在16:40至19:10六次有界尝试，候选池未新鲜时不请求七个财务接口并写轻量审计产物；当日首份摘要有效的observed产物成功后，后续尝试按交易日幂等退出。forward在19:37至20:37三次有界尝试，缺daily留下`waiting_for_daily`，已封存信号严格校验后`already_sealed`，不重复写信号。显式symbols模式不变，不新增账户、数据库或排名器，不由`no_rows`推断停牌，不扩大Tushare/Datahubco到分钟或复权价。

修复已实现交易日daily去重、等待/失败证据、forward等待状态及双cron受控升级/回滚helper。helper的显式状态机只接受`old/old`、兼容的`daily-old/forward-new`和`new/new`：升级按forward后daily执行，回滚按daily后forward执行，中断后均可从兼容混合态续做；`daily-new/forward-old`fail-closed。父任务最终专项回归 **96 passed**。正确虚拟环境全量结果为 **2668 passed、1 failed、3 warnings（957.73秒）**，唯一失败是 `tests/test_paper_writer.py::test_process_death_releases_writer` 的 `spawn ready.wait(8)` 超时；该用例随后隔离复测 **1 passed（3.15秒）**。因此本轮不能记为“全量一次全绿”，仅能并列保留全量单一超时与隔离复测通过的事实。

云端首次从 daily-v4 独立bundle执行helper preview时在导入阶段安全失败：`ModuleNotFoundError: upgrade_financial_forward_research`。当次cron未修改、任务未启动，不构成安装或部署成功。根因是daily/forward新bundle的manifest未各自包含helper的完整import依赖，仓库内测试的`sys.path`掩盖了缺口。本地修正为两个新bundle共用daily与forward required的严格最小并集，并新增仅复制manifest列出文件、非仓库cwd、隔离`sys.path`的两bundle subprocess preview；manifest继续拒绝额外文件。本轮helper相关专项 **39 passed（2.49秒）**，Ruff和diff检查通过。截至该次本地修复，已实现并完成本地测试，但尚未commit、push、安装或部署；当时下一验收点是重新打包后的云端隔离preview。

最终源提交 `a223b2d` 已 push。重建后 daily-v4 manifest SHA为 `800d60b8fe3b83bcf33acd139a521744c9b739e6ac4751812deae123be4c0b6f`，forward-v6 manifest SHA为 `f670920638b094522847fb868db8ed86da5d942ad4a4a63208338f37ee3c2ef1`。首次以root运行preview在bundle内生成`__pycache__`，因多出manifest未列出文件而被安全拒绝；当时cron未修改。清理后改用普通用户 `python -B` 执行preview，结果为`planned`且两份旧cron保持不变。

受控execute完成后状态为`upgraded/new`、`started_job=false`；receipt为 `/var/backups/qagent-financial-dependency-retry/before-install-dacfwrpf.json`，root:root 0600，所在目录为0700。安装后preview为`already_installed/new`。新daily cron SHA为 `ff8dfcc89647d9af4404d6f234de5583d87dc811a7c6eb962bd16fc3b77bf16d`，工作日北京时间16:40至19:10共六次；新forward cron SHA为 `c7425b014f6dfa37681e5f5e57e481b58d7c13a6d7f6e4f46f7689b75b1c5ecc`，工作日北京时间19:37、20:07、20:37共三次。health为ok；唯一模拟盘仍为 `paper-session-69470ca6b12c`、active，9 active / remaining 1；十分钟调度快照为`waiting`、attempts 57 / completed 53、`last_error=null`。未手动启动daily或forward，自然运行及其产物链路仍待验证；选股绩效尚未成熟，未晋级。

## G2-FQ3 同行业市值配平 Financial Challenger（2026-09-16）

稳定子目标：以 `scripts/evaluate_financial_challenger.py` 为唯一消费者，新增 `financial-rule-forward-v2`，复用同一候选池的 `industry` / `exposure_group` 与同次 `daily_basic` 中严格为正的 `total_mv`。股票的两个行业字段必须同时为非空字符串且去空白后完全相等；两者同为空时 control 不可用，只存在一个、空白或冲突同样拒绝配平。为 Financial Top5 按候选排名顺序匹配时，先选同行业、未使用且不在 Financial Top5 的 control，并固定按 log 市值差、G2 rank、instrument ID 升序决定；同行业非 Top5 不足时允许用同行业另一只 Financial Top5 补足。candidate 不得匹配自己，control 跨 pair 唯一且不得跨行业。供应方词法原值留在 `raw_evidence`；每对保存industry、candidate/control同供应方单位的规范化`total_mv`数值字符串、size distance、control G2 rank、完整性/可判别状态及per-pair digest。行业与市值均为信号日 current observation，不是历史 PIT，因此只封存新的自然前向信号，不回填旧日。完整规则见[同行业市值配平设计](research/financial-challenger-matched-control-20260916.md)。

v2 matched control 替代当前未配平的 G2 `full_features` Top5，作为 5/10/20 交易日主归因；旧 v1 仅保留历史重放和到期补算兼容，新信号不双写 v1/v2，不长期双轨。行业/市值完整且每个 candidate 均有尚未使用的同行业非 self control 时可形成五对；若其中 `control_id` 不在 Financial Top5 的配对少于2对，则标记 `control_not_discriminative`、不得计算 paired lift，至少2对的control位于Financial Top5之外时才为 `available`（可判别）。仅行业/市值不完整、字段冲突、非正市值、同行业总候选不足或证据不一致时标记 `control_unavailable`（不可用）。不得以部分均值、跨行业替代、重复或self control、补值或事后换股伪造 matched lift。

收益继续复用次交易日调整开盘、窗口末日调整收盘、沪深300同窗基准和固定往返10bps；十只股票价格完整时才产生组合 paired lift，既有缺价、质量拒绝、未成熟和只读SQLite规则不变。验收须证明v1历史兼容、v2摘要可确定重放、匹配/tie-break/不可用语义及5/10/20计算，并取得至少一份自然v2完整五对及首个真实5日成熟结果，后续补齐同信号10/20日；测试或接口成功不替代收益验收，不自动晋级。

若数据语义或单位不可确认、连续五个自然v2信号均为 `control_unavailable` 或 `control_not_discriminative`、首个可判别且完整的5日matched结果否定继续观察，或唯一消费者不再使用该能力，则停止新v2调度并移出运行链路，只保留历史证据，不回退为长期v1/v2双轨。collector/enrichment/evaluator v2、v1历史兼容及per-pair digest已实现；独立 `daily-v5` / `forward-v7` 不可变bundle升级器也已实现，固定从当前v4/v6按consumer-first顺序升级，默认preview、显式execute，支持幂等、中断续做和可恢复回滚，不启动任务，两个目标bundle使用相同完整依赖闭包并拒绝额外文件、软链及可写文件。父级本轮相关8组回归共 **186 passed**，升级器联合回归 **65 passed**，完整backend回归 **2692 passed、3 warnings**，Ruff与`git diff --check`通过。

源提交 `87d8590` 已push。`daily-v5` manifest SHA为 `551edc0ce38c5e7892ab3158ef4bb337db9f41b6bbb265853aff91fd2e68bf2f`，`forward-v7` manifest SHA为 `ec9dfd26617eda1b037d433b5eb5f0e5f51e0526786f9b4c044ab72c64a8186e`。execute前preview为`planned`且旧cron未变；execute结果为`upgraded/new`、`started_job=false`，receipt为 `/var/backups/qagent-financial-matched-control/before-install-4eu899i5.json`；安装后preview为`already_installed/new`。新daily cron SHA为 `45ee481081dd3e3ba06f6df02bbd55f07944d1b896ff54e08f8ffcff10ff62f8`，新forward cron SHA为 `1305eb71acc82ecb3730433044f218815c40ec23aff004a602db3f622cc9dec3`，均为root:root 0644；health为ok。未手动启动daily或forward任务，唯一模拟盘、账户、账本、数据库/schema、API、正式Ranking、历史交易、交易权重和实盘权限均不改变。自然v2信号与真实5/10/20交易日成熟证据仍待自然运行验收，不能由部署成功替代。

## G8 / P0：扫描 freshness gate 与 provider breaker 语义隔离（2026-09-16）

稳定子目标：自动全市场扫描把 `candidate_data_partially_stale_filtered`、`candidate_data_stale_filtered` 和兼容旧状态 `candidate_data_stale_after_retry` 统一视为 fail-closed 的 freshness deferred/watch。它们保留 cycle issue 和可观测健康状态，但不消耗错误重试预算、不创建或递增 `scan:free` circuit breaker；真实 provider/transport/coverage 错误仍沿用既有重试与 breaker 语义。后续正常周期必须仍能启动新扫描，不能因为候选陈旧门禁被六小时 breaker 阻塞。

一次性恢复工具 `scripts/recover_scan_freshness_breaker.py` 默认只预览，显式 `--execute` 才允许修改；目标固定为 `scan:free`，仅接受 state=open、`last_error_text` 精确属于上述三种历史误分类、`next_probe_at` 尚未到期且无 half-open probe owner 的行。执行只关闭并清零该 breaker 的运行计数和 probe 字段，保留最后错误证据，使用 revision compare-and-swap，并输出私有 before/after receipt；重复执行只返回 `already_recovered`，其他 scope、错误、状态或已到 probe 时间均拒绝。该工具不启动 scan、automation scheduler 或模拟盘。

验收条件：回归须同时证明 stage 为 deferred、issue 保留、`scan:free` breaker 不创建/不递增、下一 post-close cycle 可以进入 queued scan；恢复工具须覆盖 preview 无写、严格 allowlist、未到期检查、仅目标行变化、私有审计 receipt 和幂等。上线前由主任务复核真实 breaker 行与当前时间，先 preview 再显式 execute；代码部署和历史 breaker 恢复分别记录，恢复成功不能替代下一自然扫描、daily-v5/forward-v7 产物或选股收益验收。

当前状态：最小语义修复、回归及一次性恢复工具已实现，源提交 `2392d4beda7f0a8badb34490bde34b9abdabba5e` 已 push；完整 backend 回归 **2719 passed、3 warnings**。云 release `/opt/qagent/releases/2392d4beda7f0a8badb34490bde34b9abdabba5e` 已通过受控部署，证据目录 `/var/tmp/qagent-rollout-relay-54hihrnd`，8 张账本表哈希一致，`verify_linux_deployment` 通过。恢复工具先 preview 返回 `planned`，随后 execute 返回 `recovered`，再次执行返回 `already_recovered`；私有收据 `/var/backups/qagent-scan-breaker/scan-breaker-recovery-84024532a61b46d488ec265afde07316.json` 为 root:root `0600`，`scan:free` breaker 从 open / open_count 6 / revision 26 变为 closed / open_count 0 / revision 27，恢复过程未启动 scan。自然 scheduler 于北京时间 17:46:36 创建 `full-scan-20260916094636-dc7cba02`，17:46:41 状态为 running，scan stage 为 completed、error 为 none，breaker 仍为 closed；重启后的独立 paper 10 分钟 worker 为 enabled / outside_session / `last_error=null`。当前全市场扫描仍在 running，今日候选池、Financial daily-v5 和 forward-v7 尚未完成，因此不能宣称数据已经新鲜或研究收益已经验收；唯一模拟账户、账本、正式 Ranking、交易规则和研究晋级边界保持不变。

### G2-FQ2 晚扫描依赖补齐（2026-09-17，本地实施）

主任务刷新09-16运行证据：首次扫描20:35:14完成，修复扫描21:39:07完成；只有后者已确认候选freshness为fresh。既有daily最后19:10仍等待候选池，forward最后20:37仍等待daily，构成真实同日依赖缺口。历史部署状态保留，本轮复用原daily/forward唯一研究链路：拟将daily按30分钟轮询窗口延至北京时间16:40–22:40共13次，首次fresh后最多启动两批财务API，启动前在原跨进程锁内持久化计数，失败或进程退出也消耗一次；等待候选池不消耗财务预算。当日成功仍由原摘要校验幂等去重，成功或already_completed后立即调用原forward封存/评估；锁冲突返回75，不视为成功。独立forward保留19:37/20:07/20:37并加23:07恢复与成熟评估，即使无新daily也继续处理历史信号。

新调度显式启用`--bounded-same-day`，仅允许工作日当日16:40–22:40启动、财务预算不超过600秒；候选池请求后再次验证日历日及截止时间，不追认09-16或跨午夜补信号。轻量调度记录分日放入`daily-financial/schedule-attempts/YYYYMMDD`，避免增加原daily/forward根目录2000文件检查的压力；保留历史根目录审计，不删除或回填产物。缺数据或两次失败后当日停止财务请求，下一交易日重新等待自然输入；选股增益、自然v2配平信号和成熟收益仍待验。

受控升级器`upgrade_financial_late_scan.py`复用既有双cron状态机、锁、备份、consumer-first和rollback实现，以隔离配置实例固定当前v5/v7摘要，旧模板保持原样；目标为`daily-financial-20260917-v6`和`financial-forward-20260917-v8`。提供确定性打包函数，两bundle都包含完整严格依赖闭包；命令和验收见[晚扫描运行说明](research/financial-late-scan-20260917.md)。当前已实现，collector、forward runner、新旧升级器四组专项 **95 passed（3.59秒）**，含两个完整打包依赖闭包的隔离subprocess预览/中断恢复/回滚；主任务15组相关回归 **302 passed、1项既有warning（6.38秒）**，本轮未跑完整backend回归。Ruff和diff检查通过；未commit、未push、未安装或部署，不更改唯一模拟账户、交易规则、模型、历史账本或正式Ranking。

部署补录（北京时间09-17 10:34:48）：源提交`d86e8fc`已push；两个新bundle在云端隔离preview均为planned、旧cron不变，execute为`upgraded/new`且`started_job=false`，随后preview为already_installed。daily-v6 manifest SHA为`1e942320d572f2a61c5e0f155c3c2dcb2a4e0e1589b7c77f7af854f275cb8d55`，forward-v8为`92717c3ff89a50efe27a627a40d2549a6ab585989330c9111b045dfc45e6838b`；私有receipt、cron SHA与完整验收见运行说明。health ok，cron PID21、backend PID52233、frontend PID52046和current2392d4b均未改变，无后端重启。上午真实CLI被时段门禁拒绝（exit2、数据请求前退出），不是新数据采集或运行失败证据。09-14历史signal SHA保持不变；已实现、相关测试通过、已push、已部署，不改账户、规则或数据库，今晚自然daily、v2封存及真实成熟收益仍待验。子任务review无P1/P2，会话清理hook保持启用，实际删除尚未核验。

### G2-FQ3 同日 G2 基线缺口（2026-09-18，本地修复）

主任务刷新09-17自然证据：daily为140/140 observed、15/20 eligible，extended候选池输入存在；signal仍为`financial-rule-forward-v1`、`baseline_not_supplied`。G2归档只有09-11信号，其冻结每10交易日采样与财务工作日运行频率不一致。本轮修正新extended输入因缺G2隐式回落v1的问题：新信号固定v2，缺同日完整G2排名时明确`control_unavailable/g2_rank_incomplete`，不制造control或paired lift；历史extended v1仍按旧协议重放，不改旧日产物、匹配tie-break或门槛。runner显式记录baseline/control状态原因，详见[同日基线缺口](research/financial-baseline-gap-20260918.md)。

first-seal仍不可变，首次unavailable封存后不因后来G2补到而重写。实际每日基线生成尚未解决：每日冻结模型排名sidecar（须在首次seal前完成）与仅G2采样日生成财务信号的选择已向用户提出、尚待确定，本轮不擅改冻结采样、不代用生产rank、不回填09-17。evaluator/runner专项47 passed（2.59秒），Ruff/diff通过，未跑全量；已实现、已完成上述本地测试，未提交、未push、未部署。G2-FQ3自然v2完整五对及真实5/10/20成熟收益仍待验，不改变唯一模拟账户、历史账本、规则或正式Ranking。

后续只读验收：主任务复跑47 passed（2.35秒）；真实09-17旧v1归档通过新validate_archive且文件哈希不变。内存按新seal行为重算同时出现`g2_rank_incomplete`和`industry_incomplete`，原20只selection有14只industry/exposure同为空，补G2本身不足以形成control。归档source为latest_signal_day；代码显示候选池从snapshot.card提取并规范化行业，collector原样保存，并非财务适配器丢字段。上游常规card行业采用固定映射与名称/前缀推断，候选池路径未查询PIT行业库；未读云端逐只card原值，不把缺口等同供应商无数据。本轮未新增行业请求或补值，未变更生产逻辑；同日基线选择仍待用户答复，完整五对和成熟收益仍未验收。

### G3 研究补价前置预算有界修复（2026-09-18，本地验收）

针对主任务09-18自然周期的factor shadow补价provider请求为0、wall_clock_deadline快照，已确认首次provider预算claim/cursor推进前存在逐requirement过滤整张缓存表及逐缺口独立metadata查询。最小修复复用原Factor/Fuyao shadow消费者：缓存按日期建立键索引，结构metadata每500股批查并以SQL window只返回最新记录；保持90秒协作预算、provider批次预算、cursor公平续做、价格质量与停牌判定，不新增数据源或调度。详见[研究补价预算报告](research/research-price-budget-20260918.md)。

同机合成微基准：5,500条缓存检查2.162917→0.109124秒，1,000条结构检查2.280063→0.026500秒；1,001股双字段结构SELECT由最多4,004条降为6条。五文件专项48 passed（5.30秒），扩展三文件132 passed、1项既有warning（15.42秒），Ruff/diff通过；这些不是云端profile或补价成功证据。G2冻结/scorer与walk-forward固定源码摘要输入不变，但recommendation alignment的全package `package_source_digest`会变化，继续保留原身份不一致检查，不绕过旧校验。

本轮已实现、完成上述本地测试，未提交、未push、未部署；云端自然provider进度、cursor推进和缺口覆盖仍待验，G3不标完成。不修改唯一模拟账户、账本、交易规则、冻结模型、正式Ranking或研究晋级门槛。

### 2026-09-18 两项修复集成验收

主任务完成最终backend全量 **2744 passed、3项既有warnings（235.03秒）**，Ruff及`git diff --check`通过；独立review对上述财务封存协议与补价前置查询两项修复未发现P1/P2。真实09-17财务旧v1归档兼容校验通过且文件哈希不变。已实现、已完成上述测试，仍未提交、未push、未部署，不将本地验收外推为云端自然效果。每日同日G2基线方案尚未确定，真实selection的14/20行业缺失仍未解决；G2-FQ3完整五对、成熟收益及G3自然补价覆盖继续待验。

### G2-FQ3 每日冻结基线与同源行业补齐（2026-09-18，本地实施）

用户现已批准每日冻结模型 baseline 与可信供应方行业证据，更新上段“方案尚未确定”的历史状态。复用原 Financial 唯一 daily/forward 链路，新 prospective 输入使用 `financial-daily-frozen-industry-v1`、新信号使用 `financial-rule-forward-v3`；旧 v1/v2 仅用于历史重放与到期评估，不回填旧信号、不长期双轨。原 G2 每10交易日采样、冻结输入及归档保持原样；独立每日 sidecar 复用完整 source、冻结 full_features 三种子均值排名，仅供 Financial 配平 tie-break。首次 seal 前等待同日合格 baseline，缺少时 `waiting_for_baseline` 不占用 first-seal；行业证据不可用时 daily 为 incomplete、仅按原同日两批预算重试，用尽后停止且不 seal；行业齐全但同行业候选不足则保留 control_unavailable，不得伪造五对或 paired lift。

全部原选中最多20股使用同一供应方 `stock_basic.industry`，单独保存 raw response、时间、taxonomy 与摘要，不改候选池 universe、不混入启发式行业；明确 current observation、非历史PIT。原七接口加行业接口，每批最多160请求，仍受600秒与同日两批预算限制；forward 保持300秒。主任务云端 `/stock-basic` 上午单股000975.SZ成功返回“黄金”，只证明当时路由可读，未形成收盘后信号；现有系统API仍因旧allowlist返回unknown_api。本地补充81项目录及stock-basic路由，须后端发布才能生效。详见[每日依赖实施记录](research/financial-daily-dependencies-20260918.md)。

当前行业/供应方/API专项 **101 passed、1项既有warning**，每日baseline初始专项 **28 passed**；集成与全量结果待主任务补录，不能沿用上段2744结果作为本轮新增代码验证。已实现相关模块、完成上述专项，未提交、未push、未部署新后端或daily/forward bundle。G2-FQ3自然完整可判别五对和真实5/10/20收益仍未验收，原停用条件、唯一模拟账户、历史账本、交易规则及正式Ranking保持不变。

随后主任务早间只读诊断原09-17选中20股，stock-basic 20次均HTTP200/code0/单行行业，耗时10.75秒，无fetch失败；原card14只空行业不能等同供应方无数据。此为09-18上午current observation，不回填09-17、不作为收盘后信号。行业20/20可用仍不等于五对control可用，原Top5存在集合内单只行业；保持原20股、分类与门槛，不扩池或合并标签。自然配对覆盖与成熟收益继续待验，完整分类见上述实施记录。

后续相关集成119 passed（2.80秒）、1项既有warning，全量仍待完成。provider目录不在既有walk-forward源码文件集内，两个provider文件变更本身不改变该摘要；recommendation alignment全包package_source_digest会改变，保留身份失配拒绝规则，不复用为新代码验收。新受控研究升级器已实现，后端路由发布及daily-v7/forward-v9安装仍待主任务执行和验证。

02:51 UTC 云端隔离内存加载新客户端、DocumentedResearch与行业模块，单股真实返回observed/available、“黄金”；无云端文件或环境修改、无部署，原服务API仍待发布。该上午样本仅验证新代码单股链路，不算自然收盘后信号。

本轮首轮全量2805 passed/1 failed，唯一失败为新增stock_basic后CLI目录数量旧断言80；已改为81，该文件18 passed，最终全量重跑中。合成输入实际冻结collect→seal→validate_archive 8 passed，无baseline mock；双研究包本地打包解压后从/tmp执行两消费者--help通过，摘要见实施记录，均不替代自然运行或部署。自然runner以incomplete拒绝行业缺失日产物；单独evaluator seal对该类证据保留control_unavailable、拒绝有效配平及paired lift，不概称所有手工调用均抛错。未提交、未push、未部署，G2-FQ3自然配对与成熟收益仍待验。

最终本地验收补录：主任务backend全量 **2807 passed、3项既有warnings（243.69秒，exit0）**，覆盖本轮当前最终代码；涉及脚本及端到端测试Ruff复检、`git diff --check`通过。首轮2805/1失败与各专项数字保留为历史过程，最终全量已通过。已实现、已测试，仍未commit、未push、未部署新后端或研究包；G2-FQ3自然同日baseline/行业/完整可判别配对及真实5/10/20成熟收益仍待验，不改变唯一模拟账户、交易规则或晋级权限。

### 2026-09-18 受控发布补录

源提交`edbd9fa11dd12f56d009d1b3128401a6b5e8248f`已push并受控部署；冻结依赖、隔离启动和前端构建通过，证据`/var/tmp/qagent-rollout-relay-07arl0e6/result.json`确认新release、八表ledger_equal、16项settings_equal及scheduler_enabled均true，服务属主/loopback端口/backup cron与health验收通过。维护down时间03:23:54.885 UTC，最终健康03:28:17 UTC前恢复；恢复tick为enabled/waiting、1/1、last_error=null，03:20 slot实际03:28:10.841971开始、迟到490.841971秒、skipped_slots0，不称准点或无中断，不由此推断漏单，没有手动补交易。

新系统`stock_basic`真实单股成功observed/行业“黄金”后，daily-v7与forward-v9受控升级为upgraded/new，复核already_installed/new、started_job=false；receipt为`/var/backups/qagent-financial-daily-dependencies/before-install-xztb_bw9.json`。新13/4时槽及行业/baseline参数、完整manifest和cron摘要见[每日依赖部署记录](research/financial-daily-dependencies-20260918.md)。G2 cron和09-14历史signal摘要保持原值，rank目录服务用户可写。已实现、已测试、已push、已部署；没有手动启动完整财务批次，G2-FQ3自然baseline/行业/可判别五对及5/10/20收益、G3自然补价provider/cursor/覆盖仍待验，不提升目标完成状态、正式Ranking或交易权限。

恢复后自然03:30 UTC tick于03:30:00.047722启动、03:30:34.539601完成，enabled/waiting、attempts2/completed2、skipped_slots0、last_error=null。这是单次自然触发与完成证据，不泛化为所有股票行情实时性或长期准点；上述部署暂停影响更新及时性，账本未改写。

### 2026-09-18 G7 行情时效观测与 G2-FQ 资金流有界核验

主任务06:41–06:45 UTC只读核验current仍为`edbd9fa`、health ok；最新06:20/06:30/06:40三个slot各9 checked/9 resolved，分钟行数10534/10602/10676、日线降级均0，9个active持仓latest_date为09-18。06:40 slot实际06:41:12.262763启动、迟到72.262763秒，06:41:49.620261完成，attempts14/completed13、error null；这些是当次运行证据，不能证明逐只分钟无延迟。北京时间14:45尚未到16:40研究时段，当时daily-ranks为0、signals2/latest09-17、daily根目录12文件/latest09-17，不能据此判定当日收盘后链路失败。

本轮最小实现为逐只分钟数据时间/延迟观测及provider-status最近20条health读取范围收敛，不改模拟交易语义或账户规则。实施子任务云端单次只读比较、主任务复核：health读取32,715,801→1,397,149字节，SQL 0.271318→0.031141秒、解析0.665141→0.023810秒，health_equal；真实API200/0.419146秒，未复现此前15秒超时，不能确证唯一根因或长期性能。

[资金流核验与本轮证据](research/moneyflow-readiness-20260918.md)确认09-17归档moneyflow为20/20同日单行、现有Financial合格15/20、wrapper及20个response摘要一致。现有六指标未消费资金流；根目录只有09-11/09-14/09-17三个资金日期，不能称连续资金流。供应方单位/统计契约尚待确认，仅固定一个大/特大单金额差比的第七等权指标提案，未实施新权重、采集或调度。对同15股的一次性内存敏感性计算与主任务独立Fraction复核一致：Top5集合仍5/5、只内部顺序变化；不是合法历史forward或收益增益。唯一预期消费者仍为原Financial lane，保留同集合、同成本、原配平与停止条件，不增账户或长期并行策略。

主任务联合109 passed、1项warning（7.69秒），最终backend全量**2818 passed、3项既有warnings（238.22秒、exit0）**，Ruff及diff检查通过。本轮新增代码已实现、已测试，**未commit、未push、未部署**；此前`edbd9fa`已部署记录保留为历史事实。下一步为受控发布这两项小变更后验收自然逐股行情时效与收盘后baseline/行业/Financial链路，未提高G2/G7完成状态或交易权限。两个代码子任务结果已消费并关闭，会话终态task_complete已核验但仍被PID41183打开，未删除会话；文档子任务由主任务结束后按同样安全条件复核，cleanup hook保持启用，不把任务关闭计为存储已清理。

### 2026-09-18 G7 观测发布准备

源提交`e999e4f5433ad424b441cc73f3950a4f87445389`已提交并push，更新上段未提交的历史状态；主任务本轮相关109 passed、1项既有warning（9.39秒），独立review四文件89 passed、1项既有warning（9.25秒），无P1/P2阻塞。API/storage变更会改变walk-forward research digest，recommendation alignment全包摘要也会改变，保留身份失配拒绝规则；G2冻结五文件及factor-shadow scorer未变。资金流指标仍未实施。

主任务07:05 UTC确认旧current仍edbd9fa，tick为outside_session、07:00 slot于07:00:37.417870完成、last_error=null；automation仍有1个active cycle/stage，仅准备新release，尚未确认本轮部署。完整范围、验证与后续发布证据见[分钟观测发布记录](research/minute-freshness-rollout-20260918.md)。G7逐股自然行情时效及G2-FQ3收盘后链路、成熟收益继续待验，不提高目标完成状态或交易权限。

本轮最终暂存验收：`/opt/qagent/releases/e999e4f5433ad424b441cc73f3950a4f87445389`已clone并核验exact SHA，Python3.11的uv frozen、临时DB隔离startup、npm ci/build均通过，仅有既有chunk超过500kB warning。07:08及07:09:47 UTC guarded helper preview安全exit1、未execute；07:10:27 UTC仍为1cycle/1stage，factor_shadow自07:05:13.805949 running，最近三次该阶段约15–22分钟，未强停。本轮已创建云端暂存release、依赖和构建文件，未修改运行中的服务配置、cron、账本、current或交易规则，三份研究cron摘要保持原值；已push、已暂存、**未部署**，没有本轮切换后的ledger/settings验收。部署待自然空闲后重新检查门禁，本轮未设置自动续办；不将隔离构建通过等同自然分钟时效或研究验收。

后续发布补录（约07:33 UTC）：current已切换为`e999e4f5433ad424b441cc73f3950a4f87445389`。首次helper在切换后、写result.json前中断，操作方沿同一`restore()`路径恢复；本次没有result.json，不记为helper一次完整成功退出。主任务恢复后独立核验，相对于`/var/tmp/qagent-rollout-relay-rbygwvqc/ledger-before.json`的ledger_equal=true，settings_equal=true、scheduler_enabled=true；backend/frontend runit均run、health正常、前端HTTP200，独立paper-update configured/enabled true、outside_session，未手动tick。更新此前未部署的历史状态为已实现、已测试、已push、已部署并完成恢复后对账及健康核验。07:23研究快照仍无09-18 source/rank/daily/signal，当时早于16:40自然窗口，不判为链路失败；G7交易时段逐股时效及G2-FQ3同日baseline/行业/可判别配平和成熟收益继续待验，不提高目标完成状态或交易权限。详见[分钟观测发布记录](research/minute-freshness-rollout-20260918.md)。

### G2-FQ3 自然 v3 信号与配平状态（2026-09-23，只读记录）

2026-09-22 北京时间20:10–20:13，自然 daily 产物为 `observed`；20:13:58，`financial-rule-forward-v3` 信号已 sealed，且同日 baseline 为 `available`；其后23:07的 forward 运行 `complete`、无 errors。该日 Financial eligible 为13/20，但 v3 matched control 为 `control_unavailable` / `same_industry_control_unavailable`、0 pairs，故尚无配对 lift 或成熟的5/10/20交易日结果。

这是按预注册的同候选池、同行业规则 fail-closed 的结果；不得因单日不可配对而改候选池、补值或新增因子至正式 Ranking。继续积累5个自然 v3 信号；触发既有停用条件，或取得首个可判别的5日结果后，再判断是否继续。此轮仅只读研究记录，未修改唯一模拟盘、正式 Ranking 或交易权重。

### G2-FQ3 v4 受控研究包准备（2026-09-24）

最终发布证据补充：主任务读取`/var/tmp/qagent-rollout-relay-8lnet469/result.json`确认`ledger_equal=true`、`settings_equal=true`、`scheduler_enabled=true`；API health正常，独立paper tick enabled=true、间隔600秒，主调度settings间隔1800秒。以下阶段快照按发生顺序保留。

主任务刷新09-22及09-23两份自然v3信号，均baseline available、control unavailable、0 pairs；未达连续五份停用阈值。已提交的全局同行业分配v4此次补齐evaluator协议识别，避免错误沿用旧G2 baseline收益比较；旧v1–v3重放保留，行业单例仍明确不可配对。新增从实际daily-v9/forward-v11到daily-v10/forward-v12的不可变bundle升级适配，保留原调度、预算、池和门槛，升级不重置连续失败观察及停用条件。当前已实现、相关测试通过，尚未提交、push或部署本轮补齐；[准备与验收记录](research/financial-global-control-rollout-20260924.md)。自然v4完整可判别五对和真实5/10/20结果仍待验，不更改唯一模拟账户、账本、正式Ranking或交易权限。

同日部署补录：源提交`83cdcb528a226538750b41adb6a2fd7cecac8af8`已提交并push，主任务子集复核26 passed。云端daily-v10/forward-v12研究包升级成功，重复preview为`already_installed`、`started_job=false`；receipt为`/var/backups/qagent-financial-global-control/before-install-9iswa1j8.json`，四份旧signal的SHA全部保持不变。研究包已部署，但backend `83cdcb5`仍在暂存准备、尚未部署；上述准备阶段快照保留。自然v4封存、配对及成熟收益仍待验，连续失败观察不重置。

后续后端部署验收：`83cdcb528a226538750b41adb6a2fd7cecac8af8`受控发布成功、进程exit0，证据目录`/var/tmp/qagent-rollout-relay-8lnet469`；helper完成ledger/settings一致性断言，后端隔离Linux startup通过，主任务相关118 passed、1项既有warning。前端暂存首次因缺node_modules报vite not found，经确认frontend源码diff为空后复用此前成功72构建的依赖，恢复后前端HTTP200、前后端runit均运行；过程存在维护与恢复，不称零停机。四份历史云端归档在生产PYTHONPATH下校验通过。更新上段后端尚未部署为已部署；自然v4信号、配对和选股收益仍待验。

### G2-FQ3 同行业对照可行性诊断（2026-09-24）

本轮[真实归档诊断](research/financial-control-feasibility-20260924.md)确认09-22/23的Top5各有四只在原20股中为行业单例，eligible分别13/20、16/20且baseline均available；原v3重放通过，同证据内存v4仍control_unavailable/0 pairs，文件SHA不变。等待价格成熟或优化分配不能补出这两日不存在的同行；未来自然集合可能变化，但未保证可配对。最小待选方案为保留原20股排名与Top5，另行有界采样同日同源同行研究control，并事前固定市值规则和预算；这会改变研究对照集合，尚待用户批准，未实施，不改变选股池、模拟盘或正式Ranking。既有连续五份失败停止条件及跨版本累计保持，本轮不自动暂停、不回填旧信号，不提升G2完成状态。此补充仅文档与只读诊断，未提交、未push、未部署。

同轮G2可执行缺口：主任务审计现有冻结collector仅归档predictions，未找到其20交易日结果消费者，已授权独立离线evaluator实施，目前进行中、未验收。保持冻结信号日复权收盘至第20个后续交易日复权收盘、Top10%、行业至少5股用行业中位数否则沪深300；成本沿用换手率×10bps，单横截面净结果留空，不混用Financial次日开盘口径，不新增调度或改变模型/账户。Financial唯一已成熟09-14五日窗口净超额为-3.2971742378645645%、baseline unavailable且lift为空，单个负窗口不构成配平假设检验；文件SHA和来源见上述报告。

后续实施补录：`scripts/evaluate_g2_forward.py`及专项测试已实现，子任务最终12 passed、无warnings；主任务最终联合64 passed（5.00秒）、零warnings，Ruff及diff检查通过。云端隔离临时smoke读取真实5541股归档返回waiting_for_maturity、到期2026-10-19、指标为空，原signal SHA不变且不存在的DB路径未被创建；证据及脚本SHA见上述报告。不将未成熟分支通过视为选股收益验收。已实现并完成上述验证，未提交、未push、未生产部署；仅写云端临时脚本/输出，未变更cron、模型、规则或账本。额外同行研究control仍为待批准方案。

### G2-FQ3 有界同行研究 control v5（2026-09-24，本地实施）

后续锚点边界补充：原Top5不在批量首屏时，沿用原逐股同日已验证行业/市值作为匹配锚点，不因首屏缺席单独拒绝；若批量包含锚点则须精确一致。额外control仍仅取捕获交集，不伪造control或改变排名。主任务调整后独立复跑同七组测试，最终140 passed（17.81秒），所有修改Python文件的Ruff及diff检查通过；下段137 passed保留为调整前基线。未部署、未启用及收益未验边界保持。

本轮已批准并实现默认关闭的peer-control v5，更新此前“待批准、未实施”的阶段状态；[范围与限制](research/financial-control-feasibility-20260924.md)。保留原20股、排名、Top5和原report字节，另取最多10只研究control；不扩选股池、不改paper或cron。供应方offset语义不可靠且industry过滤被忽略，故仅取stock_basic及同日daily_basic各第一页、各最多5000行，在捕获交集内做本地精确行业过滤和市值距离选择；完整性未知，最近仅限捕获交集，抽样偏差明确保留。每批新增最多2次批量加70次财务请求，即72次，原160次上限增至232次，仍共享600秒deadline和同日两批预算。`--peer-controls`须同时配`--daily-frozen-industry --bounded-same-day`。主任务当前七组联合测试137 passed（16.67秒）、diff检查通过；只读集成review无阻塞项，确认v1–v4行为保持及v5原候选/额外control分开计数。已实现并完成上述验证，未push、未部署、未启用，无本轮自然配对或真实收益提升证据；保留旧信号、既有停止条件及跨版本累计。连续五份失败停止条件是运行决策标准，并非已实现自动计数/停调机制，G2状态不提升。

### G2-FQ3 peer-control v5 发布准备（2026-09-24）

现有daily-v10/forward-v12及13/4时槽经云端只读核验；新增指向daily-v11/forward-v13的版本化包装器与受控升级器，v11仅对唯一Financial研究链路显式启用`--peer-controls`，保留原600秒及同日两批预算。旧cron/manifest摘要已固定，旧包和历史信号不覆盖；[发布步骤与回滚边界](research/financial-peer-control-rollout-20260924.md)。主任务最终backend全量**2878 passed、3项既有warnings（400.16秒、exit0）**；最终新包装器/升级器专项7 passed（4.02秒），含两个manifest-only隔离包测试3 passed；Ruff及diff检查通过。当前已实现、已本地测试，**未提交、未push、未部署、未云端启用v5**；本地测试不替代自然v5封存、完整可判别五对及成熟收益。唯一模拟账户、账本、正式Ranking和交易权限未改变，G2状态不提升。

后续受控安装补录：源提交`4885639`已push至`origin/features/automation-backtest`，云端daily-v11/forward-v13 tar及manifest摘要与[发布记录](research/financial-peer-control-rollout-20260924.md)一致。升级前preview为`planned/old`，execute为`upgraded/new`、`started_job=false`，安装后preview为`already_installed/new`；私有备份收据为`/var/backups/qagent-financial-peer-control/before-install-gisc2gj1.json`、root:root 0600。新cron摘要分别为`cb22a5396452b17fd860ba2d11177afd3d0fc4e056a26b0b368c797782bb3e17`和`706b15213bd395046faa0de434d9b168fff1dc5bdb957fe5965ed55af0fb5fb5`，保持13/4时槽、root:root 0644；health正常、backend current仍83cdcb5，paper update enabled/outside_session、attempts4/completed4、last_error null。既有四份归档SHA未变，v13对四份`validate_archive`均通过。更新上段发布前状态为**已实现、已测试、已push、独立研究包及cron已部署启用**；尚无自然v5封存、完整可判别五对及真实5/10/20收益证明，不改变唯一模拟账户、账本、正式Ranking或交易规则，G2状态不提升。

### G9 / P0：持久化主目录与镜像重启恢复（2026-09-24）

本地准备中：将 Linux 部署的 canonical root 收敛到 `/home/luozhenkun/qagent`（可由`QAGENT_HOME`覆盖），包括 release/current、state DB、backups、logs、config、peer-control bundles/data，并提供重建 ephemeral `/etc` runit/cron 定义的 bootstrap。health 默认备份目录和 runit 定义回滚归档也指向持久 backups；provider config 与 Ranking V3/V4 原签名密钥必须保存在持久 config。bootstrap 必须在 `/home` 已挂载后运行；只有持久 state 中由显式 enable 创建的 `.single-writer-approved` marker 存在时才恢复已启用服务链接和 cron；marker 缺失时保持 down/disabled。bootstrap 验证配置指向持久 DB、DB 存在且 quick_check 通过；不隐式创建/替换生产 DB 或 marker，也不触发 API scheduler start。当前已完成聚焦本地测试，**未push、未部署**；当前 cloud image 缺少 Python 3.11 与 uv，不能据此尝试部署或安装运行时。

当前 bootstrap 不重建 G1 observation 或 G2 collector cron；这些任务要等 bundle/data 路径迁入持久根目录、G2 frozen models 外部恢复并校验摘要、且各自有显式持久 opt-in/rehydration 规则后再单独接入。仓库未包含 G2 frozen model 文件，不得据此恢复 collector。

完成条件：先由镜像/平台提供可验证的启动命令，在`/home`持久盘挂载后、runsvdir/cron消费定义前调用 bootstrap；执行一次受控 fresh paper run 后验证重启前后 DB manifest/账本完全一致、备份可读且 fresh run 只写原唯一 paper account，cron/scheduler 状态按持久 marker 恢复。自然重启恢复和账户验证未完成前不提升本项目运行可靠性状态，不迁移或截断 stale DB，不因 rehydrate 调用 paper scheduler start。

本地新增显式 fresh ledger 初始化入口：仅在持久 `state/qagent.db` 不存在时独占创建，要求操作者显式填写初始资金及全部账户规则并校验，启动唯一 `default` 会话，拒绝已有 DB；不创建启用 marker、不启动服务或调度。2026-09-15 历史活跃账户快照见上文（10 持仓、10% 单笔分配、成本/滑点各 5bps、止盈 50%），但该快照不能证明最新初始资金或当前规则；新账本无法恢复旧会话、持仓、现金和事件。此项仅覆盖全新账本准备，不构成云端 fresh run 或重启恢复验收。专项测试与提交、push、部署状态由主任务集成时补录。

2026-09-25 进展（更新上述历史快照）：持久化部署实现 `8204c60`、前端 package 修复 `d638abb`、安装器工作目录修复 `4bcbf82` 均已提交并 push；当前云端 release 为 `d638abb`。`/home` 为持久 XFS，运行环境已具备 Python 3.11.16、uv 0.12.18、Node 24.21；后端与前端服务健康，前端 HTTP 200。实现和聚焦本地测试已有证据，上述 push、云端 release 与服务检查分别成立；`4bcbf82` 已 push 不等于当前 release 已包含该修复。

云端已显式启动全新、唯一 `default` 模拟账本会话 `paper-session-50fa0927861b`：初始资金 100000、单笔分配上限 10%、最多 10 个持仓、成本及滑点各 5 bps、止盈 50%。未使用 09-01 旧备份，旧会话、持仓、现金及事件不视为已恢复。主调度已启动、间隔 30 分钟；独立 paper update 间隔 10 分钟，当前为 `outside_session`；首轮自动化周期正在扫描，尚无成交。ProMax 与 Datahubco 经云端独立 Squid 代理分别取得认证请求 HTTP 200、业务 code 0、各 1 行；首份备份已验证可读。这些是单次服务、数据源和备份检查，尚不证明自然周期完成、交易时段更新时效或长期可用性。

G9 **仍未完成**：镜像重启时在持久 `/home` 挂载后重建 ephemeral `/etc` 定义的启动 hook 尚未安装，受控重启及重启前后 DB manifest、唯一账本、备份和 marker 恢复状态对账尚未验证。G1/G2 所需研究 bundles 与 G2 frozen models 仍缺失，未恢复其观察和 collector 任务；不以主调度运行替代 G1/G2 自然研究验收。当前证据边界为：新账本初始化和服务启动已验证；首轮周期/自然交易尚未完成，重启恢复尚未证明。

2026-09-25 后续运行补录：空目录递归修复提交 `998b908` 已提交并 push，云端 release 已从上述 `d638abb` 切换到 `998b908`。切换前的旧自动化周期已自然终结为 `deferred_with_alert`，原因是超过最大递归层数；这是旧周期的失败终态，不计为成功扫描或交易验收。切换前发布 preflight 通过。切换后在临时空库中用模拟源验证股票与 ETF 加载函数各调用一次、无递归；后端、前端 runit 均为 run，Mac 隧道访问前端 HTTP 200、后端 health 正常。这些分别证明修复已发布、空库回归行为与服务可达，不证明全市场扫描已经完成。

切换后的目录只有一份，含 5,574 个标的；新全市场扫描任务已启动，但截至 09:33:39 UTC 进度仍为 `0/5574`、状态 running。唯一模拟账户此时交易事件数为 0；尚无本轮自然扫描完成或自然成交证据。自然 10 分钟 paper tick、镜像重启后从持久 `/home` 恢复的启动 hook、重启前后 DB manifest/账本/备份/marker 对账仍未验证，G9 维持未完成。G1/G2 的研究 bundles、G2 frozen models 与相应自然观察/collector 仍缺失，不因此提升 G1/G2 状态或扩大交易权限。

2026-09-25 持久启动信任边界补录：提交 `5c958d7519c3ad571c65a5027648ff5145388598` 已 push 至 `features/automation-backtest`，实现独立的 root-owned 持久启动包、root 独立审批，以及 backend 降权后读取 env；主任务本地相关测试 **61 passed**，`bash -n` 和 diff 检查通过。云端由 root 安装的 `/home/qagent-boot` 为 root:root `0755`，其中 `bootstrap.sh` 为 `0755`、模板为 `0644`。这证明启动材料已安装，尚未证明镜像启动 hook 已配置或重启恢复可用；当前运行中的 backend/frontend 仍为原 PID `150367` / `150368`，未因本轮启动包安装而重启，云端 current release 仍为旧 `998b908`，不包含此提交。

独立 root 审批曾创建 `/home/qagent-boot-approved`（root:root `0600`），并与 `/home/luozhenkun/qagent` 匹配。随后发现当前旧 release 的 disable 脚本不会撤销该审批；为避免过渡期误恢复，已将审批文件原子移至 `/home/qagent-boot-approved.pending-5c958d7`（root:root `0600`）。因此**当前有效 root 审批缺失，恢复应 fail-closed**；可信启动包保留，须在受控切换到新 release 后重新运行独立 approve，不能把曾经批准写成当前获批。平台在持久 `/home` 挂载后、服务消费 ephemeral `/etc` 定义前调用 bootstrap 的 hook 仍未配置或验证，也未做镜像重启及重启前后对账。

10:03 UTC 第二次自然调度返回 `already_running`，仍指向同一扫描任务 `full-scan-20260925093257-11955b9b`，未创建第二个扫描；当时进度 `2400/5574`、errors `0`、状态仍为 running。唯一 paper 会话仍为 `paper-session-50fa0927861b`，交易事件 `0`。这更新了上段 09:33:39 UTC 的进度快照，但不证明扫描完成、自然交易时段 10 分钟 tick 或成交。G9 **仍未完成**：待新 release 受控切换及重新审批、平台 hook 配置与验证、受控镜像重启和 DB manifest/唯一账本/备份/marker 恢复对账，并观察自然交易时段 tick；不提升 G1/G2 或模拟交易权限。
