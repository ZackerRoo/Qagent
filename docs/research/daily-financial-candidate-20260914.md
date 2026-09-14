# G2-FQ1：每日财务采集与独立候选（2026-09-14）

本轮用户授权把已验证的财务查询扩展为日常采集，并形成独立研究排名。范围属于既有 G2-FQ1，不改变 G1–G7 的历史与验收条件。以下为本地实施方案和测试记录；安装、自然触发、数据有效性及选股增益分别验收。

## 固定观察范围与链路

观察集合固定为 `600519.SH,603259.SH,300562.SZ,603766.SH,688612.SH,688002.SH,300750.SZ,300308.SZ`。前两只已有单次财务查询证据，后六只来自最新缓存展示的 A 股；这是有界研究观察集合，不能称全市场或生产排名基线。

独立 `collect_daily_documented_research.py` 通过本机 `http://127.0.0.1:8000` 系统只读 API 查询 Datahubco；凭据由已有后端管理，脚本和 cron 不读取或传递密钥。每页 12 行、固定 `period=20260630`，采集现金流、利润、每日估值、预告及资金流。财报期须后续根据研究要求显式更新，不随系统日期自动滚动。

采集器保留各分区原始证据、来源、取得时间、缺失和错误，通过既有财务分析器生成当前观测；调用 `rank_financial_candidate.rank_candidate` 产生独立候选，并嵌入 `financial_candidate`。原始观察集合与独立排名需要分别保留，不能把输入股票顺序称为生产模型排名。具体排名协议及字段以排名器归档协议为准，收益和成本标签不参与本次排名。

## 独立调度与安装

新文件 `/etc/cron.d/qagent-daily-financial-research` 固定周一至周五 `08:40 UTC`（北京时间16:40），主机须为 UTC。命令契约：

```sh
/opt/qagent/current/backend/.venv/bin/python -B /opt/qagent-research/daily-financial-20260914/scripts/collect_daily_documented_research.py --base-url http://127.0.0.1:8000 --source datahubco --symbol 600519.SH --symbol 603259.SH --symbol 300562.SZ --symbol 603766.SH --symbol 688612.SH --symbol 688002.SH --symbol 300750.SZ --symbol 300308.SZ --period 20260630 --today-close --budget-seconds 600 --output-dir /var/lib/qagent-research/daily-financial
```

`--today-close` 要求上海时间15:00之后并使用当天日期；它不推断交易日，节假日无行应保留为证据。600秒为请求启动预算，采集器预留单次70秒请求时间；不是严格 wall-clock 上限。已有16:30分钟研究cron、后端env、10分钟tick和账本均不由此安装器修改。

部署主任务先准备固定目录 `/opt/qagent-research/daily-financial-20260914`，及由服务用户拥有、权限0700的 `/var/lib/qagent-research/daily-financial`。包内至少包含安装器、采集器、排名器及其7个依赖文件；所有文件由 root 拥有且禁止组/其他用户写入。`manifest.json` 格式为：

```json
{"schema":"daily-financial-bundle-v1","files":{"scripts/collect_daily_documented_research.py":"<sha256>","...":"<sha256>"}}
```

`files` 必须列出包内除 manifest 本身外的所有文件及 SHA256，包括安装器本身。安装器拒绝未知额外文件、缺失依赖、软链接、路径穿越和摘要不匹配。部署者核对 manifest SHA256 后，先不带 `--execute` 运行安装器查看计划，再用相同 `--expected-manifest-sha256` 显式执行；无开关不写任何文件。

首次安装使用私有目录锁、临时文件及独占硬链接原子发布 root:root 0644 cron，拒绝覆盖并发创建或已有不同内容；相同内容重复执行为 `already_installed`。安装前在 `/var/backups/qagent-daily-financial-research` 0700目录保存0600收据，记录原目标不存在及预期新SHA。由于这是新任务，收据代表“原来无文件”，不伪造旧cron备份；回滚应由主任务确认当前SHA后仅删除新cron。安装器不启动任务，不调整已有凭据或服务。

## 验收和限制

先以 `--trade-date 20260911` 手动验证完整交易日的八股分区、财报匹配、缺失/旧预告标记、排名集合及摘要重放；随后安装当天16:40任务，并检查首次自然触发和独占归档。当前财务观测不能倒填09-11冻结信号，当前排名不能视为历史PIT，旧预告不能称当日事件。

候选不得自动接管模型、生产权重或唯一模拟账户。新候选的同集合、同窗口和同成本前向比较、成熟收益仍须另行验收；成功查询、安装或生成Top5都不证明选股改善。G2-FQ1和G2不据此提升完成状态。

