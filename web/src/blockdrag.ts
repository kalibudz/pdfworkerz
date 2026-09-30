/**
 * EDT-05: drag handles for moving and resizing the selected text: a block
 * (move and resize), or a line or word (move only, EDT-18). Pure pointer
 * mechanics in the edit layer's own pixel space: the caller (overlay.ts)
 * converts the result to page coordinates and sends the Op. The handles are
 * siblings of the text's box, never children, so the box's own text (what
 * tests and tools read) stays exactly the unit's text.
 */

import type { DragSnapper } from "./arrange";

export interface BlockHandleCallbacks {
  /** EDT-14: snaps the move handle's delta to other objects (smart guides). */
  snapper?: DragSnapper;
  /** The move handle was dragged by (`dx`, `dy`) layer pixels. */
  onMove(dx: number, dy: number): void;
  /** The resize handle was dragged: the box's new right edge, in layer pixels. */
  onResize?(rightEdge: number): void;
  /** False: no resize handle (lines and words only move). Defaults to true. */
  resizable?: boolean;
  /** What is being moved, for the handles' labels ("paragraph", "line", "word"). */
  noun?: string;
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

  const noun = callbacks.noun ?? "paragraph";
  const resizable = callbacks.resizable ?? true;
  const move = makeHandle("pw-move-handle", `Drag to move this ${noun}`);
  move.style.left = `${left - 16}px`;
  move.style.top = `${top}px`;
  move.style.height = `${height}px`;

  const resize = makeHandle("pw-resize-handle", `Drag to change this ${noun}'s width`);
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
  if (resizable) {
    track(
      resize,
      (dx) => {
        box.style.width = `${Math.max(width + dx, 8)}px`;
        resize.style.transform = dx ? `translateX(${dx}px)` : "";
      },
      (dx) => callbacks.onResize?.(left + Math.max(width + dx, 8)),
    );
    layer.append(move, resize);
  } else {
    layer.append(move);
  }
  return () => {
    move.remove();
    resize.remove();
    box.style.transform = "";
    box.style.width = `${width}px`;
  };
}
