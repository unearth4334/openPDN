# ADR-0008: The board viewport renders with Canvas 2D behind a scene-model boundary

**Status:** Accepted (2026-08-14)

## Context

Milestone 1 needs an interactive board viewport: pan, zoom, layer visibility,
net highlighting, hit-testing, via selection. Later milestones must overlay
scalar fields (voltage, current density) on the same geometry.

Candidates surveyed: BoardUI (MIT, Angular + SVG, its own IPC-2581-derived
model), GRX (MIT, regl/WebGL engine with its own artwork format), tscircuit's
pcb-viewer (MIT, welded to Circuit JSON), Tracespace (MIT, static SVG from
Gerber, maintenance hiatus), kicanvas (MIT, KiCad documents only). Every one is
coupled to its own domain model at the ingestion boundary; adopting one would
mean translating openPDN's canonical geometry into a third-party model and
building selection/highlighting against foreign ids — the two things ADR-0002
exists to prevent.

Measured reality on the reference board: ~640 normalised polygons, ~46 000
vertices, a 2 MB geometry payload served in under 100 ms.

## Decision

**A purpose-built renderer on Canvas 2D**, structured so the drawing technology
is replaceable:

```text
GET /boards/{id}/geometry   (openPDN view model, SI metres)
        │
   buildScene()      one cached scene per (board, view): Path2D batches
        │            per (layer, net), raw rings kept for hit-testing
   draw(ctx, scene, camera, options)
```

* The camera lives in refs inside the viewport; pan/zoom changes a transform
  and repaints via `requestAnimationFrame` — no React re-render, no refetch,
  no geometry rebuild.
* Hit-testing runs on the raw rings (bbox reject + even-odd test), so it works
  headless and is unit-tested without a canvas.
* Colours come from CSS custom properties; the renderer hard-codes nothing.

WebGL is deliberately *not* the starting point: at this geometry volume Canvas
2D is comfortably within 60 fps, is debuggable with screenshots, and has no
shader/tessellation maintenance load. The scene-model boundary is the part
designed to survive: when boards approach ~100k polygons or scalar-field
overlays land, a WebGL backend (a textured mesh with a colormap shader is the
natural field renderer) replaces `draw()` without touching state, selection or
the API.

## Consequences

* No third-party viewer dependency; MIT-licensed projects above remain
  reference material for interaction patterns, not code.
* Scalar overlays get a clean insertion point (a second draw pass or a swapped
  backend) with stable region/via/net ids already flowing through selection.
* If geometry volume outgrows Canvas 2D earlier than expected, the renderer is
  the only module that changes; that replacement gets its own ADR with a
  measured justification.
