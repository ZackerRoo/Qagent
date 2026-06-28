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
  ["portfolio loads paper ledger", files.portfolio.includes("fetchPaperLedger")],
  ["portfolio renders equity curve", files.portfolio.includes("paper-ledger-curve")],
  ["portfolio renders return bars", files.portfolio.includes("paper-return-bars")],
  ["styles include ledger chart shell", files.styles.includes(".paper-ledger-curve")],
  ["styles include return bars", files.styles.includes(".paper-return-bars")],
];

const failed = checks.filter(([, passed]) => !passed);
if (failed.length) {
  for (const [name] of failed) {
    console.error(`FAIL ${name}`);
  }
  process.exit(1);
}

console.log("paper ledger UI checks passed");
