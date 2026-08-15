import { BottomPanel } from "./components/BottomPanel";
import { InspectorPanel } from "./components/InspectorPanel";
import { LayersPanel } from "./components/LayersPanel";
import { SimulationPanel } from "./components/SimulationPanel";
import { Toolbar } from "./components/Toolbar";
import { Viewport } from "./components/Viewport";
import { useDeploymentInfo } from "./hooks/useDeploymentInfo";
import { BoardStateProvider, useBoardState } from "./state/boardState";

/**
 * The application shell.
 *
 * Layout follows the engineering-tool convention: a thin toolbar, the viewport
 * as the dominant workspace, instruments in narrow side panels, and the tabbed
 * review area (stackup, vias, diagnostics) along the bottom.
 */
function RightPanel({ deployment }: { deployment: ReturnType<typeof useDeploymentInfo> }) {
  const { state } = useBoardState();
  if (state.simulationOpen) {
    return <SimulationPanel />;
  }
  return <InspectorPanel deployment={deployment} />;
}

export function App() {
  const deployment = useDeploymentInfo();

  return (
    <BoardStateProvider>
      <div className="app">
        <Toolbar deployment={deployment} />
        <div className="workspace">
          <LayersPanel />
          <Viewport />
          <RightPanel deployment={deployment} />
        </div>
        <BottomPanel deployment={deployment} />
      </div>
    </BoardStateProvider>
  );
}
