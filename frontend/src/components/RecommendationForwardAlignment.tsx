import { useEffect, useState } from "react";
import { fetchRecommendationForwardAlignment } from "../api/client";
import type { DataProviderMode, RecommendationForwardAlignment as Report } from "../types";

export function RecommendationForwardAlignment({ provider }: { provider: DataProviderMode }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setReport(null);
    setError("");
    fetchRecommendationForwardAlignment(provider).then((value) => {
      if (active) setReport(value);
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "读取失败");
    });
    return () => { active = false; };
  }, [provider]);
  return <section className="panel stack">
    <h3>推荐排名前向观察</h3>
    <p>自动保存全市场扫描的推荐顺序与策略分散结果；只读本地缓存计算 5 / 10 / 20 日收盘收益。此收益不是成交组合收益，不能用来验证历史模型收益差异。</p>
    {error ? <p role="alert">{error}</p> : !report ? <p>读取已保存记录…</p> : <>
      <p>前向起点：{report.prospective_start ?? "等待首个完整当日扫描"} · 有效日期 {report.cohorts.length} · 旧记录排除 {report.legacy_runs_excluded} · 不完整记录 {report.rejected_runs.length}</p>
      <p>历史同模型比较：{report.historical_comparison.comparable ? "身份已匹配" : "身份或协议证据不足"}</p>
      <div className="table-scroll"><table><thead><tr><th>日期</th><th>组合</th><th>5 日</th><th>10 日</th><th>20 日</th></tr></thead><tbody>
        {report.cohorts.slice(-10).reverse().flatMap((cohort) => ["top5", "top10", "rank6_10"].map((group) => <tr key={`${cohort.run_id}-${group}`}>
          <td>{cohort.decision_date}</td><td>{group}</td>
          {["5", "10", "20"].map((horizon) => {
            const metric = cohort.metrics[group][horizon];
            return <td key={horizon}>{metric.mean_return_pct === null ? "待成熟 / 缺缓存" : `${metric.mean_return_pct.toFixed(2)}%`} ({metric.mature_count}/{metric.expected_count})</td>;
          })}
        </tr>))}
      </tbody></table></div>
    </>}
  </section>;
}
