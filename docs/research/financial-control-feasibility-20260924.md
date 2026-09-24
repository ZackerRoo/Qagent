# Financial 同行业对照可行性诊断（2026-09-24）

更新：下文“额外同行待批准、未实施”为当时阶段快照；本轮已获授权并完成默认关闭的 peer-control v5 本地实现，当前范围与限制见文末补录。尚未部署或启用。

09-22、09-23 的配对失败存在确定的集合结构原因：两日 Top5 各有四只股票在原始 20 股观察集合中就是行业单例，连未通过财务资格的股票中也没有同行。等待价格成熟、补同日 baseline 或改进分配顺序，都不能为这两份不可变信号产生合规的五对 control。

本轮仅通过 SSH 读取云端归档，复用已安装 forward-v12 的 `validate_archive`、`seal`；没有云端文件写入、API 取数或数据库操作。本地仅增加本报告，未改变模拟账户、账本、持仓、现金、调度、正式 Ranking、模型或交易规则。

## 逐股证据

`eligible peers` 是同日 Financial 合格集合内除自身之外的同行；下列存在的同行全部位于 Top5 之外。供应方行业是信号日 current observation，不追认为历史 PIT。

| 信号日 | Top5 顺序 | 股票 | 行业 | eligible peers | 原 20 股该行业总数 |
| --- | --- | --- | --- | --- | --- |
| 09-22 | 1 | CN:300760 | 医疗保健 | 2：CN:688139、CN:688656 | 3 |
| 09-22 | 2 | CN:603979 | 铜 | 0 | 1 |
| 09-22 | 3 | CN:603766 | 摩托车 | 0 | 1 |
| 09-22 | 4 | CN:600795 | 火力发电 | 0 | 1 |
| 09-22 | 5 | CN:002215 | 农药化肥 | 0 | 1 |
| 09-23 | 1 | CN:600368 | 路桥 | 0 | 1 |
| 09-23 | 2 | CN:000690 | 火力发电 | 0 | 1 |
| 09-23 | 3 | CN:688758 | 化工原料 | 1：CN:002226 | 2 |
| 09-23 | 4 | CN:603658 | 医疗保健 | 0 | 1 |
| 09-23 | 5 | CN:688169 | 家用电器 | 0 | 1 |

09-22 Financial eligible 为 13/20，09-23 为 16/20，同日 baseline 均为 `available`。两日最多各有一个 Top5 槽位能与合规同行形成单对，因此既不满足完整五对，也达不到至少两个 Top5 外 control 的可判别要求；正式算法不会输出这些局部配对作为组合结果。

## 相同证据的 v3 / v4 对照

两份原归档均为 `financial-rule-forward-v3`，`validate_archive` 通过，原结果均为 `control_unavailable`、`same_industry_control_unavailable`、0 pairs。将各自归档内的原 `source`、`baseline_source`、`sealed_at` 交给已安装的 v4 `seal`，只在内存计算，结果也均为相同状态、原因和 0 pairs。

这是事后算法诊断，不是新前向信号、收益评估或自然 v4 验收。v4 可解决贪心分配造成的可行解遗漏，但无法制造集合中不存在的同行；本样本不能据此否定 v4 对其他可行集合的价值。

读取前后文件 SHA256 相同：

| 云端文件 | SHA256 |
| --- | --- |
| `/var/lib/qagent-research/financial-forward-signals/2026-09-22.json` | `944f27068c35164670638a6bb512e0ac60c4c61b3424dbe5f6ad1c97a05ba941` |
| `/var/lib/qagent-research/financial-forward-signals/2026-09-23.json` | `2a6b50b222bfcb12a67dbd2f6a96132911d7c40a0b3176c1d5fa671bdc012586` |

## 复核方法与复用边界

执行环境为 `ssh -o BatchMode=yes luozhenkun@172.28.216.120`，远端命令为 `PYTHONPATH=/opt/qagent/current/backend /opt/qagent/current/backend/.venv/bin/python -B -`；stdin 将 `/opt/qagent-research/financial-forward-20260924-v12/scripts` 放入 `sys.path`，导入既有 evaluator。核心调用为：

