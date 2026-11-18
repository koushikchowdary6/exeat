# SPDX-License-Identifier: Apache-2.0
"""Argument checks shared by the evaluator. Each one returns a reason or None."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from agentleash.errors import PolicyError
from agentleash.money import parse_cents
from agentleash.policy.globmatch import glob_match
from agentleash.policy.model import Effective
from agentleash.policy.protected import PROTECTED_FILE_GLOBS

_EMAIL = re.compile(r"^([^@\s]+)@([^@\s]+)$")


@dataclass(frozen=True)
class Classified:
    name: str
    kind: str
    value: Any


def normalize_repo_path(value: str) -> tuple[str | None, str | None]:
    """Return ``(relative_posix_path, error)``. ``..`` cannot escape the root."""

    if "\x00" in value or "\\" in value:
        return None, "it contains a forbidden character"
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        return None, "absolute paths are denied"
    parts: list[str] = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None, "it escapes the scope root"
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None, "it is empty"
    return "/".join(parts), None


def path_problem(item: Classified, effective: Effective) -> tuple[str, str] | None:
    if not isinstance(item.value, str) or not item.value.strip():
        return ("args.invalid", f"Denied: argument {item.name} must be a path string.")
    raw = item.value.strip()
    normalized, error = normalize_repo_path(raw)
    if error or normalized is None:
        return ("path.invalid", f"Denied: path {raw} is not usable ({error}).")
    shown = raw if raw == normalized else f"{raw} (normalizes to {normalized})"
    for pattern in PROTECTED_FILE_GLOBS:
        if glob_match(pattern, normalized):
            return (
                "path.protected",
                f"Denied: path {shown} matches protected pattern {pattern} and cannot be granted.",
            )
    for pattern in effective.files.deny:
        if glob_match(pattern, normalized):
            return (
                "path.denied",
                f"Denied: path {normalized} matches deny pattern {pattern}.",
            )
    if not effective.files.allow:
        return ("path.out_of_scope", "Denied: no file scope is granted (deny by default).")
    if not any(glob_match(pattern, normalized) for pattern in effective.files.allow):
        allowed = ", ".join(effective.files.allow)
        return (
            "path.out_of_scope",
            f"Denied: path {normalized} is outside the file scope. Allowed: {allowed}.",
        )
    return None


def url_problem(item: Classified, effective: Effective) -> tuple[str, str] | None:
    if not isinstance(item.value, str) or not item.value.strip():
        return ("args.invalid", f"Denied: argument {item.name} must be a URL string.")
    raw = item.value.strip()
    parsed = _split_url(raw)
    if isinstance(parsed, str):
        return ("url.invalid", f"Denied: {parsed}.")
    scheme, host, port, path = parsed
    for pattern in effective.urls.deny:
        if _url_match(pattern, scheme, host, port, path):
            return ("url.denied", f"Denied: url {raw} matches deny pattern {pattern}.")
    if not effective.urls.allow:
        return ("url.out_of_scope", "Denied: no URL scope is granted (deny by default).")
    if not any(_url_match(pattern, scheme, host, port, path) for pattern in effective.urls.allow):
        allowed = ", ".join(effective.urls.allow)
        return (
            "url.out_of_scope",
            f"Denied: url {raw} is not in scope. Allowed URLs: {allowed}.",
        )
    return None


def table_problem(item: Classified, effective: Effective) -> tuple[str, str] | None:
    if not isinstance(item.value, str) or not item.value.strip():
        return ("args.invalid", f"Denied: argument {item.name} must be a table name.")
    name = item.value.strip()
    if "/" in name or "\\" in name or ".." in name:
        return ("table.invalid", f"Denied: table {name} is not a bare table name.")
    for pattern in effective.tables.deny:
        if glob_match(pattern, name):
            return ("table.denied", f"Denied: table {name} matches deny pattern {pattern}.")
    if not effective.tables.allow or not any(glob_match(pattern, name) for pattern in effective.tables.allow):
        allowed = ", ".join(effective.tables.allow) or "none"
        return (
            "table.out_of_scope",
            f"Denied: table {name} is not in scope. Allowed tables: {allowed}.",
        )
    return None


def recipient_problem(item: Classified, effective: Effective) -> tuple[str, str] | None:
    parsed = parse_email(item.value)
    if parsed is None:
        return (
            "recipient.invalid",
            f"Denied: argument {item.name} must be a single email address.",
        )
    local, domain = parsed
    full = f"{local}@{domain}"
    if any(domain_matches(pattern, domain) for pattern in effective.recipient_domains):
        return None
    if full in effective.recipient_addresses:
        return None
    domains = ", ".join(sorted(effective.recipient_domains)) or "none"
    addresses = ", ".join(sorted(effective.recipient_addresses)) or "none"
    return (
        "recipient.denied",
        f"Denied: recipient {full} (argument {item.name}) is outside the allowed "
        f"domains ({domains}) and addresses ({addresses}).",
    )


def spend_cents(item: Classified) -> tuple[int | None, tuple[str, str] | None]:
    try:
        cents = parse_cents(item.value, field=item.name)
    except PolicyError as exc:
        return None, ("spend.invalid", f"Denied: {exc}.")
    if cents <= 0:
        return None, ("spend.invalid", f"Denied: {item.name} must be greater than zero.")
    return cents, None


def parse_email(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    match = _EMAIL.fullmatch(value.strip())
    if match is None:
        return None
    local, domain = match.group(1), match.group(2).lower().rstrip(".")
    if not local or local.startswith(".") or local.endswith("."):
        return None
    if not _domain_ok(domain):
        return None
    return local.lower(), domain


def domain_matches(pattern: str, domain: str) -> bool:
    """Exact host match. ``*.example.com`` covers subdomains, not the apex."""

    pattern = pattern.lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    if pattern.startswith("*."):
        base = pattern[2:]
        return domain.endswith("." + base) and domain != base
    return domain == pattern


def _domain_ok(domain: str) -> bool:
    if ".." in domain or "." not in domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not re.fullmatch(r"[a-z0-9-]+", label):
            return False
    return True


def url_pattern_ok(pattern: str) -> bool:
    return _split_pattern(pattern) is not None


def _split_url(raw: str) -> tuple[str, str, int | None, str] | str:
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "the URL could not be parsed"
    if "@" in parts.netloc:
        return "URLs with userinfo are denied"
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return "only http and https URLs are allowed"
    if parts.hostname is None:
        return "the URL has no host"
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    default = 443 if scheme == "https" else 80
    if port == default:
        port = None
    path = parts.path or "/"
    return scheme, host, port, path


def _url_match(pattern: str, scheme: str, host: str, port: int | None, path: str) -> bool:
    parsed = _split_pattern(pattern)
    if parsed is None:
        return False
    pat_scheme, pat_host, pat_port, pat_path = parsed
    if pat_scheme != scheme or pat_port != port:
        return False
    if not domain_matches(pat_host, host):
        return False
    if pat_path in {"/**", "/**/"}:
        return True
    if pat_path.endswith("/**"):
        prefix = pat_path[: -len("/**")]
        return path == prefix or path.startswith(prefix + "/")
    return path == pat_path


def _split_pattern(pattern: str) -> tuple[str, str, int | None, str] | None:
    # ``**`` is not a legal URL token, so peel the glob suffix off before parsing.
    suffix = ""
    body = pattern
    if pattern.endswith("/**"):
        body = pattern[: -len("/**")]
        suffix = "/**"
    parts = urlsplit(body)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    if "@" in parts.netloc:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    default = 443 if scheme == "https" else 80
    if port == default:
        port = None
    path = (parts.path or "") + suffix
    if path == "":
        path = "/**"
    return scheme, host, port, path
