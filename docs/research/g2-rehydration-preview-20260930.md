# G2 研究链路镜像恢复预检（2026-09-30）

镜像重建后的 G2 研究恢复复用 `g2_forward_source`、`g2_forward_schedule` 与既有冻结模型。新增的 `scripts/preview_g2_research_restore.py` 只读核对准备状态：本地六模型逐文件 SHA256、归档与固定 manifest 摘要；目标 `/home/luozhenkun/qagent/research-data/g2-frozen-v1` 的六模型；持久 source/output 目录、当前 release 的 collector 模块、后端环境中的 `QAGENT_G2_CAPTURE_DIR` 和独立 collector cron 的路径一致性。它不会复制文件、创建目录、安装 cron、启动任务、打开数据库或改变模拟盘。

在可访问目标持久盘与 `/etc/cron.d` 的机器上运行：

```sh
python3 scripts/preview_g2_research_restore.py \
  --target-home /home/luozhenkun/qagent \
  --cron-file /etc/cron.d/qagent-g2-forward
```

默认源为本机被 Git 忽略的 `data/archives/g2-risk-feature-ablation-20260910-frozen-v1`，基准为 `docs/research/g2-risk-feature-freeze-20260910-manifest.json`。若在其他机器运行，须显式传入 `--source-dir` 和 `--canonical-manifest`；工具仍会要求 manifest 内容匹配 collector 固定摘要 `b6bbb9…`。标准输出为 JSON，不回显 env 文件的其他配置。`cron_file_present` 单列文件存在性，`scheduler_activation_verified=false` 表示静态预检未核验实际调度运行。退出 2 表示本地模型无效，1 表示目标仍有缺口，0 仅表示这些静态路径已就绪，状态为 `ready_for_opt_in_review`。

建议在任何显式启用前，核对目标目录归属和权限、目标 release 的 Python 依赖、主扫描实际进程是否读取 capture env、cron 的 UTC 时区与服务用户，以及研究包是否位于持久盘。`source_capture_files` 是可见文件数，不能证明捕获持续正常。目标路径和 cron 就绪也不证明后端进程已经重新加载 env、自然采样日已产生完整 source、collector 已运行、六模型在目标 runtime 成功推理，或 20 交易日标签已经成熟。真正的 G2 验收继续使用既有 source/signal/runs 归档及 `scripts/evaluate_g2_forward.py`，不追认历史日期，不增加正式 Ranking 权重。若继续恢复 Financial 研究链路，其每日 baseline 原消费者已指向相同的持久 G2 source/model 路径；此工具不安装或启动 Financial 调度。

预检工具初始本地阶段仅实现只读预检及测试；当时未传输模型、未配置云端、未 commit、未 push、未部署或启用 cron。

## 2026-09-30 云端准备状态补录

主任务已将六份冻结模型恢复到持久 `/home` XFS 上的 `/home/luozhenkun/qagent/research-data/g2-frozen-v1`。模型文件逐一匹配固定 manifest 的六个 SHA256；目标 `manifest.json` 文件 SHA256 为 `ac5f88ca79dd3509efcfcdb9167a975f3e07725d8380ecaa03e0e8a2c5692188`。云端当前 release `f6725c4` 的 Python 已实际加载 `full_features` 三个模型和 `without_risk` 三个模型。这证明模型字节、manifest 与该 runtime 的加载检查通过，尚不证明自然前向采集或收益。

主任务另建空的持久 `research-data/g2-forward-sources` 与 `research-data/g2-forward-results` 目录。一次性云端只读 preview 返回 `rehydration_incomplete`：目标模型已验证，当前 release 的 collector 代码、source/output 目录均存在；`QAGENT_G2_CAPTURE_DIR` 尚未匹配持久 source 路径，独立 G2 cron 尚不存在，source JSON 文件数为 0。preview 未执行调度启用动作，本次恢复尚无新自然前向样本。本次准备没有修改 paper、数据库、后端配置或 cron；没有启动 collector。G2 与 G9 均不标记完成，后续仍需显式配置与独立自然运行验收。本次文档补录未 commit、未 push、未部署。

Financial 研究包还有一条启动边界：旧版 bootstrap 在持久 `research/daily-financial-20260924-v11` 和 `research/financial-forward-20260924-v13` **同时存在**且服务已审批时会启用 peer-control cron。a18 源码及已安装的 root 启动包在两包存在时仅渲染 disabled cron；`scripts/bootstrap_linux_persistent_home.sh` 和 `scripts/enable_linux_runit.sh` 启用它还须单独的 root Financial 审批 marker `/home/qagent-financial-research-approved`，不能仅凭 paper 服务审批。因此两个 bundle 已暂存不等于 Financial 已启用。当前云端两包的 manifest SHA256 分别为 `6cc90710087e73f6420f08b6d373551292ee74242da5f02a5d0696429b5a1681` 和 `3e3964e85039a10a62274943831254187db8368d6fc72a82fe78c19e42ab7282`，runtime import 检查通过；Financial 审批 marker 与 cron 均不存在，bootstrap/enabler 未执行。root 启动门禁 a18 已安装、候选 a18 不可变 release 已暂存但未激活，生产仍为 `f6725c4`。两次北京时间15:00前的误触手动 wrapper 检查只写出失败的 attempt JSON，均因 `session_not_closed` 在 API 请求前退出，未改变 paper。G2 collector 的 capture env、cron 与新 source 仍缺失；没有新自然信号或选股增益证据，G2/G9 均未完成。
