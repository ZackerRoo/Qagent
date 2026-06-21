import { useEffect, useState } from "react";

import { evaluateAlerts, fetchAlertRules, saveAlertRule } from "../api/client";
import type { AlertRule, TriggeredAlert } from "../types";

const emptyRule: AlertRule = {
  rule_id: "entry-US-TEST",
  instrument_id: "US:TEST",
  kind: "entry_trigger",
  operator: ">=",
  threshold: "82.00",
};

export function Alerts() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [rule, setRule] = useState<AlertRule>(emptyRule);
  const [price, setPrice] = useState("83.00");
  const [triggered, setTriggered] = useState<TriggeredAlert[]>([]);

  async function load() {
    const result = await fetchAlertRules();
    setRules(result.rules);
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

  return (
    <section className="panel stack">
      <div className="panel-heading">
        <h2>Alerts</h2>
        <span className="count">{rules.length} rules</span>
      </div>
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
