import {
  Activity,
  Bell,
  BookOpenCheck,
  Briefcase,
  CalendarDays,
  ChevronDown,
  Database,
  History,
  ListFilter,
  Menu,
  MessageSquareText,
  Newspaper,
  Plus,
  Settings,
  SlidersHorizontal,
  Star,
  X,
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
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeProfile } from "../lib/localize";
import { researchProfiles } from "../lib/profiles";

const primaryNav = [
  { id: "today", labelKey: "nav.today", icon: CalendarDays },
  { id: "portfolio", labelKey: "nav.portfolio", icon: Briefcase },
  { id: "opportunities", labelKey: "nav.opportunities", icon: ListFilter },
  { id: "history", labelKey: "nav.history", icon: History },
] as const;

const secondaryNav = [
  { id: "brief", labelKey: "nav.brief", icon: Newspaper },
  { id: "overview", labelKey: "nav.overview", icon: Activity },
  { id: "watchlist", labelKey: "nav.watchlist", icon: Star },
  { id: "alerts", labelKey: "nav.alerts", icon: Bell },
  { id: "review", labelKey: "nav.review", icon: BookOpenCheck },
  { id: "settings", labelKey: "nav.settings", icon: Settings },
] as const;

const nav = [...primaryNav, ...secondaryNav] as const;

export type PageId = (typeof nav)[number]["id"];

type Props = {
  page: PageId;
  onPageChange(page: PageId): void;
  rightPanel: ReactNode;
  dataMode: DataProviderMode;
  symbols: string;
  universes: UniverseRecord[];
  selectedUniverseId: string;
  profile: ResearchProfile;
  resultStatus: "loading" | "ready" | "error";
  opportunityCount: number;
  onSymbolsChange(value: string): void;
  onUniverseChange(value: string): void;
  onDataModeChange(mode: DataProviderMode): void;
  onProfileChange(value: ResearchProfile): void;
  children: ReactNode;
};

export function Layout({
  page,
  onPageChange,
  rightPanel,
  dataMode,
  symbols,
  universes,
  selectedUniverseId,
  profile,
  resultStatus,
  opportunityCount,
  onSymbolsChange,
  onUniverseChange,
  onDataModeChange,
  onProfileChange,
  children,
}: Props) {
  const { language, setLanguage, t } = useI18n();
  const pageTitle = getPageTitle(page, t);
  const [instrumentQuery, setInstrumentQuery] = useState("");
  const [instrumentOptions, setInstrumentOptions] = useState<TradableInstrument[]>([]);
  const [selectedLabels, setSelectedLabels] = useState<Record<string, string>>({});
  const [agentOpen, setAgentOpen] = useState(false);
  const visibleUniverses = universes.filter((universe) => universe.universe_id !== "fixture_dev");
  const selectedUniverseValue = visibleUniverses.some(
    (universe) => universe.universe_id === selectedUniverseId,
  )
    ? selectedUniverseId
    : "free_default";

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
    <div className={`app-shell ${agentOpen ? "agent-is-open" : ""}`}>
      <nav className="side-nav">
        <div className="brand">
          <span>Q</span>
          <strong>Qagent</strong>
        </div>
        <p className="nav-section-label">{language === "zh" ? "工作台" : "Workspace"}</p>
        {primaryNav.map((item) => {
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
        <details className="nav-more">
          <summary>
            <Menu size={17} />
            <span>{language === "zh" ? "更多工具" : "More tools"}</span>
            <ChevronDown className="nav-more-chevron" size={14} />
          </summary>
          <div className="nav-more-list">
            {secondaryNav.map((item) => {
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
          </div>
        </details>
      </nav>
      <main className="workspace">
        <header className="top-bar">
          <div className="top-title">
            <p className="eyebrow">
              {language === "zh" ? "A股研究模拟工作台" : "A-share research workspace"}
            </p>
            <h1>{pageTitle}</h1>
          </div>
          <div className="workspace-status">
            <span className="workspace-status-item is-manual">
              <i />
              {language === "zh" ? "手动模式" : "Manual mode"}
            </span>
            <span className="workspace-status-item">
              <Database size={14} />
              {language === "zh" ? "运行数据" : "Runtime data"}
            </span>
            <span className={`workspace-status-item result-${resultStatus}`}>
              {formatResultStatus(resultStatus, opportunityCount, language)}
            </span>
            <button
              type="button"
              className={agentOpen ? "workspace-icon-button active" : "workspace-icon-button"}
              onClick={() => setAgentOpen((current) => !current)}
              title={language === "zh" ? "研究助手" : "Research assistant"}
              aria-label={language === "zh" ? "研究助手" : "Research assistant"}
              aria-pressed={agentOpen}
            >
              <MessageSquareText size={17} />
            </button>
          </div>
          <details className="workspace-controls">
            <summary>
              <SlidersHorizontal size={16} />
              <span>{language === "zh" ? "范围与偏好" : "Scope and preferences"}</span>
              <small>
                {formatSelectedControlSummary(
                  selectedUniverseValue,
                  visibleUniverses,
                  profile,
                  language,
                )}
              </small>
              <ChevronDown size={15} />
            </summary>
            <div className="top-tools terminal-top-grid">
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
              <select
                aria-label={t("top.universe")}
                value={selectedUniverseValue}
                onChange={(event) => onUniverseChange(event.target.value)}
              >
                {visibleUniverses.map((universe) => (
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
            </div>
          </div>
          </details>
        </header>
        {children}
      </main>
      {agentOpen && (
        <div className="agent-drawer">
          <button
            type="button"
            className="agent-drawer-close"
            onClick={() => setAgentOpen(false)}
            title={language === "zh" ? "关闭研究助手" : "Close research assistant"}
            aria-label={language === "zh" ? "关闭研究助手" : "Close research assistant"}
          >
            <X size={17} />
          </button>
          {rightPanel}
        </div>
      )}
    </div>
  );
}

function getPageTitle(page: PageId, t: (key: TranslationKey) => string): string {
  const item = nav.find((navItem) => navItem.id === page);
  return item ? t(item.labelKey as TranslationKey) : "Qagent";
}

function formatUniverseName(universe: UniverseRecord, language: "zh" | "en"): string {
  if (language !== "zh") {
    return universe.name;
  }
  const labels: Record<string, string> = {
    fixture_dev: "开发调试池",
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
    .map((item) => formatInstrumentDisplay(item, selectedLabels[item]));
  if (!summarize || labels.length <= 3) {
    return labels.join(", ");
  }
  return `${labels.slice(0, 3).join(", ")} 等 ${labels.length} 个`;
}

function formatResultStatus(
  status: "loading" | "ready" | "error",
  count: number,
  language: "zh" | "en",
): string {
  if (status === "loading") {
    return language === "zh" ? "结果载入中" : "Loading result";
  }
  if (status === "error") {
    return language === "zh" ? "结果待重试" : "Result unavailable";
  }
  return language === "zh" ? `${count} 个候选` : `${count} candidates`;
}

function formatSelectedControlSummary(
  selectedUniverseId: string,
  universes: UniverseRecord[],
  profile: ResearchProfile,
  language: "zh" | "en",
): string {
  const universe = universes.find((item) => item.universe_id === selectedUniverseId);
  const universeLabel = universe ? formatUniverseName(universe, language) : selectedUniverseId;
  return `${universeLabel} · ${localizeProfile(profile, language)}`;
}
