# Progress

Feature-level status lives in [`tracker/features.json`](tracker/features.json) and is set only by the evidence gate. This file records phase sign-offs and session notes.

## Phase checklist

| Phase | Scope | Status | Reviewer sign-off |
|---|---|---|---|
| P0 | Spec, tracker, CI workers, session protocol | ✅ Done: 5/5 features proven by tests | Pending: first green GitHub Actions run |
| P1 | Engine core, inspection, encryption, repair, CLI | ⏳ Next | |
| P2 | Font identification & style-matched text editing | Planned | |
| P3 | Web UI with click-to-edit | Planned | |
| P4 | Command bar & recipes | Planned | |
| P5 | Organize, page design, annotate, document structure | Planned | |
| P6 | Forms, signatures, security, redaction | Planned | |
| P7 | OCR, scans, conversions | Planned | |
| P8 | Optimize, compare, accessibility, batch, extras | Planned | |
| P9 | Packaging & documentation | Planned | |

A phase is complete when all of its features are **done** through the evidence gate, CI is green on `main`, and the reviewer agent has signed off here.

## Standing review items

- [ ] Re-verify the iLovePDF and Nitro PDF Pro parity tags against the vendors' current feature pages before the first release.
- [ ] Pin engine dependency versions in `pyproject.toml` when P1 starts, and add API-contract tests for every library call.

## Session log

### 2026-09-26 — P0 bootstrap

- Wrote SPEC.md (16 sections, 161 features across 21 categories, parity matrix, roadmap).
- Built the evidence gate (`tools/update_tracker.py`), the spec generator (`tools/gen_spec_catalog.py`) and the token look-ahead tool (`tools/session_budget.py`).
- Added the pytest evidence plugin (`tests/conftest.py`): 34 tests pass, with 93% coverage of `tools/`.
- Built the live tracker (`tracker/`) in React + Vite, plus a single-file artifact build.
- Added the CI workflow: lint, types, 3-OS × 2-Python test matrix, security, spec sync, tracker build.
- Next: P1, starting with INF-06 (golden corpus generator) and COR-01 (open/parse).
