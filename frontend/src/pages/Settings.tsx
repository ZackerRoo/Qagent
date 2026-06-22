import { useEffect, useState } from "react";

import { fetchProviderStatus } from "../api/client";
import type { DataProviderMode, ProviderStatusResponse } from "../types";

type Props = {
  dataMode: DataProviderMode;
  symbols: string;
};

export function Settings({ dataMode, symbols }: Props) {
  const [providerStatus, setProviderStatus] = useState<ProviderStatusResponse>();
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