```python
validate_archive(signal)
diagnostic = seal(signal['source'], signal['baseline_source'],
                  now=datetime.fromisoformat(signal['sealed_at']))
```

同行统计取 `source.industry_evidence.rows`，eligible 身份取 `signal.rankings`，Top5 取前五项，排除自身后按行业精确相等比较；另在完整行业证据的 20 股范围计数。两份验证与内存计算正常退出 0，前后 SHA 相同。未运行需要价格或 SQLite 的 evaluate，也未写入 seal 产物。

已检查已有 `compare_g2_selections.py`、`analyze_selection_segments.py` 和 `evaluate_financial_challenger.py`。本问题直接复用最后一个的真实封存和匹配逻辑即可，不新增比较器、采集器或长期消费者。

## 最小下一决策

将当前失败明确归为“观察集合缺少同行”，保留既有连续五份不可用信号的停止条件及跨 v3/v4 累计，不为这两份旧信号继续等待配对修复。未来自然候选池变化可能产生可配对集合，但两天证据不足以估计概率，更不能保证多等几天可治愈。

最小 prospective 备选方案是保持原 20 股候选排序及选出的 Top5，单独为研究比较采样数量有上限的额外同行 control：使用同一行业来源和信号日，提前规定市值匹配规则、资格条件、请求预算、失败停止条件和协议生效日。它改变的是研究对照集合和采集预算，需要用户批准；不扩大模拟盘或 Financial 候选选股池，不放宽行业定义。不能保证只多取五只就够，因为额外股票仍可能资格不符或证据失败，届时继续明确不可评估。

建议只把是否批准上述研究对照方案作为下一决策；尚未制定具体采样数量、请求预算或生效日，更未实施。若仍坚持所有 control 必须出自原 20 股，则必须接受行业单例日不可评估。任何新方案不追改这两份历史信号；本轮不扩池、不合并行业、不换股、不用部分均值，也不自动暂停调度。

报告已实现并以真实归档复核；纯文档未新增测试，未提交、未 push、未部署。不据本诊断提升 G2 完成状态或交易权限。

## 同轮可立即推进的 G2 缺口

主任务本轮代码审计发现，现有冻结 G2 collector 保存 predictions，尚未找到消费这些归档的 20 交易日 outcome evaluator；这项接线可现在实施，不必等待 Financial 配对。主任务已授权另一子任务实现独立离线 evaluator，当前为实施中，未完成验收。

须复用冻结标签：信号日复权收盘到第20个后续交易日复权收盘，Top10%；行业样本不少于5只时用行业中位数，否则沪深300。它不同于 Financial 的次交易日开盘入场。原对照成本口径为换手率×10bps；单个横截面没有有效换手证据时净值结果留空，不套用固定10bps伪造净收益。不增加调度，不改变规则、模型或账户。

主任务另只读核验 Financial 唯一已成熟结果 `/var/lib/qagent-research/financial-forward-evaluations/2026-09-14/5.json`：SHA256 `32f86bd3960c0e2022a39ce04f17e8a9e77660c4de18ff94d07ded2e32e1a591`，5日净超额 `-3.2971742378645645%`，`baseline_unavailable`、lift为空。这是单窗口负结果，不是 matched-control 假设检验，也不能由它宣称财务方案有效。运行快照 current为`83cdcb5`、health正常，独立paper tick为2/2、last_error为空、最近slot03:10；运行健康与研究结果分开记录。

## G2 离线结果入口验收补录

以上“实施中”为阶段快照。现已实现 `scripts/evaluate_g2_forward.py` 和 `backend/tests/test_g2_forward_outcomes.py`：子任务最终12项测试通过且无warnings；主任务最终联合64 passed（5.00秒）、零warnings，Ruff及diff检查通过。当前代码未提交、未push，未部署生产消费者或新增cron。

