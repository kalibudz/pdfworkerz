/**
 * EDT-08: selecting, moving, resizing, cropping, replacing, deleting and
 * inserting images on the current page. One box per image placement is
 * drawn in the edit layer, *under* the span boxes (style.css z-index), so
 * text on top of an image stays clickable for editing. Every change goes
 * through the journaled ops endpoint and ends in `onCommitted` -- nothing
 * here guesses at the page's state afterwards.
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, HistoryOp, ImageInfo } from "./api";
import type { ImageAction, InspectorHandle } from "./inspector";
import { cornerHandle, drag, layerToPageRect } from "./drag";
import { bboxToRect } from "./overlay";

export interface ImageToolOptions {
  api: Api;
  documentId: string;
  inspector: InspectorHandle;
  onCommitted: () => void;
}

export interface ImageToolHandle {
  /** Redraw the image boxes for a freshly rendered page (call after the
   * text overlay's own update, which clears the layer). */
  update(pageIndex: number, images: ImageInfo[], viewport: pdfjsLib.PageViewport): void;
  /** Run one of the inspector's image buttons against the selected image. */
  act(action: ImageAction): void;
  /** Prompt for an image file and place it in the middle of the current page. */
  insert(): void;
}

/** A file picker, resolved with the chosen file's bytes as base64 (or null). */
export function pickImageBase64(): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.className = "pw-image-file-input";
    input.hidden = true;
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      input.remove();
      if (!file) {
        resolve(null);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const url = String(reader.result ?? "");
        resolve(url.slice(url.indexOf(",") + 1));
      };
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(file);
    });
    document.body.appendChild(input);
    input.click();
  });
}

export function createImageTool(layer: HTMLElement, options: ImageToolOptions): ImageToolHandle {
  let pageIndex = 0;
  let viewport: pdfjsLib.PageViewport | null = null;
  let selected: { image: ImageInfo; box: HTMLElement } | null = null;
  let cropping = false;

  async function send(op: HistoryOp): Promise<void> {
    try {
      await options.api.applyOp(options.documentId, op);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The image change could not be saved.");
      return;
    }
    options.onCommitted();
  }

  /** Layer pixels (two corners) -> a MuPDF page rect, normalized. */
  function toPageRect(ax: number, ay: number, bx: number, by: number): [number, number, number, number] | null {
    return viewport ? layerToPageRect(viewport, ax, ay, bx, by) : null;
  }

  function deselect(): void {
    selected?.box.classList.remove("pw-image-selected", "pw-image-cropping");
    layer.querySelector(".pw-object-resize")?.remove();
    layer.querySelector(".pw-crop-rect")?.remove();
    selected = null;
    cropping = false;
  }

  function select(image: ImageInfo, box: HTMLElement): void {
    deselect();
    selected = { image, box };
    box.classList.add("pw-image-selected");
    options.inspector.showImage(image);

    cornerHandle(layer, box, (left, top, right, bottom) => {
      const rect = toPageRect(left, top, right, bottom);
      if (rect) {
        void send({ op: "move_image", page_index: pageIndex, index: image.index, rect });
      }
    });
  }

  function startCropDrag(image: ImageInfo, event: MouseEvent): void {
    const layerRect = layer.getBoundingClientRect();
    const ax = event.clientX - layerRect.left;
    const ay = event.clientY - layerRect.top;
    const outline = document.createElement("div");
    outline.className = "pw-crop-rect";
    layer.appendChild(outline);
    drag(
      event,
      (dx, dy) => {
        outline.style.left = `${Math.min(ax, ax + dx)}px`;
        outline.style.top = `${Math.min(ay, ay + dy)}px`;
        outline.style.width = `${Math.abs(dx)}px`;
        outline.style.height = `${Math.abs(dy)}px`;
      },
      (dx, dy) => {
        const rect = toPageRect(ax, ay, ax + dx, ay + dy);
        if (rect) {
          void send({ op: "crop_image", page_index: pageIndex, index: image.index, rect });
        }
      },
    );
  }

  function update(nextPageIndex: number, images: ImageInfo[], nextViewport: pdfjsLib.PageViewport): void {
    deselect();
    pageIndex = nextPageIndex;
    viewport = nextViewport;
    for (const image of images) {
      const rect = bboxToRect(nextViewport, image.rect);
      const box = document.createElement("div");
      box.className = "pw-image-box";
      box.style.left = `${rect.left}px`;
      box.style.top = `${rect.top}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
      box.dataset.index = String(image.index);
      box.addEventListener("mousedown", (event) => {
        if (selected?.box !== box) {
          select(image, box);
        }
        if (cropping) {
          startCropDrag(image, event);
          return;
        }
        drag(
          event,
          (dx, dy) => {
            box.style.transform = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
          },
          (dx, dy) => {
            const moved = toPageRect(rect.left + dx, rect.top + dy, rect.left + rect.width + dx, rect.top + rect.height + dy);
            if (moved) {
              void send({ op: "move_image", page_index: pageIndex, index: image.index, rect: moved });
            }
          },
        );
      });
      layer.appendChild(box);
    }
  }

  function act(action: ImageAction): void {
    const current = selected;
    if (!current) {
      return;
    }
    if (action === "delete") {
      void send({ op: "delete_image", page_index: pageIndex, index: current.image.index });
    } else if (action === "crop") {
      cropping = true;
      current.box.classList.add("pw-image-cropping");
    } else {
      void pickImageBase64().then((data) => {
        if (data) {
          void send({ op: "replace_image", page_index: pageIndex, index: current.image.index, image_base64: data });
        }
      });
    }
  }

  function insert(): void {
    const view = viewport;
    if (!view) {
      return;
    }
    const [vx0, vy0, vx1, vy1] = view.viewBox as number[];
    const width = vx1 - vx0;
    const height = vy1 - vy0;
    const size = Math.min(width, height) / 3;
    const rect = [(width - size) / 2, (height - size) / 2, (width + size) / 2, (height + size) / 2];
    const targetPage = pageIndex;
    void pickImageBase64().then((data) => {
      if (data) {
        void send({ op: "insert_image", page_index: targetPage, rect, image_base64: data });
      }
    });
  }

  // Clicking anywhere in the layer that isn't the selected image (or its
  // handle) drops the selection -- including clicking a span to edit it.
  layer.addEventListener("mousedown", (event) => {
    const target = event.target as HTMLElement;
    if (selected && !target.closest(".pw-image-box, .pw-object-resize")) {
      deselect();
    }
  });
  window.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement | null;
    const typing = target && (target.isContentEditable || target.tagName === "INPUT" || target.tagName === "TEXTAREA");
    if (selected && !typing && (event.key === "Delete" || event.key === "Backspace")) {
      event.preventDefault();
      act("delete");
    } else if (selected && event.key === "Escape") {
      deselect();
      options.inspector.showEmpty();
    }
  });

  return { update, act, insert };
}
