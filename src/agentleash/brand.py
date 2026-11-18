# SPDX-License-Identifier: Apache-2.0
"""Working name for this project.

The name has not been checked for trademark or package conflicts, and this
project is not published to PyPI. User-facing strings read these constants.

To rename the project, change this file first, then:
- the ``[project].name`` field and the console script in ``pyproject.toml``
- the ``src/agentleash`` package directory (and its imports)
"""

from __future__ import annotations

VERSION = "0.1.0"
PRODUCT_SLUG = "agentleash"
PRODUCT_TITLE = "Agentleash"
PRODUCT_PITCH = "An org chart for what an AI agent is allowed to spend, send, and open."

POLICY_FILENAME = "agent-policy.yaml"
MANIFEST_FILENAME = "tool-manifest.yaml"
STATE_DIRNAME = ".agentleash"
AUDIT_FILENAME = "audit.jsonl"
APPROVAL_FILENAME = "approvals.jsonl"
ENV_PREFIX = "AGENTLEASH"

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
