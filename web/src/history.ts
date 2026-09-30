/**
 * UI-04: the history panel (SPEC.md section 8.1's footer strip). Backed
 * entirely by capabilities COR-11 already built and tested -- GET/POST
 * .../history, .../undo, .../redo -- so there's no new server code here,
 * only the display and the wiring.
 *
 * One real limitation, not papered over: `journal.history` (and so the
 * API's HistoryResponse) is a list of the Ops as they were *requested*
 * (an Op's own fields), not the EditResult each one produced -- tier,
 * confidence and verification aren't part of it. Entries below describe
 * what was asked for, not how well it went; that richer per-edit summary
 * SPEC.md's own mockup shows ("14 hits, Exact") would need the history
 * endpoint to start recording results too, which is out of this
 * feature's scope as tracked (tracker/features.json's UI-04 is just
 * "History panel with undo/redo").
 */

import type { Api, HistoryOp } from "./api";

export interface HistoryHandle {
  /** Re-fetches history from the server and re-renders. Call this after
   * anything that could have changed it -- a committed edit, or this
   * panel's own undo/redo. */
  refresh(): Promise<void>;
  /** UI-08's Ctrl/Cmd+Z and +Shift+Z: exactly what clicking the Undo/Redo
   * button does, including doing nothing when the button is disabled
   * (native `HTMLButtonElement.click()` on a disabled button never fires
   * its listener), so a keyboard shortcut can never undo/redo past what
   * the visible button state already allows. */
  triggerUndo(): void;
  triggerRedo(): void;
}

export interface HistoryOptions {
  api: Api;
  documentId: string;
  /** The document's content changed (an undo or redo just happened) --
   * the caller must reload the page render, spans and thumbnails; this
   * panel does not do any of that itself. */
  onChanged: () => void;
}

function truncate(text: string, max = 40): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function quote(text: unknown): string {
  return `"${truncate(String(text ?? ""))}"`;
}

function pageLabel(op: HistoryOp): string {
  const pageIndex = op.page_index;
  return typeof pageIndex === "number" ? `page ${pageIndex + 1}` : "all pages";
}

/** A human-readable description of what was asked for -- switches on the
 * Op's own discriminator field, matching engine/ops/text.py's registered
 * Op shapes field-for-field. Never guesses at an Op this doesn't
 * recognize; it falls back to the raw op name instead. */
function describeOp(op: HistoryOp): string {
  switch (op.op) {
    case "replace_text":
      return `Replace ${quote(op.match)} with ${quote(op.replacement)} (${pageLabel(op)})`;
    case "delete_text":
      return `Delete ${quote(op.match)} (${pageLabel(op)})`;
    case "restyle_text": {
      const changes: string[] = [];
      if (typeof op.size === "number") {
        changes.push(`size ${op.size}pt`);
      }
      if (Array.isArray(op.color)) {
        changes.push("color");
      }
      return `Restyle ${quote(op.match)} (${changes.join(", ") || "no change"}, ${pageLabel(op)})`;
    }
    case "insert_text":
      return `Insert ${quote(op.text)} near ${quote(op.reference_match)} (${pageLabel(op)})`;
    case "reflow_text":
      return `Reflow paragraph containing ${quote(op.match)} (${pageLabel(op)})`;
    case "replace_span_text":
      return `Replace text with ${quote(op.new_text)} (${pageLabel(op)})`;
    case "edit_span": {
      const changes: string[] = [];
      if (typeof op.font === "string") changes.push(op.font);
      if (op.bold === true) changes.push("bold");
      if (op.bold === false) changes.push("not bold");
      if (op.italic === true) changes.push("italic");
      if (op.italic === false) changes.push("not italic");
      if (typeof op.size === "number") changes.push(`${op.size}pt`);
      if (Array.isArray(op.color)) changes.push("color");
      const style = changes.length ? `, ${changes.join(", ")}` : "";
      const what = typeof op.new_text === "string" ? `Edit text to ${quote(op.new_text)}` : "Restyle text";
      return `${what} (${pageLabel(op)}${style})`;
    }
    case "add_link":
      return `Add link ${typeof op.uri === "string" ? `to ${quote(op.uri)}` : "to a page"} (${pageLabel(op)})`;
    case "update_link":
      return `Edit link (${pageLabel(op)})`;
    case "remove_link":
      return `Remove link (${pageLabel(op)})`;
    case "insert_image":
      return `Insert image (${pageLabel(op)})`;
    case "replace_image":
      return `Replace image (${pageLabel(op)})`;
    case "move_image":
      return `Move/resize image (${pageLabel(op)})`;
    case "crop_image":
      return `Crop image (${pageLabel(op)})`;
    case "delete_image":
      return `Delete image (${pageLabel(op)})`;
    case "correct_word":
      return `Correct ${quote(op.word)} to ${quote(op.replacement)} (${pageLabel(op)})`;
    case "draw_shape":
      return `Draw ${typeof op.kind === "string" ? op.kind : "shape"} (${pageLabel(op)})`;
    case "edit_shape":
      return `Edit shape (${pageLabel(op)})`;
    case "delete_shape":
      return `Delete shape (${pageLabel(op)})`;
    case "move_text_block":
      return typeof op.width === "number"
        ? `Resize paragraph to ${Math.round(op.width)}pt (${pageLabel(op)})`
        : `Move paragraph (${pageLabel(op)})`;
    case "copy_style":
      return `Copy style onto text (${pageLabel({ op: op.op, page_index: op.target_page_index })})`;
    default:
      return op.op;
  }
}

