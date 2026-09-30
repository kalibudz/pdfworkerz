# PDFWorkerz — Product & Technical Specification

| | |
|---|---|
| **Version** | 1.0 (Phase P0) |
| **Date** | 2026-09-26 |
| **Owner** | kalibudz |
| **License** | AGPL-3.0-or-later |
| **Repository** | https://github.com/kalibudz/pdfworkerz |
| **Status tracker** | [`tracker/features.json`](tracker/features.json), rendered by [`tracker/FeatureTracker.jsx`](tracker/src/FeatureTracker.jsx) |

---

## 1. Overview

PDFWorkerz is a **free, open-source, offline PDF editing worker** for engineers. It edits, formats, organizes, converts, secures and verifies PDFs with the capability of a top-tier "PDF specialist" AI agent. It uses **no AI tokens and no LLM calls**: every operation is deterministic code built on proven open-source PDF libraries.

Its flagship capability is **style-faithful text editing**. When a word is changed, the replacement is drawn with the original font program, size, color, character and word spacing, horizontal scaling, baseline, rise and render mode. The result looks as if the document had been authored with the new text. Every edit is verified by rendering the page before and after and comparing the pixels.

### 1.1 Goals

1. **Free forever.** AGPL-3.0, no paid SDKs, no accounts, no usage metering.
2. **Zero AI tokens.** No network calls to AI services, and no user data leaves the machine.
3. **Match the premium tools and go further.** Every advertised iLovePDF and Nitro PDF Pro capability that can run locally, plus the "Beyond" features in §3.
4. **Handle every kind of PDF.** PDF 1.0–2.0, encrypted, restricted, signed, damaged, scanned, CJK/RTL, 1,000+ pages.
5. **Human-directed.** The user says what to change by clicking on the page or typing a plain-English command. The worker never acts on a guess.
6. **Deterministic and reproducible.** The same input and the same operations always produce the same output. Every session can be saved as a replayable recipe.
7. **Evidence over claims.** A feature counts as done only when its linked automated tests pass (§12).

### 1.2 Non-goals

- Cloud storage, accounts, collaboration servers or hosted signing workflows (for example "request signatures by email").
- Any LLM or remote AI inference. The optional offline translation (XTR-01) uses a local model file and no tokens.
- Password guessing, cracking or brute force. Encrypted files are opened only with a password the user supplies.
- Bypassing DRM or rights-management schemes.

## 2. Users & use cases

| Persona | Typical jobs |
|---|---|
| **Engineer / technical author** | Fix a typo or value in a datasheet, bump revision letters and dates on drawings, update part numbers throughout a manual, re-number pages after inserting a section |
| **Project / document controller** | Merge transmittal packs, stamp "Approved"/"Superseded", apply Bates numbers, add headers and footers, produce PDF/A for archive |
| **Compliance / legal support** | True redaction of PII with pattern packs, metadata scrubbing, compare revisions, sign with certificates |
| **Operations / admin** | Fill and flatten forms, convert Office files to and from PDF, compress for email, OCR scanned records |
| **Automation engineer** | Run recipes over folders from the CLI in CI or scheduled jobs |

## 3. Feature parity summary

The parity tags reflect features that iLovePDF and Nitro PDF Pro advertise publicly. **Tags must be re-verified against the vendors' current feature pages before each release** (tracked as a review item in PROGRESS.md). "Beyond" marks capabilities neither product advertises, or PDFWorkerz-specific infrastructure.

<!-- BEGIN GENERATED: parity -->
| Benchmark | Features covered | Feature IDs |
|---|---|---|
| iLovePDF | 48 | COR-01, COR-02, SEC-01, SEC-04, SEC-05, SEC-08, EDT-01, EDT-03, EDT-04, EDT-08, EDT-09, UI-01, UI-02, UI-06, UI-07, ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, ORG-09, DES-01, DES-04, ANN-01, ANN-03, SIG-01, OCR-01, OCR-02, OCR-04, CVF-01, CVF-02, CVF-03, CVF-04, CVF-07, CVT-01, CVT-02, CVT-03, CVT-04, CVT-05, OPT-01, OPT-02, OPT-06, CMP-01, CMP-02, XTR-01, XTR-02 |
| Nitro PDF Pro | 106 | COR-01, COR-02, COR-05, COR-06, COR-07, COR-09, COR-12, SEC-01, SEC-02, SEC-03, SEC-04, SEC-05, SEC-06, SEC-07, SEC-08, SEC-09, SEC-10, FNT-11, FNT-14, FNT-17, FNT-18, EDT-01, EDT-02, EDT-03, EDT-04, EDT-05, EDT-06, EDT-08, EDT-09, EDT-10, EDT-11, EDT-13, EDT-14, EDT-15, UI-01, UI-02, UI-04, UI-06, UI-07, UI-08, UI-09, CMD-07, ORG-01, ORG-02, ORG-03, ORG-04, ORG-05, ORG-06, ORG-07, ORG-08, ORG-09, ORG-10, DES-01, DES-02, DES-03, DES-04, DES-05, DES-06, ANN-01, ANN-02, ANN-03, ANN-04, ANN-05, ANN-06, DOC-01, DOC-02, DOC-04, DOC-05, FRM-01, FRM-02, FRM-03, FRM-04, FRM-05, FRM-06, FRM-07, SIG-01, SIG-02, SIG-03, SIG-04, OCR-01, OCR-02, OCR-03, CVF-01, CVF-02, CVF-03, CVF-04, CVF-05, CVF-07, CVF-08, CVT-01, CVT-02, CVT-03, CVT-04, CVT-05, OPT-01, OPT-02, OPT-03, OPT-04, OPT-05, CMP-01, CMP-02, CMP-03, ACC-01, ACC-02, BAT-01, XTR-03 |
| Beyond both | 57 | INF-01, INF-02, INF-03, INF-04, INF-05, INF-06, INF-07, INF-08, INF-09, INF-10, COR-03, COR-04, COR-08, COR-10, COR-11, SEC-11, FNT-01, FNT-02, FNT-03, FNT-04, FNT-05, FNT-06, FNT-07, FNT-08, FNT-09, FNT-10, FNT-12, FNT-13, FNT-15, FNT-16, EDT-07, EDT-12, UI-03, UI-05, CMD-01, CMD-02, CMD-03, CMD-04, CMD-05, CMD-06, CMD-08, ORG-11, ORG-12, ORG-13, DOC-03, FRM-08, SIG-05, SIG-06, CVF-06, CVT-06, OPT-07, ACC-03, ACC-04, ACC-05, BAT-02, BAT-03, BAT-04 |

Total features: **167**.
<!-- END GENERATED: parity -->

**Why PDFWorkerz goes beyond both products:**

