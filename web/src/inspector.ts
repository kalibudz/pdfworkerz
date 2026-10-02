/**
 * UI-03: the inspector panel (SPEC.md section 8.1) -- detected font, size,
 * color, spacing and the live match-tier confidence for whichever span
 * UI-02's overlay currently has selected. Pure presentation: overlay.ts owns
 * *when* a span is selected and *when* a fresh preview arrives; this module
 * only ever renders whatever it's told to.
 */

import type { ImageInfo, LinkInfo, PreviewResult, SelectMode, ShapeInfo, SpanTrace, TextUnit } from "./api";
import { styleFromFontName, type StyleChoice } from "./styledialog";

/** A style control of the text editor. */
export type StyleField = "font" | "size" | "color" | "bold" | "italic";

/** UI-03: what the inspector's text editor holds -- the wording plus the style to draw
 * it in. `font` is "" for "keep the original font". EDT-16: `mixed` lists the style
 * fields that differ across the selected unit's spans and that the user has not
 * touched -- those keep each span's own value and must not be sent. */
export interface TextDraft extends StyleChoice {
  mixed: StyleField[];
}

const UNIT_LABELS: Record<SelectMode, string> = { block: "Block", line: "Line", word: "Word" };
const EMPTY_HINT = "Click text on the page to inspect it. Block, Line or Word in the toolbar decides how much one click selects.";

/** EDT-16: the style fields that are not the same across every one of `spans`. */
export function mixedStyleFields(spans: SpanTrace[]): StyleField[] {
  const differs = (value: (s: SpanTrace) => string): boolean => new Set(spans.map(value)).size > 1;
  const fields: StyleField[] = [];
  if (differs((s) => s.style.font)) fields.push("font");
  if (differs((s) => s.style.size.toFixed(1))) fields.push("size");
  if (differs((s) => toHexColor(s.style.color))) fields.push("color");
  if (differs((s) => String(styleFromFontName(s.style.font).bold))) fields.push("bold");
  if (differs((s) => String(styleFromFontName(s.style.font).italic))) fields.push("italic");
  return fields;
}

export interface InspectorHandle {
  /** Nothing selected -- the panel's resting state. `note` replaces the usual
   * hint once (e.g. "Applied. Undo with Ctrl+Z." right after a commit). */
  showEmpty(note?: string): void;
  /** EDT-16: a unit of text (block, line or word) was just selected, before any
   * preview has come back for it. `spans` are the unit's own spans, first one
   * first: the editor starts from the unit's text and the first span's style,
   * and style fields that differ across `spans` show as "(mixed)". `links` are
   * the page's links overlapping it (EDT-10). The draft is reset unless
   * `keepDraft` (the same unit, shown again after a zoom or re-render). */
  showUnit(unit: TextUnit, spans: SpanTrace[], links?: LinkInfo[], keepDraft?: boolean): void;
  /** EDT-16: the format painter is span-level; Word mode turns its button off. */
  setPainterEnabled(enabled: boolean): void;
  /** Put the cursor in the text editor, with its text selected. */
  focusEditor(): void;
  /** The editor's draft differs from the selected span's text and style. */
  isDirty(): boolean;
  /** The draft as it stands. */
  draft(): TextDraft;
  /** Say what an edit is doing (null clears); `busy` locks the editor meanwhile. */
  setStatus(message: string | null, busy?: boolean): void;
  /** The font families changed (a font was added to the library): reload the list. */
  reloadFamilies(): void;
  /** The latest preview result for the currently-shown span, or null while
   * one is in flight (SPEC.md never guesses at a match tier it hasn't
   * actually computed). */
  setPreview(preview: PreviewResult | null): void;
  /** EDT-07: show (or clear, with null) the format painter's armed state --
   * `sourceText` is the span whose style is waiting to be applied. */
  setPainter(sourceText: string | null): void;
  /** EDT-08: an image placement was selected instead of a span. */
  showImage(image: ImageInfo): void;
  /** EDT-09: a vector shape was selected. */
  showShape(shape: ShapeInfo): void;
}

