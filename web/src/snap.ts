/**
 * EDT-14: smart guides. While an object is dragged, its left/center/right and
 * top/middle/bottom lines snap to the same lines of every other object on the
 * page and to the page's own edges, margins and center, within a few pixels.
 * Pure geometry in layer pixels; arrange.ts draws the guides it returns.
 */

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface Snapped {
  dx: number;
  dy: number;
  /** Layer-pixel x positions of vertical guides, and y positions of horizontal ones. */
  guidesX: number[];
  guidesY: number[];
}

export const SNAP_PX = 6;

function lines(start: number, end: number): number[] {
  return [start, (start + end) / 2, end];
}

/** The closest pairing of one of `moving`'s lines with one of `targets`' lines, if any is within `limit`. */
function nearest(moving: number[], targets: number[], limit: number): { shift: number; at: number } | null {
  let best: { shift: number; at: number } | null = null;
  for (const m of moving) {
    for (const t of targets) {
      const shift = t - m;
      if (Math.abs(shift) <= limit && (!best || Math.abs(shift) < Math.abs(best.shift))) {
        best = { shift, at: t };
      }
    }
  }
  return best;
}

/** `moving` dragged by (`dx`, `dy`), snapped to `others` and to `page` (whose
 * `margin` in px also counts as a line). Snapping is per axis: an object can snap
 * horizontally and move freely vertically. */
export function snap(moving: Box, dx: number, dy: number, others: Box[], page: Box, margin: number): Snapped {
  const targetsX = [
    ...lines(page.left, page.right),
    page.left + margin,
    page.right - margin,
    ...others.flatMap((b) => lines(b.left, b.right)),
  ];
  const targetsY = [
    ...lines(page.top, page.bottom),
    page.top + margin,
    page.bottom - margin,
    ...others.flatMap((b) => lines(b.top, b.bottom)),
  ];
  const x = nearest(lines(moving.left + dx, moving.right + dx), targetsX, SNAP_PX);
  const y = nearest(lines(moving.top + dy, moving.bottom + dy), targetsY, SNAP_PX);
  return {
    dx: dx + (x?.shift ?? 0),
    dy: dy + (y?.shift ?? 0),
    guidesX: x ? [x.at] : [],
    guidesY: y ? [y.at] : [],
  };
}

/** EDT-13: how far each box must move for `action`. `page` is used when there
 * is only one box (it is aligned to the page instead of to the others). */
export function alignOffsets(boxes: Box[], action: AlignAction, page: Box, margin: number): [number, number][] {
  if (boxes.length === 0) {
    return [];
  }
  const single = boxes.length === 1;
  const left = single ? page.left + margin : Math.min(...boxes.map((b) => b.left));
  const right = single ? page.right - margin : Math.max(...boxes.map((b) => b.right));
  const top = single ? page.top + margin : Math.min(...boxes.map((b) => b.top));
  const bottom = single ? page.bottom - margin : Math.max(...boxes.map((b) => b.bottom));
  const centerX = single ? (page.left + page.right) / 2 : (left + right) / 2;
  const middleY = single ? (page.top + page.bottom) / 2 : (top + bottom) / 2;

  if (action === "distribute-h" || action === "distribute-v") {
    return distribute(boxes, action === "distribute-h");
  }
  return boxes.map((b) => {
    switch (action) {
      case "left":
        return [left - b.left, 0];
      case "center":
        return [centerX - (b.left + b.right) / 2, 0];
      case "right":
        return [right - b.right, 0];
      case "top":
        return [0, top - b.top];
      case "middle":
        return [0, middleY - (b.top + b.bottom) / 2];
      case "bottom":
        return [0, bottom - b.bottom];
    }
  });
}

export type AlignAction = "left" | "center" | "right" | "top" | "middle" | "bottom" | "distribute-h" | "distribute-v";

/** Equal gaps between boxes, the outermost two staying where they are. */
function distribute(boxes: Box[], horizontal: boolean): [number, number][] {
  const start = (b: Box): number => (horizontal ? b.left : b.top);
  const size = (b: Box): number => (horizontal ? b.right - b.left : b.bottom - b.top);
  const order = boxes.map((_, i) => i).sort((a, b) => start(boxes[a]) - start(boxes[b]));
  const first = boxes[order[0]];
  const last = boxes[order[order.length - 1]];
  const span = start(last) + size(last) - start(first);
  const gap = (span - boxes.reduce((sum, b) => sum + size(b), 0)) / Math.max(boxes.length - 1, 1);
  const offsets: [number, number][] = boxes.map(() => [0, 0]);
  let cursor = start(first);
  for (const i of order) {
    const shift = cursor - start(boxes[i]);
    offsets[i] = horizontal ? [shift, 0] : [0, shift];
    cursor += size(boxes[i]) + gap;
  }
  return offsets;
}
