/**
 * UI-03: the inspector panel (SPEC.md section 8.1) -- detected font, size,
 * color, spacing and the live match-tier confidence for whichever span
 * UI-02's overlay currently has selected. Pure presentation: overlay.ts owns
 * *when* a span is selected and *when* a fresh preview arrives; this module
 * only ever renders whatever it's told to.
 */

import type { PreviewResult, SpanTrace } from "./api";

export interface InspectorHandle {
  /** Nothing selected -- the panel's resting state. */
  showEmpty(): void;
  /** A span was just selected, before any preview has come back for it yet. */
  showSpan(span: SpanTrace): void;
  /** The latest preview result for the currently-shown span, or null while
   * one is in flight (SPEC.md never guesses at a match tier it hasn't
   * actually computed). */
  setPreview(preview: PreviewResult | null): void;
  /** EDT-07: show (or clear, with null) the format painter's armed state --
   * `sourceText` is the span whose style is waiting to be applied. */
  setPainter(sourceText: string | null): void;
}

export interface InspectorOptions {
  /** EDT-07: the "Copy style" button was pressed for the shown span. */
  onCopyStyle?: () => void;
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

function row(label: string): Row {
  const rowEl = document.createElement("div");
  rowEl.className = "pw-inspector-row";
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

  const copyStyleButton = document.createElement("button");
  copyStyleButton.type = "button";
  copyStyleButton.className = "pw-copy-style";
  copyStyleButton.textContent = "Copy style";
  copyStyleButton.title = "Format painter: copy this text's font, size and color onto other text";
  // Keeps focus (and so the edit in progress) on the span box: without
  // this, pressing the button blurs the box first, which cancels the edit
  // and hides these fields -- button included -- before the click lands.
  copyStyleButton.addEventListener("mousedown", (event) => event.preventDefault());
  copyStyleButton.addEventListener("click", () => options.onCopyStyle?.());
  // Its own block after `fields`, not inside it: the rows stay the only
  // children there, so the Match row remains the last row.
  const actions = document.createElement("div");
  actions.className = "pw-inspector-actions";
  actions.hidden = true;
  actions.appendChild(copyStyleButton);
  container.appendChild(actions);

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
  }

  function showSpan(span: SpanTrace): void {
    empty.hidden = true;
    fields.hidden = false;
    actions.hidden = false;

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
  return { showEmpty, showSpan, setPreview, setPainter };
}
