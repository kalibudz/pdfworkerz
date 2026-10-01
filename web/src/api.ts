/**
 * Typed client for server/app.py. Every route but /health requires the
 * X-Session-Token header (SPEC.md section 4.2 rule 5); every error response
 * is `{"detail": string}` (server/app.py's PdfWorkerzError handler plus
 * FastAPI's own for auth/validation failures), which ApiError carries
 * through as `.detail` and `.status` rather than a parsed/guessed message.
 */

import type { SessionConfig } from "./config";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** CMD-06: for a command that didn't parse, the closest valid commands and a syntax hint. */
  readonly suggestions: string[];
  readonly hint: string;

  constructor(status: number, detail: string, suggestions: string[] = [], hint = "") {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.suggestions = suggestions;
    this.hint = hint;
  }
}

export interface CommandPreview {
  description: string;
  special: "undo" | "redo" | null;
  matches: number | null;
  pages: number[];
  warnings: string[];
}

export interface RecipeRun {
  recipe: string;
  steps: number;
  matches?: number;
  pages?: number[];
  warnings?: string[];
  applied: boolean;
}

export interface OpenDocumentResponse {
  document_id: string;
  page_count: number;
  is_encrypted: boolean;
  is_repaired: boolean;
}

export interface HealthResponse {
  status: string;
  version: string;
}

/** Mirrors engine.fonts.style.SpanStyle field-for-field (FNT-01). */
export interface SpanStyle {
  page_index: number;
  span_index: number;
  text: string;
  font: string;
  size: number;
  color: [number, number, number];
  opacity: number;
  bbox: [number, number, number, number];
  rotation_degrees: number;
  ascender: number;
  descender: number;
  /** Each glyph's position (engine.fonts.style.CharBox); optional so older payloads still type-check. */
  chars?: CharBox[];
}

/** Mirrors engine.fonts.style.CharBox. */
export interface CharBox {
  char: string;
  origin: [number, number];
  bbox: [number, number, number, number];
}

/** EDT-16: what one click selects (SPEC.md section 8.2 item 6). */
export type SelectMode = "block" | "line" | "word";

/** EDT-16: one selectable unit of text, computed by the server (the frozen
 * TextUnit contract, SPEC.md section 8.2 item 6). Boxes are MuPDF page space:
 * points, y-down, relative to the crop box. `index` is the block's first span
 * index for "block", else the unit's position in that granularity's list. */
export interface TextUnit {
  granularity: SelectMode;
  index: number;
  text: string;
  bbox: [number, number, number, number];
  origin: [number, number];
  rotation_degrees: number;
  span_indices: number[];
  /** [start, end) character ranges within spans. */
  segments: { span_index: number; start: number; end: number }[];
  /** Words only: the line the word is on. */
  line_index: number | null;
  /** FNT-20: an emoji or icon glyph, its own unit so an edit never redraws it away.
   * Read-only in the inspector: it can be deleted on its own but not retyped or moved. */
  icon: boolean;
}

/** An ObjectRef inside move_objects / duplicate_objects / delete_objects items. */
export interface ObjectRef {
  kind: "text" | "image" | "shape";
  page_index: number;
  /** A span index for unit "block", else a unit index. */
  index: number;
  /** Text only; the server's default is "block". */
  unit?: SelectMode;
  /** Refuses the Op when the unit at `index` no longer has this text. */
  expect_text?: string | null;
  dx?: number;
  dy?: number;
}

/** EDT-17: the edit_text_unit Op's fields (as edit_span, addressed by unit). */
export interface EditTextUnitOp {
  op: "edit_text_unit";
  page_index: number;
  unit: SelectMode;
  index: number;
  expect_text: string;
  new_text?: string | null;
  size?: number;
  color?: [number, number, number];
  font?: string;
  bold?: boolean;
  italic?: boolean;
  /** Block only. */
  align?: "left" | "justify";
  grow?: boolean;
  allow_overflow?: boolean;
}

/** EDT-18: delete_objects' fields; `close_gap` only matters for words. */
export interface DeleteObjectsOp {
  op: "delete_objects";
  items: ObjectRef[];
  close_gap?: boolean;
}

/** Mirrors engine.fonts.style.TextState field-for-field (FNT-02); absent
 * when the content stream didn't correlate 1:1 with texttrace's spans --
 * see extract_page_spans's own docstring for exactly when that happens. */
export interface SpanTextState {
  char_spacing: number;
  word_spacing: number;
  horizontal_scale: number;
  leading: number;
  rise: number;
  render_mode: number;
  font_resource: string | null;
  font_size: number | null;
}

export interface SpanTrace {
  style: SpanStyle;
  text_state: SpanTextState | null;
}

