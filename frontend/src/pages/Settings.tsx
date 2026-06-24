import { useEffect, useState } from "react";

import {
  clearDataCache,
  fetchDataCache,
  fetchProviderStatus,
  runAutomation,
} from "../api/client";
import { useI18n } from "../i18n";
import { formatInstrumentLabel } from "../lib/instruments";
import type {
  AutomationRunResponse,
  DataProviderMode,
  MarketDataCacheResponse,
  ProviderStatusResponse,
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
  name: "Custom AI Pool",
  description: "My editable AI research pool",
  market_scope: "mixed",
  tags: ["custom"],
  symbols: ["US:NVDA", "US:MSFT"],
};

export function Settings({ dataMode, symbols, universes, onSaveUniverse }: Props) {
  const { t } = useI18n();
  const [providerStatus, setProviderStatus] = useState<ProviderStatusResponse>();
  const [dataCache, setDataCache] = useState<MarketDataCacheResponse>();
  const [automationResult, setAutomationResult] = useState<AutomationRunResponse>();
  const [universeForm, setUniverseForm] = useState<UniverseCreate>(emptyUniverse);
  const [saveMessage, setSaveMessage] = useState("");
  const [cacheMessage, setCacheMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const [providers, cache] = await Promise.all([
          fetchProviderStatus(),
          fetchDataCache(dataMode),
        ]);
        setProviderStatus(providers);
        setDataCache(cache);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to load provider status");
      }
    }
    void load();
  }, [dataMode]);

  async function saveUniverseForm() {
    try {
      setError("");
      const saved = await onSaveUniverse(universeForm);
      setSaveMessage(`Saved ${saved.name}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save universe");
    }
  }

  async function clearCurrentDataCache() {
    try {
      setError("");
      const cleared = await clearDataCache(dataMode);
      setCacheMessage(`Cleared ${cleared.deleted} cached rows`);
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

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("settings.title")}</h2>
          <span className="count">Dev</span>
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
            <strong>US, CN</strong>
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
                {automationResult.summary.paper_created} new /{" "}
                {automationResult.summary.paper_total} total
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
            <option value="mixed">Mixed</option>
            <option value="US">US</option>
            <option value="CN">CN</option>
          </select>
          <input
            value={universeForm.symbols.join(",")}
            onChange={(event) =>
              setUniverseForm({ ...universeForm, symbols: splitList(event.target.value) })
            }
            placeholder="US:NVDA,US:MSFT"
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
                  <td>{universe.name}</td>
                  <td>{universe.market_scope}</td>
                  <td>{universe.source}</td>
                  <td>{universe.tags.join(", ")}</td>
                  <td className="reason-cell" title={universe.symbols.join(", ")}>
                    {universe.symbols.map((symbol) => formatInstrumentLabel(symbol)).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
                    <td>{provider.name}</td>
                    <td>
                      <span className={`status status-${provider.status}`}>{provider.status}</span>
                    </td>
                    <td className="reason-cell">{provider.capabilities.join(", ")}</td>
                    <td className="reason-cell">{provider.notes}</td>
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
            {t("settings.clearCache")} {dataMode}
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
                    <td title={summary.instrument_id}>{formatInstrumentLabel(summary.instrument_id)}</td>
                    <td>{summary.rows}</td>
                    <td>
                      {summary.first_trade_date} to {summary.last_trade_date}
                    </td>
                    <td className="reason-cell">{summary.source_providers.join(", ")}</td>
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

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatInstrumentList(value: string): string {
  return splitList(value)
    .map((symbol) => formatInstrumentLabel(symbol))
    .join(", ");
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}
