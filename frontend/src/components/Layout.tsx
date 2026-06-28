import {
  Activity,
  Bell,
  BookOpenCheck,
  Briefcase,
  CalendarDays,
  History,
  ListFilter,
  Newspaper,
  Plus,
  Settings,
  RefreshCw,
  Star,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { fetchInstrumentSearch } from "../api/client";
import { useI18n } from "../i18n";
import type { TranslationKey } from "../i18n/catalog";
import type {
  DataProviderMode,
  ResearchProfile,
  TradableInstrument,
  UniverseRecord,
} from "../types";
import { formatInstrumentLabel } from "../lib/instruments";
import { localizeProfile } from "../lib/localize";
import { researchProfiles } from "../lib/profiles";

const nav = [
  { id: "today", labelKey: "nav.today", icon: CalendarDays },
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
  profile: ResearchProfile;
  scanEnabled: boolean;
  onSymbolsChange(value: string): void;
  onUniverseChange(value: string): void;
  onDataModeChange(mode: DataProviderMode): void;
  onProfileChange(value: ResearchProfile): void;
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
  profile,
  scanEnabled,
  onSymbolsChange,
  onUniverseChange,
  onDataModeChange,
  onProfileChange,
  onScan,
  children,
}: Props) {
  const { language, setLanguage, t } = useI18n();
  const [instrumentQuery, setInstrumentQuery] = useState("");
  const [instrumentOptions, setInstrumentOptions] = useState<TradableInstrument[]>([]);
  const [selectedLabels, setSelectedLabels] = useState<Record<string, string>>({});

  useEffect(() => {
    const query = instrumentQuery.trim();
    if (dataMode !== "free" || !query) {
      setInstrumentOptions([]);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchInstrumentSearch(query, 20)
        .then((result) => {
          if (!cancelled) {
            setInstrumentOptions(result.items);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setInstrumentOptions([]);
          }
        });
    }, 220);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [dataMode, instrumentQuery]);

  function handleAddInstrument() {
    const selection = resolveInstrumentSelection(instrumentQuery, instrumentOptions);
    if (!selection) {
      return;
    }
    const label = selection.label;
    if (label) {
      setSelectedLabels((current) => ({
        ...current,
        [selection.instrumentId]: label,
      }));
    }
    onSymbolsChange(mergeManualInstrument(symbols, selection.instrumentId));
    setInstrumentQuery("");
    setInstrumentOptions([]);
  }

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
          <div className="top-tools terminal-top-grid">
            <div className="session-strip">
              <span className="terminal-live">LIVE</span>
              <span>{t("top.dailyScan")}</span>
              <span>{t("top.alerts")}</span>
              <span>{dataMode === "free" ? t("top.freeData") : t("top.fixtureMode")}</span>
              <span>
                {t("top.profile")}: {localizeProfile(profile, language)}
              </span>
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
              <select
                aria-label={t("top.profile")}
                value={profile}
                onChange={(event) => onProfileChange(event.target.value as ResearchProfile)}
              >
                {researchProfiles.map((item) => (
                  <option key={item} value={item}>
                    {localizeProfile(item, language)}
                  </option>
                ))}
              </select>
              <div className="instrument-picker">
                <input
                  aria-label={t("top.tradableSearch")}
                  disabled={dataMode === "fixture"}
                  list="tradable-instruments"
                  placeholder={t("top.tradableSearch")}
                  value={instrumentQuery}
                  onChange={(event) => setInstrumentQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      handleAddInstrument();
                    }
                  }}
                />
                <datalist id="tradable-instruments">
                  {instrumentOptions.map((item) => (
                    <option key={item.instrument_id} value={item.label} />
                  ))}
                </datalist>
                <button
                  type="button"
                  className="square-action"
                  disabled={dataMode === "fixture" || !instrumentQuery.trim()}
                  onClick={handleAddInstrument}
                  title={t("top.addInstrument")}
                  aria-label={t("top.addInstrument")}
                >
                  <Plus size={16} />
                </button>
              </div>
              <input
                aria-label={t("top.scanSymbols")}
                className="selected-symbols-field"
                readOnly
                disabled={dataMode === "fixture"}
                title={formatSelectedSymbols(symbols, selectedLabels, false)}
                value={formatSelectedSymbols(symbols, selectedLabels)}
              />
              <button type="button" className="icon-action" onClick={onScan} disabled={isScanning || !scanEnabled}>
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
    free_default: "全A综合池",
    cn_liquid_starter: "A股30只流动性样本池",
    cn_index_kcb50: "科创50成分股",
    cn_index_csi300: "沪深300成分股",
    cn_index_csi500: "中证500成分股",
    cn_index_csi1000: "中证1000成分股",
    cn_index_chinext50: "创业板50成分股",
    cn_etf_core: "核心指数ETF",
    cn_theme_semiconductor: "半导体芯片主题",
    cn_theme_memory: "存储芯片主题",
    cn_theme_ai_compute: "AI算力供应链主题",
    cn_tech_starter: "A股科技入门池",
    cn_blue_chip_starter: "A股蓝筹入门池",
    cn_growth_starter: "A股成长入门池",
  };
  return labels[universe.universe_id] ?? universe.name;
}

function resolveInstrumentSelection(
  value: string,
  options: TradableInstrument[],
): { instrumentId: string; label?: string } | null {
  const trimmed = value.trim();
  const normalized = trimmed.toUpperCase();
  const exact = options.find(
    (item) =>
      item.label === trimmed ||
      item.name === trimmed ||
      item.symbol === normalized ||
      `${item.symbol}.${item.exchange}` === normalized,
  );
  if (exact) {
    return { instrumentId: exact.instrument_id, label: exact.label };
  }
  if (options.length > 0) {
    const top = options[0];
    return { instrumentId: top.instrument_id, label: top.label };
  }
  const token = normalized.match(/CN:\d{6}/)?.[0];
  if (token) {
    return { instrumentId: token };
  }
  const code = trimmed.match(/\b\d{6}\b/)?.[0];
  return code ? { instrumentId: `CN:${code}` } : null;
}

function mergeManualInstrument(currentSymbols: string, instrumentId: string): string {
  const current = currentSymbols
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
    .filter((item) => !isDynamicUniverseToken(item));
  if (!current.includes(instrumentId)) {
    current.push(instrumentId);
  }
  return current.join(",");
}

function isDynamicUniverseToken(symbol: string): boolean {
  return (
    symbol === "CN:ALL" ||
    symbol.startsWith("CN:INDEX:") ||
    symbol.startsWith("CN:ETF:")
  );
}

function formatSelectedSymbols(
  symbols: string,
  selectedLabels: Record<string, string>,
  summarize = true,
): string {
  const labels = symbols
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
    .map((item) => selectedLabels[item] ?? formatInstrumentLabel(item));
  if (!summarize || labels.length <= 3) {
    return labels.join(", ");
  }
  return `${labels.slice(0, 3).join(", ")} 等 ${labels.length} 个`;
}
