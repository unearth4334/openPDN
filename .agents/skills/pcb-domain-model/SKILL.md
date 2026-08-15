---
name: pcb-domain-model
description: The canonical PCB model -- what each concept means, what belongs to the board versus the study, and how copper is normalised for meshing. Read before touching packages/domain.
---

# The canonical PCB model

openPDN's own description of a board. Importers produce it, solvers consume it,
and neither knows about the other.

**Everything named on this page is an openPDN concept, not an IPC-2581 or ODB++
concept.** `Board`, `Layer`, `Stackup`, `Net`, `CopperRegion`, `Via`, `Pad`,
`Component` and `Terminal` are defined by what a DC conduction analysis needs.
They resemble constructs in the interchange formats only because both describe
the same physical object.

Format vocabulary — IPC-2581's `LogicalNet`, `LayerFeature` and `Xform`, or
ODB++'s steps, symbols and EDA data — stops at the importer. Where a format
construct does not map exactly onto the canonical model, **the importer
translates the semantics**; it does not get to reshape the canonical model to
mirror a file format.

## Concepts

### Board topology — what was manufactured

| Concept | Meaning | Notes |
| --- | --- | --- |
| `Board` | One physical PCB | Immutable after import; validates referential integrity |
| `Stackup` | Ordered layers, top to bottom | Index 0 is the top; ordering is validated |
| `Layer` | One physical layer | `function` decides whether it conducts; thickness is a `Quantity` and may be unknown |
| `Material` | Isotropic conductor | Conductivity + optional TCR + reference temperature |
| `Net` | Named electrical net | Identity only; geometry lives in regions |
| `CopperRegion` | Contiguous copper on one layer, one net | The raw conductive geometry |
| `Via` | Plated connection between two layers | Hole diameter and plating thickness frequently unknown |
| `Pad` | A land on one layer | Optional outline; may be netless |
| `Terminal` | Where the board meets the outside world | Connector contact, regulator pin, load pin |
| `PhysicalComponent` | A placed part | Groups terminals under a reference designator |
| `ImportProvenance` | Which importer, which source, which digest | Reproducibility |

### Simulation boundary conditions — how it is being exercised

| Concept | Meaning |
| --- | --- |
| `AnalysisStudy` | One DC experiment on one board |
| `VoltageSource` | A terminal held at a potential — Dirichlet |
| `CurrentLoad` | A terminal sinking current — Neumann |
| `ResistanceProbe` | A request for terminal-to-terminal resistance |
| `MeshSettings` | Solver-independent discretisation controls |
| `ViaModel` | Lumped conductance (2.5-D) or resolved 3-D |
| `LayerThicknessOverride` | An engineer-supplied value for an unknown |

### Results

`ElectricalAnalysisResult` with `TerminalResult`, `NetIRDropResult`,
`ResistanceProbeResult`, `Diagnostic`, `SolverRunStats`, `SolverIdentity` and
`ResultFidelity`.

## The separating rule

**Topology is what the fabricator built. Boundary conditions are what the
engineer is asking.** They never mix.

* A `Board` has no notion of 0.85 V or 4 A.
* A `Study` never mutates a board. An unknown copper thickness the engineer
  supplies becomes a `LayerThicknessOverride` on the study; the board keeps
  recording that the value was absent.
* One import serves many studies: nominal load, worst case, per-rail sweep.
* Re-importing must never be required to change a load current.

Concretely:

```text
Board                          Study
  VCC0V85 copper geometry        source J4.3 = 0.85 V
  L1/L3 stackup                  load  U1    = 4.0 A
  two stitching vias             load  U2    = 2.1 A
  imported thicknesses           copper conductivity @ 85 °C
                                 L3 thickness override (unknown on import)
                                 via model: lumped conductance
                                 target element size 0.2 mm
```

## Provenance

Every physical quantity is a `Quantity(value, unit, provenance, note)` with
provenance in `{imported, configured, assumed, derived}`, and an assumed value
*must* carry a note. `weakest_provenance(...)` propagates: anything derived
from an assumption is itself assumed.

When a property is missing and no assumption has been authorised, raise
`MissingPhysicalPropertyError` (`Layer.require_thickness_m`,
`Via.require_barrel_cross_section_m2`). Do not substitute a default deep in a
solver — the user has to be told.

## Normalising copper for meshing

Raw `CopperRegion`s are *not* what a 2.5-D solver meshes. Before meshing,
copper is normalised and grouped by:

```text
(net, physical layer)
```

For each group: union overlapping regions, subtract anti-pads and clearances,
resolve the sheet conductance `σ·t` for the layer, and attach the terminals and
via endpoints that land on it. Each group becomes one 2-D conduction domain;
vias couple domains between layers.

Two consequences:

* `Board.copper_regions_on(net_id, layer_id)` exists because that grouping is
  the natural query. Use it.
* Normalisation output is derived data belonging to the solver pipeline, not to
  the `Board`. It is cacheable and it is keyed by geometry + material + mesh
  settings, never by source or load magnitudes.

## Extending the model

Add a concept when a *solver* or a *user-facing analysis* needs it, or when
several importers would independently populate it meaningfully — never because
one interchange format happens to have a field for it. Every new field costs
every importer, and a model shaped around one format has stopped being
canonical.

The test to apply: *would a second, unrelated importer fill this in, or does a
solver read it?* If neither, it belongs in the importer's diagnostics, in
`notes`, or nowhere.

Before adding:

1. Is it topology (board) or excitation (study)? Wrong side means it will be
   mutated later.
2. Can it be unknown in real fabrication data? Then it is `Quantity | None`,
   not a float with a default.
3. Does it need units in its name? Then it has them.
4. Does a solver read it, or is it decoration? Decoration goes in `notes` or
   nowhere.

Mark genuinely open questions with a `TODO` naming the ADR that will settle
them. Do not add fields speculatively.

## Deliberately absent, for now

Impedance/frequency-dependent properties, thermal fields, mechanical data,
netlist connectivity beyond nets, and design rules. Each arrives with the
feature that needs it — and, where it changes the architecture, an ADR.
