# G2 冻结候选的选股行为核查（2026-09-11）

本轮确认六个冻结模型仍可加载，候选确实从预测输入中移除了三个风险特征；但本地原始输入已经丢失，现存聚合结果不能还原逐股排名。因此**尚不能回答具体哪些股票进入或退出 Top、排名变化多少、行业权重如何变化**。这是一项有明确数据缺口的行为核查，不是完成的选股对照，也不是新的样本外效果证据。

## 当次可核验证据

- `data/archives/g2-risk-feature-ablation-20260910-frozen-v1/` 存在，六份模型文本、原执行源码与 manifest 均在。
- 冻结报告指定的 `/private/tmp/qagent-factor-ablation-20260908-input.json` 不存在；`/tmp`、`/private/tmp` 与本仓库 `data` 的有界文件检查未找到替代消融输入或独立 G2 source/signal 归档。这个结论只覆盖上述检查位置，不表示所有磁盘或云端都不存在副本。
- `docs/research/factor-ablation-stability-20260908-result.json` 保留模型与日期/种子聚合诊断，没有用于此次对照的逐股票预测列表。其他策略的分段统计也不能替代这对模型的共同横截面。
- 直接调用现有 `g2_risk_feature_forward.load_frozen` 成功：固定 manifest、config、scorer identity、六模型文件摘要、种子 7/19/42 及特征顺序均通过校验。manifest SHA-256 仍为 `b6bbb9a44dbb61fb4ec8879034ef6232e2cd32f07356a1407ffad71c69c9e77b`。
- 六模型各对一行零向量实际推理，全部返回有限值。此项只验证加载兼容性，零向量不是市场样本，不能用于股票偏好或效果判断。

没有读取或重建历史数据库、解压备份、获取新行情或访问云端；没有重训、调参或改动冻结产物。

## 已证实的候选差异与未能计算的结果

全特征组使用 17 个输入，`without_risk` 使用其中 14 个。移除项严格为 `volatility_20`、`downside_risk_60`、`max_drawdown_60`；其余趋势、流动性、基本面输入保持相同名称与顺序。两组各由三个冻结种子的模型组成。这里比较的是两套分别训练后冻结的预测器，不能将结果解释为在同一棵树上把三个值简单置零，也不能从移除风险输入推出候选必然偏好高波动股票。

| 所需结果 | 本轮状态 | 缺失原因 |
| --- | --- | --- |
| Top 10% 重合数、重合比例、Jaccard | 未计算 | 无同一截面的两组逐股预测或可重算输入 |
| 候选新进入、退出的具体股票 | 未知 | 聚合收益与模型文本不能反推股票身份 |
| 共同股票排名变化、完整排名相关性 | 未计算 | 缺少配对的逐股分数与排名 |
| 行业权重、最大行业占比、行业集中度 | 未知 | 缺少该截面的选股身份及可追溯行业映射 |
| 单股为何改变 | 无法归因 | 未做单股解释；全局 feature importance 不是单股因果解释 |

现有 2025 年历史测试窗口已被用于原消融与稳定性分析。即使找回旧输入并重算，也只能补上**历史模型行为诊断**，不能重新算作新 OOS 或自然前向样本。本轮没有生成任何收益、换手或回撤指标；跨模型同日的名单差异也不能命名为跨期组合换手。

## 最小补齐材料及固定比较方法

优先复用既有自然采集 source/signal，不再训练。行为对照最低需要：固定信号日期、同一股票全集及逐股票身份、17 个原始特征或明确标记为已中性化的特征、行情日期、行业（缺失则保留 unknown）、`log_market_cap`、输入版本与摘要。只分析排名不需要收益标签；标签未成熟应保留为空。自然前向证据还必须遵守现有 source 的采集时点、代码与模型摘要、同日实际预测、first-ready 去重等完整契约，不能用一个事后手工截面替代。

如果仅找回原历史输入，固定选取原测试窗口最后一个截面 **2025-11-28**，选择依据是窗口末日，不查看表现后另挑日期。先验证原始字节 SHA-256 `6621a9bfa984f24a6e9e34b9c15c7ec56aa5f9d85b38d9f93c9eefbe6ec5eff8` 和输入契约，再取该日；保留历史输入已经暴露、历史规则和时点数据限制。若只有不同的本地快照，须另报输入摘要、日期、集合与原协议的差异，不能声称精确复现原实验。

