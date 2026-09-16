# Financial Challenger 候选池研究适配

## 2026-09-16 候选池新鲜度依赖与有界重试

09-15 的自然运行暴露了调度依赖：daily v3 在北京时间 16:40 运行时，全市场扫描尚未完成，候选池仍是前一交易日；采集器正确 fail-closed，但单次 cron 没有后续机会。全市场扫描在 18:51 完成，因此 19:37 forward 也没有当日 daily 产物可封存。

本轮修复不新建账户、数据库、排名器或第二条研究链路，仍只调用原 daily collector 和原 forward runner：

- daily 候选池模式在工作日北京时间 `16:40、17:10、17:40、18:10、18:40、19:10` 有界尝试。候选池未新鲜时不请求七个财务接口，只写一份轻量、不可覆盖的调度证据并以临时失败码 `75` 退出。
- 同一交易日已有摘要验证通过的 `observed` 候选池 daily 产物后，后续尝试直接 `already_completed` 退出，不再请求候选池或七个接口。显式 `--symbol` / `--symbols-file` 路径保持原行为，不使用该去重语义。
- forward 在北京时间 `19:37、20:07、20:37` 有界尝试。缺当日 daily 时归档 `waiting_for_daily`；当日 daily 成功后复用原 seal/evaluate。信号路径按交易日唯一，已封存时校验原摘要并返回 `already_sealed`，不重复封存。
- 尝试窗口到期即停止；daily 最后一次为 19:10，forward 最后一次为 20:37。运行失败、等待和锁冲突均保留审计产物，不由 `no_rows` 推断停牌。

升级 helper 从已部署 daily v3 / forward v5 严格校验的 cron 和 manifest 起步。升级先替换 forward consumer，再替换 daily producer；若中断，`daily-old + forward-new` 是唯一可接受混合态，重试仅续写 daily。回滚按相反的安全依赖顺序，先恢复 daily producer，再恢复 forward consumer；第二步失败仍停留在同一兼容混合态，可重试完成。`daily-new + forward-old` 不兼容，helper fail-closed，不猜测操作意图。私有 receipt 校验回滚身份，安装和回滚都不启动任务。它不扩大 Datahubco/Tushare 消费边界：仍使用现有财务查询和原始日线后备，不接分钟或复权价。

当前仅为本地实现与测试阶段。父任务专项回归 **96 passed**；正确虚拟环境全量为 **2668 passed、1 failed、3 warnings（957.73秒）**。唯一失败是 `tests/test_paper_writer.py::test_process_death_releases_writer` 的 `spawn ready.wait(8)` 超时，随后对该用例隔离复测 **1 passed（3.15秒）**。这组证据不能表述为“全量一次全绿”；必须保留全量运行中单一超时和隔离复测通过两项事实。

云端首次从 daily-v4 独立bundle执行helper preview时安全失败，错误为 `ModuleNotFoundError: upgrade_financial_forward_research`；原因是daily和forward新bundle的manifest各自只包含半边依赖，而原测试通过仓库 `sys.path` 隐式补齐。失败发生在preview导入阶段，cron未修改、任务未启动，不构成安装或部署成功。修复后两个新bundle使用同一严格最小完整依赖闭包，并从仅复制manifest列出文件的隔离bundle、非仓库cwd、`-I -S` Python进程分别验证daily/forward preview；manifest仍拒绝任何额外文件。本轮helper相关专项 **39 passed（2.49秒）**，Ruff和diff检查通过。本次修复未 commit、未 push、未安装、未部署。测试不替代下一个交易日的自然扫描完成、daily 成功和 forward 封存验收。
## 目标与复用关系

`collect_daily_documented_research.py` 新增可选 `--candidate-pool` 输入。它只读调用现有本机 `/api/paper-trades/candidate-pool?provider=free&include_etfs=false&limit=100`，验证全部返回项并按来源顺序保留前20只合格A股，再原样复用既有七接口采集、财务分析和 `rank_financial_candidate`。唯一消费者是现有 Financial Challenger；目标是经对照验收后替代固定20股观察集合，不建立第二套排名、组合或长期并行任务。

主任务真实GET发现 `include_etfs=false` 的响应仍可能包含 ETF（首项曾为 `CN:159146`、`asset_type=etf`），因此该查询参数不被当作过滤已完成的证据。适配器只对 `asset_type=etf/fund/index_fund` 且沪深基金代码、可选交易所后缀均合法的记录执行有证据排除；股票和基金共享同一日期健康校验。

默认的 `--symbol` 和 `--symbols-file` 行为不变，并与新模式互斥。适配只接受 loopback base URL，不读取供应方密钥，不写数据库、模拟账本或账户，不改变正式 Ranking、交易规则和权限。

## 失败关闭与证据

进入七接口采集前必须同时通过：响应是对象且 `items/summary/data_health` 类型正确；来源展示数为1至100且计数证据一致；每项日期等于请求交易日且 `signal_date_fresh=true`；响应健康证据明确 endpoint、limit=100、fresh、期望信号日一致且 mismatch=0。`asset_type=stock` 必须映射为合法沪深北A股且无后缀冲突、无重复；明确基金类型必须映射为合法沪深基金代码，否则同样失败。基金排除后至少有5只股票，按原顺序最多选择20只。任一失败均退出，不请求七接口。

成功报告的 `universe` 保存 endpoint、provider、include_etfs、来源请求上限100、选择上限20、来源 shown/total、合格股票数、期望信号日、原始 summary/data_health、完整响应 digest、映射后的有序列表和 universe digest。基金及超过20只的合格股票写入 `excluded_items`，按原因汇总并保存 count/digest。因此相同原响应可重建相同观察集合和排除过程；报告同时声明候选池准入与财务排序是两层独立规则。

