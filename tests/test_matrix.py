# SPDX-License-Identifier: MIT
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agentleash.cli import main
from agentleash.decision import Decision, Effect
from agentleash.diff.compare import diff_documents
from agentleash.locate import finance_example_dir
from agentleash.policy.evaluate import evaluate
from agentleash.policy.load import load_manifest, load_policy, parse_manifest, parse_policy

NOW = datetime(2026, 6, 2, 8, 0, tzinfo=UTC)
ROOT = finance_example_dir()


def _policy(text: str):
    return parse_policy(yaml.safe_load(text))


def _manifest(text: str):
    return parse_manifest(yaml.safe_load(text))


BASE = """
version: 1
roles:
  - name: reader
    tools: [files.read]
    scopes:
      files:
        allow: ["data/**"]
        deny: ["data/secret/**"]
  - name: analyst
    inherits: reader
    tools: [email.send]
    spend: {per_call: 100, per_day: 200, total: 300}
    recipients:
      domains: [a.example]
      addresses: [boss@a.example]
    approvals:
      - name: mail
        tools: [email.send]
        when: {always: true}
        reason: Mail waits.
agents:
  - name: ada
    role: analyst
  - name: bob
    role: reader
"""

HEAD = """
version: 1
roles:
  - name: reader
    tools: [files.read, ledger.query]
    scopes:
      files:
        allow: ["**"]
      tables:
        allow: [finance.invoices]
  - name: analyst
    tools: [email.send, ledger.transfer]
    spend: {per_call: 500, per_day: 50, total: 1000}
    recipients:
      domains: [b.example]
      addresses: [new@b.example]
    scopes:
      urls:
        allow: ["https://ledger.internal.example/**"]
    approvals:
      - name: mail
        tools: [ledger.transfer]
        when: {arg: amount_usd, gt: 10}
        reason: Large transfers wait.
  - name: auditor
    tools: [files.read]
agents:
  - name: ada
    role: analyst
    spend: {per_call: 10, per_day: 20, total: 30}
  - name: cara
    role: analyst
"""

MANIFEST_A = """
version: 1
tools:
  - name: files.read
    arguments:
      - {name: path, type: path, required: true}
      - {name: note, type: string, required: false}
  - name: email.send
    arguments:
      - {name: to, type: recipient, required: true}
      - {name: subject, type: string, required: true}
      - {name: body, type: string, required: true}
  - name: old.tool
    arguments:
      - {name: path, type: path, required: true}
"""

MANIFEST_B = """
version: 1
tools:
  - name: files.read
    arguments:
      - {name: path, type: string, required: true}
      - {name: url, type: url, required: false}
  - name: email.send
    arguments:
      - {name: to, type: url, required: true}
      - {name: subject, type: string, required: true}
      - {name: body, type: string, required: true}
  - name: shell.exec
    arguments:
      - {name: command, type: string, required: true}
"""


def test_diff_covers_role_agent_and_manifest_changes() -> None:
    findings = diff_documents(_policy(BASE), _policy(HEAD), _manifest(MANIFEST_A), _manifest(MANIFEST_B))
    codes = {item.code for item in findings}
    assert "role.added" in codes
    assert "agent.removed" in codes
    assert "agent.added" in codes
    assert "role.inherits" in codes
    assert "tool.gained" in codes
    assert "spend.raised" in codes
    assert "spend.lowered" in codes or "approval.loosened" in codes
    assert "manifest.downgraded" in codes or "manifest.reclassified" in codes
    assert "manifest.tool_added" in codes
    assert "manifest.tool_removed" in codes


def test_odd_arguments_are_denied() -> None:
    policy = load_policy(ROOT / "agent-policy.yaml")
    manifest = load_manifest(ROOT / "tool-manifest.yaml")

    def decide(tool: str, arguments: dict) -> object:
        return evaluate(policy, manifest, "finance-assistant", tool, arguments, now=NOW)

    assert decide("files.read", {"path": "data\\secret"}).rule == "path.invalid"  # type: ignore[attr-defined]
    assert decide("files.read", {"path": "data/../../../../etc/passwd"}).rule == "path.invalid"  # type: ignore[attr-defined]
    assert decide("files.read", {"path": "."}).rule == "path.invalid"  # type: ignore[attr-defined]
    assert decide("files.read", {"path": ""}).rule == "args.invalid"  # type: ignore[attr-defined]
    assert decide("email.send", {"to": "not-an-email", "subject": "s", "body": "b"}).rule == "recipient.invalid"  # type: ignore[attr-defined]
    assert decide("http.get", {"url": "https://user:pw@ledger.internal.example/x"}).rule == "url.invalid"  # type: ignore[attr-defined]
    assert decide("http.get", {"url": "ftp://ledger.internal.example/x"}).rule == "url.invalid"  # type: ignore[attr-defined]
    assert decide("ledger.query", {"table": "../finance.invoices"}).rule == "table.invalid"  # type: ignore[attr-defined]
    assert decide("ledger.transfer", {"amount_usd": 0, "vendor_id": "V"}).rule == "spend.invalid"  # type: ignore[attr-defined]
    assert decide("ledger.transfer", {"amount_usd": "1.234", "vendor_id": "V"}).rule == "spend.invalid"  # type: ignore[attr-defined]
    decision = Decision(
        effect=Effect.ALLOW,
        rule="allow",
        reason="ok",
        agent="ada",
        role="analyst",
        tool="files.read",
        fingerprint="x",
        policy_hash="y",
    )
    assert decision.allowed is True


def test_markdown_diff_command() -> None:
    assert main(["diff", "--example", "narrowing", "--format", "markdown"]) == 0


def test_hook_flags_a_brand_new_policy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, env=env)
    (repo / "agent-policy.yaml").write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "add", "agent-policy.yaml"], cwd=repo, check=True, capture_output=True)
    assert main(["hook", "--repo", str(repo)]) == 1
    only = tmp_path / "only"
    only.mkdir()
    subprocess.run(["git", "init"], cwd=only, check=True, capture_output=True)
    (only / "agent-policy.yaml").write_text("version: 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=only, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "bad"], cwd=only, check=True, capture_output=True, env=env)
    # Change the policy and delete any chance of a manifest.
    (only / "agent-policy.yaml").write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["hook", "--repo", str(only)]) == 2
