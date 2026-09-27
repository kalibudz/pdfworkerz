/** Top-level screen wiring: connect -> open -> viewer. */

import { Api, type OpenDocumentResponse } from "./api";
import { clearConfig, loadConfig, saveConfig, type SessionConfig } from "./config";
import { renderConnectScreen } from "./connect";
import { renderOpenScreen } from "./open";
import { renderViewer } from "./viewer";

function fileNameFromPath(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function mount(root: HTMLElement): void {
  showConnectOrOpen();

  function showConnectOrOpen(): void {
    const config = loadConfig();
    if (config) {
      showOpen(config);
    } else {
      renderConnectScreen(root, (connected) => {
        saveConfig(connected);
        showOpen(connected);
      });
    }
  }

  function showOpen(config: SessionConfig): void {
    const api = new Api(config);
    renderOpenScreen(root, api, {
      onOpened: (response: OpenDocumentResponse, path: string) => showViewer(api, response, path),
      onDisconnect: () => {
        clearConfig();
        showConnectOrOpen();
      },
    });
  }

  function showViewer(api: Api, response: OpenDocumentResponse, path: string): void {
    void renderViewer(root, {
      api,
      documentId: response.document_id,
      pageCount: response.page_count,
      title: fileNameFromPath(path),
    });
  }
}