/** EDT-09: the style fields of an edit_shape Op. */
export interface ShapeStyle {
  stroke_color?: [number, number, number];
  fill_color?: [number, number, number];
  no_fill?: boolean;
  line_width?: number;
}

function fromHexColor(hex: string): [number, number, number] {
  const value = parseInt(hex.slice(1), 16);
  return [((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
}

export type ImageAction = "replace" | "crop" | "delete";

export interface InspectorOptions {
  /** EDT-07: the "Copy style" button was pressed for the shown span. */
  onCopyStyle?: () => void;
  /** UI-03: the text editor's draft changed (typed, or a style control changed). */
  onDraftChange?: (draft: TextDraft) => void;
  /** UI-03: Apply (or Enter) -- commit the draft to the selected span. */
  onApply?: (draft: TextDraft) => void;
  /** The font families the editor's font list offers (GET /fonts). */
  loadFamilies?: () => Promise<string[]>;
  /** EDT-10: add a link over the shown span. */
  onAddLink?: () => void;
  /** EDT-10: retarget, or remove, one of the shown span's links. */
  onEditLink?: (link: LinkInfo) => void;
  onRemoveLink?: (link: LinkInfo) => void;
  /** EDT-08: one of the selected image's buttons was pressed. */
  onImageAction?: (action: ImageAction) => void;
  /** EDT-09: the selected shape's "Apply" / "Delete" buttons. */
  onShapeStyle?: (style: ShapeStyle) => void;
  onShapeDelete?: () => void;
}

function describeLink(link: LinkInfo): string {
  if (link.kind === "uri") {
    return `→ ${link.uri ?? ""}`;
  }
  if (link.kind === "goto" && link.target_page !== null) {
    return `→ page ${link.target_page + 1}`;
  }
  return "→ (other link type)";
}

/** A button that doesn't steal focus from the span being edited -- see the
 * "Copy style" button below for why that matters. */
function panelButton(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", onClick);
  return button;
}

const TIER_LABELS: Record<string, string> = {
  exact: "Exact",
  approximate: "Approximate",
  fallback: "Fallback",
};

function formatFontName(font: string): string {
  const subsetMatch = /^([A-Z]{6})\+(.+)$/.exec(font);
  return subsetMatch ? `${subsetMatch[2]} (subset ${subsetMatch[1]}+)` : font;
}

export function toHexColor([r, g, b]: readonly [number, number, number]): string {
  const channel = (value: number): string =>
    Math.round(Math.min(Math.max(value, 0), 1) * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${channel(r)}${channel(g)}${channel(b)}`.toUpperCase();
}

interface Row {
  row: HTMLElement;
  /** The value cell itself -- prepend an icon/swatch into this. */
  value: HTMLElement;
  /** A dedicated text node inside `value`, separate from any prepended
   * icon/swatch, so setting text never overwrites (or ends up rendered
   * inside) that icon. */
  text: Text;
}

function row(label: string, className = "pw-inspector-row"): Row {
  const rowEl = document.createElement("div");
  rowEl.className = className;
  const labelEl = document.createElement("span");
  labelEl.className = "pw-inspector-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "pw-inspector-value";
  const text = document.createTextNode("");
  valueEl.appendChild(text);
  rowEl.append(labelEl, valueEl);
  return { row: rowEl, value: valueEl, text };
}

export function createInspector(container: HTMLElement, options: InspectorOptions = {}): InspectorHandle {
  container.innerHTML = "";
  container.className = "pw-inspector";

  const heading = document.createElement("h2");
  heading.textContent = "Inspector";
  container.appendChild(heading);

  const empty = document.createElement("p");
  empty.className = "pw-hint pw-inspector-empty";
  empty.textContent = EMPTY_HINT;
  container.appendChild(empty);

  // UI-03: the text editor. Clicking a span puts the cursor here; nothing typed is lost
  // by clicking elsewhere with an unapplied draft -- that asks first (owner, 2026-10-01).
  const editor = document.createElement("div");
  editor.className = "pw-edit-section";
  editor.hidden = true;
  const editHeading = document.createElement("h3");
  editHeading.className = "pw-inspector-subheading";
  editHeading.textContent = "Edit text ";
  // EDT-16: which kind of unit is selected -- Block, Line or Word.
  const unitKind = document.createElement("span");
  unitKind.className = "pw-unit-kind";
  editHeading.appendChild(unitKind);
  // FNT-20: badge shown instead of "Word" when the selected unit is an icon/emoji glyph.
  const iconBadge = document.createElement("span");
  iconBadge.className = "pw-icon-badge";
  iconBadge.textContent = "(icon)";
  iconBadge.hidden = true;
  editHeading.appendChild(iconBadge);
  const editText = document.createElement("textarea");
  editText.id = "pw-edit-text";
  editText.rows = 2;
  editText.setAttribute("aria-label", "Replacement text");
  const editFont = document.createElement("select");
  editFont.id = "pw-edit-font";
  const keepFontOption = document.createElement("option");
  keepFontOption.value = "";
  editFont.appendChild(keepFontOption);
  let familiesLoaded = false;
  const editSize = document.createElement("input");
  editSize.type = "number";
  editSize.id = "pw-edit-size";
  editSize.min = "1";
  editSize.max = "400";
  editSize.step = "0.5";
  const editColor = document.createElement("input");
  editColor.type = "color";
  editColor.id = "pw-edit-color";
  const editBold = document.createElement("input");
  editBold.type = "checkbox";
  editBold.id = "pw-edit-bold";
  const editItalic = document.createElement("input");
  editItalic.type = "checkbox";
  editItalic.id = "pw-edit-italic";
  function editField(label: string, control: HTMLElement, className = "pw-edit-field"): HTMLLabelElement {
    const wrapper = document.createElement("label");
    wrapper.className = className;
    const caption = document.createElement("span");
    caption.textContent = label;
    wrapper.append(caption, control);
    return wrapper;
  }
  // EDT-16: "(mixed)" beside the color control while the unit's spans differ in color.
  const colorMixed = document.createElement("span");
  colorMixed.className = "pw-mixed-note";
  colorMixed.textContent = "(mixed)";
  colorMixed.hidden = true;
  const colorField = editField("Color", editColor);
  colorField.appendChild(colorMixed);
  const styleRow = document.createElement("div");
  styleRow.className = "pw-edit-style-row";
  styleRow.append(
    editField("Size", editSize),
    colorField,
    editField("Bold", editBold, "pw-edit-check"),
    editField("Italic", editItalic, "pw-edit-check"),
  );
  const editButtons = document.createElement("div");
  editButtons.className = "pw-edit-buttons";
  const applyButton = panelButton("Apply", "pw-edit-apply", () => apply());
  applyButton.id = "pw-edit-apply";
  applyButton.title = "Apply the change to the page (Enter)";
  const revertButton = panelButton("Revert", "pw-edit-revert", () => revert());
  revertButton.id = "pw-edit-revert";
  revertButton.title = "Go back to the text and style on the page (Esc)";
  editButtons.append(applyButton, revertButton);
  const editStatus = document.createElement("p");
  editStatus.className = "pw-edit-status";
  editStatus.setAttribute("role", "status");
  editStatus.hidden = true;
  const editHint = document.createElement("p");
  editHint.className = "pw-hint";
  const EDIT_HINT = "Enter applies, Esc reverts. Esc again returns the keyboard to the page: arrows nudge, Delete removes.";
  const ICON_HINT = "This is an icon or emoji glyph, kept as its own item so edits never redraw it away. It can be deleted on its own, but not retyped, restyled or moved yet.";
  editHint.textContent = EDIT_HINT;
  editor.append(
    editHeading,
    editField("Text", editText, "pw-edit-field pw-edit-text-field"),
    editField("Font", editFont),
    styleRow,
    editButtons,
    editStatus,
    editHint,
  );
  container.appendChild(editor);

  let original: TextDraft | null = null;
  /** EDT-16: the style fields still showing "(mixed)": untouched since the unit was shown. */
  let mixed = new Set<StyleField>();
  /** FNT-20: the selected unit is an icon/emoji glyph -- text and style controls stay
   * disabled regardless of busy state, since any such edit would be refused anyway. */
  let iconLocked = false;

  function readDraft(): TextDraft {
    return {
      text: editText.value,
      font: editFont.value,
      size: Number(editSize.value) || (original?.size ?? 0),
      color: fromHexColor(editColor.value),
      bold: editBold.checked,
      italic: editItalic.checked,
      mixed: [...mixed],
    };
  }

  const mixedControls = [
    ["size", editSize],
    ["color", editColor],
    ["bold", editBold],
    ["italic", editItalic],
  ] as const;

  function showMixed(): void {
    editSize.placeholder = mixed.has("size") ? "(mixed)" : "";
    if (mixed.has("size")) {
      editSize.value = "";
    }
    colorMixed.hidden = !mixed.has("color");
    editBold.indeterminate = mixed.has("bold");
    editItalic.indeterminate = mixed.has("italic");
    for (const [field, control] of mixedControls) {
      control.toggleAttribute("data-mixed", mixed.has(field));
    }
  }

  function writeDraft(draft: TextDraft): void {
    editText.value = draft.text;
    editFont.value = draft.font;
    editSize.value = String(Math.round(draft.size * 10) / 10);
    editColor.value = toHexColor(draft.color).toLowerCase();
    editBold.checked = draft.bold;
    editItalic.checked = draft.italic;
    mixed = new Set(draft.mixed);
    showMixed();
  }

  function isDirty(): boolean {
    if (!original) {
      return false;
    }
    const draft = readDraft();
    // A "(mixed)" field the user touched counts even if it now equals the first span's value.
    const touchedMixed = original.mixed.some((field) => !mixed.has(field));
    return (
      touchedMixed ||
      draft.text !== original.text ||
      draft.font !== original.font ||
      Math.abs(draft.size - original.size) > 0.05 ||
      toHexColor(draft.color) !== toHexColor(original.color) ||
      draft.bold !== original.bold ||
      draft.italic !== original.italic
    );
  }

  let busy = false;

  function refreshDirty(): void {
    applyButton.disabled = busy || !isDirty();
    editor.classList.toggle("pw-edit-dirty", isDirty());
  }

  function draftChanged(): void {
    refreshDirty();
    options.onDraftChange?.(readDraft());
  }

  function apply(): void {
    if (!busy && isDirty()) {
      options.onApply?.(readDraft());
    }
  }

  function revert(): void {
    if (original && !busy) {
      writeDraft(original);
      draftChanged();
    }
  }

  // Touching a "(mixed)" control makes that field one value for the whole unit.
  for (const [field, control] of mixedControls) {
    control.addEventListener(control === editSize || control === editColor ? "input" : "change", () => {
      if (mixed.delete(field)) {
        if (field === "size" && !editSize.value && original) {
          editSize.value = String(Math.round(original.size * 10) / 10);
        }
        showMixed();
      }
    });
  }
  for (const control of [editText, editSize, editColor]) {
    control.addEventListener("input", draftChanged);
  }
  for (const control of [editFont, editBold, editItalic]) {
    control.addEventListener("change", draftChanged);
  }
  editor.addEventListener("keydown", (event) => {
    const onControl = event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement;
    if (event.key === "Enter" && onControl) {
      event.preventDefault(); // Enter applies, never a line break
      apply();
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (isDirty()) {
        revert();
      } else {
        // Nothing to revert: hand the keyboard back to the page, where the arrow keys
        // nudge the selected text and Delete removes it (arrange.ts).
        (event.target as HTMLElement).blur();
      }
    }
  });

  async function loadFamilies(): Promise<void> {
    if (familiesLoaded || !options.loadFamilies) {
      return;
    }
    familiesLoaded = true;
    try {
      const chosen = editFont.value;
      for (const family of await options.loadFamilies()) {
        const option = document.createElement("option");
        option.value = family;
        option.textContent = family;
        editFont.appendChild(option);
      }
      editFont.value = chosen;
    } catch {
      familiesLoaded = false; // try again the next time text is selected
    }
  }

  /** Forget the loaded font list: a font was added to the library. */
  function reloadFamilies(): void {
    editFont.replaceChildren(keepFontOption);
    familiesLoaded = false;
    if (!editor.hidden) {
      void loadFamilies();
    }
  }

  const fields = document.createElement("div");
  fields.hidden = true;
  container.appendChild(fields);

  const fontRow = row("Font");
  const sizeRow = row("Size");
  const colorRow = row("Color");
  const colorSwatch = document.createElement("span");
  colorSwatch.className = "pw-color-swatch";
  colorRow.value.prepend(colorSwatch);
  const spacingRow = row("Spacing");
  const rotationRow = row("Rotation");
  const matchRow = row("Match");
  const matchDot = document.createElement("span");
  matchDot.className = "pw-match-dot";
  matchRow.value.prepend(matchDot);
  fields.append(fontRow.row, sizeRow.row, colorRow.row, spacingRow.row, rotationRow.row, matchRow.row);

  // panelButton keeps focus (and so the edit in progress) on the span box:
  // otherwise pressing it blurs the box first, which cancels the edit and
  // hides this block -- button included -- before the click lands.
  const copyStyleButton = panelButton("Copy style", "pw-copy-style", () => options.onCopyStyle?.());
  copyStyleButton.title = "Format painter: copy this text's font, size and color onto other text";
  // Its own block after `fields`, not inside it: the rows stay the only
  // children there, so the Match row remains the last row.
  const actions = document.createElement("div");
  actions.className = "pw-inspector-actions";
  actions.hidden = true;
  actions.append(copyStyleButton);

  // EDT-10: links over the selected span.
  const linksHeading = document.createElement("h3");
  linksHeading.className = "pw-inspector-subheading";
  linksHeading.textContent = "Links";
  const linkList = document.createElement("ul");
  linkList.className = "pw-link-list";
  const addLinkButton = panelButton("Add link…", "pw-add-link", () => options.onAddLink?.());
  actions.append(linksHeading, linkList, addLinkButton);
  container.appendChild(actions);

  // EDT-08: shown instead of the span fields while an image is selected.
  const imageSection = document.createElement("div");
  imageSection.className = "pw-image-section";
  imageSection.hidden = true;
  // Their own row class: `.pw-inspector-row` is the span's fields, in a fixed order.
  const pixelsRow = row("Pixels", "pw-inspector-row-image");
  const placementRow = row("Placed at", "pw-inspector-row-image");
  const imageButtons = document.createElement("div");
  imageButtons.className = "pw-image-buttons";
  imageButtons.append(
    panelButton("Replace…", "pw-image-replace", () => options.onImageAction?.("replace")),
    panelButton("Crop", "pw-image-crop", () => options.onImageAction?.("crop")),
    panelButton("Delete", "pw-image-delete", () => options.onImageAction?.("delete")),
  );
  const imageHint = document.createElement("p");
  imageHint.className = "pw-hint";
  imageHint.textContent = "Drag the image to move it, or its corner to resize.";
  imageSection.append(pixelsRow.row, placementRow.row, imageButtons, imageHint);
  container.appendChild(imageSection);

  // EDT-09: shown while a vector shape is selected.
  const shapeSection = document.createElement("div");
  shapeSection.className = "pw-shape-section";
  shapeSection.hidden = true;
  const shapeKindRow = row("Shape", "pw-inspector-row-image");
  function control(label: string, input: HTMLInputElement): HTMLLabelElement {
    const wrapper = document.createElement("label");
    wrapper.className = "pw-shape-control";
    wrapper.append(label, input);
    return wrapper;
  }
  const strokeInput = document.createElement("input");
  strokeInput.type = "color";
  strokeInput.className = "pw-shape-stroke";
  const fillInput = document.createElement("input");
  fillInput.type = "color";
  fillInput.className = "pw-shape-fill";
  const noFillInput = document.createElement("input");
  noFillInput.type = "checkbox";
  noFillInput.className = "pw-shape-no-fill";
  const widthInput = document.createElement("input");
  widthInput.type = "number";
  widthInput.min = "0.1";
  widthInput.step = "0.5";
  widthInput.className = "pw-shape-width";
  const shapeButtons = document.createElement("div");
  shapeButtons.className = "pw-image-buttons";
  shapeButtons.append(
    panelButton("Apply", "pw-shape-apply", () => {
      const style: ShapeStyle = { stroke_color: fromHexColor(strokeInput.value) };
      if (noFillInput.checked) {
        style.no_fill = true;
      } else {
        style.fill_color = fromHexColor(fillInput.value);
      }
      const width = Number(widthInput.value);
      if (width > 0) {
        style.line_width = width;
      }
      options.onShapeStyle?.(style);
    }),
    panelButton("Delete", "pw-shape-delete", () => options.onShapeDelete?.()),
  );
  const shapeHint = document.createElement("p");
  shapeHint.className = "pw-hint";
  shapeHint.textContent = "Drag the shape to move it, or its corner to resize.";
  shapeSection.append(
    shapeKindRow.row,
    control("Outline", strokeInput),
    control("Fill", fillInput),
    control("No fill", noFillInput),
    control("Width (pt)", widthInput),
    shapeButtons,
    shapeHint,
  );
  container.appendChild(shapeSection);

  function showShape(shape: ShapeInfo): void {
    hideEditor();
    empty.hidden = true;
    fields.hidden = true;
    actions.hidden = true;
    imageSection.hidden = true;
    shapeSection.hidden = false;
    const [x0, y0, x1, y1] = shape.rect;
    shapeKindRow.text.textContent = `${shape.kind}, ${Math.round(x1 - x0)} × ${Math.round(y1 - y0)} pt`;
    strokeInput.value = toHexColor(shape.stroke_color ?? [0, 0, 0]).toLowerCase();
    fillInput.value = toHexColor(shape.fill_color ?? [1, 1, 1]).toLowerCase();
    noFillInput.checked = shape.fill_color === null;
    widthInput.value = String(shape.line_width ?? 1);
  }

  function showImage(image: ImageInfo): void {
    hideEditor();
    empty.hidden = true;
    fields.hidden = true;
    actions.hidden = true;
    shapeSection.hidden = true;
    imageSection.hidden = false;
    pixelsRow.text.textContent = `${image.pixel_width} × ${image.pixel_height}`;
    const [x0, y0, x1, y1] = image.rect;
    placementRow.text.textContent = `${Math.round(x1 - x0)} × ${Math.round(y1 - y0)} pt at ${Math.round(x0)}, ${Math.round(y0)}`;
  }

  function showLinks(links: LinkInfo[]): void {
    linkList.replaceChildren(
      ...links.map((link) => {
        const item = document.createElement("li");
        item.className = "pw-link-item";
        const label = document.createElement("span");
        label.className = "pw-link-target";
        label.textContent = describeLink(link);
        label.title = label.textContent;
        item.append(
          label,
          panelButton("Edit", "pw-link-edit", () => options.onEditLink?.(link)),
          panelButton("Remove", "pw-link-remove", () => options.onRemoveLink?.(link)),
        );
        return item;
      }),
    );
  }

  // Outside `fields` and `empty`: the painter stays armed while no span is
  // selected (that's the point -- the next click picks the target).
  const painterStatus = document.createElement("p");
  painterStatus.className = "pw-painter-status";
  painterStatus.setAttribute("role", "status");
  painterStatus.hidden = true;
  container.appendChild(painterStatus);

  function hideEditor(): void {
    editor.hidden = true;
    original = null;
    setStatus(null);
  }

  function showEmpty(note?: string): void {
    hideEditor();
    empty.textContent = note ?? EMPTY_HINT;
    empty.hidden = false;
    fields.hidden = true;
    actions.hidden = true;
    imageSection.hidden = true;
    shapeSection.hidden = true;
  }

  function showUnit(unit: TextUnit, spans: SpanTrace[], links: LinkInfo[] = [], keepDraft = false): void {
    const span = spans[0];
    if (!span) {
      showEmpty();
      return;
    }
    showLinks(links);
    unitKind.textContent = UNIT_LABELS[unit.granularity];
    iconBadge.hidden = !unit.icon;
    iconLocked = unit.icon;
    editText.readOnly = unit.icon;
    editHint.textContent = unit.icon ? ICON_HINT : EDIT_HINT;
    const mixedFields = mixedStyleFields(spans);
    keepFontOption.textContent = mixedFields.includes("font")
      ? "Original fonts (mixed)"
      : `Original font (${span.style.font.replace(/^[A-Z]{6}\+/, "")})`;
    const kept = keepDraft && original ? readDraft() : null;
    original = {
      text: unit.text,
      font: "",
      size: span.style.size,
      color: span.style.color,
      ...styleFromFontName(span.style.font),
      mixed: mixedFields,
    };
    writeDraft(kept ?? original);
    editor.hidden = false;
    setStatus(null);
    void loadFamilies();
    empty.hidden = true;
    fields.hidden = false;
    actions.hidden = false;
    imageSection.hidden = true;
    shapeSection.hidden = true;

    const shown = (field: StyleField, value: string): string => (mixedFields.includes(field) ? "(mixed)" : value);
    fontRow.text.textContent = shown("font", formatFontName(span.style.font));
    sizeRow.text.textContent = shown("size", `${span.style.size.toFixed(1)} pt`);
    colorSwatch.style.backgroundColor = toHexColor(span.style.color);
    colorRow.text.textContent = ` ${shown("color", toHexColor(span.style.color))}`;

    const state = span.text_state;
    spacingRow.text.textContent = state
      ? `Tc ${state.char_spacing.toFixed(2)}  Tz ${state.horizontal_scale.toFixed(0)}%`
      : "not available for this span";

    rotationRow.text.textContent = `${unit.rotation_degrees.toFixed(0)}°`;

    matchDot.className = "pw-match-dot";
    matchRow.text.textContent = " …";
    matchRow.row.title = "";
  }

  function setPreview(preview: PreviewResult | null): void {
    if (!preview) {
      matchDot.className = "pw-match-dot";
      matchRow.text.textContent = " …";
      matchRow.row.title = "";
      return;
    }
    matchDot.className = `pw-match-dot pw-match-${preview.tier}`;
    const label = TIER_LABELS[preview.tier] ?? preview.tier;
    matchRow.text.textContent = ` ${label} (${Math.round(preview.confidence * 100)}%)`;
    matchRow.row.title = preview.note;
  }

  function focusEditor(): void {
    editText.focus();
    editText.select();
  }

  function setStatus(message: string | null, isBusy = false): void {
    busy = isBusy;
    editStatus.hidden = message === null;
    editStatus.textContent = message ?? "";
    for (const control of [editFont, editSize, editColor, editBold, editItalic, applyButton]) {
      control.disabled = isBusy || iconLocked;
    }
    for (const control of [editText, revertButton]) {
      control.disabled = isBusy;
    }
    refreshDirty();
  }

  function setPainterEnabled(enabled: boolean): void {
    copyStyleButton.disabled = !enabled;
    copyStyleButton.title = enabled
      ? "Format painter: copy this text's font, size and color onto other text"
      : "The format painter copies one style run: switch to Line or Block mode to use it";
  }

  function setPainter(sourceText: string | null): void {
    painterStatus.hidden = sourceText === null;
    painterStatus.textContent =
      sourceText === null ? "" : `Format painter: click text to give it the style of “${sourceText}” (Esc cancels).`;
  }

  showEmpty();
  return {
    showEmpty,
    showUnit,
    setPainterEnabled,
    focusEditor,
    isDirty,
    draft: readDraft,
    setStatus,
    reloadFamilies,
    setPreview,
    setPainter,
    showImage,
    showShape,
  };
}
