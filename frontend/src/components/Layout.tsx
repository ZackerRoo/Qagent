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

import type { DataProviderMode, UniverseRecord } from "../types";

const nav = [
  { id: "brief", label: "Brief", icon: Newspaper },
  { id: "overview", label: "Overview", icon: Activity },
  { id: "opportunities", label: "Opportunities", icon: ListFilter },
  { id: "watchlist", label: "Watchlist", icon: Star },
  { id: "portfolio", label: "Portfolio", icon: Briefcase },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "history", label: "History", icon: History },
  { id: "review", label: "Review", icon: BookOpenCheck },
  { id: "settings", label: "Settings", icon: Settings },
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
  return (
    <div className="app-shell">
      <nav className="side-nav">
        <div className="brand">
          <span>Q</span>
          <strong>Qagent</strong>
        </div>
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={page === item.id ? "active" : ""}
              onClick={() => onPageChange(item.id)}
              title={item.label}
            >
              <Icon size={17} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
      <main className="workspace">
        <header className="top-bar">
          <div>
            <p className="eyebrow">US + CN</p>
            <h1>Opportunity Radar</h1>
          </div>
          <div className="session-strip">
            <span>Daily scan</span>
            <span>Key intraday alerts</span>
            <span>{dataMode === "free" ? "Free data" : "Fixture mode"}</span>
          </div>
          <div className="scan-controls">
            <div className="segment" aria-label="Data source">
              <button
                type="button"
                className={dataMode === "fixture" ? "active" : ""}
                onClick={() => onDataModeChange("fixture")}
              >
                Fixture
              </button>
              <button
                type="button"
                className={dataMode === "free" ? "active" : ""}
                onClick={() => onDataModeChange("free")}
              >
                Free
              </button>
            </div>
            <select
              aria-label="Universe"
              value={selectedUniverseId}
              onChange={(event) => onUniverseChange(event.target.value)}
            >
              {universes.map((universe) => (
                <option key={universe.universe_id} value={universe.universe_id}>
                  {universe.name}
                </option>
              ))}
            </select>
            <input
              aria-label="Scan symbols"
              disabled={dataMode === "fixture"}
              value={symbols}
              onChange={(event) => onSymbolsChange(event.target.value)}
            />
            <button type="button" className="icon-action" onClick={onScan} disabled={isScanning}>
              <RefreshCw size={16} />
              <span>{isScanning ? "Scanning" : "Scan"}</span>
            </button>
          </div>
        </header>
        {children}
      </main>
      {rightPanel}
    </div>
  );
}
