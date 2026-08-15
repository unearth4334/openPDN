/**
 * Loads the active result's field payload for the selected layer.
 *
 * Decoded layers are cached in board state per layer index, so switching
 * fields (voltage → |J|) or panning never refetches; only changing the
 * result or the layer triggers a download.
 */

import { useEffect } from "react";
import { fetchResultFields } from "../api/client";
import { useBoardState } from "../state/boardState";

export function useResultFields(): void {
  const { state, dispatch } = useBoardState();
  const jobId = state.activeResult?.jobId ?? null;
  const layerIndex = state.resultLayerIndex;
  const loaded = state.resultFields[layerIndex] !== undefined;

  useEffect(() => {
    if (jobId === null || loaded) {
      return;
    }
    const controller = new AbortController();
    fetchResultFields(jobId, layerIndex, controller.signal)
      .then((fields) => {
        dispatch({ type: "result-fields-loaded", layerIndex, fields });
      })
      .catch(() => {
        // A failed field fetch leaves the overlay off; metrics remain usable.
      });
    return () => controller.abort();
  }, [jobId, layerIndex, loaded, dispatch]);
}
