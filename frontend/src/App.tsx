import { useEffect, useMemo, useState } from "react";

import { fetchOpportunities, fetchOverview } from "./api/client";
import { AgentPanel } from "./components/AgentPanel";
import { Layout, type PageId } from "./components/Layout";
import { Alerts } from "./pages/Alerts";
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
} from "./types";

const DEFAULT_SYMBOLS = "US:AAPL,US:NVDA,US:MSFT,CN:000001,CN:600519";

export default function App() {
  const [page, setPage] = useState<PageId>("overview");
  const [overview, setOverview] = useState<OverviewResponse>();
  const [opportunities, setOpportunities] = useState<OpportunitiesResponse>();
  const [selectedCard, setSelectedCard] = useState<OpportunityCard>();
  const [dataMode, setDataMode] = useState<DataProviderMode>("fixture");
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
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
  }, []);

  function handleDataModeChange(mode: DataProviderMode) {
    setDataMode(mode);
    void loadDashboard(mode, symbols);
  }

  function handleScan() {
    void loadDashboard(dataMode, symbols);
  }

  const content = useMemo(() => {
    if (error) {
      return <section className="panel error">{error}</section>;
    }

    switch (page) {
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
        return <Alerts />;
      case "review":
        return <Review symbols={symbols} />;
      case "settings":
        return <Settings dataMode={dataMode} symbols={symbols} />;
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
      onSymbolsChange={setSymbols}
      onDataModeChange={handleDataModeChange}
      onScan={handleScan}
    >
      {content}
    </Layout>
  );
}
