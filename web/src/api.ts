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
