"""Parsing untrusted IPC-2581 XML.

An IPC-2581 file arrives from outside the trust boundary: a fabricator, a
customer, an email attachment. XML is an unusually generous attack surface, so
parsing goes through this module and never through `ElementTree.parse` (or an
`lxml` default parser) directly.

Defences, and the attack each one closes:

* **No DTD, no entity declarations.** Closes XXE (`<!ENTITY xxe SYSTEM
  "file:///etc/passwd">`, which reads local files) and entity-expansion bombs
  ("billion laughs", which expands a few hundred bytes into gigabytes). IPC-2581
  documents have no legitimate need for a DTD, so the whole class is refused
  rather than filtered.
* **No external references.** Nothing this parser touches may cause a file read
  or a network fetch beyond the document itself.
* **Bounded input size.** A document larger than the configured limit is
  refused before it is read into memory.
* **Bounded nesting depth.** Deeply nested XML exhausts the stack of any
  recursive consumer, including serialisers downstream of us.
* **Bounded element count.** Closes the "many small elements" variant of a
  decompression/expansion bomb, which no size limit alone catches.

Limits are enforced *during* parsing, so a hostile document is abandoned early
rather than after it has been fully materialised.

Numeric sanity -- absurd coordinates, NaN, values that would produce a
degenerate mesh -- is a semantic concern and belongs to the extraction layer,
not here. See `.agents/skills/ipc2581-import/SKILL.md`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from openpdn.pcb_import.api import MalformedSourceError

if TYPE_CHECKING:
    from pathlib import Path

#: Generous enough for a real board, small enough that a hostile file cannot
#: exhaust a container's memory before being rejected.
DEFAULT_MAX_BYTES: Final = 512 * 1024 * 1024
DEFAULT_MAX_DEPTH: Final = 100
DEFAULT_MAX_ELEMENTS: Final = 20_000_000

#: Bytes of the prolog inspected when sniffing a candidate document.
SNIFF_BYTES: Final = 8192

_DOCTYPE_PATTERN: Final = re.compile(rb"<!\s*DOCTYPE", re.IGNORECASE)
_ENTITY_PATTERN: Final = re.compile(rb"<!\s*ENTITY", re.IGNORECASE)


class UnsafeXmlError(MalformedSourceError):
    """The document uses an XML feature openPDN refuses to process.

    A subclass of `MalformedSourceError` so callers that only care that the
    import failed need no extra handling, while security tests can assert the
    specific cause.
    """


@dataclass(frozen=True, slots=True)
class XmlLimits:
    """Resource bounds applied while parsing an untrusted document."""

    max_bytes: int = DEFAULT_MAX_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH
    max_elements: int = DEFAULT_MAX_ELEMENTS


def parse_secure(source: Path, limits: XmlLimits | None = None) -> ElementTree.Element:
    """Parse `source` and return its root element.

    Raises:
        UnsafeXmlError: If the document declares a DTD or entities, or exceeds
            a size, depth or element-count limit.
        MalformedSourceError: If the document is not well-formed XML. The
            message never echoes document content.
    """
    applied = limits or XmlLimits()

    if not source.is_file():
        raise MalformedSourceError(f"Not a readable file: {source.name}")

    size_bytes = source.stat().st_size
    if size_bytes > applied.max_bytes:
        raise UnsafeXmlError(
            f"{source.name} is {size_bytes} bytes, above the {applied.max_bytes} byte limit"
        )

    _reject_doctype(source)

    depth = 0
    elements = 0
    root: ElementTree.Element | None = None
    try:
        # iterparse lets limits be enforced as the tree is built, so a hostile
        # document is abandoned mid-parse instead of after it has been
        # materialised in full.
        for event, element in ElementTree.iterparse(  # noqa: S314 - DTD/entities refused above
            source, events=("start", "end")
        ):
            if event == "start":
                depth += 1
                elements += 1
                if depth > applied.max_depth:
                    raise UnsafeXmlError(
                        f"{source.name} nests deeper than {applied.max_depth} elements"
                    )
                if elements > applied.max_elements:
                    raise UnsafeXmlError(
                        f"{source.name} holds more than {applied.max_elements} elements"
                    )
            else:
                depth -= 1
                if depth == 0:
                    root = element
    except ElementTree.ParseError as exc:
        # Deliberately reports the position, never the content at it.
        raise MalformedSourceError(
            f"{source.name} is not well-formed XML (line {exc.position[0]}, "
            f"column {exc.position[1]})"
        ) from exc

    if root is None:
        raise MalformedSourceError(f"{source.name} contains no XML root element")
    return root


def sniff_prolog(source: Path, max_bytes: int = SNIFF_BYTES) -> str:
    """Return a decoded prefix of `source` for cheap format detection.

    Bounded and lossy on purpose: this runs on files openPDN has not yet
    decided to trust, so it never reads the whole document and never fails on
    encoding.
    """
    try:
        with source.open("rb") as handle:
            return handle.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _reject_doctype(source: Path) -> None:
    """Refuse documents that declare a DTD or entities.

    Checked by scanning the prolog rather than by configuring the parser: it is
    a smaller thing to get right, and it rejects the document before any
    expansion machinery is reachable.
    """
    with source.open("rb") as handle:
        prolog = handle.read(SNIFF_BYTES)
    if _DOCTYPE_PATTERN.search(prolog):
        raise UnsafeXmlError(
            f"{source.name} declares a DOCTYPE. openPDN refuses DTDs: they enable "
            "external entity attacks and entity-expansion bombs, and IPC-2581 "
            "documents do not need one."
        )
    if _ENTITY_PATTERN.search(prolog):
        raise UnsafeXmlError(f"{source.name} declares XML entities, which openPDN refuses")
