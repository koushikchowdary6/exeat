# SPDX-License-Identifier: MIT
"""Errors a person can act on. The CLI prints these without a traceback."""

from __future__ import annotations


class AgentleashError(Exception):
    """A policy, config, or usage problem with a human-readable message."""


class PolicyError(AgentleashError):
    """The policy or tool manifest is invalid."""

    def __init__(self, message: str, *, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


class ToolError(AgentleashError):
    """A tool ran and failed. Spend reserved for the call is released."""
