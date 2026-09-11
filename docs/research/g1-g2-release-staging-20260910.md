# G1/G2 发布暂存记录（2026-09-10）

以下是主任务当次云端核验结果；**暂存成功不代表生产切换、调度安装或连续观察完成**。历史验证及固定验收口径继续见 PROJECT_GOAL 和 G1/G2 原研究记录。

- 实现提交 `979af5b`、文档提交 `94cf6f5057723460a88becd0c5e44f864a6cc53c` 已 push。
- 云端 `/opt/qagent/releases/94cf6f5057723460a88becd0c5e44f864a6cc53c` 已完成依赖冻结安装、`npm ci`、构建及隔离启动检查。
- 六个冻结模型已分发至 `/var/lib/qagent-research/g2-frozen-v1`，摘要核验及实际六模型加载通过；不表示已有真实前向样本。
- 暂存 release 的 G1 只读归档调用退出 0，产物为 `/var/lib/qagent-research/execution-observations/20260910T084751.682374Z-0c31bb8cbf864dd6b5ce12ba8c79d8d1.json`。主任务确认仍为 14 条事件，V2 buy `5/5`、sell `5/3`、unknown `0`，observation digest 为 `47ea95926e6de582aba57326cd49fea2d91fe07c1b349dfcf08d712686e7e61a`；重复归档不增加独立样本，不表示连续运行完成。
- 08:48 UTC 手动调用暂存 G2 runner，退出 0，状态 `skipped_unscheduled_day`（9 月 10 日），attempts `0`、errors `0`。这是非采样日跳过验证，不是前向信号；真实前向样本仍为 0。

2026-09-10 08:46:49 UTC，生产仍运行 `f1dd6d2`，自然全量扫描 `full-scan-20260910082339-10a02904` 为 running、进度 5/36。未停止扫描、未切换生产 release、未启用 backend 源捕获环境、未安装 G1/G2 cron。

主任务已准备临时 rollout helper `/tmp/qagent-guarded-rollout-g1g2.py`，目标 release 固定为上述 `94cf6f5` 完整提交。此临时文件不是已安装的自动任务；接续时须检查它仍存在且目标一致，并重新核验任务空闲后才可执行。不能凭本记录中的旧扫描进度直接切换。

当时待完成：自然扫描结束且空闲检查通过后切换 release、在既有 backend 进程环境启用 `QAGENT_G2_CAPTURE_DIR`、安装独立 G1/G2 cron，并复核生产健康、唯一账本及 settings 保留、实际归档和调度状态。G2 首个采样日为 2026-09-11，真实前向信号仍为 0；不能提前制造未来日期样本。所有切换结果由主任务另行记录，未执行事项不得标为已部署。

## 独立研究 cron 安装及验证（2026-09-10 09:00 UTC）

主任务后续核验：08:58 UTC 上述自然扫描仍为 running、进度 9/36，因此生产仍保持 `f1dd6d2`，未停止扫描或切换服务。以下仅安装独立研究周期入口，固定调用已验证的 `94cf6f5057723460a88becd0c5e44f864a6cc53c` release。

- 已核验主机 `/etc/timezone` 为 `Etc/UTC`，`/etc/localtime` 指向 UTC，cron 进程 PID 为 21。安装前两个目标均不存在；按已有模板渲染，确认无占位符、有末尾换行，采用原子且拒绝覆盖的方式安装 `/etc/cron.d/qagent-observation` 和 `/etc/cron.d/qagent-g2-forward`，均为 root:root、0644。
- G1 调度为工作日 08:10 UTC；G2 调度为工作日 08:00–15:30 UTC，每半小时一次。cron 文件 SHA-256 分别为 `27d8d94689cad178d05bf6e2e05decbbf23ed4001948a32c47ee5e40447cfadd` 与 `32eb4b92042ee48d0ba6e1ede4bf9f0c71e0b5eb495bbfb941f660ff8bc0cfe9`。
- 使用 `luozhenkun` 服务用户、最小环境分别手动验证两条研究命令，均退出 0。G1 产物为 `/var/lib/qagent-research/execution-observations/20260910T090047.539074Z-4fab91513c074fefa0f40935566230b8.json`，仍为 14 条源事件、14 个样本；source digest 为 `4add6765972ff67e0a21b4cf9940e793103003e939900a6499f9f53cae94ca70`，observation digest 为 `47ea95926e6de582aba57326cd49fea2d91fe07c1b349dfcf08d712686e7e61a`。重复旧样本不计为新增证据。
- G2 产物为 `/var/lib/qagent-research/g2-forward-results/runs/20260910T090052.443397Z-a2fac93530834ec093e2bae6b0af77a6.json`，状态为 `skipped_unscheduled_day`，attempts/errors 均为空、`activation_allowed=false`、`decision_weight=false`。这仅证明非采样日手动调用正常，前向样本仍为 0。
- 主任务独立复核安装前后 8 张账本表的行数和哈希、settings 哈希、生产 env 哈希及 current release 完全一致；backend PID 24469、frontend PID 24284 未变。未重启服务或 cron，未改生产环境或交易状态。

当前已部署的是独立研究 cron；尚无 cron 自然触发证据，持续观察尚未完成。G2 源捕获环境仍未启用，须待自然扫描结束并通过空闲核验后，由主任务完成生产 release 切换及 `QAGENT_G2_CAPTURE_DIR` 启用，再核验真实源捕获、当日调度产物和前向覆盖。原有验收条件、唯一模拟账本及禁止自动接管边界保持不变。

## 本地完整回归及容量相关测试修正（2026-09-10）

