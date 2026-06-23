import { useEffect, useState } from "react";

import { fetchProviderStatus } from "../api/client";
import type {
  DataProviderMode,
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
  const [universeForm, setUniverseForm] = useState<UniverseCreate>(emptyUniverse);
  const [saveMessage, setSaveMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        setError("");
        setProviderStatus(await fetchProviderStatus());
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to load provider status");
      }
    }
    void load();
  }, []);

  async function saveUniverseForm() {
    try {
      setError("");
      const saved = await onSaveUniverse(universeForm);
      setSaveMessage(`Saved ${saved.name}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save universe");
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
    </div>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
