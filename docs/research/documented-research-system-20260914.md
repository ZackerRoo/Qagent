# 双文档只读数据源的系统查询入口

日期：2026-09-14。本轮按用户要求，将两个来源的全部可用只读路由接到统一系统研究接口；目标是覆盖通用访问链路，不代表每个接口均稳定、有数据或已接交易消费者。沿用 G3/G2-FQ1，不新增全市场无限抓取或自动交易目标。

## 接口与调用

系统提供 `GET /api/documented-research/catalogue` 和 `POST /api/documented-research/query`。查询体为 `source`（datahubco/promax）、`api`、`params`，以及独立的 `limit`（1–5000，默认100）、`offset`（0–1000000，默认0）、`fields`；分页和字段不得塞入 params，不允许自定义供应商地址或传入密钥。

系统从服务配置获取独立来源凭据：基础服务需 datahubco_enabled、密钥和既有明确授权的 HTTP 开关；ProMax 需 tushare_relay_research_enabled 及密钥。目录以 `sources.datahubco/promax` 返回各源状态和 entries，基础80项中 `pro_bar` 标记非HTTP不可调用；ProMax 使用实际 capabilities 目录，不能将历史接口数固化为验证成功数。主任务本轮刷新目录为298项、259 enabled、39 disabled，仅为当前能力声明，不是259次业务验收。

查询返回 source/request/fetched_at/fields/rows/result_digest 和隔离标志，status 为 observed/no_rows/error；受控失败也通过 HTTP200 返回安全错误，非法请求 HTTP422 脱敏。不写账本或交易订单；接口用 POST 承载有界只读查询参数，不代表数据库写操作。

`scripts/collect_documented_research.py` 仅请求系统 API，不直接调用供应商、不读取服务密钥、不写 DB。默认 `http://127.0.0.1:8000`，允许显式 loopback 地址；localhost 固定归一为127.0.0.1，拒绝外部主机、URL凭据、路径/查询/片段，禁用代理和重定向。单次仅目录或一页查询，不自动扫描数百接口，不自动翻页。`--output` 原子独占发布，已有文件不覆盖，保留服务端证据结构及摘要；错误不回显异常原文。

```bash
python scripts/collect_documented_research.py --catalogue --output /tmp/documented-catalogue.json
python scripts/collect_documented_research.py --source datahubco --api daily --params '{"ts_code":"000001.SZ","trade_date":"20260911"}' --limit 100 --output /tmp/documented-daily.json
```

命令仅在运行该版本系统服务的机器上使用；来源开关未启用时返回 source_disabled，不能绕过开关直连供应商。

## 数据类别与业务适配边界

| 类别 | 通用研究查询范围 | 现有消费与未验部分 |
| --- | --- | --- |
| 行情 | 日/周/月、服务目录允许的分钟及其他行情路由 | 已有 raw 日线后备；通用分钟返回不直接成为实时成交价，时效/覆盖仍需验证 |
| 复权 | 实际目录允许的因子、调整相关表 | 显式锚定研究已存在；不把当前因子或 raw 日线强行填入 G2 调整价 |
| 财务与估值 | income/balancesheet/cashflow/fina_indicator/daily_basic 等 | 两股四接口研究已验收；全市场 PIT、财务排名接线与效果仍未验 |
| 资金与龙虎榜 | moneyflow/top_list/top_inst/margin 等文档只读表 | 可归档原始页，未因本入口新增选股权重或风控门禁 |
| 事件与公告 | forecast/express/dividend/disclosure_date 等 | 预告研究保留旧公告与页限；不把原始事件直接转成买卖信号 |
| 行业与指数 | 分类/成分/权重/指数行情等实际目录路由 | 通用表格可读不等于行业历史归属或基准收益口径验收 |
| 基金与特色数据 | 基金净值/持仓、期货期权、可转债、利率、影视及其他文档只读表 | 只接研究查询，不新增资产交易范围；数量、字段和语义逐请求披露 |

以上类别是路由覆盖分类，不保证两个来源都有同名接口。不可调用/禁用/写接口保留拒绝状态；未知名称不能通过任意URL转发。数据非空、HTTP200、目录 enabled、模型可消费、策略有效是不同证据，不互相替代。

