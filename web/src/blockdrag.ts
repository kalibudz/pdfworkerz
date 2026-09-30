/**
 * EDT-05: drag handles for moving and resizing the text block around the
 * span being edited. Pure pointer mechanics in the edit layer's own pixel
 * space: the caller (overlay.ts) converts the result to page coordinates
 * and sends the Op. The handles are siblings of the span box, never
 * children -- the box is contenteditable, and anything inside it would
 * become part of the text being edited.
 */

import type { DragSnapper } from "./arrange";

export interface BlockHandleCallbacks {
  /** EDT-14: snaps the move handle's delta to other objects (smart guides). */
  snapper?: DragSnapper;
  /** The move handle was dragged by (`dx`, `dy`) layer pixels. */
  onMove(dx: number, dy: number): void;
  /** The resize handle was dragged: the box's new right edge, in layer pixels. */
  onResize(rightEdge: number): void;
}

/** Below this many pixels a press is a click, not a drag, and does nothing. */
const DRAG_THRESHOLD_PX = 3;

function makeHandle(className: string, label: string): HTMLElement {
  const handle = document.createElement("div");
  handle.className = className;
  handle.title = label;
  handle.setAttribute("aria-label", label);
  handle.setAttribute("role", "button");
  return handle;
}

/** Adds the two handles next to `box`; returns a function that removes them. */
export function attachBlockHandles(layer: HTMLElement, box: HTMLElement, callbacks: BlockHandleCallbacks): () => void {
  const left = parseFloat(box.style.left);
  const top = parseFloat(box.style.top);
  const width = parseFloat(box.style.width);
  const height = parseFloat(box.style.height);

  const move = makeHandle("pw-move-handle", "Drag to move this paragraph");
  move.style.left = `${left - 16}px`;
  move.style.top = `${top}px`;
  move.style.height = `${height}px`;

  const resize = makeHandle("pw-resize-handle", "Drag to change this paragraph's width");
  resize.style.left = `${left + width}px`;
  resize.style.top = `${top}px`;
  resize.style.height = `${height}px`;

  function track(
    handle: HTMLElement,
    preview: (dx: number, dy: number) => void,
    done: (dx: number, dy: number) => void,
    snapper?: DragSnapper,
  ): void {
    handle.addEventListener("mousedown", (down: MouseEvent) => {
      // Keeps the focus where it is (the inspector's text box), so starting a
      // drag doesn't move the cursor out of an edit in progress.
      down.preventDefault();
      const delta = (event: MouseEvent): [number, number] => {
        const dx = event.clientX - down.clientX;
        const dy = event.clientY - down.clientY;
        return snapper ? snapper.adjust(dx, dy, event) : [dx, dy];
      };
      const onMouseMove = (event: MouseEvent): void => preview(...delta(event));
      const onMouseUp = (event: MouseEvent): void => {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        const raw = Math.hypot(event.clientX - down.clientX, event.clientY - down.clientY);
        const [dx, dy] = delta(event);
        snapper?.end();
        preview(0, 0);
        if (raw >= DRAG_THRESHOLD_PX) {
          done(dx, dy);
        }
      };
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    });
  }

  track(
    move,
    (dx, dy) => {
      const shift = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
      box.style.transform = shift;
      move.style.transform = shift;
      resize.style.transform = shift;
    },
    (dx, dy) => callbacks.onMove(dx, dy),
    callbacks.snapper,
  );
  track(
    resize,
    (dx) => {
      box.style.width = `${Math.max(width + dx, 8)}px`;
      resize.style.transform = dx ? `translateX(${dx}px)` : "";
    },
    (dx) => callbacks.onResize(left + Math.max(width + dx, 8)),
  );

  layer.append(move, resize);
  return () => {
    move.remove();
    resize.remove();
    box.style.transform = "";
    box.style.width = `${width}px`;
  };
}
