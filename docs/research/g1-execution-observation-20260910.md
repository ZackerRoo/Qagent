# G1 执行 V2 只读观察产物

日期：2026-09-10。本文记录新增入口、本地验证及主任务完成的云端单次执行，不代表已 push、正式部署或已接入云端周期调度。

## 现有能力与缺口

真实执行证据已保存在 `paper_trade_events.note`；`PaperTradingRepository.list_replay_evidence_audit()` 使用 `_replay_evidence_audit_records()` 解析 V1/V2、审计失败状态和同阶段冲突，并按 evidence digest 去重。`build_execution_replay_readiness()` 调用 `replay_paper_evidence()`，保留既有 V2 买入 5、卖出 3 的精确重放门槛。V1 独立统计，不挤占 V2 分母或阻断已达标的 V2。

已有 `execution/paper_replay_sqlite.py` CLI 按 trade 限量读取 facts 并输出汇总，不能替代全量 V2 event 的连续观察。新增 `execution/paper_observation.py` 只复用已有严格只读 SQLite 连接、纯审计解析、真实重放和 readiness 计算；不会调用 `execution/shadow.py` 的 synthetic 对照。

## 调用与产物契约

在仓库根目录、安装了 backend 依赖的 Python 环境执行：

```sh
PYTHONPATH=backend backend/.venv/bin/python -m qagent.execution.paper_observation --db data/qagent.db
```

云端使用实际仓库根目录及其 `.venv` 路径替换命令中的路径。数据库必须已存在、有 SQLite 文件头及 `paper_trades`、`paper_trade_events`、`market_bar_cache` 三张表；这是复用现有只读 reader 的严格前置条件，观察本身仅查询事件表。缺库、非 SQLite 或缺表直接失败，绝不初始化。连接同时使用 `mode=ro`、`PRAGMA query_only=ON`，用一次读事务取得事件快照，随后关闭数据库并在内存重放；临时 ORM event 对象不绑定 session。

stdout 是一份确定性 JSON，可由调用方在账本目录之外归档。退出码 `0` 表示读取成功且 collecting/ready_for_shadow，`2` 表示读取成功但 V2 blocked（完整产物仍在 stdout），`1` 表示读取/格式失败，错误在 stderr。`collecting` 不是验收成功，`ready_for_shadow` 不触发提权。

产物包含：

- `source_event_count`、`source_high_water`（按事件时间和 ID 排序的末项）及 `source_digest`（含原始证据 note 的源快照摘要）。原始 note 和任意 status detail 不输出。相同证据在新 event 再出现会改变源摘要，但不会重复计入匹配数。
- 每项 `audit_digest`、`input_digest`、`evidence_digest`、`sample_digest`，以及真实 replay 的 `report_digest`、expected/replay fill digest、verdict 和逐字段 differences。解析失败保留 issue_code 及 unknown verdict。
- 既有 readiness summary（V1 独立），以及覆盖整份结果的 `observation_digest`。动态生成时间不进入产物；同输入、同实现重复执行逐字相同。实际运行时间与代码版本由外部归档记录，不能把旧样本的重复执行写成新样本增长。

读取全部已知 V1/V2 evidence/status 事件，不采用会遗漏失败项的 limit 或增量游标。源摘要可发现追加和已有事件修改；high_water 仅用于定位，不单独作为覆盖完整性证明。若将来引入新 schema，应先扩展已有 parser 和观察测试。本入口不推断没有 evidence/status 的普通交易已成功重放。

## 持续运行归属与验收

由现有云端运维任务负责人部署后接入独立 cron。本地已新增以下归档包装与 opt-in 模板；未安装云端 cron/runit，也未调用可能触发 live scheduler 的 GET。周期任务只需要数据库读权限和独立报告目录写权限；任务需处理 exit 2 为“产生证据但发现阻断”，不能把它当作空报告删除。

在仓库根目录，以安装了 backend 依赖的解释器调用（路径按部署环境替换）：

```sh
backend/.venv/bin/python -B scripts/archive_paper_observation.py \
  --db /var/lib/qagent/qagent.db \
  --output-dir /var/lib/qagent-observation/runs \
  --timeout 300
```

包装器使用同一解释器从 backend 目录运行原观察 CLI，不加载业务 scheduler。产物目录必须独立于数据库，不能包含数据库文件；服务用户应事先获得该目录写权限。每次运行生成 UTC 时间和随机 ID 命名的 JSON，包含开始/结束时间、数据库路径、调用参数、Git HEAD、`backend/qagent/**/*.py` 与包装器源码 SHA256（含工作区未提交代码）、退出码、stdout/stderr、解析后的完整观察与源摘要。源码摘要不包含依赖环境，不等于可复现环境锁定。源码读取或超时等包装器异常也会保留错误记录；目录不可写、磁盘错误等归档自身故障只能在 stderr 报错，不能承诺产物已落盘。

