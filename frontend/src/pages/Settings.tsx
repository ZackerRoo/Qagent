import { useEffect, useState } from "react";

import {
  clearDataCache,
  fetchAutomationScheduler,
  fetchDataCache,
  fetchProviderStatus,
  fetchTradableCatalog,
  runFullMarketScan,
  runAutomation,
  runAutomationSchedulerOnce,
  startAutomationScheduler,
  stopAutomationScheduler,
  syncTradableCatalog,
} from "../api/client";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import {
  localizeCapability,
  localizeProvider,
  localizeProviderName,
  localizeReason,
  localizeStatus,
} from "../lib/localize";
import type {
  AutomationRunResponse,
  AutoProcessingState,
  DataProviderMode,
  MarketDataCacheResponse,
  ProviderStatusResponse,
  TradableCatalogResponse,
  UniverseCreate,
  UniverseRecord,
} from "../types";

type Props = {
  dataMode: DataProviderMode;
  symbols: string;
  universes: UniverseRecord[];
  onSaveUniverse(payload: UniverseCreate): Promise<UniverseRecord>;
};

const emptyUniverse: UniverseCreate = {
  universe_id: "custom_ai_pool",
  name: "我的 A 股研究池",
  description: "自定义 A 股研究池",
  market_scope: "CN",
  tags: ["custom"],
  symbols: ["CN:000001", "CN:000063"],
};

