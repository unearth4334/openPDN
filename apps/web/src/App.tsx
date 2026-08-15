import { BottomPanel } from "./components/BottomPanel";
import { InspectorPanel } from "./components/InspectorPanel";
import { LayersPanel } from "./components/LayersPanel";
import { Toolbar } from "./components/Toolbar";
import { Viewport } from "./components/Viewport";
import { useDeploymentInfo } from "./hooks/useDeploymentInfo";
import { BoardStateProvider } from "./state/boardState";

/**
 * The application shell.
 *
 * Layout follows the engineering-tool convention: a thin toolbar, the viewport
 * as the dominant workspace, instruments in narrow side panels, and the tabbed
 * review area (stackup, vias, diagnostics) along the bottom.
 */
export function App() {
  const deployment = useDeploymentInfo();

  return (
    <BoardStateProvider>
      <div className="app">
        <Toolbar deployment={deployment} />
        <div className="workspace">
          <LayersPanel />
          <Viewport />
          <InspectorPanel deployment={deployment} />
        </div>
        <BottomPanel deployment={deployment} />
      </div>
    </BoardStateProvider>
  );
}
