# Financial peer-control v5 研究包发布准备（2026-09-24）

## 范围与当前状态

本次只升级现有 Financial daily/forward 两个独立研究包：daily-v10 → v11，forward-v12 → v13。v11 的版本化入口显式传 `--peer-controls`；采集 CLI 默认仍关闭该功能，且要求同时启用 `--daily-frozen-industry --bounded-same-day`。v13 消费 v5 证据并保留旧 v1–v4 归档重放。原20股、排名和Top5不变；最多10只额外同行只作研究 control，不进入候选池、模拟盘或正式 Ranking。原13个 daily、4个 forward 时槽和每天最多两批财务请求不变；同批 600 秒 deadline、新增请求上限和失败封闭语义见[研究范围](financial-control-feasibility-20260924.md)。

当前为**已实现、已完成本地测试，未提交、未 push、未部署、未启用云端 v5**。主任务最终 backend 全量 `2878 passed`、3 项既有 warnings、400.16 秒、exit 0；最终新包装器/升级器专项 7 passed（4.02 秒），其中两个 manifest-only 隔离包测试 3 passed。隔离测试仅按 manifest 解包后，在仓库外导入 v11/v13 入口并执行帮助命令。Ruff 和 diff 检查通过。本地打包成功不代表云端包安装或自然封存成功。

## 固定升级基线

2026-09-24 03:42 UTC 云端只读核验：backend current 为 `83cdcb528a226538750b41adb6a2fd7cecac8af8`、health 正常；daily/forward 分别为13/4个 cron 时槽。受控升级器 `scripts/upgrade_financial_peer_control.py` 仅接受以下旧摘要：

| 对象 | 已安装旧 SHA256 | 目标 |
| --- | --- | --- |
| daily cron | `ba04633a4d19eac7255ef5b7af83a387a1d8ad424aef40d0651de96bfc01eb91` | v11 wrapper；本地模板 `cb22a5396452b17fd860ba2d11177afd3d0fc4e056a26b0b368c797782bb3e17` |
| forward cron | `aad4e58bdbf27f4d914c713ab1908432d358523b479533bdfe458f1a62e036e2` | v13；本地模板 `706b15213bd395046faa0de434d9b168fff1dc5bdb957fe5965ed55af0fb5fb5` |
| daily-v10 manifest | `08b2d4ede0d46d26586e21001c904ed95b20762ff9d3cfe0bf5abf38a1def1f8` | `/opt/qagent-research/daily-financial-20260924-v11` |
| forward-v12 manifest | `e21a920aa9bfe47213568b346a1aeb26f1cdf31a820bd66481c06b77f84d35aa` | `/opt/qagent-research/financial-forward-20260924-v13` |

新包 manifest 必须以最终提交的精确源码重新打包并记录；本地中途打包的摘要不是发布固定值。两个包都包含升级器、v5 wrapper、`financial_peer_evidence.py` 及各自完整依赖闭包。旧包和旧归档保持原样。

## 发布与验收顺序

1. 从最终提交的源码运行 `upgrade_financial_peer_control.package_bundles(destination)`，记录两个 tar 与 manifest SHA256；核对 tar 只含 manifest 允许文件，并在仓库外以隔离 Python 导入 wrapper、collector、evaluator、升级器。将 tar 复制到已核验的云主机，分别解包到上述尚不存在的 v11/v13 目录；文件和目录权限须满足现有安装器校验。不要在不可变包内生成 `__pycache__`，调用时使用 `python -B`。
2. 重新只读核验旧 cron/manifest SHA、当前无正在运行的 Financial daily/forward 任务、旧信号文件 SHA，以及唯一模拟账户和服务健康。若旧摘要不符，停止并调查差异，不重写旧包或旧归档。
3. 在 v11 或 v13 包内运行同一个升级器预览，指定 `--expected-daily-new-manifest-sha256` 与 `--expected-forward-new-manifest-sha256`。预览应为 `planned`；旧 cron 保持原 SHA。随后显式加 `--execute`，按 consumer-first 顺序先升级 forward、再升级 daily；结果须为升级成功且 `started_job=false`。重复预览应为 `already_installed`。安装器私有备份收据位于 `/var/backups/qagent-financial-peer-control`，其实际路径与权限在发布时记录。
4. 核对新 cron SHA、13/4 时槽、v11 命令指向版本化 wrapper、v13 指向同版 evaluator；健康、单一模拟账户、账本和 settings 不变。记录旧信号 SHA 前后相等。只把安装成功记为启用研究调度，等待下一次自然 daily/forward 分别验证同日 raw evidence、v5 seal、control 可判别性及5/10/20日真实成熟收益；退出0、接口成功或部署不能代替收益验收。

升级器默认预览，不自动启动任务。目标旧摘要、目录或 cron 状态不符时应安全拒绝；中断后的兼容混合态可重试。`--rollback-receipt` 使用安装时的私有收据，先恢复 daily、再恢复 forward。**仅在首份 v5 信号封存前允许回滚旧 forward-v12**；v12 不识别 v5，封存后若需停止新增采集，应停用 v11 daily 调度但保留 v13 到期消费者，另行制定兼容处理，不改写已封存信号或历史账本。
