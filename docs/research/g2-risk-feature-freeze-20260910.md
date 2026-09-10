# G2：冻结风险预测输入消融候选

2026-09-10 已离线冻结 `full_features` 与 `without_risk` 两组、种子 7/19/42
共六个 LightGBM 模型。状态为 `models_frozen_forward_unvalidated`：已生成本地
模型文件并验证可加载，未注册线上候选、未部署、未收集新前向样本，前向指标为 null。
本报告不代表 G2 完整验收。

## 假设与选择依据

固定假设：在其他输入、训练样本、种子、早停、选股比例和成本完全相同的条件下，
从预测器移除 `volatility_20`、`downside_risk_60`、`max_drawdown_60` 可改善
头部股票的前向净超额。这里的 risk 指三个预测输入，止损、仓位、行业限制和执行
风控均不变。对照是同训练窗口的全 17 特征 LightGBM；原全特征线性模型仅为旁证。

[亏损归因](paper-loss-attribution-20260910.md) 显示历史损失集中于止损组和
七月入场交易，只用于提出入场选股研究问题，不证明风控或风险输入导致亏损。
[原稳定性报告](factor-ablation-stability-20260908.md) 的风险输入消融在 13/18
日期改善毛超额、三个固定种子净差均正，支持冻结单一前向假设；但五个日期变差、
后半段 Rank IC 差为负。2025 测试窗已经暴露，不能再次当作未见样本。

没有重复已有候选：`factors/research_contract.py` 的显式线上 ID 只有
`trend-health-composite-v1` 和 `turnover-volume-strength-v1`；
`ranking_head_challenger.py` 固定的是 top5 排序头。本候选是原消融发现的
14 特征预测器，与两者均不同，不改选股头数量。

## 冻结协议与真实产物

配置见 [冻结配置](g2-risk-feature-freeze-20260910-config.json)，完整执行与模型摘要见
[归档 manifest](g2-risk-feature-freeze-20260910-manifest.json)。复用原
`factor_ablation.validate_input/prepare_features` 和
`factor_experiments.compare_baseline_and_lightgbm`，只运行上述两组，不运行其他
变体、不搜索 recipe/种子、不根据旧 test 调整参数。训练 2022-04-28 至
2024-05-09、验证 2024-06-21 至 2025-02-06，均保留原 purge geometry；
2025-03-20 至 2025-11-28 的 test 输出明确标为旧样本描述。

- 输入原始字节 SHA-256：`6621a9bfa984f24a6e9e34b9c15c7ec56aa5f9d85b38d9f93c9eefbe6ec5eff8`。
- cohort SHA-256：`dbc965784d129ee8c32bba38afe2d202e801ab64aa996d795a53e1efb2e454cc`。
- config SHA-256：`3d29c78734417a4eb38c7655a09a53b2605843849e834f25a0c71a180b7a0f96`。
- manifest SHA-256：`b6bbb9a44dbb61fb4ec8879034ef6232e2cd32f07356a1407ffad71c69c9e77b`，对移除自身摘要字段后的 JSON 使用 sort_keys + 紧凑分隔符计算。
- 模型冻结真实 UTC：`2026-09-10T06:44:40.989279+00:00`，训练加校验约 25.5 秒。
- 模型目录：`/Users/luozhenkun/Documents/Qagent/data/archives/g2-risk-feature-ablation-20260910-frozen-v1/`；六个文本总计 1,207,211 bytes，受已有 `data/archives/` Git 忽略规则管理。
- 输入仍位于 `/private/tmp/qagent-factor-ablation-20260908-input.json`，未重复复制 403 MB 数据；可能被系统清理。模型本地文件不随 Git push 分发，完整复现依赖输入和模型另行备份。源码 commit 加 source_sha256 标识本次未提交 runner 的实际字节。

六个模型摘要与旧消融归档逐一完全一致，说明本次冻结可复现原模型，不能解释为新增
策略效果证据。八项协议/失败处理测试通过；六个真实模型的加载、特征名称/摘要和
合成零向量推理通过，合成推理仅验证文件兼容。Ruff 检查通过。

