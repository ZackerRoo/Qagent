# 分钟行情时效观测受控发布（2026-09-18）

## 范围与边界

源提交 `e999e4f5433ad424b441cc73f3950a4f87445389` 已提交并 push。本轮仅发布逐笔持仓分钟响应时间观测，以及 provider-status 最近20条扫描窗口内的 health 字段投影读取。资金流第七指标仍为研究提案，未实施权重、采集或调度变更；不改变唯一模拟账户、交易规则、历史账本、正式 Ranking 或研究晋级权限。

`paper_minute_freshness` 按 trade_id 保存 instrument_id、响应最新时间、信号之后且不晚于 as-of 的最新时间、与 as-of 的秒差及缺失原因。该子集仅用于诊断，不过滤传入执行器的数据，不代表执行器实际消费了最后一行；无时区时间沿用上海本地时间约定。分钟请求失败、空表及诊断失败均保留原因，原执行返回与日线降级语义不变。自然交易时段逐股延迟仍待部署后验收。

provider-status 保持先取最近20条扫描、再选最新 full_market_batch 的顺序及 created_at/run_id 降序排序，仅读取所需 health，避免反序列化无关字段与其他扫描。此前云端单次比较 health 相等，读取量与耗时下降，详见[本轮资金流与性能证据](moneyflow-readiness-20260918.md)；这不是长期性能保证。

## 验证与研究身份影响

- 上一轮最终 backend 全量：2818 passed、3项既有 warnings，238.22秒。
- 本轮主任务复跑相关回归：109 passed、1项既有 warning，9.39秒。
- 独立 review 复跑四文件：89 passed、1项既有 warning，9.25秒；未发现 P1/P2 阻塞。
- `api/routes.py` 和 `storage/repository.py` 属于 walk-forward 的 `RESEARCH_SOURCE_DIRECTORIES`，本次变更会改变其 research source digest。recommendation alignment 的全包 `package_source_digest` 也会改变；保留旧证据身份失配拒绝规则，不绕过校验或把旧产物追认为新实现验收。
- G2 freeze 的五个显式源码文件及 factor-shadow scorer 摘要函数未改变；不修改冻结模型、输入或历史信号。

## 发布准备快照

主任务07:05 UTC只读核验：旧 current 仍为 `edbd9fa`；独立 tick 为 outside_session，最后07:00 slot于07:00:37.417870完成、last_error=null。automation仍有1个 active cycle/stage，因此只准备新 release，不在活动周期中切换。此时已实现、已测试、已提交并push；本轮部署尚未确认。最终 guarded 切换、八表账本及settings对账、服务健康和自然运行证据由主任务后续补录，不能由暂存或构建成功替代。

G7逐股行情时效、G2-FQ3收盘后同日baseline/行业/Financial自然链路及成熟收益继续待验，不提高目标完成状态。

## 本轮最终交付：已暂存，生产切换受活动周期阻塞

新 release `/opt/qagent/releases/e999e4f5433ad424b441cc73f3950a4f87445389` 已按 exact SHA clone；Python3.11 下 `uv --frozen`、临时数据库隔离 startup、`npm ci` 与前端 build 全部通过，仅有既有 chunk 超过500kB的构建 warning。暂存文件和隔离验证不代表运行服务已经升级。

07:08及07:09:47 UTC，guarded helper preview均安全退出1，未执行 execute。07:10:27 UTC只读复核仍为1个active cycle、1个active stage，`factor_shadow` 从07:05:13.805949开始持续 running；最近三次同阶段约15–22分钟。这是当前切换门禁未通过的直接证据，不据此认定任务故障，也未强制停止研究周期。

本轮已创建云端暂存release、依赖和构建文件；未修改运行中的服务配置、cron、账本、current或交易规则，三份研究cron摘要复核保持原值。新release已暂存，但current仍为旧版本，本轮没有切换后的ledger/settings对账或新服务运行验收。最终状态为**已实现、已测试、已提交并push、已暂存、未部署**。部署待活动周期自然结束后重新检查空闲门禁并执行受控切换；本轮未设置自动续办，不承诺自动后续。自然逐股分钟时效与G2-FQ3收盘后研究链路仍待验。
