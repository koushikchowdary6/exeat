# SPDX-License-Identifier: Apache-2.0
"""Command line. ``python -m agentleash demo`` is the quick start."""

from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from agentleash.brand import (
    APPROVAL_FILENAME,
    AUDIT_FILENAME,
    MANIFEST_FILENAME,
    POLICY_FILENAME,
    PRODUCT_SLUG,
    PRODUCT_TITLE,
    STATE_DIRNAME,
    VERSION,
)
from agentleash.chart import render_chart
from agentleash.decision import Effect
from agentleash.demo.backend import FinanceBackend
from agentleash.demo.run import render_demo, run_demo
from agentleash.diff.compare import diff_documents, should_fail
from agentleash.diff.render import render_json, render_text
from agentleash.errors import AgentleashError
from agentleash.jsonl import read_jsonl
from agentleash.locate import finance_example_dir
from agentleash.mcp.proxy import ProxyBackend
from agentleash.mcp.server import Session, serve_stdio
from agentleash.policy.load import load_manifest, load_policy, parse_manifest, parse_policy
from agentleash.runtime.approvals import ApprovalLog
from agentleash.runtime.leash import Leash

_FAIL_ON = ("note", "low", "medium", "high")


class Paths:
    def __init__(self, policy: Path, manifest: Path, audit: Path, approvals: Path, sandbox: Path) -> None:
        self.policy = policy
        self.manifest = manifest
        self.audit = audit
        self.approvals = approvals
        self.sandbox = sandbox


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AgentleashError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PRODUCT_SLUG,
        description=f"{PRODUCT_TITLE} decides what an agent may spend, send, and open.",
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT_SLUG} {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="run the offline finance assistant")
    _add_state_flags(demo)
    demo.add_argument("--keep", action="store_true", help="append to the existing demo tape")
    demo.set_defaults(func=cmd_demo)

    diff = commands.add_parser("diff", help="compare permissions between two policies")
    diff.add_argument("--example", choices=("escalation", "narrowing"))
    diff.add_argument("--rev-base")
    diff.add_argument("--rev-head")
    diff.add_argument("--policy", help="policy path inside the repo when using --rev-base")
    diff.add_argument("--manifest", help="manifest path inside the repo when using --rev-base")
    diff.add_argument("--base-policy", type=Path)
    diff.add_argument("--head-policy", type=Path)
    diff.add_argument("--base-manifest", type=Path)
    diff.add_argument("--head-manifest", type=Path)
    diff.add_argument("--repo", type=Path, default=Path("."))
    diff.add_argument("--fail-on", choices=_FAIL_ON, default="medium")
    diff.add_argument("--format", choices=("text", "json"), default="text")
    diff.set_defaults(func=cmd_diff)

    check = commands.add_parser("check", help="decide one call without writing the audit log")
    _add_path_flags(check)
    check.add_argument("--agent", required=True)
    check.add_argument("--tool", required=True)
    check.add_argument("--args", default="{}")
    check.set_defaults(func=cmd_check)

    approve = commands.add_parser("approve", help="approve one exact call")
    _add_path_flags(approve)
    approve.add_argument("--agent", required=True)
    approve.add_argument("--tool", required=True)
    approve.add_argument("--args", default="{}")
    approve.add_argument("--by", default="operator")
    approve.add_argument("--ttl", type=int, default=1800)
    approve.set_defaults(func=cmd_approve)

    audit = commands.add_parser("audit", help="print the audit tape")
    _add_state_flags(audit)
    audit.add_argument("--follow", action="store_true")
    audit.set_defaults(func=cmd_audit)

    chart = commands.add_parser("chart", help="print the org chart")
    _add_path_flags(chart)
    chart.set_defaults(func=cmd_chart)

    board = commands.add_parser("dashboard", help="open the local permission board")
    _add_path_flags(board)
    board.add_argument("--host", default="127.0.0.1")
    board.add_argument("--port", type=int, default=4747)
    board.set_defaults(func=cmd_dashboard)

    mcp = commands.add_parser("mcp", help="serve the gate on MCP stdio")
    _add_path_flags(mcp)
    mcp.add_argument("--agent", default=None)
    mcp.add_argument(
        "upstream",
        nargs=argparse.REMAINDER,
        help="optional upstream command after -- , for example: -- python server.py",
    )
    mcp.set_defaults(func=cmd_mcp)
    return parser


