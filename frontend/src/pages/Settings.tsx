import { useEffect, useState } from "react";

import {
  clearDataCache,
  fetchDataCache,
  fetchProviderStatus,
  runAutomation,
} from "../api/client";
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
          <h2>Settings</h2>
          <span className="count">Dev</span>
        </div>
        <div className="settings-list">
          <div>
            <span>Data mode</span>
            <strong>{dataMode === "free" ? "Free provider" : "Fixture provider"}</strong>
          </div>
          <div>
            <span>Universe</span>
            <strong>{dataMode === "free" ? symbols : "US:TEST, CN:000001"}</strong>
          </div>
          <div>
            <span>Markets</span>
            <strong>US, CN</strong>
          </div>
          <div>
            <span>Execution</span>
            <strong>Research only</strong>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Automation</h2>
          <span className="count">{automationResult ? "Ready" : "Idle"}</span>
        </div>
        <div className="form-row">
          <button type="button" onClick={runAutomationNow}>
            Run Automation
          </button>
        </div>
        {automationResult ? (
          <div className="settings-list">
            <div>
              <span>Scan</span>
              <strong>
                {automationResult.summary.cards} cards / {automationResult.summary.scanned} scanned
              </strong>
            </div>
            <div>
              <span>Brief</span>
              <strong>{automationResult.brief_id}</strong>
            </div>
            <div>
              <span>Delivery</span>
              <strong>{automationResult.brief_delivery_id ?? "-"}</strong>
            </div>
            <div>
              <span>Backtest</span>
              <strong>{automationResult.summary.backtest_signals} signals</strong>
            </div>
            <div>
              <span>Paper</span>
              <strong>
                {automationResult.summary.paper_created} new /{" "}
                {automationResult.summary.paper_total} total
              </strong>
            </div>
          </div>
        ) : (
          <div className="empty-state">No automation run in this session.</div>
        )}
      </section>

      <section className="panel stack">
        <div className="panel-heading">
          <h2>Universes</h2>
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
            placeholder="Name"
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
            Save
          </button>
        </div>
        {saveMessage && <div className="empty-state">{saveMessage}</div>}
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Scope</th>
                <th>Source</th>
                <th>Tags</th>
                <th>Symbols</th>
              </tr>
            </thead>
            <tbody>
              {universes.map((universe) => (
                <tr key={universe.universe_id}>
                  <td>{universe.name}</td>
                  <td>{universe.market_scope}</td>
                  <td>{universe.source}</td>
                  <td>{universe.tags.join(", ")}</td>
                  <td className="reason-cell">{universe.symbols.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Provider Readiness</h2>
          <span className="count">{providerStatus?.providers.length ?? 0}</span>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {!providerStatus?.providers.length ? (
          <div className="empty-state">Provider status is loading.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Status</th>
                  <th>Capabilities</th>
                  <th>Notes</th>
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
          <h2>Market Data Cache</h2>
          <span className="count">{dataCache?.summaries.length ?? 0}</span>
        </div>
        <div className="form-row">
          <button type="button" onClick={clearCurrentDataCache}>
            Clear {dataMode} cache
          </button>
        </div>
        {cacheMessage && <div className="empty-state">{cacheMessage}</div>}
        {!dataCache?.summaries.length ? (
          <div className="empty-state">No cached market data for this mode.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Rows</th>
                  <th>Date Range</th>
                  <th>Source</th>
                  <th>Cached</th>
                </tr>
              </thead>
              <tbody>
                {dataCache.summaries.map((summary) => (
                  <tr key={`${summary.provider_mode}-${summary.instrument_id}`}>
                    <td>{summary.instrument_id}</td>
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

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}
