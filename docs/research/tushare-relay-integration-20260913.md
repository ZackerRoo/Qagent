# Tushare 双服务全目录验证与 Qagent 接入（2026-09-13）

本报告以用户提供的 [基础功能手册](../15000积分基础功能.txt) 和 [ProMax 手册](../promax.txt) 全文为契约来源；两份手册是供应方声明，实际能力、请求成功、数据质量和生产启用分别验收。[早期四接口验证](tushare-relay-validation-20260913.md) 保留为本日此前快照，不能代表本轮扩展验证结果。

## 目录核对与协议差异

| 范围 | 完整盘点结果 | 含义 |
| --- | --- | --- |
| 基础功能总览与逐接口正文 | 80 个唯一 API、80 节正文 | 结尾提到原始清单 81 行，其中 index_daily 重复；提供的总览已去重，不能再次减一 |
| 基础功能分类 | 股票 20、财务 10、基金 7、期货 6、指数 8、期权 2、债券 3、外汇 2、港股 1、宏观 7、行业特色 8、特色 6 | 附录逐一列出；接口覆盖远超四项行情 |
| 基础功能额外声明 | adj_factor、stock_basic | 仅出现在结尾“9 个本地接口族”声明，未在 80 项总览中；需单列，不能假装有逐接口契约 |
| ProMax 文档表格 | 实际 271 项，258 可用、13 未启用；269 GET、1 POST、1 DELETE | 标题 298、页首 257 已启用都不能代替逐行结果；表中 adj_factor/news_cctv 等行列不齐，不能用固定列数丢行 |
| 两份文档表格集合 | 交集 72、并集 279 | 基础独有 8 项：tmt_twincome、tmt_twincomedetail、bo_monthly、bo_weekly、bo_daily、bo_cinema、film_record、teleplay_record；额外声明两项已在 ProMax 表中 |
| 09-13 主任务实际认证目录 | HTTP 200，count=298、实际 entries=298；enabled=259、disabled=39 | 这是当前机器目录快照，不是 298 项业务数据成功；与手册差异应保留并按实时元数据校验 |

ProMax 文档标记未启用的 13 项为 `concept_board`、`dc_index_prev`、`fund_announcement_report_em`、`fut_level2`、`index_min`、`realtime_tick`、`rt_fut_level2`、`rt_fut_min_daily`、`rt_fut_min_health`、`rt_fut_ticks`、`rt_hk_k`、`rt_tick`、`us_depth`。实时目录声明 39 项禁用，实际请求必须采用该目录禁用集合，不按旧手册只跳过 13 项。

基础服务路径为 `/app-api/openapi/v1/tushare/{api}`，提供的入口仍声明 HTTP；主任务本轮对 HTTPS 443 做无密钥连接检查，293 ms 后连接失败、HTTP 000，未向 HTTP 发送密钥。供应方需提供可验证证书的 HTTPS 入口或修复 443 服务，之后再验基础账户业务调用。此结论是本机本次连接结果，不宣称任何地点都不可达。

ProMax 使用 `https://pcd.mobcvb.cn/tushare/pro/{api}`，能力目录在 `/tushare/capabilities`。两个账户的密钥均应仅在请求头传输、分开配置；Relay key 不是官方 Tushare token。`pro_bar` 在基础手册明确不支持 HTTP，ProMax 则声明本地聚合支持，必须按服务分别处理。基础服务通常要求成对紧凑日期，ProMax 允许日线短横线日期和分钟完整时间；新闻、月度行业统计、财报期均有不同时间语义。

## 接入范围与复用位置

“接入全部能力”分为三层：目录层完整记录接口、方法、启用状态和参数；只读客户端可调用已允许的 GET 并返回带来源的原始表格；Qagent 消费层将实际使用的行情、财务和事件转换为既有模型。目录存在不等于每个 API 都有专用消费者，更不等于已启用生产。`p_save`/`p_delete` 是远端组合写操作，目录可保留，数据源验证不调用；远端组合不能替代 Qagent 唯一模拟账本。

现有 `backend/qagent/providers/base.py` 定义日线、快照和分钟三种行情 getter；`strategy_data/providers.py` 已有财务、盈利事件、公告、filings 和分析师接口，原 `TushareStrategyDataProvider` 仅保存 token、继承空实现，不能作为已经接通 Tushare 的证据。`historical_evidence/providers.py` 已有历史股票池、基准、公司行动、行业和交易状态证据接口；`catalysts/providers.py` 已有新闻提供者。按这些接口复用，避免另建账户、实验库或完整数据平台。

