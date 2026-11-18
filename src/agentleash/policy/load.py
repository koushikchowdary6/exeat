# SPDX-License-Identifier: MIT
"""Parse ``agent-policy.yaml`` and the tool manifest."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

from agentleash.errors import PolicyError
from agentleash.money import parse_cents
from agentleash.policy.model import (
    Agent,
    ApprovalRule,
    Argument,
    Manifest,
    Org,
    Policy,
    Role,
    ScopeSet,
    SpendLimits,
    ToolSpec,
)

_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_TOOL = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_ARG_TYPES = frozenset({"string", "number", "boolean", "spend", "path", "url", "table", "recipient"})
_RISKS = frozenset({"low", "medium", "high"})
_SUPPORTED_VERSION = 1


def load_policy(path: Path) -> Policy:
    return parse_policy(_read_yaml(path), source=str(path))


def load_manifest(path: Path) -> Manifest:
    return parse_manifest(_read_yaml(path), source=str(path))


def parse_policy(data: object, *, source: str = "policy") -> Policy:
    mapping = _mapping(data, source)
    version = _version(mapping, source)
    org = _org(mapping.get("org"), source)
    roles_raw = _list(mapping.get("roles"), f"{source}.roles")
    if not roles_raw:
        raise PolicyError(f"{source}.roles must list at least one role")
    roles = tuple(_role(item, f"{source}.roles[{index}]") for index, item in enumerate(roles_raw))
    _unique((role.name for role in roles), f"{source}.roles")
    agents_raw = _list(mapping.get("agents"), f"{source}.agents")
    if not agents_raw:
        raise PolicyError(f"{source}.agents must list at least one agent")
    agents = tuple(_agent(item, f"{source}.agents[{index}]") for index, item in enumerate(agents_raw))
    _unique((agent.name for agent in agents), f"{source}.agents")
    policy = Policy(version=version, org=org, roles=roles, agents=agents)
    _validate_graph(policy, source)
    return policy


def parse_manifest(data: object, *, source: str = "manifest") -> Manifest:
    mapping = _mapping(data, source)
    version = _version(mapping, source)
    tools_raw = _list(mapping.get("tools"), f"{source}.tools")
    if not tools_raw:
        raise PolicyError(f"{source}.tools must list at least one tool")
    tools = tuple(_tool(item, f"{source}.tools[{index}]") for index, item in enumerate(tools_raw))
    _unique((tool.name for tool in tools), f"{source}.tools")
    return Manifest(version=version, tools=tools)


def _read_yaml(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"could not read {path}: {exc.strerror}") from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"could not parse {path}: {exc}") from exc
    if loaded is None:
        raise PolicyError(f"{path} is empty")
    return loaded


def _version(mapping: dict[str, Any], source: str) -> int:
    version = mapping.get("version")
    if version != _SUPPORTED_VERSION:
        raise PolicyError(f"{source}.version must be {_SUPPORTED_VERSION}")
    return _SUPPORTED_VERSION


def _org(value: object, source: str) -> Org:
    if value is None:
        return Org(name="Local", unit="")
    mapping = _mapping(value, f"{source}.org")
    return Org(
        name=_text(mapping, "name", f"{source}.org", default="Local"),
        unit=_text(mapping, "unit", f"{source}.org", default=""),
    )


def _role(value: object, where: str) -> Role:
    mapping = _mapping(value, where)
    name = _ident(mapping, "name", where)
    return Role(
        name=name,
        description=_text(mapping, "description", where, default=""),
        inherits=_optional_ident(mapping, "inherits", where),
        tools=_tools(mapping.get("tools", []), f"{where}.tools"),
        spend=_spend(mapping.get("spend"), f"{where}.spend"),
        recipient_domains=_domain_list(mapping.get("recipients"), f"{where}.recipients", "domains"),
        recipient_addresses=_address_list(mapping.get("recipients"), f"{where}.recipients", "addresses"),
        files=_scope(mapping.get("scopes"), where, "files"),
        urls=_scope(mapping.get("scopes"), where, "urls"),
        tables=_scope(mapping.get("scopes"), where, "tables"),
        approvals=_approvals(mapping.get("approvals", []), f"{where}.approvals"),
    )


def _agent(value: object, where: str) -> Agent:
    mapping = _mapping(value, where)
    return Agent(
        name=_ident(mapping, "name", where),
        role=_ident(mapping, "role", where),
        description=_text(mapping, "description", where, default=""),
        spend=_spend(mapping.get("spend"), f"{where}.spend"),
        exclude_tools=frozenset(_tools(mapping.get("exclude_tools", []), f"{where}.exclude_tools")),
        files=_scope(mapping.get("scopes"), where, "files", allow_grants=False),
        urls=_scope(mapping.get("scopes"), where, "urls", allow_grants=False),
        tables=_scope(mapping.get("scopes"), where, "tables", allow_grants=False),
    )


def _tool(value: object, where: str) -> ToolSpec:
    mapping = _mapping(value, where)
    name = _tool_name(mapping, "name", where)
    risk = _text(mapping, "risk", where, default="medium")
    if risk not in _RISKS:
        raise PolicyError(f"{where}.risk must be one of low, medium, high")
    raw_args = mapping.get("arguments", [])
    arguments = _arguments(raw_args, f"{where}.arguments")
    return ToolSpec(
        name=name,
        description=_text(mapping, "description", where, default=""),
        risk=risk,
        arguments=arguments,
    )


def _arguments(value: object, where: str) -> tuple[Argument, ...]:
    items = _list(value, where)
    arguments: list[Argument] = []
    for index, item in enumerate(items):
        mapping = _mapping(item, f"{where}[{index}]")
        name = _text(mapping, "name", f"{where}[{index}]")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise PolicyError(f"{where}[{index}].name must be a lower_snake identifier")
        kind = _text(mapping, "type", f"{where}[{index}]")
        if kind not in _ARG_TYPES:
            allowed = ", ".join(sorted(_ARG_TYPES))
            raise PolicyError(f"{where}[{index}].type must be one of {allowed}")
        required = mapping.get("required", False)
        if not isinstance(required, bool):
            raise PolicyError(f"{where}[{index}].required must be true or false")
        arguments.append(
            Argument(
                name=name,
                type=kind,
                required=required,
                description=_text(mapping, "description", f"{where}[{index}]", default=""),
            )
        )
    _unique((arg.name for arg in arguments), where)
    return tuple(arguments)


def _approvals(value: object, where: str) -> tuple[ApprovalRule, ...]:
    items = _list(value, where)
    rules: list[ApprovalRule] = []
    for index, item in enumerate(items):
        mapping = _mapping(item, f"{where}[{index}]")
        name = _ident(mapping, "name", f"{where}[{index}]")
        tools = frozenset(_tools(mapping.get("tools"), f"{where}[{index}].tools"))
        if not tools:
            raise PolicyError(f"{where}[{index}].tools must list at least one tool")
        reason = _text(mapping, "reason", f"{where}[{index}]")
        when = _mapping(mapping.get("when"), f"{where}[{index}].when")
        always = bool(when.get("always", False))
        if when.get("always") is not None and not isinstance(when.get("always"), bool):
            raise PolicyError(f"{where}[{index}].when.always must be true or false")
        arg = when.get("arg")
        gt = when.get("gt")
        if always:
            if arg is not None or gt is not None:
                raise PolicyError(f"{where}[{index}].when cannot mix always with arg/gt")
            rules.append(ApprovalRule(name=name, tools=tools, reason=reason, always=True))
            continue
        if not isinstance(arg, str) or not arg:
            raise PolicyError(f"{where}[{index}].when.arg must be an argument name")
        try:
            gt_cents = parse_cents(gt, field=f"{where}[{index}].when.gt")
        except PolicyError:
            raise
        rules.append(
            ApprovalRule(
                name=name,
                tools=tools,
                reason=reason,
                always=False,
                arg=arg,
                gt_cents=gt_cents,
            )
        )
    _unique((rule.name for rule in rules), where)
    return tuple(rules)


def _spend(value: object, where: str) -> SpendLimits:
    if value is None:
        return SpendLimits()
    mapping = _mapping(value, where)
    currency = _text(mapping, "currency", where, default="USD")
    if currency != "USD":
        raise PolicyError(f"{where}.currency must be USD in this version")
    return SpendLimits(
        per_call=_optional_cents(mapping, "per_call", where),
        per_day=_optional_cents(mapping, "per_day", where),
        total=_optional_cents(mapping, "total", where),
        currency=currency,
    )


def _optional_cents(mapping: dict[str, Any], key: str, where: str) -> int | None:
    if key not in mapping or mapping[key] is None:
        return None
    return parse_cents(mapping[key], field=f"{where}.{key}")


def _scope(
    value: object,
    where: str,
    kind: str,
    *,
    allow_grants: bool = True,
) -> ScopeSet:
    if value is None:
        return ScopeSet()
    mapping = _mapping(value, f"{where}.scopes")
    raw = mapping.get(kind)
    if raw is None:
        return ScopeSet()
    block = _mapping(raw, f"{where}.scopes.{kind}")
    allow = _str_tuple(block.get("allow", []), f"{where}.scopes.{kind}.allow")
    deny = _str_tuple(block.get("deny", []), f"{where}.scopes.{kind}.deny")
    if allow and not allow_grants:
        raise PolicyError(
            f"{where}.scopes.{kind}.allow is not permitted on an agent. "
            "Agents can add deny patterns only; grants live on roles."
        )
    for pattern in allow + deny:
        if "\\" in pattern or pattern.startswith("/"):
            raise PolicyError(f"{where}.scopes.{kind} pattern {pattern!r} must be a relative glob")
    if kind == "urls":
        from agentleash.policy.check import url_pattern_ok

        for pattern in allow + deny:
            if not url_pattern_ok(pattern):
                raise PolicyError(f"{where}.scopes.urls pattern {pattern!r} is not an http(s) URL glob")
    return ScopeSet(allow=allow, deny=deny)


def _domain_list(value: object, where: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    mapping = _mapping(value, where)
    domains = _str_tuple(mapping.get(key, []), f"{where}.{key}")
    cleaned: list[str] = []
    for domain in domains:
        lowered = domain.lower().rstrip(".")
        if not lowered or " " in lowered or "@" in lowered:
            raise PolicyError(f"{where}.{key} entry {domain!r} is not a domain")
        cleaned.append(lowered)
    return tuple(cleaned)


def _address_list(value: object, where: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    mapping = _mapping(value, where)
    from agentleash.policy.check import parse_email

    addresses = _str_tuple(mapping.get(key, []), f"{where}.{key}")
    cleaned: list[str] = []
    for address in addresses:
        parsed = parse_email(address)
        if parsed is None:
            raise PolicyError(f"{where}.{key} entry {address!r} must be one email address")
        local, domain = parsed
        cleaned.append(f"{local}@{domain}")
    return tuple(cleaned)


def _tools(value: object, where: str) -> tuple[str, ...]:
    names = _str_tuple(value, where)
    for name in names:
        if not _TOOL.fullmatch(name):
            raise PolicyError(f"{where} entry {name!r} is not a tool name like ledger.read")
    return names


def _validate_graph(policy: Policy, source: str) -> None:
    by_name = {role.name: role for role in policy.roles}
    for role in policy.roles:
        if role.inherits is not None and role.inherits not in by_name:
            raise PolicyError(f"role {role.name} inherits unknown role {role.inherits}")
        _inheritance_chain(role.name, by_name, source)
    from agentleash.policy.effective import effective_for_agent

    for agent in policy.agents:
        if agent.role not in by_name:
            raise PolicyError(f"agent {agent.name} is bound to unknown role {agent.role}")
        effective_for_agent(policy, agent.name)


def _inheritance_chain(name: str, roles: dict[str, Role], source: str) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = name
    while current is not None:
        if current in seen:
            cycle = " -> ".join([*chain, current])
            raise PolicyError(f"{source} role inheritance cycle: {cycle}")
        seen.add(current)
        chain.append(current)
        current = roles[current].inherits
    chain.reverse()
    return tuple(chain)


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{where} must be a mapping")
    return cast(dict[str, Any], value)


def _list(value: object, where: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PolicyError(f"{where} must be a list")
    return list(value)


def _text(mapping: dict[str, Any], key: str, where: str, *, default: str | None = None) -> str:
    if key not in mapping or mapping[key] is None:
        if default is None:
            raise PolicyError(f"{where} is missing {key}")
        return default
    value = mapping[key]
    if not isinstance(value, str):
        raise PolicyError(f"{where}.{key} must be a string")
    if default is None and not value.strip():
        raise PolicyError(f"{where}.{key} must not be empty")
    return value.strip()


def _ident(mapping: dict[str, Any], key: str, where: str) -> str:
    value = _text(mapping, key, where)
    if not _NAME.fullmatch(value):
        raise PolicyError(f"{where}.{key} must be a lowercase identifier")
    return value


def _optional_ident(mapping: dict[str, Any], key: str, where: str) -> str | None:
    if key not in mapping or mapping[key] is None:
        return None
    value = mapping[key]
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise PolicyError(f"{where}.{key} must be a role name")
    return value


def _tool_name(mapping: dict[str, Any], key: str, where: str) -> str:
    value = _text(mapping, key, where)
    if not _TOOL.fullmatch(value):
        raise PolicyError(f"{where}.{key} must be a tool name like ledger.read")
    return value


def _str_tuple(value: object, where: str) -> tuple[str, ...]:
    items = _list(value, where)
    cleaned: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(f"{where}[{index}] must be a non-empty string")
        cleaned.append(item.strip())
    return tuple(cleaned)


def _unique(values: Any, where: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise PolicyError(f"{where} contains a duplicate name {value!r}")
        seen.add(value)
