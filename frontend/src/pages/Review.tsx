import { useEffect, useState } from "react";

import { fetchCatalysts } from "../api/client";
import { DataHealth } from "../components/DataHealth";
import { useI18n } from "../i18n";
import { formatInstrumentLabel } from "../lib/instruments";
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
    <div className="stack">
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
                  <th>{t("review.hypothesis")}</th>
                  <th>{t("review.verification")}</th>
                </tr>
              </thead>
              <tbody>
                {data.hypotheses.map((item) => (
                  <tr key={`${item.news_id}-${item.catalyst_type}`}>
                    <td className="ticker" title={item.instrument_id}>
                      {formatInstrumentLabel(item.instrument_id)}
                    </td>
                    <td>{localizeCatalyst(item.catalyst_type, language)}</td>
                    <td>{Math.round(item.confidence * 100)}</td>
                    <td className="reason-cell">
                      {localizeReason(item.investment_hypothesis, language)}
                    </td>
                    <td className="reason-cell">{localizeReason(item.verification_path, language)}</td>
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
                <span title={item.instrument_id}>{formatInstrumentLabel(item.instrument_id)}</span>
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
