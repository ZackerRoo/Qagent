import { useEffect, useState } from "react";

import { fetchWatchlist, saveWatchlistItem } from "../api/client";
import type { WatchlistItem } from "../types";

const emptyItem: WatchlistItem = {
  instrument_id: "CN:000001",
  thesis: "",
  status: "watch",
  tags: [],
};

export function Watchlist() {
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
        <h2>Watchlist</h2>
        <span className="count">{items.length}</span>
      </div>
      <div className="form-row">
        <input
          value={form.instrument_id}
          onChange={(event) => setForm({ ...form, instrument_id: event.target.value })}
          placeholder="US:AAPL or CN:000001"
        />
        <input
          value={form.thesis ?? ""}
          onChange={(event) => setForm({ ...form, thesis: event.target.value })}
          placeholder="Thesis"
        />
        <button type="button" onClick={submit}>
          Add
        </button>
      </div>
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Status</th>
              <th>Thesis</th>
              <th>Tags</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.instrument_id}>
                <td className="ticker">{item.instrument_id}</td>
                <td>{item.status}</td>
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
