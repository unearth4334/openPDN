"""Errors raised by application services.

Adapters raise their own error types (`PCBImportError`, `SolverError`);
services translate them where a use case needs a single, surface-agnostic
failure mode. The HTTP API maps these to status codes, the CLI to exit codes --
neither knows about adapter exceptions.
"""


class ApplicationError(Exception):
    """Base class for use-case level failures."""


class ImportRequestError(ApplicationError):
    """The requested import cannot be performed as asked.

    Covers an unknown importer name and a source no registered importer
    recognises -- both user errors, not internal faults.
    """


class BoardNotFoundError(ApplicationError):
    """The requested board id is not in the workspace.

    Distinct from `ImportRequestError` so the HTTP layer can answer 404 rather
    than 400: the request was well-formed, the resource just is not there.
    """


class AnalysisRequestError(ApplicationError):
    """The requested analysis cannot be performed as asked.

    Covers an unknown solver name and a study asking for physics the chosen
    backend does not support.
    """
