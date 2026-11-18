# SPDX-License-Identifier: MIT
"""Framework adapters. Each one is optional and offline in tests."""

from exeat.adapters.crewai_tools import as_crew_tool
from exeat.adapters.langchain_tools import as_tool
from exeat.adapters.openai_agents import function_tool

__all__ = ["as_crew_tool", "as_tool", "function_tool"]
