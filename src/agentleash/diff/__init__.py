# SPDX-License-Identifier: Apache-2.0
"""Compare agent permissions between two revisions."""

from agentleash.diff.compare import Finding, diff_documents, should_fail

__all__ = ["Finding", "diff_documents", "should_fail"]
