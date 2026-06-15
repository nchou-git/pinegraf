from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "database_url, expected",
    [
        ("postgresql://x:y@34.181.200.174:5432/pinegraf", "34.181.200.174"),
        ("postgresql://x:y@203.0.113.10:5432/pinegraf_test", "203.0.113.10"),
        ("postgresql://x:y@localhost:5432/pinegraf", "pinegraf"),
    ],
)
def test_conftest_refuses_prod_shaped_database_url(
    database_url: str,
    expected: str,
) -> None:
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = ""
    env["DATABASE_URL"] = database_url

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "tests/test_health.py"],
        capture_output=True,
        cwd=os.getcwd(),
        env=env,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "refusing to run tests against production" in result.stderr.lower()
    assert expected in result.stderr
