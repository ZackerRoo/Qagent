import { useEffect, useMemo, useState } from "react";

import { fetchOpportunities, fetchOverview, fetchUniverses, saveUniverse } from "./api/client";
import { AgentPanel } from "./components/AgentPanel";
import { Layout, type PageId } from "./components/Layout";
import { Alerts } from "./pages/Alerts";
import { Brief } from "./pages/Brief";
import { History } from "./pages/History";
import { Opportunities } from "./pages/Opportunities";
import { Overview } from "./pages/Overview";
import { Portfolio } from "./pages/Portfolio";
import { Review } from "./pages/Review";
import { Settings } from "./pages/Settings";
import { Watchlist } from "./pages/Watchlist";
import type {
  DataProviderMode,
  OpportunitiesResponse,
  OpportunityCard,
  OverviewResponse,
  UniverseCreate,
  UniverseRecord,
} from "./types";

const DEFAULT_SYMBOLS = "US:AAPL,US:NVDA,US:MSFT,CN:000001,CN:600519";

export default function App() {
  const [page, setPage] = useState<PageId>("brief");
  const [overview, setOverview] = useState<OverviewResponse>();
  const [opportunities, setOpportunities] = useState<OpportunitiesResponse>();
  const [selectedCard, setSelectedCard] = useState<OpportunityCard>();
  const [dataMode, setDataMode] = useState<DataProviderMode>("fixture");
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [universes, setUniverses] = useState<UniverseRecord[]>([]);
  const [selectedUniverseId, setSelectedUniverseId] = useState("fixture_dev");
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState("");

  async function loadDashboard(mode: DataProviderMode, symbolText: string) {
    setIsScanning(true);
    setError("");
    try {
      const params = {
        provider: mode,
        symbols: mode === "free" ? symbolText : undefined,
      };
      const [overviewResult, opportunitiesResult] = await Promise.all([
        fetchOverview(params),
        fetchOpportunities(params),
      ]);
      setOverview(overviewResult);
      setOpportunities(opportunitiesResult);
      setSelectedCard(opportunitiesResult.cards[0]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load dashboard");
    } finally {
      setIsScanning(false);
    }
  }

  useEffect(() => {
    void loadDashboard("fixture", DEFAULT_SYMBOLS);
    void refreshUniverses();
  }, []);

  async function refreshUniverses() {
    const result = await fetchUniverses();
    setUniverses(result.universes);
  }

  function handleDataModeChange(mode: DataProviderMode) {
    setDataMode(mode);
    void loadDashboard(mode, symbols);
  }

  function handleScan() {
    void loadDashboard(dataMode, symbols);
  }

  function handleUniverseChange(universeId: string) {
    setSelectedUniverseId(universeId);
    const universe = universes.find((item) => item.universe_id === universeId);
    if (!universe) {
      return;
    }
    setSymbols(universe.symbols.join(","));
    setDataMode(universe.universe_id === "fixture_dev" ? "fixture" : "free");
  }

  async function handleSaveUniverse(payload: UniverseCreate) {
    const saved = await saveUniverse(payload);
    await refreshUniverses();
    setSelectedUniverseId(saved.universe_id);
    setSymbols(saved.symbols.join(","));
    setDataMode("free");
    return saved;
  }

  const content = useMemo(() => {
    if (error) {
      return <section className="panel error">{error}</section>;
    }

    switch (page) {
      case "brief":
        return <Brief dataMode={dataMode} symbols={symbols} />;
      case "overview":
        return (
          <Overview
            overview={overview}
            selectedCardId={selectedCard?.card_id}
            onSelect={setSelectedCard}
          />
        );
      case "opportunities":
        return (
          <Opportunities
            cards={opportunities?.cards ?? []}
            items={opportunities?.items ?? []}
            strategyHealth={opportunities?.strategy_health ?? []}
            selectedCard={selectedCard}
            onSelect={setSelectedCard}
          />
        );
      case "watchlist":
        return <Watchlist />;
      case "portfolio":
        return <Portfolio dataMode={dataMode} />;
      case "alerts":
        return <Alerts dataMode={dataMode} />;
      case "history":
        return <History dataMode={dataMode} symbols={symbols} />;
      case "review":
        return <Review symbols={symbols} />;
      case "settings":
        return (
          <Settings
            dataMode={dataMode}
            symbols={symbols}
            universes={universes}
            onSaveUniverse={handleSaveUniverse}
          />
        );
      default:
        return null;
    }
  }, [dataMode, error, opportunities, overview, page, selectedCard, symbols]);

  return (
    <Layout
      page={page}
      onPageChange={setPage}
      rightPanel={<AgentPanel selectedCard={selectedCard} />}
      dataMode={dataMode}
      isScanning={isScanning}
      symbols={symbols}
      universes={universes}
      selectedUniverseId={selectedUniverseId}
      onSymbolsChange={setSymbols}
      onUniverseChange={handleUniverseChange}
      onDataModeChange={handleDataModeChange}
      onScan={handleScan}
    >
      {content}
    </Layout>
  );
}
