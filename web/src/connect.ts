/**
 * Fallback "connect" screen, shown when no `?token=` was found in the URL
 * or remembered from earlier in this tab's session (config.ts). Validates
 * against the one route that needs no token, GET /health, before handing
 * back a config -- so a typo in the server URL is caught here rather than
 * surfacing as a confusing failure on the first real request.
 */

import { Api, ApiError } from "./api";
import { defaultApiBase, type SessionConfig } from "./config";

export function renderConnectScreen(container: HTMLElement, onConnected: (config: SessionConfig) => void): void {
  container.innerHTML = "";

  const screen = document.createElement("div");
  screen.className = "pw-screen";

  const card = document.createElement("div");
  card.className = "pw-card";
  screen.appendChild(card);

  const heading = document.createElement("h1");
  heading.textContent = "Connect to PDFWorkerz";
  card.appendChild(heading);

  const subtitle = document.createElement("p");
  subtitle.className = "pw-subtitle";
  subtitle.textContent = "Run “pdfworkerz serve” and enter the address and session token it prints.";
  card.appendChild(subtitle);

  const form = document.createElement("form");
  card.appendChild(form);

  const apiField = document.createElement("div");
  apiField.className = "pw-field";
  const apiLabel = document.createElement("label");
  apiLabel.textContent = "Server address";
  apiLabel.htmlFor = "pw-connect-api";
  const apiInput = document.createElement("input");
  apiInput.type = "text";
  apiInput.id = "pw-connect-api";
  apiInput.autocomplete = "off";
  apiInput.value = defaultApiBase();
  apiField.append(apiLabel, apiInput);
  form.appendChild(apiField);

  const tokenField = document.createElement("div");
  tokenField.className = "pw-field";
  const tokenLabel = document.createElement("label");
  tokenLabel.textContent = "Session token";
  tokenLabel.htmlFor = "pw-connect-token";
  const tokenInput = document.createElement("input");
  tokenInput.type = "password";
  tokenInput.id = "pw-connect-token";
  tokenInput.autocomplete = "off";
  tokenField.append(tokenLabel, tokenInput);
  form.appendChild(tokenField);

  const errorLine = document.createElement("p");
  errorLine.className = "pw-error";
  errorLine.setAttribute("role", "alert");
  form.appendChild(errorLine);

  const actions = document.createElement("div");
  actions.className = "pw-actions";
  const connectButton = document.createElement("button");
  connectButton.type = "submit";
  connectButton.className = "pw-button";
  connectButton.textContent = "Connect";
  actions.appendChild(connectButton);
  form.appendChild(actions);

  const hint = document.createElement("p");
  hint.className = "pw-hint";
  hint.textContent =
    "The token authenticates this browser tab to your own local PDFWorkerz server " +
    "(SPEC.md §4.2: local only). It is remembered only for this tab, and never sent anywhere else.";
  card.appendChild(hint);

  function setBusy(busy: boolean): void {
    connectButton.disabled = busy;
    connectButton.textContent = busy ? "Connecting…" : "Connect";
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void attemptConnect();
  });

  async function attemptConnect(): Promise<void> {
    const apiBase = apiInput.value.trim().replace(/\/+$/, "");
    const token = tokenInput.value.trim();
    if (!apiBase || !token) {
      errorLine.textContent = "Enter both the server address and its session token.";
      return;
    }
    errorLine.textContent = "";
    setBusy(true);
    try {
      await new Api({ apiBase, token }).health();
      onConnected({ apiBase, token });
    } catch (error) {
      errorLine.textContent =
        error instanceof ApiError
          ? `Server responded with an error (${error.status}).`
          : "Could not reach that address. Check the server is running and the address is correct.";
    } finally {
      setBusy(false);
    }
  }

  container.appendChild(screen);
  apiInput.focus();
}
