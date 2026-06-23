import { useEffect, useState } from "react";

import {
  evaluateAlerts,
  fetchAlertRules,
  fetchAlertSuggestions,
  runAlerts,
  saveAlertRule,
} from "../api/client";
import type { AlertRule, AlertRunResponse, AlertSuggestion, DataProviderMode, TriggeredAlert } from "../types";

const emptyRule: AlertRule = {
  rule_id: "entry-US-TEST",
  instrument_id: "US:TEST",
  kind: "entry_trigger",
  operator: ">=",
  threshold: "82.00",
};

export function Alerts({ dataMode }: { dataMode: DataProviderMode }) {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [suggestions, setSuggestions] = useState<AlertSuggestion[]>([]);
  const [rule, setRule] = useState<AlertRule>(emptyRule);
  const [price, setPrice] = useState("83.00");
  const [triggered, setTriggered] = useState<TriggeredAlert[]>([]);
  const [runResult, setRunResult] = useState<AlertRunResponse>();
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    try {
      setError("");
      const [ruleResult, suggestionResult] = await Promise.all([
        fetchAlertRules(),
        fetchAlertSuggestions(),
      ]);
      setRules(ruleResult.rules);
      setSuggestions(suggestionResult.suggestions);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load alerts");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function save() {
    await saveAlertRule(rule);
    await load();
  }

  async function evaluate() {
    const result = await evaluateAlerts({ [rule.instrument_id]: price });
    setTriggered(result.alerts);
  }

  async function runProviderAlerts() {
    try {
      setIsRunning(true);
      setError("");
      const result = await runAlerts(dataMode);
      setRunResult(result);
      setTriggered(result.alerts);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to run alert scan");
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <section className="panel stack">
      <div className="panel-heading">
        <h2>Alerts</h2>
        <div className="brief-actions">
          <span className="count">{rules.length} rules</span>
          <button className="icon-action" type="button" onClick={runProviderAlerts} disabled={isRunning}>
            {isRunning ? "Running" : "Run Alerts"}
          </button>
        </div>
      </div>
      {error && <div className="empty-state error">{error}</div>}
      <div className="form-row alert-form">
        <input
          value={rule.rule_id}
          onChange={(event) => setRule({ ...rule, rule_id: event.target.value })}
          placeholder="Rule id"
        />
        <input
          value={rule.instrument_id}
          onChange={(event) => setRule({ ...rule, instrument_id: event.target.value })}
          placeholder="US:TEST"
        />
        <select
          value={rule.kind}
          onChange={(event) => setRule({ ...rule, kind: event.target.value })}
        >
          <option value="entry_trigger">Entry</option>
          <option value="stop_guard">Stop</option>
          <option value="target_1_reached">Target</option>
        </select>
        <select
          value={rule.operator}
          onChange={(event) =>
            setRule({ ...rule, operator: event.target.value as AlertRule["operator"] })
          }
        >
          <option value=">=">{">="}</option>
          <option value="<=">{"<="}</option>
        </select>
        <input
          value={rule.threshold}
          onChange={(event) => setRule({ ...rule, threshold: event.target.value })}
          placeholder="Threshold"
        />
        <button type="button" onClick={save}>
          Save
        </button>
      </div>
      <div className="form-row alert-form">
        <input value={price} onChange={(event) => setPrice(event.target.value)} placeholder="Price" />
        <button type="button" onClick={evaluate}>
          Evaluate
        </button>
      </div>
      {runResult && (
        <div className="metric-grid">
          <div>
            <span>Provider</span>
            <strong>{runResult.summary.provider}</strong>
          </div>
          <div>
            <span>Rules</span>
            <strong>{runResult.summary.rules}</strong>
          </div>
          <div>
            <span>Triggered</span>
            <strong>{runResult.summary.triggered}</strong>
          </div>
          <div>
            <span>Queued</span>
            <strong>{runResult.delivery ? runResult.delivery.delivery_id.slice(-8) : "-"}</strong>
          </div>
        </div>
      )}
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Suggestion</th>
              <th>Symbol</th>
              <th>Kind</th>
              <th>Condition</th>
              <th>Rationale</th>
              <th>Use</th>
            </tr>
          </thead>
          <tbody>
            {suggestions.map((item) => (
              <tr key={item.rule_id}>
                <td>{item.rule_id}</td>
                <td className="ticker">{item.instrument_id}</td>
                <td>{item.kind}</td>
                <td>
                  {item.operator} {item.threshold}
                </td>
                <td className="reason-cell">{item.rationale}</td>
                <td>
                  <button
                    className="table-action"
                    type="button"
                    onClick={() =>
                      setRule({
                        rule_id: item.rule_id,
                        instrument_id: item.instrument_id,
                        kind: item.kind,
                        operator: item.operator,
                        threshold: item.threshold,
                      })
                    }
                  >
                    Use
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Rule</th>
              <th>Symbol</th>
              <th>Kind</th>
              <th>Condition</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((item) => (
              <tr key={item.rule_id}>
                <td>{item.rule_id}</td>
                <td className="ticker">{item.instrument_id}</td>
                <td>{item.kind}</td>
                <td>
                  {item.operator} {item.threshold}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {triggered.length > 0 && (
        <div className="alert-results">
          {triggered.map((alert) => (
            <div key={`${alert.rule_id}-${alert.triggered_at}`}>
              <strong>{alert.instrument_id}</strong>
              <span>{alert.message}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
