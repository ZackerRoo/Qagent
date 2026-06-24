import { useEffect, useState } from "react";

import {
  fetchDailyBrief,
  fetchDailyBriefMarkdown,
  fetchDailyBriefRun,
  fetchDailyBriefRuns,
  fetchDeliveries,
  markDeliverySent,
  queueBriefDelivery,
  saveDailyBriefRun,
} from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import type { TranslationKey } from "../i18n/catalog";
import { formatInstrumentLabel } from "../lib/instruments";
import {
  localizeAction,
  localizeCatalyst,
  localizeCaveat,
  localizeProvider,
  localizeReason,
  localizeStatus,
  localizeStrategy,
} from "../lib/localize";
import { createMarketSections } from "../lib/markets";
import type {
  BriefRun,
  DailyBriefEntryWatch,
  DailyBriefOpportunity,
  DailyBriefResponse,
  DataProviderMode,
  DeliveryOutboxRecord,
} from "../types";

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
  const { language, t } = useI18n();
  const [brief, setBrief] = useState<DailyBriefResponse>();
  const [runs, setRuns] = useState<BriefRun[]>([]);
  const [deliveries, setDeliveries] = useState<DeliveryOutboxRecord[]>([]);
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

  async function loadDeliveries() {
    const result = await fetchDeliveries();
    setDeliveries(result.deliveries);
  }

  async function saveBrief() {
    try {
      setIsSaving(true);
      setError("");
      const saved = await saveDailyBriefRun(dataMode, dataMode === "free" ? symbols : undefined);
      setBrief(saved.payload);
      setMarkdown("");
      await loadRuns();
      await loadDeliveries();
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

  async function queueDelivery(briefId: string) {
    try {
      setError("");
      await queueBriefDelivery(briefId);
      await loadDeliveries();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to queue delivery");
    }
  }

  async function markSent(deliveryId: string) {
    try {
      setError("");
      await markDeliverySent(deliveryId);
      await loadDeliveries();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to mark delivery sent");
    }
  }

  useEffect(() => {
    void loadBrief();
    void loadRuns();
    void loadDeliveries();
  }, [dataMode, symbols]);

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>{t("brief.title")}</h2>
            <p className="brief-headline">
              {brief ? localizeReason(brief.headline, language) : t("brief.loading")}
            </p>
          </div>
          <div className="brief-actions">
            <button className="icon-action" type="button" onClick={loadBrief} disabled={isLoading}>
              {isLoading ? t("common.refreshing") : t("brief.refresh")}
            </button>
            <button className="icon-action" type="button" onClick={saveBrief} disabled={isSaving}>
              {isSaving ? t("common.saving") : t("brief.save")}
            </button>
          </div>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {brief && <DataHealth data={brief.data_health} language={language} />}
        {brief && (
          <div className="metric-grid brief-metrics">
            <div>
              <span>{t("brief.opportunities")}</span>
              <strong>{brief.top_opportunities.length}</strong>
            </div>
            <div>
              <span>{t("brief.entryWatch")}</span>
              <strong>{brief.entry_watch.length}</strong>
            </div>
            <div>
              <span>{t("brief.riskAlerts")}</span>
              <strong>{brief.risk_alerts.length}</strong>
            </div>
            <div>
              <span>{t("brief.catalysts")}</span>
              <strong>{brief.catalyst_watch.length}</strong>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("brief.saved")}</h2>
          <span className="count">{runs.length}</span>
        </div>
        {!runs.length ? (
          <div className="empty-state">{t("brief.noSaved")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.created")}</th>
                  <th>{t("common.provider")}</th>
                  <th>{t("brief.headline")}</th>
                  <th>{t("brief.opportunities")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.brief_id}>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{localizeProvider(run.provider, language)}</td>
                    <td className="reason-cell">{localizeReason(run.headline, language)}</td>
                    <td>{run.opportunity_count}</td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="table-action"
                          type="button"
                          onClick={() => loadSavedBrief(run.brief_id)}
                        >
                          {t("common.load")}
                        </button>
                        <button
                          className="table-action"
                          type="button"
                          onClick={() => loadMarkdown(run.brief_id)}
                        >
                          {t("common.markdown")}
                        </button>
                        <button
                          className="table-action"
                          type="button"
                          onClick={() => queueDelivery(run.brief_id)}
                        >
                          {t("common.queue")}
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
          <textarea className="markdown-export" readOnly value={markdown} aria-label={t("common.markdown")} />
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("brief.delivery")}</h2>
          <span className="count">{deliveries.length}</span>
        </div>
        {!deliveries.length ? (
          <div className="empty-state">{t("brief.noDeliveries")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.created")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("brief.channel")}</th>
                  <th>{t("brief.recipient")}</th>
                  <th>{t("brief.subject")}</th>
                  <th>{t("brief.sent")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.map((delivery) => (
                  <tr key={delivery.delivery_id}>
                    <td>{new Date(delivery.created_at).toLocaleString()}</td>
                    <td>
                      <span className={`status status-${delivery.status}`}>
                        {localizeStatus(delivery.status, language)}
                      </span>
                    </td>
                    <td>{formatChannel(delivery.channel, language)}</td>
                    <td>{formatRecipient(delivery.recipient, language)}</td>
                    <td className="reason-cell">{localizeReason(delivery.subject, language)}</td>
                    <td>{delivery.sent_at ? new Date(delivery.sent_at).toLocaleString() : "-"}</td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="table-action"
                          type="button"
                          onClick={() => setMarkdown(delivery.markdown)}
                        >
                          {t("common.markdown")}
                        </button>
                        {delivery.status === "queued" && (
                          <button
                            className="table-action"
                            type="button"
                            onClick={() => markSent(delivery.delivery_id)}
                          >
                            {t("brief.markSent")}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("brief.top")}</h2>
          <span className="count">{brief?.top_opportunities.length ?? 0}</span>
        </div>
        {!brief?.top_opportunities.length ? (
          <div className="empty-state">{t("brief.noRanked")}</div>
        ) : (
          <BriefOpportunityMarketSections items={brief.top_opportunities} />
        )}
      </section>

      <div className="brief-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("brief.entryWatch")}</h2>
            <span className="count">{brief?.entry_watch.length ?? 0}</span>
          </div>
          {!brief?.entry_watch.length ? (
            <div className="empty-state">{t("brief.noTrigger")}</div>
          ) : (
            <BriefEntryWatchMarketSections items={brief.entry_watch} />
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <h2>{t("brief.validation")}</h2>
            <span className="count">{brief?.strategy_validation.length ?? 0}</span>
          </div>
          {!brief?.strategy_validation.length ? (
            <div className="empty-state">{t("brief.noValidation")}</div>
          ) : (
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>{t("common.strategy")}</th>
                    <th>{t("common.samples")}</th>
                    <th>{t("brief.targetHit")}</th>
                    <th>{t("brief.positive10d")}</th>
                    <th>{t("brief.avg10d")}</th>
                  </tr>
                </thead>
                <tbody>
                  {brief.strategy_validation.map((item) => (
                    <tr key={item.strategy_id}>
                      <td className="reason-cell">{localizeStrategy(item.strategy_id, language)}</td>
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
          title={t("brief.catalystWatch")}
          count={brief?.catalyst_watch.length ?? 0}
          items={
            brief?.catalyst_watch.map((item) => ({
              key: `${item.instrument_id}-${item.title}`,
              title: `${formatInstrumentLabel(item.instrument_id)} · ${localizeCatalyst(
                item.catalyst_type,
                language,
              )}`,
              body: `${localizeReason(item.investment_hypothesis, language)} ${localizeReason(
                item.verification_path,
                language,
              )}`,
            })) ?? []
          }
          empty={t("brief.noCatalysts")}
        />
        <BriefList
          title={t("brief.riskAlerts")}
          count={brief?.risk_alerts.length ?? 0}
          items={
            brief?.risk_alerts.map((item) => ({
              key: `${item.instrument_id}-${item.status}`,
              title: `${formatInstrumentLabel(item.instrument_id)} · ${localizeStatus(
                item.status,
                language,
              )}`,
              body: localizeReason(item.message, language),
            })) ?? []
          }
          empty={t("brief.noRiskAlerts")}
        />
      </div>

      <div className="brief-grid">
        <BriefList
          title={t("brief.dataCaveats")}
          count={brief?.data_caveats.length ?? 0}
          items={
            brief?.data_caveats.map((item) => ({
              key: item,
              title: t("brief.caveat"),
              body: localizeCaveat(item, language),
            })) ?? []
          }
          empty={t("brief.noCaveats")}
        />
        <BriefList
          title={t("brief.nextSteps")}
          count={brief?.next_steps.length ?? 0}
          items={
            brief?.next_steps.map((item) => ({
              key: item,
              title: t("brief.check"),
              body: localizeReason(item, language),
            })) ?? []
          }
          empty={t("brief.noNextSteps")}
        />
      </div>
    </div>
  );
}

function BriefOpportunityMarketSections({ items }: { items: DailyBriefOpportunity[] }) {
  const { t } = useI18n();
  const sections = createMarketSections(items, (item) => item.instrument_id);

  if (!sections.length) {
    return <p className="empty">{t("brief.noRanked")}</p>;
  }

  return (
    <div className="market-sections">
      {sections.map((section) => (
        <div className="market-section" key={section.market}>
          <div className="market-section-heading">
            <h3>{t(section.labelKey as TranslationKey)}</h3>
            <span className="count">{section.items.length}</span>
          </div>
          {section.items.length ? (
            <BriefOpportunityTable items={section.items} />
          ) : (
            <p className="empty">{t("market.noOpportunities")}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function BriefOpportunityTable({ items }: { items: DailyBriefOpportunity[] }) {
  const { language, t } = useI18n();

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("common.status")}</th>
            <th>{t("brief.decision")}</th>
            <th>{t("brief.conviction")}</th>
            <th>{t("brief.risk")}</th>
            <th>{t("common.strategy")}</th>
            <th>{t("brief.rank")}</th>
            <th>{t("factors.score")}</th>
            <th>{t("factors.rank")}</th>
            <th>{t("brief.trigger")}</th>
            <th>{t("brief.stop")}</th>
            <th>{t("brief.target")}</th>
            <th>{t("brief.riskReward")}</th>
            <th>{t("brief.why")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.instrument_id}>
              <td className="ticker" title={item.instrument_id}>
                {formatInstrumentLabel(item.instrument_id)}
              </td>
              <td>
                <span className={`status status-${item.status}`}>
                  {localizeStatus(item.status, language)}
                </span>
              </td>
              <td>
                <span className={`status status-${item.decision_action ?? "pending"}`}>
                  {localizeAction(item.decision_action, language)}
                </span>
              </td>
              <td>{formatRatio(item.conviction_score)}</td>
              <td>{formatNumber(item.suggested_risk_pct, "%")}</td>
              <td className="reason-cell">
                {localizeStrategy(item.primary_strategy_id, language)}
              </td>
              <td>{item.rank_score.toFixed(2)}</td>
              <td>{formatRatio(item.factor_score)}</td>
              <td>{item.factor_rank ?? "-"}</td>
              <td>{item.trigger_price ?? "-"}</td>
              <td>{item.initial_stop ?? "-"}</td>
              <td>{item.target_1 ?? "-"}</td>
              <td>{formatNumber(item.risk_reward)}</td>
              <td className="reason-cell">
                {item.rank_reasons.map((reason) => localizeReason(reason, language)).join("；")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BriefEntryWatchMarketSections({ items }: { items: DailyBriefEntryWatch[] }) {
  const { t } = useI18n();
  const sections = createMarketSections(items, (item) => item.instrument_id);

  if (!sections.length) {
    return <p className="empty">{t("brief.noTrigger")}</p>;
  }

  return (
    <div className="market-sections">
      {sections.map((section) => (
        <div className="market-section" key={section.market}>
          <div className="market-section-heading">
            <h3>{t(section.labelKey as TranslationKey)}</h3>
            <span className="count">{section.items.length}</span>
          </div>
          {section.items.length ? (
            <BriefEntryWatchTable items={section.items} />
          ) : (
            <p className="empty">{t("brief.noTrigger")}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function BriefEntryWatchTable({ items }: { items: DailyBriefEntryWatch[] }) {
  const { language, t } = useI18n();

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>{t("common.ticker")}</th>
            <th>{t("common.strategy")}</th>
            <th>{t("brief.trigger")}</th>
            <th>{t("brief.stop")}</th>
            <th>{t("brief.target")}</th>
            <th>{t("brief.decision")}</th>
            <th>{t("brief.risk")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.instrument_id}-${item.trigger_price}`}>
              <td className="ticker" title={item.instrument_id}>
                {formatInstrumentLabel(item.instrument_id)}
              </td>
              <td className="reason-cell">
                {localizeStrategy(item.primary_strategy_id, language)}
              </td>
              <td>{item.trigger_price}</td>
              <td>{item.initial_stop ?? "-"}</td>
              <td>{item.target_1 ?? "-"}</td>
              <td>{localizeAction(item.decision_action, language)}</td>
              <td>{formatNumber(item.suggested_risk_pct, "%")}</td>
            </tr>
          ))}
        </tbody>
      </table>
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

function formatChannel(value: string, language: "zh" | "en"): string {
  if (value === "markdown") {
    return language === "zh" ? "Markdown" : "Markdown";
  }
  return value;
}

function formatRecipient(value: string | null, language: "zh" | "en"): string {
  if (!value || value === "local") {
    return language === "zh" ? "本地" : "local";
  }
  return value;
}