/** Mirrors engine.ops.text.PreviewResult -- what committing a text edit
 * would do, without doing it (UI-02's live "Match" preview). */
export interface PreviewResult {
  tier: "exact" | "approximate" | "fallback";
  confidence: number;
  requires_approval: boolean;
  note: string;
}

/** Mirrors engine.links.LinkInfo (EDT-10). `index` is the link's position
 * in the page's current link list -- only valid until the next edit. */
export interface LinkInfo {
  index: number;
  rect: [number, number, number, number];
  kind: "uri" | "goto" | "other";
  uri: string | null;
  target_page: number | null;
}

/** Mirrors engine.images.ImageInfo (EDT-08). `index` is the placement's
 * position in the page's current image list -- only valid until the next edit. */
export interface ImageInfo {
  index: number;
  xref: number;
  rect: [number, number, number, number];
  pixel_width: number;
  pixel_height: number;
  axis_aligned: boolean;
}

/** Mirrors engine.shapes.ShapeInfo (EDT-09). */
export interface ShapeInfo {
  index: number;
  rect: [number, number, number, number];
  kind: "line" | "rect" | "curve" | "path";
  stroke_color: [number, number, number] | null;
  fill_color: [number, number, number] | null;
  line_width: number | null;
}

/** Mirrors engine.forms.FieldInfo (FRM-01). A radio group is one logical field,
 * reported with `field_type: "radio"` and one option per button. */
export interface FieldInfo {
  name: string;
  field_type: "text" | "checkbox" | "radio" | "dropdown" | "listbox" | "signature" | "unknown";
  rect: [number, number, number, number];
  value: string | boolean | null;
  options: string[] | null;
  page_index: number;
}

/** Mirrors engine.forms.FillResult (FRM-02). `unknown` is always `[]` from fillFields
 * itself (an unknown name there refuses the whole call instead); importFormData (FRM-07)
 * is the one caller that can return names in it. */
export interface FillResult {
  filled: string[];
  unknown: string[];
}

/** Mirrors engine.forms.FlattenResult (FRM-06). */
export interface FlattenResult {
  fields_flattened: number;
  pages_affected: number[];
}

/** FRM-03's createField/editField params, per field_type:
 * - text: `value` (string), `font` (one of Cour/TiRo/Helv/ZaDb), `size`, `multiline`.
 * - checkbox: `value` (boolean).
 * - dropdown / listbox: `options` (required), `value` (one of `options`).
 * - radio: a GROUP -- `options` (>= 2) and `rects` (one per option, same order); the
 *   top-level `rect` is ignored for this type.
 * - signature: an interactive /Sig placeholder field (not SIG-01's drawn/typed/image
 *   stamp); no `value`. */
export interface FieldTypeProps {
  rect?: [number, number, number, number];
  options?: string[];
  rects?: [number, number, number, number][];
  value?: string | boolean | null;
  font?: string;
  size?: number;
  multiline?: boolean;
}

/** Mirrors engine.forms.XFAReport (FRM-08). */
export interface XFAReport {
  has_xfa: boolean;
  has_static_fields: boolean;
  warning: string | null;
}

export type FormDataFormat = "fdf" | "xfdf" | "json" | "csv";

/** Mirrors engine.form_detect.FieldProposal (FRM-04). One auto-detected
 * candidate field, for review before anything is created -- nothing is
 * created by detection alone. A review UI would list these with a checkbox
 * per proposal (accept/reject), then call createDetectedFields once with
 * only the accepted ones. */
export interface FieldProposal {
  field_type: "text" | "checkbox";
  rect: [number, number, number, number];
  confidence: number;
  suggested_name: string;
  label_text: string | null;
}

/** Mirrors engine.signatures.PlaceResult (SIG-01). */
export interface SignatureResult {
  page_index: number;
  rect: [number, number, number, number];
  kind: "drawn" | "typed" | "image";
}

/** PlaceSignatureOp's own params (engine.ops.signatures): give exactly one of
 * `strokes` (kind "drawn"), `text` (kind "typed") or `image_base64` (kind
 * "image"), matching `kind`. No UI panel sends this yet (SIG-01 has no
 * toolbar button or draw-pad/upload affordance as of this change) -- see
 * Api.placeSignature below. */
export interface PlaceSignatureRequest {
  page_index: number;
  rect: [number, number, number, number];
  kind: "drawn" | "typed" | "image";
  strokes?: [number, number][][];
  text?: string;
  font?: string;
  image_base64?: string;
}

