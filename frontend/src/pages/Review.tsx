import { useEffect, useState } from "react";

import { fetchCatalysts } from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeCatalyst, localizeReason } from "../lib/localize";
import type { CatalystsResponse } from "../types";

export function Review({ symbols }: { symbols: string }) {
  const { language, t } = useI18n();
  const [data, setData] = useState<CatalystsResponse>();
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        setError("");
        setData(await fetchCatalysts(symbols));
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to load catalysts");
      }
    }
    void load();
  }, [symbols]);

  return (
    <div className="stack review-page">
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("review.title")}</h2>
          <span className="count">{data?.hypotheses.length ?? 0}</span>
        </div>
        {data && <DataHealth data={data.data_health} language={language} />}
        {error && <div className="empty-state error">{error}</div>}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("review.hypotheses")}</h2>
          <span className="count">{data?.hypotheses.length ?? 0}</span>
        </div>
        {!data?.hypotheses.length ? (
          <div className="empty-state">{t("review.noHypotheses")}</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("common.ticker")}</th>
                  <th>{t("review.type")}</th>
                  <th>{t("review.confidence")}</th>
                  <th>{language === "zh" ? "事实、推断与需求" : "Facts, inference, and demand"}</th>
                  <th>{language === "zh" ? "财务传导与反证" : "Financial transmission and disconfirmation"}</th>
                </tr>
              </thead>
              <tbody>
                {data.hypotheses.map((item) => (
                  <tr key={`${item.news_id}-${item.catalyst_type}`}>
                    <td className="ticker" title={formatInstrumentDisplay(item.instrument_id)}>
                      {formatInstrumentDisplay(item.instrument_id)}
                      <small>{item.source}{item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ""}</small>
                    </td>
                    <td>{localizeCatalyst(item.catalyst_type, language)}</td>
                    <td>{Math.round(item.confidence * 100)}</td>
                    <td className="reason-cell">
                      <strong>{language === "zh" ? "观察事实" : "Observed"}</strong>
                      <p>{item.observed_facts[0] ?? item.title}</p>
                      <strong>{language === "zh" ? "研究推断" : "Inference"}</strong>
                      <p>{localizeReason(item.investment_hypothesis, language)}</p>
                      <strong>{language === "zh" ? "需求翻译" : "Demand translation"}</strong>
                      <p>{item.demand_translation}</p>
                      <small>
                        {language === "zh" ? "受益身份" : "Beneficiary status"}: {item.beneficiary_chain[0]?.benefit_order ?? "unverified"}
                        {` · ${language === "zh" ? "已计价" : "Priced in"}: ${item.priced_in_assessment}`}
                      </small>
                    </td>
                    <td className="reason-cell">
                      {item.financial_transmission.slice(0, 2).map((transmission) => (
                        <p key={`${transmission.line_item}-${transmission.reporting_lag}`}>
                          <strong>{transmission.line_item}</strong>: {transmission.mechanism}
                          {` · ${transmission.reporting_lag}`}
                        </p>
                      ))}
                      <strong>{language === "zh" ? "下一证据" : "Next evidence"}</strong>
                      <p>{item.evidence_to_watch.slice(0, 2).join("；") || item.verification_path}</p>
                      <strong>{language === "zh" ? "证伪/失效" : "Disconfirm / invalidate"}</strong>
                      <p>{[...item.risks.slice(0, 1), ...item.invalidation_triggers.slice(0, 1)].join("；") || "-"}</p>
                      <small>{language === "zh" ? "决策影响" : "Decision effect"}: {item.decision_effect}</small>
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
          <h2>{t("review.news")}</h2>
          <span className="count">{data?.news.length ?? 0}</span>
        </div>
        {!data?.news.length ? (
          <div className="empty-state">{t("review.noNews")}</div>
        ) : (
          <div className="news-list">
            {data.news.map((item) => (
              <a key={item.news_id} href={item.url ?? "#"} target="_blank" rel="noreferrer">
                <span title={formatInstrumentDisplay(item.instrument_id)}>{formatInstrumentDisplay(item.instrument_id)}</span>
                <strong>{item.title}</strong>
                <small>
                  {item.publisher ?? item.source}
                  {item.published_at ? ` · ${new Date(item.published_at).toLocaleString()}` : ""}
                </small>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
