// pdfjs-dist's own worker, wrapped only so ./polyfills installs in *this*
// realm too -- a Web Worker is a separate global scope from the page, so
// the main-thread polyfill import in main.ts never reaches it.
import "./polyfills";
import "pdfjs-dist/build/pdf.worker.mjs";
