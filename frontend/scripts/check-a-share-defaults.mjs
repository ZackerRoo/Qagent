import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appPath = resolve(__dirname, "../src/App.tsx");
const catalogPath = resolve(__dirname, "../src/i18n/catalog.ts");
const layoutPath = resolve(__dirname, "../src/components/Layout.tsx");

if (!existsSync(appPath)) {
  throw new Error("missing src/App.tsx");
}
if (!existsSync(catalogPath)) {
  throw new Error("missing src/i18n/catalog.ts");
}
if (!existsSync(layoutPath)) {
  throw new Error("missing src/components/Layout.tsx");
}

const app = readFileSync(appPath, "utf8");
const catalog = readFileSync(catalogPath, "utf8");
const layout = readFileSync(layoutPath, "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const defaultSymbolsMatch = app.match(/const DEFAULT_SYMBOLS\s*=\s*"([^"]+)"/s);
assert(defaultSymbolsMatch, "App.tsx must define DEFAULT_SYMBOLS");

const defaultSymbols = defaultSymbolsMatch[1].split(",");
assert(defaultSymbols.join(",") === "CN:ALL", "A-share default universe should use CN:ALL");
assert(app.includes('useState<DataProviderMode>("free")'), "App must default to free data mode");
assert(app.includes('useState("free_default")'), "App must default to free_default universe");
assert(
  app.includes("void loadCachedDashboard(dataMode);") &&
    app.includes("fetchLatestFullMarketBatchResult(mode, true)"),
  "App initial dashboard load must use the default free A-share data mode",
);
assert(catalog.includes('"top.eyebrow": "A 股"'), "Chinese eyebrow must present A-share focus");
assert(catalog.includes('"top.eyebrow": "A-Shares"'), "English eyebrow must present A-share focus");
assert(layout.includes("cn_index_kcb50"), "Universe menu must include STAR 50 constituents");
assert(layout.includes("cn_index_csi300"), "Universe menu must include CSI 300 constituents");
assert(layout.includes("cn_etf_core"), "Universe menu must include core index ETFs");

console.log("A-share defaults ok: CN:ALL free universe is the primary route");
