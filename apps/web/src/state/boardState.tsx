/**
 * Central review state: the imported board, geometry caches, layer visibility,
 * net selection and the cross-probed selection.
 *
 * One store on purpose: the net list, viewport, via tables and inspector all
 * read and write the same selection, which is what keeps them synchronised
 * without ad-hoc prop plumbing. Camera state is deliberately NOT here -- pan
 * and zoom live inside the viewport and must never cause an app-wide render.
 */

import { createContext, type ReactNode, useContext, useMemo, useReducer } from "react";
import type { BoardReviewResponse, GeometryResponse, GeometryViewName } from "../api/types";

/** What the user has selected, driving the inspector and viewport highlight. */
export type Selection =
  | { kind: "net"; netId: string }
  | { kind: "via"; viaId: string }
  | { kind: "region"; regionId: string; layerId: string }
  | { kind: "layer"; layerId: string };

export type BottomTab = "stackup" | "vias" | "diagnostics" | "stats" | "source";

export type ImportPhase =
  | { status: "empty" }
  | { status: "importing"; sourceName: string }
  | { status: "ready" }
  | { status: "failed"; sourceName: string; message: string };

/** A one-shot request asking the viewport to move its camera. */
export interface FocusRequest {
  /** Monotonic token so an identical target still retriggers the effect. */
  token: number;
  x_m: number;
  y_m: number;
  /** Half-extent to frame around the target, metres. */
  radius_m: number;
}

export interface BoardState {
  phase: ImportPhase;
  review: BoardReviewResponse | null;
  /** Geometry per view, cached so switching views never refetches. */
  geometry: Partial<Record<GeometryViewName, GeometryResponse>>;
  view: GeometryViewName;
  /** Hidden layers; anything absent is visible. */
  hiddenLayers: ReadonlySet<string>;
  /** Layer opacity in [0.1, 1]; anything absent renders at 1. */
  layerOpacity: Readonly<Record<string, number>>;
  soloLayerId: string | null;
  selection: Selection | null;
  /** Vias highlighted from the via-group table. */
  highlightedViaIds: ReadonlySet<string>;
  bottomTab: BottomTab;
  focusRequest: FocusRequest | null;
}

export const initialBoardState: BoardState = {
  phase: { status: "empty" },
  review: null,
  geometry: {},
  view: "normalized",
  hiddenLayers: new Set(),
  layerOpacity: {},
  soloLayerId: null,
  selection: null,
  highlightedViaIds: new Set(),
  bottomTab: "stackup",
  focusRequest: null,
};

export type BoardAction =
  | { type: "import-started"; sourceName: string }
  | { type: "import-succeeded"; review: BoardReviewResponse }
  | { type: "import-failed"; sourceName: string; message: string }
  | { type: "geometry-loaded"; geometry: GeometryResponse }
  | { type: "view-changed"; view: GeometryViewName }
  | { type: "layer-visibility-toggled"; layerId: string }
  | { type: "layer-solo-toggled"; layerId: string }
  | { type: "all-layers-shown" }
  | { type: "layer-opacity-set"; layerId: string; opacity: number }
  | { type: "selected"; selection: Selection | null }
  | { type: "via-group-highlighted"; viaIds: string[] }
  | { type: "bottom-tab-changed"; tab: BottomTab }
  | { type: "focus-requested"; x_m: number; y_m: number; radius_m: number };

export function boardReducer(state: BoardState, action: BoardAction): BoardState {
  switch (action.type) {
    case "import-started":
      return { ...state, phase: { status: "importing", sourceName: action.sourceName } };
    case "import-succeeded":
      // A fresh board invalidates everything derived from the previous one.
      return {
        ...initialBoardState,
        phase: { status: "ready" },
        review: action.review,
        bottomTab: state.bottomTab,
      };
    case "import-failed":
      return {
        ...state,
        phase: { status: "failed", sourceName: action.sourceName, message: action.message },
      };
    case "geometry-loaded":
      return {
        ...state,
        geometry: { ...state.geometry, [action.geometry.view]: action.geometry },
      };
    case "view-changed":
      return { ...state, view: action.view };
    case "layer-visibility-toggled": {
      const hidden = new Set(state.hiddenLayers);
      if (hidden.has(action.layerId)) {
        hidden.delete(action.layerId);
      } else {
        hidden.add(action.layerId);
      }
      return { ...state, hiddenLayers: hidden };
    }
    case "layer-solo-toggled":
      return {
        ...state,
        soloLayerId: state.soloLayerId === action.layerId ? null : action.layerId,
      };
    case "all-layers-shown":
      return { ...state, hiddenLayers: new Set(), soloLayerId: null };
    case "layer-opacity-set":
      return {
        ...state,
        layerOpacity: { ...state.layerOpacity, [action.layerId]: action.opacity },
      };
    case "selected":
      return { ...state, selection: action.selection };
    case "via-group-highlighted":
      return { ...state, highlightedViaIds: new Set(action.viaIds) };
    case "bottom-tab-changed":
      return { ...state, bottomTab: action.tab };
    case "focus-requested":
      return {
        ...state,
        focusRequest: {
          token: (state.focusRequest?.token ?? 0) + 1,
          x_m: action.x_m,
          y_m: action.y_m,
          radius_m: action.radius_m,
        },
      };
    default:
      return state;
  }
}

/** True when `layerId` should be drawn under the current visibility state. */
export function isLayerVisible(state: BoardState, layerId: string): boolean {
  if (state.soloLayerId !== null) {
    return state.soloLayerId === layerId;
  }
  return !state.hiddenLayers.has(layerId);
}

/** The net id the current selection implies, if any (for highlight/dim). */
export function selectedNetId(state: BoardState): string | null {
  const selection = state.selection;
  if (selection === null || state.review === null) {
    return null;
  }
  if (selection.kind === "net") {
    return selection.netId;
  }
  if (selection.kind === "via") {
    const via = state.review.vias.find((candidate) => candidate.id === selection.viaId);
    return via?.net_id ?? null;
  }
  if (selection.kind === "region") {
    const layers = state.geometry[state.view]?.layers ?? [];
    for (const layer of layers) {
      const region = layer.regions.find((candidate) => candidate.id === selection.regionId);
      if (region) {
        return region.net_id;
      }
    }
  }
  return null;
}

interface BoardStore {
  state: BoardState;
  dispatch: (action: BoardAction) => void;
}

const BoardContext = createContext<BoardStore | null>(null);

export function BoardStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(boardReducer, initialBoardState);
  const store = useMemo(() => ({ state, dispatch }), [state]);
  return <BoardContext.Provider value={store}>{children}</BoardContext.Provider>;
}

export function useBoardState(): BoardStore {
  const store = useContext(BoardContext);
  if (store === null) {
    throw new Error("useBoardState requires a BoardStateProvider ancestor");
  }
  return store;
}
