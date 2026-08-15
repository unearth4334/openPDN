/**
 * Geometry statistics: per-layer copper accounting and pipeline instrumentation.
 *
 * These numbers make importer regressions visible at a glance -- a layer whose
 * region count halves between two imports of the same file is a bug found.
 */

import { formatBytes, formatMm2, formatMs } from "../../lib/units";
import { useBoardState } from "../../state/boardState";

export function StatsView() {
  const { state } = useBoardState();
  const review = state.review;
  if (review === null) {
    return null;
  }
  const layerName = (id: string) => review.layers.find((layer) => layer.id === id)?.name ?? id;
  const timings = review.timings;

  return (
    <div className="tab-panel--split">
      <table className="property-table" aria-label="Copper per layer">
        <thead>
          <tr>
            <th>Layer</th>
            <th>Copper area</th>
            <th>Source features</th>
            <th>Regions</th>
            <th>Nets</th>
            <th>Vias</th>
          </tr>
        </thead>
        <tbody>
          {review.layer_stats.map((stats) => (
            <tr key={stats.layer_id}>
              <td>{layerName(stats.layer_id)}</td>
              <td className="value numeric">{formatMm2(stats.copper_area_m2)}</td>
              <td className="value numeric">{stats.source_feature_count}</td>
              <td className="value numeric">{stats.normalized_region_count}</td>
              <td className="value numeric">{stats.net_count}</td>
              <td className="value numeric">{stats.via_count}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <table className="property-table" aria-label="Pipeline">
        <tbody>
          {timings.source_bytes !== null ? (
            <tr>
              <th scope="row">Source size</th>
              <td className="value numeric">{formatBytes(timings.source_bytes)}</td>
            </tr>
          ) : null}
          {timings.element_count !== null ? (
            <tr>
              <th scope="row">Source elements</th>
              <td className="value numeric">{timings.element_count}</td>
            </tr>
          ) : null}
          {timings.parse_seconds !== null ? (
            <tr>
              <th scope="row">Parse</th>
              <td className="value numeric">{formatMs(timings.parse_seconds)}</td>
            </tr>
          ) : null}
          {timings.extract_seconds !== null ? (
            <tr>
              <th scope="row">Extract</th>
              <td className="value numeric">{formatMs(timings.extract_seconds)}</td>
            </tr>
          ) : null}
          {timings.normalize_seconds !== null ? (
            <tr>
              <th scope="row">Normalise</th>
              <td className="value numeric">{formatMs(timings.normalize_seconds)}</td>
            </tr>
          ) : null}
          {timings.boolean_operations !== null ? (
            <tr>
              <th scope="row">Boolean unions</th>
              <td className="value numeric">{timings.boolean_operations}</td>
            </tr>
          ) : null}
          {timings.repaired_region_count !== null ? (
            <tr>
              <th scope="row">Repaired regions</th>
              <td className="value numeric">{timings.repaired_region_count}</td>
            </tr>
          ) : null}
          {timings.discarded_degenerate_count !== null ? (
            <tr>
              <th scope="row">Discarded slivers</th>
              <td className="value numeric">{timings.discarded_degenerate_count}</td>
            </tr>
          ) : null}
          {Object.entries(timings.feature_counts).map(([label, count]) => (
            <tr key={label}>
              <th scope="row">Source {label.replace(/_/g, " ")}</th>
              <td className="value numeric">{count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