主任务首次完整运行 `backend/tests` 得到 2050 passed、1 failed。失败项为 `test_backup_is_consistent_and_retention_argument_is_validated`：本地可用空间为 `7,110,762,496` 字节（约 7.1 GB / 6.6 GiB），小型测试数据库为 8192 字节，低于备份后默认保留 10 GiB 的要求。复现 stderr 为 `insufficient backup space: available_bytes=7110762496 estimated_backup_bytes=8192 minimum_free_after_backup_bytes=10737418240 required_bytes=10737426432`。修正前备份脚本与该测试相对已部署 `f1dd6d2` 均无差异，确认是本地容量触发既有保护，不是 G1/G2 回归。

仅在该一致性测试调用中显式传入第四参数 `0`，使微型 fixture 备份不依赖宿主机保留 10 GiB 空间；生产备份脚本、默认 10 GiB 容量保护及独立容量拒绝测试均未改变。子任务与主任务分别运行部署资产测试文件，均为 16 passed；主任务随后完整重跑 `backend/tests`，结果为 **2051 passed、3 项既有 warnings，312.57 秒**。这些是本地测试证据，不代表云端 release 切换或源捕获启用已经完成。

## 生产切换成功及剩余验收（2026-09-10 11:56–12:03 UTC）

以下为主任务后续独立核验结果，更新上述暂存阶段的未部署状态；保留前文作为各时刻历史证据。

- 自然扫描 `full-scan-20260910104100-63a4ef30` 于 11:55:57 UTC `succeeded`、36/36；11:56:12 UTC 核验 idle 后，guarded helper 退出 0，生产 current 已切换至 `94cf6f5057723460a88becd0c5e44f864a6cc53c`。切换证据保存在 `/var/tmp/qagent-rollout-g1g2-f11js7yb`。
- 主任务比较 ledger-before/after，8 张账本表完全一致、16 项 settings 完全保留；scheduler 为 `enabled=true`、`in_flight=false`、`last_error=null`，前后端运行且 health 为 ok。
- 生产 env 字节核验确认仅追加 `QAGENT_G2_CAPTURE_DIR=/var/lib/qagent-research/g2-forward-sources`，run 脚本确实 source 该 env。进程 environ 读取受权限限制，未直接核验进程内环境；源目录仍空，首次自然全量 source 捕获尚待验收，G2 真实前向样本仍为 0。
- G2 已有 09:30、10:00、10:30、11:00 UTC 四份按期运行产物，均为 `skipped_unscheduled_day`、退出 0。这证明周期入口已运行，不代表产生真实前向样本。
- 12:03:39 UTC 新生产 G1 手动归档退出 0，产物为 `/var/lib/qagent-research/execution-observations/20260910T120339.582516Z-273021fa82f54c5aaf5defe0803d8409.json`，仍为 14 个样本，observation digest 仍为 `47ea95926e6de582aba57326cd49fea2d91fe07c1b349dfcf08d712686e7e61a`。重复样本不增加独立证据，G1 自然 cron 触发及持续覆盖仍待验。

本阶段实现与 `94cf6f5` 已 push 并部署；本地全量测试为 2051 passed，测试 fixture 的单行容量参数修正不改变生产备份默认 10 GiB 保护。剩余工作为 G1 自然触发、G2 首份真实源捕获与采样日前向产物验证；唯一模拟账本、研究隔离和禁止自动接管边界保持不变。

## 采样日午间核验（2026-09-11 12:55 北京时间）

以下为主任务在 04:52 UTC（北京时间 12:52）取得的当次云端证据及随后源码核对，不代表全天持续健康。

- health 为 `ok`，生产 current 仍为 `94cf6f5`。latest scan 仍是 9 月 10 日 11:55:57 UTC 完成的部署前扫描；04:15 UTC 调度已完成，返回 `cache_fresh`、`expected_signal_date=2026-09-10`，尚无部署后新扫描。后续只读查询 `automation_cycles` 显示 04:45:40 UTC 周期为 `running`，`factor_shadow` 阶段自 04:46:48 UTC 为 `running`；scheduler checkpoint 仍显示上轮完成态，不能据此认定当前空闲。
- G2 源目录仍空。源码仅在分批全市场扫描完成各批次、进入最终排名阶段时调用 `capture_if_enabled`（`backend/qagent/jobs/full_market.py`），启用捕获不会追溯生成既有扫描的源文件。午间仍以最近已完成交易日 9 月 10 日判断缓存新鲜度，与此次尚未产生新源的状态一致；这不构成源捕获故障的证据，也尚未验证运行进程能成功写出源文件。
- G1 自然 cron 计划为工作日 08:10 UTC（北京时间 16:10）；G2 为工作日 08:00–15:30 UTC（北京时间 16:00–23:30）每半小时一次。当次核验早于当天首轮研究 cron。G2 collector 只接受当日源且要求捕获时间不早于北京时间 15:30；collector 本身不启动扫描，首轮可能仍为 `waiting_for_source`。
- 待验事项仍为 G1 自然归档、首份自然全量 source、9 月 11 日采样日真实 collector 产物及覆盖。需待收盘后自然扫描进入最终排名阶段，再核对源文件日期、捕获时间及 collector 状态；首份源与真实前向样本尚未验收。
- 本轮主任务本地运行 `test_g2_risk_feature_forward.py` 和 `test_g2_forward_schedule.py`，18 tests passed（2.64 秒）。所检查云端日志尾部存在数据源连接超时，未见 G2 捕获报错；此结论仅限已读尾部，不能证明完整日志无错或源捕获已成功。

本轮仅只读核验和追加记录，未启动扫描、重启或切换服务；未改变研究隔离、模拟账户或交易权限。本小节为本地文档更新，尚未提交或 push，不表示新增部署。