## 发布与验收

发布 helper 新增 `--enable-documented-research`，显式执行时只将 `QAGENT_TUSHARE_RELAY_RESEARCH_ENABLED=true` 写入既有原子环境替换流程；原环境备份、回滚、Datahubco双密钥方式及账户对账保持原逻辑。不新增 cron、不修改10分钟tick或交易规则。启用研究访问不等于授予自动排名权重。

CLI与发布 helper 本轮子任务相关 **58 passed（0.33秒）**、Ruff通过，包含loopback约束、请求契约、禁止重定向、不可覆盖归档、错误脱敏及研究开关成功/失败回滚。系统API由并行子任务实施，主任务集成与真实部署验收另行补录，不能由客户端mock测试宣称服务已部署。当前本轮未commit、未push、未部署；前轮独立财务采集和日线部署证据继续有效，但不替代本入口验收。

最终子任务复跑 **60 passed、1项既有warning（0.47秒）**、Ruff通过，新增CLI经真实FastAPI路由及研究service的契约验证（供应方替身，非公网请求）和目录incomplete退出码检查。CLI的limit/offset/fields位于请求顶层，目录总是全目录；error/incomplete保留证据但退出1，no_rows退出0仅表示查询完成，不代表数据覆盖成功。

主任务集成验收补录：后端全量 **2,456 passed、3项warnings（242.91秒）**，收集早于部分最终新增测试，不称覆盖最终全部用例；最终相关 **105 passed、1项warning（2.27秒）**，Ruff/diff通过。代码提交 `5b72362b43b1401b742e1cea710f48e101eb64b1` 已完成云端暂存构建，preflight通过并核验16项settings。受控部署正在执行，尚未取得最终切换和对账结果，不能据构建或preflight宣称已部署成功；最终运行证据待主任务续补。

## 最终部署与系统API真实验收

主任务已确认新 release `5b72362b43b1401b742e1cea710f48e101eb64b1` 受控部署成功，`/var/tmp/qagent-rollout-relay-pu3u1eba/result.json` 的 ledger_equal、settings_equal、scheduler_enabled 均 true。研究来源开关已显式启用。本段更新此前部署待验状态，保留前段测试和暂存历史；已commit、未push，未改cron、交易权重或历史账本。

系统 CLI 已经通过真实云端 API 成功取得目录：基础80项、79 callable；ProMax298项、255 callable，来自259 enabled中排除4个操作接口。255表示目录上可只读调用的路由，不是255项真实业务请求成功。目录产物 `/var/lib/qagent-research/financial-enrichment/system-catalogue-5b72362.json`。

同系统入口的两个数据请求结果分别保留：

| 请求 | 真实结果 | 产物与摘要 |
| --- | --- | --- |
| Datahubco moneyflow / 600519.SH / 20260911 | observed、1行，系统CLI成功 | `system-moneyflow-5b72362.json`；digest `400eb551f21b5734dcb8bb79a76f387c604dd23c3bd663b3db7a0a0576eb95e2` |
| ProMax daily / 000001.SZ / 20260911 / limit3 | transport_error，系统CLI退出1 | `system-promax-daily-5b72362.json`；fetched_at `2026-09-14T03:21:27.878469+00:00`；digest `2c87f7bcc59c5d27793ad679d7ea4038274851a440e8f2dfbc48746c5fe592ad` |

两份数据产物均位于 `/var/lib/qagent-research/financial-enrichment/`。ProMax目录成功和其数据请求失败是并列证据：接线路径已经真实执行，不等于该业务数据可用或服务稳定。基础资金流单样本也不证明全部财务、行业或特色路由有效。

此次运行续验 tick 于北京时间 `11:20:00.410` 开始、`11:20:47.175` 完成，attempts2/completed2、错误null；这是该次周期运行证据，不撤销此前发布恢复首slot迟到422秒的历史，也不证明所有股票分钟行情新鲜。

本轮系统只读目录/查询接线与CLI已实现、测试并受控部署；新增生产业务适配、全接口数据稳定性、分钟时效和选股收益仍未由此完成。G3/G2-FQ1原有未完成验收保留，本文补录待主任务review提交。
