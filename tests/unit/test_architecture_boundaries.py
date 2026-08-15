"""Executable enforcement of the layering rules.

The dependency direction in ADR-0001 is worth exactly as much as its
enforcement. This test parses every source file, extracts its imports, and
fails on any edge that points the wrong way -- so a well-meaning
`from openpdn.solver.mock import MockSolver` inside an application service
breaks the build instead of quietly becoming precedent.

Adding a layer or an adapter means adding a row to `LAYER_RULES`. Relaxing a
row is an architectural change and needs an ADR.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE_ROOTS = (
    REPO_ROOT / "packages" / "domain" / "src",
    REPO_ROOT / "packages" / "solver-api" / "src",
    REPO_ROOT / "packages" / "pcb-import" / "src",
    REPO_ROOT / "packages" / "geometry" / "src",
    REPO_ROOT / "packages" / "application" / "src",
    REPO_ROOT / "packages" / "solver-mock" / "src",
    REPO_ROOT / "packages" / "infrastructure" / "src",
    REPO_ROOT / "apps" / "api" / "src",
    REPO_ROOT / "apps" / "cli" / "src",
)


@dataclass(frozen=True)
class LayerRule:
    """What one layer is allowed to depend on.

    Attributes:
        name: Human-readable layer name used in failure messages.
        prefix: Module prefix owning this layer. Longest match wins, so
            `openpdn.pcb_import.api` is classified before `openpdn.pcb_import`.
        allowed_internal: openPDN module prefixes this layer may import.
        allow_third_party: Whether non-stdlib imports are permitted at all.
        allowed_third_party: Exceptions when `allow_third_party` is False.
    """

    name: str
    prefix: str
    allowed_internal: tuple[str, ...]
    allow_third_party: bool
    allowed_third_party: frozenset[str] = field(default_factory=frozenset)


#: Ordered from innermost to outermost. Each layer may only reach inward.
LAYER_RULES: tuple[LayerRule, ...] = (
    LayerRule(
        name="domain",
        prefix="openpdn.domain",
        allowed_internal=("openpdn.domain",),
        allow_third_party=False,
    ),
    LayerRule(
        name="solver contract",
        prefix="openpdn.solver.api",
        allowed_internal=("openpdn.domain", "openpdn.solver.api"),
        allow_third_party=False,
    ),
    LayerRule(
        name="importer contract",
        prefix="openpdn.pcb_import.api",
        allowed_internal=("openpdn.domain", "openpdn.pcb_import.api"),
        allow_third_party=False,
    ),
    LayerRule(
        # The package __init__ re-exports the contract and nothing else, so it
        # is classified with the contract rather than with the adapters.
        name="importer package root",
        prefix="openpdn.pcb_import.__init__",
        allowed_internal=("openpdn.domain", "openpdn.pcb_import.api"),
        allow_third_party=False,
    ),
    LayerRule(
        # Geometry normalisation contract (ADR-0007): application services and
        # solvers consume normalised copper through this, never through the
        # concrete Shapely engine.
        name="geometry contract",
        prefix="openpdn.geometry.api",
        allowed_internal=("openpdn.domain", "openpdn.geometry.api"),
        allow_third_party=False,
    ),
    LayerRule(
        # The package __init__ re-exports the contract and nothing else.
        name="geometry package root",
        prefix="openpdn.geometry.__init__",
        allowed_internal=("openpdn.domain", "openpdn.geometry.api"),
        allow_third_party=False,
    ),
    LayerRule(
        name="application",
        prefix="openpdn.application",
        allowed_internal=(
            "openpdn.domain",
            "openpdn.application",
            "openpdn.solver.api",
            "openpdn.pcb_import.api",
            "openpdn.geometry.api",
        ),
        allow_third_party=False,
    ),
    LayerRule(
        name="importer adapter",
        prefix="openpdn.pcb_import",
        allowed_internal=("openpdn.domain", "openpdn.pcb_import"),
        allow_third_party=True,
    ),
    LayerRule(
        name="geometry adapter",
        prefix="openpdn.geometry",
        allowed_internal=("openpdn.domain", "openpdn.geometry"),
        allow_third_party=True,
    ),
    LayerRule(
        name="solver adapter",
        prefix="openpdn.solver",
        allowed_internal=("openpdn.domain", "openpdn.solver"),
        allow_third_party=True,
    ),
    LayerRule(
        name="infrastructure",
        prefix="openpdn.infrastructure",
        allowed_internal=(
            "openpdn.domain",
            "openpdn.application",
            "openpdn.solver",
            "openpdn.pcb_import",
            "openpdn.geometry",
            "openpdn.infrastructure",
        ),
        allow_third_party=True,
    ),
    LayerRule(
        name="http api",
        prefix="openpdn.api",
        allowed_internal=(
            "openpdn.domain",
            "openpdn.application",
            "openpdn.solver.api",
            "openpdn.pcb_import.api",
            "openpdn.geometry.api",
            "openpdn.infrastructure",
            "openpdn.api",
        ),
        allow_third_party=True,
    ),
    LayerRule(
        name="cli",
        prefix="openpdn.cli",
        allowed_internal=(
            "openpdn.domain",
            "openpdn.application",
            "openpdn.solver.api",
            "openpdn.pcb_import.api",
            "openpdn.geometry.api",
            "openpdn.infrastructure",
            "openpdn.cli",
        ),
        allow_third_party=True,
    ),
)

#: Technologies that must never appear in an inner layer, called out by name so
#: the failure message explains itself. See `.agents/skills/architecture`.
FORBIDDEN_IN_PURE_LAYERS = (
    # Interchange formats and their parsers terminate at the importer boundary.
    "ipc2581",
    "xml",
    "lxml",
    "defusedxml",
    "odbdesign",
    "padne",
    "fypa",
    "elmer",
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "starlette",
    "uvicorn",
    "sqlalchemy",
    "psycopg",
)


@dataclass(frozen=True)
class ModuleImports:
    """One source module and the modules it imports."""

    module: str
    path: Path
    imports: tuple[str, ...]


def _module_name(path: Path, root: Path) -> str:
    """Return the dotted module name of `path` relative to source `root`."""
    relative = path.relative_to(root).with_suffix("")
    return ".".join(relative.parts)


def _imports_of(tree: ast.AST) -> tuple[str, ...]:
    """Return every absolute module imported by `tree`, including under TYPE_CHECKING.

    Type-only imports count: importing a solver's types still couples the
    caller to that solver's shape, and `if TYPE_CHECKING` is not a loophole.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return tuple(found)


