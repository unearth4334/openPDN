import { useEffect, useState } from "react";
import { ApiError, fetchInfo } from "../api/client";
import type { InfoResponse } from "../api/types";

export type DeploymentState =
  | { status: "loading" }
  | { status: "ready"; info: InfoResponse }
  | { status: "error"; message: string };

/**
 * Loads the deployment description once on mount.
 *
 * The UI must know which capabilities are real before it offers them, so this
 * is fetched at start-up rather than assumed.
 */
export function useDeploymentInfo(): DeploymentState {
  const [state, setState] = useState<DeploymentState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchInfo(controller.signal)
      .then((info) => setState({ status: "ready", info }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        const message =
          error instanceof ApiError ? error.message : "Unexpected error loading /api/info";
        setState({ status: "error", message });
      });
    return () => controller.abort();
  }, []);

  return state;
}
