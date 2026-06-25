import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = resolve(__dirname, "../src/lib/instruments.ts");

if (!existsSync(sourcePath)) {
  throw new Error("missing src/lib/instruments.ts");
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

assert(mod.marketSymbol("CN:INDEX:KCB50") === "INDEX:KCB50", "multi-part CN tokens must keep the full symbol");
assert(mod.formatInstrumentLabel("CN:INDEX:KCB50") === "科创50成分股", "STAR 50 token must be readable");
assert(mod.formatInstrumentLabel("CN:ETF:KCB50") === "科创50ETF", "STAR 50 ETF token must be readable");
assert(mod.formatInstrumentLabel("CN:588000") === "科创50ETF 588000.SH", "SSE ETF suffix must be SH");
assert(mod.formatInstrumentLabel("CN:159949") === "创业板50ETF 159949.SZ", "SZSE ETF suffix must be SZ");

console.log("instrument labels ok: index tokens and ETFs are readable");
