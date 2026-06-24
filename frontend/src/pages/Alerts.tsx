import { useEffect, useState } from "react";

import {
  evaluateAlerts,
  fetchAlertRules,
  fetchAlertSuggestions,
  runAlerts,
  saveAlertRule,
} from "../api/client";
import { useI18n } from "../i18n";
import { formatInstrumentLabel } from "../lib/instruments";
import type { AlertRule, AlertRunResponse, AlertSuggestion, DataProviderMode, TriggeredAlert } from "../types";

const emptyRule: AlertRule = {
  rule_id: "entry-US-TEST",
  instrument_id: "US:TEST",
  kind: "entry_trigger",
  operator: ">=",
  threshold: "82.00",
};

export function Alerts({ dataMode }: { dataMode: DataProviderMode }) {
  const { t } = useI18n();
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
        <h2>{t("alerts.title")}</h2>
        <div className="brief-actions">
          <span className="count">
            {rules.length} {t("alerts.rules")}
          </span>
          <button className="icon-action" type="button" onClick={runProviderAlerts} disabled={isRunning}>
            {isRunning ? t("common.running") : t("alerts.runAlerts")}
          </button>
        </div>
      </div>
      {error && <div className="empty-state error">{error}</div>}
      <div className="form-row alert-form">
        <input
          value={rule.rule_id}
          onChange={(event) => setRule({ ...rule, rule_id: event.target.value })}
          placeholder={t("alerts.ruleId")}
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
          <option value="entry_trigger">{t("alerts.entry")}</option>
          <option value="stop_guard">{t("alerts.stop")}</option>
          <option value="target_1_reached">{t("alerts.target")}</option>
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
          placeholder={t("alerts.threshold")}
        />
        <button type="button" onClick={save}>
          {t("common.save")}
        </button>
      </div>
      <div className="form-row alert-form">
        <input value={price} onChange={(event) => setPrice(event.target.value)} placeholder={t("alerts.price")} />
        <button type="button" onClick={evaluate}>
          {t("alerts.evaluate")}
        </button>
      </div>
      {runResult && (
        <div className="metric-grid">
          <div>
            <span>{t("common.provider")}</span>
            <strong>{runResult.summary.provider}</strong>
          </div>
          <div>
            <span>{t("common.rules")}</span>
            <strong>{runResult.summary.rules}</strong>
          </div>
          <div>
            <span>{t("common.triggered")}</span>
            <strong>{runResult.summary.triggered}</strong>
          </div>
          <div>
            <span>{t("common.queued")}</span>
            <strong>{runResult.delivery ? runResult.delivery.delivery_id.slice(-8) : "-"}</strong>
          </div>
        </div>
      )}
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>{t("alerts.suggestion")}</th>
              <th>{t("common.symbol")}</th>
              <th>{t("alerts.kind")}</th>
              <th>{t("alerts.condition")}</th>
              <th>{t("alerts.rationale")}</th>
              <th>{t("alerts.use")}</th>
            </tr>
          </thead>
          <tbody>
            {suggestions.map((item) => (
              <tr key={item.rule_id}>
                <td>{item.rule_id}</td>
                <td className="ticker" title={item.instrument_id}>
                  {formatInstrumentLabel(item.instrument_id)}
                </td>
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
                    {t("alerts.use")}
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
              <th>{t("alerts.rule")}</th>
              <th>{t("common.symbol")}</th>
              <th>{t("alerts.kind")}</th>
              <th>{t("alerts.condition")}</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((item) => (
              <tr key={item.rule_id}>
                <td>{item.rule_id}</td>
                <td className="ticker" title={item.instrument_id}>
                  {formatInstrumentLabel(item.instrument_id)}
                </td>
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
              <strong title={alert.instrument_id}>{formatInstrumentLabel(alert.instrument_id)}</strong>
              <span>{alert.message}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
