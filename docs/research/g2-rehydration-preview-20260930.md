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

## 2026-09-30 收盘后恢复阶段验收

以下记录更新上文“capture env、cron 缺失”的准备期快照；生产 current 始终为 `f6725c4`，暂存 a18 release 未激活。主任务 **07:32 UTC** 先确认 paper update **78/78**、无 skipped slot，停止主 scheduler 并核验 idle。在线备份 `qagent-20260930T154037+0800.db` 经备份脚本 `quick_check`，配置调整前后的 ledger manifest 字节相等。仅给 `config/qagent.env` 追加 `QAGENT_G2_CAPTURE_DIR=/home/luozhenkun/qagent/research-data/g2-forward-sources`；backend 重启至 PID **926109**，直接读取进程环境确认该变量，API health 正常。随后以原 **1800 秒**间隔恢复主 scheduler。此处的对账覆盖本次受控配置调整，不是 G9 镜像重启恢复验收。

独立 G2 cron 已从模板安装为 root-owned `/etc/cron.d/qagent-g2-forward`；持久副本 `config/qagent-g2-forward.cron` 的 SHA256 是 `7cf3d019c86998a508c9d3bc8e702ccb95639396fa8e8168b7427615327dccd0`，两份内容固定指向 `f6725c4` release 路径。暂存 a18 脚本运行的静态 preview 报告全部检查为 true、状态 `ready_for_opt_in_review`，仅证明其静态检查通过。**09:00 与 09:30 UTC** 的自然 cron 各退出 0、均为 `skipped_unscheduled_day`：已证实自然触发，但非采样日跳过不产生 G2 信号。

约 **09:33 UTC**，最新自然全量扫描 `full-scan-20260930083633-e14d97e2` 为 `running`、**4200/7219**、errors **0**，source 文件数仍为 **0**。扫描终态、完整自然 source、采样日 collector 归档、信号覆盖及成熟收益均待验；不能由 preview ready、cron exit 0 或扫描进度推出 G2 完成。Financial daily-v11/forward-v13 仍仅暂存，独立审批 marker 与 cron 未安装；G9 平台启动 hook、受控镜像重启和前后账本、备份、marker 对账也未完成。唯一 paper 账本、交易规则、正式 Ranking 与实盘禁令保持不变。此阶段只补录文档，未改代码、未 commit、未 push，也未切换生产 release。

## 2026-09-30 10:34 UTC 后续诊断：扫描完成但 source 捕获失败

上文 09:33 UTC 的运行中状态保留为当时快照。同一自然扫描 `full-scan-20260930083633-e14d97e2` 于 **10:07:17 UTC** `succeeded`，标的 **7219/7219**、批次 **37/37**、errors **0**；随后核验 G2 source 目录仍为 **0**。backend 日志为 `G2 research source capture failed`。只读复现显示全新 DB 的 `historical_data_revisions` 和各 `historical_*` 表均为 **0 行**，而 v1 捕获要求 historical revision，触发 `ValueError`。因此扫描终态已验，捕获尚未验；静态 preview 的 `ready_for_opt_in_review` 没有覆盖这条运行时依赖。

本地研究代码已准备 `g2-forward-source-v2` 兼容修复：无历史 revision 的鲜库归档如实记录 `revision=null` 和行业证据覆盖 **0**，沿用既有 benchmark fallback，保留 v1 读取兼容。没有历史行业证据的 v2 输出与历史行业完整的 v1 输出在 benchmark/行业标签口径上不能直接比较。聚焦 **54 项测试通过**，Ruff 与 diff 检查通过；这只证明本地修复的相关验证。修复尚未 commit、push、部署，当前生产 `f6725c4` 仍是旧捕获路径；没有新自然 source、采样日 signal 或选股增益证明。后续需 review、受控发布，再按 source→collector/signal→成熟结果的顺序取得自然证据，不追认本轮失败捕获。

Financial 独立 root 审批 marker 在 **09:37 UTC** 已创建为 root:root `0600`，更新上文“marker 未安装”的历史状态；active Financial cron 仍不存在，daily-v11/forward-v13 的暂存状态也未变，不能宣称 Financial 已运行。G9 的平台启动 hook、受控镜像重启及前后账本、备份、marker 恢复对账仍未完成。唯一 paper 账本、交易规则、正式 Ranking 与实盘禁令不变；本次仅追加文档，未 commit、push 或部署。

## 2026-09-30 12:11 UTC 受控发布与待验自然捕获

鲜库兼容修复的精确提交 `c8659cf4357eb3d0daa7bc7ea4b09ec1decb9967` 已 push 至 origin；发布前 backend 全量 **2919 passed、3 项 warnings**，更新上文“尚未 commit/push”的阶段状态。云端 HTTPS clone 超时后，从该提交的 Git archive 传输 release，所选源码文件的 SHA256 与本地一致。离线 `uv sync` 安装 **70 包**、离线 `npm ci/build` 和隔离启动通过。上述验证证明包准备完成，不代表自然扫描捕获成功。

**12:05 UTC**，主 scheduler 已停止并确认 idle、SQLite preflight 通过；新备份为 `/home/luozhenkun/qagent/backups/qagent-20260930T195721+0800.db`。`/home/luozhenkun/qagent/current` 从 `f6725c4` 切换为 `c8659cf`。安装、服务启用和 scheduler 恢复后的 ledger manifest 与切换前逐字节相等；仍是唯一会话 `paper-session-50fa0927861b`，含 **16 笔交易、626 条事件**。backend/frontend runit 正常、health 和前端均 HTTP **200**，备份 cron active；主 scheduler 以 **1800 秒**间隔恢复，独立 paper tick 已恢复。backend PID **967277** 的进程环境已直接核验 G2 capture env。

root-owned 独立 G2 cron 的持久 pin 已改指 `c8659cf`，SHA256 为 `a71ebed51f51a745432de0c40337d03d142070d65cdc67b2d30803f703cfb817`。root 启动审批重新建立为 root:root `0600`；此前的 Financial 专用 marker 可逆移到 `pending-g2-source-20260930`，active Financial cron 保持关闭，直到真实 G2 source 可验。此操作不表示 Financial daily/forward 已自然运行。

**12:11 UTC** 最近扫描仍为 **10:38 UTC** 的成功扫描（**7219/7219**），G2 source 文件仍为 **0**。这是旧 release 下的扫描记录；新 release 尚无完成的自然扫描及 v2 source。下一验收依次为新 release 自然全量扫描及完整 v2 source、采样日 collector/signal、随后真实成熟结果；不能回填旧失败扫描或用服务健康推断选股改善。G2 保持未完成，Financial 仍关闭；G9 平台启动 hook、受控镜像重启及重启前后恢复对账仍未验证。唯一模拟账户、账本规则、正式 Ranking 和实盘权限不变。本段文档改动未 commit、push 或部署。
