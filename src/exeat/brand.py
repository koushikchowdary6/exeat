# SPDX-License-Identifier: MIT
"""Product name.

User-facing title, slug, state directory, and environment prefix live here.
The import package and the ``[project].name`` field in ``pyproject.toml`` use
the same slug. This project is not published to PyPI.
"""

from __future__ import annotations

VERSION = "0.1.0"
PRODUCT_SLUG = "exeat"
PRODUCT_TITLE = "Exeat"
PRODUCT_PITCH = "The permission slip for AI agents."

POLICY_FILENAME = "agent-policy.yaml"
MANIFEST_FILENAME = "tool-manifest.yaml"
STATE_DIRNAME = ".exeat"
AUDIT_FILENAME = "audit.jsonl"
APPROVAL_FILENAME = "approvals.jsonl"
LEDGER_FILENAME = "ledger.sqlite"
ENV_PREFIX = "EXEAT"

# Reserved argument names. The runtime classifies these even when a manifest
# tries to downgrade them to plain strings. Rename the argument if you need
# a different meaning (for example ``to_date`` instead of ``to``).
RESERVED_ARGUMENT_KINDS: dict[str, str] = {
    "path": "path",
    "file": "path",
    "filepath": "path",
    "file_path": "path",
    "url": "url",
    "uri": "url",
    "table": "table",
    "relation": "table",
    "to": "recipient",
    "cc": "recipient",
    "bcc": "recipient",
    "recipient": "recipient",
    "amount_usd": "spend",
}