| 能力 | 文档 API 示例 | Qagent 用处与验收重点 |
| --- | --- | --- |
| 日线、复权、基准 | daily、adj_factor、pro_bar、index_daily、fund_daily、fund_adj、weekly、monthly | G2/G3 历史价格、标签与基准；原始/前复权/后复权口径和锚点明确，因子必须同股票同日期配对，不放宽既有价格质量检查 |
| 盘中行情与历史分钟 | rt_k、rt_min、rt_min_daily、stk_mins、a_share_mins | G7 更新及时性候选来源；日期、市场时区、实际行情时间、OHLC、频率及完整性通过后才能谈时效 |
| 股票池与交易日历 | stock_basic、trade_cal、namechange、stock_st、suspend_d、stk_limit | 标的生命周期、交易日和真实停牌/涨跌停证据；空表不得直接当成停牌，当前股票池不得冒充历史股票池 |
| 估值、财报、业绩事件 | daily_basic、income、balancesheet、cashflow、fina_indicator、forecast、express、disclosure_date、dividend | 复用 StrategyDataProvider；区分报告期与公告/实际公告日，以决策时已知信息为准，缺失值不填零，不改冻结特征 |
| 新闻公告、分析师和问答 | news、major_news、express_news、anns_d、report_rc、research_report、irm_qa_sh、irm_qa_sz | 复用公告/分析师/催化剂模型；保留发布与获取时间、身份去重、来源、股票关联，不把抓取日当作事件日 |
| 资金和交易行为 | moneyflow 及其变体、margin、margin_detail、top_list、top_inst、block_trade、hm_detail、limit_list_d | 研究与风险解释；单位、净额方向、资金口径、盘后公布时间逐项确认，各供应商同名指标不自动混合 |
| 股东、质押、回购、解禁 | top10_holders、top10_floatholders、stk_holdernumber、stk_holdertrade、pledge_stat、pledge_detail、repurchase、share_float | 研究事件与已有暴露分析；公告日、报告期、实施日分开，当前状态不回填历史决策 |
| 行业、主题、指数、ETF | index_weight、index_classify、index_member_all、concept/detail、ths_*、dc_*、fund_portfolio、etf_* | 复用行业/主题/ETF 重叠约束与基准；历史生效时间、权重单位和分页完整性必须能复核 |
| 特色因子与筹码 | stk_factor_pro、stk_factor、cyq_perf、cyq_chips、stk_nineturn、factor_list、factor_value | 可读取研究资料；不因可用就进入冻结模型或宣称提升选股效果 |
| 宏观与行业数据 | cn_*、shibor、shibor_lpr、sf_month、tmt_twincome、bo_*、film_record | 补充研究背景；频率、发布日期和关联机制需明确，不能直接解释为交易信号 |
| 港美股、期货、期权、外汇、债券 | hk_*、us_*、fut_*、opt_*、fx_*、cb_*、sge_* | 全目录只读访问与覆盖记录；不自动扩大当前 A 股模拟账户交易市场或启用衍生品交易 |

## 分钟数据必须分开的验收

主任务当前读到 `a_share_mins`、`stk_mins`、`rt_min_daily` 的能力缓存 TTL 为 300 秒，`rt_k` 为 5 秒、`rt_min` 为 10 秒，均是服务端配置声明。TTL 不是实测行情延迟，也不是“每 1 分钟必更新”承诺。

历史分钟以固定过去交易日、小股票集合和明确时间窗口验证：需有真实行，股票和日期命中，时间可解析且单调去重，OHLC 为有限正数并满足高低边界，成交量单位明确。服务端可能由 5 分钟派生高周期，不得反向声称已经取得原生 1 分钟。202 或 minute_data_pending 表示补数未完成，不能算通过；截断或仅返回样本不算区间完整。

实时分钟/快照以交易时段当日数据验证：记录请求时刻、响应时刻、数据自身时间和缓存头；至少跨越缓存窗口观察数据时间是否前进，再统计有效行情年龄与请求耗时。`rt_k` 快照不是历史分钟，`rt_min_daily` 当日分钟不是多年历史。2026-09-13 为周日，本轮可验接口契约和历史样本，不能完成交易时段实时性验收。成功也不单独证明 G7 调度拆分完成。

## 覆盖验证与验收口径

1. 对机器目录和文档表格分别记录全部名字、方法、enabled 和来源版本；输出交并集和缺失，不把名字差异默默忽略。
2. 全目录只读 probe 使用本地样本语义，显式 `__probe=1`，按 IP 探测上限做预算；仅能验证路由、认证、结构和本地样本。未启用/写方法明确 skipped，不循环重试。
3. 有用能力的正式业务请求必须明确代码、日期/报告期/新闻时段等筛选条件，并设置 `__probe=0`；纯 fields/limit/offset 不构成业务条件。无筛选型 API 也需显式关闭 probe。
4. 分开记录 sample_valid、empty、pending、auth/rate/transport/upstream/schema/quality error；HTTP 200 与 code 0 不足以证明业务适用。分页有最大页数、行数、总时长预算和重复页检测；请求上限触发应报告不完整，不能假报全量。
5. 归一化消费者通过隔离测试验证代码映射、字段顺序、单位、复权、公告时间、失败降级；未通过的数据不能进入可信价格或触发模拟成交。所有研究输入保留提供者、获取时间和质量信息。
6. 完成代码与测试后，分别记录是否 push、是否部署、是否配置启用，以及真实请求结果。生产开启前还需实际可用数据和现有安全边界验收；本轮不凭接口覆盖提升 G1/G2/G3/G7 完成状态。

