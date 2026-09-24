# Financial v4 受控研究包升级

2026-09-24 本地验收记录。复用原 Financial 唯一 daily/forward 链路，将已提交的全局同行业分配协议接入新前向信号；v1–v3 继续按原协议重放，旧信号不重写。发现 evaluator 的协议分支漏列 v4，会把 v4 当作旧 G2 baseline 收益比较；已将 v4 纳入 matched-control 评估，并覆盖成熟五日、未成熟窗口及不可配对场景。行业单例仍不可配对，不扩池、不合并行业、不放宽至少两只非 Financial Top5 control 的门槛。

主任务本轮实时检查：09-22、09-23 两份自然 v3 信号 baseline available、control unavailable、0 pairs。仍只有两份，不满足连续五份停用阈值；升级不重置原连续失败观察或停用条件，须跨 v3/v4 连续核验。部署成功不代表取得可判别配对或收益增益。

新升级适配器 `scripts/upgrade_financial_global_control.py` 复用已有锁、私有 receipt、consumer-first、幂等、中断恢复及 producer-first rollback 状态机。固定源为 daily-20260922-v9 / forward-20260922-v11，目标为 daily-20260924-v10 / forward-20260924-v12。保留13/4个时槽、600/300秒预算、同日最多两次财务批次、原候选池和规则。新增短 wrapper 只将两个 bundle 路径固定到新版本；旧 wrapper 默认路径保留。两包均包含完整严格依赖闭包。

本地代码回归63项通过（12.06秒），升级适配器及两个独立包的 preview/execute/幂等/中断恢复/rollback 共6项通过（2.38秒）；补强 singleton 到期评估断言后最终联合 **69 passed（14.35秒）**，Ruff及diff检查通过。没有运行 cloud 写操作、没有启动采集、没有更改模拟账户、历史账本、正式 Ranking、交易规则或冻结模型。当前已实现并测试，未提交、未push、未部署。

打包（使用新建空目录，打包器拒绝覆盖已有 tar）：

```sh
PYTHONPATH=scripts backend/.venv/bin/python -B -c 'import json, tempfile; import upgrade_financial_global_control as u; print(json.dumps(u.package_bundles(tempfile.mkdtemp(prefix="qagent-financial-v4-")), indent=2))'
```

按输出记录两 tar 和 manifest SHA256；将 tar 复制至已验证云主机后，分别解压到尚不存在的精确目标目录，保留 root 属主与0444文件权限。不要覆盖已安装旧包，不使用会在包内生成 pycache 的 Python 启动方式。先核验旧cron/manifest与helper常量一致、当前无旧daily/forward任务运行，再预览：

```sh
python3 -B /opt/qagent-research/daily-financial-20260924-v10/scripts/upgrade_financial_global_control.py --expected-daily-new-manifest-sha256 DAILY_NEW_SHA --expected-forward-new-manifest-sha256 FORWARD_NEW_SHA
```

预览必须为 planned/old、started_job=false；以相同参数追加 `--execute` 进行授权升级，保存输出 receipt；再次预览须为 already_installed/new。需要恢复时以相同参数追加 `--rollback-receipt RECEIPT_PATH` 预览，再追加 `--execute`。helper只更改两个研究cron，不启动任务、不重启后端。恢复旧包仍保留已生成 v4 信号，因此回滚后的历史评估能力必须另行核验，不把cron回滚等同所有产物兼容。

安装后核验两cron、bundle摘要、权限和健康，旧信号SHA保持原值；后续等待自然新v4 seal与matched-control评估证据，真实5/10/20收益仍需成熟和合格价格。
