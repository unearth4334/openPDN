"""`openpdn inspect` and `openpdn validate-import` renderers.

Developer-facing views of the same review data the WebUI shows, produced by
the same application service, so the CLI and the UI cannot disagree about what
was imported. Formatting only -- no engineering decisions are made here.

Display units are engineering units (mm, um, mm^2); the DTOs carry SI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openpdn.domain.units import METRE, m_to_mm, m_to_um

if TYPE_CHECKING:
    from openpdn.application.review_models import BoardReview
    from openpdn.domain.provenance import Quantity

_RULE: Final = "─" * 52


def render_inspection(review: BoardReview) -> str:
    """Render the compact structural summary `openpdn inspect` prints."""
    lines: list[str] = []
    out = lines.append

    out(f"{review.source_format} Import Inspection")
    out(_RULE)
    out("")
    out("Source")
    out(f"  Format             {review.source_format}")
    out(f"  Revision           {review.format_revision or 'unknown'}")
    out(f"  File               {review.source_name}")
    if review.source_digest:
        out(f"  SHA-256            {review.source_digest[:16]}...")
    out("")
    out("Board")
    out(f"  Name               {review.name}")
    if review.bounds is not None:
        width_mm = m_to_mm(review.bounds.max_x_m - review.bounds.min_x_m)
        height_mm = m_to_mm(review.bounds.max_y_m - review.bounds.min_y_m)
        out(f"  Width              {width_mm:.3f} mm")
        out(f"  Height             {height_mm:.3f} mm")
    if review.total_thickness is not None:
        out(f"  Thickness          {m_to_mm(review.total_thickness.value):.3f} mm (derived)")
    out("")
    conductive = [layer for layer in review.layers if layer.is_conductive]
    out("Layers")
    out(f"  Total physical     {len(review.layers)}")
    out(f"  Conductive         {len(conductive)}")
    out(f"  Dielectric         {sum(1 for x in review.layers if x.function == 'dielectric')}")
    for layer in conductive:
        thickness = _quantity_um(layer.thickness)
        out(f"    [{layer.index:>2}] {layer.name:<22} {thickness}")
    out("")
    out(f"Nets                 {len(review.nets)}")
    out(f"Components           {len(review.components)}")
    out(f"Terminals            {len(review.terminals)}")
    out(f"Vias                 {len(review.vias)}")
    out("")
    out("Via groups")
    out(f"  {'Count':>6}  {'Span':<32} {'Drill':>9}  Padstack")
    for group in review.via_groups:
        drill = f"{m_to_mm(group.drill_diameter_m):.3f} mm" if group.drill_diameter_m else "-"
        span = f"{group.from_layer_id} -> {group.to_layer_id}"
        out(f"  {group.count:>6}  {span:<32} {drill:>9}  {group.padstack_name or '-'}")
    out("")
    out("Geometry")
    for label, count in sorted(review.timings.feature_counts.items()):
        out(f"  {label:<18} {count}")
    out(f"  {'normalized':<18} {sum(s.normalized_region_count for s in review.layer_stats)}")
    out("")
    out("Copper per layer")
    for stats in review.layer_stats:
        out(
            f"  {stats.layer_id:<22} {stats.copper_area_m2 * 1e6:>9.2f} mm^2  "
            f"{stats.source_feature_count:>5} features -> "
            f"{stats.normalized_region_count:>4} regions  "
            f"{stats.net_count:>3} nets  {stats.via_count:>4} vias"
        )
    out("")
    out("Import readiness")
    for item in review.capability_items:
        marker = {"present": "+", "partial": "~", "absent": "!", "unknown": "?"}[item.status]
        note = f"  ({item.note})" if item.note else ""
        out(f"  [{marker}] {item.name:<24}{note}")
    out(f"  => {review.readiness.value.replace('_', ' ')}")
    out("")
    out("Timings")
    timings = review.timings
    if timings.parse_seconds is not None:
        out(f"  parse              {timings.parse_seconds * 1e3:8.1f} ms")
    if timings.extract_seconds is not None:
        out(f"  extract            {timings.extract_seconds * 1e3:8.1f} ms")
    if timings.normalize_seconds is not None:
        out(f"  normalize          {timings.normalize_seconds * 1e3:8.1f} ms")
    if review.diagnostics:
        out("")
        out("Diagnostics")
        for diagnostic in review.diagnostics:
            out(f"  [{diagnostic.severity.value:<7}] {diagnostic.code}")
            out(f"            {diagnostic.message}")
    return "\n".join(lines)


def run_validation(
    review: BoardReview,
    *,
    expect_conductive_layers: int | None,
    expect_vias: int | None,
    expect_nets: int | None,
    expect_components: int | None,
) -> tuple[str, bool]:
    """Check a review against developer expectations.

    Built for local verification against private fixtures: expectations are
    passed as flags, so nothing board-specific is committed to source.

    Returns:
        A report and whether every check passed.
    """
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append((name, passed, detail))

    conductive = [layer for layer in review.layers if layer.is_conductive]
    if expect_conductive_layers is not None:
        check(
            "conductive layer count",
            len(conductive) == expect_conductive_layers,
            f"expected {expect_conductive_layers}, found {len(conductive)}",
        )
    if expect_vias is not None:
        check(
            "via count",
            len(review.vias) == expect_vias,
            f"expected {expect_vias}, found {len(review.vias)}",
        )
    if expect_nets is not None:
        check(
            "net count",
            len(review.nets) == expect_nets,
            f"expected {expect_nets}, found {len(review.nets)}",
        )
    if expect_components is not None:
        check(
            "component count",
            len(review.components) == expect_components,
            f"expected {expect_components}, found {len(review.components)}",
        )

    check("board profile", review.bounds is not None, "board extent resolved")
    empty_layers = [
        str(stats.layer_id) for stats in review.layer_stats if stats.normalized_region_count == 0
    ]
    check(
        "normalized copper on every conductive layer",
        not empty_layers,
        "all layers have copper" if not empty_layers else f"empty: {', '.join(empty_layers)}",
    )
    unthick = [layer.name for layer in conductive if layer.thickness is None]
    thickness_diagnosed = any(
        diagnostic.code == "import.missing_layer_thickness" for diagnostic in review.diagnostics
    )
    check(
        "stackup parsed or missing values diagnosed",
        not unthick or thickness_diagnosed,
        "thicknesses imported" if not unthick else f"missing but diagnosed: {unthick}",
    )
    errors = [d for d in review.diagnostics if d.severity.value == "error"]
    check(
        "no fatal geometry errors",
        not errors,
        "clean" if not errors else "; ".join(d.code for d in errors),
    )
    check(
        "not blocked for review",
        review.readiness.value != "not_ready" or not errors,
        f"readiness: {review.readiness.value}",
    )

    lines = [f"Validation of {review.source_name}", _RULE]
    all_passed = True
    for name, passed, detail in checks:
        all_passed &= passed
        lines.append(f"  [{'PASS' if passed else 'FAIL'}] {name:<44} {detail}")
    lines.append("")
    lines.append("OK" if all_passed else "FAILED")
    return "\n".join(lines), all_passed


def _quantity_um(quantity: Quantity | None) -> str:
    """Format an optional thickness in micrometres with provenance."""
    if quantity is None:
        return "unknown"
    return f"{m_to_um(quantity.require_unit(METRE)):.3f} um ({quantity.provenance.value})"
