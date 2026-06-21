import { useEffect, useState } from "react";

import { fetchCatalysts } from "../api/client";
import { DataHealth } from "../components/DataHealth";
import type { CatalystsResponse } from "../types";

export function Review({ symbols }: { symbols: string }) {
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
          <h2>Catalyst Review</h2>
          <span className="count">{data?.hypotheses.length ?? 0}</span>
        </div>
        {data && <DataHealth data={data.data_health} />}
        {error && <div className="empty-state error">{error}</div>}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>Hypotheses</h2>
          <span className="count">{data?.hypotheses.length ?? 0}</span>
        </div>
        {!data?.hypotheses.length ? (
          <div className="empty-state">No catalyst hypotheses yet.</div>
        ) : (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Type</th>
                  <th>Confidence</th>
                  <th>Hypothesis</th>
                  <th>Verification</th>
                </tr>
              </thead>
              <tbody>
                {data.hypotheses.map((item) => (
                  <tr key={`${item.news_id}-${item.catalyst_type}`}>
                    <td className="ticker">{item.instrument_id}</td>
                    <td>{item.catalyst_type}</td>
                    <td>{Math.round(item.confidence * 100)}</td>
                    <td className="reason-cell">{item.investment_hypothesis}</td>
                    <td className="reason-cell">{item.verification_path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>News</h2>
          <span className="count">{data?.news.length ?? 0}</span>
        </div>
        {!data?.news.length ? (
          <div className="empty-state">No news returned by free providers.</div>
        ) : (
          <div className="news-list">
            {data.news.map((item) => (
              <a key={item.news_id} href={item.url ?? "#"} target="_blank" rel="noreferrer">
                <span>{item.instrument_id}</span>
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
