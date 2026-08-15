---
name: frontend-ux
description: How the openPDN UI behaves -- density, units, provenance, viewport, colour and interaction rules for an engineering tool. Read before any work in apps/web.
---

# Frontend and UX

openPDN is an instrument, not a SaaS dashboard. The user is an engineer
deciding whether a rail meets its budget. They want numbers, units, and a
picture of where the copper is losing volts.

## Principles

* **Density without clutter.** 13 px base, 22 px rows, thin borders, no card
  shadows, no oversized headings. Whitespace that pushes data off screen is a
  cost, not a style.
* **The viewport is the workspace.** Panels are instruments around it. Nothing
  decorative may take space from it.
* **Units always.** No bare number anywhere in the UI. `mm`, `µm`, `mΩ`, `mV`,
  `A`, `A/mm²`, `W/mm²`, `mΩ/□`.
* **Assumptions are visible.** Every uncertain value carries a
  `ProvenanceBadge`: imported / configured / assumed / derived. An IR drop
  computed from an assumed thickness must not look like one computed from
  imported data.
* **Sources and loads are obvious.** Distinct, consistent colours
  (`--source`, `--load`), visible in the viewport and in the tables.
* **Numbers are copyable.** Monospace, tabular figures, selectable, no
  thousands separators — they break paste into a spreadsheet.
* **No animation that impedes measurement.** No transitions on values, no
  easing on pan/zoom of a plot being read. Instant is correct.
* **Dark and light.** Colours come from CSS custom properties in `styles.css`,
  overridden under `prefers-color-scheme: dark`. Never hard-code a hex value in
  a component.

## Layout

```text
┌─────────────────────────────────────────────────────┐
│ Toolbar / Study / Solve                             │
├──────────────┬──────────────────────┬───────────────┤
│ Layers/Nets  │                      │ Properties    │
│              │     PCB VIEWPORT     │ Sources       │
│              │                      │ Loads         │
│              │                      │ Probe         │
├──────────────┴──────────────────────┴───────────────┤
│ Results / Warnings / Solver output                  │
└─────────────────────────────────────────────────────┘
```

Implemented in `App.tsx` as a three-row grid (toolbar / workspace / bottom
panel) with a three-column workspace. The bottom panel is the deployment
console until a board loads, then the tabbed review area (Stackup, Vias,
Import Diagnostics, Geometry Stats, Source Info). Panels are
`<aside>`/`<main>` with `aria-label`s — tests query by role and label, never
by class.

## Honesty in the UI

The UI reads `/api/info` at start-up and must not offer a control for a
capability reported as `planned`. A disabled "Solve" button that never becomes
enabled is worse than no button.

When only the mock solver is available, the console says so unprompted. Nobody
should discover from a result that no physics was applied. When a result
arrives with `fidelity: "mock"`, it must be labelled everywhere it appears —
never rendered as a simulation.

Empty states explain *why* they are empty and what will fill them.

## Heat maps and legends

* A legend is mandatory: units and the numeric range, always visible.
* State whether the range is auto-scaled or fixed, and let the user pin it —
  comparing two studies with different auto-scales is a trap.
* Use a perceptually uniform sequential ramp for magnitudes; use a diverging
  ramp only around a meaningful zero.
* Never let a point-source singularity set the maximum (see the
  `solver-development` skill). Clip and say so.
* Colour is never the only channel: values are readable numerically too.

## Interaction

* Mouse and keyboard both work. Pan, zoom, select, probe, toggle layer — all
  reachable from the keyboard; document shortcuts in one place.
* Touch is supported for review, not required for precision work.
* Selection drives the inspector; the inspector never opens a modal for a value
  that could be edited in place.
* Long operations report progress and stay cancellable. A solve is not a
  spinner.

## Code structure

```text
src/
  api/         client.ts (the only fetch), types.ts (wire types)
  components/  presentational, props-in
  hooks/       data loading and derived state
  test/        setup and fixtures
  styles.css   design tokens and layout
```

* No component calls `fetch`.
* No `any`; wire types mirror the API response models.
* Unit conversion (SI → display) happens in components, at the boundary.
* Biome formats and lints; `npm run format` before committing.

## The viewport

Built on Canvas 2D behind a scene-model boundary (ADR-0008); the rules live in
`.agents/skills/pcb-viewer/SKILL.md`. WebGL arrives with scalar-field
overlays, replacing the draw backend only -- state, selection and the API do
not change.
