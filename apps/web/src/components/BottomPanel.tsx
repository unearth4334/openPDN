/**
 * The tabbed review area under the viewport: stackup, vias, diagnostics,
 * geometry statistics and source information.
 *
 * Falls back to the deployment console while no board is loaded, so the shell
 * still reports backend reachability the way an instrument should.
 */

import type { DeploymentState } from "../hooks/useDeploymentInfo";
import { type BottomTab, useBoardState } from "../state/boardState";
import { ConsolePanel } from "./ConsolePanel";
import { DiagnosticsView } from "./views/DiagnosticsView";
import { SourceView } from "./views/SourceView";
import { StackupView } from "./views/StackupView";
import { StatsView } from "./views/StatsView";
import { ViasView } from "./views/ViasView";

const TABS: { id: BottomTab; label: string }[] = [
  { id: "stackup", label: "Stackup" },
  { id: "vias", label: "Vias" },
  { id: "diagnostics", label: "Import Diagnostics" },
  { id: "stats", label: "Geometry Stats" },
  { id: "source", label: "Source Info" },
];

export function BottomPanel({ deployment }: { deployment: DeploymentState }) {
  const { state, dispatch } = useBoardState();

  if (state.review === null) {
    return <ConsolePanel deployment={deployment} />;
  }

  return (
    <section className="bottom-panel" aria-label="Board review">
      <div className="tab-bar" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={state.bottomTab === tab.id}
            className="tab-bar__tab"
            onClick={() => dispatch({ type: "bottom-tab-changed", tab: tab.id })}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="tab-panel" role="tabpanel">
        {state.bottomTab === "stackup" ? <StackupView /> : null}
        {state.bottomTab === "vias" ? <ViasView /> : null}
        {state.bottomTab === "diagnostics" ? <DiagnosticsView /> : null}
        {state.bottomTab === "stats" ? <StatsView /> : null}
        {state.bottomTab === "source" ? <SourceView /> : null}
      </div>
    </section>
  );
}