## 本轮交付状态

最简使用：隔离审计脚本 `scripts/audit_tushare_relay.py` 从环境变量 `TUSHARE_RELAY_KEY` 读取凭据，可用 `--catalog` 只查目录；研究 Settings 使用 `QAGENT_TUSHARE_RELAY_KEY`（SecretStr）和 `QAGENT_TUSHARE_RELAY_RESEARCH_ENABLED=true`。设置研究开关后仍需在研究代码中显式调用 `qagent.providers.tushare_relay_research.build_tushare_relay_research_provider()`，不会接入原默认 provider 工厂，也不会令既有日常 job 自动使用该源。上述为使用入口说明，本轮未配置生产凭据或部署。

文档目录盘点、全目录只读探测、上述正式业务样本及归一化日线/基本面 smoke 已完成；迁移后 134 项相关回归通过（2.23 秒），最终全量 2171 passed、3 项既有 warnings（237.88 秒、exit 0），Ruff 与 diff 检查通过，已完成本地实现验收。基础服务 HTTPS 连接未通过，分钟、交易时段实时性和全业务覆盖尚未验收；G3/G7 不升级完成。未 commit、未 push、未部署、未启用默认源。

主任务后续以 `000001.SZ`、`20260911`、`limit=5`、30 秒超时正式复查：`adj_factor` HTTP 200、code 0、1 行、因子 139.008，耗时 945 ms，更新了早期传输失败快照；`a_share_mins` 的 5 分钟 09:30–10:00 请求 HTTP 503，耗时 2,559 ms，错误种类未明确；同范围 `stk_mins` HTTP 202、`minute_data_pending`，耗时 2,871 ms。前者是因子单样本读取成功，后两者尚未获得可验分钟；不能由补数状态宣称成功。

## 09-13 后续正式业务请求：17 项

主任务以 `000001.SZ`、行情日期 `20260911`、财报期 `20260630`、`limit=5`、单次超时 30 秒、最多 3 次尝试执行。此版本尚未加入 `rt_k`，因此下表不能算作 `rt_k` 实测。进程退出 0 只表示目录成功，不代表所有请求通过。

| API | HTTP | 行数 | 尝试次数 | 实际分类与限制 |
| --- | --- | --- | --- | --- |
| daily | 200 | 1 | 1 | business_sample_valid，代码/日期/OHLC 单样本校验通过 |
| adj_factor | 200 | 1 | 1 | business_sample_valid，代码/日期/正因子单样本校验通过 |
| moneyflow | 200 | 1 | 1 | valid_shape，仅非空表结构通过 |
| daily_basic | 200 | 1 | 2 | valid_shape，仅非空表结构通过 |
| stock_basic | 200 | 5 | 1 | valid_shape，仅非空表结构通过，limit=5 不代表全股票池 |
| trade_cal | 200 | 1 | 2 | valid_shape，仅非空表结构通过 |
| income | 200 | 2 | 2 | valid_shape，仅非空表结构通过 |
| balancesheet | 200 | 2 | 1 | valid_shape，仅非空表结构通过 |
| cashflow | 200 | 1 | 2 | valid_shape，仅非空表结构通过 |
| fina_indicator | 200 | 1 | 2 | valid_shape，仅非空表结构通过 |
| get_industries | 200 | 5 | 2 | valid_shape，仅非空表结构通过，limit=5 不代表完整行业目录 |
| get_index_stocks | 200 | 0 | 2 | 空结果，未获得业务样本 |
| index_daily | 503 | — | 3 | 未通过；不能用股票日线成功替代基准验收 |
| a_share_mins | 503 | — | 3 | 未获得历史分钟样本 |
| stk_mins | 202 | — | 1 | minute_data_pending，后台补数尚未验收 |
| rt_min | 200 | 1 | 2 | 返回 20260911 行；与查询当日 20260913 不同，date_mismatch，周日不能据此判断盘中错误或通过实时验收 |
| rt_min_daily | 503 | — | 3 | 未获得实时分钟样本 |

合计 11 项获得非空且通过对应结构/价格检查的返回，其中仅 daily/adj_factor 通过专门业务单样本校验；其余 9 项尚无字段业务语义、完整覆盖或 point-in-time 验收。另有 rt_min 返回上一交易日 1 行，但未通过当日实时校验。此轮财务原始表成功不等于研究模型已完整接收，分钟和实时性均保持待验。

主任务在此套件之外单独正式调用 `rt_k`（`ts_code=000001.SZ`、`__probe=0`、`limit=5`），取得 HTTP 503、`upstream_pool_exhausted`；再次调用 `a_share_mins` 的 5 分钟 09:30–10:00 窗口也返回相同机器错误。因此累计正式尝试过 18 种 API，但 17 项套件仍为独立批次，不能混算为该套件 18 项。错误码可供服务商排查上游池，尚不证明池耗尽的具体根因，也不代表分钟已经可用。

