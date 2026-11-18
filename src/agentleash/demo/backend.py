# SPDX-License-Identifier: Apache-2.0
"""In-process finance tools. They never open a socket or send mail."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agentleash.demo.world import INVOICE, LEDGER_URL, TABLES
from agentleash.errors import ToolError
from agentleash.policy.check import normalize_repo_path
from agentleash.policy.globmatch import glob_match
from agentleash.policy.protected import PROTECTED_FILE_GLOBS


class FinanceBackend:
    """Simulated ledger, files, and mail for the offline demo."""

    def __init__(self, sandbox: Path) -> None:
        self.sandbox = sandbox

    def supports(self, name: str) -> bool:
        return name in {
            "ledger.read",
            "ledger.query",
            "ledger.transfer",
            "email.send",
            "files.read",
            "http.get",
        }

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "ledger.read":
            return self._read_invoice(str(arguments["invoice_id"]), str(arguments["path"]))
        if name == "ledger.query":
            return self._query(str(arguments["table"]), arguments.get("where"))
        if name == "ledger.transfer":
            return _transfer(arguments)
        if name == "email.send":
            return _email(arguments)
        if name == "files.read":
            return self._read_file(str(arguments["path"]))
        if name == "http.get":
            return _http_get(str(arguments["url"]))
        raise ToolError(f"builtin backend does not implement {name}")

    def _read_invoice(self, invoice_id: str, path: str) -> dict[str, Any]:
        payload = self._read_file(path)
        document = payload.get("document")
        if not isinstance(document, dict) or document.get("id") != invoice_id:
            raise ToolError(f"no invoice {invoice_id} at {path}")
        return {"invoice": document}

    def _query(self, table: str, where: object) -> dict[str, Any]:
        rows = TABLES.get(table)
        if rows is None:
            raise ToolError(f"no synthetic table {table}")
        return {"table": table, "where": where or "", "rows": list(rows)}

    def _read_file(self, path: str) -> dict[str, Any]:
        candidate = self._resolve(path)
        if not candidate.is_file():
            raise ToolError(f"file not found: {path}")
        text = candidate.read_text(encoding="utf-8")
        if len(text) > 65536:
            text = text[:65536] + "\n...[truncated]"
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = None
        return {"path": path, "text": text, "document": document}

    def _resolve(self, path: str) -> Path:
        normalized, error = normalize_repo_path(path)
        if error or normalized is None:
            raise ToolError(f"refusing path {path}: {error}")
        for pattern in PROTECTED_FILE_GLOBS:
            if glob_match(pattern, normalized):
                raise ToolError(f"refusing protected path {normalized}")
        root = self.sandbox.resolve()
        candidate = (root / normalized).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ToolError("path escapes the sandbox") from exc
        return candidate


def _transfer(arguments: dict[str, Any]) -> dict[str, Any]:
    amount = arguments.get("amount_usd")
    vendor = str(arguments.get("vendor_id", ""))
    return {
        "status": "settled",
        "confirmation": f"NW-{vendor}-{amount}",
        "vendor_id": vendor,
        "amount_usd": amount,
        "memo": arguments.get("memo", ""),
    }


def _email(arguments: dict[str, Any]) -> dict[str, Any]:
    recipient = str(arguments.get("to", ""))
    subject = str(arguments.get("subject", ""))
    digest = hashlib.sha256(f"{recipient}|{subject}".encode()).hexdigest()[:8]
    return {"status": "queued", "id": f"msg_{digest}", "to": recipient, "subject": subject}


def _http_get(url: str) -> dict[str, Any]:
    if url.split("?", 1)[0].rstrip("/") != LEDGER_URL:
        raise ToolError("builtin http.get does not use the network and has no canned response for this URL")
    return {"url": url, "status": 200, "body": {"invoice": INVOICE}}
