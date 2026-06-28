import { useEffect, useState } from "react";

import { fetchWatchlist, saveWatchlistItem } from "../api/client";
import { useI18n } from "../i18n";
import { formatInstrumentDisplay } from "../lib/instruments";
import { localizeStatus } from "../lib/localize";
import type { WatchlistItem } from "../types";

const emptyItem: WatchlistItem = {
  instrument_id: "CN:000001",
  thesis: "",
  status: "watch",
  tags: [],
};

export function Watchlist() {
  const { language, t } = useI18n();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [form, setForm] = useState<WatchlistItem>(emptyItem);

  async function load() {
    const result = await fetchWatchlist();
    setItems(result.items);
  }

  useEffect(() => {
    void load();
  }, []);

  async function submit() {
    await saveWatchlistItem({
      ...form,
      tags: form.tags.length ? form.tags : ["manual"],
    });
    await load();
  }

  return (
    <section className="panel stack">
      <div className="panel-heading">
        <h2>{t("watchlist.title")}</h2>
        <span className="count">{items.length}</span>
      </div>
      <div className="form-row">
        <input
          value={form.instrument_id}
          onChange={(event) => setForm({ ...form, instrument_id: event.target.value })}
          placeholder={t("watchlist.instrumentPlaceholder")}
        />
        <input
          value={form.thesis ?? ""}
          onChange={(event) => setForm({ ...form, thesis: event.target.value })}
          placeholder={t("watchlist.thesis")}
        />
        <button type="button" onClick={submit}>
          {t("watchlist.add")}
        </button>
      </div>
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>{t("common.symbol")}</th>
              <th>{t("common.status")}</th>
              <th>{t("watchlist.thesis")}</th>
              <th>{t("watchlist.tags")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.instrument_id}>
                <td className="ticker" title={formatInstrumentDisplay(item.instrument_id)}>
                  {formatInstrumentDisplay(item.instrument_id)}
                </td>
                <td>{localizeStatus(item.status, language)}</td>
                <td>{item.thesis ?? "-"}</td>
                <td>{item.tags.join(", ") || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
