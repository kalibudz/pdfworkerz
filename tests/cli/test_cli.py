"""COR-10: the pdfworkerz CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app
from tests.corpus.build_corpus import Corpus

runner = CliRunner()


@pytest.mark.feature("COR-10")
def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()  # a real version string, not blank


@pytest.mark.feature("COR-10")
def test_inspect_command_prints_valid_json(corpus: Corpus) -> None:
    result = runner.invoke(app, ["inspect", str(corpus.multi_page)])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["page_count"] == corpus.multi_page_count


@pytest.mark.feature("COR-10")
def test_inspect_command_on_missing_file_fails_cleanly() -> None:
    result = runner.invoke(app, ["inspect", "no/such/file.pdf"])
    assert result.exit_code != 0


@pytest.mark.feature("COR-10")
def test_inspect_command_reports_password_errors_without_a_traceback(corpus: Corpus) -> None:
    result = runner.invoke(app, ["inspect", str(corpus.encrypted_aes_256)])
    assert result.exit_code == 1
    assert "password-protected" in result.output
    assert "Traceback" not in result.output


@pytest.mark.feature("COR-10")
def test_inspect_command_accepts_a_password(corpus: Corpus) -> None:
    result = runner.invoke(app, ["inspect", str(corpus.encrypted_aes_256), "--password", corpus.user_password])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["encryption"]["is_encrypted"] is True


@pytest.mark.feature("COR-10")
def test_render_command_writes_a_png(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "page.png"
    result = runner.invoke(app, ["render", str(corpus.simple), "0", "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert out.read_bytes().startswith(b"\x89PNG")


@pytest.mark.feature("COR-10")
def test_render_command_defaults_the_output_name(corpus: Corpus, work_dir: Path) -> None:
    import shutil

    source = work_dir / "doc.pdf"
    shutil.copy(corpus.simple, source)
    result = runner.invoke(app, ["render", str(source), "0"])
    assert result.exit_code == 0
    expected = work_dir / "doc.p0.png"
    assert expected.exists()


@pytest.mark.feature("COR-10")
def test_repair_command_reports_whether_repair_was_needed(corpus: Corpus, work_dir: Path) -> None:
    clean_out = work_dir / "clean.pdf"
    result = runner.invoke(app, ["repair", str(corpus.simple), "--out", str(clean_out)])
    assert result.exit_code == 0
    assert "no repair was needed" in result.output

    repaired_out = work_dir / "repaired.pdf"
    result2 = runner.invoke(app, ["repair", str(corpus.broken_truncated), "--out", str(repaired_out)])
    assert result2.exit_code == 0
    assert "repaired and saved" in result2.output
    assert repaired_out.exists()


@pytest.mark.feature("COR-10")
def test_cli_uses_the_same_op_classes_as_the_engine(corpus: Corpus) -> None:
    """The CLI must not duplicate logic: it applies the registered InspectOp/RenderPageOp."""
    from engine.document import Document
    from engine.ops.base import InspectOp

    result = runner.invoke(app, ["inspect", str(corpus.simple)])
    cli_report = json.loads(result.stdout)

    with Document.open(corpus.simple) as document:
        direct_report = InspectOp().apply(document)

    assert cli_report == json.loads(direct_report.model_dump_json())