## 验收与运行边界

先用同一候选池集合、同一信号日、同一财报期与成本口径保留固定输入路径和候选池输入路径的基线对照；至少取得首个真实5交易日成熟结果后，再比较覆盖、Top5变化和同集合5日净收益。数据健康连续合格、同集合基线存在及5日证据支持继续观察，才可另行批准替换固定集合调度。

候选池模式已按下述consumer-first步骤接入原有daily/forward调度，未新建并行研究链路或第二模拟账户。自然运行连续失败、无法形成同集合基线、首个5日结果否定候选或 Financial Challenger 长期不消费时，应停用该模式并移出运行链路，只保留必要证据；不得长期同时运行固定集合与候选池集合。

## 上线前兼容与升级设计

Financial forward seal 继续接受旧 `explicit_observation_order`，其 digest 算法不变；同时新增对 `paper_candidate_pool_order` 的严格校验，覆盖来源身份、100/20限制、股票映射和顺序、来源位置、选中/合格/来源计数、排除原因与digest、响应日期健康、响应及universe digest。任一字段被篡改均拒绝，原有同日15:30后、raw evidence和至少5只eligible门禁不降低。

上线采用consumer-first顺序：先将 `financial-forward v4 -> v5`，确认兼容旧 `explicit_observation_order` 的consumer就位，再将 `daily v2 -> v3`；由此不存在动态daily被旧forward拒绝的窗口。daily v3只把bundle路径及固定20个 `--symbol` 改为 `--candidate-pool`；forward v5只替换bundle路径，使19:37 runner加载新evaluator。两步分别固定校验当前manifest/cron摘要、加独占锁、留0600备份并以临时文件原子替换，支持预览、幂等和显式rollback，且不启动任务。`upgrade_financial_candidate_pool_chain.py` 编排两步；若daily第二步失败，则forward回滚v4：首次升级使用0600备份，重试时已处于v5则用经固定摘要及bundle校验的旧cron模板恢复。

实现验收专项 **143 passed（3.35秒）**；主任务使用正确backend虚拟环境完成全量 **2660 passed、3项既有warnings（256.45秒）**。此前一次从backend工作目录误写 `backend/.venv` 路径，命令立即exit 127且未执行测试，已由正确命令的完整通过结果取代。未修改模拟盘、数据库、后端服务或交易策略。Ruff与diff检查通过。

## 部署验收与待自然验证

源提交 `d599a91` 已 push 至 `features/automation-backtest`。consumer-first 两步升级均成功且 `started_job=false`，升级后daily与forward的preview均为 `already_installed`，health为ok：daily v3 bundle `/opt/qagent-research/daily-financial-20260914-v3` 的manifest SHA为 `1a6855501e14e4d54b2eca0f59afc618971135b932e74d612de835399307efbe`，cron SHA为 `701215372bdb050190ab1df52910e74040ec6545b3691978575d7de823b19aa7`，工作日北京时间16:40运行；forward v5 bundle `/opt/qagent-research/financial-forward-20260914-v5` 的manifest SHA为 `386ea1dbc0c427a4693e0109161145c89b73df83a9820e6ffefa35d7333711ae`，cron SHA为 `04040c5f48a42865c5f43289c5012fdfadd0dcd6f0f50500505397e783ad2016`，工作日北京时间19:37运行。

云端唯一模拟盘仍为 `paper-session-69470ca6b12c`、active；current_model为total 58、pending 1、open 9、closed 39、active 10，规则保持max_positions 10、allocation 10%、cost 5bps、slippage 5bps、take_profit 50%，未创建第二账本。隔离端到端的140/140 observed、19 eligible和候选池动态适配已经验证；但今日自然daily和forward尚未发生，绩效及晋级未验证，不能宣称选股收益提升。

## 2026-09-15 本机集合验收

loopback 真实 GET 的 `expected_signal_date=2026-09-14`，来源 shown/total 均为90。适配器验证出56只合法股票和34只明确 fund/ETF，按源顺序选择前20只；首3只为 `002746.SZ`、`600025.SH`、`688581.SH`。排除证据共70条：34条 `explicit_fund_asset_type`、36条 `selected_limit`。真实响应确认 `include_etfs=false` 仍会混入ETF，适配层已留证排除。

本次只验证候选集合，未调用后续七接口。主任务相关 **99 passed（1.89秒）**，Ruff与diff检查通过；未 commit、未 push、未 deploy，未改调度和模拟盘。同集合基线和5日结果仍待后续验收。

## 隔离端到端补跑

临时产物 `/tmp/qagent-fin-pool.UlhQIW/20260915T105253-612f840dffc645e7a196e929cc7dc7db.json` 使用 `trade_date=20260914`、`observation_day=2026-09-15`。20股×7接口共140项 classification 全部 observed，报告 status observed，result digest `cb59...` 校验有效。19只 eligible；`600028.SH` 因 `fina_indicator` 存在 ambiguous consumed revision 被明确排除。财务候选 Top5 为 `603565.SH`、`600398.SH`、`688581.SH`、`601919.SH`、`601857.SH`，与原候选顺序 Top5 重合2/5。

这是次日隔离补跑，不得追认为09-14前向信号；产物未进入正式归档，排名差异不构成收益结论。未部署、未改 cron 或模拟盘，前述相关99项测试证据保留。
