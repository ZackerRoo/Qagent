import {
  Activity,
  Bell,
  BookOpenCheck,
  Briefcase,
  History,
  ListFilter,
  Newspaper,
  Settings,
  RefreshCw,
  Star,
} from "lucide-react";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";
import type { TranslationKey } from "../i18n/catalog";
import type { DataProviderMode, UniverseRecord } from "../types";

const nav = [
  { id: "brief", labelKey: "nav.brief", icon: Newspaper },
  { id: "overview", labelKey: "nav.overview", icon: Activity },
  { id: "opportunities", labelKey: "nav.opportunities", icon: ListFilter },
  { id: "watchlist", labelKey: "nav.watchlist", icon: Star },
  { id: "portfolio", labelKey: "nav.portfolio", icon: Briefcase },
  { id: "alerts", labelKey: "nav.alerts", icon: Bell },
  { id: "history", labelKey: "nav.history", icon: History },
  { id: "review", labelKey: "nav.review", icon: BookOpenCheck },
  { id: "settings", labelKey: "nav.settings", icon: Settings },
] as const;

export type PageId = (typeof nav)[number]["id"];

type Props = {
  page: PageId;
  onPageChange(page: PageId): void;
  rightPanel: ReactNode;
  dataMode: DataProviderMode;
  isScanning: boolean;
  symbols: string;
  universes: UniverseRecord[];
  selectedUniverseId: string;
  onSymbolsChange(value: string): void;
  onUniverseChange(value: string): void;
  onDataModeChange(mode: DataProviderMode): void;
  onScan(): void;
  children: ReactNode;
};

export function Layout({
  page,
  onPageChange,
  rightPanel,
  dataMode,
  isScanning,
  symbols,
  universes,
  selectedUniverseId,
  onSymbolsChange,
  onUniverseChange,
  onDataModeChange,
  onScan,
  children,
}: Props) {
  const { language, setLanguage, t } = useI18n();

  return (
    <div className="app-shell">
      <nav className="side-nav">
        <div className="brand">
          <span>Q</span>
          <strong>Qagent</strong>
        </div>
        {nav.map((item) => {
          const Icon = item.icon;
          const label = t(item.labelKey as TranslationKey);
          return (
            <button
              key={item.id}
              type="button"
              className={page === item.id ? "active" : ""}
              onClick={() => onPageChange(item.id)}
              title={label}
            >
              <Icon size={17} />
              <span>{label}</span>
            </button>
          );
        })}
      </nav>
      <main className="workspace">
        <header className="top-bar">
          <div className="top-title">
            <p className="eyebrow">{t("top.eyebrow")}</p>
            <h1>{t("top.title")}</h1>
          </div>
          <div className="top-tools">
            <div className="session-strip">
              <span>{t("top.dailyScan")}</span>
              <span>{t("top.alerts")}</span>
              <span>{dataMode === "free" ? t("top.freeData") : t("top.fixtureMode")}</span>
            </div>
            <div className="scan-controls">
              <div className="segment language-toggle" aria-label="Language">
                <button
                  type="button"
                  className={language === "zh" ? "active" : ""}
                  onClick={() => setLanguage("zh")}
                >
                  {t("language.zh")}
                </button>
                <button
                  type="button"
                  className={language === "en" ? "active" : ""}
                  onClick={() => setLanguage("en")}
                >
                  {t("language.en")}
                </button>
              </div>
              <div className="segment data-mode-toggle" aria-label={t("top.dataSource")}>
                <button
                  type="button"
                  className={dataMode === "fixture" ? "active" : ""}
                  onClick={() => onDataModeChange("fixture")}
                >
                  {t("top.fixture")}
                </button>
                <button
                  type="button"
                  className={dataMode === "free" ? "active" : ""}
                  onClick={() => onDataModeChange("free")}
                >
                  {t("top.free")}
                </button>
              </div>
              <select
                aria-label={t("top.universe")}
                value={selectedUniverseId}
                onChange={(event) => onUniverseChange(event.target.value)}
              >
                {universes.map((universe) => (
                  <option key={universe.universe_id} value={universe.universe_id}>
                    {formatUniverseName(universe, language)}
                  </option>
                ))}
              </select>
              <input
                aria-label={t("top.scanSymbols")}
                disabled={dataMode === "fixture"}
                value={symbols}
                onChange={(event) => onSymbolsChange(event.target.value)}
              />
              <button type="button" className="icon-action" onClick={onScan} disabled={isScanning}>
                <RefreshCw size={16} />
                <span>{isScanning ? t("top.scanning") : t("top.scan")}</span>
              </button>
            </div>
          </div>
        </header>
        {children}
      </main>
      {rightPanel}
    </div>
  );
}

function formatUniverseName(universe: UniverseRecord, language: "zh" | "en"): string {
  if (language !== "zh") {
    return universe.name;
  }
  const labels: Record<string, string> = {
    fixture_dev: "样例开发池",
    free_default: "A股免费默认池",
    cn_tech_starter: "A股科技入门池",
    cn_blue_chip_starter: "A股蓝筹入门池",
    cn_growth_starter: "A股成长入门池",
  };
  return labels[universe.universe_id] ?? universe.name;
}
