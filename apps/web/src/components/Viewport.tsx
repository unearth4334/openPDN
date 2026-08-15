/**
 * The PCB workspace: a Canvas 2D renderer over the cached scene model.
 *
 * The camera lives in refs and repaints go through requestAnimationFrame, so
 * panning and zooming never re-render the React tree and never touch the
 * network. Selection and visibility come from the shared board state; the
 * renderer consumes the openPDN view model only (ADR-0008).
 *
 * Two rules keep the viewport from flickering, and both are load-bearing:
 *
 * 1. `canvas.width`/`canvas.height` are assigned **only when the pixel size
 *    actually changes**. Assigning them resets the bitmap to transparent
 *    black even when the value is unchanged, and with the repaint deferred to
 *    the next animation frame that shows the user an empty board.
 * 2. Nothing that runs per pointer-move may change the identity of `redraw`,
 *    or the effects that depend on it re-run — including the one that sizes
 *    the canvas. `redraw` is therefore stable, and reads what to paint from a
 *    ref that the paint effect refreshes after every render.
 *
 * When a resize does legitimately clear the bitmap, the repaint is synchronous
 * so not even that single frame shows an empty board.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BoardReviewResponse, ViaResponse } from "../api/types";
import { useBoardActions } from "../hooks/useBoardActions";
import { formatMm } from "../lib/units";
import { isLayerVisible, selectedNetId, useBoardState } from "../state/boardState";
import { type Camera, fitBounds, focusOn, pan, screenToWorld, zoomAt } from "../viewer/camera";
import { resizeCanvasIfNeeded } from "../viewer/canvasSize";
import { draw, type LayerPaint } from "../viewer/render";
import { buildScene, hitTest, type Scene } from "../viewer/scene";
import { OpenBoard } from "./OpenBoard";

/** Conductive-layer palette slots defined in styles.css. */
const LAYER_COLOR_SLOTS = 6;

/** Pointer travel below this (CSS px) is a click, not a drag. */
const CLICK_SLOP_PX = 3;

/** What sits under the pointer, in board coordinates. */
interface Hover {
  x_m: number;
  y_m: number;
  regionId: string | null;
  viaId: string | null;
  netId: string | null;
  layerId: string | null;
}

/** The identity of the hovered object, which drives React re-renders. */
interface HoverTarget {
  regionId: string | null;
  viaId: string | null;
  netId: string | null;
  layerId: string | null;
}

/** Everything one repaint needs, refreshed after every render. */
interface PaintInputs {
  scene: Scene | null;
  review: BoardReviewResponse | null;
  layerPaints: LayerPaint[];
  highlightNetId: string | null;
  selectedRegionId: string | null;
  hoveredRegionId: string | null;
  highlightedViaIds: ReadonlySet<string>;
  selectedViaId: string | null;
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
  const hudXRef = useRef<HTMLSpanElement | null>(null);
  const hudYRef = useRef<HTMLSpanElement | null>(null);
  const cameraRef = useRef<Camera | null>(null);
  const sizeRef = useRef({ width: 0, height: 0, dpr: 1 });
  const rectRef = useRef<DOMRect | null>(null);
  const frameRef = useRef<number | null>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const paintRef = useRef<PaintInputs | null>(null);
  const boundsRef = useRef(review?.bounds ?? null);
  const hoverTargetRef = useRef<HoverTarget | null>(null);

  // Only the *identity* of the hovered object is React state; the continuous
  // coordinate readout is written straight to the DOM, so sweeping the pointer
  // across copper does not re-render the application.
  const [hoverTarget, setHoverTarget] = useState<HoverTarget | null>(null);

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

  const visibleLayerIdsTopFirst = useMemo(
    () =>
      conductiveLayers.filter((layer) => isLayerVisible(state, layer.id)).map((layer) => layer.id),
    [conductiveLayers, state],
  );

