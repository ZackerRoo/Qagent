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
import type { OpportunitiesResponse, OpportunityCard, OverviewResponse } from "./types";

export default function App() {
  const [page, setPage] = useState<PageId>("overview");
  const [overview, setOverview] = useState<OverviewResponse>();
  const [opportunities, setOpportunities] = useState<OpportunitiesResponse>();
  const [selectedCard, setSelectedCard] = useState<OpportunityCard>();
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [overviewResult, opportunitiesResult] = await Promise.all([
          fetchOverview(),
          fetchOpportunities(),
        ]);
        setOverview(overviewResult);
        setOpportunities(opportunitiesResult);
        setSelectedCard(opportunitiesResult.cards[0]);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to load dashboard");
      }
    }
    void load();
  }, []);

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
            selectedCard={selectedCard}
            onSelect={setSelectedCard}
          />
        );
      case "watchlist":
        return <Watchlist />;
      case "portfolio":
        return <Portfolio />;
      case "alerts":
        return <Alerts />;
      case "review":
        return <Review />;
      case "settings":
        return <Settings />;
      default:
        return null;
    }
  }, [error, opportunities?.cards, overview, page, selectedCard]);

  return (
    <Layout page={page} onPageChange={setPage} rightPanel={<AgentPanel selectedCard={selectedCard} />}>
      {content}
    </Layout>
  );
}
