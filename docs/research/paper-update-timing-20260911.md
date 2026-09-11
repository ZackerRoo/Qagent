# 模拟盘持仓更新及时性：代码事实与最小分离方案

日期：2026-09-11。范围为现有单一模拟账户的更新可靠性与延迟；本轮完成调查和方案，不改变频率、日线降级规则、生产调度、冻结模型或交易权限。当前代码与线上证据分别列示，代码测试不代表线上成交正确。

## 已确认结论

分钟取数失败通常会继续日线 fallback，日线可以参与模拟成交和估值。不能把分钟超时一概表述为“该股票跳过更新”，也不能由 `latest_date` 为当天推断分钟覆盖完整。现有 scheduler 在整轮结束后再等待 interval，研究阶段的同步耗时会增大两次 paper 更新的间隔；free 全市场扫描本身通过后台任务提交，不能把扫描从开始到完成的全部时间当作 scheduler 被阻塞的时间。

### 分钟异常与降级路径

| 条件 | 当前行为 | 对成交与估值的含义 | 代码证据 |
| --- | --- | --- | --- |
| 无分钟 getter、非 A 股、无法确定信号时间 | 分钟路径返回 None，尝试日线 | 有有效日线时仍可更新 | `backend/qagent/paper_trading/engine.py:4730` |
| 分钟 getter 抛异常或返回空表 | 捕获 getter 异常，或识别空表，随后日线 fallback | 不保证跳过；日线 `_evaluate_trade` 能开仓、退出和更新最新价 | `engine.py:4740`、`:948`、`:4359` |
| free_cn 分钟源超时 | provider 捕获异常、记录 last_errors、返回空表；缓存 wrapper 收集错误 | 取数失败可同时有日线更新与阶段错误报告 | `backend/qagent/providers/free_cn.py:284`、`cached.py:540` |
| 分钟表非空，但推荐之后的有效时间行为空 | 返回保留状态和未覆盖说明，不进入日线 | 本轮不获得推荐后的分钟更新；原始 rows>0 仍计 resolved | `engine.py:937`、`:4771` |
| 分钟表字段/数值解析或评估抛错 | 不在 getter 的 try 内，外溢至 paper stage | 停止后续股票处理；之前逐笔提交的更新保留 | `engine.py:4746`、`routes.py:4666`、`backend/qagent/storage/paper.py:794` |
| 日线也为空 | 当前股票 continue | 本次该股票估值/成交评估没有新结果，coverage 不足 | `engine.py:956` |
| 日线 getter 或评估抛错 | 外溢至 paper stage | 与分钟评估异常一样，可能部分持久化后失败 | `engine.py:950`、`:961` |

日线 fallback 已有测试 `backend/tests/test_paper_trading.py:905`：分钟为空、缓存有日线时，pending 变为 open 且更新最新价。这是当前语义的直接验证，不是本轮提出的新策略。保留执行上下文已有的交易规则；是否将日线降级限制为估值、或分钟失败时禁止成交，是另外的行为变更，本轮不实施。

`paper_price_resolved` 表示有返回行/其他已处理状态，并非分钟完整性或新鲜度；`paper_minute_rows` 是整轮累计，无法定位单只股票最后可用时间。`updated_at` 是账本更新时间，`latest_date` 只有日期，两者均不能单独证明最新分钟已被处理。`routes.py:3884` 的阶段判定会先检查 provider errors，即使部分账本更新成功，也可能将该 stage 判为错误并进入既有重试流程。

### 调度与写入边界

当前同步阶段顺序为 scan → fuyao market/theme → paper seed → paper update → factor shadow → fuyao shadow → alerts → forward evidence（`backend/qagent/api/routes.py:4312` 起）。paper 前的同步阶段推迟本轮更新，paper 后的同步阶段推迟整轮完成和下一轮更新。`backend/qagent/jobs/automation_scheduler.py:520` 明确用 `finished_at + interval_seconds` 安排下一轮；默认 interval 为 1800 秒，当前线上值须另查。正常相邻轮的 paper 开始间隔包含上一轮 paper 及后续耗时、interval、下一轮 paper 前耗时；重试和协调冲突另外计量。

free 扫描由 `_maybe_start_automatic_full_scan` 提交后台任务（`routes.py:4334`、`:6752`），是否发生 CPU、SQLite、网络争用要靠运行指标证明。factor shadow 的 90 秒预算声明为 provider 调用之间协作检查，不是硬取消（`:4695` 起），也不能由预算断言每轮耗时上限。

automation 已有进程内 `_run_lock`、整轮 single-host SQLite flock、持久化 lease/fencing token、cycle slot 与 stage checkpoint（`routes.py:4094`、`:4151`、`:4170`；`backend/qagent/storage/automation_runtime.py:166`）。现有 flock 覆盖整轮，直接新增一个同锁的 paper cron 仍会等待重任务；另换一把锁则失去互斥。

手动 `POST /paper-trades/update` 在 `routes.py:7403` 直接调用 engine，未经过 automation 整轮 fence。该事实说明分离设计必须收口入口，并非已经发生并发双写的线上证据。执行事件已有 `trade_id + phase + market_event_id` 幂等键（`engine.py:4211`）；事件幂等不能替代写入互斥与过期快照校验。整轮失败不是整轮账本回滚，单笔 update 有独立提交。

## 2026-09-11 线上只读快照

