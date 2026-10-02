/**
 * EDT-09: vector shapes on the current page -- selecting one to move,
 * resize, restyle or delete it, and a draw mode for new lines, rectangles
 * and ellipses. Shape boxes sit under images and text boxes in the edit
 * layer (style.css z-index), larger shapes under smaller ones, and a shape
 * covering most of the page (typically a background fill) gets no box at
 * all -- otherwise it would swallow every click meant for the page.
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, HistoryOp, ShapeInfo } from "./api";
import type { ArrangeHandle } from "./arrange";
import { cornerHandle, drag, layerToPageRect } from "./drag";
import type { InspectorHandle, ShapeStyle } from "./inspector";
import { bboxToRect, viewportToMupdfPoint } from "./overlay";

export type DrawKind = "line" | "rect" | "ellipse";

export interface ShapeToolOptions {
  /** EDT-13..15: the shared selection (Shift+click, group drag, snapping, Delete). */
  arrange?: ArrangeHandle;
  api: Api;
  documentId: string;
  inspector: InspectorHandle;
  onCommitted: () => void;
  /** Before selecting this shape away from whatever (text) is selected now:
   * true to proceed, false (after asking) to leave things as they are. */
  confirmDiscard?: () => boolean;
}

export interface ShapeToolHandle {
  update(pageIndex: number, shapes: ShapeInfo[], viewport: pdfjsLib.PageViewport): void;
  /** Start drawing `kind` with the next drag on the page, or stop (null). */
  setDrawMode(kind: DrawKind | null): void;
  restyle(style: ShapeStyle): void;
  deleteSelected(): void;
  /** Show this shape (by index) as selected -- the shared selection landed on
   * it (after a move, a copy, or just a redraw that found it again). */
  selectIndex(index: number): void;
  /** Nothing selected without being highlighted: clear this shape's
   * selection, its handle and the inspector. */
  deselect(): void;
}

/** Shapes covering more than this share of the page get no selection box. */
const BACKGROUND_AREA_SHARE = 0.9;
/** Hairlines still get a box a person can click. */
const MIN_BOX_PX = 8;

