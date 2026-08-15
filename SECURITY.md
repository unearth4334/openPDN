# Security

## Reporting a vulnerability

Report privately through GitHub's **Report a vulnerability** button on the
Security tab of <https://github.com/unearth4334/openPDN>, or by opening a
minimal issue asking for a private channel. Please do not file public issues
with exploit detail, and never attach confidential fabrication data.

openPDN is pre-alpha. There is no support commitment yet; reports are still
welcome and will be acted on.

## Threat model

openPDN's central exposure is not authentication — it is **file input**. A PCB
document is untrusted, attacker-influenceable and deeply nested, and a user will
happily feed it to the tool because someone emailed it to them.

IPC-2581 is the reference interchange format (ADR-0006), so **XML is the
primary attack surface today**.

| Asset | Concern |
| --- | --- |
| Uploaded IPC-2581 XML | Entity expansion, external entity resolution (XXE), oversized input, excessive nesting, malformed or extreme numeric values, geometry designed to be pathological to process |
| Uploaded archives | Path traversal, decompression bombs, malicious member types |
| The host filesystem | Writes outside the intended workspace |
| External solver processes | Command injection, unbounded runtime |
| PCB geometry and net names | Confidential customer data leaking into logs or issues |
| Deployment credentials | Tokens reaching source control or logs |

## Rules the codebase enforces

### XML documents are untrusted

IPC-2581 files are parsed only through
`openpdn.pcb_import.ipc2581.secure_xml.parse_secure` — never
`ElementTree.parse`, `fromstring`, `minidom`, or an `lxml` default parser.

| Refused | Attack it closes |
| --- | --- |
| `<!DOCTYPE ...>` | **XXE**: `<!ENTITY x SYSTEM "file:///etc/passwd">` reads local files into the document |
| `<!ENTITY ...>` | **Entity-expansion bombs** ("billion laughs"): a few hundred bytes expanding to gigabytes |
| Documents over `max_bytes` | Memory exhaustion before parsing begins |
| Nesting over `max_depth` | Stack exhaustion in any recursive consumer downstream |
| More than `max_elements` | The "many small elements" bomb, which no byte limit catches |

DTDs are refused wholesale rather than filtered: IPC-2581 documents have no
legitimate need for one, and refusing the feature closes the whole class.
Limits are enforced *during* parsing, so a hostile document is abandoned early
rather than after being fully materialised. No external reference is ever
resolved — no `xi:include`, no schema fetch, no network access during import.

Covered by `tests/integration/test_ipc2581_importer.py`, whose payloads are
built in the test rather than committed as fixture files.

**Semantic hostility is handled during extraction**, not by the parser: NaN and
infinite values, coordinates outside a plausible board envelope, and geometry
whose vertex count would produce a pathological mesh are all rejected there. A
well-formed document can still describe a board designed to exhaust CPU or
memory in normalisation.

### Archives are untrusted

Zipped IPC-2581 packages, and ODB++ jobs when that importer lands, arrive as
archives. Never `ZipFile.extractall` or `TarFile.extractall`. Use
`openpdn.infrastructure.archives`, which rejects:

* absolute paths, drive letters and `..` traversal
  (`safe_relative_path` resolves and re-checks containment);
* symlinks, hard links, devices and FIFOs;
* members exceeding a per-member or total uncompressed size limit
  (decompression bombs), verified against what actually arrives rather than
  what the archive declares;
* archives with more members than the configured limit.

Covered by `tests/integration/test_archive_safety.py`. Those tests encode
attacks the tool must refuse; deleting one is a security regression.

### Uploaded filenames never choose a location

A filename is a display hint, not a path. `workspace.sanitise_label` strips
separators, control characters and traversal; uniqueness comes from a random
suffix, never from the supplied name. Work happens inside a
`TemporaryWorkspace` under the configured data directory, and it is removed
afterwards.

### External processes get argument lists, never shell strings

`infrastructure.process.run_tool` takes `argv` as a list, never uses
`shell=True`, requires a timeout, sets an explicit working directory, and
passes a minimal allow-listed environment so tokens and cloud metadata are not
inherited by a solver binary.

### Errors do not echo untrusted content

Parser failures report *that* a file was invalid, not what it contained. This
keeps hostile content out of logs, issues and screenshots.

### Bounded input

Uploads are capped by `OPENPDN_MAX_UPLOAD_BYTES` (256 MB by default); the
IPC-2581 parser applies its own size, depth and element-count limits, and the
canonical-JSON reader refuses documents beyond its own limit. Per-endpoint
request size and rate limits will be added with the first upload endpoint.

### Logs are not a data-exfiltration channel

Log counts, ids and durations — never full geometry, and never credentials. The
JSON formatter redacts fields whose names look secret as a backstop, not as a
licence to pass them.

### Secrets stay out of the repository

`.gitignore` covers `.env`, `.env.*` (except `.env.example`), `.deploy/`,
`.agents/private/`, and key material. CI authenticates to GHCR with the
workflow's own `GITHUB_TOKEN`; no registry credential exists in this
repository. Deployment hostnames and orchestrator endpoint ids are treated as
sensitive and live only in gitignored local notes.

## Container posture

The runtime image runs as a non-root user (uid 10001), contains no build tools
or test dependencies, and writes only to `/var/lib/openpdn` and
`/var/cache/openpdn`. The health check uses the bundled interpreter — there is
no shell in the probe path. Images are published with build provenance
attestation.

## Not yet implemented

Deliberately, with no pretence otherwise:

* **Authentication and authorisation.** openPDN currently assumes a trusted
  single-user deployment. Do not expose it to an untrusted network. Multi-user
  access needs a design, not a bolted-on login.
* **Per-request rate and size limits** beyond the global upload cap.
* **Sandboxing of external solver processes** (containers, seccomp, cgroups).
  Planned for when a backend actually runs third-party binaries.
* **Signed result artefacts.**

## For contributors

Before adding code that touches files, archives or subprocesses, read
`.agents/skills/development-conventions/SKILL.md`; before touching import code,
read `.agents/skills/ipc2581-import/SKILL.md`. If you find yourself writing
`extractall`, `ElementTree.parse`, `shell=True`, or a path built from user
input, stop — there is a helper for each, and a test that will fail if you skip
it.