- **Font forensics:** a per-span style inspector with a match-confidence score.
- **Pixel-verified edits:** every edit is diffed and flagged if it deviates from the expected result.
- **Deterministic command bar:** type what you want, preview it, apply it, replay it.
- **Recipes and CLI:** every UI action is scriptable and repeatable.
- **Redaction proof:** after saving, the text is re-extracted to prove the redacted content is gone.
- **Offline and free:** no uploads, no subscription, no tokens.

## 4. Architecture

```
┌──────────────────────── Browser UI (localhost only) ────────────────────────┐
│  pdf.js canvas · thumbnail rail · click-to-edit overlay · inspector panel   │
│  command bar (autocomplete, preview) · history · before/after split view    │
└──────────────┬───────────────────────────────────────────────▲──────────────┘
               │ JSON Ops / previews (HTTP on 127.0.0.1)        │ renders, reports
┌──────────────▼────────────────────────────────────────────────┴──────────────┐
│  FastAPI server  ── same Op layer ──  CLI (pdfworkerz --do / --recipe)       │
├──────────────────────────────────────────────────────────────────────────────┤
│  Command parser (Lark grammar → Ops)      Recipe loader (YAML/JSON → Ops)    │
├──────────────────────────────────────────────────────────────────────────────┤
│  Operation layer: typed Ops · validation · preview · undo/redo journal       │
├───────────────┬───────────────┬──────────────┬───────────────┬──────────────┤
│ Font & text   │ Page / org    │ Security &   │ Forms & sign  │ Convert, OCR │
│ intelligence  │ design, annot │ redaction    │               │ optimize,cmp │
├───────────────┴───────────────┴──────────────┴───────────────┴──────────────┤
│  Document model: open/auth/repair · object access · incremental & full save │
│  PyMuPDF · pikepdf/qpdf · fontTools · pdfplumber · OCRmyPDF · pyHanko · …    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Technology stack (all free and open source)

| Concern | Library / tool | License | Used for |
|---|---|---|---|
| Core PDF engine | **PyMuPDF** (MuPDF) | AGPL-3.0 | Rendering, text and font extraction, text insertion, redaction, annotations, forms, page operations |
| Low-level object access | **pikepdf** (qpdf) | MPL-2.0 | Content-stream parsing and rewriting, encryption (RC4/AES, R2–R6), repair, linearization, tag trees |
| Fonts | **fontTools** | MIT | Glyph coverage, metrics, subsetting, glyph merging, kerning and ligature tables |
| Layout and tables | **pdfplumber**, **camelot** | MIT | Table detection, PDF → Excel |
| OCR | **OCRmyPDF** + **Tesseract** | MPL-2.0 / Apache-2.0 | Searchable PDF, deskew, PDF/A |
| Office → PDF | **LibreOffice** (headless) | MPL-2.0 | Word, Excel and PowerPoint → PDF |
| PDF → Word | **pdf2docx** | GPL-3.0 | .docx reconstruction |
| PDF → PowerPoint | **python-pptx** | MIT | .pptx generation |
| HTML → PDF | **WeasyPrint** | BSD-3 | HTML/URL → PDF |
| Compression / PDF-A | **Ghostscript** | AGPL-3.0 | Aggressive compression, grayscale, PDF/A |
| Digital signatures | **pyHanko**, **cryptography** | MIT / Apache-2.0 | PAdES signing and validation, timestamps, local certificates |
| Images | **Pillow**, **OpenCV**, **numpy** | HPND / Apache-2.0 / BSD | Image operations, scan clean-up, pixel diffs |
| Spell-check | **spylls** (Hunspell port) + LibreOffice dictionaries | MPL-2.0 | Offline spell-check |
| Command grammar | **Lark**, **rapidfuzz** | MIT | Deterministic parsing, "did you mean" suggestions |
| Server / CLI | **FastAPI**, **uvicorn**, **Typer**, **pydantic** | MIT/BSD | Local API, CLI, Op schemas |
| Viewer | **pdf.js** | Apache-2.0 | In-browser rendering and text layer |
| Optional | **Argos Translate**, **veraPDF** | MIT / GPL-3.0 & MPL-2.0 | Offline translation, PDF/A validation |

Library APIs named in this spec must be confirmed against the **installed** version before use. The builder pins versions in `pyproject.toml` and writes a test for every API it relies on (§12).

### 4.2 Core design rules

1. **Everything is an Op.** Clicks, commands, CLI flags and recipes all produce the same typed, serializable `Op` objects (§9). There is exactly one code path per capability.
2. **Preview, then apply.** Each Op can render a preview of the affected pages before it touches the file.
3. **The original is sacred.** Output is written to `name.edited.pdf` (or a versioned name) unless the user explicitly chooses to overwrite.
4. **Incremental save by default** when the document is signed or the user asks for it. Otherwise do a full rewrite with garbage collection.
5. **Local only.** The server binds to `127.0.0.1` and generates a random session token. No telemetry.

## 5. Text & font identification engine (flagship)

### 5.1 What is extracted for every text span

| Property | Source |
|---|---|
| Unicode text, bbox, baseline origin, writing direction | `Page.get_text("rawdict")`, `Page.get_texttrace()` (PyMuPDF) |
| Font resource name, BaseFont, subset tag (`ABCDEF+`), font type (Type1, TrueType, CFF, Type3, CID/Type0), embedded or not | `Page.get_fonts(full=True)`, font dictionaries via pikepdf |
| Encoding, `/Differences`, ToUnicode CMap, CID system info | Font dictionary + CMap parsing (pikepdf) |
| Font size, text matrix (scale, rotation, skew), CTM | Span size plus the `Tm`/`cm` operators in the content stream |
| Character spacing `Tc`, word spacing `Tw`, horizontal scaling `Tz`, leading `TL`, rise `Ts`, render mode `Tr` | Content-stream operator parsing (pikepdf `parse_content_stream`), cross-checked against glyph positions from `get_texttrace()` |
| Fill and stroke color and color space, opacity (`ExtGState` `ca`/`CA`), stroke width | Texttrace color and opacity plus graphics-state parsing |
| Per-glyph advance widths, kerning (`TJ` adjustments) | `/Widths` or `/W` arrays, TJ arrays |
| Font program bytes | `Document.extract_font(xref)` |

### 5.2 Style fingerprint

The engine builds a fingerprint for each font in the document. Weight and italic are taken from the font descriptor flags and `/FontWeight`, `/ItalicAngle` and `/StemV`, from name tokens (such as "Bold", "Semibold", "It", "Oblique" or "MT"), and from measured stem widths. Serif, sans or mono is classified from the glyph outlines (fontTools) and fixed-pitch flags. The fingerprint also records cap height, x-height, ascender, descender and average advance width. Identical fonts that are embedded several times as different subsets are grouped into one family entry.

### 5.3 Replacement strategy (in priority order)

For every edit the engine picks the first strategy that works and tells the user which one it used:

| Tier | Strategy | When it applies | Confidence |
|---|---|---|---|
| **1. Stream surgery** | Re-encode the new string through the font's own encoding or CMap and rewrite the operands of the `Tj`/`TJ` operators in place. All graphics state is untouched. | Every needed glyph exists in the embedded (possibly subset) font | **Exact** |
| **2. Glyph borrow** | Find the complete font (system fonts, then the bundled OFL library, using normalized names). Merge the missing glyphs into a new subset with fontTools, embed it, and draw the text with the original state values. | The glyphs are missing from the subset, but the same font family is available | **High** |
| **3. Metric match** | Rank candidate fonts by width-table correlation, cap height, x-height, stem width and serif class. Use the best match. | The original font is unavailable | **Approximate**. The user must approve before it is applied |
| **4. Fallback** | Use the closest standard-14 or bundled font and flag it clearly | Nothing better is available | **Low**. The user must approve before it is applied |

Tiers 2–4 remove the original glyphs with a text-only redaction (`apply_redactions` with images and graphics preserved). They then insert the new text at the original baseline origin with the extracted size, color, render mode, spacing, scaling and rotation.

### 5.4 Fitting the replacement

- **Same width:** no change needed.
- **Slightly longer or shorter** (default ≤ ±6%): adjust `Tc` tracking first, then `Tz` scaling within 94–106%.
- **Much longer:** reflow within the detected text block (line, paragraph or column). Justification, leading, indents and hyphenation rules are preserved, and later lines on the same page are shifted if needed.
- **Would overflow the block:** stop and show the overflow in the preview. The user can choose to shrink, reflow or cancel.

### 5.5 Special cases

- **Missing ToUnicode:** recover characters from glyph names (AGL), from the font's `cmap`, and finally from shape matching against a reference-rendered glyph set (FNT-13).
- **CJK, RTL and vertical text:** CID fonts and `Identity-H` encoding for horizontal CJK text (FNT-14). Right-to-left (bidi ordering) and vertical writing (`Identity-V`) are planned as FNT-18.
- **Type3 fonts:** reuse the existing glyph procedures when they cover the new characters. Otherwise use Tier 3 with an explicit warning (FNT-15).
- **Scanned pages:** run OCR to get words and boxes. Estimate the font class, size and color. Rebuild the background patch by inpainting, then draw the matched text (FNT-16, EDT-12).

### 5.6 Verification (every edit)

1. Render the edited region before and after at 2× the target DPI.
2. Check that nothing outside the edit mask changed (≤ 0.1% of pixels differ, to allow for anti-aliasing noise).
3. Check that text extracted after the edit equals the intended string.
4. For Tiers 1–2, re-render the new glyphs in isolation and compare their stroke density with neighbouring original glyphs.
5. If any check fails, the edit is flagged in the history panel and is not auto-saved.

## 6. Encrypted and difficult PDFs

| Case | Behavior |
|---|---|
| **User-password encryption** — RC4 40-bit (R2), RC4 128-bit (R3), AES-128 (R4), AES-256 (R5/R6) | Prompt for the password (masked, never logged or written to disk). Open with `pikepdf.open(password=…)` / `Document.authenticate()`. Edit normally. |
| **Owner-password-only (restricted)** | Opens without a prompt, as in any viewer. The permission flags are shown. Editing, printing or copying against a restriction requires the owner password, or an explicit confirmation from the user that they are authorized to modify the document. That confirmation is recorded in the recipe log. |
| **Saving an encrypted file** | Default: keep the original algorithm and passwords. Options: re-encrypt with AES-256, or remove encryption (requires the owner password). |
| **Certificate / public-key encryption** (`/Adobe.PubSec`) | Detected and reported. Opening requires the recipient's private key, which is out of scope for v1 and flagged in the inspector. |
| **Digitally signed** | The signatures are listed and validated. Editing warns that it invalidates signatures, and incremental-save mode keeps earlier revisions intact. |
| **Damaged** (broken xref, truncated, bad streams) | Automatic repair (qpdf reconstruction, then MuPDF repair). The repair log is shown to the user. |
| **Other** | PDF/A, PDF/X, linearized, tagged, portfolios/collections, XFA (detected, with a static fallback or flatten), optional-content layers, 1,000+ pages (lazy loading, streamed rendering) |

No password guessing or cracking is ever performed.

## 7. Feature catalog

Every feature has a stable ID. The table below is **generated** from `tracker/features.json` by `tools/gen_spec_catalog.py`, and CI fails if it drifts. Size is the look-ahead estimate in tokens used for build planning: **S** ≈ 50k, **M** ≈ 150k, **L** ≈ 400k.

<!-- BEGIN GENERATED: catalog -->
#### INF — Infrastructure & quality (10)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| INF-01 | Specification document (SPEC.md) with generated feature catalog | Beyond | Python | P0 | S |
| INF-02 | Live feature tracker (React JSX) driven by features.json | Beyond | React, Vite | P0 | S |
| INF-03 | Token-free review workers (lint, types, tests, security, tracker gate), run locally | Beyond | tools/gate.py, ruff, mypy, pytest, bandit, pip-audit | P0 | S |
| INF-04 | Session checkpoint & token look-ahead budgeting | Beyond | Python | P0 | S |
| INF-05 | Evidence gate: a feature is done only when its linked tests pass | Beyond | pytest | P0 | S |
| INF-06 | Golden PDF test corpus generator (fonts, encryption, forms, scans, broken files) | Beyond | PyMuPDF, pikepdf, reportlab | P1 | M |
| INF-07 | Pixel-diff visual regression harness | Beyond | PyMuPDF, numpy | P1 | M |
| INF-08 | Cross-platform packaging (pipx install, Windows/macOS/Linux bundles) | Beyond | PyInstaller | P9 | L |
| INF-09 | User guide and developer documentation | Beyond | MkDocs | P9 | M |
| INF-10 | Automatic resume after a session ends abruptly (in-progress mark, recovery report) | Beyond | Python | P0 | S |

#### COR — Core engine & document handling (12)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| COR-01 | Open and parse PDF 1.0-2.0 files | iLovePDF, Nitro | PyMuPDF, pikepdf | P1 | M |
| COR-02 | Render pages to raster at any DPI | iLovePDF, Nitro | PyMuPDF | P1 | S |
| COR-03 | Document inspection report (version, fonts, images, encryption, forms, signatures, layers) | Beyond | PyMuPDF, pikepdf | P1 | M |
| COR-04 | Typed, serializable operation (Op) model with JSON schema | Beyond | pydantic | P1 | M |
| COR-05 | Undo/redo journal | Nitro | Python | P1 | M |
| COR-06 | Incremental save preserving untouched objects | Nitro | PyMuPDF, pikepdf | P1 | M |
| COR-07 | Full rewrite save with garbage collection | Nitro | PyMuPDF, pikepdf | P1 | S |
| COR-08 | Never overwrite the original by default (versioned outputs) | Beyond | Python | P1 | S |
| COR-09 | Large-document handling (1,000+ pages, lazy page loading) | Nitro | PyMuPDF | P1 | M |
| COR-10 | CLI entry point (pdfworkerz) | Beyond | Typer | P1 | M |
| COR-11 | Local-only HTTP API server (127.0.0.1) | Beyond | FastAPI, uvicorn | P3 | M |
| COR-12 | Optional content (layers/OCG) read and toggle | Nitro | PyMuPDF | P5 | S |

#### SEC — Security, encryption & redaction (11)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| SEC-01 | Open encrypted PDFs with user password (RC4 40/128, AES-128, AES-256 R5/R6) | iLovePDF, Nitro | PyMuPDF, pikepdf | P1 | M |
| SEC-02 | Open owner-restricted PDFs and report permission flags | Nitro | pikepdf | P1 | S |
| SEC-03 | Save edits while preserving the original encryption settings | Nitro | pikepdf | P1 | M |
| SEC-04 | Protect: add password with AES-256 encryption | iLovePDF, Nitro | pikepdf | P6 | S |
| SEC-05 | Unlock: remove encryption when the valid password is supplied | iLovePDF, Nitro | pikepdf | P6 | S |
| SEC-06 | Set document permissions (print, copy, modify, annotate) | Nitro | pikepdf | P6 | S |
| SEC-07 | Detect and report certificate (public-key) encryption | Nitro | pikepdf | P1 | S |
| SEC-08 | True redaction (removes text, images and vectors under the mark) | iLovePDF, Nitro | PyMuPDF | P6 | M |
| SEC-09 | Redaction pattern packs (email, phone, SSN, card numbers, custom regex) | Nitro | PyMuPDF | P6 | S |
| SEC-10 | Sanitize: remove metadata, XMP, JavaScript, embedded files, hidden text | Nitro | PyMuPDF, pikepdf | P6 | M |
| SEC-11 | Redaction verification (re-extract after save proves removal) | Beyond | PyMuPDF | P6 | S |

#### FNT — Font & text intelligence (18)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| FNT-01 | Per-span style extraction (font, size, color, flags, origin, bbox) | Beyond | PyMuPDF | P2 | M |
| FNT-02 | Per-character trace (char/word spacing, scaling, rise, render mode, opacity, matrix) | Beyond | PyMuPDF, pikepdf | P2 | M |
| FNT-03 | Font classification (type, embedding, subset tag, encoding, ToUnicode) | Beyond | PyMuPDF, pikepdf | P2 | M |
| FNT-04 | Style fingerprint (weight, italic, serif/sans/mono inference) | Beyond | fontTools | P2 | M |
| FNT-05 | Subset glyph-coverage check for replacement text | Beyond | fontTools | P2 | S |
| FNT-06 | Full-font lookup (system fonts + bundled OFL library, name normalization) | Beyond | fontTools | P2 | M |
| FNT-07 | Metric-similarity font matching with confidence score | Beyond | fontTools, numpy | P2 | L |
| FNT-08 | Glyph-borrow merge into the embedded subset font | Beyond | fontTools | P2 | L |
| FNT-09 | Kerning and ligature preservation | Beyond | fontTools | P2 | M |
| FNT-10 | Fit-to-width (tracking or horizontal scaling within tolerance) | Beyond | PyMuPDF | P2 | M |
| FNT-11 | Paragraph reflow within its own lines, keeping the baseline | Nitro | PyMuPDF | P2 | L |
| FNT-12 | Pixel-verified edit (before/after region diff with mismatch flag) | Beyond | PyMuPDF, numpy | P2 | M |
| FNT-13 | Missing-ToUnicode recovery (glyph names, shape matching) | Beyond | fontTools, PyMuPDF | P2 | L |
| FNT-14 | CJK text editing (horizontal) | Nitro | PyMuPDF, fontTools | P2 | L |
| FNT-15 | Type3 font handling (detect, reuse glyph procedures or fall back) | Beyond | pikepdf | P2 | L |
| FNT-16 | Scanned-text style estimation (font class, size, color) | Beyond | Tesseract, OpenCV | P7 | L |
| FNT-17 | Justified reflow, and moving later content when a paragraph grows | Nitro | PyMuPDF | P5 | L |
| FNT-18 | Right-to-left and vertical text editing | Nitro | PyMuPDF, fontTools | P8 | L |

#### EDT — Edit content (15)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| EDT-01 | Inline text edit that matches the original style | iLovePDF, Nitro | PyMuPDF, fontTools | P2 | L |
| EDT-02 | Find and replace (literal, regex, whole word, case sensitive) | Nitro | PyMuPDF | P2 | M |
| EDT-03 | Add new text with 'match style of' or explicit style | iLovePDF, Nitro | PyMuPDF | P2 | M |
| EDT-04 | Delete text | iLovePDF, Nitro | PyMuPDF | P2 | S |
| EDT-05 | Move and resize text blocks | Nitro | PyMuPDF | P3 | M |
| EDT-06 | Change font, size, color and weight of existing text | Nitro | PyMuPDF, fontTools | P2 | M |
| EDT-07 | Format painter (copy style from one span to another) | Beyond | PyMuPDF | P3 | S |
| EDT-08 | Insert, replace, resize, crop and delete images | iLovePDF, Nitro | PyMuPDF, Pillow | P3 | M |
| EDT-09 | Draw and edit vector shapes and lines | iLovePDF, Nitro | PyMuPDF | P3 | M |
| EDT-10 | Add, edit and remove hyperlinks | Nitro | PyMuPDF | P3 | S |
| EDT-11 | Offline spell-check with Hunspell dictionaries | Nitro | spylls | P3 | M |
| EDT-12 | Edit text on scanned pages (OCR + background patch reconstruction) | Beyond | Tesseract, OpenCV, PyMuPDF | P7 | L |
| EDT-13 | Select several objects (Shift+click, marquee); align and distribute text blocks, images and shapes | Nitro | PyMuPDF, TypeScript | P5 | M |
| EDT-14 | Smart guides that snap dragged objects to other objects and the page; arrow-key nudging | Nitro | TypeScript | P5 | M |
| EDT-15 | Copy, paste, duplicate and delete page objects (text blocks, images, shapes), across pages | Nitro | PyMuPDF, TypeScript | P5 | M |

#### UI — User interface (9)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| UI-01 | Page canvas with thumbnail rail | iLovePDF, Nitro | pdf.js | P3 | M |
| UI-02 | Click-to-edit overlay styled with the detected font | iLovePDF, Nitro | pdf.js, TypeScript | P3 | L |
| UI-03 | Inspector panel (detected font, size, color, spacing, confidence) | Beyond | TypeScript | P3 | M |
| UI-04 | History panel with undo/redo | Nitro | TypeScript | P3 | S |
| UI-05 | Before/after split view | Beyond | pdf.js | P3 | M |
| UI-06 | Password prompt for encrypted files | iLovePDF, Nitro | TypeScript | P3 | S |
| UI-07 | Drag-and-drop page organizer | iLovePDF, Nitro | TypeScript | P5 | M |
| UI-08 | Keyboard shortcuts and accessible UI | Nitro | TypeScript | P3 | S |
| UI-09 | Light and dark themes | Nitro | CSS | P3 | S |

#### CMD — Command bar & recipes (8)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| CMD-01 | Deterministic plain-English command grammar | Beyond | Lark | P4 | M |
| CMD-02 | Target selectors ("text", /regex/, page ranges, odd/even, all pages) | Beyond | Lark | P4 | M |
| CMD-03 | Style modifiers (match style, bold, size, color) | Beyond | Lark | P4 | S |
| CMD-04 | Autocomplete and inline syntax help | Beyond | TypeScript | P4 | M |
| CMD-05 | Preview before apply | Beyond | PyMuPDF | P4 | M |
| CMD-06 | 'Did you mean' suggestions; never guesses silently | Beyond | rapidfuzz | P4 | S |
| CMD-07 | Recipes: save history as YAML/JSON and replay | Nitro | PyYAML | P4 | M |
| CMD-08 | CLI --do "<command>" and --recipe <file> | Beyond | Typer | P4 | S |

#### ORG — Organize pages (13)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| ORG-01 | Merge PDFs | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-02 | Split (ranges, every N pages, by size, by bookmarks) | iLovePDF, Nitro | PyMuPDF | P5 | M |
| ORG-03 | Reorder pages | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-04 | Rotate pages | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-05 | Delete pages | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-06 | Extract pages to a new file | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-07 | Insert blank pages or pages from another file | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-08 | Duplicate pages | Nitro | PyMuPDF | P5 | S |
| ORG-09 | Crop pages (visual selection or margins) | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ORG-10 | Resize pages (A4/Letter/custom, scale content) | Nitro | PyMuPDF | P5 | M |
| ORG-11 | N-up imposition | Beyond | PyMuPDF | P5 | M |
| ORG-12 | Booklet imposition | Beyond | PyMuPDF | P5 | M |
| ORG-13 | Blank-page detection and removal | Beyond | PyMuPDF, numpy | P5 | S |

#### DES — Page design (6)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| DES-01 | Page numbers (position, format, start value, skip pages) | iLovePDF, Nitro | PyMuPDF | P5 | S |
| DES-02 | Bates numbering | Nitro | PyMuPDF | P5 | M |
| DES-03 | Headers and footers | Nitro | PyMuPDF | P5 | S |
| DES-04 | Text and image watermarks (opacity, rotation, behind/over content) | iLovePDF, Nitro | PyMuPDF | P5 | S |
| DES-05 | Page backgrounds (color or image) | Nitro | PyMuPDF | P5 | S |
| DES-06 | Stamps (Approved, Draft, custom) | Nitro | PyMuPDF | P5 | S |

#### ANN — Annotate & review (6)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| ANN-01 | Highlight, underline and strikeout | iLovePDF, Nitro | PyMuPDF | P5 | S |
| ANN-02 | Comments and sticky notes | Nitro | PyMuPDF | P5 | S |
| ANN-03 | Shape, line, arrow and freehand annotations | iLovePDF, Nitro | PyMuPDF | P5 | M |
| ANN-04 | Flatten annotations | Nitro | PyMuPDF | P5 | S |
| ANN-05 | Annotation summary export | Nitro | PyMuPDF | P5 | S |
| ANN-06 | Edit and delete existing annotations | Nitro | PyMuPDF | P5 | S |

#### DOC — Document structure (5)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| DOC-01 | Edit metadata (title, author, subject, keywords, XMP) | Nitro | PyMuPDF, pikepdf | P5 | S |
| DOC-02 | Bookmarks / outline editor | Nitro | PyMuPDF | P5 | M |
| DOC-03 | Auto-generate bookmarks and TOC from heading styles | Beyond | PyMuPDF | P5 | M |
| DOC-04 | File attachments (add, extract, remove) | Nitro | PyMuPDF | P5 | S |
| DOC-05 | Page labels (i, ii, 1, 2, A-1) | Nitro | PyMuPDF | P5 | S |

#### FRM — Forms (8)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| FRM-01 | Detect and list AcroForm fields | Nitro | PyMuPDF | P6 | S |
| FRM-02 | Fill form fields | Nitro | PyMuPDF | P6 | S |
| FRM-03 | Create and edit fields (text, checkbox, radio, dropdown, signature) | Nitro | PyMuPDF | P6 | M |
| FRM-04 | Auto-detect fields on flat forms (lines, boxes, labels) | Nitro | PyMuPDF, OpenCV | P6 | L |
| FRM-05 | Field tab order | Nitro | pikepdf | P6 | S |
| FRM-06 | Flatten forms | Nitro | PyMuPDF | P6 | S |
| FRM-07 | Import/export form data (FDF, XFDF, JSON, CSV) | Nitro | pikepdf | P6 | M |
| FRM-08 | XFA form detection with static fallback and warning | Beyond | pikepdf | P6 | M |

#### SIG — Sign (6)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| SIG-01 | Visual signatures (drawn, typed, image) | iLovePDF, Nitro | PyMuPDF | P6 | M |
| SIG-02 | Digital certificate signing (PAdES) | Nitro | pyHanko | P6 | M |
| SIG-03 | Signature validation report | Nitro | pyHanko | P6 | M |
| SIG-04 | RFC 3161 timestamps from a user-configured TSA | Nitro | pyHanko | P6 | S |
| SIG-05 | Signed-document warning and incremental-save mode | Beyond | pyHanko, PyMuPDF | P6 | S |
| SIG-06 | Local self-signed certificate generator | Beyond | cryptography | P6 | S |

#### OCR — OCR & scans (4)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| OCR-01 | Searchable PDF via OCR | iLovePDF, Nitro | OCRmyPDF, Tesseract | P7 | M |
| OCR-02 | Multi-language OCR language packs | iLovePDF, Nitro | Tesseract | P7 | S |
| OCR-03 | Deskew, denoise and auto-rotate | Nitro | OCRmyPDF, OpenCV | P7 | S |
| OCR-04 | Scan to PDF (photos to cleaned, perspective-corrected PDF) | iLovePDF | OpenCV, Pillow | P7 | M |

#### CVF — Convert from PDF (8)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| CVF-01 | PDF to Word (.docx) | iLovePDF, Nitro | pdf2docx | P7 | M |
| CVF-02 | PDF to Excel (.xlsx) via table extraction | iLovePDF, Nitro | pdfplumber, camelot, openpyxl | P7 | L |
| CVF-03 | PDF to PowerPoint (.pptx) | iLovePDF, Nitro | python-pptx, PyMuPDF | P7 | M |
| CVF-04 | PDF to images (PNG, JPG, TIFF) | iLovePDF, Nitro | PyMuPDF, Pillow | P7 | S |
| CVF-05 | PDF to text and Markdown | Nitro | PyMuPDF | P7 | S |
| CVF-06 | PDF to HTML | Beyond | PyMuPDF | P7 | S |
| CVF-07 | PDF to PDF/A (with optional veraPDF validation) | iLovePDF, Nitro | OCRmyPDF, Ghostscript | P7 | M |
| CVF-08 | Extract embedded images | Nitro | PyMuPDF | P7 | S |

#### CVT — Convert to PDF (6)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| CVT-01 | Word to PDF | iLovePDF, Nitro | LibreOffice | P7 | S |
| CVT-02 | Excel to PDF | iLovePDF, Nitro | LibreOffice | P7 | S |
| CVT-03 | PowerPoint to PDF | iLovePDF, Nitro | LibreOffice | P7 | S |
| CVT-04 | Images to PDF | iLovePDF, Nitro | PyMuPDF, Pillow | P7 | S |
| CVT-05 | HTML or URL to PDF | iLovePDF, Nitro | WeasyPrint | P7 | M |
| CVT-06 | Text and Markdown to PDF | Beyond | PyMuPDF | P7 | S |

#### OPT — Optimize & repair (7)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| OPT-01 | Compress with presets (lossless, balanced, strong) | iLovePDF, Nitro | PyMuPDF, pikepdf, Ghostscript | P8 | M |
| OPT-02 | Image downsampling and recompression | iLovePDF, Nitro | PyMuPDF, Pillow | P8 | M |
| OPT-03 | Font de-duplication and subsetting | Nitro | PyMuPDF, fontTools | P8 | M |
| OPT-04 | Remove unused objects and streams | Nitro | pikepdf | P8 | S |
| OPT-05 | Linearize (fast web view) | Nitro | pikepdf | P8 | S |
| OPT-06 | Repair broken or corrupt PDFs | iLovePDF | pikepdf, PyMuPDF | P1 | M |
| OPT-07 | Convert to grayscale | Beyond | Ghostscript | P8 | S |

#### CMP — Compare (3)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| CMP-01 | Text diff between two PDFs | iLovePDF, Nitro | PyMuPDF, difflib | P8 | M |
| CMP-02 | Visual (pixel) diff with overlay | iLovePDF, Nitro | PyMuPDF, numpy | P8 | M |
| CMP-03 | Diff report export (PDF, HTML) | Nitro | PyMuPDF | P8 | S |

#### ACC — Accessibility (5)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| ACC-01 | Tag tree inspection | Nitro | pikepdf | P8 | M |
| ACC-02 | Alt text editing for figures | Nitro | pikepdf | P8 | M |
| ACC-03 | Reading order review | Beyond | pikepdf, PyMuPDF | P8 | L |
| ACC-04 | Document language and title checks | Beyond | pikepdf | P8 | S |
| ACC-05 | Basic PDF/UA checks report | Beyond | pikepdf | P8 | M |

#### BAT — Batch & automation (4)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| BAT-01 | Apply a recipe to every PDF in a folder | Nitro | Python | P8 | M |
| BAT-02 | Batch dry-run preview | Beyond | Python | P8 | S |
| BAT-03 | Batch run report (CSV, JSON) | Beyond | Python | P8 | S |
| BAT-04 | Watch-folder mode | Beyond | watchdog | P8 | S |

#### XTR — Extras (3)

| ID | Feature | Parity | Libraries | Phase | Size |
|---|---|---|---|---|---|
| XTR-01 | Offline translation of PDF text (optional, local models) | iLovePDF | Argos Translate | P8 | L |
| XTR-02 | Extractive summary (TextRank, deterministic) | iLovePDF | numpy | P8 | M |
| XTR-03 | Table detection and export to CSV | Nitro | pdfplumber, camelot | P7 | M |
<!-- END GENERATED: catalog -->

## 8. User interface

### 8.1 Layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│ File ▾  Edit ▾  Organize ▾  Convert ▾  Secure ▾  Tools ▾      ☀/☾  ⚙    │
├──────┬──────────────────────────────────────────────┬───────────────────┤
│ ▢ 1  │                                              │ INSPECTOR         │
│ ▢ 2  │          page canvas (pdf.js)                │ Font  Helvetica-  │
│ ▣ 3  │     ┌───────────────────────┐                │       Bold (subset│
│ ▢ 4  │     │ Rev C  →  |Rev D|     │ ← click-to-   │       ABCDEF+)    │
│ …    │     └───────────────────────┘   edit overlay │ Size  9.5 pt      │
│      │                                              │ Color #1F2A44     │
│      │                                              │ Tc 0.12  Tz 100%  │
│      │                                              │ Match ● Exact     │
├──────┴──────────────────────────────────────────────┴───────────────────┤
│ ❯ replace "Rev C" with "Rev D" on all pages match style      [Preview]  │
├─────────────────────────────────────────────────────────────────────────┤
│ History: ✓ replace "Rev C"→"Rev D" (14 hits, Exact) · ↶ undo · ↷ redo   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Click-to-edit

1. Hovering shows span boxes. Clicking selects the span and puts the caret in the inspector's **Edit text** box, prefilled with the span's text and its detected style: the original font (the default), size, color, bold and italic. All typing happens there, never on the page (owner's decision, 2026-09-29): clicking elsewhere keeps the draft, and choosing other text with an unapplied draft asks first.
2. The inspector shows the detected style and the match tier (Exact / High / Approximate / Low), live as the draft changes.
3. Pressing Enter (or Apply) commits text and style together as one `edit_span` Op: one history entry, one undo. The edited span stays selected. Escape (or Revert) restores the draft. A weaker tier than Exact asks for confirmation first.
4. Selecting a block exposes move, resize, reflow, style change and format painter. Images and shapes offer replace, crop, resize and delete.
5. Arranging objects, Nitro-style (EDT-13..15, owner's request 2026-09-30):
   - **Selection:** text blocks, images and shapes share one selection. Shift+click adds or removes an object; dragging on empty page selects everything inside the rectangle.
   - **Guides:** dragging snaps to other objects' edges and centers, and to the page's edges, margins and center, with guide lines shown. Holding Alt turns snapping off.
   - **Align:** the Align menu aligns left, center, right, top, middle or bottom, or distributes evenly. A single object aligns to the page.
   - **Keys:** arrows nudge 1pt (10pt with Shift). Ctrl+C, Ctrl+V and Ctrl+D copy, paste and duplicate, and Delete removes.
   - **Undo:** each action is one Op (`move_objects`, `duplicate_objects` or `delete_objects`), so one undo reverses it.
   - **Text editing:** clicking text still puts the cursor in the inspector. Esc in an unchanged text box hands the keyboard back to the page.

### 8.3 Command bar (deterministic, no AI)

The command bar parses plain-English commands with a fixed **Lark grammar**. Nothing is guessed. When a command does not parse, the bar shows the nearest valid forms ("did you mean …", using rapidfuzz) and a syntax hint. Recognized commands show a preview with a count of matches before anything is applied.

**Grammar sketch (EBNF):**

```
command     := action target? scope? modifier*
action      := replace | delete | insert | move | set | rotate | crop | resize
             | merge | split | extract | number | bates | watermark | stamp
             | header | footer | redact | protect | unlock | compress | ocr
             | convert | compare | flatten | sign | fill | bookmark | undo | redo