同目录 `.observation.lock` 防止任务重叠。JSON 先写临时文件并 fsync，再以不可覆盖的 atomic link 发布，文件设为 `0444`，重复执行保留旧文件。这是本地防误改措施，不是 WORM 存储；暂不自动删除历史归档。退出码 `0` 为 collecting/ready 的正常观察，`2` 为 blocked 且报告保留，`1` 为常见运行/包装错误，`75` 表示已有任务持锁、本次未执行也不创建报告；子进程其他非负退出码原样返回。

`deploy/runit/qagent-observation.cron.in` 是 Debian `/etc/cron.d` 格式的可选模板，现有部署安装器不会自动启用。负责人需渲染 `@APP_DIR@`、`@STATE_DIR@`、`@SERVICE_USER@`、`@OBSERVATION_DIR@`，确认路径与权限后单独安装。模板依赖主机 cron 使用 UTC，每周一至周五 `08:10 UTC`（北京时间 `16:10`）执行；它没有交易所节假日过滤。非 UTC 主机必须换算时刻。安装后应核验实际触发日志、报告时间/退出码及连续 source/sample digest，而非仅检查模板存在。当前没有新增周期执行证据。

重复运行时比较 source digest、sample digest 和 verdict。无新源事件且结果未变表示观察仍覆盖同一批证据；有新源事件但 unique 样本未增长表示重复证据或状态事件；结果变坏、读取失败或任务未运行需要运维关注。没有周期执行日志和连续产物时，只能声明单次只读观察已实现/已验证，不能声明持续调度完成。

不改唯一模拟账本、成交规则、费用、策略或账户权限，不新建模拟账户，不自动接管，不接实盘。重放内存中的临时执行状态沿用原有实现，不产生持久账户。

## 本地验证

`backend/tests/test_paper_observation.py` 覆盖只读强制/账本文件哈希不变、同输入幂等 JSON、空数据 collecting、缺库拒绝创建、V1 隔离、重复 V2 去重、malformed/status build failure、未知差异、解释性差异、同阶段冲突、缺表拒绝，以及重复证据新 event 改变源摘要但不增加唯一匹配数。测试 fixture 仅证明实现行为，不是真实账户效果证据。

2026-09-10 新增 `backend/tests/test_observation_archive.py` 的 6 项测试通过（6.48 秒），覆盖成功与 blocked 归档、缺库不创建、重叠锁、异常和超时，验证两次归档互不覆盖、源摘要一致与账本文件哈希不变。新增包装器和测试通过 Ruff；尚未 push、正式部署或安装观察 cron。

## 云端单次执行证据（2026-09-10）

主任务审阅实现后，将 CLI 单文件放在 `/tmp/qagent-paper-observation-20260910.py`，使用已部署虚拟环境及已有模块，对 `/var/lib/qagent/qagent.db` 执行一次，退出码 `0`。这次临时文件执行不等于正式部署；未安装 cron。

| 字段 | 观察值 |
| --- | --- |
| source_event_count / sample_count | 14 / 14 |
| V2 buy matched / target | 5 / 5 |
| V2 sell matched / target | 5 / 3 |
| V2 unknown / audit_build_failures | 0 / 0 |
| gate | ready_for_shadow |
| V1 observed / matched / unknown | 4 / 3 / 1 |
| source_high_water.occurred_at（数据库原值，未附时区） | 2026-09-10 02:06:00.000000 |
| source_high_water.event_id | paper-event-1d04e5e7ae1945d193d4f470afdbcdad |
| source_digest | 4add6765972ff67e0a21b4cf9940e793103003e939900a6499f9f53cae94ca70 |
| observation_digest | 47ea95926e6de582aba57326cd49fea2d91fe07c1b349dfcf08d712686e7e61a |

以上为主任务实际 CLI 输出的摘要；不是新增 14 笔成交，且 V1 的 1 条 unknown 不计入 V2 阻断。主任务同日检查 `/etc/cron.d` 仅见 backup、sysstat、e2scrub_all，未发现专用 observation cron。当前证据支持“入口已实现/已测试，云端一次只读观察成功”，尚不支持“持续运行已完成”。

下一项具体工作：由云端运维任务负责人正式部署只读 CLI、接入独立周期任务，并核验多次运行日志与连续归档产物（含实际执行时间、代码版本、退出码和摘要变化）。当前不安装调度、不改变任何交易权限。
