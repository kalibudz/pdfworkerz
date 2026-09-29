/**
 * EDT-03 / EDT-06: one modal form for choosing a text style -- font family, weight, slant,
 * size and color -- used both to change the style of selected text and to add new text
 * (with a text field). A native <dialog>, so it opens in the browser's top layer.
 */

import { toHexColor } from "./inspector";

export interface TextStyle {
  font: string;
  size: number;
  color: [number, number, number];
  bold: boolean;
  italic: boolean;
}

export interface StyleDialogOptions {
  title: string;
  families: string[];
  initial: TextStyle;
  /** Show a text field (for adding text); its value comes back as `text`. */
  withText?: boolean;
  submitLabel: string;
  /** Offer "keep the current font" as the first choice (value ""), labelled with this text. */
  keepFontLabel?: string;
}

export type StyleChoice = TextStyle & { text: string };

function fromHex(hex: string): [number, number, number] {
  const value = parseInt(hex.slice(1), 16);
  return [((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
}

function field(label: string, control: HTMLElement): HTMLLabelElement {
  const wrapper = document.createElement("label");
  wrapper.className = "pw-style-field";
  const caption = document.createElement("span");
  caption.textContent = label;
  wrapper.append(caption, control);
  return wrapper;
}

/** Resolves with the chosen style, or null if the user cancelled. */
export function openStyleDialog(options: StyleDialogOptions): Promise<StyleChoice | null> {
  const dialog = document.createElement("dialog");
  dialog.className = "pw-style-dialog";
  const form = document.createElement("form");
  form.method = "dialog";

  const heading = document.createElement("h2");
  heading.textContent = options.title;

  const text = document.createElement("input");
  text.type = "text";
  text.id = "pw-style-text";
  text.required = true;

  const family = document.createElement("select");
  family.id = "pw-style-font";
  const choices: [string, string][] = options.families.map((name) => [name, name]);
  if (options.keepFontLabel !== undefined) {
    choices.unshift(["", options.keepFontLabel]);
  }
  for (const [value, label] of choices) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    family.appendChild(option);
  }
  family.value = options.initial.font;

  const size = document.createElement("input");
  size.type = "number";
  size.id = "pw-style-size";
  size.min = "1";
  size.max = "400";
  size.step = "0.5";
  size.value = String(Math.round(options.initial.size * 10) / 10);

  const color = document.createElement("input");
  color.type = "color";
  color.id = "pw-style-color";
  color.value = toHexColor(options.initial.color).toLowerCase();

  const bold = document.createElement("input");
  bold.type = "checkbox";
  bold.id = "pw-style-bold";
  bold.checked = options.initial.bold;
  const italic = document.createElement("input");
  italic.type = "checkbox";
  italic.id = "pw-style-italic";
  italic.checked = options.initial.italic;

  const buttons = document.createElement("div");
  buttons.className = "pw-style-buttons";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "pw-style-submit";
  submit.textContent = options.submitLabel;
  buttons.append(cancel, submit);

  form.append(heading);
  if (options.withText) {
    form.append(field("Text", text));
  }
  form.append(
    field("Font", family),
    field("Size (pt)", size),
    field("Color", color),
    field("Bold", bold),
    field("Italic", italic),
    buttons,
  );
  dialog.appendChild(form);
  document.body.appendChild(dialog);

  return new Promise((resolve) => {
    let choice: StyleChoice | null = null;
    form.addEventListener("submit", () => {
      choice = {
        text: text.value,
        font: family.value,
        size: Number(size.value),
        color: fromHex(color.value),
        bold: bold.checked,
        italic: italic.checked,
      };
    });
    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(choice);
    });
    dialog.showModal();
    (options.withText ? text : family).focus();
  });
}

/** Guess whether a PDF font name is bold or italic, to pre-fill the dialog. */
export function styleFromFontName(font: string): { bold: boolean; italic: boolean } {
  const name = font.toLowerCase();
  return { bold: /bold|black|heavy|semibold/.test(name), italic: /italic|oblique/.test(name) };
}
