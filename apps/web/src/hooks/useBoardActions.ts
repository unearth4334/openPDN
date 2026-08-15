/**
 * Board workflow actions: import a file, import the dev fixture, and keep the
 * geometry cache filled for the active view.
 *
 * All network access goes through `api/client`; components call these and
 * never fetch. Geometry for a view is fetched once and cached in state, so
 * switching views or toggling layers never re-imports anything.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchDevFixture, fetchGeometry, importBoard, importDevFixture } from "../api/client";
import { useBoardState } from "../state/boardState";

export function useBoardActions() {
  const { state, dispatch } = useBoardState();

  const importFile = useCallback(
    async (file: File) => {
      dispatch({ type: "import-started", sourceName: file.name });
      try {
        const review = await importBoard(file);
        dispatch({ type: "import-succeeded", review });
      } catch (error) {
        dispatch({
          type: "import-failed",
          sourceName: file.name,
          message: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [dispatch],
  );

  const importFixture = useCallback(
    async (name: string) => {
      dispatch({ type: "import-started", sourceName: name });
      try {
        const review = await importDevFixture();
        dispatch({ type: "import-succeeded", review });
      } catch (error) {
        dispatch({
          type: "import-failed",
          sourceName: name,
          message: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [dispatch],
  );

  // Keep the active view's geometry present. Cached views are never refetched.
  const boardId = state.review?.board_id ?? null;
  const view = state.view;
  const haveGeometry = state.geometry[view] !== undefined;
  useEffect(() => {
    if (boardId === null || haveGeometry) {
      return;
    }
    const controller = new AbortController();
    fetchGeometry(boardId, view, controller.signal)
      .then((geometry) => dispatch({ type: "geometry-loaded", geometry }))
      .catch(() => {
        // A failed geometry fetch leaves the viewport empty; the review data
        // (readiness, diagnostics) is still usable, so this is not fatal.
      });
    return () => controller.abort();
  }, [boardId, view, haveGeometry, dispatch]);

  return { importFile, importFixture };
}

/** The dev fixture's name when the backend offers one; null otherwise. */
export function useDevFixture(): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetchDevFixture(controller.signal)
      .then((fixture) => setName(fixture.name))
      .catch(() => setName(null));
    return () => controller.abort();
  }, []);
  return name;
}
