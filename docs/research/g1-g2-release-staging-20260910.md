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