target      := STRING | REGEX | "pages" range | named_pattern | "text" | "images"
scope       := "on" ("page" INT | "pages" range | "all pages" | "odd pages" | "even pages")
range       := INT ("-" INT)? ("," INT ("-" INT)?)*
modifier    := "match style" | "bold" | "italic" | "size" NUMBER | "color" HEX
             | "font" STRING | "opacity" PERCENT | "rotate" NUMBER | "at" POSITION
             | "below"|"above"|"after"|"before" STRING | "starting" INT | "format" STRING
named_pattern := "emails" | "phone numbers" | "ssns" | "card numbers" | "dates"
```

**Examples:**

| Command | Resulting Op |
|---|---|
| `replace "2024" with "2025" on all pages` | `replace_text` literal, all pages, style = match |
| `replace /Rev\s+[A-C]/ with "Rev D" on pages 1-3` | `replace_text` regex, pages 1–3 |
| `insert "Checked by: K.H." below "Prepared by" match style` | `insert_text` anchored to a found span |
| `set size 11 bold for "Note:" on page 3` | `restyle_text` |
| `delete pages 7, 9-10` | `delete_pages` |
| `move pages 5-6 after page 1` | `reorder_pages` |
| `number pages bottom center format "Page {n} of {total}" starting 1` | `page_numbers` |
| `watermark "CONFIDENTIAL" opacity 20% rotate 45` | `watermark` |
| `redact emails and phone numbers on all pages` | `redact` with pattern packs |
| `protect` | `protect`, which opens a masked password dialog. Passwords are never typed into the command bar. |
| `compress balanced` · `ocr language eng+deu` · `convert to docx` | `compress` · `ocr` · `convert` |

### 8.4 Other UI requirements

- Before/after split view with synchronized scrolling, and a diff overlay toggle.
- History panel with undo/redo and per-edit verification status. The history can be exported as a recipe.
- Drag-and-drop organizer for pages and files. Keyboard shortcuts for every action. WCAG 2.2 AA contrast. Light and dark themes.

## 9. Operation model & recipes

Every Op is a pydantic model with a JSON schema published in `docs/ops.schema.json`.

```json
{
  "op": "replace_text",
  "target": { "match": "Rev\\s+[A-C]", "mode": "regex", "pages": "1-3", "case": true, "wholeWord": false },
  "replacement": "Rev D",
  "style": { "mode": "match" },
  "fit": { "strategy": "auto", "maxTracking": 0.06, "allowReflow": true },
  "requireTier": "high"
}
```

Recipes are ordered lists of Ops in YAML or JSON:

```yaml
recipe: bump-revision
inputs: "drawings/*.pdf"
output: "{stem}.revD.pdf"
ops:
  - { op: replace_text, target: { match: "Rev C", pages: all }, replacement: "Rev D", style: { mode: match } }
  - { op: stamp, text: "SUPERSEDES REV C", position: top-right, opacity: 0.8 }
  - { op: compress, preset: balanced }
