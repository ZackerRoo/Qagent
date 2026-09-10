# 既有研究模拟盘亏损归因（2026-09-10）

数据来自云端 `172.28.216.120` 的只读 `GET /api/paper-trades/ledger?reporting_scope=legacy`，会话 `paper-session-69470ca6b12c`。这是当日取得的快照，净值 95,935.24 元；后续行情会改变浮盈。122 条生命周期记录全部为 `legacy_unknown`，正式认证交易为 0，不能据此宣称正式策略业绩。

源 payload 的规范 JSON SHA-256：`2a452996ff80a8a0e44c1b27e83f9940b30df34326aa1810fbfcf69591fed657`（`sort_keys=True, separators=(",", ":"), ensure_ascii=False`，UTF-8）。此哈希标识本次完整源数据；报告保留结果，未另存完整含 notes 的 payload。

初始 100,000.00 − 已实现亏损 4,108.43 + 未实现收益 43.67 = 当前权益 95,935.24，总收益率 −4.0648%。122 条记录中，实际有现金流水的为 59 笔，其中 50 笔平仓、9 笔持仓；其余为替换 45、错过入场 11、失效 7，不能算作 122 笔成交。

## 亏损集中在哪里

|退出状态|实际交易笔数|净损益（元）|
|---|---:|---:|
|止损 stopped|31|−11,258.10|
|目标退出 target_1_hit|12|+6,976.90|
|时间退出 time_exit|7|+172.77|
|仍持仓 open|9|+43.67|
|合计|59|−4,064.76|

止损组损失超过目标退出和时间退出的盈利。这是按最终状态分组的描述，不能据此认定止损规则导致亏损，也不能推导放宽止损就会改善结果。

|入场月份|实际交易笔数（其中持仓）|已实现损益|含浮盈总损益|
|---|---:|---:|---:|
|2026-07|17（0）|−5,532.00|−5,532.00|
|2026-08|34（3）|+2,271.38|+2,378.87|
|2026-09|8（6）|−847.81|−911.63|

账面亏损主要留在 7 月入场批次，8 月部分修复。月份有不同交易机会、资金规模、规则版本与持仓成熟度，不能把这个横截面比较解释为新模型改善的因果证据。

|账本 holding_days 分组|实际交易笔数（其中持仓）|已实现损益|含浮盈总损益|
|---|---:|---:|---:|
|0–5|32（3）|−4,432.78|−4,511.42|
|6–10|11（3）|−1,167.57|−1,152.75|
|11+|16（3）|+1,491.92|+1,599.41|

持有期采用账本原字段，没有重新计算交易日；短持有期和止损有选择偏差，不能据此建议延长持有。

策略标签总损益：`trend_momentum_stage2` −3,355.21；`breakout_volume_confirmation` −1,505.86；`factor_rotation_watch` −988.83；缺失标签 −489.76；`healthy_pullback` +142.40；`tam_adj_peg_growth` +2,054.25；`bayesian_intrinsic_growth` +78.25。这些是历史标签分组，不是同期间、同风险的策略对照实验。

最大五笔净亏损均为止损：

|交易 ID|标的|净损益|
|---|---|---:|
|paper-b91a0189ad0d|CN:588200|−755.17|
|paper-68b9ee481927|CN:002955|−588.65|
|paper-112ba6617d43|CN:002612|−554.97|
|paper-7e9ade5d00df|CN:688278|−542.41|
|paper-0ea35b938397|CN:159321|−524.81|

最大两笔盈利都来自 CN:301211：`paper-d3bb6fc2c5e6` +1,877.35、`paper-588d61542050` +1,005.99，属于 `tam_adj_peg_growth`。该标签盈利存在单一标的集中，不能只看其总数为正。

## 成本和复核口径

手续费 391.29 元，滑点 406.49 元，合计 797.78 元；它们已进入净损益，不能再次扣减。作为固定成交集合的纯算术加回，−4,064.76 + 797.78 = −3,266.98，说明已报告成本不足以解释全部亏损；这不是无成本重新回放结果。成交事实模式的滑点体现在成交价格中，旧回退模式则直接进入现金流，详见 `backend/qagent/paper_trading/engine.py` 的 `_paper_execution_leg`、`_buy_lot`、`_sell_lot_transactions`。

复用现有 chronological cash ledger，新增 `backend/qagent/research/paper_loss_attribution.py` 仅消费其 JSON：按 trade_id 汇总真实 `transactions.cash_flow`，加上持仓 `market_value` 得到每笔净损益；不使用可能含指示性分配的 `items.total_pnl`。交易状态、策略、持有期来自 items。已实现、未实现、总损益、手续费、滑点五项均与 summary 精确相等，差额全部 0.00；初始资金加总损益也与权益相等。对账失败则脚本报错。

复现（仓库根目录；只读网络请求，脚本不写文件、数据库或调用行情提供商）：

```sh
ssh -o BatchMode=yes luozhenkun@172.28.216.120 "curl -fsS 'http://127.0.0.1:8000/api/paper-trades/ledger?reporting_scope=legacy'" | backend/.venv/bin/python backend/qagent/research/paper_loss_attribution.py
backend/.venv/bin/python -m pytest backend/tests/test_paper_loss_attribution.py -q
```

价格来源仍为 `paper_trade_latest_fields`，本报告没有独立验证行情。未修改交易、资金账本、选股、风控、配置或部署；没有请求 scheduler。该归因首先支持检查 7 月亏损批次和短期止损交易的入场证据，不能直接授权任何实盘或模拟盘规则调整。

验证：17 项测试通过，覆盖多条卖出流水归并、未成交记录排除、金额精确对账、重复 ID、非有限金额、成交数量不一致和输入不变性；云端当日源数据再次运行通过全部对账。