计算口径固定如下：

1. 两组使用同一可用股票集合，先按 `instrument_id` 排序。历史原输入保留其原共同 cohort；自然 source 复用 `source_rows` 同时排除旧行情/缺失日期及全 17 特征缺失的股票，归档被排除身份、原因与覆盖率。部分缺失沿用原实现，不对每个变体另筛一套集合。
2. 复用 `prepare_features`，按输入声明的 `raw`/`neutralized` 处理，避免重复中性化。原始输入在完整共同横截面上预处理一次，再按两组特征顺序切列。
3. 复用 `load_frozen` 校验六模型，对每组先取三个种子的逐股预测均值，再按分数降序选股；同分按股票身份顺序处理。固定 `K = max(1, ceil(N × 0.10))`，不采用逐种子选股后的指标平均。
4. 记录交集数、交集/K、交集/并集，以及候选进入/退出的全部股票；逐股保留两组分数、排名和 `全特征排名 − 候选排名`（正值表示候选排名上升）。行业采用当时映射，缺失归入 unknown，行业权重以 K 为分母，不因缺失行业而删股；另报 unknown 比例、最大行业占比及 HHI。

这些是缺少输入时预先固定的真实截面计算口径，本轮尚未在市场样本上执行。只读推理与输入预处理已有实现，不新增预测器；新增的 `scripts/compare_g2_selections.py` 只消费现有 ready signal 中的配对预测，不调用推理、预处理、训练、网络或数据库。补齐真实 signal 后即可生成具体进出股票和行业表。

工具同时输出固定股票数 Top5、Top10，以及冻结比例对应的 Top10%。三者名称分开，Top10 指十只股票，不等于头部 10%，均只做同日行为描述，不产生收益指标。股票不足十只时保留 `requested_k` 与实际 `effective_k`。各表包含交集、进出股票（两边排名/分数）、共同股票、两组完整头部名单及行业分布；结果另保留全体配对排名和原 coverage。行业 unknown 单列，HHI 将 unknown 作为一个缺失桶，仅作诊断，不能解释为真实行业集中度。

工具校验 `result_digest`、协议、ready 状态、隔离标志、日期、覆盖行数、重复身份、两组字段、有限分数、1..N 排名及降序/同分身份排序一致性。结果摘要只能验证文件内容一致，不能证明来源真实性或独立前向资格；这些仍由既有 collector 及原始 source 证据负责。输出默认标准输出，指定 `--output` 时只创建新文件，拒绝覆盖现有归档。

## 本轮复核命令与状态

在仓库根目录使用现有本地环境执行以下有界校验；它不访问数据库、不训练、不写模型或注册候选：

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
from pathlib import Path
import numpy as np
from qagent.research.g2_risk_feature_forward import load_frozen

manifest, models = load_frozen(
    Path("data/archives/g2-risk-feature-ablation-20260910-frozen-v1")
)
print("manifest_sha256:", manifest["manifest_sha256"])
for name, group in models.items():
    for seed, model in zip((7, 19, 42), group, strict=True):
        values = model.predict(np.zeros((1, model.num_feature())))
        assert np.isfinite(values).all()
        print(name, seed, model.num_feature(), "load_and_finite_probe_ok")
PY
```

有真实 ready signal 后，在仓库根目录运行（两个路径均替换为实际路径；本轮没有这样的本地市场信号）：

```sh
backend/.venv/bin/python -B scripts/compare_g2_selections.py \
  --signal /absolute/g2-forward-results/signals/DATE.json \
  --output /absolute/new-selection-behavior.json
```

本轮 `backend/tests/test_compare_g2_selections.py` 的 **12 项测试通过（0.15 秒）**，覆盖完全相同/完全不同 Top、未知行业、较小共同集合、损坏摘要/身份/分数/排名/两组字段/coverage、非 ready 以及 CLI 拒绝覆盖。CLI 测试使用 Python `-S`，确认工具无需第三方推理依赖。这些全部是合成夹具，只验工具逻辑，**不能作为真实信号或选股效果结果**。两个新增 Python 文件 Ruff 通过，diff 空白检查通过。

本轮新增本报告、离线比较脚本及专项测试；已完成六模型摘要、加载和有限推理检查，未完成真实截面行为分析，未运行生产回归。未 commit、未 push、未部署，未改变模拟账户或生产风控。G2 前向验收状态不能因本报告或工具验证而升级。
