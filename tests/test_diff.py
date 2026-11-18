# SPDX-License-Identifier: MIT
import os
import subprocess
from pathlib import Path

import yaml

from agentleash.diff.compare import diff_documents, should_fail
from agentleash.locate import finance_example_dir
from agentleash.policy.load import load_manifest, load_policy, parse_manifest, parse_policy


def test_escalation_fixture_fails_and_names_the_gains() -> None:
    root = finance_example_dir()
    findings = diff_documents(
        load_policy(root / "agent-policy.yaml"),
        load_policy(root / "fixtures" / "escalated-policy.yaml"),
        load_manifest(root / "tool-manifest.yaml"),
        load_manifest(root / "fixtures" / "escalated-manifest.yaml"),
    )
    codes = {finding.code for finding in findings}
    blob = "\n".join(finding.message for finding in findings)
    assert "tool.gained" in codes
    assert "shell.exec" in blob
    assert "spend.raised" in codes
    assert "recipient.domain_added" in codes
    assert "evil.test" in blob
    assert "file.allow_widened" in codes
    assert "approval.loosened" in codes
    assert "manifest.downgraded" in codes
    assert "agent.added" in codes
    assert "payroll-bot" in blob
    assert "finance-intern at $800.00" in blob
    assert should_fail(findings, "medium")
    assert should_fail(findings, "high")


def test_narrowing_fixture_does_not_fail() -> None:
    root = finance_example_dir()
    base_manifest = load_manifest(root / "tool-manifest.yaml")
    findings = diff_documents(
        load_policy(root / "agent-policy.yaml"),
        load_policy(root / "fixtures" / "narrowed-policy.yaml"),
        base_manifest,
        base_manifest,
    )
    assert findings
    assert not should_fail(findings, "medium")
    assert not should_fail(findings, "high")
    assert all(finding.severity in {"note", "low"} for finding in findings)


def test_glob_narrowing_is_not_a_widening() -> None:
    base = _policy('allow: ["data/**"]')
    head = _policy('allow: ["data/finance/**"]')
    manifest = _manifest()
    findings = diff_documents(base, head, manifest, manifest)
    assert not any(finding.severity == "high" for finding in findings)
    assert any(finding.code == "file.allow_narrowed" for finding in findings)


def test_git_refs(tmp_path: Path) -> None:
    root = finance_example_dir()
    policy = root / "agent-policy.yaml"
    manifest = root / "tool-manifest.yaml"
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "policy"
    target.mkdir()
    (target / "agent-policy.yaml").write_text(policy.read_text(encoding="utf-8"), encoding="utf-8")
    (target / "tool-manifest.yaml").write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, env=env)
    widened = (root / "fixtures" / "escalated-policy.yaml").read_text(encoding="utf-8")
    (target / "agent-policy.yaml").write_text(widened, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "widen"], cwd=repo, check=True, capture_output=True, env=env)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    from agentleash.cli import main

    code = main(
        [
            "diff",
            "--rev-base",
            base,
            "--rev-head",
            head,
            "--repo",
            str(repo),
            "--policy",
            "policy/agent-policy.yaml",
            "--manifest",
            "policy/tool-manifest.yaml",
            "--format",
            "json",
        ]
    )
    assert code == 1


def _policy(files_allow: str) -> object:
    text = f"""
version: 1
roles:
  - name: clerk
    tools: [files.read]
    scopes:
      files:
        {files_allow}
agents:
  - name: ada
    role: clerk
"""
    return parse_policy(yaml.safe_load(text))


def _manifest() -> object:
    text = """
version: 1
tools:
  - name: files.read
    arguments:
      - {name: path, type: path, required: true}
"""
    return parse_manifest(yaml.safe_load(text))
