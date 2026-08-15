import { describe, expect, it } from "vitest";
import type { BoardReviewResponse } from "../api/types";
import {
  type BoardState,
  boardReducer,
  initialBoardState,
  isLayerVisible,
  isViaSpanVisible,
  selectedNetId,
  viaSpanKey,
} from "./boardState";

const review = { board_id: "b1", vias: [{ id: "via-1", net_id: "net-gnd" }] };

function withReview(state: BoardState): BoardState {
  // Only the fields the reducer helpers read are needed here.
  return { ...state, review: review as unknown as BoardReviewResponse };
}

describe("boardReducer", () => {
  it("a successful import resets everything derived from the previous board", () => {
    let state = boardReducer(initialBoardState, {
      type: "layer-visibility-toggled",
      layerId: "L1",
    });
    state = boardReducer(state, { type: "selected", selection: { kind: "net", netId: "n" } });
    state = boardReducer(state, {
      type: "import-succeeded",
      review: review as unknown as BoardReviewResponse,
    });
    expect(state.hiddenLayers.size).toBe(0);
    expect(state.selection).toBeNull();
    expect(state.geometry).toEqual({});
    expect(state.phase.status).toBe("ready");
  });

  it("toggling visibility twice restores the layer", () => {
    let state = boardReducer(initialBoardState, {
      type: "layer-visibility-toggled",
      layerId: "L1",
    });
    expect(isLayerVisible(state, "L1")).toBe(false);
    state = boardReducer(state, { type: "layer-visibility-toggled", layerId: "L1" });
    expect(isLayerVisible(state, "L1")).toBe(true);
  });

  it("solo overrides per-layer visibility until released", () => {
    let state = boardReducer(initialBoardState, { type: "layer-solo-toggled", layerId: "L2" });
    expect(isLayerVisible(state, "L1")).toBe(false);
    expect(isLayerVisible(state, "L2")).toBe(true);
    state = boardReducer(state, { type: "layer-solo-toggled", layerId: "L2" });
    expect(isLayerVisible(state, "L1")).toBe(true);
  });

  it("focus requests carry a monotonic token so repeats retrigger", () => {
    let state = boardReducer(initialBoardState, {
      type: "focus-requested",
      x_m: 1,
      y_m: 2,
      radius_m: 0.001,
    });
    const first = state.focusRequest?.token;
    state = boardReducer(state, { type: "focus-requested", x_m: 1, y_m: 2, radius_m: 0.001 });
    expect(state.focusRequest?.token).not.toBe(first);
  });

  it("toggling via-span visibility twice restores the span", () => {
    const key = viaSpanKey("L1", "L2");
    let state = boardReducer(initialBoardState, {
      type: "via-span-visibility-toggled",
      spanKey: key,
    });
    expect(isViaSpanVisible(state, key)).toBe(false);
    state = boardReducer(state, { type: "via-span-visibility-toggled", spanKey: key });
    expect(isViaSpanVisible(state, key)).toBe(true);
  });

  it("all-via-spans-shown clears every hidden span", () => {
    let state = boardReducer(initialBoardState, {
      type: "via-span-visibility-toggled",
      spanKey: viaSpanKey("L1", "L2"),
    });
    state = boardReducer(state, {
      type: "via-span-visibility-toggled",
      spanKey: viaSpanKey("L2", "L3"),
    });
    state = boardReducer(state, { type: "all-via-spans-shown" });
    expect(state.hiddenViaSpans.size).toBe(0);
  });

  it("a successful import also resets hidden via spans", () => {
    let state = boardReducer(initialBoardState, {
      type: "via-span-visibility-toggled",
      spanKey: viaSpanKey("L1", "L2"),
    });
    state = boardReducer(state, {
      type: "import-succeeded",
      review: review as unknown as BoardReviewResponse,
    });
    expect(state.hiddenViaSpans.size).toBe(0);
  });
});

describe("viaSpanKey", () => {
  it("is independent of endpoint order", () => {
    expect(viaSpanKey("L1", "L2")).toBe(viaSpanKey("L2", "L1"));
  });

  it("distinguishes different spans", () => {
    expect(viaSpanKey("L1", "L2")).not.toBe(viaSpanKey("L2", "L3"));
  });
});

describe("selectedNetId", () => {
  it("resolves a via selection to its net for highlighting", () => {
    const state = withReview({
      ...initialBoardState,
      selection: { kind: "via", viaId: "via-1" },
    });
    expect(selectedNetId(state)).toBe("net-gnd");
  });

  it("is null with no selection", () => {
    expect(selectedNetId(withReview(initialBoardState))).toBeNull();
  });
});
