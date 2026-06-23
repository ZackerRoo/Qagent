import { useI18n } from "../i18n";

export function AlertList() {
  const { t } = useI18n();

  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{t("alerts.title")}</h2>
        <span className="count">0</span>
      </div>
      <div className="empty-state">{t("alerts.noTriggered")}</div>
    </section>
  );
}
