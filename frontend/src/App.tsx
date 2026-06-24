import { useEffect, useMemo, useState } from "react";

import {
  fetchIntradayRadar,
  fetchOpportunities,
  fetchOverview,
  fetchUniverses,
  saveUniverse,
} from "./api/client";
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
  IntradayRadarResponse,
  OpportunitiesResponse,
  OpportunityCard,
  OverviewResponse,
  ResearchProfile,
  UniverseCreate,
  UniverseRecord,
} from "./types";
import { applyResearchProfile } from "./lib/profiles";

const DEFAULT_SYMBOLS = "CN:000001,CN:600519,CN:300750,CN:000063,CN:002415";

export default function App() {
  const [page, setPage] = useState<PageId>("brief");
  const [overview, setOverview] = useState<OverviewResponse>();
  const [opportunities, setOpportunities] = useState<OpportunitiesResponse>();
  const [radar, setRadar] = useState<IntradayRadarResponse>();
  const [selectedCard, setSelectedCard] = useState<OpportunityCard>();
  const [dataMode, setDataMode] = useState<DataProviderMode>("free");
  const [profile, setProfile] = useState<ResearchProfile>("balanced");
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [universes, setUniverses] = useState<UniverseRecord[]>([]);
  const [selectedUniverseId, setSelectedUniverseId] = useState("free_default");
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
      const [overviewResult, opportunitiesResult, radarResult] = await Promise.all([
        fetchOverview(params),
        fetchOpportunities(params),
        fetchIntradayRadar(mode, mode === "free" ? symbolText : undefined),
      ]);
      setOverview(overviewResult);
      setOpportunities(opportunitiesResult);
      setRadar(radarResult);
      setSelectedCard(applyResearchProfile(opportunitiesResult.cards, profile)[0]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load dashboard");
    } finally {
      setIsScanning(false);
    }
  }

  useEffect(() => {
    void loadDashboard("free", DEFAULT_SYMBOLS);
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

  function handleProfileChange(value: ResearchProfile) {
    setProfile(value);
    const nextCards = applyResearchProfile(opportunities?.cards ?? [], value);
    if (nextCards.length) {
      setSelectedCard(nextCards[0]);
    }
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

  const profiledOpportunities = useMemo(
    () => (opportunities ? applyResearchProfile(opportunities.cards, profile) : []),
    [opportunities, profile],
  );
  const profiledOverview = useMemo(
    () =>
      overview
        ? {
            ...overview,
            top_cards: applyResearchProfile(overview.top_cards, profile),
          }
        : undefined,
    [overview, profile],
  );

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
            overview={profiledOverview}
            radar={radar}
            selectedCardId={selectedCard?.card_id}
            onSelect={setSelectedCard}
          />
        );
      case "opportunities":
        return (
          <Opportunities
            cards={profiledOpportunities}
            items={opportunities?.items ?? []}
            strategyHealth={opportunities?.strategy_health ?? []}
            factorRankings={opportunities?.factor_rankings ?? []}
            selectedCard={selectedCard}
            dataMode={dataMode}
            profile={profile}
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
  }, [
    dataMode,
    error,
    opportunities,
    page,
    profile,
    profiledOpportunities,
    profiledOverview,
    radar,
    selectedCard,
    symbols,
  ]);

  return (
    <Layout
      page={page}
      onPageChange={setPage}
      rightPanel={<AgentPanel selectedCard={selectedCard} dataMode={dataMode} symbols={symbols} />}
      dataMode={dataMode}
      isScanning={isScanning}
      symbols={symbols}
      universes={universes}
      selectedUniverseId={selectedUniverseId}
      profile={profile}
      onSymbolsChange={setSymbols}
      onUniverseChange={handleUniverseChange}
      onDataModeChange={handleDataModeChange}
      onProfileChange={handleProfileChange}
      onScan={handleScan}
    >
      {content}
    </Layout>
  );
}
