# SPDX-License-Identifier: MIT
"""Ed25519 signatures for policy files.

The signature covers the exact file bytes. Any edit, including whitespace,
breaks it. The private key stays on the machine that signs. Verification
needs only the public key.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agentleash.errors import AgentleashError


def generate_keypair(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    private_path = directory / "policy.key"
    public_path = directory / "policy.pub"
    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def sign_file(policy_path: Path, private_key_path: Path) -> Path:
    payload = _read(policy_path)
    key = _private(private_key_path)
    signature = base64.b64encode(key.sign(payload)).decode("ascii")
    destination = signature_path(policy_path)
    destination.write_text(signature + "\n", encoding="utf-8")
    return destination


def verify_file(policy_path: Path, public_key_path: Path) -> None:
    payload = _read(policy_path)
    signature_file = signature_path(policy_path)
    if not signature_file.is_file():
        raise AgentleashError(f"no signature file at {signature_file}")
    raw = signature_file.read_text(encoding="utf-8").strip()
    try:
        signature = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise AgentleashError(f"{signature_file} is not a base64 signature") from exc
    public = _public(public_key_path)
    try:
        public.verify(signature, payload)
    except InvalidSignature as exc:
        raise AgentleashError(f"signature for {policy_path.name} does not match the public key") from exc


def signature_path(policy_path: Path) -> Path:
    return policy_path.with_name(policy_path.name + ".sig")


def _read(path: Path) -> bytes:
    if not path.is_file():
        raise AgentleashError(f"could not read {path}")
    return path.read_bytes()


def _private(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(_read(path), password=None)
    except (ValueError, TypeError) as exc:
        raise AgentleashError(f"{path} is not an Ed25519 private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise AgentleashError(f"{path} is not an Ed25519 private key")
    return key


def _public(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(_read(path))
    except (ValueError, TypeError) as exc:
        raise AgentleashError(f"{path} is not an Ed25519 public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise AgentleashError(f"{path} is not an Ed25519 public key")
    return key