export function createShapeTool(layer: HTMLElement, options: ShapeToolOptions): ShapeToolHandle {
  let pageIndex = 0;
  let viewport: pdfjsLib.PageViewport | null = null;
  let selected: { shape: ShapeInfo; box: HTMLElement } | null = null;
  let drawKind: DrawKind | null = null;
  let surface: HTMLElement | null = null;
  let currentShapes: ShapeInfo[] = [];

  async function send(op: HistoryOp): Promise<void> {
    try {
      await options.api.applyOp(options.documentId, op);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The shape change could not be saved.");
      return;
    }
    options.onCommitted();
  }

  function deselect(): void {
    selected?.box.classList.remove("pw-shape-selected");
    if (selected) {
      layer.querySelector(".pw-object-resize")?.remove();
    }
    selected = null;
  }

  function select(shape: ShapeInfo, box: HTMLElement): void {
    deselect();
    selected = { shape, box };
    box.classList.add("pw-shape-selected");
    options.inspector.showShape(shape);
    cornerHandle(layer, box, (left, top, right, bottom) => {
      if (viewport) {
        void send({
          op: "edit_shape",
          page_index: pageIndex,
          index: shape.index,
          rect: layerToPageRect(viewport, left, top, right, bottom),
        });
      }
    });
  }

  /** Draw mode: a transparent surface over the whole layer takes the drag. */
  function installSurface(): void {
    surface?.remove();
    surface = null;
    if (!drawKind) {
      return;
    }
    const pad = document.createElement("div");
    pad.className = "pw-draw-surface";
    pad.addEventListener("mousedown", (event) => {
      const kind = drawKind;
      const view = viewport;
      if (!kind || !view) {
        return;
      }
      const origin = layer.getBoundingClientRect();
      const ax = event.clientX - origin.left;
      const ay = event.clientY - origin.top;
      const ghost = document.createElement("div");
      ghost.className = `pw-draw-ghost pw-draw-ghost-${kind}`;
      layer.appendChild(ghost);
      drag(
        event,
        (dx, dy) => {
          ghost.style.left = `${Math.min(ax, ax + dx)}px`;
          ghost.style.top = `${Math.min(ay, ay + dy)}px`;
          ghost.style.width = `${Math.abs(dx)}px`;
          ghost.style.height = `${Math.abs(dy)}px`;
        },
        (dx, dy) => {
          ghost.remove();
          const [x0, y0, x1, y1] = layerToPageRect(view, ax, ay, ax + dx, ay + dy);
          // A line keeps its drag direction; boxes only need opposite corners.
          const points =
            kind === "line"
              ? [viewportToMupdfPoint(view, ax, ay), viewportToMupdfPoint(view, ax + dx, ay + dy)]
              : [
                  [x0, y0],
                  [x1, y1],
                ];
          void send({ op: "draw_shape", page_index: pageIndex, kind, points });
        },
      );
    });
    layer.appendChild(pad);
    surface = pad;
  }

  function update(nextPageIndex: number, shapes: ShapeInfo[], nextViewport: pdfjsLib.PageViewport): void {
    deselect();
    pageIndex = nextPageIndex;
    viewport = nextViewport;
    const [vx0, vy0, vx1, vy1] = nextViewport.viewBox as number[];
    const pageArea = (vx1 - vx0) * (vy1 - vy0);
    const area = (s: ShapeInfo): number => (s.rect[2] - s.rect[0]) * (s.rect[3] - s.rect[1]);
    const selectable = shapes.filter((s) => area(s) < pageArea * BACKGROUND_AREA_SHARE).sort((a, b) => area(b) - area(a));
    currentShapes = selectable;
    for (const shape of selectable) {
      const rect = bboxToRect(nextViewport, shape.rect);
      const width = Math.max(rect.width, MIN_BOX_PX);
      const height = Math.max(rect.height, MIN_BOX_PX);
      const box = document.createElement("div");
      box.className = "pw-shape-box";
      box.dataset.index = String(shape.index);
      box.style.left = `${rect.left - (width - rect.width) / 2}px`;
      box.style.top = `${rect.top - (height - rect.height) / 2}px`;
      box.style.width = `${width}px`;
      box.style.height = `${height}px`;
      box.addEventListener("mousedown", (event) => {
        if (options.arrange?.pointerDown("shape", shape.index, event)) {
          return;
        }
        if (selected?.box !== box) {
          if (options.confirmDiscard && !options.confirmDiscard()) {
            return;
          }
          // selectOnly first: it clears the other tools' own selections (overlay's
          // among them, which also resets the inspector to empty) before select()
          // below shows this shape there -- the other order let that reset run
          // *after* and wipe out the inspector content select() had just shown.
          options.arrange?.selectOnly("shape", shape.index);
          select(shape, box);
        }
        drag(
          event,
          (dx, dy) => {
            box.style.transform = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
          },
          (dx, dy) => {
            const moved = layerToPageRect(
              nextViewport,
              rect.left + dx,
              rect.top + dy,
              rect.left + rect.width + dx,
              rect.top + rect.height + dy,
            );
            void send({ op: "edit_shape", page_index: pageIndex, index: shape.index, rect: moved });
          },
          options.arrange?.snapper("shape", shape.index),
        );
      });
      layer.appendChild(box);
    }
    installSurface();
  }

  function setDrawMode(kind: DrawKind | null): void {
    drawKind = kind;
    deselect();
    installSurface();
  }

  function restyle(style: ShapeStyle): void {
    if (selected) {
      void send({ op: "edit_shape", page_index: pageIndex, index: selected.shape.index, ...style });
    }
  }

  function deleteSelected(): void {
    if (selected) {
      void send({ op: "delete_shape", page_index: pageIndex, index: selected.shape.index });
    }
  }

  function selectIndex(index: number): void {
    const shape = currentShapes.find((s) => s.index === index);
    const box = layer.querySelector<HTMLElement>(`.pw-shape-box[data-index="${index}"]`);
    if (shape && box) {
      select(shape, box);
    }
  }

  layer.addEventListener("mousedown", (event) => {
    const target = event.target as HTMLElement;
    if (selected && !target.closest(".pw-shape-box, .pw-object-resize, .pw-inspector")) {
      deselect();
    }
  });
  return { update, setDrawMode, restyle, deleteSelected, selectIndex, deselect };
}
