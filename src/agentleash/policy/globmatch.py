# SPDX-License-Identifier: Apache-2.0
"""Segment-aware globs for file scopes and for the permission diff.

``*`` matches inside one path segment. ``**`` matches zero or more whole
segments. ``covers`` is deliberately conservative: when it cannot prove that
one pattern contains another, it returns false, and the diff treats that as a
widening.
"""

from __future__ import annotations


def glob_match(pattern: str, path: str) -> bool:
    """Return whether ``path`` matches ``pattern``."""

    return _match(_segments(pattern), _segments(path))


def covers(parent: str, child: str) -> bool:
    """Whether every path matched by ``child`` is also matched by ``parent``."""

    if parent == child or parent in {"*", "**"}:
        return True
    if parent.endswith("/**"):
        prefix = parent[: -len("/**")]
        if child in {parent, prefix} or child.startswith(prefix + "/"):
            return True
    if any(token in child for token in ("*", "?")):
        return False
    return glob_match(parent, child)


def _segments(value: str) -> list[str]:
    return [part for part in value.split("/") if part not in ("", ".")]


def _match(pattern: list[str], parts: list[str]) -> bool:
    index = 0
    cursor = 0
    while index < len(pattern):
        token = pattern[index]
        if token == "**":
            if index == len(pattern) - 1:
                return True
            rest = pattern[index + 1 :]
            return any(_match(rest, parts[split:]) for split in range(cursor, len(parts) + 1))
        if cursor >= len(parts) or not _segment_match(token, parts[cursor]):
            return False
        index += 1
        cursor += 1
    return cursor == len(parts)


def _segment_match(pattern: str, text: str) -> bool:
    if pattern == "**":
        return False
    plen = len(pattern)
    tlen = len(text)
    pi = 0
    ti = 0
    star_pi = -1
    star_ti = -1
    while ti < tlen:
        if pi < plen and (pattern[pi] == "?" or pattern[pi] == text[ti]):
            pi += 1
            ti += 1
            continue
        if pi < plen and pattern[pi] == "*":
            star_pi = pi
            star_ti = ti
            pi += 1
            continue
        if star_pi >= 0:
            star_ti += 1
            pi = star_pi + 1
            ti = star_ti
            continue
        return False
    while pi < plen and pattern[pi] == "*":
        pi += 1
    return pi == plen
