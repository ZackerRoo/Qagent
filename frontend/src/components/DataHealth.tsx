import type { Language } from "../i18n/catalog";
import { localizeDataHealthKey, localizeDataHealthValue } from "../lib/localize";

export function DataHealth({ data, language }: { data: Record<string, string>; language: Language }) {
  const entries = Object.entries(data).filter(([, value]) => String(value ?? "") !== "");
  if (!entries.length) {
    return null;
  }
  const summary = DataHealthSummary(data, language);

  return (
    <details className={`data-health compact-data-health data-health-${summary.tone}`}>
      <summary className="data-health-summary">
        <span>{language === "zh" ? "数据可信度" : "Data confidence"}</span>
        <strong>{summary.score}/100</strong>
        <em>{summary.issueCount ? `${summary.issueCount} ${language === "zh" ? "项缺口" : "gaps"}` : language === "zh" ? "可用" : "ready"}</em>
        <small>{summary.primaryIssue}</small>
      </summary>
      <div className="data-health-highlights">
        {summary.highlights.map(([key, value]) => (
          <span key={key}>
            <strong>{localizeDataHealthKey(key, language)}</strong>{" "}
            {localizeDataHealthValue(value, language)}
          </span>
        ))}
      </div>
      <div className="data-health-details">
        {entries.map(([key, value]) => (
          <span key={key} className={systemKeys.some((pattern) => key.includes(pattern)) ? "system-field" : ""}>
            <strong>{localizeDataHealthKey(key, language)}</strong>{" "}
            {localizeDataHealthValue(value, language)}
          </span>
        ))}
      </div>
    </details>
  );
}

type HealthTone = "good" | "watch" | "risk";

type HealthSummary = {
  score: number;
  issueCount: number;
  primaryIssue: string;
  tone: HealthTone;
  highlights: [string, string][];
};

const systemKeys = [
  "cache",
  "cards",
  "rows",
  "batches",
  "symbols",
  "returned",
  "requested",
  "items",
  "provider",
  "source",
  "mode",
];

function DataHealthSummary(data: Record<string, string>, language: Language): HealthSummary {
  const entries = Object.entries(data).filter(([, value]) => String(value ?? "") !== "");
  const explicitScore = dataHealthScore(entries);
  const issues = entries.filter(([key, value]) => isHealthIssue(key, value));
  const score = explicitScore ?? Math.max(35, 100 - issues.length * 8);
  const primary = issues[0] ?? entries.find(([key]) => key.includes("readiness") || key.includes("score")) ?? entries[0];
  const highlights = [
    ...issues.slice(0, 3),
    ...entries.filter(([key]) => key.includes("readiness") || key.includes("score")).slice(0, 2),
  ];
  const uniqueHighlights = dedupeHighlights(highlights).slice(0, 5);
  return {
    score,
    issueCount: issues.length,
    primaryIssue: primary
      ? `${localizeDataHealthKey(primary[0], language)} ${localizeDataHealthValue(primary[1], language)}`
      : language === "zh"
        ? "暂无数据源状态"
        : "No data-source status",
    tone: score >= 75 ? "good" : score >= 55 ? "watch" : "risk",
    highlights: uniqueHighlights.length ? uniqueHighlights : entries.slice(0, 4),
  };
}

function dataHealthScore(entries: [string, string][]): number | null {
  const candidates = entries
    .filter(([key]) => key.includes("readiness") || key.endsWith("_score") || key.includes("data_score"))
    .map(([, value]) => Number(value))
    .filter((value) => Number.isFinite(value));
  if (!candidates.length) {
    return null;
  }
  const normalized = candidates.map((value) => (value <= 1 ? value * 100 : value));
  return Math.max(0, Math.min(100, Math.round(normalized.reduce((sum, value) => sum + value, 0) / normalized.length)));
}

function isHealthIssue(key: string, value: string): boolean {
  const normalized = `${key} ${value}`.toLowerCase();
  if (normalized.includes("missing") || normalized.includes("partial") || normalized.includes("error")) {
    return true;
  }
  if (normalized.includes("unavailable") || normalized.includes("unknown") || normalized.includes("blocked")) {
    return true;
  }
  return false;
}

function dedupeHighlights(items: [string, string][]): [string, string][] {
  const seen = new Set<string>();
  const result: [string, string][] = [];
  for (const item of items) {
    if (seen.has(item[0])) {
      continue;
    }
    seen.add(item[0]);
    result.push(item);
  }
  return result;
}
