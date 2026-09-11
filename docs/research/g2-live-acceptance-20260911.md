# G2 自然前向信号验收（2026-09-11）

截至 **2026-09-11 09:30:57 UTC / 北京时间 17:30:57**，当日自然扫描仍在运行，研究 source 为 0，真实 ready signal 为 0。当前不能计算真实 Top 差异，也不能升级 G2 为前向采集完成或策略有效。

## 本轮只读证据

检查主机 `luozhenkun@172.28.216.120`。数据库连接为 `file:/var/lib/qagent/qagent.db?mode=ro`，设置 `PRAGMA query_only=ON` 并以 `BEGIN` 固定读取快照；只 SELECT 最近两条 `full_market_scan_jobs`。归档文件直接读取。未请求 scheduler GET，未启动扫描、collector、行情采集或补价，未重启服务、修改云端文件或数据库。

| 对象 | 当次观察 |
| --- | --- |
| 当日自然 scan | `full-scan-20260911075213-223b48a3`，provider `free`，`running` |
| 进度 | `15 / 36` batches，`3000 / 7148` symbols，`cards=1780`，`errors=0` |
| scan 时间 | started `07:52:21.778542 UTC`，updated `09:30:45.878692 UTC`，finished `null` |
| source | `/var/lib/qagent-research/g2-forward-sources` 存在，JSON 文件 `0` |
| signal | `/var/lib/qagent-research/g2-forward-results/signals` 尚不存在，JSON 文件 `0` |
| 当前 release 符号链接 | `/opt/qagent/current` 指向 `94cf6f5057723460a88becd0c5e44f864a6cc53c`；仅核验链接，不等同于本轮核验进程环境 |

09-10 上一条 scan `full-scan-20260910104100-63a4ef30` 为 `succeeded`、`36 / 36`、`7148 / 7148`，结束于 `2026-09-10 11:55:57.356013 UTC`。这是先前扫描的完成证据，不能替代 09-11 的全量研究 source 或 ready signal。运行中 `cards=1780` 也不是最终研究覆盖数。

## Collector 状态

今日已有四份自然调度产物，位于 `/var/lib/qagent-research/g2-forward-results/runs/`：

| UTC 启动时间 | 文件 |
| --- | --- |
| 08:00:03.891744 | `20260911T080003.891744Z-ca686d1cb9a94553a5e5f0ab9d39ab24.json` |
| 08:30:05.006665 | `20260911T083005.006665Z-ff13c063fd374715b22127ce759197b4.json` |
| 09:00:03.591840 | `20260911T090003.591840Z-e760cbf714504a868b5bb94765c8b620.json` |
| 09:30:03.716466 | `20260911T093003.716466Z-8717fb44558d4fd6a1cbabf944d133b7.json` |

四份均为 `protocol=g2-forward-schedule-v1`、`signal_date=2026-09-11`、`status=waiting_for_source`、`attempts=[]`、`errors=[]`、`exit_code=0`；`decision_weight=false`、`activation_allowed=false`。这说明调度已执行并正常记录等待，尚未尝试处理真实 source。退出 0 不代表 collector 已产生前向信号。

## 验收边界与下一步

本轮没有 ready signal，因此没有复制信号文件，也没有运行已有 `scripts/compare_g2_selections.py`。脚本实际支持的 Top-5、Top-10、Top-10% 重合度、进出股票、排名变化和行业差异均为**待验**，不是 0 差异。合成测试结果不能填入这些市场结果。

后续自然扫描完成并产生完整 source、当日 collector 归档 ready signal 后，才可将单一 signal 复制到本地独立目录，使用[已有行为对照入口](g2-selection-behavior-20260911.md)生成独立结果；应保留源摘要、共同股票集合、排除身份和覆盖比例。本次观察停在运行中的扫描，不主动触发任务，也不长时间等待。

完整数据验收仍缺首份 source 与 ready signal，不能由当前扫描进度推断完成时间或成功结局。即使后续真实截面行为对照完成，也只说明选股行为差异；冻结窗口的成熟收益、样本外净超额、回撤、换手和行业暴露仍需按既有协议验收。依[冻结协议](g2-risk-feature-freeze-20260910.md)，若 09-11 首日实际归档合格信号，20 交易日标签最早 10-19 到期；本报告没有计算或补造收益。

本轮仅新增此只读快照文档；未修改 `PROJECT_GOAL.md`、冻结模型、交易权重或唯一模拟账本。未新增代码，未执行代码测试；未 commit、未 push、未部署。
