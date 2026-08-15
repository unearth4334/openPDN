"""Infrastructure adapters: the outermost backend layer.

Everything that talks to the operating system, the environment, third-party
libraries or external processes lives here -- configuration, logging, archive
extraction, subprocess execution, and the composition root that wires adapters
into application services.

Dependencies point inward: this package may import the domain, the contracts
and the application layer. None of them may import this package (ADR-0001).
"""

from openpdn.infrastructure.config import (
    LogFormat,
    LogLevel,
    Settings,
    configure_settings,
    get_settings,
    load_settings,
    reset_settings,
)
from openpdn.infrastructure.container import Container, build_container
from openpdn.infrastructure.logging import configure_logging

__all__ = [
    "Container",
    "LogFormat",
    "LogLevel",
    "Settings",
    "build_container",
    "configure_logging",
    "configure_settings",
    "get_settings",
    "load_settings",
    "reset_settings",
]
