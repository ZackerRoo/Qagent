import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const files = {
  client: readFileSync(join(root, "src/api/client.ts"), "utf8"),
  portfolio: readFileSync(join(root, "src/pages/Portfolio.tsx"), "utf8"),
  styles: readFileSync(join(root, "src/styles.css"), "utf8"),
};

const checks = [
  ["client exposes fetchPaperLedger", files.client.includes("fetchPaperLedger")],
  ["client exposes fetchPaperValidation", files.client.includes("fetchPaperValidation")],
  ["client exposes runPaperValidation", files.client.includes("runPaperValidation")],
  ["client exposes fetchPaperSession", files.client.includes("fetchPaperSession")],
  ["client exposes startPaperSession", files.client.includes("startPaperSession")],
  ["portfolio renders paper session starter", files.portfolio.includes("PaperSessionStarter")],
  ["portfolio renders automatic validation center", files.portfolio.includes("PaperValidationCenter")],
  ["portfolio renders validation sample age", files.portfolio.includes("PaperValidationAgeCard")],
  ["portfolio renders validation batches", files.portfolio.includes("PaperValidationBatchList")],
  ["portfolio renders validation credibility", files.portfolio.includes("PaperValidationCredibilityCard")],
  ["portfolio shows 5/10/20 day validation", files.portfolio.includes("validation.windows")],
  ["portfolio can reset development records", files.portfolio.includes("reset_existing")],
  ["styles include paper session starter", files.styles.includes(".paper-session-starter")],
  ["styles include validation center", files.styles.includes(".paper-validation-center")],
  ["styles include validation window cards", files.styles.includes(".paper-validation-windows")],
  ["styles include validation age card", files.styles.includes(".paper-validation-age")],
  ["styles include validation batches", files.styles.includes(".paper-validation-batches")],
  ["styles include validation credibility", files.styles.includes(".paper-validation-credibility")],
  ["portfolio loads paper ledger", files.portfolio.includes("fetchPaperLedger")],
  ["portfolio renders equity curve", files.portfolio.includes("paper-ledger-curve")],
  ["portfolio renders return bars", files.portfolio.includes("paper-return-bars")],
  ["portfolio renders transaction ledger", files.portfolio.includes("PaperTransactionsPanel")],
  ["portfolio renders validation positions", files.portfolio.includes("PaperPositionsPanel")],
  ["styles include ledger chart shell", files.styles.includes(".paper-ledger-curve")],
  ["styles include return bars", files.styles.includes(".paper-return-bars")],
  ["styles include transaction table", files.styles.includes(".paper-flow-table")],
  ["styles include validation positions", files.styles.includes(".paper-position-grid")],
];

const failed = checks.filter(([, passed]) => !passed);
if (failed.length) {
  for (const [name] of failed) {
    console.error(`FAIL ${name}`);
  }
  process.exit(1);
}

console.log("paper ledger UI checks passed");
