import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const files = {
  dataHealth: resolve(__dirname, "../src/components/DataHealth.tsx"),
  opportunityDetail: resolve(__dirname, "../src/components/OpportunityDetail.tsx"),
  opportunityChart: resolve(__dirname, "../src/components/OpportunityChart.tsx"),
  today: resolve(__dirname, "../src/pages/Today.tsx"),
  brief: resolve(__dirname, "../src/pages/Brief.tsx"),
  app: resolve(__dirname, "../src/App.tsx"),
  opportunities: resolve(__dirname, "../src/pages/Opportunities.tsx"),
  overview: resolve(__dirname, "../src/pages/Overview.tsx"),
  localize: resolve(__dirname, "../src/lib/localize.ts"),
  styles: resolve(__dirname, "../src/styles.css"),
};

for (const path of Object.values(files)) {
  if (!existsSync(path)) {
    throw new Error(`missing ${path}`);
  }
}

const dataHealth = readFileSync(files.dataHealth, "utf8");
const opportunityDetail = readFileSync(files.opportunityDetail, "utf8");
const opportunityChart = readFileSync(files.opportunityChart, "utf8");
const today = readFileSync(files.today, "utf8");
const brief = readFileSync(files.brief, "utf8");
const app = readFileSync(files.app, "utf8");
const opportunities = readFileSync(files.opportunities, "utf8");
const overview = readFileSync(files.overview, "utf8");
const localize = readFileSync(files.localize, "utf8");
const styles = readFileSync(files.styles, "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(dataHealth.includes("DataHealthSummary"), "DataHealth must compute a reader-facing summary");
assert(dataHealth.includes("data-health-summary"), "DataHealth must render a compact summary row");
assert(dataHealth.includes("data-health-details"), "DataHealth must hide raw keys behind details");
assert(dataHealth.includes("systemKeys"), "DataHealth must classify system/debug fields instead of dumping all keys");
assert(dataHealth.includes("dataHealthScore"), "DataHealth must show a score/readiness style summary");
assert(opportunityDetail.indexOf("detail-kline-primary") < opportunityDetail.indexOf("recommendation-brief-card"), "Opportunity detail K-line must appear before text-heavy brief cards");
assert(opportunityChart.includes("SignalMarker"), "K-line chart must support recommendation signal markers");
assert(opportunityChart.includes("signal-marker"), "K-line chart must render visible signal marker SVG groups");
assert(opportunityDetail.includes("signalMarkersFromCard"), "Opportunity detail must pass signal markers into the K-line chart");
assert(today.includes("signalMarkersFromTodayCard"), "Today page must pass signal markers into the K-line chart");

assert(brief.includes("BriefKlineFocus"), "Brief page must render a K-line focus panel for top opportunities");
assert(brief.includes("fetchMarketBars"), "Brief page must fetch bars for the focus K-line");
assert(brief.includes("OpportunityCandlestickChart"), "Brief page must reuse the candlestick chart");
assert(brief.includes("brief-kline-focus"), "Brief page must expose K-line focus styling hook");
assert(brief.includes("ReasonDigest"), "Brief tables must summarize long reasons");
assert(brief.includes("briefSignalMarkers"), "Brief K-line must pass signal markers into the chart");
assert(app.includes("profiledBriefOpportunities") && app.includes("currentOpportunities={profiledBriefOpportunities}"), "Brief page must receive the same profiled opportunity set used by Today");
assert(brief.includes("buildTodayBriefFromOpportunities"), "Brief page must derive its default summary from the current Today opportunity set");
assert(
  brief.includes("brief_source: \"today_current_scan\"") &&
    brief.includes("currentOpportunities?.cards.length") &&
    brief.includes("if (todayBrief && briefMode === \"fast\")") &&
    brief.includes("setBrief(undefined);"),
  "Brief refresh must prefer current Today opportunities before falling back to a separate daily-brief request",
);
assert(brief.includes("BriefThemeRadarSummary"), "Brief page must show a same-source theme summary");

assert(opportunities.includes("ReasonDigest"), "Opportunity scan tables must summarize long reasons");
assert(opportunities.includes("reason-digest"), "Opportunity scan tables must render short reason chips");
assert(opportunities.includes("reason-details"), "Opportunity scan tables must keep full reasons in details");

assert(overview.includes("DataHealth"), "Overview must use compact DataHealth rather than raw debug chips");
assert(localize.includes("a_share_adjusted_price"), "Data health must translate adjusted-price readiness keys");
assert(localize.includes("a_share_announcements"), "Data health must translate announcement readiness keys");
assert(localize.includes("localizeDataHealthFallback"), "Data health must have a Chinese fallback for new backend keys");
assert(styles.includes(".data-health-summary"), "CSS must define compact data-health summary");
assert(styles.includes(".data-health-details"), "CSS must define data-health details");
assert(styles.includes("details.compact-data-health:not([open])"), "Closed data-health details must be hidden");
assert(styles.includes(".reason-digest"), "CSS must define short reason digest chips");
assert(styles.includes("details.reason-details:not([open])"), "Closed long reasons must be hidden");
assert(styles.includes(".brief-kline-focus"), "CSS must define brief K-line focus layout");
assert(styles.includes(".brief-sync-banner"), "CSS must define same-source brief banner");
assert(styles.includes(".brief-theme-grid"), "CSS must define same-source theme cards");
assert(styles.includes(".detail-kline-primary"), "CSS must define primary detail K-line layout");
assert(styles.includes(".signal-marker"), "CSS must define K-line signal marker styling");

console.log("dashboard noise ui checks passed");
