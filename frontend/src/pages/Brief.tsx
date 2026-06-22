import { useEffect, useState } from "react";

import {
  fetchDailyBrief,
  fetchDailyBriefMarkdown,
  fetchDailyBriefRun,
  fetchDailyBriefRuns,
  saveDailyBriefRun,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import type { BriefRun, DailyBriefResponse, DataProviderMode } from "../types";

function formatNumber(value: number | null, suffix = "") {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}${suffix}`;
}

function formatRatio(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${(value * 100).toFixed(0)}%`;
}

export function Brief({ dataMode, symbols }: { dataMode: DataProviderMode; symbols: string }) {
  const [brief, setBrief] = useState<DailyBriefResponse>();
  const [runs, setRuns] = useState<BriefRun[]>([]);
  const [markdown, setMarkdown] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState("");

  async function loadBrief() {
    try {
      setIsLoading(true);
      setError("");
      const result = await fetchDailyBrief(dataMode, dataMode === "free" ? symbols : undefined);
      setBrief(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load daily brief");
    } finally {
      setIsLoading(false);
    }
  }

  async function loadRuns() {
    const result = await fetchDailyBriefRuns();
    setRuns(result.runs);
  }

  async function saveBrief() {
    try {
      setIsSaving(true);
      setError("");
      const saved = await saveDailyBriefRun(dataMode, dataMode === "free" ? symbols : undefined);
      setBrief(saved.payload);
      setMarkdown("");
      await loadRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save brief");
    } finally {
      setIsSaving(false);
    }
  }

  async function loadSavedBrief(briefId: string) {
    try {
      setError("");
      const result = await fetchDailyBriefRun(briefId);
      setBrief(result.brief);
      setMarkdown("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load saved brief");
    }
  }

  async function loadMarkdown(briefId: string) {
    try {
      setError("");
      const result = await fetchDailyBriefMarkdown(briefId);
      setMarkdown(result.markdown);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load markdown export");
    }
  }

  useEffect(() => {
    void loadBrief();
    void loadRuns();
  }, [dataMode, symbols]);

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Daily Brief</h2>
            <p className="brief-headline">{brief?.headline ?? "Loading research brief"}</p>
          </div>
          <div className="brief-actions">
            <button className="icon-action" type="button" onClick={loadBrief} disabled={isLoading}>
              {isLoading ? "Refreshing" : "Refresh Brief"}
            </button>
            <button className="icon-action" type="button" onClick={saveBrief} disabled={isSaving}>
              {isSaving ? "Saving" : "Save Brief"}
            </button>
          </div>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {brief && <DataHealth data={brief.data_health} />}
        {brief && (
          <div className="metric-grid brief-metrics">
            <div>
              <span>Opportunities</span>
              <strong>{brief.top_opportunities.length}</strong>
            </div>
            <div>
              <span>Entry Watch</span>
              <strong>{brief.entry_watch.length}</strong>
            </div>
            <div>
              <span>Risk Alerts</span>
              <strong>{brief.risk_alerts.length}</strong>
            </div>
            <div>
              <span>Catalysts</span>
              <strong>{brief.catalyst_watch.length}</strong>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Saved Briefs</h2>
          <span className="count">{runs.length}</span>
        </div>
        {!runs.length ? (
          <div className="empty-state">No saved briefs yet.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Provider</th>
                  <th>Headline</th>
                  <th>Opportunities</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.brief_id}>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{run.provider}</td>
                    <td className="reason-cell">{run.headline}</td>
                    <td>{run.opportunity_count}</td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="table-action"
                          type="button"
                          onClick={() => loadSavedBrief(run.brief_id)}
                        >
                          Load
                        </button>
                        <button
                          className="table-action"
                          type="button"
                          onClick={() => loadMarkdown(run.brief_id)}
                        >
                          Markdown
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {markdown && (
          <textarea className="markdown-export" readOnly value={markdown} aria-label="Markdown export" />
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Top Opportunities</h2>
          <span className="count">{brief?.top_opportunities.length ?? 0}</span>
        </div>
        {!brief?.top_opportunities.length ? (
          <div className="empty-state">No ranked opportunities in this brief.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Status</th>
                  <th>Strategy</th>
                  <th>Rank</th>
                  <th>Trigger</th>
                  <th>Stop</th>
                  <th>Target</th>
                  <th>Risk/Reward</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {brief.top_opportunities.map((item) => (
                  <tr key={item.instrument_id}>
                    <td className="ticker">{item.instrument_id}</td>
                    <td>
                      <span className={`status status-${item.status}`}>{item.status}</span>
                    </td>
                    <td className="reason-cell">{item.primary_strategy_id ?? "None"}</td>
                    <td>{item.rank_score.toFixed(2)}</td>
                    <td>{item.trigger_price ?? "-"}</td>
                    <td>{item.initial_stop ?? "-"}</td>
                    <td>{item.target_1 ?? "-"}</td>
                    <td>{formatNumber(item.risk_reward)}</td>
                    <td className="reason-cell">{item.rank_reasons.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="brief-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>Entry Watch</h2>
            <span className="count">{brief?.entry_watch.length ?? 0}</span>
          </div>
          {!brief?.entry_watch.length ? (
            <div className="empty-state">No trigger levels in the current brief.</div>
          ) : (
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Strategy</th>
                    <th>Trigger</th>
                    <th>Stop</th>
                    <th>Target</th>
                  </tr>
                </thead>
                <tbody>
                  {brief.entry_watch.map((item) => (
                    <tr key={`${item.instrument_id}-${item.trigger_price}`}>
                      <td className="ticker">{item.instrument_id}</td>
                      <td className="reason-cell">{item.primary_strategy_id ?? "None"}</td>
                      <td>{item.trigger_price}</td>
                      <td>{item.initial_stop ?? "-"}</td>
                      <td>{item.target_1 ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <h2>Strategy Validation</h2>
            <span className="count">{brief?.strategy_validation.length ?? 0}</span>
          </div>
          {!brief?.strategy_validation.length ? (
            <div className="empty-state">No validation samples available.</div>
          ) : (
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Samples</th>
                    <th>Target Hit</th>
                    <th>Positive 10D</th>
                    <th>Avg 10D</th>
                  </tr>
                </thead>
                <tbody>
                  {brief.strategy_validation.map((item) => (
                    <tr key={item.strategy_id}>
                      <td className="reason-cell">{item.strategy_id}</td>
                      <td>{item.sample_count}</td>
                      <td>{formatRatio(item.target_hit_rate)}</td>
                      <td>{formatRatio(item.positive_rate_10d)}</td>
                      <td>{formatNumber(item.avg_return_10d, "%")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <div className="brief-grid">
        <BriefList
          title="Catalyst Watch"
          count={brief?.catalyst_watch.length ?? 0}
          items={
            brief?.catalyst_watch.map((item) => ({
              key: `${item.instrument_id}-${item.title}`,
              title: `${item.instrument_id} · ${item.catalyst_type}`,
              body: `${item.investment_hypothesis} ${item.verification_path}`,
            })) ?? []
          }
          empty="No catalyst hypotheses in this brief."
        />
        <BriefList
          title="Risk Alerts"
          count={brief?.risk_alerts.length ?? 0}
          items={
            brief?.risk_alerts.map((item) => ({
              key: `${item.instrument_id}-${item.status}`,
              title: `${item.instrument_id} · ${item.status}`,
              body: item.message,
            })) ?? []
          }
          empty="No position risk alerts."
        />
      </div>

      <div className="brief-grid">
        <BriefList
          title="Data Caveats"
          count={brief?.data_caveats.length ?? 0}
          items={
            brief?.data_caveats.map((item) => ({
              key: item,
              title: "Caveat",
              body: item,
            })) ?? []
          }
          empty="No data caveats."
        />
        <BriefList
          title="Next Steps"
          count={brief?.next_steps.length ?? 0}
          items={
            brief?.next_steps.map((item) => ({
              key: item,
              title: "Check",
              body: item,
            })) ?? []
          }
          empty="No next steps."
        />
      </div>
    </div>
  );
}

function BriefList({
  title,
  count,
  items,
  empty,
}: {
  title: string;
  count: number;
  items: { key: string; title: string; body: string }[];
  empty: string;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{title}</h2>
        <span className="count">{count}</span>
      </div>
      {!items.length ? (
        <div className="empty-state">{empty}</div>
      ) : (
        <div className="brief-list">
          {items.map((item) => (
            <div key={item.key}>
              <strong>{item.title}</strong>
              <span>{item.body}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
