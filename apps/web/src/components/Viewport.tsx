/**
 * The PCB workspace: a Canvas 2D renderer over the cached scene model.
 *
 * The camera lives in refs and repaints go through requestAnimationFrame, so
 * panning and zooming never re-render the React tree and never touch the
 * network. Selection and visibility come from the shared board state; the
 * renderer consumes the openPDN view model only (ADR-0008).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ViaResponse } from "../api/types";
import { useBoardActions } from "../hooks/useBoardActions";
import { formatMm } from "../lib/units";
import { isLayerVisible, selectedNetId, useBoardState } from "../state/boardState";
import { type Camera, fitBounds, focusOn, pan, screenToWorld, zoomAt } from "../viewer/camera";
import { draw, type LayerPaint } from "../viewer/render";
import { buildScene, hitTest, type Scene } from "../viewer/scene";
import { OpenBoard } from "./OpenBoard";

/** Conductive-layer palette slots defined in styles.css. */
const LAYER_COLOR_SLOTS = 6;

interface Hover {
  x_m: number;
  y_m: number;
  regionId: string | null;
  viaId: string | null;
  netId: string | null;
  layerId: string | null;
}

export function Viewport() {
  const { state } = useBoardState();
  useBoardActions(); // keeps the active view's geometry loaded

  if (state.phase.status !== "ready" || state.review === null) {
    return (
      <main className="viewport" aria-label="PCB viewport">
        <OpenBoard />
      </main>
    );
  }
  return <BoardCanvas />;
}

