/**
 * EDT-05: drag handles for moving and resizing the text block around the
 * span being edited. Pure pointer mechanics in the edit layer's own pixel
 * space: the caller (overlay.ts) converts the result to page coordinates
 * and sends the Op. The handles are siblings of the span box, never
 * children -- the box is contenteditable, and anything inside it would
 * become part of the text being edited.
 */

export interface BlockHandleCallbacks {
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
  ): void {
    handle.addEventListener("mousedown", (down: MouseEvent) => {
      // Keeps focus on the span box, so starting a drag doesn't blur (and
      // so cancel) the edit this handle belongs to.
      down.preventDefault();
      const onMouseMove = (event: MouseEvent): void => preview(event.clientX - down.clientX, event.clientY - down.clientY);
      const onMouseUp = (event: MouseEvent): void => {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        const dx = event.clientX - down.clientX;
        const dy = event.clientY - down.clientY;
        preview(0, 0);
        if (Math.hypot(dx, dy) >= DRAG_THRESHOLD_PX) {
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
