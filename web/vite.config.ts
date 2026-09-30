import { defineConfig } from "vite";

// The frontend is a static SPA: it never assumes it's served by the same
// origin as the API (SPEC.md's server binds 127.0.0.1 only and picks its
// own port at `pdfworkerz serve` time). Session config (API base URL +
// token) is read from the page URL or entered by hand -- see src/config.ts
// -- rather than through a dev-server proxy, so the built `dist/` behaves
// identically to `vite dev` and to how Playwright drives it in tests/web/.
export default defineConfig({
  build: {
    outDir: "dist",
    sourcemap: true,
    rolldownOptions: {
      output: {
        // pdf.js is most of the bundle and changes only on upgrade: its own chunk
        // keeps the app's code small (and cacheable separately).
        codeSplitting: {
          groups: [{ name: "pdfjs", test: /node_modules[\\/]pdfjs-dist/ }],
        },
      },
    },
  },
});
