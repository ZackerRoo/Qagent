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
assert(mod.formatInstrumentLabel("CN:920580") === "920580.BJ", "BSE 920xxx suffix must be BJ");
assert(mod.formatInstrumentLabel("CN:688059") === "华锐精密 688059.SH", "known STAR symbol must show Chinese name");
assert(mod.formatInstrumentLabel("CN:000021") === "深科技 000021.SZ", "known memory-chip symbol must show Chinese name");
assert(mod.formatInstrumentLabel("CN:000030") === "富奥股份 000030.SZ", "known A-share symbol must show Chinese name");
assert(
  mod.formatInstrumentDisplay("CN:688059", "688059.SH") === "华锐精密 688059.SH",
  "bare exchange labels should be upgraded to readable Chinese labels",
);
assert(
  mod.formatInstrumentDisplay("CN:000021", "CN:000021") === "深科技 000021.SZ",
  "raw CN labels should be upgraded to readable Chinese labels",
);
assert(
  mod.formatInstrumentText("688059.SH：等待触发", "CN:688059", "688059.SH") ===
    "华锐精密 688059.SH：等待触发",
  "bare code prefixes in recommendation text should be upgraded to readable labels",
);

console.log("instrument labels ok: index tokens and ETFs are readable");
