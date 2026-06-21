import type { OpportunityStatus } from "../types";

const labels: Record<OpportunityStatus, string> = {
  new_idea: "New",
  watch: "Watch",
  setup_ready: "Setup",
  triggered: "Triggered",
  extended: "Extended",
  active: "Active",
  risk_elevated: "Risk",
  invalidated: "Invalid",
  closed: "Closed",
  postmortem_done: "Reviewed",
};

export function StatusBadge({ status }: { status: OpportunityStatus }) {
  return <span className={`status status-${status}`}>{labels[status]}</span>;
}
