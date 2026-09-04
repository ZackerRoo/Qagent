import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const history = readFileSync(new URL("../src/pages/History.tsx", import.meta.url), "utf8");
const client = readFileSync(new URL("../src/api/client.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

assert.match(types, /export type WalkForwardRunSummary/);
assert.match(types, /runs: WalkForwardRunSummary\[\]/);
assert.match(client, /fetchLatestWalkForwardRun[\s\S]*Promise<WalkForwardRunSummary>/);
assert.match(client, /fetchWalkForwardRun\(runId: string\)[\s\S]*Promise<WalkForwardRun>/);
assert.doesNotMatch(
  client.match(/fetchLatestWalkForwardRun[\s\S]*?\n\}/)?.[0] ?? "",
  /include_payload/,
);
assert.match(history, /useState<WalkForwardRunSummary>/);
assert.match(history, /data-testid="walk-forward-summary-boundary"/);
assert.match(history, /完整组合、逐期快照和研究明细仅通过单次运行详情接口按需获取/);
assert.match(history, /async function loadWalkForwardDetail\(\)/);
assert.match(history, /await fetchWalkForwardRun\(runId\)/);
assert.equal(history.match(/fetchWalkForwardRun\(runId\)/g)?.length, 1);
assert.match(history, /data-testid="walk-forward-load-detail"/);
assert.match(history, /onClick=\{onLoadDetail\}/);
assert.match(history, /disabled=\{isDetailLoading\}/);
assert.match(history, /完整证据加载中/);
assert.match(history, /data-testid="walk-forward-detail-error"/);
assert.match(history, /data-testid="walk-forward-detail-loaded"/);
assert.match(history, /walkForwardDetail\?\.run_id === walkForward\?\.run_id/);

console.log("walk-forward summary UI checks passed");
