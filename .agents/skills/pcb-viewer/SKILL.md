---
name: pcb-viewer
description: The board viewport -- renderer/domain separation, coordinate systems, scene model, selection and cross-probing, performance rules and future scalar-field compatibility. Read before touching apps/web/src/viewer or the Viewport component.
---

# The PCB viewer

The viewport is where an engineer decides whether openPDN understood their
board. Its architecture is decided by ADR-0008: **Canvas 2D today, behind a
scene-model boundary designed to be replaced by WebGL when scalar fields
arrive.**

## 1. What the renderer consumes

The renderer consumes the openPDN view model and nothing else:

```text
GET /api/boards/{id}/geometry     wire model: rings of [x_m, y_m] pairs
        │  buildScene()           once per (board, view), cached by useMemo
        ▼
Scene                             Path2D batches per (layer, net) +
        │  draw()                 raw rings for hit-testing
        ▼
canvas pixels
```

No IPC-2581 concept, no interchange-format vocabulary, and no backend type
other than the wire model may appear in `apps/web/src/viewer/`. A future
solver-result overlay consumes the *same* scene, keyed by the same ids.

## 2. Coordinate systems

* **World**: board coordinates, SI metres, y **up** — exactly what the API
  serves. Nothing in the frontend rescales geometry.
* **Screen**: CSS pixels, y down. The camera (`viewer/camera.ts`) is the only
  code that converts, and it is pure math with unit tests.
* The canvas transform maps world metres directly to device pixels
  (`setTransform` with a negative y scale). Zoom changes the transform, never
  the geometry; stroke widths are specified in screen pixels and divided by
  the scale.
* Engineering-unit display (mm, µm, mm²) happens in components via
  `lib/units.ts`, never in the viewer math.

## 3. Layer ordering and colour

* Layers arrive in physical stackup order, top first, and are drawn
  bottom-up so the top layer reads on top. Never sort layers alphabetically.
* Conductive layer colours are the `--layer-color-N` CSS custom properties,
  assigned by stackup position. The layer panel swatch and the canvas read
  the same variables; neither hard-codes a hex value, and dark mode stays a
  stylesheet concern.
* Visibility state (`hidden`, `solo`, opacity) never touches the scene or the
  network — it is paint-time input to `draw()`.

## 4. Selection and cross-probing

Selection is one shared concept in `state/boardState.tsx`:

```text
net list ⇄ viewport ⇄ via tables ⇄ stackup ⇄ inspector
```

* One `Selection` union (`net | via | region | layer`) drives every panel. Do
  not add a panel-private selection state; that is how views drift apart.
* Highlighting derives from selection (`selectedNetId`): selecting a via
  highlights its net's copper too.
* Tables ask the viewport to move with a `focus-requested` action carrying a
  monotonic token; the camera itself lives in refs inside the viewport so
  pan/zoom never re-renders React.
* Hit-testing runs on raw rings (bbox reject + even-odd), scanning visible
  layers top-first — it must work headless, because that is how it is tested.

## 5. Performance rules

Budget: 60 fps pan/zoom on the reference board (~640 normalised polygons,
~46 000 vertices) with obvious headroom.

* Geometry is fetched **once per (board, view)** and cached in state; layer
  toggles, net selection and tab switches must cause zero network traffic and
  zero scene rebuilds.
* Repaints go through one `requestAnimationFrame` gate; input events never
  draw synchronously.
* Never create per-polygon React elements. The scene batches one `Path2D` per
  `(layer, net)`; a full repaint is a handful of `fill()` calls.
* If a board makes this architecture visibly struggle, profile first; the
  planned answer is a WebGL draw backend behind the same `draw()` seam, with
  its own ADR.

## 6. Future scalar fields

Voltage, current density and via current will render in this viewport. The
decisions already made for them:

* Region, via, net and layer ids are stable across re-imports of identical
  content — results refer to geometry by id.
* Selection is independent of rendering mode; a heatmap must not need new
  selection plumbing.
* A field overlay is an additional draw pass (or a swapped backend), plus a
  mandatory legend per the frontend-ux skill. It must not replace the board
  UI or fork the scene model.
