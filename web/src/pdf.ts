/**
 * pdf.js wrapper (SPEC.md section 4.1: "Viewer -- pdf.js -- in-browser
 * rendering and text layer"). The main canvas and the thumbnail rail both
 * render through the same library, at different scales, from the same
 * loaded document -- there's exactly one rendering path here, matching the
 * rest of this project's "one code path per capability" rule even on the
 * frontend.
 */

import * as pdfjsLib from "pdfjs-dist";
// `?worker&url` (rather than plain `?url` on pdfjs-dist's own worker file)
// builds ./pdf.worker.ts as its own bundle and resolves to *that* URL --
// needed so the getOrInsertComputed polyfill (polyfills.ts) installs
// inside the worker's own realm before pdf.js's worker code runs there.
import pdfWorkerUrl from "./pdf.worker.ts?worker&url";
import "./polyfills";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export type PdfDocument = pdfjsLib.PDFDocumentProxy;

export async function loadPdf(data: ArrayBuffer): Promise<PdfDocument> {
  // pdf.js detaches/transfers the buffer it's given; callers that need the
  // bytes again (there are none today) would need their own copy.
  const loadingTask = pdfjsLib.getDocument({ data });
  return await loadingTask.promise;
}

/** Renders one page at `scale` (1.0 == 72 DPI, pdf.js's native unit) into
 * `canvas`, sized to match. Cancels cleanly if called again on the same
 * canvas before a previous render finishes (page navigation faster than
 * rendering), rather than letting two renders race onto one canvas.
 * Returns the viewport actually drawn (null if cancelled) -- UI-02's
 * overlay needs it to convert a span's PDF-space bbox to this exact
 * canvas's CSS pixel space via `viewport.convertToViewportRectangle`. */
export class PageRenderer {
  private currentRender: ReturnType<pdfjsLib.PDFPageProxy["render"]> | null = null;

  async render(
    pdf: PdfDocument,
    pageNumber: number,
    canvas: HTMLCanvasElement,
    scale: number,
  ): Promise<pdfjsLib.PageViewport | null> {
    if (this.currentRender) {
      this.currentRender.cancel();
    }
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale });
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("canvas 2d context is unavailable");
    }
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const renderTask = page.render({ canvasContext: context, viewport, canvas });
    this.currentRender = renderTask;
    try {
      await renderTask.promise;
      return viewport;
    } catch (error) {
      if (error instanceof Error && error.name === "RenderingCancelledException") {
        return null;
      }
      throw error;
    } finally {
      if (this.currentRender === renderTask) {
        this.currentRender = null;
      }
    }
  }
}

/** A thumbnail's natural width/height at `targetWidth`, preserving the page's
 * own aspect ratio -- pages in a real document aren't all the same size. */
export function thumbnailViewport(page: pdfjsLib.PDFPageProxy, targetWidth: number): pdfjsLib.PageViewport {
  const unscaled = page.getViewport({ scale: 1 });
  return page.getViewport({ scale: targetWidth / unscaled.width });
}
