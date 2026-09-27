from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gen_ops_schema  # noqa: E402


@pytest.mark.feature("COR-04")
def test_ops_schema_file_is_in_sync_with_the_registry() -> None:
    # Run in a fresh interpreter (exactly what CI does): other test modules in this
    # session register their own test-double Ops, which would otherwise make an
    # in-process registry check see Ops that were never meant to reach the schema file.
    result = subprocess.run(
        [sys.executable, "tools/gen_ops_schema.py", "--check"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.feature("COR-04")
def test_render_produces_valid_json_with_only_the_builtin_ops() -> None:
    import json

    schema = json.loads(gen_ops_schema.render())
    titles = {branch["title"] for branch in schema["oneOf"]}
    assert titles >= {"InspectOp", "RenderPageOp"}
