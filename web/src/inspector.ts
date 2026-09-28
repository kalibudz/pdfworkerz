/**
 * UI-03: the inspector panel (SPEC.md section 8.1) -- detected font, size,
 * color, spacing and the live match-tier confidence for whichever span
 * UI-02's overlay currently has selected. Pure presentation: overlay.ts owns
 * *when* a span is selected and *when* a fresh preview arrives; this module
 * only ever renders whatever it's told to.
 */

import type { ImageInfo, LinkInfo, PreviewResult, SpanTrace } from "./api";

export interface InspectorHandle {
  /** Nothing selected -- the panel's resting state. */
  showEmpty(): void;
  /** A span was just selected, before any preview has come back for it yet.
   * `links` are the page's links overlapping it (EDT-10). */
  showSpan(span: SpanTrace, links?: LinkInfo[]): void;
  /** The latest preview result for the currently-shown span, or null while
   * one is in flight (SPEC.md never guesses at a match tier it hasn't
   * actually computed). */
  setPreview(preview: PreviewResult | null): void;
  /** EDT-07: show (or clear, with null) the format painter's armed state --
   * `sourceText` is the span whose style is waiting to be applied. */
  setPainter(sourceText: string | null): void;
  /** EDT-08: an image placement was selected instead of a span. */
  showImage(image: ImageInfo): void;
}

export type ImageAction = "replace" | "crop" | "delete";

export interface InspectorOptions {
  /** EDT-07: the "Copy style" button was pressed for the shown span. */
  onCopyStyle?: () => void;
  /** EDT-10: add a link over the shown span. */
  onAddLink?: () => void;
  /** EDT-10: retarget, or remove, one of the shown span's links. */
  onEditLink?: (link: LinkInfo) => void;
  onRemoveLink?: (link: LinkInfo) => void;
  /** EDT-08: one of the selected image's buttons was pressed. */
  onImageAction?: (action: ImageAction) => void;
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
  empty.className = "pw-hint";
  empty.textContent = "Click a span of text on the page to inspect it.";
  container.appendChild(empty);

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
  actions.appendChild(copyStyleButton);

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

  function showImage(image: ImageInfo): void {
    empty.hidden = true;
    fields.hidden = true;
    actions.hidden = true;
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

  function showEmpty(): void {
    empty.hidden = false;
    fields.hidden = true;
    actions.hidden = true;
    imageSection.hidden = true;
  }

  function showSpan(span: SpanTrace, links: LinkInfo[] = []): void {
    showLinks(links);
    empty.hidden = true;
    fields.hidden = false;
    actions.hidden = false;
    imageSection.hidden = true;

    fontRow.text.textContent = formatFontName(span.style.font);
    sizeRow.text.textContent = `${span.style.size.toFixed(1)} pt`;
    colorSwatch.style.backgroundColor = toHexColor(span.style.color);
    colorRow.text.textContent = ` ${toHexColor(span.style.color)}`;

    const state = span.text_state;
    spacingRow.text.textContent = state
      ? `Tc ${state.char_spacing.toFixed(2)}  Tz ${state.horizontal_scale.toFixed(0)}%`
      : "not available for this span";

    rotationRow.text.textContent = `${span.style.rotation_degrees.toFixed(0)}°`;

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

  function setPainter(sourceText: string | null): void {
    painterStatus.hidden = sourceText === null;
    painterStatus.textContent =
      sourceText === null ? "" : `Format painter: click text to give it the style of “${sourceText}” (Esc cancels).`;
  }

  showEmpty();
  return { showEmpty, showSpan, setPreview, setPainter, showImage };
}
