"""`openpdn` command-line entry point.

Argument parsing, rendering and exit codes only. Every command resolves to an
application service call on a container built exactly the way the API builds
one, so `openpdn info` and `GET /api/info` cannot disagree.

Exit codes:
    0  success
    1  the request failed (bad input, unavailable adapter)
    2  usage error (argparse)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from openpdn.application.errors import ApplicationError
from openpdn.application.version import APPLICATION_NAME, get_version
from openpdn.infrastructure.config import LogFormat, LogLevel, load_settings
from openpdn.infrastructure.container import Container, build_container
from openpdn.infrastructure.logging import configure_logging
from openpdn.pcb_import.api import PCBImportError
from openpdn.solver.api import SolverError

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_OK: Final = 0
EXIT_FAILURE: Final = 1


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="openpdn",
        description=f"{APPLICATION_NAME}: DC conduction analysis for printed circuit boards.",
    )
    parser.add_argument("--version", action="version", version=get_version())
    parser.add_argument(
        "--log-level",
        choices=[level.value for level in LogLevel],
        help="Override OPENPDN_LOG_LEVEL for this invocation.",
    )
    parser.add_argument(
        "--log-format",
        choices=[fmt.value for fmt in LogFormat],
        help="Override OPENPDN_LOG_FORMAT for this invocation.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("info", help="Describe this deployment and its capabilities.")
    subparsers.add_parser("solvers", help="List the electrical solvers available here.")
    subparsers.add_parser("importers", help="List the PCB importers available here.")

    import_parser = subparsers.add_parser(
        "import", help="Import a PCB source into the canonical board model."
    )
    import_parser.add_argument("source", type=Path, help="Path to the PCB source file.")
    import_parser.add_argument(
        "--importer",
        dest="importer_name",
        help=(
            "Force a specific importer. Omit it: openPDN identifies the format from "
            "the document itself."
        ),
    )

    inspect_parser = subparsers.add_parser(
        "inspect", help="Import a PCB source and print a structural review summary."
    )
    inspect_parser.add_argument("source", type=Path, help="Path to the PCB source file.")
    inspect_parser.add_argument(
        "--importer", dest="importer_name", help="Force a specific importer (default: detect)."
    )

    validate_parser = subparsers.add_parser(
        "validate-import",
        help=(
            "Import a PCB source and check it against expectations. Built for local "
            "verification of private fixtures; expectations are flags, never code."
        ),
    )
    validate_parser.add_argument("source", type=Path, help="Path to the PCB source file.")
    validate_parser.add_argument(
        "--importer", dest="importer_name", help="Force a specific importer (default: detect)."
    )
    validate_parser.add_argument(
        "--expect-conductive-layers", type=int, help="Required conductive layer count."
    )
    validate_parser.add_argument("--expect-vias", type=int, help="Required via count.")
    validate_parser.add_argument("--expect-nets", type=int, help="Required named-net count.")
    validate_parser.add_argument("--expect-components", type=int, help="Required component count.")

    serve_parser = subparsers.add_parser("serve", help="Run the HTTP API.")
    serve_parser.add_argument("--host", help="Bind address (default: OPENPDN_API_HOST).")
    serve_parser.add_argument("--port", type=int, help="Bind port (default: OPENPDN_API_PORT).")
    serve_parser.add_argument(
        "--reload", action="store_true", help="Reload on source changes (development only)."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Explicit flags sit at the top of the configuration hierarchy.
    overrides: dict[str, Any] = {}
    if args.log_level:
        overrides["log_level"] = args.log_level
    if args.log_format:
        overrides["log_format"] = args.log_format
    if args.command == "serve":
        if args.host:
            overrides["api_host"] = args.host
        if args.port:
            overrides["api_port"] = args.port

    settings = load_settings(**overrides)
    configure_logging(settings.log_level, settings.log_format)
    container = build_container(settings)

    try:
        return _dispatch(args, container)
    except (ApplicationError, PCBImportError, SolverError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


def _dispatch(args: argparse.Namespace, container: Container) -> int:
    """Route a parsed command to its handler."""
    if args.command == "info":
        return _command_info(container, as_json=args.json)
    if args.command == "solvers":
        return _command_solvers(container, as_json=args.json)
    if args.command == "importers":
        return _command_importers(container, as_json=args.json)
    if args.command == "import":
        return _command_import(container, args.source, args.importer_name, as_json=args.json)
    if args.command == "inspect":
        return _command_inspect(container, args.source, args.importer_name)
    if args.command == "validate-import":
        return _command_validate_import(container, args)
    if args.command == "serve":
        return _command_serve(container, reload=args.reload)
    raise AssertionError(f"Unhandled command {args.command!r}")  # pragma: no cover


def _command_info(container: Container, *, as_json: bool) -> int:
    """Print the deployment description."""
    info = container.info_service.describe()
    if as_json:
        _print_json(
            {
                "name": info.name,
                "version": info.version,
                "api_version": info.api_version,
                "environment": info.environment,
                "solvers": [solver.name for solver in info.solvers],
                "importers": [importer.name for importer in info.importers],
                "capabilities": {
                    capability.name: str(capability.status) for capability in info.capabilities
                },
            }
        )
        return EXIT_OK

    print(f"{info.name} {info.version}  (API {info.api_version}, {info.environment})")
    print(f"  default solver : {container.settings.solver}")
    print(f"  data dir       : {container.settings.data_dir}")
    print(f"  cache dir      : {container.settings.cache_dir}")
    print("\nCapabilities:")
    for capability in info.capabilities:
        print(f"  [{capability.status!s:<12}] {capability.name}")
    return EXIT_OK


def _command_solvers(container: Container, *, as_json: bool) -> int:
    """List registered solvers."""
    descriptors = container.solvers.available()
    if as_json:
        _print_json(
            [
                {
                    "name": descriptor.name,
                    "version": descriptor.version,
                    "fidelity": str(descriptor.capabilities.fidelity),
                    "available": descriptor.available,
                }
                for descriptor in descriptors
            ]
        )
        return EXIT_OK
    for descriptor in descriptors:
        marker = " " if descriptor.available else "!"
        print(
            f"{marker} {descriptor.name:<16} {descriptor.version:<8} "
            f"{descriptor.capabilities.fidelity!s:<12} {descriptor.summary}"
        )
    return EXIT_OK


def _command_importers(container: Container, *, as_json: bool) -> int:
    """List registered importers."""
    descriptors = container.importers.available()
    if as_json:
        _print_json(
            [
                {
                    "name": descriptor.name,
                    "version": descriptor.version,
                    "source_format": descriptor.source_format,
                    "available": descriptor.available,
                    "unavailable_reason": descriptor.unavailable_reason,
                }
                for descriptor in descriptors
            ]
        )
        return EXIT_OK
    for descriptor in descriptors:
        marker = " " if descriptor.available else "!"
        print(
            f"{marker} {descriptor.name:<16} {descriptor.version:<8} "
            f"{descriptor.source_format:<28} {descriptor.summary}"
        )
        if not descriptor.available and descriptor.unavailable_reason:
            # An unusable importer has to say why, or a user is left guessing
            # whether their file is unsupported or openPDN is unfinished.
            print(f"{'':<19}unavailable: {descriptor.unavailable_reason}")
    return EXIT_OK


def _command_import(
    container: Container,
    source: Path,
    importer_name: str | None,
    *,
    as_json: bool,
) -> int:
    """Import a board and report what came back."""
    result = container.import_service.import_board(source, importer_name)
    board = result.board
    if as_json:
        _print_json(
            {
                "board_id": str(board.id),
                "name": board.name,
                "layers": len(board.stackup.layers),
                "nets": len(board.nets),
                "copper_regions": len(board.copper_regions),
                "vias": len(board.vias),
                "terminals": len(board.terminals),
                "diagnostics": [
                    {
                        "code": diagnostic.code,
                        "severity": str(diagnostic.severity),
                        "message": diagnostic.message,
                    }
                    for diagnostic in result.diagnostics
                ],
            }
        )
        return EXIT_OK

    print(f"board {board.id}  {board.name!r}")
    print(f"  layers         : {len(board.stackup.layers)}")
    print(f"  nets           : {len(board.nets)}")
    print(f"  copper regions : {len(board.copper_regions)}")
    print(f"  vias           : {len(board.vias)}")
    print(f"  terminals      : {len(board.terminals)}")
    if result.diagnostics:
        print("\nDiagnostics:")
        for diagnostic in result.diagnostics:
            print(f"  [{diagnostic.severity!s:<7}] {diagnostic.code}: {diagnostic.message}")
    return EXIT_OK


def _command_inspect(container: Container, source: Path, importer_name: str | None) -> int:
    """Import a source and print the structural review summary."""
    from openpdn.cli.review_commands import render_inspection

    review = container.review_service.import_and_review(source, importer_name)
    print(render_inspection(review))
    return EXIT_OK


def _command_validate_import(container: Container, args: argparse.Namespace) -> int:
    """Import a source and check it against developer expectations."""
    from openpdn.cli.review_commands import run_validation

    review = container.review_service.import_and_review(args.source, args.importer_name)
    report, passed = run_validation(
        review,
        expect_conductive_layers=args.expect_conductive_layers,
        expect_vias=args.expect_vias,
        expect_nets=args.expect_nets,
        expect_components=args.expect_components,
    )
    print(report)
    return EXIT_OK if passed else EXIT_FAILURE


def _command_serve(container: Container, *, reload: bool) -> int:
    """Run the HTTP API with uvicorn."""
    import uvicorn

    settings = container.settings
    print(
        f"{APPLICATION_NAME} {get_version()} listening on "
        f"http://{settings.api_host}:{settings.api_port}/api/health",
        file=sys.stderr,
    )
    uvicorn.run(
        "openpdn.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
        log_config=None,  # openPDN configures logging itself.
    )
    return EXIT_OK


def _print_json(payload: object) -> None:
    """Print `payload` as indented JSON, ready to pipe into jq."""
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
