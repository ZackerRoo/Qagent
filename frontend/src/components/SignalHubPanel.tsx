import type { CSSProperties } from "react";

import { useI18n } from "../i18n";
import { localizeAlertKind, localizeReason, localizeStatus } from "../lib/localize";
import type { SignalAlertSuggestion, SignalHub } from "../types";

type Props = {
  hub?: SignalHub | null;
  onSaveAlerts?: (alerts: SignalAlertSuggestion[]) => void;
  isSavingAlerts?: boolean;
  saveMessage?: string;
};

export function SignalHubPanel({
  hub,
  onSaveAlerts,
  isSavingAlerts = false,
  saveMessage = "",
}: Props) {
  const { language, t } = useI18n();

  if (!hub) {
    return null;
  }

  const score = Math.round(hub.trust_score * 100);

  return (
    <div className="signal-hub-card">
      <div className="signal-hub-hero">
        <div className="signal-hub-score">
          <span>{t("signalHub.score")}</span>
          <strong>{score}</strong>
        </div>
        <div>
          <p className="eyebrow">{t("signalHub.title")}</p>
          <h3>{hub.label}</h3>
          <p>{localizeReason(hub.verdict, language)}</p>
          <small>{localizeReason(hub.next_action, language)}</small>
        </div>
      </div>

      <div className="signal-hub-components">
        {hub.components.map((component) => (
          <div key={component.key} className={`signal-hub-component signal-hub-${component.status}`}>
            <header>
              <span>{component.label}</span>
              <strong>{Math.round(component.score * 100)}</strong>
            </header>
            <i style={{ "--hub-width": `${Math.max(4, Math.round(component.score * 100))}%` } as CSSProperties} />
            <p>{localizeReason(component.detail, language)}</p>
          </div>
        ))}
      </div>

      <div className="signal-hub-grid">
        <section>
          <header>
            <h4>{t("signalHub.similar")}</h4>
            <span>{hub.similar_validation.verdict}</span>
          </header>
          <div className="signal-hub-metrics">
            <span>
              {t("common.samples")} <strong>{hub.similar_validation.sample_count}</strong>
            </span>
            <span>
              {t("signalHub.winRate")} <strong>{formatPct(hub.similar_validation.win_rate_10d)}</strong>
            </span>
            <span>
              {t("signalHub.avg10d")} <strong>{formatSigned(hub.similar_validation.avg_return_10d)}</strong>
            </span>
            <span>
              {t("signalHub.maxLoss")} <strong>{formatSigned(hub.similar_validation.max_loss_10d)}</strong>
            </span>
          </div>
          <p>{localizeReason(hub.similar_validation.summary, language)}</p>
        </section>

        <section>
          <header>
            <h4>{t("signalHub.timeline")}</h4>
            <span>{hub.rotation_context ?? "-"}</span>
          </header>
          <div className="signal-timeline">
            {hub.timeline.map((event) => (
              <div key={event.key} className={`signal-timeline-event signal-timeline-${event.severity}`}>
                <span>{event.label}</span>
                <strong>{localizeStatus(event.status, language)}</strong>
                <p>{localizeReason(event.detail, language)}</p>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="signal-alert-box">
        <div>
          <h4>{t("signalHub.alerts")}</h4>
          <p>{t("signalHub.alertsSubtitle")}</p>
        </div>
        <div className="signal-alert-list">
          {hub.alert_suggestions.map((alert) => (
            <span key={alert.rule_id}>
              {localizeAlertKind(alert.kind, language)} {alert.operator} {alert.threshold}
            </span>
          ))}
        </div>
        {onSaveAlerts && (
          <button
            className="icon-action"
            type="button"
            onClick={() => onSaveAlerts(hub.alert_suggestions)}
            disabled={isSavingAlerts || !hub.alert_suggestions.length}
          >
            {isSavingAlerts ? t("common.saving") : t("signalHub.saveAlerts")}
          </button>
        )}
        {saveMessage && <small>{saveMessage}</small>}
      </div>
    </div>
  );
}

function formatPct(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(1)}%`;
}

function formatSigned(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