/** Mirrors engine.certs.CertResult (SIG-06). */
export interface CertResult {
  common_name: string;
  key_size: number;
  cert_pem: string;
  key_pem: string;
  key_encrypted: boolean;
  pkcs12_base64: string;
}

/** SignDocumentOp's own params (engine.ops.sign), for SIG-02's real PAdES
 * signature -- distinct from PlaceSignatureRequest above (SIG-01), which is
 * only a picture on the page, never a cryptographic signature. `tsa_url`
 * (SIG-04) embeds an RFC 3161 timestamp; omitted, the signature is PAdES
 * B-B rather than B-T (see engine.pades module docstring). No UI panel
 * sends this yet. */
export interface SignDocumentRequest {
  cert_pem: string;
  key_pem: string;
  key_passphrase?: string;
  field_name?: string;
  reason?: string;
  location?: string;
  tsa_url?: string;
}

/** Mirrors engine.pades.SignResult (SIG-02/SIG-04). */
export interface SignResult {
  field_name: string;
  path: string;
  pades_level: "B-B" | "B-T";
  has_timestamp: boolean;
  bytes_written: number;
}

/** Mirrors engine.pades.SignatureReport (SIG-03/SIG-04). */
export interface SignatureReport {
  field_name: string;
  signed_revision: number;
  coverage: "entire_file" | "entire_revision" | "contiguous_block_from_start" | "unclear";
  intact: boolean;
  signature_valid: boolean;
  modified_after_signing: boolean;
  /** The bottom line: `intact && signature_valid && !modified_after_signing`. */
  valid: boolean;
  cert_subject: string;
  cert_valid_at_signing_time: boolean;
  signer_reported_time: string | null;
  has_trusted_timestamp: boolean;
  /** The RFC 3161 timestamp's own trusted time, distinct from `signer_reported_time`. */
  timestamp_time: string | null;
  timestamp_trusted: boolean;
}

/** Mirrors engine.pades.SignedDocStatus (SIG-05): what the UI checks before
 * warning that editing a signed document further will invalidate it. */
export interface SignedDocStatus {
  has_signature: boolean;
  is_still_valid: boolean;
  signature_count: number;
}

/** Mirrors engine.spellcheck.Misspelling (EDT-11). */
export interface Misspelling {
  span_index: number;
  start: number;
  end: number;
  word: string;
  bbox: [number, number, number, number];
  suggestions: string[];
}

/** One applied edit, exactly as it was requested -- an Op's own fields
 * (op.model_dump()), not the result of applying it. Which fields exist
 * beyond `op` depends on which Op it is; see history.ts's describeOp. */
export interface SaveResponse {
  path: string;
  mode: string;
  bytes_written: number;
  /** e.g. that a signed file's signatures no longer cover it; empty when there's nothing to add. */
  note: string;
}

export interface HistoryOp {
  op: string;
  [field: string]: unknown;
}

/** One text block (paragraph) on a page: PageBlocksOp (EDT-13). */
export interface BlockInfo {
  span_indices: number[];
  bbox: [number, number, number, number];
}

/** Mirrors engine.fonts.research.FontToResearch, plus the per-document fields GET
 * /fonts/research adds when given a document. */
export interface FontToResearch {
  font: string;
  best_tier: string;
  note: string;
  times_seen: number;
  first_seen: string;
  last_seen: string;
  example_document: string;
  harvestable_here?: boolean;
  harvest_problem?: string | null;
}

/** Mirrors engine.fonts.library.LibraryFont. */
export interface LibraryFont {
  postscript_name: string;
  family: string;
  style: string;
  file: string;
  source: string;
  added: string;
  copyright: string;
}

/** Mirrors server/app.py's HistoryResponse (UI-04). */
export interface HistoryState {
  ops: HistoryOp[];
  can_undo: boolean;
  can_redo: boolean;
}

/** Mirrors engine.redact.PatternMatch (SEC-09): one proposed redaction area, not yet
 * redacted -- a caller reviews these and redacts the accepted ones via redactAreas. */
export interface PatternMatch {
  page_index: number;
  span_index: number;
  start: number;
  end: number;
  text: string;
  rect: [number, number, number, number];
}

/** Mirrors engine.redact.RectStats (SEC-08): what was removed under one redacted rect. */
export interface RedactionRectStats {
  rect: [number, number, number, number];
  glyphs_removed: number;
  images_removed: number;
  images_altered: number;
  shapes_removed: number;
  shapes_clipped: number;
}

/** Mirrors engine.redact.RedactionVerification (SEC-11). */
export interface RedactionVerification {
  ok: boolean;
  problems: string[];
}

/** Mirrors engine.redact.RedactionResult (SEC-08/11): what RedactAreasOp returns. */
export interface RedactionResult {
  stats: RedactionRectStats[];
  outside_changed_fraction: number;
  verification: RedactionVerification;
}

