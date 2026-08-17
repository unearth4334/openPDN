"""CLI surface for simulations, jobs, results and execution processes.

`simulate` runs inline by default (import, solve, print -- the numerical
debugging path) or queues through the durable store with `--queue`, where the
orchestrator executes it exactly as a WebUI submission. Terminals and nets
are addressed by their human names (`J4.3`, net names) and resolved against
the imported board; ids also work.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import TYPE_CHECKING, Any

import numpy as np

from openpdn.application.simulation_models import (
    AccuracyProfile,
    LoadSpec,
    ReferencePolicy,
    SimulationDraft,
    SimulationKind,
    SimulationRequestError,
)

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from openpdn.domain.board import Board
    from openpdn.infrastructure.container import Container

EXIT_OK = 0
EXIT_FAILURE = 1


def register(subparsers: Any) -> None:
    """Add simulation-related subcommands to the CLI parser."""
    simulate = subparsers.add_parser("simulate", help="Run or queue a DC simulation.")
    kinds = simulate.add_subparsers(dest="simulate_kind", required=True)

    common: list[tuple[str, dict[str, Any]]] = [
        ("source", {"type": _path, "help": "Path to the PCB source file."}),
        ("--net", {"required": True, "help": "Net name or id."}),
        (
            "--accuracy",
            {
                "choices": [p.value for p in AccuracyProfile],
                "default": AccuracyProfile.STANDARD.value,
                "help": "Accuracy profile (default: standard).",
            },
        ),
        (
            "--target-error",
            {
                "type": float,
                "default": 1e-3,
                "help": "Reference only: QoI relative-change target (default 1e-3).",
            },
        ),
        (
            "--max-passes",
            {
                "type": int,
                "default": 5,
                "help": "Reference only: adaptive pass ceiling (default 5).",
            },
        ),
        (
            "--max-dofs",
            {
                "type": int,
                "default": 2_000_000,
                "help": "Reference only: DOF ceiling (default 2,000,000).",
            },
        ),
        (
            "--element-order",
            {
                "choices": ["p1", "p2"],
                "default": "p2",
                "help": "Reference only: element order (default p2).",
            },
        ),
        (
            "--via-plating-um",
            {
                "type": float,
                "default": None,
                "help": "Assumed via plating thickness in micrometres, when unknown.",
            },
        ),
        (
            "--queue",
            {
                "action": "store_true",
                "help": "Queue for the orchestrator instead of solving inline.",
            },
        ),
    ]

    resistance = kinds.add_parser("resistance", help="Effective terminal-to-terminal resistance.")
    for name, options in common:
        resistance.add_argument(name, **options)
    resistance.add_argument("--from", dest="from_terminal", required=True, help="Terminal A.")
    resistance.add_argument("--to", dest="to_terminal", required=True, help="Terminal B.")

    ir_drop = kinds.add_parser("ir-drop", help="DC IR-drop with sources and loads.")
    for name, options in common:
        ir_drop.add_argument(name, **options)
    ir_drop.add_argument("--source", dest="source_terminal", required=True)
    ir_drop.add_argument("--voltage", type=float, required=True, help="Source voltage in volts.")
    ir_drop.add_argument(
        "--load",
        action="append",
        default=[],
        metavar="TERMINAL=AMPS",
        help="A load terminal and its current; repeatable.",
    )

    jobs = subparsers.add_parser("jobs", help="Inspect and manage simulation jobs.")
    job_commands = jobs.add_subparsers(dest="jobs_command", required=True)
    job_commands.add_parser("list", help="List recent jobs.")
    inspect = job_commands.add_parser("inspect", help="Show one job in detail.")
    inspect.add_argument("job_id")
    cancel = job_commands.add_parser("cancel", help="Cancel a queued or running job.")
    cancel.add_argument("job_id")

    results = subparsers.add_parser("results", help="Inspect stored simulation results.")
    result_commands = results.add_subparsers(dest="results_command", required=True)
    r_inspect = result_commands.add_parser("inspect", help="Print a result's metrics.")
    r_inspect.add_argument("job_id")
    r_conv = result_commands.add_parser(
        "convergence", help="Print a Reference result's convergence history."
    )
    r_conv.add_argument("job_id")
    r_vtk = result_commands.add_parser(
        "export-vtk", help="Export result fields as VTK for ParaView."
    )
    r_vtk.add_argument("job_id")
    r_vtk.add_argument("--output", type=_path, required=True, help="Output .vtk path.")

    subparsers.add_parser("orchestrator", help="Run the simulation orchestrator (long-running).")
    worker = subparsers.add_parser(
        "solver-worker", help="Execute one claimed job (spawned by the orchestrator)."
    )
    worker.add_argument("--job-id", required=True)
    worker.add_argument("--worker-id", required=True)


def _path(value: str) -> Path:
    from pathlib import Path as RealPath

    return RealPath(value)


def dispatch(args: argparse.Namespace, container: Container) -> int | None:
    """Handle a simulation-related command, or return None if not ours."""
    command = args.command
    if command == "simulate":
        return _command_simulate(args, container)
    if command == "jobs":
        return _command_jobs(args, container)
    if command == "results":
        return _command_results(args, container)
    if command == "orchestrator":
        return _command_orchestrator(container)
    if command == "solver-worker":
        return _command_worker(args, container)
    return None


# -- simulate -------------------------------------------------------------------------


def _command_simulate(args: argparse.Namespace, container: Container) -> int:
    review = container.review_service.import_and_review(args.source)
    record = container.review_service._store.get(review.board_id)
    if record is None:  # pragma: no cover - import_and_review just stored it
        raise SimulationRequestError("Imported board vanished from the store")
    board = record.import_result.board

    net_id = _resolve_net(board, args.net)
    plating_m = args.via_plating_um * 1e-6 if args.via_plating_um is not None else None
    reference_policy = (
        ReferencePolicy(
            target_qoi_rel_change=args.target_error,
            max_passes=args.max_passes,
            max_dofs=args.max_dofs,
            element_order=args.element_order,
        )
        if args.accuracy == AccuracyProfile.REFERENCE.value
        else None
    )

    if args.simulate_kind == "resistance":
        draft = SimulationDraft(
            kind=SimulationKind.RESISTANCE,
            board_id=review.board_id,
            net_id=net_id,
            source_terminal_ids=(_resolve_terminal(board, args.from_terminal, net_id),),
            to_terminal_ids=(_resolve_terminal(board, args.to_terminal, net_id),),
            accuracy=AccuracyProfile(args.accuracy),
            via_plating_m=plating_m,
            reference_policy=reference_policy,
        )
    else:
        loads = []
        for item in args.load:
            terminal, _, amps = item.partition("=")
            if not amps:
                raise SimulationRequestError(f"Load {item!r} is not TERMINAL=AMPS")
            loads.append(
                LoadSpec(
                    current_a=float(amps),
                    terminal_ids=(_resolve_terminal(board, terminal, net_id),),
                )
            )
        draft = SimulationDraft(
            kind=SimulationKind.IR_DROP,
            board_id=review.board_id,
            net_id=net_id,
            source_terminal_ids=(_resolve_terminal(board, args.source_terminal, net_id),),
            source_voltage_v=args.voltage,
            loads=tuple(loads),
            accuracy=AccuracyProfile(args.accuracy),
            via_plating_m=plating_m,
            reference_policy=reference_policy,
        )

    plan = container.simulation_service.plan(draft)
    estimate = plan.estimate
    print(
        f"estimate: ~{estimate.mesh_points} points, ~{estimate.dofs} DOFs, "
        f"~{estimate.estimated_memory_bytes / 1e9:.2f} GB, "
        f"compute {estimate.compute_class.value}"
    )
    if not plan.connectivity_ok:
        print(f"error: {plan.connectivity_message}", file=sys.stderr)
        return EXIT_FAILURE

    if args.queue:
        queued = container.simulation_service.queue(draft)
        label = "duplicate of active job" if queued.duplicate_of else "queued"
        print(f"{label}: {queued.job.spec.job_id}")
        return EXIT_OK

    # Inline execution: the numerical debugging path.
    from openpdn.infrastructure.simulation_worker import run_inline

    result, fields, reference_history = run_inline(
        plan.resolved_spec, board, container.geometry_normalizer
    )

    if reference_history is not None:
        _print_reference_history(reference_history)
    print(f"mesh: {result.stats.mesh_nodes} nodes, {result.stats.mesh_elements} elements")
    print(f"residual: {result.stats.residual:.3e}")
    for probe in result.probes:
        print(f"R = {_format_resistance(probe.resistance_ohm)}")
    for terminal in result.terminals:
        print(f"  {terminal.terminal_id}: {terminal.voltage_v:.6f} V, {terminal.current_a:.4f} A")
    conservation = fields.conservation
    print(
        f"conservation: imbalance {conservation.imbalance_fraction:.2e}, "
        f"power mismatch {conservation.power_mismatch_fraction:.2e}, "
        f"loss {conservation.dissipated_power_w:.6e} W"
    )
    for diagnostic in result.diagnostics:
        print(f"  [{diagnostic.severity}] {diagnostic.code}: {diagnostic.message}")
    return EXIT_OK


def _resolve_net(board: Board, name_or_id: str) -> str:
    for net in board.nets:
        if str(net.id) == name_or_id or net.name == name_or_id:
            return str(net.id)
    raise SimulationRequestError(f"Unknown net {name_or_id!r}")


def _resolve_terminal(board: Board, name_or_id: str, net_id: str) -> str:
    matches = [
        terminal
        for terminal in board.terminals
        if str(terminal.id) == name_or_id or terminal.name == name_or_id
    ]
    if not matches:
        raise SimulationRequestError(f"Unknown terminal {name_or_id!r}")
    on_net = [t for t in matches if str(t.net_id) == net_id]
    chosen = on_net[0] if on_net else matches[0]
    return str(chosen.id)


def _format_resistance(value_ohm: float) -> str:
    if abs(value_ohm) >= 1.0:
        return f"{value_ohm:.6f} ohm"
    if abs(value_ohm) >= 1e-3:
        return f"{value_ohm * 1e3:.4f} mohm"
    return f"{value_ohm * 1e6:.2f} uohm"


# -- jobs -----------------------------------------------------------------------------


def _command_jobs(args: argparse.Namespace, container: Container) -> int:
    service = container.simulation_service
    if args.jobs_command == "list":
        for job in service.list_jobs():
            summary = job.result_summary or {}
            extra = ""
            if "resistance_ohm" in summary:
                extra = f"  R={_format_resistance(summary['resistance_ohm'])}"
            elif "worst_drop_v" in summary:
                extra = f"  worst drop {summary['worst_drop_v'] * 1e3:.3f} mV"
            print(
                f"{job.spec.job_id}  {job.state.value:<24} {job.stage:<22} {job.spec.name}{extra}"
            )
        return EXIT_OK
    if args.jobs_command == "inspect":
        record = service.get(args.job_id)
        if record is None:
            print("error: unknown job", file=sys.stderr)
            return EXIT_FAILURE
        payload = {
            "job_id": record.spec.job_id,
            "state": record.state.value,
            "stage": record.stage,
            "message": record.message,
            "attempt": record.attempt,
            "spec": json.loads(record.spec.to_json()),
            "result_summary": record.result_summary,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    if args.jobs_command == "cancel":
        if container.simulation_service.cancel(args.job_id):
            print("cancellation requested")
            return EXIT_OK
        print("error: job is not active", file=sys.stderr)
        return EXIT_FAILURE
    raise AssertionError


# -- results --------------------------------------------------------------------------


def _command_results(args: argparse.Namespace, container: Container) -> int:
    result_dir = container.artifact_store.result_dir(args.job_id)
    if result_dir is None:
        print("error: no published result for that job", file=sys.stderr)
        return EXIT_FAILURE
    if args.results_command == "inspect":
        metrics = json.loads((result_dir / "metrics.json").read_text())
        print(json.dumps(metrics, indent=2, sort_keys=True))
        return EXIT_OK
    if args.results_command == "convergence":
        metrics = json.loads((result_dir / "metrics.json").read_text())
        reference = metrics.get("reference")
        if reference is None:
            print(
                "error: not a Reference result (no adaptive history)",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        _print_reference_history(reference)
        return EXIT_OK
    if args.results_command == "export-vtk":
        _export_vtk(result_dir, args.output)
        print(f"wrote {args.output}")
        return EXIT_OK
    raise AssertionError


def _print_reference_history(reference: dict[str, Any]) -> None:
    """Render the published adaptive history as the convergence table."""
    header = (
        f"{'pass':>4} {'DOFs':>9} {'elements':>9} {'QoI':>14} "
        f"{'dQoI':>10} {'est. error':>12} {'marked':>8}"
    )
    print(header)
    for generation in reference["generations"]:
        change = generation["qoi_rel_change"]
        rendered = "--" if change is None else f"{change:.3e}"
        print(
            f"{generation['index']:>4} {generation['dofs']:>9} "
            f"{generation['elements']:>9} {generation['quantity_of_interest']:>14.9f} "
            f"{rendered:>10} {generation['estimated_error']:>12.4e} "
            f"{generation['marked_elements']:>8}"
        )
    print(f"status: {reference['status']}")
    clamped = sum(g.get("floor_clamped_seeds", 0) for g in reference["generations"])
    if clamped:
        print(
            f"note: {clamped} refinement seed(s) hit the geometry-precision "
            "floor -- further accuracy is limited by the imported geometry's "
            "own fidelity, not by compute"
        )
    for quantity in reference["quantities"]:
        detail = ""
        if quantity["singular"]:
            detail = " (singular: reported, never converged on)"
        elif quantity["extrapolated"] is not None:
            detail = (
                f" extrapolated={quantity['extrapolated']:.9g}"
                f" observed_order={quantity['observed_order']:.2f}"
            )
        state = "converged" if quantity["converged"] else "not converged"
        print(f"  {quantity['name']}: {state}{detail}")


def _export_vtk(result_dir: Path, output: Path) -> None:
    """Write the result's meshes and fields as a legacy ASCII VTK file.

    All layers go into one unstructured grid; a `layer` cell array separates
    them in ParaView. Point data: voltage. Cell data: |J| and power density.
    """
    metrics = json.loads((result_dir / "metrics.json").read_text())
    points_all: list[np.ndarray] = []
    tris_all: list[np.ndarray] = []
    voltage_all: list[np.ndarray] = []
    j_all: list[np.ndarray] = []
    layer_tag: list[np.ndarray] = []
    offset = 0
    for index, entry in enumerate(metrics["layer_files"]):
        with np.load(result_dir / entry["file"], allow_pickle=False) as data:
            pts = data["points"]
            tris = data["triangles"] + offset
            points_all.append(pts)
            tris_all.append(tris)
            voltage_all.append(data["voltage_v"])
            j_all.append(data["j_a_per_m2"])
            layer_tag.append(np.full(len(tris), index))
            offset += len(pts)
    points = np.vstack(points_all)
    tris = np.vstack(tris_all)
    voltage = np.concatenate(voltage_all)
    j_mag = np.concatenate(j_all)
    layers = np.concatenate(layer_tag)

    with output.open("w") as handle:
        handle.write("# vtk DataFile Version 3.0\nopenPDN result\nASCII\n")
        handle.write("DATASET UNSTRUCTURED_GRID\n")
        handle.write(f"POINTS {len(points)} double\n")
        for x, y in points:
            handle.write(f"{x} {y} 0.0\n")
        handle.write(f"CELLS {len(tris)} {len(tris) * 4}\n")
        for a, b, c in tris:
            handle.write(f"3 {a} {b} {c}\n")
        handle.write(f"CELL_TYPES {len(tris)}\n")
        handle.write("5\n" * len(tris))
        handle.write(f"POINT_DATA {len(points)}\n")
        handle.write("SCALARS voltage_v double 1\nLOOKUP_TABLE default\n")
        for value in voltage:
            handle.write(f"{value}\n")
        handle.write(f"CELL_DATA {len(tris)}\n")
        handle.write("SCALARS j_a_per_m2 double 1\nLOOKUP_TABLE default\n")
        for value in j_mag:
            handle.write(f"{value}\n")
        handle.write("SCALARS layer int 1\nLOOKUP_TABLE default\n")
        for value in layers:
            handle.write(f"{value}\n")


# -- execution processes --------------------------------------------------------------


def _command_orchestrator(container: Container) -> int:
    from openpdn.infrastructure.orchestrator import Orchestrator

    Orchestrator(
        jobs=container.job_store,
        artifacts=container.artifact_store,
        limits=container.worker_limits,
    ).run_forever()
    return EXIT_OK


def _command_worker(args: argparse.Namespace, container: Container) -> int:
    from openpdn.infrastructure.simulation_worker import run_job

    return run_job(
        job_id=args.job_id,
        worker_id=args.worker_id,
        jobs=container.job_store,
        artifacts=container.artifact_store,
        lease_seconds=container.worker_limits.lease_seconds,
        normalizer=container.geometry_normalizer,
        board_decoder=container.board_decoder,  # type: ignore[arg-type]
        # Self-limit adaptive runs below the orchestrator's hard timeout:
        # stopping at a pass boundary keeps every completed generation,
        # where the SIGKILL at the full limit keeps nothing.
        time_budget_seconds=container.worker_limits.max_job_seconds * 0.8,
    )


def make_worker_id() -> str:
    """A unique worker identity (exported for tests)."""
    return f"worker-{uuid.uuid4().hex[:12]}"
