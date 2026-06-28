import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const stylesPath = resolve(__dirname, "../src/styles.css");
const layoutPath = resolve(__dirname, "../src/components/Layout.tsx");

for (const path of [stylesPath, layoutPath]) {
  if (!existsSync(path)) {
    throw new Error(`missing ${path}`);
  }
}

const styles = readFileSync(stylesPath, "utf8");
const layout = readFileSync(layoutPath, "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const requiredTokens = [
  "--terminal-bg",
  "--terminal-panel",
  "--terminal-panel-strong",
  "--terminal-border",
  "--terminal-text",
  "--terminal-muted",
  "--terminal-green",
  "--terminal-red",
  "--terminal-yellow",
];

for (const token of requiredTokens) {
  assert(styles.includes(token), `missing terminal token ${token}`);
}

const requiredSelectors = [
  ".app-shell",
  ".workspace",
  ".top-bar",
  ".side-nav",
  ".panel",
  ".agent-panel",
  ".metric-grid div",
  ".table-shell",
  "table",
  "th",
  "td",
  ".opportunity-card",
  ".strategy-health-tile",
  ".chart-shell svg",
  ".validation-chart-meta",
  ".bar-caption-grid",
  ".signal-console",
  ".signal-distribution",
  ".portfolio-plan-card",
  ".context-panel",
  ".recommendation-card",
  ".terminal-live",
];

for (const selector of requiredSelectors) {
  assert(styles.includes(selector), `missing terminal selector ${selector}`);
}

assert(layout.includes("terminal-live"), "Layout must render a live/session terminal status indicator");
assert(layout.includes("terminal-top-grid"), "Layout must expose a terminal top grid for all pages");
assert(
  !styles.includes("background: #f5f7fa"),
  "global background must not remain the old light dashboard background",
);
assert(
  !styles.includes("background: #ffffff;"),
  "global components must not keep broad pure-white panel backgrounds",
);
assert(
  /\.validation-grid\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(styles),
  "validation charts should default to full-width readable cards",
);

console.log("terminal ui ok: global shell, panels, tables, cards, charts, and controls use the dark quant terminal theme");
