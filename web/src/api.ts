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

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
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

/** One applied edit, exactly as it was requested -- an Op's own fields
 * (op.model_dump()), not the result of applying it. Which fields exist
 * beyond `op` depends on which Op it is; see history.ts's describeOp. */
export interface HistoryOp {
  op: string;
  [field: string]: unknown;
}

/** Mirrors server/app.py's HistoryResponse (UI-04). */
export interface HistoryState {
  ops: HistoryOp[];
  can_undo: boolean;
  can_redo: boolean;
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
    }
  } catch {
    // response body wasn't JSON -- fall through to the generic message below
  }
  return `request failed with status ${response.status}`;
}

export class Api {
  constructor(private readonly config: SessionConfig) {}

  async health(): Promise<HealthResponse> {
    const response = await fetch(`${this.config.apiBase}/health`);
    if (!response.ok) {
      throw new ApiError(response.status, await parseErrorDetail(response));
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

  /** UI-02/UI-03: every text span on one page, with its style and text state. */
  async pageSpans(documentId: string, pageIndex: number): Promise<SpanTrace[]> {
    const response = await this.request(`/documents/${documentId}/pages/${pageIndex}/spans`);
    return (await response.json()) as SpanTrace[];
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
  ): Promise<void> {
    await this.request(`/documents/${documentId}/ops`, {
      method: "POST",
      body: JSON.stringify({
        op: "replace_span_text",
        page_index: pageIndex,
        span_index: spanIndex,
        new_text: newText,
        require_tier: requireTier,
      }),
    });
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
      throw new ApiError(response.status, await parseErrorDetail(response));
    }
    return response;
  }
}
