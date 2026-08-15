/**
 * Source information: what was imported, from where, through which pipeline.
 */

import { useBoardState } from "../../state/boardState";

export function SourceView() {
  const { state } = useBoardState();
  const review = state.review;
  if (review === null) {
    return null;
  }
  return (
    <table className="property-table" aria-label="Source information">
      <tbody>
        <tr>
          <th scope="row">File</th>
          <td className="value numeric">{review.source_name}</td>
        </tr>
        <tr>
          <th scope="row">Format</th>
          <td className="value">{review.source_format}</td>
        </tr>
        <tr>
          <th scope="row">Revision</th>
          <td className="value">{review.format_revision ?? "unknown"}</td>
        </tr>
        <tr>
          <th scope="row">Board name</th>
          <td className="value">{review.name}</td>
        </tr>
        <tr>
          <th scope="row">SHA-256</th>
          <td className="value numeric">{review.source_digest ?? "—"}</td>
        </tr>
        <tr>
          <th scope="row">Board id</th>
          <td className="value numeric">{review.board_id}</td>
        </tr>
        <tr>
          <th scope="row">Imported</th>
          <td className="value numeric">
            {new Date(review.stored_at_epoch_s * 1000).toISOString()}
          </td>
        </tr>
      </tbody>
    </table>
  );
}