本子任务实现安装器、专项测试及本文档，未修改PROJECT_GOAL。安装器专项 **11 passed（0.11秒）**，Ruff及diff检查通过，覆盖预览无写入、原子安装、重复安装、旧cron保留、摘要/文件/软链接拒绝、时区/root检查和并发覆盖拒绝。未commit、未push、未部署、未安装或启动任务。

## 主任务最终验收补录（2026-09-14）

以上为实施阶段历史状态。本轮主任务最终相关回归 **103 passed、1项warning（3.28秒）**，Ruff/diff通过；首次为102 passed、1 failed，原因是测试替身对缺失文件抛出异常与真实实现不一致，修正后复跑通过，未跳过测试。本轮未跑全量、未commit、未push。

独立包已部署至上述固定目录，root持有的 manifest SHA256为 `a60212b96de7e83b7e34ea29ad27b3304368a53dcf2e9c0ba94e74d555947142`。首包夹带Mac `._`元数据，安装器拒绝且没有安装cron；失败包保留，重新打包仅包含声明文件后通过校验。

主任务以 `period=20260630`、`trade_date=20260911` 手动运行真实八股40请求，全部为observed。上海时间自 `2026-09-14 11:39:46.068308` 至 `11:40:07.619686`；归档 `/var/lib/qagent-research/daily-financial/20260914T034007-b1d9680836b14f78a0d91b08e52a7915.json`，result digest为 `e4cb89ba8f183e69ef6ff786c4f8594b6c2a4322542ee737438c4958924b4cf2`。主任务完成digest核验、原始返回重放以及使用Fraction的独立排序复算，均通过。

本次eligible为8/8，独立Top5依次为 `600519.SH、300750.SZ、603766.SH、603259.SH、688002.SH`；与显式观察顺序前五只重叠3/5。该比较只说明观察集合内的排序变化，不是生产基线对照，也不是收益证据。forecast旧公告与页上限仍限制事件新鲜度及完整覆盖。

新cron已安装为周一至周五08:40 UTC，文件SHA256为 `c9582b6d90d9a70e9be2cd02760312bff8104b37e458fae17820df6e53878bba`，安装收据 `/var/backups/qagent-daily-financial-research/before-install-fwi06squ.json`；原16:30研究cron保留。首次自然触发尚待验，手动运行不能代替它。后端current仍为 `5b72362`、本轮未重启；主任务状态快照为tick attempts/completed 3/3、无error、午休outside状态，不能据此外推分钟新鲜度。

因此本轮为已实现、已完成上述相关测试及真实单次验收、独立研究包已部署且新cron已安装；未commit、未push，不改冻结模型、生产权重、既有账本或账户规则。三个实施子任务完成，清理hooks保持开启，session实际删除尚未核验。G2-FQ1/G2保留自然运行、数据覆盖和同集合成熟收益的未完成验收。

主任务最终只读复核：cron进程PID21，新cron为root拥有、0644；上述归档为luozhenkun拥有、0400，文件SHA256 `c8124a0554bf91a1fb5ddf97d7450c75a030d7ce24015b8055e8671e29725077`。文件SHA与JSON结果digest分别记录，不混同。

## 七接口版本升级实施阶段（2026-09-14）

用户后续批准扩展七接口，并将独立观察集合固定为20股：`600519.SH,603259.SH,300562.SZ,603766.SH,688612.SH,688002.SH,300750.SZ,300308.SZ,600918.SH,301287.SZ,601528.SH,600398.SH,300194.SZ,601198.SH,002602.SZ,001227.SZ,688600.SH,002746.SZ,603444.SH,300871.SZ`。这更新下一版本采集范围，不改写前段八股实测快照。观察集合不是全市场或生产模型排名基线。

采集器确认七接口为新版本默认行为，原 `--symbol` 参数兼容，不需要额外开关或新增依赖文件。升级保留周一至周五16:40北京时间、600秒请求启动预算、`--today-close`、固定报告期20260630和原归档目录。20股最多140个分区；慢请求或预算不足允许以缺失/incomplete留证，不能把600秒误称硬总时限或据接口数宣称覆盖完成。16:30任务的时间与内容不变；开始时间错开不证明异常慢请求时绝无运行重叠。

