import type { Language } from "../i18n/catalog";
import { localizeDataHealthKey, localizeDataHealthValue } from "../lib/localize";

export function DataHealth({ data, language }: { data: Record<string, string>; language: Language }) {
  return (
    <div className="data-health">
      {Object.entries(data).map(([key, value]) => (
        <span key={key}>
          <strong>{localizeDataHealthKey(key, language)}</strong>{" "}
          {localizeDataHealthValue(value, language)}
        </span>
      ))}
    </div>
  );
}
