import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const settings = read("src/pages/Settings.tsx");
const client = read("src/api/client.ts");
const types = read("src/types.ts");
const styles = read("src/styles.css");

assert(settings.includes("AutomaticProcessingPanel"), "Settings must render automatic processing panel");
assert(settings.includes("runAutomationCycleNow"), "Settings must expose run-once automatic processing");
assert(settings.includes("startAutomationLoop"), "Settings must expose start automatic loop");
assert(settings.includes("stopAutomationLoop"), "Settings must expose stop automatic loop");
assert(settings.includes("自动处理系统"), "Settings must explain automatic processing in Chinese");
assert(client.includes("fetchAutomationScheduler"), "Client must fetch automation scheduler state");
assert(client.includes("runAutomationSchedulerOnce"), "Client must run one automation cycle");
assert(client.includes("startAutomationScheduler"), "Client must start automation scheduler");
assert(client.includes("stopAutomationScheduler"), "Client must stop automation scheduler");
assert(types.includes("AutoProcessingState"), "Types must define scheduler state");
assert(types.includes("AutoProcessingCycleResult"), "Types must define scheduler cycle result");
assert(styles.includes(".auto-processing-panel"), "Styles must define automatic processing panel");
assert(styles.includes(".auto-processing-metrics"), "Styles must define automatic processing metrics");

console.log("automation ui checks passed");