  /** Paint immediately from the latest inputs. Stable for the whole lifetime. */
  const paintNow = useCallback(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    const camera = cameraRef.current;
    const paint = paintRef.current;
    if (!canvas || !context || !camera || !paint?.scene || !paint.review) {
      return;
    }
    const rootStyle = getComputedStyle(document.documentElement);
    draw(context, paint.scene, camera, {
      width_px: sizeRef.current.width,
      height_px: sizeRef.current.height,
      devicePixelRatio: sizeRef.current.dpr,
      layerPaints: paint.layerPaints,
      highlightNetId: paint.highlightNetId,
      selectedRegionId: paint.selectedRegionId,
      hoveredRegionId: paint.hoveredRegionId,
      vias: paint.review.vias,
      highlightedViaIds: paint.highlightedViaIds,
      selectedViaId: paint.selectedViaId,
      colors: {
        profile: rootStyle.getPropertyValue("--viewport-profile").trim(),
        viaRing: rootStyle.getPropertyValue("--viewport-via-ring").trim(),
        viaHole: rootStyle.getPropertyValue("--viewport-via-hole").trim(),
        highlight: rootStyle.getPropertyValue("--viewport-highlight").trim(),
        selection: rootStyle.getPropertyValue("--viewport-selection").trim(),
      },
    });
  }, []);

  /**
   * Request a repaint on the next frame. Stable for the component's lifetime:
   * effects may depend on it without being re-run by ordinary state changes,
   * and the queued frame paints the newest inputs rather than the ones
   * captured when it was scheduled.
   */
  const redraw = useCallback(() => {
    if (frameRef.current !== null) {
      return;
    }
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      paintNow();
    });
  }, [paintNow]);

  useEffect(() => {
    return () => {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, []);

  // Publish what to paint, then ask for a frame. No dependency array on
  // purpose: this runs after every render, and `redraw` collapses however many
  // requests arrive into one frame.
  useEffect(() => {
    boundsRef.current = review?.bounds ?? null;
    paintRef.current = {
      scene,
      review,
      layerPaints: conductiveLayers.map((layer) => ({
        layerId: layer.id,
        color: layerColors.get(layer.id) ?? "#888888",
        opacity: state.layerOpacity[layer.id] ?? 1,
        visible: isLayerVisible(state, layer.id),
      })),
      highlightNetId: selectedNetId(state),
      selectedRegionId: state.selection?.kind === "region" ? state.selection.regionId : null,
      hoveredRegionId: hoverTarget?.regionId ?? null,
      highlightedViaIds: state.highlightedViaIds,
      selectedViaId: state.selection?.kind === "via" ? state.selection.viaId : null,
    };
    redraw();
  });

  // Size the canvas to its container, and keep it sized. `redraw` is stable, so
  // this effect runs once per mount and then only when the container resizes.
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) {
      return;
    }
    const measure = () => {
      const rect = container.getBoundingClientRect();
      rectRef.current = rect;
      const dpr = window.devicePixelRatio || 1;
      sizeRef.current = { width: rect.width, height: rect.height, dpr };
      // Only reallocates when the size really changed; see canvasSize.ts.
      resizeCanvasIfNeeded(canvas, {
        width_px: rect.width,
        height_px: rect.height,
        devicePixelRatio: dpr,
      });
      if (cameraRef.current === null && boundsRef.current) {
        cameraRef.current = fitBounds(boundsRef.current, rect.width, rect.height);
      }
      // Paint synchronously rather than waiting for a frame: a resize that did
      // clear the bitmap would otherwise composite one empty frame.
      paintNow();
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [paintNow]);

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
    if (boundsRef.current) {
      cameraRef.current = fitBounds(
        boundsRef.current,
        sizeRef.current.width,
        sizeRef.current.height,
      );
      redraw();
    }
  }, [redraw]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "f" && !(event.target instanceof HTMLInputElement)) {
        fitView();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitView]);

  const hitAt = (clientX: number, clientY: number): Hover | null => {
    const camera = cameraRef.current;
    const rect = rectRef.current ?? containerRef.current?.getBoundingClientRect() ?? null;
    if (!camera || !rect || !scene || !review) {
      return null;
    }
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
  };

  /** Update the coordinate readout without re-rendering the tree. */
  const writeCoordinates = (hover: Hover | null) => {
    const xNode = hudXRef.current;
    const yNode = hudYRef.current;
    if (xNode) {
      xNode.textContent = hover ? `X: ${formatMm(hover.x_m)}` : "X: —";
    }
    if (yNode) {
      yNode.textContent = hover ? `Y: ${formatMm(hover.y_m)}` : "Y: —";
    }
  };

  /** Re-render only when the hovered object actually changes. */
  const commitHoverTarget = (hover: Hover | null) => {
    const next: HoverTarget | null =
      hover && (hover.regionId || hover.viaId)
        ? {
            regionId: hover.regionId,
            viaId: hover.viaId,
            netId: hover.netId,
            layerId: hover.layerId,
          }
        : null;
    const previous = hoverTargetRef.current;
    const unchanged =
      previous === next ||
      (previous !== null &&
        next !== null &&
        previous.regionId === next.regionId &&
        previous.viaId === next.viaId);
    if (unchanged) {
      return;
    }
    hoverTargetRef.current = next;
    setHoverTarget(next);
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    rectRef.current = event.currentTarget.getBoundingClientRect();
    dragRef.current = { x: event.clientX, y: event.clientY };
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (drag && event.buttons !== 0) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      dragRef.current = { x: event.clientX, y: event.clientY };
      const camera = cameraRef.current;
      if (camera) {
        cameraRef.current = pan(camera, dx, dy);
        redraw();
      }
      return;
    }
    const hover = hitAt(event.clientX, event.clientY);
    writeCoordinates(hover);
    commitHoverTarget(hover);
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const start = dragRef.current;
    dragRef.current = null;
    // A click, not a drag: select what is under the pointer.
    if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) < CLICK_SLOP_PX) {
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

  const onPointerLeave = () => {
    writeCoordinates(null);
    commitHoverTarget(null);
  };

  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    const camera = cameraRef.current;
    const rect = rectRef.current ?? event.currentTarget.getBoundingClientRect();
    if (!camera) {
      return;
    }
    const factor = event.deltaY < 0 ? 1.18 : 1 / 1.18;
    cameraRef.current = zoomAt(
      camera,
      rect.width,
      rect.height,
      event.clientX - rect.left,
      event.clientY - rect.top,
      factor,
    );
    redraw();
  };

  if (review === null) {
    return null;
  }

  const netName = hoverTarget?.netId
    ? (review.nets.find((net) => net.id === hoverTarget.netId)?.name ?? null)
    : hoverTarget?.regionId
      ? "(unassigned)"
      : null;
  const layerName = hoverTarget?.layerId
    ? (review.layers.find((layer) => layer.id === hoverTarget.layerId)?.name ?? null)
    : null;

  return (
    <main className="viewport" aria-label="PCB viewport">
      <div ref={containerRef} className="viewport__stage">
        <canvas
          ref={canvasRef}
          className="viewport__canvas"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerLeave}
          onWheel={onWheel}
        />
        {hoverTarget ? (
          <div className="viewport__hover" role="status">
            {hoverTarget.viaId ? `via ${hoverTarget.viaId}` : layerName}
            {netName ? ` · ${netName}` : null}
          </div>
        ) : null}
        <div className="viewport__controls">
          <button type="button" className="button" onClick={fitView} title="Fit board (f)">
            Fit
          </button>
        </div>
        <div className="viewport__hud">
          <span ref={hudXRef}>X: —</span>
          <span ref={hudYRef}>Y: —</span>
        </div>
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