def cmd_demo(args: argparse.Namespace) -> int:
    args.example = True
    paths = resolve_paths(args, default_example=True)
    if not args.keep:
        for path in (paths.audit, paths.approvals):
            if path.exists():
                path.unlink()
    results = run_demo(
        root=paths.sandbox,
        audit_path=paths.audit,
        approval_path=paths.approvals,
    )
    sys.stdout.write(render_demo(results))
    print(f"audit      {paths.audit}")
    print("dashboard  python -m agentleash dashboard")
    missed = [result for result in results if not result.ok]
    if missed:
        print(f"{len(missed)} demo steps did not match the script", file=sys.stderr)
        return 1
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    base_policy, head_policy, base_manifest, head_manifest, base_label, head_label = _diff_inputs(args)
    findings = diff_documents(base_policy, head_policy, base_manifest, head_manifest)
    if args.format == "json":
        sys.stdout.write(render_json(findings, base_label=base_label, head_label=head_label, fail_on=args.fail_on))
    else:
        sys.stdout.write(render_text(findings, base_label=base_label, head_label=head_label, fail_on=args.fail_on))
    return 1 if should_fail(findings, args.fail_on) else 0


def cmd_check(args: argparse.Namespace) -> int:
    paths = resolve_paths(args, default_example=True)
    leash = _leash(paths, args.agent)
    decision = leash.decide(args.tool, _parse_args(args.args))
    print(decision.effect.value)
    print(decision.reason)
    if decision.effect is Effect.ALLOW:
        return 0
    if decision.effect is Effect.REQUIRE_APPROVAL:
        return 3
    return 1


