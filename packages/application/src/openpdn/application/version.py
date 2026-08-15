"""Application identity.

One source of truth for the version, read from the installed distribution
metadata so that the wheel, the container image, `/api/health` and
`openpdn info` can never disagree.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from typing import Final

APPLICATION_NAME: Final = "openPDN"
DISTRIBUTION_NAME: Final = "openpdn"

#: HTTP API contract version. Bumped when a breaking change reaches `/api`.
API_VERSION: Final = "v0"

_FALLBACK_VERSION: Final = "0.0.0+unknown"


def get_version() -> str:
    """Return the installed openPDN version.

    Falls back to a clearly marked placeholder when running from a source tree
    that was never installed, rather than raising during a health check.
    """
    try:
        return distribution_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _FALLBACK_VERSION
