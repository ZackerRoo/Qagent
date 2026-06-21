import {
  Activity,
  Bell,
  BookOpenCheck,
  Briefcase,
  ListFilter,
  Settings,
  Star,
} from "lucide-react";
import type { ReactNode } from "react";

const nav = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "opportunities", label: "Opportunities", icon: ListFilter },
  { id: "watchlist", label: "Watchlist", icon: Star },
  { id: "portfolio", label: "Portfolio", icon: Briefcase },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "review", label: "Review", icon: BookOpenCheck },
  { id: "settings", label: "Settings", icon: Settings },
] as const;

export type PageId = (typeof nav)[number]["id"];

type Props = {
  page: PageId;
  onPageChange(page: PageId): void;
  rightPanel: ReactNode;
  children: ReactNode;
};

export function Layout({ page, onPageChange, rightPanel, children }: Props) {
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
            <span>Fixture mode</span>
          </div>
        </header>
        {children}
      </main>
      {rightPanel}
    </div>
  );
}
