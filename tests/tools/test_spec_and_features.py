from __future__ import annotations

import copy
import re

import pytest

import features
import gen_spec_catalog

REQUIRED_SPEC_SECTIONS = [
    "## 1. Overview",
    "## 3. Feature parity summary",
    "## 4. Architecture",
    "## 5. Text & font identification engine (flagship)",
    "## 6. Encrypted and difficult PDFs",
    "## 7. Feature catalog",
    "## 8. User interface",
    "## 9. Operation model & recipes",
    "## 11. Workers: who builds and who reviews",
    "## 12. Quality & anti-hallucination process",
    "## 13. Session continuity & token look-ahead",
    "## 14. Roadmap",
]


@pytest.fixture(scope="module")
def data() -> dict:
    return features.load()


@pytest.mark.feature("INF-02")
def test_features_json_is_valid(data: dict) -> None:
    assert features.validate(data) == []


@pytest.mark.feature("INF-02")
def test_every_category_and_phase_has_features(data: dict) -> None:
    used_categories = {f["category"] for f in data["features"]}
    used_phases = {f["phase"] for f in data["features"]}
    assert used_categories == {c["id"] for c in data["categories"]}
    assert used_phases == {p["id"] for p in data["phases"]}


@pytest.mark.feature("INF-02")
def test_validate_reports_bad_entries(data: dict) -> None:
    broken = copy.deepcopy(data)
    broken["features"][0]["phase"] = "P99"
    broken["features"][1]["id"] = broken["features"][0]["id"]
    broken["features"][2]["parity"] = ["Adobe"]
    del broken["features"][3]["libs"]
    errors = features.validate(broken)
    assert any("unknown phase P99" in e for e in errors)
    assert any("duplicate id" in e for e in errors)
    assert any("parity" in e for e in errors)
    assert any("missing keys ['libs']" in e for e in errors)


@pytest.mark.feature("INF-02")
def test_dumps_round_trips(data: dict) -> None:
    import json

    assert json.loads(features.dumps(data)) == data


@pytest.mark.feature("INF-01")
def test_spec_has_required_sections() -> None:
    spec = gen_spec_catalog.SPEC_PATH.read_text(encoding="utf-8")
    for heading in REQUIRED_SPEC_SECTIONS:
        assert heading in spec, heading


@pytest.mark.feature("INF-01")
def test_spec_catalog_is_in_sync(data: dict) -> None:
    spec = gen_spec_catalog.SPEC_PATH.read_text(encoding="utf-8")
    assert gen_spec_catalog.regenerate(spec, data) == spec, "run python tools/gen_spec_catalog.py"


@pytest.mark.feature("INF-01")
def test_every_feature_id_appears_in_spec_exactly_once(data: dict) -> None:
    spec = gen_spec_catalog.SPEC_PATH.read_text(encoding="utf-8")
    catalog = spec.split("<!-- BEGIN GENERATED: catalog -->")[1].split("<!-- END GENERATED: catalog -->")[0]
    ids_in_catalog = re.findall(r"^\| ([A-Z]{2,3}-\d{2}) \|", catalog, flags=re.MULTILINE)
    assert sorted(ids_in_catalog) == sorted(f["id"] for f in data["features"])


@pytest.mark.feature("INF-01")
def test_regenerate_rejects_missing_markers(data: dict) -> None:
    with pytest.raises(ValueError, match="markers"):
        gen_spec_catalog.regenerate("# no markers here\n", data)


@pytest.mark.feature("INF-01")
def test_gen_check_mode_passes_on_current_spec() -> None:
    assert gen_spec_catalog.main(["--check"]) == 0
