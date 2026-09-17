# 晚扫描与当日Financial链路

G2-FQ2本轮只补调度依赖。09-16两次扫描完成时间分别为北京时间20:35:14、21:39:07；后一次确认fresh，旧daily19:10/forward20:37截止，因此无当日财务信号。原历史保持不变，不补封09-16信号。

新daily轮询16:40–22:40每30分钟一次，最多13次本地候选池GET；fresh且集合合格后最多两批财务API，每批仍600秒预算、最多20股×7接口。`financial_batch_started`在请求前、原daily锁内原子发布并fsync，崩溃消耗预算。成功daily按原校验幂等；成功或already_completed用shell `&&`立即调用原forward，失败与锁冲突不触发。旧独立forward三个时间保留，加23:07恢复，因此没有新daily也可评估旧到期信号。按600秒采集与300秒forward预算安排保留同日余量；异常阻塞或跨日仍由原封存日期校验拒绝，不保证任务硬性同日结束。

轻量记录分区至`schedule-attempts/YYYYMMDD`；日期必须规范八位，软链拒绝，读取启动记录复核digest。旧根目录审计保留。two-batch限制默认不影响显式股票和既有CLI，只有新cron opt-in；时间必须当日工作日窗口，禁止显式旧交易日和超600秒预算。

打包（仓库根目录，输出目录须为新临时目录；不读取凭据）：

```sh
package_dir=$(mktemp -d /tmp/qagent-late-scan-package.XXXXXX)
PYTHONPATH=scripts backend/.venv/bin/python -B -c 'import json,sys; from upgrade_financial_late_scan import package_bundles; print(json.dumps(package_bundles(sys.argv[1]), indent=2))' "$package_dir"
```

返回两个tar及manifest/tar SHA。文件按精确allowlist打包、固定排序和tar元数据、root:root 0444；禁止覆盖已有tar。云端以root创建两个新bundle目录并各自解压对应tar；不要复制到当前旧bundle。分别从两个新bundle、非仓库cwd用`python -B`预览，可确认导入闭包不依赖checkout。使用打包输出的实际SHA替换下列参数：

```sh
python3 -B /opt/qagent-research/daily-financial-20260917-v6/scripts/upgrade_financial_late_scan.py --expected-daily-new-manifest-sha256 DAILY_SHA --expected-forward-new-manifest-sha256 FORWARD_SHA
```

预览为`planned`且确认旧cron不变后，同一命令加`--execute`安装；不启动任务，不重启后端。再次preview应为`already_installed`。回滚使用同一命令加`--rollback-receipt RECEIPT --execute`，只接受工具产生的私有receipt；先回daily再回forward，中断可恢复。receipt schema沿用原engine，备份目录独立为`/var/backups/qagent-financial-late-scan`。

安装后验收须分别记录cron摘要、自然候选fresh、首次daily observed、同日forward v2封存及五对control语义。测试、打包和安装均不替代自然信号或成熟5/10/20交易日结果；不晋级、不开新账户、不改交易权重。
