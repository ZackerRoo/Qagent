# Financial peer-control 恢复审批门禁

`bootstrap_linux_persistent_home.sh` 可以在持久目录中发现 `daily-financial-20260924-v11` 与 `financial-forward-20260924-v13`，但只暂存 Financial cron 模板。仅有这两个目录或仅有 paper 的 `/home/qagent-boot-approved` 与 `.single-writer-approved`，均不会启用 `/etc/cron.d/qagent-financial-peer-control`。

Financial 研究需另有 `/home/qagent-financial-research-approved`。该文件必须是普通文件、非符号链接、root:root、权限 `0600`，内容须精确为 `financial-peer-control-v11-v13:<QAGENT_HOME>` 加一个结尾换行。`/home` 本身也须由 root 持有，且不可被 group/other 写入。只有 paper 恢复条件成立、两份固定 bundle 目录均存在且不是符号链接、Financial 审批有效时，bootstrap 才恢复 Financial cron。显式启用 paper 的 `enable_linux_runit.sh` 同样检查这份独立审批和两份目录；它不会替 Financial 创建审批。

在确认研究链路可恢复后，root 操作者可显式建立标记。例如默认持久根目录：

```sh
printf 'financial-peer-control-v11-v13:/home/luozhenkun/qagent\n' | \
  sudo install -m 0600 -o root -g root /dev/stdin /home/qagent-financial-research-approved
```

撤销时由 root 将该标记移出有效路径，再运行 bootstrap 或显式 enable；若 Financial cron 已处于 active，脚本会将其移回 `.disabled`。标记缺失、属主/组/权限不符、内容不匹配或符号链接均按未批准处理。paper 服务与备份 cron 继续使用原有审批规则，不因 Financial 审批缺失而停止。此门禁不运行研究任务，不改唯一模拟账本、正式 Ranking 或交易规则；本地代码和测试不构成云端安装或重启恢复验收。