以下由主任务云端查询提供，本报告未自行执行云端操作：09:30 UTC 核验到上一轮 09:00 结束 cycle 的 `paper_minute_checked=9`、`paper_minute_rows=9002`，last_error 含 002696、605177 的 Sina minute `read timeout=3`。两只股票仍 open、`latest_date=2026-09-11`，账本 `updated_at` 分别为 07:19:49 和 07:08:46 UTC。09:30 新 cycle 正在 paper_update，未被中断。

09:31:18 UTC 主任务再次查询，最新 paper_update stage 为 error，error_text 为 002293、603259 的 Sina minute `read timeout=3`，`last_error_kind=provider_or_coverage`。超时再次出现且不止前两只股票，不能宣称问题已经修复。

这些观测确认分钟源失败与更新时效需要区分；尚未证明错误成交、漏掉某次止损、两只股票用了哪条具体日线、或扫描导致了延迟。下一步应关联该周期的 stage 起止、逐股票行情时间与执行事件来源，不能用日期或总行数补足缺失证据。

主任务同轮对 61 笔 funded 交易进行只读账本归因复核：total PnL -5,588.79、realized -5,225.95、unrealized -362.84、fees 415.32、slippage 436.49，五项对账差均为 0；源摘要 `8fbbcd03a81168b24f377a1350357b8951f462d7c3702eec9420268a8cb8caae`。这证明此次账本内部归因对账通过，不能证明行情完整、新鲜或成交正确。

## G7 最小轻重分离方案（待实施确认）

目标：让持仓更新拥有独立、可观测的到期时间，同时保留唯一账本、唯一写入方及现有执行语义。先测量延迟，不直接选更短生产周期，不新增第二模拟账户。

1. **先补最小诊断契约。** 在独立诊断产物中区分 due/start/finish、等待写锁时间、paper 各股票取数耗时、最后分钟 timestamp、分钟异常原因、实际选用分钟/日线/无价、有效行覆盖和报价年龄。保留现有 ledger/event，仅在未来运行增加必要诊断，不回写历史执行事实。盘中、午休、收盘与非交易日按现有市场时段分别评估，避免自然无行情误报。
2. **所有 paper 写入口汇入唯一 writer。** 将 scheduler、手动更新及 seed/replace 等会改变同一账户的动作汇入同一串行写服务；在同一主机/同一 DB 身份上共享 writer fence，保留 lease owner/token 校验。writer 在读交易状态前获取归属，并在提交前确认归属；断电/失租后的旧 owner 不得提交。重任务仅提交有稳定 ID 的意图，不能持另一个账户副本直接写 ledger。已有 flock/lease 工具可复用，但需区分研究 cycle 锁与账户写锁的职责，固定获取顺序避免互锁。
3. **重任务移出账户锁持有范围。** 扫描、研究、标签补齐沿用现有 job/checkpoint/预算，完成后交付研究结果；持仓更新 tick 仅读取现有账户和取价、调用既有引擎。长网络调用若仍在 writer 内，先测量其时间上界；如后续把取价放到锁外，必须提交时重新读取交易状态并校验快照版本/报价时间，不能应用旧快照。不要仅把整轮 runner 放入第二线程或 cron。
4. **保持幂等与恢复身份。** 到期 tick 保留稳定 slot，重试同 slot，不因重启产生新的账本效果；已完成阶段可重放结果，部分完成重试复用交易事件身份。跳过/合并的过期 tick 必须记录原因且不能制造历史成交或补写旧证据；不放宽费用、T+1、价格质量、容量或持仓规则。
5. **最后才选频率与切换。** 依据基线延迟和取价预算确定 tick 目标，先在隔离数据库回放下述场景。切换时确认没有活动 writer/旧周期，保留账本摘要与 settings；只能有一个正式入口启用，回滚同样恢复唯一 writer。方案验收不等于授权生产拆分，本轮不切换。

### 可验收场景

| 验收项 | 通过条件 |
| --- | --- |
| 延迟基线 | 至少覆盖一个正常交易时段，记录 paper due/start/finish、前后研究阶段耗时及逐股票有效行情时间，能拆分 scheduler 等待与数据源等待；无行情说明原因 |
| 研究长阻塞 | 隔离环境将研究阻塞超过 paper 到期时间，paper 按独立到期执行；观察等待不超过测试中声明的 writer/行情预算，研究不持账户锁 |
| 并发与失租 | 同时触发 tick、手动更新、seed，最多一个账户 writer；旧 owner 失租后提交被拒，进程死亡能由新 owner 恢复 |
| 部分完成与重启 | 第一笔提交后注入失败，重试同 slot 不重复扣款/成交/事件，未完成股票能续跑；已完成 slot 只回放结果 |
| 分钟降级 | getter 异常、空表、无推荐后有效行、解析错误、日线为空分别覆盖；产物准确区分来源与失败阶段，当前日线成交语义保持不变 |
| 账本与权限 | 仅一个 ledger；研究及 shadow 无账户写权限；切换前后交易历史、现金、持仓和 settings 对账；不进入实盘 |

## 本轮交付与验证

本轮仅新增此报告和 G7 跟踪项；没有新增生产逻辑或测试代码。现有 `test_paper_trading.py`、`test_automation_scheduler.py` 合计 **75 passed（8.32 秒）**，包括分钟空表降级及调度恢复场景。`test_automation_runtime_coordination.py` 定向验证 process fence、失租、同 slot 回放、paper 部分提交重试，**4 passed、28 deselected（4.45 秒）**。这些验证覆盖现有保护，并不证明尚未实施的独立 writer 方案。完整回归由主任务执行，本文不提前引用结果。本报告交付时未 commit、未 push、未部署、未启用调度拆分；主任务后续集成状态单独记录。
