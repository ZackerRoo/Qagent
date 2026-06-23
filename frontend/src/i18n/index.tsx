import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { type Language, type TranslationKey, translate } from "./catalog";

type I18nContextValue = {
  language: Language;
  setLanguage(language: Language): void;
  t(key: TranslationKey): string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

function initialLanguage(): Language {
  const stored = window.localStorage.getItem("qagent.language");
  return stored === "en" || stored === "zh" ? stored : "zh";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(initialLanguage);

  function setLanguage(next: Language) {
    window.localStorage.setItem("qagent.language", next);
    setLanguageState(next);
  }

  const value = useMemo(
    () => ({
      language,
      setLanguage,
      t: (key: TranslationKey) => translate(key, language),
    }),
    [language],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return value;
}
