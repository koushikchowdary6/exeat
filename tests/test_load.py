# SPDX-License-Identifier: MIT
from pathlib import Path

import pytest

from agentleash.errors import PolicyError
from agentleash.locate import finance_example_dir
from agentleash.policy.load import load_manifest, load_policy, parse_policy


def test_example_files_load() -> None:
    root = finance_example_dir()
    policy = load_policy(root / "agent-policy.yaml")
    manifest = load_manifest(root / "tool-manifest.yaml")
    assert policy.org.name == "Northwind Books"
    assert {agent.name for agent in policy.agents} == {"finance-assistant", "finance-intern"}
    assert manifest.tool("ledger.transfer") is not None
    load_policy(root / "fixtures" / "escalated-policy.yaml")
    load_manifest(root / "fixtures" / "escalated-manifest.yaml")
    load_policy(root / "fixtures" / "narrowed-policy.yaml")


def test_agent_cannot_raise_a_ceiling() -> None:
    text = Path(finance_example_dir() / "agent-policy.yaml").read_text(encoding="utf-8")
    broken = text.replace("per_call: 50", "per_call: 50000", 1)
    with pytest.raises(PolicyError, match="only lower"):
        parse_policy(yaml_load(broken))


def test_agent_cannot_add_an_allow() -> None:
    text = """
version: 1
roles:
  - name: clerk
    tools: [files.read]
    scopes:
      files:
        allow: ["data/**"]
agents:
  - name: ada
    role: clerk
    scopes:
      files:
        allow: ["**"]
"""
    with pytest.raises(PolicyError, match="not permitted on an agent"):
        parse_policy(yaml_load(text))


def test_cycle_is_rejected() -> None:
    text = """
version: 1
roles:
  - name: a
    inherits: b
    tools: [files.read]
  - name: b
    inherits: a
    tools: [files.read]
agents:
  - name: ada
    role: a
"""
    with pytest.raises(PolicyError, match="cycle"):
        parse_policy(yaml_load(text))


def test_bad_url_pattern_is_rejected() -> None:
    text = """
version: 1
roles:
  - name: clerk
    tools: [http.get]
    scopes:
      urls:
        allow: ["**"]
agents:
  - name: ada
    role: clerk
"""
    with pytest.raises(PolicyError, match="URL glob"):
        parse_policy(yaml_load(text))


def yaml_load(text: str) -> object:
    import yaml

    return yaml.safe_load(text)