## 附录：两份手册逐 API 覆盖台账

### 后续运行接线授权：模拟盘与日常任务（2026-09-13，本地验收通过）

用户已明确授权将 Relay 用于模拟盘和日常任务，前文“研究专用、默认工厂未接线”保留为此前交付快照。本轮实施市场日线后备，由独立的 `QAGENT_TUSHARE_RELAY_MARKET_ENABLED` 控制，默认 `false`，还需 `QAGENT_TUSHARE_RELAY_KEY`；既有研究开关不能自动打开运行消费者。密钥只通过环境配置，不写入文档、测试产物或状态返回。

运行路径保持原有 CN 主源及后备顺序，仅在所有既有来源均未返回某只股票日线时调用 Relay；每次最多 2 只股票、一个批次，不补已有股票内部日期，不覆盖原源成功数据。请求超时 5 秒且零重试，批次启动预算 30 秒；首次上游或结构错误停止剩余批次并熔断 300 秒。预算和熔断用于限制异常源对日常任务的延迟，不能解释成所有股票/区间已有覆盖。

主任务 review 发现逐请求选择最新日线因子会让增量缓存混用不同前复权锚点，因此本轮运行后备仅查询原始 `daily`，不查询因子。逐行校验身份、日期、有限正 OHLC、高低范围、重复行及非负成交字段，保留原始 OHLC；调整价留空、类型 `raw`，来源标记 `tushare_relay_promax_daily_raw`。`vol` 从手乘 100 转为股，`amount` 从千元乘 1000 转为元，单位依据 [Tushare 官方 daily 文档](https://tushare.pro/document/1?doc_id=27)；该契约不外推到分钟接口。上海时间 15:00 前排除当日日线，周日成功样本仍不能证明交易时段更新延迟或收盘后立即完整。带显式锚点的日线/因子配对仍保留在独立研究入口，不能把原始运行后备算作 G2 复权价格补齐。

Relay 不提供运行分钟/快照，继续使用原提供者路径。分钟真实频率、完整区间、单位和新鲜度尚未通过，故本次接线不代表分钟可用于模拟成交；现有日线降级语义保留。新原始日线可进入基本日线与模拟盘日线消费者，可能改变未来输入和结果；不补 G2 复权价格缺口，不接基本面，不修改冻结模型、规则、唯一账本历史或增加账户，也不替代 G7 调度拆分验收。

本轮主任务已接受原始日线实现并完成本地测试验收，覆盖默认关闭/缺凭据、原源优先、缺股限额、错误熔断、原始价格及单位、分钟委托、配置状态脱敏及相关回归；真实可用性和自然运行另行验收。未 commit、未 push、未部署、未配置云端启用。主任务本轮只读确认云端当前 release `94cf6f5057723460a88becd0c5e44f864a6cc53c` 健康、backend/frontend runit 运行，这是旧版健康快照，不代表本轮接线已生效。只读发布 preflight 因 `automation scheduler is enabled` 退出 1，未停止调度、重启或部署，受控发布待完成。上文历史测试结果不计为本轮测试；G3/G7 继续未完成。

新运行适配器真实 smoke 已由主任务执行：`CN:000001` / `2026-09-11`，5 秒超时、零重试，0 行、`transport_error`。脚本退出 0 表示安全返回失败状态，不能作为可用数据通过；本轮不因此启用云端数据源，云端保持原状。

主任务本轮全量后端回归 **2215 passed、3 项既有 warnings（231.82 秒）**；收集发生在新运行测试文件定稿前，因此不能宣称该次全量已包含最终全部新增测试。最终相关回归 **107 passed、1 项 warning（2.42 秒）**，包含 18 项运行适配专项；reviewer 独立复核同 18 项通过，Ruff/diff 检查通过。研究摘要仍为 `309625c7be262e81bc22c35f40f563f7c497d78252ac6b4dfabd0e168642c848`。已实现并通过上述本地验收，未 commit、未 push、未部署、未启用云端；真实取数可用性仍待验。

### 09-13 全目录只读 probe 与归一化进展

机器目录 298 项 = 259 启用 + 39 禁用。原版本安全筛选对 252 项进行了 `__probe=1` 请求，全部 HTTP 200、`no_rows`；随后补查公开数据接口 `fund_portfolio` 也是 HTTP 200、`no_rows`，累计 253 个独立 API 已探测。`no_rows` 表示网关本地 probe 没有样本，不表示正式业务无数据或请求失败；本轮 probe 退出 1 是未取得已验样本，不是传输失败。不能宣称 253 项业务已通过。

其余 6 项启用接口未探测：`p_delete`、`p_get`、`p_list`、`p_save` 为组合管理；`stk_account`、`stk_account_old` 因名字含 account 被保守排除，尚未判断实际数据语义和可用性，不据名字认定它们是客户账户。39 项禁用接口没有发送请求。因此整个目录都有已探测/禁用跳过/保守跳过的去向，但业务数据覆盖仍按前述正式样本记录。

归一化入口已在主任务真实请求中产出 1 条 daily bar。基本面首次因同报告期修订歧义未输出；进一步核对 `20260630` 报告期、`20260815` 公告日的两条候选，其实际消费字段 tr_yoy/netprofit_yoy/grossprofit_margin/netprofit_margin/roe 完全一致，仅 15 个未消费字段不同。修复后真实 smoke 返回 1 个 snapshot，has_growth=True、has_valuation=True，asof=2026-09-13、valuation_date=2026-09-11、financial_period=2026-06-30，并保留 `unused_field_revision_difference` 提示；这是当前研究快照成功，不是历史 point-in-time 全覆盖或冻结模型效果验证。

最终研究入口为 `backend/qagent/providers/tushare_relay_research.py` 的 `build_tushare_relay_research_provider`，必须显式 opt-in；原 `strategy_data` 默认工厂不接线，该目录无改动，保留 config/status 能力。如此隔离避免仅添加默认关闭代码也改变冻结研究指纹。主任务复算 144 个研究源文件/依赖，当前与 HEAD 摘要完全一致：`309625c7be262e81bc22c35f40f563f7c497d78252ac6b4dfabd0e168642c848`。所有适配默认关闭，未启用云端或修改唯一模拟账本。

前版相关回归 126 passed，之后 134 项相关回归通过，迁移完成后同样 134 passed（2.23 秒）。首轮全量 2168 passed、1 failed，失败为 manifest 不兼容，此处保留失败快照；调整隔离后冻结代码重跑，最终全量 2171 passed、3 项既有 warnings（237.88 秒、exit 0），Ruff 与 diff 检查通过。本地实现验收完成，不能由测试通过推断所有上游业务数据可用；未 commit、未 push、未部署、未启用默认源。

以下按提供文档逐行提取，记录文档声明而非实测结果。基础目录路径中的“上游/缓存”等也只表示供应方描述。机器目录 298 项及 39 禁用项由本轮探测产物另行记录。

### 基础功能：80 项

| API | 分类 | 文档路径声明 |
| --- | --- | --- |
| `daily` | 股票 | 本地 + Redis |
| `weekly` | 股票 | 上游/缓存 |
| `monthly` | 股票 | 上游/缓存 |
| `pro_bar` | 股票 | HTTP 不支持 |
| `daily_basic` | 股票 | 本地 + Redis |
| `new_share` | 股票 | 上游/缓存 |
| `top_list` | 股票 | 上游/缓存 |
| `top_inst` | 股票 | 上游/缓存 |
| `pledge_detail` | 股票 | 上游/缓存 |
| `pledge_stat` | 股票 | 上游/缓存 |
| `margin` | 股票 | 上游/缓存 |
| `margin_detail` | 股票 | 上游/缓存 |
| `repurchase` | 股票 | 上游/缓存 |
| `share_float` | 股票 | 上游/缓存 |
| `block_trade` | 股票 | 上游/缓存 |
| `stk_holdernumber` | 股票 | 上游/缓存 |
| `moneyflow` | 股票 | 上游/缓存 |
| `stk_holdertrade` | 股票 | 上游/缓存 |
| `stk_limit` | 股票 | 上游/缓存 |
| `hk_hold` | 股票 | 上游/缓存 |
| `income` | 财务 | 上游/缓存 |
| `balancesheet` | 财务 | 上游/缓存 |
| `cashflow` | 财务 | 上游/缓存 |
| `forecast` | 财务 | 上游/缓存 |
| `express` | 财务 | 上游/缓存 |
| `dividend` | 财务 | 上游/缓存 |
| `fina_indicator` | 财务 | 上游/缓存 |
| `fina_audit` | 财务 | 上游/缓存 |
| `fina_mainbz` | 财务 | 上游/缓存 |
| `disclosure_date` | 财务 | 上游/缓存 |
| `fund_basic` | 基金 | 上游/缓存 |
| `fund_company` | 基金 | 上游/缓存 |
| `fund_nav` | 基金 | 上游/缓存 |
| `fund_daily` | 基金 | 本地 + Redis |
| `fund_div` | 基金 | 上游/缓存 |
| `fund_portfolio` | 基金 | 上游/缓存 |
| `fund_adj` | 基金 | 上游/缓存 |
| `fut_basic` | 期货 | 上游/缓存 |
| `trade_cal` | 期货 | 本地 + Redis |
| `fut_daily` | 期货 | 上游/缓存 |
| `fut_holding` | 期货 | 上游/缓存 |
| `fut_wsr` | 期货 | 上游/缓存 |
| `fut_settle` | 期货 | 上游/缓存 |
| `index_daily` | 指数 | 本地 + Redis |
| `opt_basic` | 期权 | 上游/缓存 |
| `opt_daily` | 期权 | 上游/缓存 |
| `cb_basic` | 债券 | 上游/缓存 |
| `cb_issue` | 债券 | 上游/缓存 |
| `cb_daily` | 债券 | 本地 + Redis |
| `fx_obasic` | 外汇 | 上游/缓存 |
| `fx_daily` | 外汇 | 上游/缓存 |
| `index_basic` | 指数 | 上游/缓存 |
| `index_weekly` | 指数 | 上游/缓存 |
| `index_monthly` | 指数 | 上游/缓存 |
| `index_weight` | 指数 | 上游/缓存 |
| `index_dailybasic` | 指数 | 本地 + Redis |
| `index_classify` | 指数 | 上游/缓存 |
| `index_member_all` | 指数 | 上游/缓存 |
| `hk_basic` | 港股 | 上游/缓存 |
| `shibor` | 宏观 | 上游/缓存 |
| `shibor_quote` | 宏观 | 上游/缓存 |
| `shibor_lpr` | 宏观 | 上游/缓存 |
| `libor` | 宏观 | 上游/缓存 |
| `hibor` | 宏观 | 上游/缓存 |
| `wz_index` | 宏观 | 上游/缓存 |
| `gz_index` | 宏观 | 上游/缓存 |
| `tmt_twincome` | 行业特色 | 上游/缓存 |
| `tmt_twincomedetail` | 行业特色 | 上游/缓存 |
| `bo_monthly` | 行业特色 | 上游/缓存 |
| `bo_weekly` | 行业特色 | 上游/缓存 |
| `bo_daily` | 行业特色 | 上游/缓存 |
| `bo_cinema` | 行业特色 | 上游/缓存 |
| `film_record` | 行业特色 | 上游/缓存 |
| `teleplay_record` | 行业特色 | 上游/缓存 |
| `report_rc` | 特色 | 上游/缓存 |
| `cyq_perf` | 特色 | 上游/缓存 |
| `cyq_chips` | 特色 | 上游/缓存 |
| `stk_rewards` | 特色 | 上游/缓存 |
| `stk_factor_pro` | 特色 | 上游/缓存 |
| `stk_nineturn` | 特色 | 上游/缓存 |

### ProMax：271 项

| API | 方法 | 文档状态 |
| --- | --- | --- |
| `a_share_mins` | GET | 可用 |
| `adj_factor` | GET | 可用 |
| `anns_d` | GET | 可用 |
| `bak_basic` | GET | 可用 |
| `bak_daily` | GET | 可用 |
| `balancesheet` | GET | 可用 |
| `bc_bestotcqt` | GET | 可用 |
| `bc_otcqt` | GET | 可用 |
| `block_trade` | GET | 可用 |
| `bond_blk` | GET | 可用 |
| `bond_blk_detail` | GET | 可用 |
| `broker_recommend` | GET | 可用 |
| `bse_mapping` | GET | 可用 |
| `cashflow` | GET | 可用 |
| `cb_basic` | GET | 可用 |
| `cb_call` | GET | 可用 |
| `cb_daily` | GET | 可用 |
| `cb_factor_pro` | GET | 可用 |
| `cb_issue` | GET | 可用 |
| `cb_price_chg` | GET | 可用 |
| `cb_rate` | GET | 可用 |
| `cb_rating` | GET | 可用 |
| `cb_share` | GET | 可用 |
| `cctv_news` | GET | 可用 |
| `ci_daily` | GET | 可用 |
| `ci_index_member` | GET | 可用 |
| `cn_cpi` | GET | 可用 |
| `cn_gdp` | GET | 可用 |
| `cn_m` | GET | 可用 |
| `cn_pmi` | GET | 可用 |
| `cn_ppi` | GET | 可用 |
| `cn_schedule` | GET | 可用 |
| `concept` | GET | 可用 |
| `concept_board` | GET | 未启用 |
| `concept_detail` | GET | 可用 |
| `cyq_chips` | GET | 可用 |
| `cyq_perf` | GET | 可用 |
| `daily` | GET | 可用 |
| `daily_basic` | GET | 可用 |
| `daily_info` | GET | 可用 |
| `dc_concept` | GET | 可用 |
| `dc_concept_cons` | GET | 可用 |
| `dc_daily` | GET | 可用 |
| `dc_hot` | GET | 可用 |
| `dc_index` | GET | 可用 |
| `dc_index_prev` | GET | 未启用 |
| `dc_member` | GET | 可用 |
| `disclosure_date` | GET | 可用 |
| `dividend` | GET | 可用 |
| `eco_cal` | GET | 可用 |
| `etf_basic` | GET | 可用 |
| `etf_index` | GET | 可用 |
| `etf_mins` | GET | 可用 |
| `etf_sh_cons` | GET | 可用 |
| `etf_share_size` | GET | 可用 |
| `etf_sz_cons` | GET | 可用 |
| `express` | GET | 可用 |
| `express_news` | GET | 可用 |
| `factor_list` | GET | 可用 |
| `factor_value` | GET | 可用 |
| `fina_audit` | GET | 可用 |
| `fina_indicator` | GET | 可用 |
| `fina_mainbz` | GET | 可用 |
| `fina_mainbz_vip` | GET | 可用 |
| `forecast` | GET | 可用 |
| `ft_limit` | GET | 可用 |
| `ft_mins` | GET | 可用 |
| `fund_adj` | GET | 可用 |
| `fund_announcement_report_em` | GET | 未启用 |
| `fund_basic` | GET | 可用 |
| `fund_company` | GET | 可用 |
| `fund_daily` | GET | 可用 |
| `fund_div` | GET | 可用 |
| `fund_factor_pro` | GET | 可用 |
| `fund_manager` | GET | 可用 |
| `fund_min` | GET | 可用 |
| `fund_nav` | GET | 可用 |
| `fund_portfolio` | GET | 可用 |
| `fund_share` | GET | 可用 |
| `fut_basic` | GET | 可用 |
| `fut_daily` | GET | 可用 |
| `fut_holding` | GET | 可用 |
| `fut_index_daily` | GET | 可用 |
| `fut_level2` | GET | 未启用 |
| `fut_mapping` | GET | 可用 |
| `fut_settle` | GET | 可用 |
| `fut_trade_cal` | GET | 可用 |
| `fut_weekly_detail` | GET | 可用 |
| `fut_weekly_monthly` | GET | 可用 |
| `fut_wsr` | GET | 可用 |
| `fx_daily` | GET | 可用 |
| `fx_mins` | GET | 可用 |
| `fx_obasic` | GET | 可用 |
| `get_all_securities` | GET | 可用 |
| `get_index_stocks` | GET | 可用 |
| `get_index_weights` | GET | 可用 |
| `get_industries` | GET | 可用 |
| `get_industry_stocks` | GET | 可用 |
| `get_trade_days` | GET | 可用 |
| `gz_index` | GET | 可用 |
| `hibor` | GET | 可用 |
| `hk_adj_factor` | GET | 可用 |
| `hk_balancesheet` | GET | 可用 |
| `hk_basic` | GET | 可用 |
| `hk_cashflow` | GET | 可用 |
| `hk_daily` | GET | 可用 |
| `hk_fina_indicator` | GET | 可用 |
| `hk_hold` | GET | 可用 |
| `hk_income` | GET | 可用 |
| `hk_mins` | GET | 可用 |
| `hk_monthly` | GET | 可用 |
| `hk_weekly` | GET | 可用 |
| `hm_detail` | GET | 可用 |
| `hm_list` | GET | 可用 |
| `hsgt_top10` | GET | 可用 |
| `idx_anns` | GET | 可用 |
| `idx_factor_pro` | GET | 可用 |
| `idx_mins` | GET | 可用 |
| `income` | GET | 可用 |
| `index_basic` | GET | 可用 |
| `index_classify` | GET | 可用 |
| `index_daily` | GET | 可用 |
| `index_dailybasic` | GET | 可用 |
| `index_global` | GET | 可用 |
| `index_member` | GET | 可用 |
| `index_member_all` | GET | 可用 |
| `index_min` | GET | 未启用 |
| `index_monthly` | GET | 可用 |
| `index_weekly` | GET | 可用 |
| `index_weight` | GET | 可用 |
| `irm_qa_sh` | GET | 可用 |
| `irm_qa_sz` | GET | 可用 |
| `kpl_concept_cons` | GET | 可用 |
| `kpl_list` | GET | 可用 |
| `libor` | GET | 可用 |
| `limit_cpt_list` | GET | 可用 |
| `limit_list_d` | GET | 可用 |
| `limit_list_ths` | GET | 可用 |
| `limit_step` | GET | 可用 |
| `major_news` | GET | 可用 |
| `margin` | GET | 可用 |
| `margin_detail` | GET | 可用 |
| `margin_secs` | GET | 可用 |
| `mkt_idx_bmk` | GET | 可用 |
| `monetary_policy` | GET | 可用 |
| `moneyflow` | GET | 可用 |
| `moneyflow_cnt_ths` | GET | 可用 |
| `moneyflow_dc` | GET | 可用 |
| `moneyflow_hsgt` | GET | 可用 |
| `moneyflow_ind_dc` | GET | 可用 |
| `moneyflow_ind_ths` | GET | 可用 |
| `moneyflow_mkt_dc` | GET | 可用 |
| `moneyflow_ths` | GET | 可用 |
| `monthly` | GET | 可用 |
| `namechange` | GET | 可用 |
| `new_share` | GET | 可用 |
| `news` | GET | 可用 |
| `news_cctv` | GET | 可用 |
| `npr` | GET | 可用 |
| `opt_basic` | GET | 可用 |
| `opt_daily` | GET | 可用 |
| `opt_mins` | GET | 可用 |
| `p_delete` | DELETE | 可用 |
| `p_get` | GET | 可用 |
| `p_list` | GET | 可用 |
| `p_save` | POST | 可用 |
| `pledge_detail` | GET | 可用 |
| `pledge_stat` | GET | 可用 |
| `pro_bar` | GET | 可用 |
| `realtime_tick` | GET | 未启用 |
| `repo_daily` | GET | 可用 |
| `report_rc` | GET | 可用 |
| `repurchase` | GET | 可用 |
| `research_report` | GET | 可用 |
| `rt_etf_k` | GET | 可用 |
| `rt_etf_min` | GET | 可用 |
| `rt_etf_min_daily` | GET | 可用 |
| `rt_etf_sz_iopv` | GET | 可用 |
| `rt_fut_level2` | GET | 未启用 |
| `rt_fut_min` | GET | 可用 |
| `rt_fut_min_daily` | GET | 未启用 |
| `rt_fut_min_health` | GET | 未启用 |
| `rt_fut_ticks` | GET | 未启用 |
| `rt_hk_k` | GET | 未启用 |
| `rt_idx_k` | GET | 可用 |
| `rt_idx_min` | GET | 可用 |
| `rt_k` | GET | 可用 |
| `rt_min` | GET | 可用 |
| `rt_min_daily` | GET | 可用 |
| `rt_sw_k` | GET | 可用 |
| `rt_tick` | GET | 未启用 |
| `sf_month` | GET | 可用 |
| `sge_basic` | GET | 可用 |
| `sge_daily` | GET | 可用 |
| `share_float` | GET | 可用 |
| `shibor` | GET | 可用 |
| `shibor_lpr` | GET | 可用 |
| `shibor_quote` | GET | 可用 |
| `slb_len` | GET | 可用 |
| `slb_len_mm` | GET | 可用 |
| `slb_sec` | GET | 可用 |
| `slb_sec_detail` | GET | 可用 |
| `st` | GET | 可用 |
| `stk_account` | GET | 可用 |
| `stk_account_old` | GET | 可用 |
| `stk_ah_comparison` | GET | 可用 |
| `stk_alert` | GET | 可用 |
| `stk_auction` | GET | 可用 |
| `stk_auction_c` | GET | 可用 |
| `stk_auction_o` | GET | 可用 |
| `stk_auction_replay` | GET | 可用 |
| `stk_factor` | GET | 可用 |
| `stk_factor_pro` | GET | 可用 |
| `stk_high_shock` | GET | 可用 |
| `stk_holdernumber` | GET | 可用 |
| `stk_holdertrade` | GET | 可用 |
| `stk_limit` | GET | 可用 |
| `stk_managers` | GET | 可用 |
| `stk_mins` | GET | 可用 |
| `stk_nineturn` | GET | 可用 |
| `stk_premarket` | GET | 可用 |
| `stk_rewards` | GET | 可用 |
| `stk_shock` | GET | 可用 |
| `stk_surv` | GET | 可用 |
| `stk_week_month_adj` | GET | 可用 |
| `stk_weekly_monthly` | GET | 可用 |
| `stock_basic` | GET | 可用 |
| `stock_company` | GET | 可用 |
| `stock_hsgt` | GET | 可用 |
| `stock_st` | GET | 可用 |
| `suspend_d` | GET | 可用 |
| `sw_daily` | GET | 可用 |
| `sw_mins` | GET | 可用 |
| `sz_daily_info` | GET | 可用 |
| `tdx_daily` | GET | 可用 |
| `tdx_index` | GET | 可用 |
| `tdx_member` | GET | 可用 |
| `the_member` | GET | 可用 |
| `ths_daily` | GET | 可用 |
| `ths_hot` | GET | 可用 |
| `ths_index` | GET | 可用 |
| `ths_member` | GET | 可用 |
| `top_inst` | GET | 可用 |
| `top_list` | GET | 可用 |
| `top10_cb_holders` | GET | 可用 |
| `top10_floatholders` | GET | 可用 |
| `top10_holders` | GET | 可用 |
| `trade_cal` | GET | 可用 |
| `us_adj_factor` | GET | 可用 |
| `us_adjfactor` | GET | 可用 |
| `us_balancesheet` | GET | 可用 |
| `us_basic` | GET | 可用 |
| `us_cashflow` | GET | 可用 |
| `us_daily` | GET | 可用 |
| `us_daily_adj` | GET | 可用 |
| `us_daily_market_cap` | GET | 可用 |
| `us_depth` | GET | 未启用 |
| `us_fina_indicator` | GET | 可用 |
| `us_income` | GET | 可用 |
| `us_mins` | GET | 可用 |
| `us_monthly` | GET | 可用 |
| `us_tbr` | GET | 可用 |
| `us_tltr` | GET | 可用 |
| `us_tradecal` | GET | 可用 |
| `us_trltr` | GET | 可用 |
| `us_trycr` | GET | 可用 |
| `us_tycr` | GET | 可用 |
| `us_weekly` | GET | 可用 |
| `weekly` | GET | 可用 |
| `wz_index` | GET | 可用 |
| `yc_cb` | GET | 可用 |
