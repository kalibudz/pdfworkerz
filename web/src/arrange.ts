/**
 * EDT-13..EDT-15: one selection across text, images and shapes, and what
 * can be done with it, the way Nitro does it:
 *
 * - Click an object to select it; Shift+click or Ctrl/Cmd+click adds or removes; drag
 *   on empty page to select everything inside a rectangle. Esc clears (viewer.ts, since
 *   it has to clear the tool-level selections too, not just this shared one).
 * - Drag any selected object to move the whole selection; smart guides snap it to
 *   other objects and to the page (snap.ts). Hold Alt to drag without snapping.
 * - The Align menu aligns (left/center/right/top/middle/bottom) and distributes;
 *   one object is aligned to the page.
 * - Arrow keys nudge 1pt (Shift: 10pt); presses in quick succession are one move.
 * - Ctrl+C / Ctrl+V copy and paste (onto the page shown), Ctrl+D duplicates,
 *   Delete removes.
 *
 * EDT-16/EDT-18: text is selected in the toolbar's mode -- whole blocks
 * (paragraphs), lines or words, as the server groups them (GET .../text_units).
 * Every text ref carries its `unit` and `expect_text`, so the server refuses a
 * stale index instead of changing the wrong text. Every change is one
 * move_objects / duplicate_objects / delete_objects Op -- one undo -- and
 * afterwards the same objects, at their new places, are selected again: text
 * by its unit, text and moved origin, anything else by its moved rectangle.
 */

import type * as pdfjsLib from "pdfjs-dist";

import { ApiError, type Api, type HistoryOp, type ImageInfo, type ObjectRef, type SelectMode, type ShapeInfo, type TextUnit } from "./api";
import { applyWithApproval, confirmVerified } from "./approval";
import { bboxToRect, viewportToMupdfPoint } from "./overlay";
import { alignOffsets, snap, type AlignAction, type Box } from "./snap";

export type ObjectKind = "text" | "image" | "shape";

interface PageObject {
  kind: ObjectKind;
  /** Text: the unit's index (a block's is its first span index); images and shapes: their own index. */
  index: number;
  /** Text only: what the index counts, the unit's text and its first glyph's origin (page points). */
  unit?: SelectMode;
  text?: string;
  origin?: [number, number];
  spanIndices: number[];
  /** Page points, MuPDF space (y down). */
  pageRect: [number, number, number, number];
  /** Layer pixels. */
  box: Box;
}

export interface ArrangeOptions {
  api: Api;
  documentId: string;
  layer: HTMLElement;
  /** 0-based page shown in the viewer: where Ctrl+V pastes. */
  currentPage: () => number;
  onCommitted: () => void;
  /** The selection changed (count), for the toolbar's Align menu. */
  onSelectionChange: (count: number) => void;
  /** A plain click (no drag) landed on empty page: nothing under the pointer. */
  onEmptyClick: () => void;
  /** The selection just became exactly this one kind (a plain click through a tool's
   * own box): the other kinds' tool-level selection (highlight, handles, inspector)
   * should drop, since they're no longer part of what's selected. */
  onSelectOnly: (kind: ObjectKind) => void;
  /** A plain click (no drag) narrowed a multi-object selection down to this one:
   * the owning tool should show its own highlight, handles and inspector for it. */
  onActivate: (kind: ObjectKind, index: number) => void;
}

export interface ArrangeHandle {
  /** Call after the text, image and shape tools have drawn their boxes. `units`
   * are the page's text units in the current selection mode. */
  update(
    pageIndex: number,
    viewport: pdfjsLib.PageViewport,
    units: TextUnit[],
    images: ImageInfo[],
    shapes: ShapeInfo[],
  ): void;
  /** A tool's box got a mousedown. Handles Shift+click and dragging a multi-object
   * selection; returns true when it did, so the tool does nothing more. */
  pointerDown(kind: ObjectKind, index: number, event: MouseEvent): boolean;
  /** A plain click selected this object through its own tool. */
  selectOnly(kind: ObjectKind, index: number): void;
  /** For a single object's own drag: snap its (dx, dy) and draw guides. */
  snapper(kind: ObjectKind, index: number): DragSnapper;
  /** EDT-18: move one object by (dx, dy) page points, as a single-item move_objects. */
  move(kind: ObjectKind, index: number, dxPt: number, dyPt: number): void;
  /** The one selected object's kind and index, or null unless exactly one is selected. */
  sole(): { kind: ObjectKind; index: number } | null;
  count(): number;
  clear(): void;
  align(action: AlignAction): void;
  /** Arrow keys, Delete, Ctrl+C/V/D, Esc. Returns true when it used the key. */
  handleKey(event: KeyboardEvent): boolean;
}

