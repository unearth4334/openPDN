# ADR-0004: SI units internally, provenance on every physical quantity

## Status

Accepted — 2026-08-14.

## Context

PCB engineering mixes units freely: millimetres and mils for geometry, ounces
per square foot for copper weight, micrometres for plating, milliohms for
resistance, A/mm² for current density. Fabrication data arrives in whichever
the exporter preferred. A unit error in a conduction solver does not crash — it
returns a wrong number that looks reasonable.

The second problem is worse. Fabrication data routinely omits the properties a
DC analysis needs most: finished copper thickness per layer, via plating
thickness, actual conductivity, finished hole diameter. The tempting fix is a
sensible default — 1 oz copper, 25 µm plating, 5.8e7 S/m. The result is an
IR-drop figure indistinguishable from one computed from real data.

## Decision

**SI internally.** Metres, amperes, volts, ohms, siemens per metre, kelvin,
seconds throughout the domain, the application layer and the solvers.
Conversion happens only at boundaries: importers, the HTTP API, the CLI and the
UI. Conversion helpers live in `openpdn.domain.units`.

**Units in names.** Any variable holding a bare float carries its unit:
`thickness_m`, `current_a`, `resistance_ohm`, `conductivity_s_per_m`.

**Named constants.** Physical constants are named and sourced —
`COPPER_CONDUCTIVITY_S_PER_M = 5.8001e7` (IEC 60028, 100 % IACS at 20 °C) —
never literals inside an expression.

**Provenance on every physical quantity.** `Quantity(value, unit, provenance,
note)` with provenance in:

| Provenance | Meaning |
| --- | --- |
| `IMPORTED` | Read from the fabrication data |
| `CONFIGURED` | Entered by the engineer for this study |
| `ASSUMED` | A default standing in for an unknown |
| `DERIVED` | Computed from other quantities |

An `ASSUMED` quantity must carry a note explaining the assumption; the
constructor rejects it otherwise. `weakest_provenance()` propagates: anything
computed from an assumption is itself assumed.

**Missing means missing.** Where no assumption has been authorised, code raises
`MissingPhysicalPropertyError` (`Layer.require_thickness_m`,
`Via.require_barrel_cross_section_m2`) rather than substituting a value.

**The UI must show the difference.** `ProvenanceBadge` renders the four states
distinctly, and every displayed value carries its unit.

A unit-aware type system (pint or a typed newtype layer) is deferred: the
per-scalar overhead is unacceptable inside matrix assembly, and the naming
convention plus `Quantity.require_unit()` catches the realistic errors. Revisit
when a solver exists and can be profiled.

## Consequences

* Every conversion has one obvious place, and a swapped argument usually fails
  at `require_unit()` rather than producing a wrong answer.
* Importers must decide, per field, whether a value is imported or assumed —
  and justify assumptions in text a user will read.
* Results inherit the uncertainty of their inputs, so a report can state which
  numbers rest on guesses.
* More ceremony than a bare float. Numerical kernels may use bare floats
  internally once the surrounding code has established units; the boundary
  keeps `Quantity`.
* Refusing to default sometimes blocks a user until they supply a thickness.
  That is the intended behaviour, not a gap.
