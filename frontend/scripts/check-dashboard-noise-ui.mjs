import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const files = {
  dataHealth: resolve(__dirname, "../src/components/DataHealth.tsx"),
  opportunityDetail: resolve(__dirname, "../src/components/OpportunityDetail.tsx"),
  brief: resolve(__dirname, "../src/pages/Brief.tsx"),
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
const brief = readFileSync(files.brief, "utf8");
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

assert(brief.includes("BriefKlineFocus"), "Brief page must render a K-line focus panel for top opportunities");
assert(brief.includes("fetchMarketBars"), "Brief page must fetch bars for the focus K-line");
assert(brief.includes("OpportunityCandlestickChart"), "Brief page must reuse the candlestick chart");
assert(brief.includes("brief-kline-focus"), "Brief page must expose K-line focus styling hook");
assert(brief.includes("ReasonDigest"), "Brief tables must summarize long reasons");

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
assert(styles.includes(".detail-kline-primary"), "CSS must define primary detail K-line layout");

console.log("dashboard noise ui checks passed");
