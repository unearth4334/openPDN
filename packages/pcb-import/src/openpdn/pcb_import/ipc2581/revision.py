"""IPC-2581 revision handling.

The standard has been revised, and revisions move elements between parents,
add attributes and change defaults. Reading a document with the wrong
revision's rules does not usually fail -- it produces a board that looks
plausible and is quietly wrong. So the revision is determined once, here, and
an unsupported one is refused rather than guessed at.

Revision handling stays in this module. Application code, the domain and the
solver never learn that revisions exist; anything that needs to branch on one
does so inside the importer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from openpdn.pcb_import.api import MalformedSourceError, UnsupportedRevisionError


class IPC2581Revision(StrEnum):
    """A revision of the IPC-2581 standard."""

    A = "A"
    B = "B"
    C = "C"


#: Revisions this adapter can read. Revision B is the first implementation
#: target: it is the most widely generated revision and the one openPDN's
#: fixtures are written against. Adding a revision means adding its semantic
#: differences *and* fixtures, not just widening this set.
SUPPORTED_REVISIONS: Final[frozenset[IPC2581Revision]] = frozenset({IPC2581Revision.B})

#: XML namespace URIs, by revision. Generators are inconsistent about
#: namespacing, so the namespace is corroborating evidence, not the only
#: source of truth -- the `revision` attribute on the root element wins.
NAMESPACE_BY_REVISION: Final[dict[IPC2581Revision, str]] = {
    IPC2581Revision.A: "http://webstds.ipc.org/2581",
    IPC2581Revision.B: "http://webstds.ipc.org/2581",
    IPC2581Revision.C: "http://webstds.ipc.org/2581",
}

#: Root element local name of an IPC-2581 document.
ROOT_LOCAL_NAME: Final = "IPC-2581"


def detect_revision(revision_attribute: str | None) -> IPC2581Revision:
    """Map a document's `revision` attribute onto a known revision.

    Args:
        revision_attribute: Value of the root element's `revision` attribute.
            Generators write it variously as `"B"`, `"b"`, `"IPC-2581B"` or
            `"2581-B"`, so the value is normalised rather than compared raw.

    Raises:
        MalformedSourceError: If the attribute is missing or unrecognisable.
            An IPC-2581 document that does not say which revision it is cannot
            be read safely, and defaulting to one would be a guess.
        UnsupportedRevisionError: If the revision is recognised but this
            adapter does not implement its semantics.
    """
    if revision_attribute is None or not revision_attribute.strip():
        raise MalformedSourceError(
            "IPC-2581 document does not declare a revision; "
            f"expected a revision attribute on the <{ROOT_LOCAL_NAME}> element"
        )

    normalised = _normalise(revision_attribute)
    try:
        revision = IPC2581Revision(normalised)
    except ValueError as exc:
        known = ", ".join(sorted(item.value for item in IPC2581Revision))
        raise MalformedSourceError(
            f"Unrecognised IPC-2581 revision {revision_attribute!r}; known revisions: {known}"
        ) from exc

    if revision not in SUPPORTED_REVISIONS:
        supported = ", ".join(sorted(item.value for item in SUPPORTED_REVISIONS))
        raise UnsupportedRevisionError(
            f"IPC-2581 revision {revision.value} is not supported by this build "
            f"(supported: {supported}). Reading it with another revision's "
            "semantics would produce a plausible but incorrect board."
        )
    return revision


def _normalise(revision_attribute: str) -> str:
    """Reduce a generator-written revision string to a bare letter."""
    text = revision_attribute.strip().upper()
    # Strip the common prefixes before taking the trailing letter, so
    # "IPC-2581B", "2581-C" and "B" all reduce alike.
    for prefix in ("IPC-2581", "IPC2581", "2581-", "2581", "REV.", "REV", "-", " "):
        while text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text


def local_name(tag: str) -> str:
    """Return an XML tag without its `{namespace}` prefix.

    Namespacing varies between generators; matching on the local name keeps
    element lookup working for both namespaced and bare documents.
    """
    return tag.rpartition("}")[2]