function BoardCanvas() {
  const { state, dispatch } = useBoardState();
  const review = state.review;
  const geometry = state.geometry[state.view];
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef<Camera | null>(null);
  const sizeRef = useRef({ width: 0, height: 0, dpr: 1 });
  const frameRef = useRef<number | null>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  const scene: Scene | null = useMemo(() => (geometry ? buildScene(geometry) : null), [geometry]);

  const conductiveLayers = useMemo(
    () => (review ? review.layers.filter((layer) => layer.is_conductive) : []),
    [review],
  );

  const layerColors = useMemo(() => {
    const style = getComputedStyle(document.documentElement);
    const colors = new Map<string, string>();
    conductiveLayers.forEach((layer, index) => {
      const slot = index % LAYER_COLOR_SLOTS;
      colors.set(layer.id, style.getPropertyValue(`--layer-color-${slot}`).trim() || "#888888");
    });
    return colors;
  }, [conductiveLayers]);

  const bounds = review?.bounds ?? null;
  const highlightNet = selectedNetId(state);

  const redraw = useCallback(() => {
    if (frameRef.current !== null) {
      return;
    }
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      const camera = cameraRef.current;
      if (!canvas || !context || !camera || !scene || !review) {
        return;
      }
      const rootStyle = getComputedStyle(document.documentElement);
      const paints: LayerPaint[] = conductiveLayers.map((layer) => ({
        layerId: layer.id,
        color: layerColors.get(layer.id) ?? "#888888",
        opacity: state.layerOpacity[layer.id] ?? 1,
        visible: isLayerVisible(state, layer.id),
      }));
      draw(context, scene, camera, {
        width_px: sizeRef.current.width,
        height_px: sizeRef.current.height,
        devicePixelRatio: sizeRef.current.dpr,
        layerPaints: paints,
        highlightNetId: highlightNet,
        selectedRegionId: state.selection?.kind === "region" ? state.selection.regionId : null,
        hoveredRegionId: hover?.regionId ?? null,
        vias: review.vias,
        highlightedViaIds: state.highlightedViaIds,
        selectedViaId: state.selection?.kind === "via" ? state.selection.viaId : null,
        colors: {
          profile: rootStyle.getPropertyValue("--viewport-profile").trim(),
          viaRing: rootStyle.getPropertyValue("--viewport-via-ring").trim(),
          viaHole: rootStyle.getPropertyValue("--viewport-via-hole").trim(),
          highlight: rootStyle.getPropertyValue("--viewport-highlight").trim(),
          selection: rootStyle.getPropertyValue("--viewport-selection").trim(),
        },
      });
    });
  }, [scene, review, conductiveLayers, layerColors, state, highlightNet, hover]);

  // Size the canvas to its container and keep it sized on resize.
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) {
      return;
    }
    const resize = () => {
      const rect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      sizeRef.current = { width: rect.width, height: rect.height, dpr };
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      if (cameraRef.current === null && bounds) {
        cameraRef.current = fitBounds(bounds, rect.width, rect.height);
      }
      redraw();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [bounds, redraw]);

  // Repaint whenever anything the renderer reads has changed.
  useEffect(() => {
    redraw();
  }, [redraw]);

  // Honour focus requests from tables and lists (centre a via, frame a net).
  useEffect(() => {
    const request = state.focusRequest;
    if (!request || !cameraRef.current) {
      return;
    }
    cameraRef.current = focusOn(
      sizeRef.current.width,
      sizeRef.current.height,
      request.x_m,
      request.y_m,
      request.radius_m,
    );
    redraw();
    // focusRequest is a fresh object per request (monotonic token), so this
    // retriggers even for an identical target.
  }, [state.focusRequest, redraw]);

  const fitView = useCallback(() => {
    if (bounds) {
      cameraRef.current = fitBounds(bounds, sizeRef.current.width, sizeRef.current.height);
      redraw();
    }
  }, [bounds, redraw]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "f" && !(event.target instanceof HTMLInputElement)) {
        fitView();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitView]);

  const visibleLayerIdsTopFirst = conductiveLayers
    .filter((layer) => isLayerVisible(state, layer.id))
    .map((layer) => layer.id);

  const hitAt = useCallback(
    (clientX: number, clientY: number): Hover | null => {
      const container = containerRef.current;
      const camera = cameraRef.current;
      if (!container || !camera || !scene || !review) {
        return null;
      }
      const rect = container.getBoundingClientRect();
      const world = screenToWorld(
        camera,
        rect.width,
        rect.height,
        clientX - rect.left,
        clientY - rect.top,
      );
      const via = nearestVia(review.vias, world.x_m, world.y_m, 6 / camera.scale_px_per_m);
      if (via) {
        return {
          x_m: world.x_m,
          y_m: world.y_m,
          regionId: null,
          viaId: via.id,
          netId: via.net_id,
          layerId: null,
        };
      }
      const region = hitTest(scene, visibleLayerIdsTopFirst, world.x_m, world.y_m);
      return {
        x_m: world.x_m,
        y_m: world.y_m,
        regionId: region?.id ?? null,
        viaId: null,
        netId: region?.netId ?? null,
        layerId: region?.layerId ?? null,
      };
    },
    [scene, review, visibleLayerIdsTopFirst],
  );

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY };
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current && event.buttons !== 0) {
      const dx = event.clientX - dragRef.current.x;
      const dy = event.clientY - dragRef.current.y;
      dragRef.current = { x: event.clientX, y: event.clientY };
      if (cameraRef.current) {
        cameraRef.current = pan(cameraRef.current, dx, dy);
        redraw();
      }
      return;
    }
    setHover(hitAt(event.clientX, event.clientY));
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const start = dragRef.current;
    dragRef.current = null;
    // A click, not a drag: select what is under the pointer.
    if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 3) {
      const hit = hitAt(event.clientX, event.clientY);
      if (hit?.viaId) {
        dispatch({ type: "selected", selection: { kind: "via", viaId: hit.viaId } });
      } else if (hit?.regionId && hit.layerId) {
        dispatch({
          type: "selected",
          selection: { kind: "region", regionId: hit.regionId, layerId: hit.layerId },
        });
      } else {
        dispatch({ type: "selected", selection: null });
      }
    }
  };

  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    const container = containerRef.current;
    if (!container || !cameraRef.current) {
      return;
    }
    const rect = container.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.18 : 1 / 1.18;
    cameraRef.current = zoomAt(
      cameraRef.current,
      rect.width,
      rect.height,
      event.clientX - rect.left,
      event.clientY - rect.top,
      factor,
    );
    redraw();
  };

  const netName =
    hover?.netId && review
      ? (review.nets.find((net) => net.id === hover.netId)?.name ?? null)
      : hover?.regionId
        ? "(unassigned)"
        : null;
  const layerName =
    hover?.layerId && review
      ? (review.layers.find((layer) => layer.id === hover.layerId)?.name ?? null)
      : null;

  return (
    <main className="viewport" aria-label="PCB viewport">
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }}>
        <canvas
          ref={canvasRef}
          className="viewport__canvas"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={() => setHover(null)}
          onWheel={onWheel}
        />
        {hover && (hover.viaId || hover.regionId) ? (
          <div className="viewport__hover" role="status">
            {hover.viaId ? `via ${hover.viaId}` : layerName}
            {netName ? ` · ${netName}` : null}
          </div>
        ) : null}
        <div className="viewport__controls">
          <button type="button" className="button" onClick={fitView} title="Fit board (f)">
            Fit
          </button>
        </div>
        {hover ? (
          <div className="viewport__hud">
            <span>X: {formatMm(hover.x_m)}</span>
            <span>Y: {formatMm(hover.y_m)}</span>
          </div>
        ) : null}
      </div>
    </main>
  );
}

function nearestVia(
  vias: ViaResponse[],
  x_m: number,
  y_m: number,
  tolerance_m: number,
): ViaResponse | null {
  let best: ViaResponse | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const via of vias) {
    const distance = Math.hypot(via.x_m - x_m, via.y_m - y_m);
    const radius = Math.max((via.drill_diameter?.value ?? 0) * 0.85, tolerance_m);
    if (distance <= radius && distance < bestDistance) {
      best = via;
      bestDistance = distance;
    }
  }
  return best;
}
