# G2 隔离前向采集入口

2026-09-10 已实现本地入口，默认关闭；未 commit、未 push、未部署，真实前向样本仍为 **0**。这份记录不代表 G2 策略验收或持续采集已经开始。

## 为什么需要当场保存输入

`full_market.py` 的最终缓存只保存 card universe 的排名，不能代替完整研究横截面。逐批 checkpoint 虽然保留完整输入，但成功任务会经 `delete_succeeded_full_market_scan_checkpoints` 删除。独立事后读取成功任务缓存不能可靠复原完整横截面。

新增 `g2_forward_source.capture_if_enabled` 在既有 shadow 评分前保存已计算的 `global_shadow_rankings` 股票全集，不重复运行行情 provider、训练或选股。产物明确标为 `ranking_finalized_before_job_completion`，不声称任务最终成功。只有设置 `QAGENT_G2_CAPTURE_DIR` 才启用，未设置时不访问数据库或文件。异常仅记通用日志，原评分继续。

启用后，从当前应用配置的 SQLite 文件使用 `mode=ro`、`PRAGMA query_only=ON` 和一个读事务读取 revision 与行业；按现有行业日期/revision/provider 顺序选择，并排除采集开始时尚未知的行业。读连接确定性关闭。源归档包含完整股票排名原始特征、交易日期、股票全集、行业原行、revision、源码 hash 和真实 UTC。独立 JSON 使用原子独占发布、文件及目录 fsync、只读权限；同任务不覆盖原归档。这是应用级不可覆盖归档，不是防管理员修改的 WORM 存储。

## 固定口径与缺失处理

collector 核验原冻结 manifest 的固定摘要、config、六模型字节和特征顺序、原 scorer identity；复用 `prepare_features(raw)` 的横截面预处理、两组原 subset 和三 seed 均值预测。源归档及原始输入完整嵌入结果，保存模型/source/scorer/collector/预处理 hash、两组逐股票预测与 coverage，前向收益仍为 null。

在首个真实样本之前明确共同可用集合：交易日期缺失或不是当日的股票，以及全部 17 特征缺失的股票，从**两组同时**排除；记录排除身份、原因、原全集和有效比例。部分特征缺失沿用原预处理与 LightGBM 缺失值行为，不添加 100% 特征覆盖门槛。两组至少共享 5 个可用股票。这个口径避免停牌或旧行情使整个批次永远不能采集，但可用集合仍可能有覆盖/幸存者偏差；后续报告必须展示覆盖，不能声称代表 100% 全市场。行业、市值和各特征非空数、两组联合完整数单独归档。

只有 2026-09-11 至 12-31 的 XSHG 固定每 10 交易日采样日、冻结后同日 15:30（上海）以后的输入采集，且预测也在同一自然日完成，才进入 `signals/日期.json`。日期、时区与真实时钟不可通过 CLI 参数回填。其他输入只归档 `not_ready` diagnostic，退出码 2；坏 hash/模型/契约报错非零。测试中的固定时间和合成来源不算真实前向记录。

同一天第一个合格预测永久占用信号文件；再次运行返回旧文件，不增加独立样本。失败尝试进入单独 diagnostics，不阻止当日新扫描修复后产生合格信号。不同日不能补做历史预测后追认前向样本。源记录中的源码 hash 提供可复核身份，不是对任意手工篡改者的数字签名。

## 启用与调用（尚未执行）

在运行扫描的既有进程环境中配置独立研究目录，然后按原流程重启/部署该进程；这一步尚未执行，不应只给 collector 配置环境就认为生产源捕获已启用：

```sh
export QAGENT_G2_CAPTURE_DIR=/absolute/research/g2-forward-sources
```

当上述源文件在当日形成后，在仓库根目录调用（替换 source 路径）：

```sh
PYTHONPATH=backend backend/.venv/bin/python -m qagent.research.g2_risk_feature_forward \
  --source /absolute/research/g2-forward-sources/DATE-JOBHASH.json \
  --frozen-dir data/archives/g2-risk-feature-ablation-20260910-frozen-v1 \
  --output /absolute/research/g2-forward-results
```

本地已补充 `qagent.research.g2_forward_schedule` 与独立 opt-in 模板 `deploy/runit/qagent-g2-forward.cron.in`，尚未安装到云端。模板假定主机 UTC，在工作日 08:00 至 15:30 每半小时检查一次（上海 16:00 至 23:30）。wrapper 自行核验冻结交易日采样表，非采样日跳过；只读取当日文件名及 signal_date 一致的源，按 captured_at_utc、文件名排序尝试，首个 ready 后停止。同日已有完整且摘要正确的 ready 信号直接返回，不覆盖、不增加样本。collector 仍负责真实时钟、冻结摘要和模型检查，未来日期不能预造前向样本。

替换模板中的 `APP_DIR`、`SERVICE_USER`、`G2_SOURCE_DIR`、`G2_FROZEN_DIR`、`G2_OUTPUT_DIR` 占位符后才可由运维负责人安装。源目录必须与既有 backend 进程环境中的 `QAGENT_G2_CAPTURE_DIR` 一致。冻结模型被 Git 忽略，部署须另行分发并通过 hash 检查；仅 push 代码或设置 cron 环境不足以启用生产源捕获。

```sh
cd backend
.venv/bin/python -B -m qagent.research.g2_forward_schedule \
  --source-dir /absolute/research/g2-forward-sources \
  --frozen-dir /absolute/research/g2-frozen-models \
  --output-dir /absolute/research/g2-forward-results
```

同一输出目录用文件锁防止调度重叠。无当日来源返回 `waiting_for_source` / 0；非采样日、已采集和成功也返回 0，not_ready 返回 2，仅进入原 collector diagnostics；无成功且有损坏来源/采集异常返回 1，锁占用返回 75。每个取得锁的运行在 `runs/` 独占归档状态、尝试及错误，源文件保留。新到来源可在后续运行重试，归档失败会直接报错非零。wrapper 不连接数据库、不请求 provider、不调用交易，不增加评估器、标签补齐或生产策略登记。

## 本地验证

`test_g2_risk_feature_forward.py` 覆盖六个真实模型的精确预处理与均值推理、源/模型损坏、时间边界、共同排除旧行情、失败可修复、同日幂等不可覆盖、SQLite 源文件不变及缺失库不创建、默认关闭和捕获失败隔离。当前本地冻结模型存在，10 项通过且没有跳过；缺少非 Git 分发模型的环境会明确跳过真实模型用例，不能据此声称已经验证模型加载。

主任务复核当前代码后，最终相关综合回归 **112 项通过（9.48 秒），零跳过**；新增文件与 `full_market.py` 的 Ruff 检查、`git diff --check` 均通过。这个验证不表示云端已启用或任何前向收益成立。

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_g2_risk_feature_forward.py -q
```