export interface DragSnapper {
  adjust(dx: number, dy: number, event: MouseEvent): [number, number];
  end(): void;
}

const NUDGE_IDLE_MS = 400;
const PASTE_OFFSET_PT = 10;
const PAGE_MARGIN_PT = 36;
const REFIND_TOLERANCE_PT = 2.5;
/** A text unit is found again by its text and its first glyph's origin, this close. */
const ORIGIN_TOLERANCE_PT = 1;
const MARQUEE_THRESHOLD_PX = 4;

/** An object to select again after a change: where it should be now. */
interface Wanted {
  kind: ObjectKind;
  pageRect: [number, number, number, number];
  unit?: SelectMode;
  text?: string;
  origin?: [number, number];
  /** Images and shapes keep the same index across an in-place edit (unlike a text
   * unit's, which an edit can renumber), so it finds one reliably after a move too
   * big, or too far from `pageRect`'s stale guess, for the position match below --
   * a shape or image dragged through its own tool's box, rather than through this
   * module's moveSelection/align, never updates `pageRect` to the new place at all. */
  index?: number;
}

function unionBox(boxes: Box[]): Box {
  return {
    left: Math.min(...boxes.map((b) => b.left)),
    top: Math.min(...boxes.map((b) => b.top)),
    right: Math.max(...boxes.map((b) => b.right)),
    bottom: Math.max(...boxes.map((b) => b.bottom)),
  };
}