export function Settings({ dataMode, symbols, universes, onSaveUniverse }: Props) {
  const { language, t } = useI18n();
  const [providerStatus, setProviderStatus] = useState<ProviderStatusResponse>();
  const [dataCache, setDataCache] = useState<MarketDataCacheResponse>();
  const [automationResult, setAutomationResult] = useState<AutomationRunResponse>();
  const [automationScheduler, setAutomationScheduler] = useState<AutoProcessingState>();
  const [automationBusy, setAutomationBusy] = useState(false);
  const [tradableCatalog, setTradableCatalog] = useState<TradableCatalogResponse>();
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogAssetType, setCatalogAssetType] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogSyncing, setCatalogSyncing] = useState(false);
  const [fullMarketScanning, setFullMarketScanning] = useState(false);
  const [catalogMessage, setCatalogMessage] = useState("");
  const [universeForm, setUniverseForm] = useState<UniverseCreate>(emptyUniverse);
  const [saveMessage, setSaveMessage] = useState("");
  const [cacheMessage, setCacheMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const apply = <T,>(setter: (value: T) => void) => (value: T) => {
      if (!cancelled) {
        setter(value);
      }
    };
    const fail = (caught: unknown, fallback: string) => {
      if (!cancelled) {
        setError(caught instanceof Error ? caught.message : fallback);
      }
    };

    setError("");
    void fetchProviderStatus()
      .then(apply(setProviderStatus))
      .catch((caught) => fail(caught, "Failed to load provider status"));
    void fetchDataCache(dataMode)
      .then(apply(setDataCache))
      .catch((caught) => fail(caught, "Failed to load data cache"));
    void fetchTradableCatalog("", 12)
      .then(apply(setTradableCatalog))
      .catch((caught) => fail(caught, "Failed to load tradable catalog"));
    void fetchAutomationScheduler()
      .then(apply(setAutomationScheduler))
      .catch((caught) => fail(caught, "Failed to load automatic processing"));

    return () => {
      cancelled = true;
    };
  }, [dataMode]);

  async function saveUniverseForm() {
    try {
      setError("");
      const saved = await onSaveUniverse(universeForm);
      setSaveMessage(language === "zh" ? `已保存 ${saved.name}` : `Saved ${saved.name}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save universe");
    }
  }

  async function clearCurrentDataCache() {
    try {
      setError("");
      const cleared = await clearDataCache(dataMode);
      setCacheMessage(
        language === "zh"
          ? `已清理 ${cleared.deleted} 行缓存`
          : `Cleared ${cleared.deleted} cached rows`,
      );
      setDataCache(await fetchDataCache(dataMode));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to clear data cache");
    }
  }

  async function runAutomationNow() {
    try {
      setError("");
      setAutomationResult(await runAutomation(dataMode, symbols));
      setDataCache(await fetchDataCache(dataMode));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to run automation");
    }
  }

  async function runAutomationCycleNow() {
    try {
      setError("");
      setAutomationBusy(true);
      const state = await runAutomationSchedulerOnce(dataMode, symbols);
      setAutomationScheduler(state);
      setDataCache(await fetchDataCache(dataMode));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to run automatic processing");
    } finally {
      setAutomationBusy(false);
    }
  }

  async function startAutomationLoop() {
    try {
      setError("");
      setAutomationBusy(true);
      setAutomationScheduler(await startAutomationScheduler(dataMode, symbols));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to start automatic processing");
    } finally {
      setAutomationBusy(false);
    }
  }

  async function stopAutomationLoop() {
    try {
      setError("");
      setAutomationBusy(true);
      setAutomationScheduler(await stopAutomationScheduler());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to stop automatic processing");
    } finally {
      setAutomationBusy(false);
    }
  }

  async function searchTradableCatalog() {
    try {
      setError("");
      setCatalogLoading(true);
      setTradableCatalog(
        await fetchTradableCatalog(catalogQuery, 30, catalogAssetType || undefined),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to search tradable catalog");
    } finally {
      setCatalogLoading(false);
    }
  }

  async function syncTradableCatalogNow() {
    try {
      setError("");
      setCatalogMessage("");
      setCatalogSyncing(true);
      const result = await syncTradableCatalog(true);
      setCatalogMessage(
        language === "zh"
          ? `已同步 ${formatNumber(result.summary.total_count, language)} 个可交易标的`
          : `Synced ${formatNumber(result.summary.total_count, language)} tradable instruments`,
      );
      setTradableCatalog(
        await fetchTradableCatalog(catalogQuery, 30, catalogAssetType || undefined),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to sync tradable catalog");
    } finally {
      setCatalogSyncing(false);
    }
  }

  async function runFullMarketScanNow() {
    try {
      setError("");
      setCatalogMessage("");
      setFullMarketScanning(true);
      const result = await runFullMarketScan(dataMode, 300, true);
      setCatalogMessage(
        language === "zh"
          ? `已扫描 ${result.items.length} 个标的，生成 ${result.cards.length} 张机会卡`
          : `Scanned ${result.items.length} instruments, created ${result.cards.length} cards`,
      );
      setDataCache(await fetchDataCache(dataMode));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to run full-market scan");
    } finally {
      setFullMarketScanning(false);
    }
  }

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("settings.title")}</h2>
          <span className="count">{language === "zh" ? "开发" : "Dev"}</span>
        </div>
        <div className="settings-list">
          <div>
            <span>{t("settings.dataMode")}</span>
            <strong>{dataMode === "free" ? t("settings.freeProvider") : t("settings.fixtureProvider")}</strong>
          </div>
          <div>
            <span>{t("top.universe")}</span>
            <strong>
              {formatInstrumentList(dataMode === "free" ? symbols : "US:TEST,CN:000001")}
            </strong>
          </div>
          <div>
            <span>{t("settings.markets")}</span>
            <strong>{language === "zh" ? "A股" : "A-Shares"}</strong>
          </div>
          <div>
            <span>{t("settings.execution")}</span>
            <strong>{t("settings.researchOnly")}</strong>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("settings.automation")}</h2>
          <span className="count">{automationResult ? t("settings.ready") : t("settings.idle")}</span>
        </div>
        <AutomaticProcessingPanel
          state={automationScheduler}
          busy={automationBusy}
          language={language}
          onRunOnce={runAutomationCycleNow}
          onStart={startAutomationLoop}
          onStop={stopAutomationLoop}
        />
        <div className="form-row">
          <button type="button" onClick={runAutomationNow}>
            {t("settings.runAutomation")}
          </button>
        </div>
        {automationResult ? (
          <div className="settings-list">
            <div>
              <span>{t("settings.scan")}</span>
              <strong>
                {automationResult.summary.cards} {t("common.cards")} /{" "}
                {automationResult.summary.scanned} {t("common.scanned")}
              </strong>
            </div>
            <div>
              <span>{t("settings.brief")}</span>
              <strong>{automationResult.brief_id}</strong>
            </div>
            <div>
              <span>{t("settings.delivery")}</span>
              <strong>{automationResult.brief_delivery_id ?? "-"}</strong>
            </div>
            <div>
              <span>{t("settings.backtest")}</span>
              <strong>
                {automationResult.summary.backtest_signals} {t("opportunities.signals")}
              </strong>
            </div>
            <div>
              <span>{t("settings.paper")}</span>
              <strong>
                {language === "zh"
                  ? `${automationResult.summary.paper_created} 新增 / ${automationResult.summary.paper_total} 合计`
                  : `${automationResult.summary.paper_created} new / ${automationResult.summary.paper_total} total`}
              </strong>
            </div>
          </div>
        ) : (
          <div className="empty-state">{t("settings.noAutomation")}</div>
        )}
      </section>

      <section className="panel stack">
        <div className="panel-heading">
          <h2>{t("settings.universes")}</h2>
          <span className="count">{universes.length}</span>
        </div>
        <div className="form-row universe-form">
          <input
            value={universeForm.universe_id}
            onChange={(event) =>
              setUniverseForm({ ...universeForm, universe_id: event.target.value })
            }
            placeholder="universe_id"
          />
          <input
            value={universeForm.name}
            onChange={(event) => setUniverseForm({ ...universeForm, name: event.target.value })}
            placeholder={t("common.name")}
          />
          <select
            value={universeForm.market_scope}
            onChange={(event) =>
              setUniverseForm({ ...universeForm, market_scope: event.target.value })
            }
          >
            <option value="mixed">{language === "zh" ? "混合" : "Mixed"}</option>
            <option value="US">{language === "zh" ? "美股" : "US"}</option>
            <option value="CN">{language === "zh" ? "A股" : "CN"}</option>
          </select>
          <input
            value={universeForm.symbols.join(",")}
            onChange={(event) =>
              setUniverseForm({ ...universeForm, symbols: splitList(event.target.value) })
            }
            placeholder="CN:000001,CN:000063"
          />
          <button type="button" onClick={saveUniverseForm}>
            {t("common.save")}
          </button>
        </div>
        {saveMessage && <div className="empty-state">{saveMessage}</div>}
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>{t("common.name")}</th>
                <th>{t("common.scope")}</th>
                <th>{t("settings.source")}</th>
                <th>{t("common.tags")}</th>
                <th>{t("common.symbols")}</th>
              </tr>
            </thead>
            <tbody>
              {universes.map((universe) => (
                <tr key={universe.universe_id}>
                  <td>{formatUniverseName(universe, language)}</td>
                  <td>{formatScope(universe.market_scope, language)}</td>
                  <td>{formatSource(universe.source, language)}</td>
                  <td>{formatTags(universe.tags, language)}</td>
                  <td
                    className="reason-cell"
                    title={universe.symbols.map((symbol) => formatInstrumentDisplay(symbol)).join(", ")}
                  >
                    {universe.symbols.map((symbol) => formatInstrumentDisplay(symbol)).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel stack">
        <div className="panel-heading">
          <h2>{t("settings.tradableCatalog")}</h2>
          <span className="count">
            {formatNumber(tradableCatalog?.summary.total_count ?? 0, language)}
          </span>
        </div>
        <div className="metric-grid catalog-metrics">
          <div>
            <span>{t("settings.catalogTotal")}</span>
            <strong>{formatNumber(tradableCatalog?.summary.total_count ?? 0, language)}</strong>
          </div>
          <div>
            <span>{t("settings.catalogStocks")}</span>
            <strong>{formatNumber(tradableCatalog?.summary.stock_count ?? 0, language)}</strong>
          </div>
          <div>
            <span>{t("settings.catalogEtfs")}</span>
            <strong>{formatNumber(tradableCatalog?.summary.etf_count ?? 0, language)}</strong>
          </div>
          <div>
            <span>{t("settings.catalogSynced")}</span>
            <strong>{formatTimestamp(tradableCatalog?.summary.last_synced_at ?? null)}</strong>
          </div>
        </div>
        <div className="catalog-toolbar">
          <input
            value={catalogQuery}
            onChange={(event) => setCatalogQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void searchTradableCatalog();
              }
            }}
            placeholder={t("settings.catalogSearchPlaceholder")}
          />
          <select
            value={catalogAssetType}
            onChange={(event) => setCatalogAssetType(event.target.value)}
          >
            <option value="">{t("settings.catalogAllTypes")}</option>
            <option value="stock">{t("settings.catalogStock")}</option>
            <option value="etf">{t("settings.catalogEtf")}</option>
          </select>
          <button type="button" onClick={searchTradableCatalog} disabled={catalogLoading}>
            {catalogLoading ? t("common.refreshing") : t("common.load")}
          </button>
          <button type="button" onClick={syncTradableCatalogNow} disabled={catalogSyncing}>
            {catalogSyncing ? t("common.refreshing") : t("settings.syncCatalog")}
          </button>
          <button type="button" onClick={runFullMarketScanNow} disabled={fullMarketScanning}>
            {fullMarketScanning ? t("common.running") : t("settings.runFullMarketScan")}
          </button>
        </div>
        {catalogMessage && <div className="empty-state">{catalogMessage}</div>}
        <div className="settings-list">
          <div>
            <span>{t("settings.catalogExchange")}</span>
            <strong>{formatExchangeSummary(tradableCatalog?.summary.exchanges ?? {}, language)}</strong>
          </div>
          <div>
            <span>{t("settings.catalogCoverage")}</span>
            <strong>{t("settings.catalogCoverageValue")}</strong>
          </div>
        </div>
        {!tradableCatalog?.items.length ? (
          <div className="empty-state">{t("settings.catalogEmpty")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("common.symbol")}</th>
                  <th>{t("settings.catalogType")}</th>
                  <th>{t("settings.catalogExchange")}</th>
                  <th>{t("settings.source")}</th>
                </tr>
              </thead>
              <tbody>
                {tradableCatalog.items.map((item) => (
                  <tr key={item.instrument_id}>
                    <td className="ticker" title={formatInstrumentDisplay(item.instrument_id, item.label)}>
                      {formatInstrumentDisplay(item.instrument_id, item.label)}
                    </td>
                    <td>{item.symbol}</td>
                    <td>{formatAssetType(item.asset_type, language)}</td>
                    <td>{formatExchange(item.exchange)}</td>
                    <td className="reason-cell">{formatSource(item.source, language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("settings.providerReadiness")}</h2>
          <span className="count">{providerStatus?.providers.length ?? 0}</span>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {!providerStatus?.providers.length ? (
          <div className="empty-state">{t("common.loading")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.provider")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("settings.capabilities")}</th>
                  <th>{t("settings.notes")}</th>
                </tr>
              </thead>
              <tbody>
                {providerStatus.providers.map((provider) => (
                  <tr key={provider.provider_id}>
                    <td>{localizeProviderName(provider.name, language)}</td>
                    <td>
                      <span className={`status status-${provider.status}`}>
                        {localizeStatus(provider.status, language)}
                      </span>
                    </td>
                    <td className="reason-cell">
                      {provider.capabilities
                        .map((item) => localizeCapability(item, language))
                        .join(language === "zh" ? "、" : ", ")}
                    </td>
                    <td className="reason-cell">{localizeReason(provider.notes, language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("settings.cache")}</h2>
          <span className="count">{dataCache?.summaries.length ?? 0}</span>
        </div>
        <div className="form-row">
          <button type="button" onClick={clearCurrentDataCache}>
            {t("settings.clearCache")} {localizeProvider(dataMode, language)}
          </button>
        </div>
        {cacheMessage && <div className="empty-state">{cacheMessage}</div>}
        {!dataCache?.summaries.length ? (
          <div className="empty-state">{t("settings.noCache")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.symbol")}</th>
                  <th>{t("settings.rows")}</th>
                  <th>{t("settings.dateRange")}</th>
                  <th>{t("settings.source")}</th>
                  <th>{t("settings.cached")}</th>
                </tr>
              </thead>
              <tbody>
                {dataCache.summaries.map((summary) => (
                  <tr key={`${summary.provider_mode}-${summary.instrument_id}`}>
                    <td title={formatInstrumentDisplay(summary.instrument_id)}>{formatInstrumentDisplay(summary.instrument_id)}</td>
                    <td>{summary.rows}</td>
                    <td>
                      {summary.first_trade_date} {language === "zh" ? "至" : "to"}{" "}
                      {summary.last_trade_date}
                    </td>
                    <td className="reason-cell">
                      {summary.source_providers
                        .map((provider) => localizeProvider(provider, language))
                        .join(language === "zh" ? "、" : ", ")}
                    </td>
                    <td>{formatTimestamp(summary.last_cached_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function AutomaticProcessingPanel({
  state,
  busy,
  language,
  onRunOnce,
  onStart,
  onStop,
}: {
  state?: AutoProcessingState;
  busy: boolean;
  language: "zh" | "en";
  onRunOnce(): void;
  onStart(): void;
  onStop(): void;
}) {
  const enabled = state?.enabled ?? false;
  const result = state?.last_result;
  return (
    <div className="auto-processing-panel">
      <div className="auto-processing-head">
        <div>
          <span>{language === "zh" ? "自动处理系统" : "Automatic Processing"}</span>
          <strong>
            {enabled
              ? language === "zh"
                ? "运行中"
                : "Running"
              : language === "zh"
                ? "已关闭"
                : "Off"}
          </strong>
        </div>
        <div className="auto-processing-actions">
          <button type="button" onClick={onRunOnce} disabled={busy}>
            {busy ? (language === "zh" ? "处理中" : "Running") : language === "zh" ? "立即执行一轮" : "Run once"}
          </button>
          <button type="button" onClick={onStart} disabled={busy || enabled}>
            {language === "zh" ? "开启自动处理" : "Start loop"}
          </button>
          <button type="button" onClick={onStop} disabled={busy || !enabled}>
            {language === "zh" ? "停止" : "Stop"}
          </button>
        </div>
      </div>

      <div className="settings-list auto-processing-metrics">
        <div>
          <span>{language === "zh" ? "运行间隔" : "Interval"}</span>
          <strong>{formatInterval(state?.settings.interval_seconds, language)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "下次运行" : "Next run"}</span>
          <strong>{formatMaybeDate(state?.next_run_at)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "已执行轮次" : "Runs"}</span>
          <strong>{state?.run_count ?? 0}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "扫描状态" : "Scan status"}</span>
          <strong>{localizeAutomationScanStatus(result?.scan_status, language)}</strong>
        </div>
        <div>
          <span>{language === "zh" ? "模拟盘" : "Paper"}</span>
          <strong>
            {result
              ? language === "zh"
                ? `${result.paper_created} 新增 / ${result.paper_total} 合计 / ${result.paper_closed} 闭环`
                : `${result.paper_created} new / ${result.paper_total} total / ${result.paper_closed} closed`
              : "-"}
          </strong>
        </div>
        <div>
          <span>{language === "zh" ? "提醒触发" : "Alerts"}</span>
          <strong>{result?.alerts_triggered ?? "-"}</strong>
        </div>
      </div>

      {result?.scan_job_id && (
        <div className="empty-state compact">
          {language === "zh" ? "全量扫描任务：" : "Full scan job: "}
          {result.scan_job_id}
        </div>
      )}
      {state?.last_error && <div className="empty-state error compact">{state.last_error}</div>}
      {result?.errors.length ? (
        <div className="empty-state error compact">{result.errors.slice(0, 2).join("；")}</div>
      ) : null}
    </div>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatInstrumentList(value: string): string {
  return splitList(value)
    .map((symbol) => formatInstrumentDisplay(symbol))
    .join(", ");
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}

function formatMaybeDate(value?: string | null): string {
  return formatTimestamp(value ?? null);
}

function formatInterval(value: number | undefined, language: "zh" | "en"): string {
  if (!value) {
    return "-";
  }
  if (value < 60) {
    return language === "zh" ? `${value} 秒` : `${value}s`;
  }
  const minutes = Math.round(value / 60);
  if (minutes < 60) {
    return language === "zh" ? `${minutes} 分钟` : `${minutes}m`;
  }
  const hours = Math.round(minutes / 60);
  return language === "zh" ? `${hours} 小时` : `${hours}h`;
}

function localizeAutomationScanStatus(value: string | undefined, language: "zh" | "en"): string {
  const labels: Record<string, { zh: string; en: string }> = {
    disabled: { zh: "未扫描", en: "Disabled" },
    completed: { zh: "已完成", en: "Completed" },
    queued: { zh: "已排队", en: "Queued" },
    already_running: { zh: "已有任务运行", en: "Already running" },
    cache_fresh: { zh: "缓存仍新鲜", en: "Cache fresh" },
    failed: { zh: "失败", en: "Failed" },
  };
  if (!value) {
    return "-";
  }
  return labels[value]?.[language] ?? value;
}

function formatNumber(value: number, language: "zh" | "en"): string {
  return new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US").format(value);
}

function formatAssetType(value: string, language: "zh" | "en"): string {
  if (value === "stock") {
    return language === "zh" ? "股票" : "Stock";
  }
  if (value === "etf") {
    return "ETF";
  }
  return value;
}

function formatExchange(value: string): string {
  if (value === "SZ") {
    return "深交所";
  }
  if (value === "SH") {
    return "上交所";
  }
  if (value === "BJ") {
    return "北交所";
  }
  return value || "-";
}

function formatExchangeSummary(exchanges: Record<string, number>, language: "zh" | "en"): string {
  const entries = Object.entries(exchanges).sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) {
    return "-";
  }
  return entries
    .map(([exchange, count]) =>
      language === "zh"
        ? `${formatExchange(exchange)} ${formatNumber(count, language)}`
        : `${exchange} ${formatNumber(count, language)}`,
    )
    .join(language === "zh" ? "、" : ", ");
}

function formatScope(value: string, language: "zh" | "en"): string {
  if (value === "CN") {
    return language === "zh" ? "A股" : "A-Shares";
  }
  if (value === "US") {
    return language === "zh" ? "美股" : "US Stocks";
  }
  if (value === "mixed") {
    return language === "zh" ? "混合" : "Mixed";
  }
  return value;
}

function formatSource(value: string, language: "zh" | "en"): string {
  if (value === "builtin_starter") {
    return language === "zh" ? "内置模板" : "Built-in starter";
  }
  if (value === "custom") {
    return language === "zh" ? "自定义" : "Custom";
  }
  if (value.includes("akshare_stock_info_a_code_name") || value.includes("akshare_fund_etf_spot_em")) {
    return language === "zh" ? "免费行情目录" : "Free market catalog";
  }
  return value;
}

function formatUniverseName(universe: UniverseRecord, language: "zh" | "en"): string {
  if (language !== "zh") {
    return universe.name;
  }
  const labels: Record<string, string> = {
    fixture_dev: "开发调试池",
    free_default: "全A综合池",
    cn_liquid_starter: "A股30只流动性样本池",
    cn_index_kcb50: "科创50成分股",
    cn_index_csi300: "沪深300成分股",
    cn_index_csi500: "中证500成分股",
    cn_index_csi1000: "中证1000成分股",
    cn_index_chinext50: "创业板50成分股",
    cn_etf_core: "核心指数ETF",
    cn_theme_semiconductor: "半导体芯片主题",
    cn_theme_memory: "存储芯片主题",
    cn_theme_ai_compute: "AI算力供应链主题",
    cn_tech_starter: "A股科技入门池",
    cn_blue_chip_starter: "A股蓝筹入门池",
    cn_growth_starter: "A股成长入门池",
  };
  return labels[universe.universe_id] ?? universe.name;
}

function formatTags(tags: string[], language: "zh" | "en"): string {
  if (!tags.length) {
    return "-";
  }
  const labels: Record<string, string> = {
    cn: "A股",
    free: "免费",
    default: "默认",
    growth: "成长",
    technology: "科技",
    theme: "主题",
    semiconductor: "半导体",
    chip: "芯片",
    memory: "存储",
    ai_compute: "AI算力",
    starter: "入门",
    blue_chip: "蓝筹",
    liquid: "高流动性",
    fixture: "开发",
    dev: "开发",
    custom: "自定义",
  };
  return tags
    .map((tag) => (language === "zh" ? labels[tag] ?? tag.replace(/_/g, " ") : tag.replace(/_/g, " ")))
    .join(language === "zh" ? "、" : ", ");
}
