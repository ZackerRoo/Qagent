import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const history = readFileSync(new URL("../src/pages/History.tsx", import.meta.url), "utf8");
const client = readFileSync(new URL("../src/api/client.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

assert.match(client, /fetchValidationCenter/);
assert.match(client, /"\/validation-center"/);
assert.match(types, /export type ValidationCenterResponse/);
assert.match(history, /data-testid="validation-center-status"/);
assert.match(history, /data-validation-track=\{track\.key\}/);
assert.match(history, /V3\/V4 仅保留审计，不参与当前排名或模拟盘/);
assert.match(history, /旧 Walk-forward 已过期/);
assert.match(history, /它不会自动重跑/);
assert.match(history, /fetchValidationCenter\(dataMode/);
assert.match(history, /startWalkForwardJob\("2021-11-01", "2025-12-31", dataMode\)/);

console.log("validation center UI checks passed");