export function createHistoryPanel(container: HTMLElement, options: HistoryOptions): HistoryHandle {
  container.innerHTML = "";
  container.className = "pw-history";

  const label = document.createElement("span");
  label.className = "pw-history-label";
  label.textContent = "History:";

  const list = document.createElement("div");
  list.className = "pw-history-list";

  const undoButton = document.createElement("button");
  undoButton.type = "button";
  undoButton.textContent = "↶ Undo";
  undoButton.title = "Undo (Ctrl/Cmd+Z)";
  undoButton.disabled = true;

  const redoButton = document.createElement("button");
  redoButton.type = "button";
  redoButton.textContent = "↷ Redo";
  redoButton.title = "Redo (Ctrl/Cmd+Shift+Z)";
  redoButton.disabled = true;

  container.append(label, list, undoButton, redoButton);

  async function refresh(): Promise<void> {
    const state = await options.api.history(options.documentId);
    list.innerHTML = "";
    if (state.ops.length === 0) {
      const empty = document.createElement("span");
      empty.className = "pw-history-empty";
      empty.textContent = "No edits yet";
      list.appendChild(empty);
    } else {
      state.ops.forEach((op, index) => {
        if (index > 0) {
          const separator = document.createElement("span");
          separator.className = "pw-history-separator";
          separator.textContent = "·";
          list.appendChild(separator);
        }
        const entry = document.createElement("span");
        entry.className = "pw-history-entry";
        entry.textContent = describeOp(op);
        entry.title = JSON.stringify(op, (_key, value: unknown) =>
          typeof value === "string" && value.length > 80 ? `${value.slice(0, 77)}…` : value,
        );
        if (index === state.ops.length - 1) {
          entry.classList.add("pw-history-current");
        }
        list.appendChild(entry);
      });
      list.scrollLeft = list.scrollWidth;
    }
    undoButton.disabled = !state.can_undo;
    redoButton.disabled = !state.can_redo;
  }

  // On success, `options.onChanged()` triggers viewer.ts's reloadDocument,
  // which ends with its own history.refresh() -- refreshing here too would
  // just double-fetch. refresh() is only needed on failure, to re-sync the
  // buttons' disabled state (e.g. a race lost a 409) since nothing else will.
  undoButton.addEventListener("click", () => {
    undoButton.disabled = true;
    void options.api
      .undo(options.documentId)
      .then(() => options.onChanged())
      .catch(() => refresh());
  });
  redoButton.addEventListener("click", () => {
    redoButton.disabled = true;
    void options.api
      .redo(options.documentId)
      .then(() => options.onChanged())
      .catch(() => refresh());
  });

  return {
    refresh,
    triggerUndo: () => undoButton.click(),
    triggerRedo: () => redoButton.click(),
  };
}
