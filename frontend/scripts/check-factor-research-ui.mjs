import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const files = {
  types: readFileSync(join(root, "src/types.ts"), "utf8"),
  client: readFileSync(join(root, "src/api/client.ts"), "utf8"),
  portfolio: readFileSync(join(root, "src/pages/Portfolio.tsx"), "utf8"),
  styles: readFileSync(join(root, "src/styles.css"), "utf8"),
};
const packageJson = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

const checks = [
  ["types expose experiment records", files.types.includes("FactorResearchExperiment")],
  ["client fetches experiment records", files.client.includes("fetchFactorResearchExperiments")],
  ["client starts a frozen experiment", files.client.includes("startFactorResearchExperiment")],
  ["portfolio renders the research panel", files.portfolio.includes("FactorModelResearchPanel")],
  ["portfolio compares baseline and challenger", files.portfolio.includes("lightgbm_challenger")],
  ["portfolio makes paper isolation explicit", files.portfolio.includes("不会自动替换模拟盘模型")],
  ["types expose execution-sized shadow head", files.types.includes("FactorShadowExecutionHeadEvaluation")],
  ["portfolio renders Top10 cap3 evidence", files.portfolio.includes("Top10/cap3")],
  ["portfolio shows constraint-matched head lift", files.portfolio.includes("头部增益中位数")],
  ["portfolio localizes head promotion gates", files.portfolio.includes("execution_head_lift_not_positive")],
  ["research polling does not use a paper refresh interval", !files.portfolio.includes("setInterval")],
  ["styles include the research panel", files.styles.includes(".factor-model-research")],
  [
    "package exposes factor research UI check",
    packageJson.scripts?.["check:factor-research-ui"] ===
      "node scripts/check-factor-research-ui.mjs",
  ],
];

const failed = checks.filter(([, passed]) => !passed);
if (failed.length) {
  for (const [name] of failed) console.error(`FAIL ${name}`);
  process.exit(1);
}

console.log("factor research UI checks passed");