```

Running a recipe: `pdfworkerz run recipe.yaml --dry-run`, then `pdfworkerz run recipe.yaml`. Recipes are deterministic. Running the same recipe on the same input produces byte-identical output, except for timestamps, which can be pinned with `--fixed-date`.

## 10. Non-functional requirements

| Area | Requirement |
|---|---|
| Cost | Free. No paid dependencies, no AI tokens, no network needed after install (except optional TSA timestamps and URL → PDF) |
| Privacy | Files are never uploaded. The server binds to loopback only. No telemetry. Passwords are held only in memory. |
| Platforms | Windows 10+, macOS 12+, Ubuntu 22.04+. Python 3.11+ |
| Performance | Open a 1,000-page file in under 3 s (lazy loading). Render a page in under 150 ms at 150 DPI. A single text replace in under 500 ms including verification |
| Reliability | Never corrupt the original. Atomic writes (temp file, then rename). Crash-safe undo journal |
| Determinism | Same input and Ops produce the same output. Pinned dependency versions |
| Accessibility | Keyboard-operable UI. Screen-reader labels. AA contrast |

## 11. Workers: who builds and who reviews

| Worker | Type | Cost | Responsibility |
|---|---|---|---|
| **Gate · lint** | ruff (`tools/gate.py --job lint`) | Free, no tokens | Style and common bug patterns |
| **Gate · types** | mypy --strict | Free, no tokens | Type integrity of engine and tools |
| **Gate · tests** | pytest + coverage gate, Playwright UI suite | Free, no tokens | Functionality, including the golden-corpus and pixel-diff suites |
| **Gate · security** | bandit, pip-audit, npm audit | Free, no tokens | Unsafe code patterns, vulnerable dependencies |
| **Gate · evidence gate** | `tools/update_tracker.py --check` | Free, no tokens | Blocks any "done" claim without passing tests |
| **Gate · spec sync** | `tools/gen_spec_catalog.py --check`, `tools/gen_ops_schema.py --check` | Free, no tokens | SPEC.md, ops schema and features.json cannot drift |
| **Gate · tracker build** | Vite build of FeatureTracker.jsx | Free, no tokens | The tracker always compiles |
| **Builder agent** | Claude sub-agent (build sessions only) | Uses tokens | Implements one bounded task at a time |
| **Reviewer agent** | Independent Claude sub-agent | Uses tokens | Reviews each phase against this spec, the reviewer checklist and gate results before merge |

The review workers run locally through `python tools/gate.py`, which must pass before every commit. They are deterministic tooling with no AI services, the permanent, token-free guardians of integrity and functionality. `.github/workflows/ci.yml` mirrors the same jobs across three OSes but is manual-only (`workflow_dispatch`), because GitHub Actions minutes are billed on this private repository. AI agents are used only to write and review code during build sessions. They cannot mark a feature done: only passing tests can (§12.3).

## 12. Quality & anti-hallucination process

### 12.1 Golden corpus

`tests/corpus/build_corpus.py` generates the test PDFs. Nothing is downloaded, so licensing and ground truth are fully known. The corpus includes:

- Embedded full fonts, subset fonts, non-embedded standard-14 fonts, CID/CJK, RTL, vertical, Type3, and fonts without ToUnicode.
- Text with non-zero Tc/Tw/Tz/Ts, render modes 0–7, rotated and skewed matrices, TJ kerning.
- Every encryption revision (R2, R3, R4 RC4/AES, R6 AES-256), owner-only restrictions, and public-key encryption stubs.
- AcroForms, XFA stubs, signed documents, annotations, layers, attachments, bookmarks.
- Scanned pages (rendered and noised), broken xref and truncated files, and a 1,000-page file.

### 12.2 Test types

- **Unit tests** for every engine function and every library API the code relies on (API-contract tests, which catch invented or changed APIs).
- **Pixel-diff regression**: reference renders are stored as hashes plus tolerance images.
- **Font-match benchmark**: accuracy of tier selection and metric matching on the corpus, reported by the local gate.
- **UI tests**: Playwright on the local server.

### 12.3 Evidence gate: "done" means proven

- Each test declares the feature it proves: `@pytest.mark.feature("EDT-01")`, optionally with `criterion=n` for features that have several acceptance criteria.
- pytest writes `build/feature_results.json`. `tools/update_tracker.py --write` marks a feature **done** only when every criterion has a passing test and none of its tests fail.
- `--check` runs in the local gate (`tools/gate.py`) and fails it if any feature is marked done without that evidence.

### 12.4 Rules for builder and reviewer agents

1. Never call a library API without confirming it exists in the installed version (read the docs or source, then write a contract test).
2. Never mark progress by hand. Only the evidence gate changes status.
3. Every PR lists the feature IDs it touches and the tests that prove them.
4. The reviewer rejects code with no test, with unverified APIs, with silent fallbacks, or with network calls outside the documented exceptions.

## 13. Session continuity & token look-ahead

Build sessions are finite. Usage limits, context size and session expiry can all end one. PDFWorkerz uses **look-ahead budgeting with atomic, resumable tasks** so that no sub-task is left half-done. The full procedure is in [`docs/SESSION_PROTOCOL.md`](docs/SESSION_PROTOCOL.md). In short:

1. Every task has a size estimate (S/M/L). No task is larger than L; bigger work is split.
2. Before starting a task: `python tools/session_budget.py --remaining <tokens>`. A task starts only if its estimate fits within the remaining budget minus a 25% reserve.
3. Long tasks commit to a WIP branch at natural break points. `state/checkpoint.json` records phase, task, step, branch and next action, and is pushed after every task.
4. A new session reads the checkpoint, verifies the branch, runs `python tools/gate.py`, and resumes at `nextAction`.
5. **Automatic resume after an abrupt end (INF-10).** A usage limit, a context overflow or an expiry can stop a session between two tool calls, with no chance to write a checkpoint. So the checkpoint is marked `in-progress` *before* the work starts (`python tools/resume.py --begin "<task>"`) and cleared only once the task is committed (`--end`). Every session begins with `python tools/resume.py`, which reports whether the session before it was cut off, what it was doing, which files it left uncommitted and which WIP branches are ahead — and then **resumes that task automatically**, without waiting to be told what it was. `--check` exits 1 when a task was left unfinished. The gate, not the checkpoint, decides what state the code is actually in: a killed session can leave `step` one step stale, but it cannot leave the gate wrong.
6. Actual token use per task is appended to `state/usage_log.jsonl` to recalibrate the estimates.
7. The gate is deterministic and token-free, so it gives the same verdict whether or not an AI session is running.

## 14. Roadmap

<!-- BEGIN GENERATED: phases -->
| Phase | Scope | Features | Est. tokens |
|---|---|---|---|
| P0 | Spec, tracker, CI workers, session protocol | 6 | ~300k |
| P1 | Engine core, inspection, encryption, repair, CLI | 17 | ~2,050k |
| P2 | Font identification & style-matched text editing | 20 | ~4,550k |
| P3 | Web UI with click-to-edit | 15 | ~1,900k |
| P4 | Command bar & recipes | 8 | ~900k |
| P5 | Organize, page design, annotate, document structure | 36 | ~3,350k |
| P6 | Forms, signatures, security, redaction | 21 | ~2,200k |
| P7 | OCR, scans, conversions | 21 | ~2,800k |
| P8 | Optimize, compare, accessibility, batch, extras | 21 | ~3,100k |
| P9 | Packaging & documentation | 2 | ~550k |
<!-- END GENERATED: phases -->

Each phase ends when all of its features are **done** through the evidence gate, the local gate is green on `main`, and the reviewer agent has signed off in PROGRESS.md.

## 15. Known limitations & risks

| Risk | Mitigation |
|---|---|
| Type3 and ToUnicode-less fonts limit how exactly edits can match | Tier reporting, shape-matching recovery, and user approval for Approximate or Low tiers |
| Licensing of embedded fonts (some forbid editing or subsetting) | Read the OS/2 `fsType` flags. Warn, and fall back to a metric match when editing is disallowed |
| Editing invalidates digital signatures | Detect signatures, warn the user, and offer incremental save |
| XFA dynamic forms are not fully supported by open-source engines | Detect them, fill the static fallback, and warn |
| Office conversion fidelity differs from Microsoft Office | Document the known differences. Pixel-compare conversions in the tests |
| PyMuPDF's AGPL license carries network-use obligations for any hosted fork | The project itself is AGPL, and it runs locally by design |
| Public-key encryption cannot be opened in v1 | Detect and report it. A future extension could accept the user's certificate and private key |

## 16. Repository structure

```
pdfworkerz/
├── SPEC.md                 ← this document
├── README.md  PROGRESS.md  LICENSE  pyproject.toml
├── engine/                 ← Python package: document model, ops, fonts, security, convert …
├── server/                 ← FastAPI local server
├── cli/                    ← Typer CLI (pdfworkerz)
├── web/                    ← TypeScript UI (pdf.js, overlay, command bar)
├── tracker/                ← features.json + FeatureTracker.jsx (live status)
├── tests/                  ← pytest suites, conftest evidence plugin, corpus builder
├── tools/                  ← local gate (gate.py), spec generator, evidence gate, session budget
├── state/                  ← checkpoint.json, usage_log.jsonl
├── docs/                   ← SESSION_PROTOCOL.md, ops.schema.json, user guide
└── .github/workflows/      ← manual-only mirror of the gate (3-OS matrix)
```
