export function Settings() {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>Settings</h2>
        <span className="count">Dev</span>
      </div>
      <div className="settings-list">
        <div>
          <span>Data mode</span>
          <strong>Fixture + free provider ready</strong>
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
