"""The gate between "a solver exists" and "a solver may be trusted".

openPDN's central honesty rule is that it does not claim analyses it has not
validated. This test enforces it mechanically: any registered solver that
advertises real physics must appear in `VALIDATED_SOLVERS`, and a solver only
belongs there once it passes validation cases against `analytical.py`.

Adding a backend therefore fails the build until its validation cases exist.
That is the intent.
"""

from __future__ import annotations

import pytest

from openpdn.domain.results import ResultFidelity
from openpdn.infrastructure.container import Container

pytestmark = pytest.mark.validation

#: Solvers whose numerical results have been checked against closed-form
#: references. Add a name here only together with the validation cases that
#: earn it, and record the tolerances those cases use.
VALIDATED_SOLVERS: frozenset[str] = frozenset()


def test_every_physical_solver_has_been_validated(container: Container) -> None:
    """A backend claiming real physics must have validation cases."""
    unvalidated = [
        descriptor.name
        for descriptor in container.solvers.available()
        if descriptor.capabilities.fidelity.is_physical and descriptor.name not in VALIDATED_SOLVERS
    ]
    assert not unvalidated, (
        "These solvers advertise physical results but have no validation cases: "
        f"{unvalidated}. Add cases under tests/validation/ comparing them against "
        "tests/validation/analytical.py, then list them in VALIDATED_SOLVERS."
    )


def test_mock_results_are_excluded_from_validation(container: Container) -> None:
    """The mock backend must stay outside the validated set."""
    mock = next(
        descriptor for descriptor in container.solvers.available() if descriptor.name == "mock"
    )
    assert mock.capabilities.fidelity is ResultFidelity.MOCK
    assert "mock" not in VALIDATED_SOLVERS


def test_the_capability_table_does_not_claim_unvalidated_analyses(
    container: Container,
) -> None:
    """`/api/info` must not report an analysis as implemented before it is validated."""
    analysis_capabilities = {
        "IR-drop analysis",
        "Current-density analysis",
        "Via current",
        "Terminal-to-terminal resistance",
        "Resistive power-loss mapping",
    }
    overclaimed = [
        capability.name
        for capability in container.info_service.describe().capabilities
        if capability.name in analysis_capabilities
        and str(capability.status) == "implemented"
        and not VALIDATED_SOLVERS
    ]
    assert not overclaimed, (
        f"Capabilities claimed as implemented with no validated solver: {overclaimed}"
    )
