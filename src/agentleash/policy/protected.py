# SPDX-License-Identifier: Apache-2.0
"""Paths the runtime refuses even when a policy allow-list says ``**``.

This is a best-effort denylist for obvious secret locations, not a data-loss
product. It cannot be turned off from the policy file.
"""

from __future__ import annotations

PROTECTED_FILE_GLOBS: tuple[str, ...] = (
    "**/secrets/**",
    "**/.env",
    "**/.env.*",
    "**/.ssh/**",
    "**/*id_rsa*",
    "**/*id_ed25519*",
    "**/*.pem",
    "**/*credentials*",
    "**/*service-account*.json",
)