def cmd_approve(args: argparse.Namespace) -> int:
    paths = resolve_paths(args, default_example=True)
    record = ApprovalLog(paths.approvals).grant(
        agent=args.agent,
        tool=args.tool,
        arguments=_parse_args(args.args),
        by=args.by,
        ttl_seconds=args.ttl,
    )
    print(record["id"])
    print(f"fingerprint {record['fingerprint']}")
    print(f"expires     {record['expires']}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    paths = resolve_paths(args, default_example=True)
    seen = 0
    while True:
        events = read_jsonl(paths.audit)
        for event in events[seen:]:
            print(_format_event(event))
        seen = len(events)
        if not args.follow:
            if seen == 0:
                print(f"(no decisions yet in {paths.audit})")
            return 0
        time.sleep(0.5)


def cmd_chart(args: argparse.Namespace) -> int:
    paths = resolve_paths(args, default_example=True)
    policy = load_policy(paths.policy)
    history = read_jsonl(paths.audit)
    sys.stdout.write(render_chart(policy, history=history))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from agentleash.dashboard.server import serve

    paths = resolve_paths(args, default_example=True)
    serve(
        host=args.host,
        port=args.port,
        policy_path=paths.policy,
        manifest_path=paths.manifest,
        audit_path=paths.audit,
        approval_path=paths.approvals,
        sandbox=paths.sandbox,
    )
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    paths = resolve_paths(args, default_example=True)
    agent = args.agent or "finance-assistant"
    leash = _leash(paths, agent)
    upstream = [part for part in (args.upstream or []) if part != "--"]
    backend: FinanceBackend | ProxyBackend
    proxy: ProxyBackend | None = None
    if upstream:
        proxy = ProxyBackend(upstream)
        backend = proxy
    else:
        backend = FinanceBackend(paths.sandbox)
    try:
        serve_stdio(Session(leash, backend, leash.manifest))
    finally:
        if proxy is not None:
            proxy.close()
    return 0


def resolve_paths(args: argparse.Namespace, *, default_example: bool) -> Paths:
    policy_arg = getattr(args, "policy", None)
    manifest_arg = getattr(args, "manifest", None)
    audit_arg = getattr(args, "audit", None)
    approval_arg = getattr(args, "approvals", None)
    use_example = bool(getattr(args, "example", False))
    if policy_arg:
        policy = Path(policy_arg)
        sandbox = policy.parent
        manifest = Path(manifest_arg) if manifest_arg else sandbox / MANIFEST_FILENAME
        state = sandbox / STATE_DIRNAME
    elif use_example or (default_example and not Path(POLICY_FILENAME).is_file()):
        sandbox = finance_example_dir()
        policy = sandbox / POLICY_FILENAME
        manifest = Path(manifest_arg) if manifest_arg else sandbox / MANIFEST_FILENAME
        state = sandbox / STATE_DIRNAME
    else:
        policy = Path(POLICY_FILENAME)
        sandbox = Path(".")
        manifest = Path(manifest_arg) if manifest_arg else Path(MANIFEST_FILENAME)
        state = Path(STATE_DIRNAME)
    audit = Path(audit_arg) if audit_arg else state / AUDIT_FILENAME
    approvals = Path(approval_arg) if approval_arg else state / APPROVAL_FILENAME
    return Paths(policy, manifest, audit, approvals, sandbox)


def _diff_inputs(args: argparse.Namespace) -> tuple[Any, Any, Any, Any, str, str]:
    selected = [
        bool(args.example),
        bool(args.rev_base or args.rev_head),
        bool(args.base_policy or args.head_policy or args.base_manifest or args.head_manifest),
    ]
    if sum(selected) != 1:
        raise AgentleashError("choose one diff mode: --example, --rev-base/--rev-head, or --base-policy/--head-policy")
    if args.example:
        root = finance_example_dir()
        base_path = root / POLICY_FILENAME
        base_manifest_path = root / MANIFEST_FILENAME
        if args.example == "narrowing":
            head_path = root / "fixtures" / "narrowed-policy.yaml"
            head_manifest_path = base_manifest_path
        else:
            head_path = root / "fixtures" / "escalated-policy.yaml"
            head_manifest_path = root / "fixtures" / "escalated-manifest.yaml"
        return (
            load_policy(base_path),
            load_policy(head_path),
            load_manifest(base_manifest_path),
            load_manifest(head_manifest_path),
            _display_path(base_path),
            _display_path(head_path),
        )
    if args.rev_base or args.rev_head:
        if not args.rev_base or not args.rev_head:
            raise AgentleashError("pass both --rev-base and --rev-head")
        policy_path = args.policy or str(finance_example_dir() / POLICY_FILENAME)
        manifest_path = args.manifest or str(finance_example_dir() / MANIFEST_FILENAME)
        # Git paths are relative to the repo, not the checkout layout of this file.
        policy_rel = _repo_relative(args.repo, policy_path)
        manifest_rel = _repo_relative(args.repo, manifest_path)
        base_policy = parse_policy(
            _git_yaml(args.repo, args.rev_base, policy_rel),
            source=f"{args.rev_base}:{policy_rel}",
        )
        head_policy = parse_policy(
            _git_yaml(args.repo, args.rev_head, policy_rel),
            source=f"{args.rev_head}:{policy_rel}",
        )
        base_manifest = parse_manifest(
            _git_yaml(args.repo, args.rev_base, manifest_rel),
            source=f"{args.rev_base}:{manifest_rel}",
        )
        head_manifest = parse_manifest(
            _git_yaml(args.repo, args.rev_head, manifest_rel),
            source=f"{args.rev_head}:{manifest_rel}",
        )
        return (
            base_policy,
            head_policy,
            base_manifest,
            head_manifest,
            f"{args.rev_base}:{policy_rel}",
            f"{args.rev_head}:{policy_rel}",
        )
    if not (args.base_policy and args.head_policy and args.base_manifest and args.head_manifest):
        raise AgentleashError("file mode needs --base-policy, --head-policy, --base-manifest, and --head-manifest")
    return (
        load_policy(args.base_policy),
        load_policy(args.head_policy),
        load_manifest(args.base_manifest),
        load_manifest(args.head_manifest),
        _display_path(args.base_policy),
        _display_path(args.head_policy),
    )


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def _repo_relative(repo: Path, path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return str(candidate.resolve().relative_to(repo.resolve()))
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix()


def _git_yaml(repo: Path, ref: str, path: str) -> object:
    import subprocess

    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "git show failed"
        raise AgentleashError(f"could not read {path} at {ref}: {detail}")
    try:
        loaded = yaml.safe_load(proc.stdout)
    except yaml.YAMLError as exc:
        raise AgentleashError(f"could not parse {ref}:{path}: {exc}") from exc
    return loaded


def _leash(paths: Paths, agent: str) -> Leash:
    return Leash.open(
        policy_path=paths.policy,
        manifest_path=paths.manifest,
        agent=agent,
        audit_path=paths.audit,
        approval_path=paths.approvals,
    )


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise AgentleashError(f"--args is not JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AgentleashError("--args must be a JSON object")
    return loaded


def _format_event(event: dict[str, Any]) -> str:
    effect = str(event.get("effect", ""))
    agent = str(event.get("agent", ""))
    tool = str(event.get("tool", ""))
    reason = str(event.get("reason", ""))
    stamp = str(event.get("ts", ""))
    return f"{stamp}  {effect:<18}  {agent:<20}  {tool:<16}  {reason}"


def _add_path_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--example", action="store_true", help="use the finance example")
    parser.add_argument("--policy", default=None)
    parser.add_argument("--manifest", default=None)
    _add_state_flags(parser)


def _add_state_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--audit", default=None)
    parser.add_argument("--approvals", default=None)
