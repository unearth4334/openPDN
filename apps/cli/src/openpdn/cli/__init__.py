"""Command-line surface.

The CLI calls the same application services as the HTTP API. Anything it can do
that the API cannot -- or that it computes for itself -- is a layering defect
(ADR-0001).
"""

from openpdn.cli.main import main

__all__ = ["main"]
