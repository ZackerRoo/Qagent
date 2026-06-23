import { useI18n } from "../i18n";
import type { OpportunityStatus } from "../types";

export function StatusBadge({ status }: { status: OpportunityStatus }) {
  const { t } = useI18n();
  const labels: Record<OpportunityStatus, string> = {
    new_idea: t("status.new"),
    watch: t("status.watch"),
    setup_ready: t("status.setup"),
    triggered: t("status.triggered"),
    extended: t("status.extended"),
    active: t("status.active"),
    risk_elevated: t("status.risk"),
    invalidated: t("status.invalid"),
    closed: t("status.closed"),
    postmortem_done: t("status.reviewed"),
  };

  return <span className={`status status-${status}`}>{labels[status]}</span>;
}