/** Mirrors engine.sanitize.SanitizeReport (SEC-10): what SanitizeOp found and removed. */
export interface SanitizeReport {
  metadata_removed: boolean;
  javascript_actions_removed: number;
  embedded_files_removed: number;
  hidden_text_chars_removed: number;
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const { detail, suggestions, hint } = body as { detail: unknown; suggestions?: unknown; hint?: unknown };
      if (typeof detail === "string") {
        const list = Array.isArray(suggestions) ? suggestions.filter((s): s is string => typeof s === "string") : [];
        return new ApiError(response.status, detail, list, typeof hint === "string" ? hint : "");
      }
    }
  } catch {
    // response body wasn't JSON -- fall through to the generic message below
  }
  return new ApiError(response.status, `request failed with status ${response.status}`);
}

export class Api {
  constructor(private readonly config: SessionConfig) {}

  async health(): Promise<HealthResponse> {
    const response = await fetch(`${this.config.apiBase}/health`);
    if (!response.ok) {
      throw await toApiError(response);
    }
    return (await response.json()) as HealthResponse;
  }

  async openDocument(path: string, password?: string): Promise<OpenDocumentResponse> {
    const response = await this.request("/documents", {
      method: "POST",
      body: JSON.stringify(password ? { path, password } : { path }),
    });
    return (await response.json()) as OpenDocumentResponse;
  }

  async closeDocument(documentId: string): Promise<void> {
    await this.request(`/documents/${documentId}`, { method: "DELETE" });
  }

  /** The document's current (post-edit) bytes, for pdf.js to render client-side. */
  async documentFile(documentId: string): Promise<ArrayBuffer> {
    const response = await this.request(`/documents/${documentId}/file`);
    return await response.arrayBuffer();
  }

  private families: Promise<string[]> | null = null;

  /** EDT-03/EDT-06: the font families the user can choose (fetched once per session). */
  async fonts(): Promise<string[]> {
    this.families ??= this.request("/fonts")
      .then((response) => response.json() as Promise<{ families: string[] }>)
      .then((body) => body.families);
    return await this.families;
  }

