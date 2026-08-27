import fs from "node:fs";

const review = fs.readFileSync(new URL("../src/pages/Review.tsx", import.meta.url), "utf8");
const types = fs.readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

const checks = [
  ["types expose observed facts", types.includes("observed_facts: string[]")],
  ["types expose beneficiary chain", types.includes("beneficiary_chain: Array")],
  ["types expose financial transmission", types.includes("financial_transmission: Array")],
  ["types expose invalidation triggers", types.includes("invalidation_triggers: string[]")],
  ["review separates observed facts", review.includes("Observed")],
  ["review shows demand translation", review.includes("Demand translation")],
  ["review shows disconfirmation", review.includes("Disconfirm / invalidate")],
  ["review shows research-only decision effect", review.includes("Decision effect")],
];

const failed = checks.filter(([, ok]) => !ok);
if (failed.length) {
  for (const [label] of failed) console.error(`missing: ${label}`);
  process.exit(1);
}

console.log(`catalyst research UI contract ok (${checks.length} checks)`);