新增 `scripts/upgrade_daily_financial_research.py`，新包固定 `/opt/qagent-research/daily-financial-20260914-v2`。它核验旧cron SHA `c9582b6d90d9a70e9be2cd02760312bff8104b37e458fae17820df6e53878bba`、旧包manifest SHA `a60212b96de7e83b7e34ea29ad27b3304368a53dcf2e9c0ba94e74d555947142`及双方每个文件摘要；新包manifest须同时包含旧安装器与新升级器，共至少11个文件。复用现有校验器的参数化入口，不修改已部署旧包。

部署者准备并核验新包后使用 `--expected-cron-sha256`、`--expected-old-manifest-sha256`、`--expected-new-manifest-sha256` 查看计划，显式添加 `--execute` 才升级。仅包路径和授权股票名单发生变化；其他cron字节固定。升级器使用原私密目录中的同一锁，将旧cron完整内容保存为0600 `before-v2-*.cron`，写临时root:root 0644文件，发布前再次核验两个包及旧cron内容/inode，然后原子替换。相同目标重复执行为already_installed；旧内容漂移、包变化及软链接等拒绝。安装器不启动任务、读取密钥、重启服务或操作账本。运维工具应共用此锁，非协作管理员在最后检查与原子替换间的并发修改不受此锁协调。

本实施子任务旧安装器加升级器专项 **21 passed（0.21秒）**、Ruff通过，覆盖预览无写、20股命令、备份、幂等、旧任务保留、包和cron漂移、备份后变更及权限前提；diff检查通过。七接口真实数据、独立排序和新包部署仍待主任务验收，本阶段未commit、未push、未部署升级；PROJECT_GOAL暂不追加完成证据。

## 七接口版本最终验收与切换（2026-09-14）

主任务最终专项 **112 passed**，Ruff/diff通过；使用 `backend/.venv` 的最终全量为 **2579 passed、3 warnings，无errors**。中间误用系统Python曾得到2558 passed、7 skipped、3 warnings、7 errors，原因是该环境缺lightgbm；此历史诊断已由正确环境全量通过更新，不再列为依赖缺口。Decimal等值修订的P2问题已修复，新增7个参数化用例，真实数值冲突仍拒绝处理。保留前述实施阶段和八股版本的历史证据。

最终独立包 `/opt/qagent-research/daily-financial-20260914-v2` 已部署，manifest SHA256为 `4549c4af411f1f3cd212d154f5b340a9671cd3e54291a79f204bee91b4326a14`。真实 `trade_date=20260911`、`period=20260630` 的20股×7接口全部140/140 observed，最终报告status为observed，归档 `/var/lib/qagent-research/daily-financial/20260914T041222-bf75194a3fb14a0692b6b7fedbb65ea9.json`。result digest为 `dad7f8b5a2c38e2d25ad5d4a40c489d28536d5cc088c7cff5a3d8e73109e8c28`，文件SHA256为 `10536793a1e4892ad5fdb0fc5778c69f5ad0c3d69f4d6cf70eba2d61884fe08f`；两种摘要分别保留。

排名eligible为16，四只金融股按明确适用范围排除；数据源成功与排名适用性分别记录。首次真实源数据已140/140成功，但旧顶层状态把有意排除误计为incomplete；修正状态语义后形成上述最终报告，未把排除股票强行纳入排名。最终Top5为 `600519.SH、603444.SH、603259.SH、002602.SZ、600398.SH`，仅是这次观察集合的独立研究结果，排名效果和收益增益尚未证明。

主任务确认cron已从旧SHA `c9582b6d90d9a70e9be2cd02760312bff8104b37e458fae17820df6e53878bba` 切换至 `4a80…`；固定升级模板本地重算完整目标SHA为 `4a80db491109badc90190fe9bdd45bf39160118ebe17733a1e934a8cf9b88b9d`。旧cron备份权限0600，重复执行返回already_installed。此次升级没有启动采集任务或重启后端；真实验收来自独立手动采集，首次自然16:40运行仍待观察。

主任务只读核验 `/api/health` 为ok，后端release仍 `5b72362b43b1401b742e1cea710f48e101eb64b1`；模拟盘保持同一session且active，状态字段为128 total、9 active、remaining1。十分钟更新outside_session、attempts/completed为3/3、last_error为null。这是状态快照，不是新一次账本全量哈希对账，也不证明分钟行情新鲜度；本轮研究和调度升级不修改模拟盘、账本或交易规则。

本轮已实现、专项及正确环境全量测试通过、上述真实采集验收通过、独立包和cron已升级；commit/push状态未在本次验收中新增确认。固定报告期20260630后续须显式滚动，旧预告与分页限制继续保留；G2-FQ1/G2的自然运行、覆盖、同集合前向成熟收益验收仍未完成。
