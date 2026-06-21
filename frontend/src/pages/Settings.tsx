import type { DataProviderMode } from "../types";

type Props = {
  dataMode: DataProviderMode;
  symbols: string;
};

export function Settings({ dataMode, symbols }: Props) {
  return (
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
  );
}
