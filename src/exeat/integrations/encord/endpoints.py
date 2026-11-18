# SPDX-License-Identifier: MIT
"""Public Merlin MCP URLs. Exeat does not speak for Encord."""

from __future__ import annotations

from exeat.errors import ExeatError

MERLIN_MCP_URLS = {
    "eu": "https://mcp.encord.com/mcp",
    "us": "https://mcp.us.encord.com/mcp",
}


def merlin_mcp_url(region: str) -> str:
    try:
        return MERLIN_MCP_URLS[region]
    except KeyError as exc:
        raise ExeatError("Merlin region must be eu or us") from exc
