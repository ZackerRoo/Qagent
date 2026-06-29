import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const todayPath = resolve(__dirname, "../src/pages/Today.tsx");
const tablePath = resolve(__dirname, "../src/components/OpportunityTable.tsx");
const stylesPath = resolve(__dirname, "../src/styles.css");
const catalogPath = resolve(__dirname, "../src/i18n/catalog.ts");

for (const path of [todayPath, tablePath, stylesPath, catalogPath]) {
  if (!existsSync(path)) {
    throw new Error(`missing ${path}`);
  }
}

const today = readFileSync(todayPath, "utf8");
const table = readFileSync(tablePath, "utf8");
const styles = readFileSync(stylesPath, "utf8");
const catalog = readFileSync(catalogPath, "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(today.includes("SignalCommandCenter"), "Today page must render the signal command center");
assert(today.includes("SignalDistribution"), "Today page must render signal distribution");
assert(today.includes("ResearchCommandCenterPanel"), "Today page must render the research command center");
assert(today.includes("CompactDataHealth"), "Today page must keep data health in a compact panel");
assert(today.includes("signal-console"), "Today page must expose signal-console layout class");
assert(table.includes("SignalStrengthBar"), "Opportunity cards must render a signal strength bar");
assert(table.includes("opportunity-signal-row"), "Opportunity cards must show rank/factor/conviction as a signal row");
assert(styles.includes(".signal-console"), "CSS must define signal-console layout");
assert(styles.includes(".signal-distribution"), "CSS must define signal distribution");
assert(styles.includes(".research-center"), "CSS must define research command center layout");
assert(styles.includes(".signal-strength-bar"), "CSS must define signal strength bars");
assert(styles.includes(".market-board-grid"), "CSS must define market board grid");
assert(catalog.includes('"today.signalConsole"'), "i18n catalog must include signal console labels");
assert(catalog.includes('"today.signalDistribution"'), "i18n catalog must include signal distribution labels");
assert(catalog.includes('"research.title"'), "i18n catalog must include research center labels");

console.log("today ui ok: command center, research center, signal distribution, compact health, and strength bars are present");
