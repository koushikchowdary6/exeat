# SPDX-License-Identifier: Apache-2.0
from agentleash.policy.globmatch import covers, glob_match
from agentleash.policy.protected import PROTECTED_FILE_GLOBS


def test_glob_segments() -> None:
    assert glob_match("data/finance/**", "data/finance/invoices/INV-1042.json")
    assert glob_match("data/finance/**", "data/finance/a")
    assert not glob_match("data/finance/**", "data/finance-secret/a")
    assert not glob_match("data/finance/**", "data/other/a")
    assert glob_match("**/.env", ".env")
    assert glob_match("**/.env", "a/b/.env")
    assert not glob_match("**/.env", ".env.local")
    assert glob_match("**/.env.*", ".env.local")
    assert glob_match("**/*.pem", "keys/a.pem")
    assert glob_match("**/*credentials*", "aws-credentials.json")
    assert glob_match("**/*service-account*.json", "prod-service-account-key.json")
    assert glob_match("data/finance/**/*.raw", "data/finance/drop.raw")
    assert glob_match("data/finance/**/*.raw", "data/finance/a/drop.raw")
    assert not glob_match("data/finance/**/*.raw", "data/finance/drop.json")


def test_protected_patterns_catch_common_secrets() -> None:
    samples = {
        "secrets/api-keys.txt": True,
        "a/secrets/token": True,
        ".env": True,
        "app/.env": True,
        ".env.production": True,
        ".ssh/id_ed25519": True,
        "id_rsa": True,
        "certs/server.pem": True,
        "credentials.txt": True,
        "data/finance/invoices/INV-1042.json": False,
    }
    for path, expected in samples.items():
        matched = any(glob_match(pattern, path) for pattern in PROTECTED_FILE_GLOBS)
        assert matched is expected, path


def test_covers_is_conservative() -> None:
    assert covers("**", "data/finance/**")
    assert covers("data/**", "data/finance/**")
    assert covers("data/**", "data/finance/invoices/*.json")
    assert not covers("data/finance/**", "data/**")
    assert not covers("data/finance/**", "**")
    assert covers("finance.*", "finance.payroll")
    assert not covers("finance.invoices", "finance.payroll")
    assert covers(
        "https://ledger.internal.example/**",
        "https://ledger.internal.example/v1/invoices",
    )
    assert not covers("https://ledger.internal.example/**", "https://evil.test/**")
