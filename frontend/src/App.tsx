import { useEffect, useMemo, useState } from "react";

import {
  fetchLatestFullMarketBatchResult,
  fetchInstrumentLabels,
  fetchUniverses,
  saveUniverse,
  fetchInstrumentSearch,
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
import { registerInstrumentLabels } from "./lib/instruments";
import { hasInstrumentLabel, marketSymbol } from "./lib/instruments";
import type {
  DataProviderMode,
  FullMarketScanResponse,
  IntradayRadarResponse,
  MarketRotationRadar,
  OpportunitiesResponse,
  OpportunityCard,
  OverviewResponse,
  ResearchProfile,
  UniverseCreate,
  UniverseRecord,
} from "./types";
import { applyResearchProfile } from "./lib/profiles";

const DEFAULT_SYMBOLS = "CN:ALL";

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
  const [labelBootstrapNonce, setLabelBootstrapNonce] = useState(0);

  useEffect(() => {
    void refreshUniverses();
  }, []);

  useEffect(() => {
    void bootstrapInstrumentLabels(dataMode);
  }, [dataMode]);

  async function refreshUniverses() {
    try {
      const result = await fetchUniverses();
      setUniverses(result.universes);
    } catch {
      setUniverses([]);
    }
  }

  useEffect(() => {
    void loadCachedDashboard(dataMode);
  }, [dataMode]);

  async function loadCachedDashboard(mode: DataProviderMode) {
    try {
      const result = await fetchLatestFullMarketBatchResult(mode, true);
      applyDashboardResult(result);
    } catch {
      setOverview(undefined);
      setOpportunities(undefined);
      setRadar(undefined);
    }
  }

  function applyDashboardResult(result: FullMarketScanResponse) {
    setOpportunities(toOpportunitiesResponse(result));
    setOverview(toOverviewResponse(result));
    setRadar(toRadarResponse(result));
    const nextCards = applyResearchProfile(result.cards, profile);
    if (nextCards.length) {
      setSelectedCard(nextCards[0]);
    }

    const symbols = result.cards
      .map((card) => card.instrument_id)
      .concat(result.items.map((item) => item.instrument_id));
    void hydrateInstrumentLabels(dataMode, symbols);
  }

  function handleDataModeChange(mode: DataProviderMode) {
    setDataMode(mode);
  }

  async function bootstrapInstrumentLabels(mode: DataProviderMode) {
    if (mode !== "free") {
      return;
    }
    await hydrateInstrumentLabels(mode, []);
  }

  async function hydrateInstrumentLabels(
    mode: DataProviderMode,
    rawSymbols: string[],
  ): Promise<void> {
    if (mode !== "free") {
      return;
    }

    if (!rawSymbols.length) {
      try {
        const result = await fetchInstrumentLabels();
        const loaded = registerInstrumentLabels(result.labels ?? {});
        if (loaded > 0) {
          setLabelBootstrapNonce((value) => value + 1);
        }
      } catch {
        // older backend versions may not have /instruments/labels; fallback below.
        await hydrateInstrumentLabelsFromSearch([]);
      }
      return;
    }

    const symbols = collectMissingCnSymbols(rawSymbols);
    if (!symbols.length) {
      return;
    }

    try {
      const result = await fetchInstrumentLabels(symbols);
      const loaded = registerInstrumentLabels(result.labels ?? {});
      if (loaded > 0) {
        setLabelBootstrapNonce((value) => value + 1);
      }
      const labels = result.labels ?? {};
      const unresolved = symbols.filter((symbol) => {
        return !labels[symbol] && !labels[marketSymbol(symbol) as string];
      });
      if (!unresolved.length) {
        return;
      }
      await hydrateInstrumentLabelsFromSearch(unresolved);
      return;
    } catch {
      // older backend versions may not have /instruments/labels; fallback below.
    }

    await hydrateInstrumentLabelsFromSearch(rawSymbols);
  }

  function collectMissingCnSymbols(rawSymbols: string[]): string[] {
    const seen = new Set<string>();
    const missing: string[] = [];
    for (const symbol of rawSymbols) {
      const normalized = marketSymbol(symbol);
      if (!normalized || seen.has(normalized) || !/^\d{6}$/.test(normalized) || hasInstrumentLabel(normalized)) {
        if (normalized) {
          seen.add(normalized);
        }
        continue;
      }
      seen.add(normalized);
      missing.push(`CN:${normalized}`);
    }
    return missing;
  }

  async function hydrateInstrumentLabelsFromSearch(rawSymbols: string[]): Promise<void> {
    const symbols = rawSymbols
      .map((symbol) => marketSymbol(symbol))
      .filter((symbol): symbol is string => Boolean(symbol) && /^\d{6}$/.test(symbol));
    const bySymbol: Record<string, string> = {};

    for (const symbol of symbols) {
      try {
        const result = await fetchInstrumentSearch(`CN:${symbol}`, 50);
        const matched = result.items.find((item) => {
          const itemSymbol = marketSymbol(item.instrument_id);
          return itemSymbol === symbol || item.instrument_id === `CN:${symbol}`;
        });
        if (matched?.label) {
          bySymbol[symbol] = matched.label;
        }
      } catch {
        // Keep best effort; skip unresolved symbols.
      }
    }

    const loaded = registerInstrumentLabels(bySymbol);
    if (loaded > 0) {
      setLabelBootstrapNonce((value) => value + 1);
    }
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
    switch (page) {
      case "today":
        return (
          <Today
            dataMode={dataMode}
            profile={profile}
            selectedCard={selectedCard}
            onSelect={setSelectedCard}
            onResult={applyDashboardResult}
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
            researchCenter={opportunities?.research_center}
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
        return <History dataMode={dataMode} symbols={symbols} selectedCard={selectedCard} />;
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
    labelBootstrapNonce,
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
      symbols={symbols}
      universes={universes}
      selectedUniverseId={selectedUniverseId}
      profile={profile}
      onSymbolsChange={setSymbols}
      onUniverseChange={handleUniverseChange}
      onDataModeChange={handleDataModeChange}
      onProfileChange={handleProfileChange}
    >
      {content}
    </Layout>
  );
}

function toOpportunitiesResponse(result: FullMarketScanResponse): OpportunitiesResponse {
  return {
    cards: result.cards,
    items: result.items,
    strategy_health: result.strategy_health,
    factor_rankings: result.factor_rankings,
    sector_strength: result.sector_strength,
    rotation_radar: result.rotation_radar ?? emptyRotationRadar(),
    portfolio_plan: result.portfolio_plan,
    research_center: result.research_center,
    data_health: result.data_health,
  };
}

function toOverviewResponse(result: FullMarketScanResponse): OverviewResponse {
  return {
    market_regime: {
      US: "not_in_scope",
      CN: "latest_cached_scan",
    },
    top_cards: result.cards.slice(0, 5),
    strategy_health: result.strategy_health.slice(0, 6),
    factor_rankings: result.factor_rankings.slice(0, 10),
    sector_strength: result.sector_strength.slice(0, 6),
    rotation_radar: result.rotation_radar ?? emptyRotationRadar(),
    portfolio_plan: result.portfolio_plan,
    research_center: result.research_center,
    data_health: result.data_health,
  };
}

function emptyRotationRadar(): MarketRotationRadar {
  return {
    as_of: "",
    themes: [],
    data_health: {},
  };
}

function toRadarResponse(result: FullMarketScanResponse): IntradayRadarResponse {
  return {
    items: result.cards.slice(0, 20).map((card) => ({
      instrument_id: card.instrument_id,
      instrument_label: card.instrument_label,
      latest_trade_date: null,
      latest_close: card.entry_plan.trigger_price,
      previous_close: null,
      change_pct: null,
      volume_ratio: null,
      signal: card.decision?.action ?? card.status,
      severity: card.decision?.risk_status === "blocked" ? "danger" : "success",
      score: Number(card.rank_score),
      message: card.rank_reasons[0] ?? card.thesis,
      action: card.decision?.action ?? card.status,
      distance_to_trigger_pct: null,
      trigger_price: card.entry_plan.trigger_price,
      initial_stop: card.exit_plan.initial_stop,
      target_1: card.exit_plan.target_1,
      no_chase_above: card.entry_plan.no_chase_above,
    })),
    data_health: {
      ...result.data_health,
      radar_source: "latest_cached_scan",
    },
  };
}
