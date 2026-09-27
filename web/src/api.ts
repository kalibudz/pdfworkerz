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
