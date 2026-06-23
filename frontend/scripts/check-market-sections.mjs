import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = resolve(__dirname, "../src/lib/markets.ts");

if (!existsSync(sourcePath)) {
  throw new Error("missing src/lib/markets.ts");
}

const source = readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
});
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(compiled.outputText)}`);

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(Array.isArray(mod.MARKET_SECTIONS), "MARKET_SECTIONS must be exported");
assert(
  mod.MARKET_SECTIONS.map((section) => section.market).join(",") === "US,CN",
  "market section order must be US,CN",
);
assert(mod.getMarketFromInstrument("US:NVDA") === "US", "US symbols must map to US");
assert(mod.getMarketFromInstrument("CN:600519") === "CN", "CN symbols must map to CN");
assert(mod.getMarketFromInstrument("HK:00700") === null, "unknown prefixes must be ignored");

const sections = mod.createMarketSections(
  [
    { instrument_id: "CN:600519" },
    { instrument_id: "US:NVDA" },
    { instrument_id: "US:MSFT" },
  ],
  (item) => item.instrument_id,
);

assert(sections.length === 2, "createMarketSections must always return US and CN sections");
assert(sections[0].labelKey === "market.us", "US label key must be market.us");
assert(sections[1].labelKey === "market.cn", "CN label key must be market.cn");
assert(
  sections[0].items.map((item) => item.instrument_id).join(",") === "US:NVDA,US:MSFT",
  "US section must contain only US symbols in input order",
);
assert(
  sections[1].items.map((item) => item.instrument_id).join(",") === "CN:600519",
  "CN section must contain only CN symbols in input order",
);

console.log("market sections ok: US and CN split deterministically");