export function createArrange(options: ArrangeOptions): ArrangeHandle {
  const { layer } = options;
  let pageIndex = 0;
  let viewport: pdfjsLib.PageViewport | null = null;
  let objects: PageObject[] = [];
  let selected: PageObject[] = [];
  /** Full refs (unit and expect_text included), so a paste finds exactly what was copied. */
  let clipboard: { pageIndex: number; items: ObjectRef[] } | null = null;
  /** After a change: the objects to select again, by kind and where they should now be. */
  let reselect: Wanted[] | null = null;
  let busy = false;
  let nudge = { dx: 0, dy: 0, timer: null as ReturnType<typeof setTimeout> | null };

  function key(object: { kind: ObjectKind; index: number }): string {
    return `${object.kind}:${object.index}`;
  }

  function find(kind: ObjectKind, index: number): PageObject | undefined {
    return objects.find((o) => o.kind === kind && o.index === index);
  }

  function isSelected(object: PageObject): boolean {
    return selected.some((s) => key(s) === key(object));
  }

  /** The element on the layer that shows `object` (overlay.ts draws one box per text unit). */
  function elementsOf(object: PageObject): HTMLElement[] {
    const selector =
      object.kind === "text"
        ? `.pw-span-box[data-unit-index="${object.index}"]`
        : `.pw-${object.kind}-box[data-index="${object.index}"]`;
    const box = layer.querySelector<HTMLElement>(selector);
    return box ? [box] : [];
  }

  function drawOutlines(): void {
    layer.querySelectorAll(".pw-arrange-outline").forEach((el) => el.remove());
    if (selected.length < 2 && !selected.some((s) => s.kind === "text")) {
      options.onSelectionChange(selected.length);
      return; // an image or shape alone shows its own tool's selection
    }
    for (const object of selected) {
      const outline = document.createElement("div");
      outline.className = "pw-arrange-outline";
      outline.dataset.key = key(object);
      outline.style.left = `${object.box.left}px`;
      outline.style.top = `${object.box.top}px`;
      outline.style.width = `${object.box.right - object.box.left}px`;
      outline.style.height = `${object.box.bottom - object.box.top}px`;
      layer.appendChild(outline);
    }
    options.onSelectionChange(selected.length);
  }

  function setSelection(next: PageObject[]): void {
    selected = next;
    drawOutlines();
  }

  /** Show the selection shifted by (dx, dy) layer pixels, before anything is sent. */
  function previewShift(dx: number, dy: number): void {
    const shift = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
    for (const object of selected) {
      for (const element of elementsOf(object)) {
        element.style.transform = shift;
      }
    }
    layer.querySelectorAll<HTMLElement>(".pw-arrange-outline").forEach((el) => (el.style.transform = shift));
  }

  function drawGuides(guidesX: number[], guidesY: number[]): void {
    clearGuides();
    for (const x of guidesX) {
      const guide = document.createElement("div");
      guide.className = "pw-guide pw-guide-v";
      guide.style.left = `${x}px`;
      layer.appendChild(guide);
    }
    for (const y of guidesY) {
      const guide = document.createElement("div");
      guide.className = "pw-guide pw-guide-h";
      guide.style.top = `${y}px`;
      layer.appendChild(guide);
    }
  }

  function clearGuides(): void {
    layer.querySelectorAll(".pw-guide").forEach((el) => el.remove());
  }

  function pageBox(): Box {
    const [x0, y0, x1, y1] = (viewport?.viewBox as number[] | undefined) ?? [0, 0, 0, 0];
    const topLeft = viewport ? bboxToRect(viewport, [0, 0, x1 - x0, y1 - y0]) : { left: 0, top: 0, width: 0, height: 0 };
    return { left: topLeft.left, top: topLeft.top, right: topLeft.left + topLeft.width, bottom: topLeft.top + topLeft.height };
  }

  function marginPx(): number {
    return PAGE_MARGIN_PT * (viewport?.scale ?? 1);
  }

  /** Layer pixels -> page points, through the viewport (rotation-safe). */
  function toPoints(dx: number, dy: number): [number, number] {
    if (!viewport) {
      return [0, 0];
    }
    const [x0, y0] = viewportToMupdfPoint(viewport, 0, 0);
    const [x1, y1] = viewportToMupdfPoint(viewport, dx, dy);
    return [x1 - x0, y1 - y0];
  }

  function snapFor(moving: PageObject[], dx: number, dy: number, event: MouseEvent): [number, number] {
    if (event.altKey || moving.length === 0) {
      clearGuides();
      return [dx, dy];
    }
    const movingKeys = new Set(moving.map(key));
    const others = objects.filter((o) => !movingKeys.has(key(o))).map((o) => o.box);
    const result = snap(unionBox(moving.map((o) => o.box)), dx, dy, others, pageBox(), marginPx());
    drawGuides(result.guidesX, result.guidesY);
    return [result.dx, result.dy];
  }

  function refs(items: PageObject[], offsets?: [number, number][]): ObjectRef[] {
    return items.map((object, i) => ({
      kind: object.kind,
      page_index: pageIndex,
      index: object.index,
      ...(object.kind === "text" && object.unit ? { unit: object.unit, expect_text: object.text ?? null } : {}),
      ...(offsets ? { dx: offsets[i][0], dy: offsets[i][1] } : {}),
    }));
  }

  /** The server refused a text ref because that unit's text (or the page's units) changed. */
  function isStaleRef(error: unknown): boolean {
    return (
      error instanceof ApiError &&
      error.status === 400 &&
      (/check the page again/.test(error.detail) || /index \d+ is out of range/.test(error.detail))
    );
  }

  /** `staleMessage`: what to say instead of the server's words when a ref turned out stale. */
  async function send(op: HistoryOp, next: Wanted[], staleMessage?: string): Promise<void> {
    if (busy) {
      return;
    }
    busy = true;
    try {
      const applied = await applyWithApproval(options.api, options.documentId, op);
      if (!applied) {
        return;
      }
      await confirmVerified(options.api, options.documentId, applied.result);
      reselect = next;
    } catch (error) {
      window.alert(
        staleMessage && isStaleRef(error)
          ? staleMessage
          : error instanceof Error
            ? error.message
            : "The change could not be made.",
      );
      return;
    } finally {
      busy = false;
      previewShift(0, 0);
      clearGuides();
    }
    options.onCommitted();
  }

  function shifted(object: PageObject, dx: number, dy: number): Wanted {
    const [x0, y0, x1, y1] = object.pageRect;
    return {
      kind: object.kind,
      pageRect: [x0 + dx, y0 + dy, x1 + dx, y1 + dy],
      unit: object.unit,
      text: object.text,
      origin: object.origin ? [object.origin[0] + dx, object.origin[1] + dy] : undefined,
      index: object.kind === "text" ? undefined : object.index,
    };
  }

  function moveSelection(dxPt: number, dyPt: number): void {
    if (selected.length === 0 || (dxPt === 0 && dyPt === 0)) {
      return;
    }
    const items = [...selected];
    void send(
      { op: "move_objects", items: refs(items), dx: dxPt, dy: dyPt },
      items.map((o) => shifted(o, dxPt, dyPt)),
    );
  }

  function move(kind: ObjectKind, index: number, dxPt: number, dyPt: number): void {
    const object = find(kind, index);
    if (!object || (dxPt === 0 && dyPt === 0)) {
      return;
    }
    void send({ op: "move_objects", items: refs([object]), dx: dxPt, dy: dyPt }, [shifted(object, dxPt, dyPt)]);
  }

  /** A plain mousedown on a member of a 2+ selection: drags the whole group, unless
   * released close to where it started, which narrows the selection to just this one. */
  function groupDrag(event: MouseEvent, kind: ObjectKind, index: number): void {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startY = event.clientY;
    const moving = [...selected];
    const onMove = (e: MouseEvent): void => {
      const [dx, dy] = snapFor(moving, e.clientX - startX, e.clientY - startY, e);
      previewShift(dx, dy);
    };
    const onUp = (e: MouseEvent): void => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      const [dx, dy] = snapFor(moving, e.clientX - startX, e.clientY - startY, e);
      clearGuides();
      if (Math.hypot(dx, dy) < 3) {
        previewShift(0, 0);
        selectOnly(kind, index);
        options.onActivate(kind, index);
        return;
      }
      const [dxPt, dyPt] = toPoints(dx, dy);
      moveSelection(dxPt, dyPt);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function pointerDown(kind: ObjectKind, index: number, event: MouseEvent): boolean {
    const object = find(kind, index);
    if (!object || event.button !== 0) {
      return false;
    }
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      event.preventDefault();
      event.stopPropagation();
      setSelection(isSelected(object) ? selected.filter((s) => key(s) !== key(object)) : [...selected, object]);
      return true;
    }
    if (selected.length > 1 && isSelected(object)) {
      groupDrag(event, kind, index);
      return true;
    }
    return false;
  }

  function selectOnly(kind: ObjectKind, index: number): void {
    const object = find(kind, index);
    setSelection(object ? [object] : []);
    options.onSelectOnly(kind);
  }

  function snapper(kind: ObjectKind, index: number): DragSnapper {
    const object = find(kind, index);
    return {
      adjust: (dx, dy, event) => (object ? snapFor([object], dx, dy, event) : [dx, dy]),
      end: clearGuides,
    };
  }

  function align(action: AlignAction): void {
    if (selected.length === 0 || (action.startsWith("distribute") && selected.length < 3)) {
      return;
    }
    const items = [...selected];
    // Page points throughout, so what is sent is exactly what was computed.
    const toBox = (r: [number, number, number, number]): Box => ({ left: r[0], top: r[1], right: r[2], bottom: r[3] });
    const [vx0, vy0, vx1, vy1] = (viewport?.viewBox as number[] | undefined) ?? [0, 0, 0, 0];
    const page: Box = { left: 0, top: 0, right: vx1 - vx0, bottom: vy1 - vy0 };
    const offsets = alignOffsets(items.map((o) => toBox(o.pageRect)), action, page, PAGE_MARGIN_PT);
    const moving = items
      .map((object, i) => ({ object, offset: offsets[i] }))
      .filter(({ offset }) => Math.abs(offset[0]) > 0.05 || Math.abs(offset[1]) > 0.05);
    if (moving.length === 0) {
      return;
    }
    void send(
      {
        op: "move_objects",
        items: refs(
          moving.map((m) => m.object),
          moving.map((m) => m.offset),
        ),
      },
      items.map((o, i) => shifted(o, offsets[i][0], offsets[i][1])),
    );
  }

  function flushNudge(): void {
    const { dx, dy } = nudge;
    nudge = { dx: 0, dy: 0, timer: null };
    moveSelection(dx, dy);
  }

  function handleKey(event: KeyboardEvent): boolean {
    const modifier = event.ctrlKey || event.metaKey;
    const lower = event.key.toLowerCase();
    if (modifier && lower === "c" && selected.length) {
      clipboard = { pageIndex, items: refs(selected) };
      return true;
    }
    if (modifier && (lower === "v" || lower === "d")) {
      const source = lower === "d" ? (selected.length ? { pageIndex, items: refs(selected) } : null) : clipboard;
      if (!source || busy) {
        return Boolean(source);
      }
      const target = lower === "d" ? pageIndex : options.currentPage();
      const offset = target === source.pageIndex ? PASTE_OFFSET_PT : 0;
      // The copies land where the originals are, shifted: they are selected there afterwards.
      const originals =
        source.pageIndex === pageIndex
          ? source.items.map((ref) =>
              objects.find(
                (o) =>
                  o.kind === ref.kind &&
                  o.index === ref.index &&
                  (ref.kind !== "text" || (o.unit === ref.unit && o.text === ref.expect_text)),
              ),
            )
          : [];
      void send(
        {
          op: "duplicate_objects",
          items: source.items,
          dx: offset,
          dy: offset,
          target_page_index: target,
        },
        originals.filter((o): o is PageObject => o !== undefined).map((o) => shifted(o, offset, offset)),
        lower === "v" ? "The copied text has changed since you copied it. Select it and copy it again." : undefined,
      );
      return true;
    }
    if (selected.length === 0) {
      return false;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      void send({ op: "delete_objects", items: refs(selected) }, []);
      setSelection([]);
      return true;
    }
    const step = event.shiftKey ? 10 : 1;
    const arrows: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const arrow = arrows[event.key];
    if (!arrow || busy) {
      return Boolean(arrow);
    }
    nudge.dx += arrow[0];
    nudge.dy += arrow[1];
    const scale = viewport?.scale ?? 1;
    previewShift(nudge.dx * scale, nudge.dy * scale);
    if (nudge.timer) {
      clearTimeout(nudge.timer);
    }
    nudge.timer = setTimeout(flushNudge, NUDGE_IDLE_MS);
    return true;
  }

  // Marquee: a drag that starts on empty page (not on any object box) selects
  // every object entirely inside the dragged rectangle.
  layer.parentElement?.addEventListener("mousedown", (event) => {
    const target = event.target as HTMLElement;
    const onEmptyPage = target === layer || target.tagName === "CANVAS";
    if (!onEmptyPage || event.button !== 0 || layer.classList.contains("pw-adding-text") || layer.querySelector(".pw-draw-surface")) {
      return;
    }
    const layerRect = layer.getBoundingClientRect();
    const ax = event.clientX - layerRect.left;
    const ay = event.clientY - layerRect.top;
    let band: HTMLElement | null = null;
    const onMove = (e: MouseEvent): void => {
      const bx = e.clientX - layerRect.left;
      const by = e.clientY - layerRect.top;
      if (!band && Math.hypot(bx - ax, by - ay) < MARQUEE_THRESHOLD_PX) {
        return;
      }
      band ??= layer.appendChild(Object.assign(document.createElement("div"), { className: "pw-marquee" }));
      band.style.left = `${Math.min(ax, bx)}px`;
      band.style.top = `${Math.min(ay, by)}px`;
      band.style.width = `${Math.abs(bx - ax)}px`;
      band.style.height = `${Math.abs(by - ay)}px`;
    };
    const onUp = (e: MouseEvent): void => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (!band) {
        if (!e.shiftKey) {
          options.onEmptyClick(); // a plain click on empty page
        }
        return;
      }
      band.remove();
      const bx = e.clientX - layerRect.left;
      const by = e.clientY - layerRect.top;
      const area: Box = { left: Math.min(ax, bx), top: Math.min(ay, by), right: Math.max(ax, bx), bottom: Math.max(ay, by) };
      const inside = objects.filter(
        (o) => o.box.left >= area.left && o.box.right <= area.right && o.box.top >= area.top && o.box.bottom <= area.bottom,
      );
      setSelection(e.shiftKey ? [...selected, ...inside.filter((o) => !isSelected(o))] : inside);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  function update(
    nextPageIndex: number,
    nextViewport: pdfjsLib.PageViewport,
    units: TextUnit[],
    images: ImageInfo[],
    shapes: ShapeInfo[],
  ): void {
    const samePage = nextPageIndex === pageIndex;
    const previous: Wanted[] = samePage ? selected.map((o) => shifted(o, 0, 0)) : [];
    pageIndex = nextPageIndex;
    viewport = nextViewport;
    const make = (kind: ObjectKind, index: number, spanIndices: number[], rect: [number, number, number, number]): PageObject => {
      const r = bboxToRect(nextViewport, rect);
      return { kind, index, spanIndices, pageRect: rect, box: { left: r.left, top: r.top, right: r.left + r.width, bottom: r.top + r.height } };
    };
    const makeText = (u: TextUnit): PageObject => ({
      ...make("text", u.index, u.span_indices, u.bbox),
      unit: u.granularity,
      text: u.text,
      origin: u.origin,
    });
    const shapeBoxes = new Set(
      [...layer.querySelectorAll<HTMLElement>(".pw-shape-box")].map((el) => Number(el.dataset.index)),
    );
    objects = [
      ...units.map(makeText),
      ...images.map((i) => make("image", i.index, [], i.rect)),
      // Only shapes the shape tool made selectable (not page backgrounds).
      ...shapes.filter((s) => shapeBoxes.has(s.index)).map((s) => make("shape", s.index, [], s.rect)),
    ];
    // Select again: the objects just changed, where they should be now; otherwise the
    // same objects as before, if they are still where they were (a zoom, say).
    const wanted = reselect ?? previous;
    reselect = null;
    const near = (a: readonly number[], b: readonly number[], tolerance = REFIND_TOLERANCE_PT): boolean =>
      a.every((v, i) => Math.abs(v - b[i]) <= tolerance);
    // Text: the same unit and text at the (moved) origin; anything else, or text that
    // changed shape, by its (moved) rectangle.
    const byText = (w: Wanted): PageObject | undefined => {
      const origin = w.origin;
      if (w.kind !== "text" || !origin || w.text === undefined) {
        return undefined;
      }
      return objects.find(
        (o) => o.kind === "text" && o.unit === w.unit && o.text === w.text && !!o.origin && near(o.origin, origin, ORIGIN_TOLERANCE_PT),
      );
    };
    // An image or shape: by its own index, which an in-place edit never renumbers --
    // the one way this reliably survives a move through the object's own tool (a drag
    // on its box, or a crop/replace/restyle), none of which update `pageRect` here to
    // match. Only when that fails (the object is gone) does the moved rectangle matter.
    const byIndex = (w: Wanted): PageObject | undefined =>
      w.kind === "text" || w.index === undefined ? undefined : objects.find((o) => o.kind === w.kind && o.index === w.index);
    const found = wanted
      .map(
        (w) =>
          byText(w) ?? byIndex(w) ?? objects.find((o) => o.kind === w.kind && o.unit === w.unit && near(o.pageRect, w.pageRect)),
      )
      .filter((o): o is PageObject => o !== undefined);
    setSelection([...new Map(found.map((o) => [key(o), o])).values()]);
  }

  return {
    update,
    pointerDown,
    selectOnly,
    snapper,
    move,
    sole: () => (selected.length === 1 ? { kind: selected[0].kind, index: selected[0].index } : null),
    count: () => selected.length,
    clear: () => setSelection([]),
    align,
    handleKey,
  };
}
