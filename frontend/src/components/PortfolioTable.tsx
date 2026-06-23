import { useI18n } from "../i18n";

export function PortfolioTable() {
  const { t } = useI18n();

  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{t("portfolio.title")}</h2>
        <span className="count">{t("common.manual")}</span>
      </div>
      <div className="empty-state">{t("common.noPositions")}</div>
    </section>
  );
}