运行后补充了一行失败报告修复：`finally` 根据目录中实际模型文件设置
`local_models_persisted`，避免训练失败时错误报告已保存模型。manifest 保留运行时
runner 的源码摘要；这项报告修复不改变训练、模型字节或本次成功结果。
执行原字节已保存在模型目录 `g2_risk_feature_freeze.executed.py`，摘要重新核对为
`fc7ae858373384693fc340c3396f84cc071a59d5491c0c40a86392886be86d99`；
修复后 runner 摘要为 `4230b87362d0d1105f139b52f3c64355fdc559fd23216a3ab863d85da456dc58`。
修复后八项专项测试再次通过（6.45 秒），含失败时无模型文件的 false 标志断言；
此前主任务综合 69 项通过属于修复前验收记录。
主任务最终相关回归 **70 项通过（12.56 秒）**，四个新增 Python 文件 Ruff 及
diff 检查通过；独立复核六模型 hash、manifest 以及原执行/修复后源码摘要，均与
本报告匹配。

## 固定前向观察口径与剩余工作

独立信号窗预声明为冻结时刻之后的第一个 XSHG 交易日 **2026-09-11 至
2026-12-31**。复用原 10 交易日采样、20 交易日标签、头部 10%、均值 seed 预测、
10 bps 往返成本启发式和相同横截面/行业/市值中性化。仅有在信号时刻归档了
输入版本、source/scorer digest、模型 digest 与两组预测的批次才可进入前向证据；
未来补做历史截面的预测不能追认为当日自然前向记录。开始日期是允许采集的下界，
不是已经开始采集的声明。若 9/11 实际有合格信号，现有 XSHG 日历给出的首个
20 交易日标签成熟日为 **2026-10-19**；无实际归档则不存在该批到期样本。

同批、同股票全集、同标签与同成本配对比较两组，保持缺失率、失败及负结果。
汇报原比较器净头部超额差、毛超额差、Rank IC、换手；另外从同一选股集合报告
行业权重和集中度。支持假设需要未见前向窗口的净差为正并结合回撤、换手和行业
暴露解释；净差为负则否定，标签未到期或配对数据不全则标为未成熟/不完整，不能
用旧测试结果补位。窗口是研究评估计划，不是新增实盘门禁或自动晋级条件。

原 `_model_metrics` 的净指标是标签均值减平均换手成本启发式；重叠 20 日标签
复利所得回撤是诊断，不能冒充可交易组合回撤。G2 完整报告仍需复用已有
matched-control / execution 口径，在同成本与同可执行集合上给出净超额及真实组合
回撤，报告两种口径差异。缺少成交/价格事实时保留缺失，不制造结果。

`factor_shadow._score_factor_shadow_bundle` 已从排名的 `research_features` 取全 17
特征并按模型 subset 推理，因此数值输入结构支持本候选；本轮未核验在线14项联合
覆盖率。`FactorResearchConfig` 的公开验证器只接受已登记候选或全特征 legacy，
所以本地研究 `model_copy` 不能绕过公开配置去注册。本轮没有修改 registry、评分器、
scheduler、账户或数据库。下一步明确是接上这对冻结模型的隔离前向归档，核验该
批特征覆盖，并等待实际信号对应的20日标签；不是再做一轮旧测试调参。

## 复现与失败归档

在仓库根目录运行，输出目录必须不存在：

```sh
PYTHONPATH=backend backend/.venv/bin/python -m qagent.research.g2_risk_feature_freeze \
  --input /private/tmp/qagent-factor-ablation-20260908-input.json \
  --config docs/research/g2-risk-feature-freeze-20260910-config.json \
  --output data/archives/g2-risk-feature-ablation-reproduction-unique
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_g2_risk_feature_freeze.py -q
```

新 run 会有自己的真实冻结时刻及 manifest；只比较输入/cohort/model 摘要，不要求
带时间戳的整个 manifest 相等。输入丢失、空数据、hash 不符、训练失败等退出非零，
已创建的新目录保存 `status: failed` 和错误；既有目录拒绝覆盖。未成熟前向窗口
不运行伪造测试，当前 `forward_observations: 0`、`forward_metrics: null`。

本轮状态：已实现、已测试；未 commit、未 push、未部署。未写生产 registry，
本地六模型已持久化。这些边界也适用于把本次产物交给下一项工作时。

后续同日已补充默认关闭的完整输入捕获和隔离前向 collector，见
[采集入口与运行说明](g2-forward-collector-20260910.md)。真实样本仍为 0；未部署或接入调度。
首样本前澄清共同可用集合：旧行情/缺失日期及全特征缺失股票从两组同时排除，保留原全集、排除身份与覆盖比例，部分特征缺失沿用原处理。此前“同股票全集”指两组配对使用相同的该批可用集合，不将覆盖不足隐藏为全市场完整。
