import { useEffect, useMemo, useRef, useState } from "react";

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
import { Today } from "./pages/Today";
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

const DEFAULT_SYMBOLS = "CN:ALL";
const NON_BRIEF_PAGES: PageId[] = [
  "overview",
  "opportunities",
  "watchlist",
  "portfolio",
  "alerts",
  "history",
  "review",
  "settings",
];

export default function App() {
  const [page, setPage] = useState<PageId>("today");
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
  const scanRequestRef = useRef(0);
  const scanAbortRef = useRef<AbortController | null>(null);

  function shouldLoadDashboard(nextPage: PageId = page): boolean {
    return NON_BRIEF_PAGES.includes(nextPage);
  }

  async function loadDashboard(mode: DataProviderMode, symbolText: string) {
    const requestId = scanRequestRef.current + 1;
    scanRequestRef.current = requestId;
    scanAbortRef.current?.abort();
    const controller = new AbortController();
    scanAbortRef.current = controller;
    setIsScanning(true);
    setError("");
    try {
      const params = {
        provider: mode,
        symbols: mode === "free" ? symbolText : undefined,
      };
      const [overviewResult, opportunitiesResult, radarResult] = await Promise.all([
        fetchOverview(params, { signal: controller.signal }),
        fetchOpportunities(params, { signal: controller.signal }),
        fetchIntradayRadar(mode, mode === "free" ? symbolText : undefined, {
          signal: controller.signal,
        }),
      ]);
      if (requestId !== scanRequestRef.current) {
        return;
      }
      setOverview(overviewResult);
      setOpportunities(opportunitiesResult);
      setRadar(radarResult);
      setSelectedCard(applyResearchProfile(opportunitiesResult.cards, profile)[0]);
    } catch (caught) {
      if (requestId !== scanRequestRef.current) {
        return;
      }
      if (caught instanceof DOMException && caught.name === "AbortError") {
        return;
      }
      setError(caught instanceof Error ? caught.message : "Failed to load dashboard");
    } finally {
      if (requestId === scanRequestRef.current) {
        setIsScanning(false);
      }
    }
  }

  useEffect(() => {
    void refreshUniverses();

    if (shouldLoadDashboard(page)) {
      void loadDashboard("free", DEFAULT_SYMBOLS);
    }
  }, []);

  async function refreshUniverses() {
    const result = await fetchUniverses();
    setUniverses(result.universes);
  }

  useEffect(() => {
    if (!shouldLoadDashboard()) {
      return;
    }
    void loadDashboard(dataMode, symbols);
  }, [dataMode, symbols, page]);

  function handleDataModeChange(mode: DataProviderMode) {
    setDataMode(mode);
  }

  function handleScan() {
    if (!shouldLoadDashboard()) {
      return;
    }
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
    const nextSymbols = universe.symbols.join(",");
    const nextMode = universe.universe_id === "fixture_dev" ? "fixture" : "free";
    setSymbols(nextSymbols);
    setDataMode(nextMode);
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
      case "today":
        return (
          <Today
            dataMode={dataMode}
            profile={profile}
            selectedCard={selectedCard}
            onSelect={setSelectedCard}
          />
        );
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
            sectorStrength={opportunities?.sector_strength ?? []}
            portfolioPlan={opportunities?.portfolio_plan}
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
      scanEnabled={shouldLoadDashboard()}
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
