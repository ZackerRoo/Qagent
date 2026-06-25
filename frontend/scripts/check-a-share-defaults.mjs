import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appPath = resolve(__dirname, "../src/App.tsx");
const catalogPath = resolve(__dirname, "../src/i18n/catalog.ts");

if (!existsSync(appPath)) {
  throw new Error("missing src/App.tsx");
}
if (!existsSync(catalogPath)) {
  throw new Error("missing src/i18n/catalog.ts");
}

const app = readFileSync(appPath, "utf8");
const catalog = readFileSync(catalogPath, "utf8");

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
  app.includes('void loadDashboard("free", DEFAULT_SYMBOLS);'),
  "App initial dashboard load must use free A-share data",
);
assert(catalog.includes('"top.eyebrow": "A 股"'), "Chinese eyebrow must present A-share focus");
assert(catalog.includes('"top.eyebrow": "A-Shares"'), "English eyebrow must present A-share focus");

console.log("A-share defaults ok: CN:ALL free universe is the primary route");
