import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const catalogPath = resolve(__dirname, "../src/i18n/catalog.ts");
const source = readFileSync(catalogPath, "utf8");

const keys = [
  "nav.brief",
  "nav.overview",
  "nav.opportunities",
  "nav.portfolio",
  "nav.settings",
  "top.title",
  "top.scan",
  "language.zh",
  "language.en",
  "brief.title",
  "portfolio.paperTitle",
];

const languageMatch = source.match(/languages\s*=\s*\[([^\]]+)\]/);
const languages = languageMatch
  ? [...languageMatch[1].matchAll(/"([^"]+)"/g)].map((match) => match[1])
  : [];

if (!Array.isArray(languages) || !languages.includes("zh") || !languages.includes("en")) {
  throw new Error("languages must include zh and en");
}

function extractKeys(language) {
  const start = source.indexOf(`  ${language}: {`);
  if (start < 0) {
    throw new Error(`missing ${language} catalog`);
  }

  const nextLanguage = languages.find((item) => item !== language && source.indexOf(`  ${item}: {`, start + 1) > start);
  const end = nextLanguage ? source.indexOf(`  ${nextLanguage}: {`, start + 1) : source.indexOf("\n} as const", start);
  const block = source.slice(start, end);
  return new Set([...block.matchAll(/"([^"]+)":/g)].map((match) => match[1]));
}

const catalogs = Object.fromEntries(languages.map((language) => [language, extractKeys(language)]));
const [baseLanguage, ...otherLanguages] = languages;
const baseKeys = catalogs[baseLanguage];

for (const lang of otherLanguages) {
  const missing = [...baseKeys].filter((key) => !catalogs[lang].has(key));
  const extra = [...catalogs[lang]].filter((key) => !baseKeys.has(key));
  if (missing.length || extra.length) {
    throw new Error(
      `${lang} catalog mismatch: missing=${missing.join(",") || "-"} extra=${extra.join(",") || "-"}`,
    );
  }
}

for (const lang of languages) {
  for (const key of keys) {
    if (!catalogs[lang].has(key)) {
      throw new Error(`missing ${lang}.${key}`);
    }
  }
}

console.log(`i18n catalog ok: ${languages.join(",")}, ${baseKeys.size} keys`);
