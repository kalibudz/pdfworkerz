/**
 * UI-06: the "open a document" screen, including the password prompt for
 * encrypted files.
 *
 * server/app.py's POST /documents maps both PasswordRequiredError (no
 * password given for an encrypted file) and WrongPasswordError (a wrong one
 * given) to the same 401 status (SPEC.md doesn't need the browser to tell
 * them apart by status code alone) -- so this screen tells them apart the
 * way a person actually experiences the difference: the first 401, before
 * any password has been sent, means "this needs a password"; a 401 *after*
 * a password was sent means that password was wrong. Never guesses beyond
 * that (no password recovery, no attempt limiting -- SPEC.md section 6: "No
 * password guessing or cracking is ever performed").
 */

import { Api, ApiError, type OpenDocumentResponse } from "./api";

export interface OpenScreenHandlers {
  onOpened: (response: OpenDocumentResponse, path: string) => void;
  /** "Use a different server" -- back to the connect screen. */
  onDisconnect: () => void;
}

export function renderOpenScreen(container: HTMLElement, api: Api, handlers: OpenScreenHandlers): void {
  container.innerHTML = "";

  const screen = document.createElement("div");
  screen.className = "pw-screen";

  const card = document.createElement("div");
  card.className = "pw-card";
  screen.appendChild(card);

  const heading = document.createElement("h1");
  heading.textContent = "Open a PDF";
  card.appendChild(heading);

  const subtitle = document.createElement("p");
  subtitle.className = "pw-subtitle";
  subtitle.textContent = "Enter the path to a PDF file on the machine running the PDFWorkerz server.";
  card.appendChild(subtitle);

  const form = document.createElement("form");
  card.appendChild(form);

  const pathField = document.createElement("div");
  pathField.className = "pw-field";
  const pathLabel = document.createElement("label");
  pathLabel.textContent = "File path";
  pathLabel.htmlFor = "pw-open-path";
  const pathInput = document.createElement("input");
  pathInput.type = "text";
  pathInput.id = "pw-open-path";
  pathInput.autocomplete = "off";
  pathInput.placeholder = "/path/to/document.pdf";
  pathField.append(pathLabel, pathInput);
  form.appendChild(pathField);

  const passwordField = document.createElement("div");
  passwordField.className = "pw-field";
  passwordField.hidden = true;
  const passwordLabel = document.createElement("label");
  passwordLabel.textContent = "Password";
  passwordLabel.htmlFor = "pw-open-password";
  const passwordInput = document.createElement("input");
  passwordInput.type = "password";
  passwordInput.id = "pw-open-password";
  passwordInput.autocomplete = "off";
  passwordField.append(passwordLabel, passwordInput);
  form.appendChild(passwordField);

  const errorLine = document.createElement("p");
  errorLine.className = "pw-error";
  errorLine.setAttribute("role", "alert");
  form.appendChild(errorLine);

  const actions = document.createElement("div");
  actions.className = "pw-actions";
  const openButton = document.createElement("button");
  openButton.type = "submit";
  openButton.className = "pw-button";
  openButton.textContent = "Open";
  const disconnectButton = document.createElement("button");
  disconnectButton.type = "button";
  disconnectButton.className = "pw-button pw-secondary";
  disconnectButton.textContent = "Server…";
  disconnectButton.title = "Connect to a different PDFWorkerz server";
  actions.append(openButton, disconnectButton);
  form.appendChild(actions);

  disconnectButton.addEventListener("click", handlers.onDisconnect);

  pathInput.addEventListener("input", () => {
    passwordField.hidden = true;
    passwordInput.value = "";
  });

  function setBusy(busy: boolean): void {
    openButton.disabled = busy;
    openButton.textContent = busy ? "Opening…" : "Open";
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void attemptOpen();
  });

  async function attemptOpen(): Promise<void> {
    const path = pathInput.value.trim();
    if (!path) {
      errorLine.textContent = "Enter a file path.";
      return;
    }
    errorLine.textContent = "";
    setBusy(true);
    const password = passwordField.hidden ? undefined : passwordInput.value;
    const sendingPassword = password !== undefined && password !== "";
    try {
      const response = await api.openDocument(path, password);
      handlers.onOpened(response, path);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401 && error.detail.includes("X-Session-Token")) {
        // The server rejected the session itself (usually: it was restarted, so the
        // token this tab still holds is stale), not the document's password.
        errorLine.textContent =
          "This session is no longer valid (the server may have restarted). Open the new link it printed.";
      } else if (error instanceof ApiError && error.status === 401) {
        passwordField.hidden = false;
        // A 401 means either "this needs a password" or "that password was
        // wrong" -- and whether *this* request sent one is exactly what
        // tells those two cases apart; no need to remember anything about
        // earlier attempts to get this right.
        errorLine.textContent = sendingPassword
          ? "Incorrect password. Try again."
          : "This document is password protected.";
        passwordInput.value = "";
        passwordInput.focus();
      } else if (error instanceof ApiError) {
        errorLine.textContent = error.detail;
      } else {
        errorLine.textContent = "Could not reach the PDFWorkerz server.";
      }
    } finally {
      setBusy(false);
    }
  }

  container.appendChild(screen);
  pathInput.focus();
}
