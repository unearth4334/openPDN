"""Domain-level error types.

Adapters translate their own failures (malformed IPC-2581 document, solver
crash, HTTP error) into these -- or into their own adapter errors -- so that
application services never have to catch third-party exception types.
"""


class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class InvalidQuantityError(DomainError):
    """A physical quantity was constructed with a nonsensical value or unit."""


class InvalidGeometryError(DomainError):
    """A geometric primitive was constructed from degenerate input."""


class InvalidBoardError(DomainError):
    """The canonical board model is internally inconsistent."""


class InvalidStudyError(DomainError):
    """An analysis study is internally inconsistent, or inconsistent with a board."""


class MissingPhysicalPropertyError(DomainError):
    """A physical property required for analysis is unknown.

    Raised instead of silently substituting a plausible-looking number.
    Callers that *want* a fallback must supply it explicitly, as a
    `Quantity` carrying `Provenance.ASSUMED`.
    """