本地使用方法（下列绝对输入路径须替换为实际归档、只读数据库及冻结manifest路径，output须为新文件）：

```sh
backend/.venv/bin/python -B scripts/evaluate_g2_forward.py \
  --signal /absolute/path/to/signal.json \
  --db /absolute/path/to/qagent.db \
  --manifest docs/research/g2-risk-feature-freeze-20260910-manifest.json \
  --output /absolute/path/to/new-outcomes.json
```

主任务将最终脚本临时复制到云端隔离目录做真实归档smoke；脚本SHA256为`943f13b562434d55080df634c29bd1361b26651e7d22d88a5e083c1659835557`。结果`waiting_for_maturity`、expected 5541、到期日2026-10-19、指标为空，指定不存在的DB路径执行后仍不存在。原signal SHA256 `d538386a89183f42b6a2b900be3405c338c034c014bd1fa4cd6e00d83ee6eef4`保持不变；临时结果保存在`/var/tmp/qagent-g2-outcome-check.dy647S/final-observed.json`。本次仅验证未成熟分支及隔离边界，不是成熟收益验收。云端确有临时脚本和输出文件写入；未修改生产部署、cron或账本。额外同行control方案仍待批准，未实施。

## G2-FQ3 有界同行研究 control v5 本地实施补录

本轮已批准并实现显式 opt-in 的 `financial-rule-forward-v5`：仍由原 Financial evaluator 消费；原20股、Financial排名和Top5不变，原report字节保持不变，附加研究证据独立留存。最多新增10只研究control，不进入选股池、模拟盘或正式Ranking，不重写旧信号。上述“待批准”记录保留为此前阶段；当前已授权的是本地实现，未部署、未启用。

供应方实测显示offset分页语义不可靠、industry过滤被忽略，因此实现只采集`stock_basic`和同日`daily_basic`各第一页、各最多5000行，共2次批量请求。采集后在本地精确匹配行业，仅在实际捕获的两份数据交集中按预定市值距离寻找同行；“最近”只指该交集内最近，不能称全市场最近。两份目录完整性均未知，首屏截断及交集选择可能造成系统性抽样偏差，必须随结果保留，不能把目录成功等同全市场覆盖。

后续有界调整：首屏可能未包含原Top5，不能仅因该缺席拒绝整批；匹配锚点继续使用原逐股采集且已校验的同日行业和市值。批量数据若包含该锚点，则必须与原证据精确一致。额外control仍只能来自实际捕获交集，不制造目录行、不补造control、不改变原排名。调整后主任务独立复跑下述七组测试，最终140 passed（17.81秒），所有修改Python文件的Ruff及`git diff --check`通过；下述137 passed保留为调整前基线。

额外最多10股×7个财务接口=70次，加2次目录请求，新增最多72次；叠加原20股的最多160次，每批上限232次。继续共享原600秒deadline及同日最多两批预算，不因额外control扩大时间或重试次数。额外股票仍须通过资格与证据校验，10只是上限，不保证形成完整五对；不可用、不可判别及缺价时继续保留失败/未完成状态，不计算部分组合lift。既有停止条件和跨版本失败累计不重置。

入口为`collect_daily_documented_research.py --peer-controls`，必须同时使用`--daily-frozen-industry --bounded-same-day`；默认不启用。没有修改paper账户、持仓、现金、账本、交易规则或cron。主任务独立运行当前七组测试（peer evidence、peer integration、challenger forward、prospective integration、daily baseline、industry evidence、G2 forward outcomes），137 passed（16.67秒），`git diff --check`通过。只读集成review未发现阻塞项，确认v1–v4行为保持、v5原候选与额外control计数分别记录。这是当前实现验证，不借用上节64 passed结果。既有连续五份失败停止条件是运行决策标准，不是本轮新增的自动计数器或自动停调机制。已实现并完成上述测试/review；未push、未部署、未启用，尚无本轮自然配对或真实收益提升证据。
