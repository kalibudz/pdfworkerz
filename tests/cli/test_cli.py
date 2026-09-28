"""COR-10: the pdfworkerz CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from cli.main import app
from engine.document import Document
from engine.fonts.style import extract_page_spans
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


@pytest.mark.feature("FNT-01")
def test_spans_command_prints_every_span_as_json(corpus: Corpus) -> None:
    result = runner.invoke(app, ["spans", str(corpus.simple), "0"])
    assert result.exit_code == 0
    traces = json.loads(result.stdout)
    assert len(traces) == 1
    assert traces[0]["style"]["text"] == "Hello, PDFWorkerz."


@pytest.mark.feature("FNT-01")
def test_spans_command_on_missing_file_fails_cleanly() -> None:
    result = runner.invoke(app, ["spans", "no/such/file.pdf"])
    assert result.exit_code != 0


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


@pytest.mark.feature("EDT-02")
def test_replace_command(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(
        app, ["replace", str(corpus.simple), "PDFWorkerz", "Editor", "--require-tier", "exact", "--out", str(out)]
    )
    assert result.exit_code == 0
    assert "1 edit(s) applied" in result.output
    assert out.exists()

    inspect_result = runner.invoke(app, ["inspect", str(out)])
    assert inspect_result.exit_code == 0


@pytest.mark.feature("EDT-02")
def test_replace_command_regex_and_case_insensitive(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(
        app,
        [
            "replace",
            str(corpus.simple),
            r"pdf\w+",
            "App",
            "--regex",
            "--case-insensitive",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0
    assert "1 edit(s) applied" in result.output


@pytest.mark.feature("EDT-02")
def test_replace_command_no_match_exits_cleanly(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(app, ["replace", str(corpus.simple), "NotThere", "X", "--out", str(out)])
    assert result.exit_code == 0
    assert "no matches found" in result.output


@pytest.mark.feature("EDT-02")
def test_replace_command_rejects_an_invalid_tier_name(corpus: Corpus, work_dir: Path) -> None:
    result = runner.invoke(
        app, ["replace", str(corpus.simple), "a", "b", "--require-tier", "bogus", "--out", str(work_dir / "o.pdf")]
    )
    assert result.exit_code != 0


@pytest.mark.feature("EDT-04")
def test_delete_command(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(app, ["delete", str(corpus.simple), "PDFWorkerz", "--out", str(out)])
    assert result.exit_code == 0
    assert "1 edit(s) applied" in result.output


@pytest.mark.feature("EDT-06")
def test_restyle_command_size_and_color(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(
        app,
        ["restyle", str(corpus.simple), "PDFWorkerz", "--size", "20", "--color", "1,0,0", "--out", str(out)],
    )
    assert result.exit_code == 0
    assert "1 edit(s) applied" in result.output


@pytest.mark.feature("EDT-06")
def test_restyle_command_rejects_a_malformed_color(corpus: Corpus, work_dir: Path) -> None:
    result = runner.invoke(
        app, ["restyle", str(corpus.simple), "PDFWorkerz", "--color", "not-a-color", "--out", str(work_dir / "o.pdf")]
    )
    assert result.exit_code != 0
    assert "color must be" in result.output


@pytest.mark.feature("EDT-03")
def test_insert_command(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(
        app,
        [
            "insert",
            str(corpus.simple),
            " Extra",
            "--position",
            "300,72",
            "--reference",
            "PDFWorkerz",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0
    assert "1 edit(s) applied" in result.output


@pytest.mark.feature("EDT-03")
def test_insert_command_rejects_a_malformed_position(corpus: Corpus, work_dir: Path) -> None:
    result = runner.invoke(
        app,
        [
            "insert",
            str(corpus.simple),
            "x",
            "--position",
            "not-a-point",
            "--reference",
            "PDFWorkerz",
            "--out",
            str(work_dir / "o.pdf"),
        ],
    )
    assert result.exit_code != 0


@pytest.mark.feature("COR-10")
def test_edit_commands_default_to_a_versioned_output_name(corpus: Corpus, work_dir: Path) -> None:
    import shutil

    source = work_dir / "doc.pdf"
    shutil.copy(corpus.simple, source)
    result = runner.invoke(app, ["replace", str(source), "PDFWorkerz", "Editor"])
    assert result.exit_code == 0
    assert (work_dir / "doc.edited.pdf").exists()
    assert source.read_bytes() == corpus.simple.read_bytes()  # the original is untouched (COR-08)


@pytest.mark.feature("COR-10")
def test_edit_commands_can_overwrite_the_original(corpus: Corpus, work_dir: Path) -> None:
    import shutil

    source = work_dir / "doc.pdf"
    shutil.copy(corpus.simple, source)
    result = runner.invoke(app, ["replace", str(source), "PDFWorkerz", "Editor", "--overwrite"])
    assert result.exit_code == 0
    assert f"saved -> {source}" in result.output


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


@pytest.mark.feature("COR-11")
def test_serve_command_is_registered() -> None:
    """Doesn't actually start the server (that would block forever) -- just
    confirms `serve` is wired up with its `--port` option. Checked against
    the Click command's own params, not rendered `--help` text: Rich wraps
    that text to the detected terminal width, which is narrow and
    inconsistent across CI runners/OSes and can split "--port" across
    lines, making a substring check on it flaky by environment rather than
    by behavior (confirmed failing this way on real CI before this fix)."""
    command = typer.main.get_command(app)
    serve_command = command.commands["serve"]
    assert "port" in {param.name for param in serve_command.params}


@pytest.mark.feature("EDT-07")
def test_copy_style_command(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(
        app,
        [
            "copy-style",
            str(corpus.bold_italic_standard),
            "--source",
            "Bold text",
            "--target",
            "Regular text",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    with Document.open(out) as document:
        fonts = {span.style.text: span.style.font for span in extract_page_spans(document.raw, 0)}
    assert fonts["Regular text"] == "Helvetica-Bold"


@pytest.mark.feature("EDT-07")
def test_copy_style_command_reports_a_missing_source(corpus: Corpus, work_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["copy-style", str(corpus.bold_italic_standard), "--source", "nope", "--target", "Regular text"],
    )
    assert result.exit_code != 0
    assert "no span on page 0 contains 'nope'" in result.output


@pytest.mark.feature("EDT-10")
def test_add_list_and_remove_link_commands(corpus: Corpus, work_dir: Path) -> None:
    linked = work_dir / "linked.pdf"
    result = runner.invoke(
        app,
        ["add-link", str(corpus.simple), "--over", "PDFWorkerz", "--uri", "https://example.com", "--out", str(linked)],
    )
    assert result.exit_code == 0, result.output

    listed = runner.invoke(app, ["links", str(linked)])
    assert listed.exit_code == 0
    assert json.loads(listed.output)[0]["uri"] == "https://example.com"

    unlinked = work_dir / "unlinked.pdf"
    result = runner.invoke(app, ["remove-link", str(linked), "0", "--out", str(unlinked)])
    assert result.exit_code == 0, result.output
    assert json.loads(runner.invoke(app, ["links", str(unlinked)]).output) == []


@pytest.mark.feature("EDT-10")
def test_add_link_command_needs_exactly_one_area(corpus: Corpus) -> None:
    result = runner.invoke(app, ["add-link", str(corpus.simple), "--uri", "https://example.com"])
    assert result.exit_code != 0
    assert "--over or --rect" in result.output


@pytest.mark.feature("EDT-10")
def test_add_link_command_refuses_an_unsafe_uri(corpus: Corpus, work_dir: Path) -> None:
    result = runner.invoke(
        app,
        [
            "add-link",
            str(corpus.simple),
            "--rect",
            "72,72,200,90",
            "--uri",
            "javascript:x",
            "--out",
            str(work_dir / "o.pdf"),
        ],
    )
    assert result.exit_code != 0
    assert "scheme" in result.output


@pytest.mark.feature("EDT-05")
def test_move_block_command(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    result = runner.invoke(
        app,
        [
            "move-block",
            str(corpus.paragraph),
            "--match",
            "line two",
            "--dy",
            "250",
            "--width",
            "150",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    with Document.open(out) as document:
        spans = extract_page_spans(document.raw, 0)
    moved = [span for span in spans if span.style.chars[0].origin[1] > 300]
    assert len(moved) > 3  # three lines, re-wrapped narrower, now lower on the page


@pytest.mark.feature("EDT-08")
def test_image_commands_insert_move_crop_replace_and_delete(corpus: Corpus, work_dir: Path) -> None:
    from PIL import Image

    picture = work_dir / "pic.png"
    Image.new("RGB", (20, 20), (0, 128, 255)).save(picture)
    steps = [
        ["insert-image", "{src}", str(picture), "--rect", "72,200,172,300"],
        ["move-image", "{src}", "0", "--rect", "200,400,300,500"],
        ["crop-image", "{src}", "0", "--rect", "200,400,250,450"],
        ["replace-image", "{src}", "0", str(picture)],
    ]
    src = corpus.simple
    for number, step in enumerate(steps):
        out = work_dir / f"step{number}.pdf"
        result = runner.invoke(app, [arg.format(src=src) for arg in step] + ["--out", str(out)])
        assert result.exit_code == 0, (step, result.output)
        src = out
    listed = json.loads(runner.invoke(app, ["images", str(src)]).output)
    assert [round(v) for v in listed[0]["rect"]] == [200, 400, 250, 450]

    final = work_dir / "final.pdf"
    assert runner.invoke(app, ["delete-image", str(src), "0", "--out", str(final)]).exit_code == 0
    assert json.loads(runner.invoke(app, ["images", str(final)]).output) == []


@pytest.mark.feature("EDT-08")
def test_delete_image_command_reports_a_bad_index(corpus: Corpus) -> None:
    result = runner.invoke(app, ["delete-image", str(corpus.simple), "3"])
    assert result.exit_code != 0
    assert "out of range" in result.output


@pytest.mark.feature("EDT-09")
def test_shape_commands_draw_edit_and_delete(corpus: Corpus, work_dir: Path) -> None:
    drawn = work_dir / "drawn.pdf"
    result = runner.invoke(
        app,
        [
            "draw-shape",
            str(corpus.simple),
            "ellipse",
            "--points",
            "72,200 272,300",
            "--fill",
            "1,0,0",
            "--out",
            str(drawn),
        ],
    )
    assert result.exit_code == 0, result.output
    listed = json.loads(runner.invoke(app, ["shapes", str(drawn)]).output)
    assert listed[0]["kind"] == "curve" and listed[0]["fill_color"] == [1.0, 0.0, 0.0]

    edited = work_dir / "edited.pdf"
    result = runner.invoke(
        app, ["edit-shape", str(drawn), "0", "--rect", "100,400,200,450", "--no-fill", "--out", str(edited)]
    )
    assert result.exit_code == 0, result.output
    shape = json.loads(runner.invoke(app, ["shapes", str(edited)]).output)[0]
    assert [round(v) for v in shape["rect"]] == [100, 400, 200, 450] and shape["fill_color"] is None

    deleted = work_dir / "deleted.pdf"
    assert runner.invoke(app, ["delete-shape", str(edited), "0", "--out", str(deleted)]).exit_code == 0
    assert json.loads(runner.invoke(app, ["shapes", str(deleted)]).output) == []


@pytest.mark.feature("EDT-09")
def test_draw_shape_command_rejects_bad_points(corpus: Corpus) -> None:
    result = runner.invoke(app, ["draw-shape", str(corpus.simple), "line", "--points", "72,200"])
    assert result.exit_code != 0
    assert "exactly 2" in result.output
