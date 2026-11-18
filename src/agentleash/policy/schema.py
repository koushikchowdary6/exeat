# SPDX-License-Identifier: MIT
"""JSON Schema for the policy document. The loader is the validator."""

from __future__ import annotations

POLICY_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://agentleash.local/schema/policy.json",
    "title": "Agent policy",
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "roles", "agents"],
    "properties": {
        "version": {"const": 1},
        "org": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "unit": {"type": "string"},
            },
        },
        "roles": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/role"},
        },
        "agents": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/agent"},
        },
    },
    "$defs": {
        "role": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"},
                "description": {"type": "string"},
                "inherits": {"type": ["string", "null"]},
                "tools": {"type": "array", "items": {"type": "string"}},
                "spend": {"$ref": "#/$defs/spend"},
                "recipients": {"$ref": "#/$defs/recipients"},
                "scopes": {"$ref": "#/$defs/scopes"},
                "approvals": {"type": "array", "items": {"$ref": "#/$defs/approval"}},
            },
        },
        "agent": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "role"],
            "properties": {
                "name": {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"},
                "role": {"type": "string"},
                "description": {"type": "string"},
                "spend": {"$ref": "#/$defs/spend"},
                "exclude_tools": {"type": "array", "items": {"type": "string"}},
                "scopes": {"$ref": "#/$defs/scopes"},
            },
        },
        "spend": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "currency": {"const": "USD"},
                "per_call": {"type": "number", "minimum": 0},
                "per_day": {"type": "number", "minimum": 0},
                "total": {"type": "number", "minimum": 0},
            },
        },
        "recipients": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "domains": {"type": "array", "items": {"type": "string"}},
                "addresses": {"type": "array", "items": {"type": "string"}},
            },
        },
        "scopes": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "files": {"$ref": "#/$defs/scope"},
                "urls": {"$ref": "#/$defs/scope"},
                "tables": {"$ref": "#/$defs/scope"},
            },
        },
        "scope": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "allow": {"type": "array", "items": {"type": "string"}},
                "deny": {"type": "array", "items": {"type": "string"}},
            },
        },
        "approval": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "tools", "reason", "when"],
            "properties": {
                "name": {"type": "string"},
                "tools": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "reason": {"type": "string", "minLength": 1},
                "when": {
                    "type": "object",
                    "properties": {
                        "always": {"type": "boolean"},
                        "arg": {"type": "string"},
                        "gt": {"type": "number", "minimum": 0},
                    },
                },
            },
        },
    },
}