def _collect() -> list[ModuleImports]:
    """Parse every openPDN source module."""
    collected: list[ModuleImports] = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            collected.append(
                ModuleImports(
                    module=_module_name(path, root),
                    path=path,
                    imports=_imports_of(tree),
                )
            )
    return collected


def _rule_for(module: str) -> LayerRule | None:
    """Return the most specific rule matching `module`."""
    matches = [
        rule
        for rule in LAYER_RULES
        if module == rule.prefix or module.startswith(f"{rule.prefix}.")
    ]
    if not matches:
        return None
    return max(matches, key=lambda rule: len(rule.prefix))


def _is_stdlib(module: str) -> bool:
    """True when `module`'s top-level package ships with Python."""
    return module.split(".")[0] in sys.stdlib_module_names


MODULES = _collect()


def test_sources_were_found() -> None:
    """Guard against the test silently passing because it scanned nothing."""
    assert len(MODULES) > 20, "Expected to scan the whole backend source tree"


def test_every_module_is_classified() -> None:
    """Every module belongs to a declared layer.

    A new top-level package must state where it sits before it can be merged.
    """
    unclassified = [module.module for module in MODULES if _rule_for(module.module) is None]
    assert not unclassified, (
        "These modules are not covered by LAYER_RULES; add a rule declaring "
        f"what they may depend on: {unclassified}"
    )


def test_internal_dependencies_point_inward() -> None:
    """No openPDN module imports a layer it is not allowed to know about."""
    violations: list[str] = []
    for module in MODULES:
        rule = _rule_for(module.module)
        if rule is None:
            continue
        for imported in module.imports:
            if not imported.startswith("openpdn"):
                continue
            if any(
                imported == allowed or imported.startswith(f"{allowed}.")
                for allowed in rule.allowed_internal
            ):
                continue
            violations.append(
                f"{module.path.relative_to(REPO_ROOT)} ({rule.name}) imports {imported!r}; "
                f"allowed: {', '.join(rule.allowed_internal)}"
            )
    assert not violations, "Forbidden dependency direction:\n  " + "\n  ".join(violations)


def test_pure_layers_have_no_third_party_dependencies() -> None:
    """The domain, the contracts and the application layer stay framework-free.

    This is what lets the domain be unit-tested with nothing installed, and
    what stops a framework's data model from becoming the engineering model.
    """
    violations: list[str] = []
    for module in MODULES:
        rule = _rule_for(module.module)
        if rule is None or rule.allow_third_party:
            continue
        for imported in module.imports:
            top_level = imported.split(".")[0]
            if top_level == "openpdn" or _is_stdlib(imported):
                continue
            if top_level in rule.allowed_third_party:
                continue
            violations.append(
                f"{module.path.relative_to(REPO_ROOT)} ({rule.name}) imports "
                f"third-party module {imported!r}"
            )
    assert not violations, "Third-party dependency in a pure layer:\n  " + "\n  ".join(violations)


@pytest.mark.parametrize("technology", FORBIDDEN_IN_PURE_LAYERS)
def test_named_technologies_stay_out_of_pure_layers(technology: str) -> None:
    """Spell out the specific couplings the project refuses to accept."""
    offenders = [
        str(module.path.relative_to(REPO_ROOT))
        for module in MODULES
        if (rule := _rule_for(module.module)) is not None
        and not rule.allow_third_party
        and any(imported.split(".")[0] == technology for imported in module.imports)
    ]
    assert not offenders, (
        f"{technology!r} must not be imported by the domain, the contracts or the "
        f"application layer: {offenders}"
    )


def test_applications_do_not_import_each_other() -> None:
    """The CLI and the HTTP API stay independent surfaces."""
    violations = [
        f"{module.module} imports {imported}"
        for module in MODULES
        for imported in module.imports
        if (module.module.startswith("openpdn.api") and imported.startswith("openpdn.cli"))
        or (module.module.startswith("openpdn.cli") and imported.startswith("openpdn.api"))
    ]
    assert not violations, (
        "Surfaces must share application services, not each other:\n  " + "\n  ".join(violations)
    )


def test_only_the_composition_root_knows_concrete_adapters() -> None:
    """Concrete adapters are named in exactly one place.

    Everything else resolves them through a registry, which is what makes
    adding a solver a one-file change.
    """
    concrete = (
        "openpdn.solver.mock",
        "openpdn.pcb_import.canonical_json",
        "openpdn.pcb_import.ipc2581",
        "openpdn.geometry.shapely_engine",
    )
    allowed_importers = {"openpdn.infrastructure.container"}
    violations = [
        f"{module.module} imports {imported}"
        for module in MODULES
        for imported in module.imports
        if any(imported.startswith(name) for name in concrete)
        and module.module not in allowed_importers
        and not any(module.module.startswith(f"{name}") for name in concrete)
    ]
    assert not violations, (
        "Concrete adapters may only be named in the composition root "
        f"({', '.join(sorted(allowed_importers))}):\n  " + "\n  ".join(violations)
    )