  /** EDT-13: the page's text blocks -- what the arrangement tools select as one text object. */
  async pageBlocks(documentId: string, pageIndex: number): Promise<BlockInfo[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/blocks`);
    return (await response.json()) as BlockInfo[];
  }

  /** EDT-16: the page's text split into blocks, lines or words, by the server. */
  async pageTextUnits(documentId: string, pageIndex: number, granularity: SelectMode): Promise<TextUnit[]> {
    const params = new URLSearchParams({ granularity });
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/text_units?${params}`);
    return (await response.json()) as TextUnit[];
  }

  /** Fonts that edits could only approximate (engine.fonts.research). With a
   * document, each row says whether that document can supply the font. */
  async fontsToResearch(documentId?: string): Promise<FontToResearch[]> {
    const query = documentId ? `?${new URLSearchParams({ document_id: documentId })}` : "";
    const response = await this.request(`/fonts/research${query}`);
    return (await response.json()) as FontToResearch[];
  }

  /** The fonts the user added on this machine (engine.fonts.library). */
  async fontLibrary(): Promise<LibraryFont[]> {
    const response = await this.request("/fonts/library");
    return (await response.json()) as LibraryFont[];
  }

  /** Copy a font file on the server's machine into the user's library. */
  async addFontFile(path: string): Promise<LibraryFont> {
    const response = await this.request("/fonts/library", { method: "POST", body: JSON.stringify({ path }) });
    this.families = null; // the family list now includes it
    return (await response.json()) as LibraryFont;
  }

  /** Copy the complete font `font` that this document embeds into the user's library. */
  async harvestFont(documentId: string, font: string): Promise<LibraryFont> {
    const response = await this.request(`/documents/${documentId}/fonts/harvest`, {
      method: "POST",
      body: JSON.stringify({ font }),
    });
    this.families = null;
    return (await response.json()) as LibraryFont;
  }

  /** The edited document to hand to the user: keeps the original encryption, unlike documentFile. */
  async documentDownload(documentId: string): Promise<ArrayBuffer> {
    const response = await this.request(`/documents/${documentId}/download`);
    return await response.arrayBuffer();
  }

  /** UI-02/UI-03: every text span on one page, with its style and text state. */
  async pageSpans(documentId: string, pageIndex: number): Promise<SpanTrace[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/spans`);
    return (await response.json()) as SpanTrace[];
  }

  /** EDT-10: every link on one page. */
  async pageLinks(documentId: string, pageIndex: number): Promise<LinkInfo[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/links`);
    return (await response.json()) as LinkInfo[];
  }

  /** EDT-08: every image placement on one page. */
  async pageImages(documentId: string, pageIndex: number): Promise<ImageInfo[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/images`);
    return (await response.json()) as ImageInfo[];
  }

  /** EDT-09: every vector path on one page. */
  async pageShapes(documentId: string, pageIndex: number): Promise<ShapeInfo[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/shapes`);
    return (await response.json()) as ShapeInfo[];
  }

  /** FRM-01: every AcroForm field on one page (a radio group counts as one field). */
  async pageFields(documentId: string, pageIndex: number): Promise<FieldInfo[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/fields`);
    return (await response.json()) as FieldInfo[];
  }

  /** FRM-02: set several named fields' values on one page in one undo step.
   * `values` keys are field names; a checkbox's value is boolean, a radio/dropdown/
   * listbox's is one of that field's `options`, a text field's is a string. */
  async fillFields(documentId: string, pageIndex: number, values: Record<string, string | boolean>): Promise<FillResult> {
    return this.applyOp<FillResult>(documentId, { op: "fill_fields", page_index: pageIndex, values });
  }

  /** FRM-05: reorder one page's fields for tab navigation. A field left out of
   * `fieldNames` is placed after every named field, in its current relative order. */
  async setTabOrder(documentId: string, pageIndex: number, fieldNames: string[]): Promise<null> {
    return this.applyOp<null>(documentId, { op: "set_tab_order", page_index: pageIndex, field_names: fieldNames });
  }

  /** FRM-03: add a new field to one page; see FieldTypeProps for which properties
   * apply to which `fieldType`. */
  async createField(
    documentId: string,
    pageIndex: number,
    fieldType: FieldInfo["field_type"],
    name: string,
    props: FieldTypeProps = {},
  ): Promise<FieldInfo> {
    return this.applyOp<FieldInfo>(documentId, {
      op: "create_field",
      page_index: pageIndex,
      field_type: fieldType,
      name,
      ...props,
    });
  }

  /** FRM-03: change an existing field's type-specific properties; only the keys given
   * in `changes` are touched (e.g. `{ options: [...] }` for a dropdown, `{ font, size,
   * multiline }` for a text field, or `{ options, rects }` together to rebuild a radio
   * group). */
  async editField(
    documentId: string,
    pageIndex: number,
    name: string,
    changes: Omit<FieldTypeProps, "value">,
  ): Promise<FieldInfo> {
    return this.applyOp<FieldInfo>(documentId, { op: "edit_field", page_index: pageIndex, name, ...changes });
  }

  /** FRM-03: remove a field from both the page's annotations and the AcroForm's field
   * tree (a radio group's every button together). */
  async deleteField(documentId: string, pageIndex: number, name: string): Promise<null> {
    return this.applyOp<null>(documentId, { op: "delete_field", page_index: pageIndex, name });
  }

  /** FRM-04: likely field locations detected from visual cues alone on a flat
   * (non-interactive) page -- a line/underscore proposes a text field, a small
   * box proposes a checkbox. Read-only; nothing is created until
   * createDetectedFields (or individual createField calls) is sent. No UI
   * panel calls this yet -- a review list with a checkbox per proposal
   * (accept/reject), then one "create accepted" button, is still needed. */
  async detectFormFields(documentId: string, pageIndex: number, dpi = 150): Promise<FieldProposal[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/field_proposals?dpi=${dpi}`);
    return (await response.json()) as FieldProposal[];
  }

  /** FRM-04's accept step: turn a batch of (presumably reviewed/filtered)
   * FieldProposals into real AcroForm fields in one undo step, through the
   * generic, journaled ops endpoint (CreateDetectedFieldsOp). */
  async createDetectedFields(documentId: string, pageIndex: number, proposals: FieldProposal[]): Promise<FieldInfo[]> {
    return this.applyOp<FieldInfo[]>(documentId, {
      op: "create_detected_fields",
      page_index: pageIndex,
      proposals,
    });
  }

  /** FRM-07: every field on one page -- name and current value -- as FDF/XFDF/JSON/CSV bytes. */
  async exportFormData(documentId: string, pageIndex: number, format: FormDataFormat): Promise<ArrayBuffer> {
    const response = await this.request(
      `/documents/${documentId}/pages/${pageIndex}/form_data?format=${format}`,
    );
    return await response.arrayBuffer();
  }

  /** FRM-07: apply previously-exported (or hand-written) field data to one page, in one
   * undo step. An unknown field name comes back in the result's `unknown`, not as a
   * thrown error for the whole import. */
  async importFormData(
    documentId: string,
    pageIndex: number,
    format: FormDataFormat,
    data: ArrayBuffer,
  ): Promise<FillResult> {
    const dataBase64 = btoa(String.fromCharCode(...new Uint8Array(data)));
    return this.applyOp<FillResult>(documentId, {
      op: "import_form_data",
      page_index: pageIndex,
      format,
      data_base64: dataBase64,
    });
  }

  /** FRM-08: whether this document's AcroForm carries an /XFA entry; its layer isn't
   * processed here -- only its ordinary static fields, if any, are read or filled. */
  async detectXFA(documentId: string): Promise<XFAReport> {
    const response = await this.request(`/documents/${documentId}/xfa`);
    return (await response.json()) as XFAReport;
  }

  /** FRM-06: draw every field's current appearance into static page content and drop
   * the interactive AcroForm. `pageIndex` of `null`/`undefined` flattens the whole
   * document; a document with no AcroForm is left unchanged. */
  async flattenForm(documentId: string, pageIndex?: number | null): Promise<FlattenResult> {
    return this.applyOp<FlattenResult>(documentId, { op: "flatten_form", page_index: pageIndex ?? null });
  }

  /** SIG-01: place a drawn/typed/image signature as ordinary page content
   * (never an AcroForm field) through the generic, journaled ops endpoint, so
   * it's undoable like any other edit. The same `request` (same strokes/text/
   * image_base64) can be sent again with a different `page_index`/`rect` to
   * place the same signature elsewhere without the person re-drawing, re-typing
   * or re-uploading it -- that re-send, kept client-side, is this feature's
   * "reuse without redrawing from scratch" (no separate server-side signature
   * library exists yet). No toolbar button calls this yet; a signature pad /
   * upload UI is still needed (see the P6 session report). */
  async placeSignature(documentId: string, request: PlaceSignatureRequest): Promise<SignatureResult> {
    return this.applyOp<SignatureResult>(documentId, { op: "place_signature", ...request });
  }

  /** SIG-06: a local self-signed certificate and private key pair, generated
   * entirely on this machine (no network call). Not document-scoped, so --
   * unlike placeSignature above -- this hits its own top-level route rather
   * than an open document's ops endpoint. No UI calls this yet. */
  async generateCertificate(commonName: string, keySize = 2048, passphrase?: string): Promise<CertResult> {
    const response = await this.request("/certs/generate", {
      method: "POST",
      body: JSON.stringify({ common_name: commonName, key_size: keySize, passphrase: passphrase ?? null }),
    });
    return (await response.json()) as CertResult;
  }

  /** SIG-02/SIG-04: apply a real PAdES digital signature (optionally RFC 3161
   * timestamped) through the generic, journaled ops endpoint. No UI panel
   * sends this yet -- see SIG-05's documentSignatureStatus for the warning
   * check a future signing panel would show before calling this. */
  async signDocument(documentId: string, request: SignDocumentRequest): Promise<SignResult> {
    return this.applyOp<SignResult>(documentId, { op: "sign_document", ...request });
  }

  /** SIG-03/SIG-04: a validation report for every digital signature embedded
   * in the document (modified-since-signing, certificate validity window,
   * and a trusted RFC 3161 timestamp when one is present). Read-only, its
   * own GET route like pageFields above. */
  async validateSignatures(documentId: string): Promise<SignatureReport[]> {
    const response = await this.request(`/documents/${documentId}/signatures`);
    return (await response.json()) as SignatureReport[];
  }

  /** SIG-05: whether the open document is digitally signed and that
   * signature is still current -- call before letting an edit proceed, to
   * show the "this will invalidate the signature" warning. */
  async documentSignatureStatus(documentId: string): Promise<SignedDocStatus> {
    const response = await this.request(`/documents/${documentId}/signature-status`);
    return (await response.json()) as SignedDocStatus;
  }

  /** EDT-11: misspelled words on one page, minus `ignore`d ones. */
  async pageSpelling(documentId: string, pageIndex: number, ignore: string[] = []): Promise<Misspelling[]> {
    const params = new URLSearchParams(ignore.length ? { ignore: ignore.join(",") } : {});
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/spelling?${params}`);
    return (await response.json()) as Misspelling[];
  }

  /** UI-02's live preview, before anything is committed: what font-resolution
   * tier `neededText` would get if it replaced `spanIndex`'s current text. */
  async previewText(
    documentId: string,
    pageIndex: number,
    spanIndex: number,
    neededText: string,
  ): Promise<PreviewResult> {
    const params = new URLSearchParams({ span_index: String(spanIndex), needed_text: neededText });
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/preview?${params}`);
    return (await response.json()) as PreviewResult;
  }

  /** UI-02's commit step: replace exactly `spanIndex`'s text, journaled
   * normally (unlike the two read-only calls above) so it's undoable. */
  async replaceSpanText(
    documentId: string,
    pageIndex: number,
    spanIndex: number,
    newText: string,
    requireTier: "exact" | "approximate" | "fallback",
  ): Promise<unknown> {
    const response = await this.request(`/documents/${documentId}/ops`, {
      method: "POST",
      body: JSON.stringify({
        op: "replace_span_text",
        page_index: pageIndex,
        span_index: spanIndex,
        new_text: newText,
        require_tier: requireTier,
      }),
    });
    return await response.json();
  }

  /** Any registered Op through the generic, journaled ops endpoint (so it
   * shows up in history and is undoable), returning its JSON result. The
   * EDT-0x/EDT-1x editing tools all go through this rather than one
   * method per Op -- server/app.py itself has no per-Op routes for them. */
  async applyOp<T = unknown>(documentId: string, op: HistoryOp): Promise<T> {
    const response = await this.request(`/documents/${documentId}/ops`, {
      method: "POST",
      body: JSON.stringify(op),
    });
    return (await response.json()) as T;
  }

  /** SEC-04/05/06: print/copy/modify/annotate flags, each independently allow/deny.
   * Mirrors engine.ops.protect's three Ops' own flags; omitted fields default to allow. */
  protectDocument(
    documentId: string,
    options: {
      userPassword?: string;
      ownerPassword?: string;
      allowPrint?: boolean;
      allowCopy?: boolean;
      allowModify?: boolean;
      allowAnnotate?: boolean;
      path?: string;
      overwrite?: boolean;
    },
  ): Promise<SaveResponse> {
    return this.applyOp<SaveResponse>(documentId, {
      op: "set_password",
      user_password: options.userPassword ?? null,
      owner_password: options.ownerPassword ?? null,
      allow_print: options.allowPrint ?? true,
      allow_copy: options.allowCopy ?? true,
      allow_modify: options.allowModify ?? true,
      allow_annotate: options.allowAnnotate ?? true,
      path: options.path ?? null,
      overwrite: options.overwrite ?? false,
    });
  }

  /** SEC-05: remove encryption from an already-open document. No password field --
   * a wrong password was already refused when the document was opened. */
  unlockDocument(
    documentId: string,
    options: { path?: string; overwrite?: boolean } = {},
  ): Promise<SaveResponse> {
    return this.applyOp<SaveResponse>(documentId, {
      op: "remove_password",
      path: options.path ?? null,
      overwrite: options.overwrite ?? false,
    });
  }

  /** SEC-06: set permissions independently. `ownerPassword` is required by the
   * server (engine.ops.protect.SetPermissionsOp) -- without one, the restriction
   * could be removed by anyone who reopens the file with no password at all. */
  setPermissions(
    documentId: string,
    options: {
      ownerPassword: string;
      userPassword?: string;
      allowPrint?: boolean;
      allowCopy?: boolean;
      allowModify?: boolean;
      allowAnnotate?: boolean;
      path?: string;
      overwrite?: boolean;
    },
  ): Promise<SaveResponse> {
    return this.applyOp<SaveResponse>(documentId, {
      op: "set_permissions",
      owner_password: options.ownerPassword,
      user_password: options.userPassword ?? null,
      allow_print: options.allowPrint ?? true,
      allow_copy: options.allowCopy ?? true,
      allow_modify: options.allowModify ?? true,
      allow_annotate: options.allowAnnotate ?? true,
      path: options.path ?? null,
      overwrite: options.overwrite ?? false,
    });
  }

  /** SEC-09: proposed redaction rects on one page matching a built-in pattern
   * ("email", "phone", "ssn", "credit_card") or a custom regex. Read-only --
   * nothing is redacted; pass the accepted matches' `rect`s to redactAreas.
   * No toolbar button calls this yet (see the P6 session report). */
  async findRedactionCandidates(documentId: string, pageIndex: number, pattern: string): Promise<PatternMatch[]> {
    const params = new URLSearchParams({ pattern });
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/redaction_candidates?${params}`);
    return (await response.json()) as PatternMatch[];
  }

  /** SEC-08: true redaction -- removes the glyphs, image pixels and vector paths
   * under each of `rects` on `pageIndex`, never just a box drawn over them.
   * SEC-11's verification runs automatically; a failed verification surfaces as a
   * 422 ApiError, same as any other OpValidationError-like failure. No toolbar
   * button calls this yet (see the P6 session report). */
  async redactAreas(
    documentId: string,
    pageIndex: number,
    rects: [number, number, number, number][],
  ): Promise<RedactionResult> {
    return this.applyOp<RedactionResult>(documentId, { op: "redact_areas", page_index: pageIndex, rects });
  }

  /** SEC-10: remove metadata/XMP, JavaScript, embedded files and hidden text from the
   * whole document; each category defaults on, pass `false` to keep it. No toolbar
   * button calls this yet (see the P6 session report). */
  async sanitizeDocument(
    documentId: string,
    options: {
      removeMetadata?: boolean;
      removeJavascript?: boolean;
      removeEmbeddedFiles?: boolean;
      removeHiddenText?: boolean;
    } = {},
  ): Promise<SanitizeReport> {
    return this.applyOp<SanitizeReport>(documentId, {
      op: "sanitize",
      remove_metadata: options.removeMetadata ?? true,
      remove_javascript: options.removeJavascript ?? true,
      remove_embedded_files: options.removeEmbeddedFiles ?? true,
      remove_hidden_text: options.removeHiddenText ?? true,
    });
  }

  /** UI-05's before/after split view: one page rendered server-side to PNG
   * -- the live document (`original: false`, the default) or the document
   * exactly as it was first opened (`original: true`), regardless of any
   * edits or undo/redo since. Distinct from the pdf.js client-side render
   * the main canvas uses: the split view wants the server's own
   * authoritative render on both sides, not a second pdf.js instance. */
  async renderPage(documentId: string, pageIndex: number, options: { original?: boolean } = {}): Promise<Blob> {
    const suffix = options.original ? "/original" : "";
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/render${suffix}`);
    return await response.blob();
  }

  /** UI-04: the ops applied so far (oldest first), and whether there's
   * anything to undo/redo right now. */
  async history(documentId: string): Promise<HistoryState> {
    const response = await this.request(`/documents/${documentId}/history`);
    return (await response.json()) as HistoryState;
  }

  /** UI-04: revert the most recent op. Rejects (409, via ApiError) if
   * there's nothing to undo -- callers should already be gating this on
   * HistoryState.can_undo, this is the belt-and-suspenders backstop. */
  async undo(documentId: string): Promise<void> {
    await this.request(`/documents/${documentId}/undo`, { method: "POST" });
  }

  /** UI-04: re-apply the most recently undone op. */
  async redo(documentId: string): Promise<void> {
    await this.request(`/documents/${documentId}/redo`, { method: "POST" });
  }

  /** CMD-04: what can come next in a half-typed command. */
  async completeCommand(text: string): Promise<string[]> {
    const response = await this.request(`/commands/complete?${new URLSearchParams({ text })}`);
    return ((await response.json()) as { suggestions: string[] }).suggestions;
  }

  /** CMD-05: what a command would do, without doing it. Rejects with suggestions if it doesn't parse. */
  async previewCommand(documentId: string, text: string, pageIndex: number): Promise<CommandPreview> {
    const response = await this.request(`/documents/${documentId}/commands/preview`, {
      method: "POST",
      body: JSON.stringify({ text, page_index: pageIndex }),
    });
    return (await response.json()) as CommandPreview;
  }

  async applyCommand(documentId: string, text: string, pageIndex: number): Promise<unknown> {
    const response = await this.request(`/documents/${documentId}/commands/apply`, {
      method: "POST",
      body: JSON.stringify({ text, page_index: pageIndex }),
    });
    return ((await response.json()) as { result: unknown }).result;
  }

  /** CMD-07: this session's edits as a YAML recipe. */
  async exportRecipe(documentId: string): Promise<string> {
    const response = await this.request(`/documents/${documentId}/recipe`);
    return await response.text();
  }

  async runRecipe(documentId: string, text: string, dryRun: boolean): Promise<RecipeRun> {
    const response = await this.request(`/documents/${documentId}/recipe`, {
      method: "POST",
      body: JSON.stringify({ text, dry_run: dryRun }),
    });
    return (await response.json()) as RecipeRun;
  }

  /** Save the edited document on the server's disk. With no options it writes a
   * new versioned file next to the original (`name.edited.pdf`), never over it. */
  async save(documentId: string, options: { path?: string; overwrite?: boolean } = {}): Promise<SaveResponse> {
    const response = await this.request(`/documents/${documentId}/save`, {
      method: "POST",
      body: JSON.stringify(options),
    });
    return (await response.json()) as SaveResponse;
  }

  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    const response = await fetch(`${this.config.apiBase}${path}`, {
      ...init,
      headers: {
        "X-Session-Token": this.config.token,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    if (!response.ok) {
      throw await toApiError(response);
    }
    return response;
  }
}
