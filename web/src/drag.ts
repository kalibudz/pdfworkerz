/**
 * Shared pointer-drag mechanics for the edit layer's object tools
 * (images.ts, shapes.ts): track a drag from a mousedown, report live
 * deltas for a visual preview, and report the final delta only when the
 * pointer really moved -- a plain click is never mistaken for a tiny drag.
 */

import type * as pdfjsLib from "pdfjs-dist";

import { viewportToMupdfPoint } from "./overlay";

const DRAG_THRESHOLD_PX = 3;

export function drag(
  start: MouseEvent,
  preview: (dx: number, dy: number) => void,
  done: (dx: number, dy: number) => void,
): void {
  start.preventDefault();
  start.stopPropagation();
  const onMove = (event: MouseEvent): void => preview(event.clientX - start.clientX, event.clientY - start.clientY);
  const onUp = (event: MouseEvent): void => {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    const dx = event.clientX - start.clientX;
    const dy = event.clientY - start.clientY;
    preview(0, 0);
    if (Math.hypot(dx, dy) >= DRAG_THRESHOLD_PX) {
      done(dx, dy);
    }
  };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

/** Two layer-pixel corners -> a normalized MuPDF page rect. */
export function layerToPageRect(
  viewport: pdfjsLib.PageViewport,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): [number, number, number, number] {
  const [x0, y0] = viewportToMupdfPoint(viewport, ax, ay);
  const [x1, y1] = viewportToMupdfPoint(viewport, bx, by);
  return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
}

/** A small square handle at a box's bottom-right corner that resizes it. */
export function cornerHandle(
  layer: HTMLElement,
  box: HTMLElement,
  onResized: (left: number, top: number, right: number, bottom: number) => void,
): HTMLElement {
  const left = parseFloat(box.style.left);
  const top = parseFloat(box.style.top);
  const width = parseFloat(box.style.width);
  const height = parseFloat(box.style.height);
  const handle = document.createElement("div");
  handle.className = "pw-object-resize";
  handle.title = "Drag to resize";
  handle.style.left = `${left + width - 5}px`;
  handle.style.top = `${top + height - 5}px`;
  handle.addEventListener("mousedown", (event) =>
    drag(
      event,
      (dx, dy) => {
        box.style.width = `${Math.max(width + dx, 4)}px`;
        box.style.height = `${Math.max(height + dy, 4)}px`;
        handle.style.transform = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
      },
      (dx, dy) => onResized(left, top, left + Math.max(width + dx, 4), top + Math.max(height + dy, 4)),
    ),
  );
  layer.appendChild(handle);
  return handle;
}
