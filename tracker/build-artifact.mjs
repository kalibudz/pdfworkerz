// Bundle the tracker into one self-contained HTML page (React, data and CSS inlined)
// for publishing as a claude.ai Artifact: `npm run artifact` -> dist-artifact/tracker.html
import { build } from "esbuild";
import { mkdir, writeFile } from "node:fs/promises";

const result = await build({
  entryPoints: ["src/main.jsx"],
  bundle: true,
  minify: true,
  format: "iife",
  jsx: "automatic",
  loader: { ".js": "jsx" },
  define: { "process.env.NODE_ENV": '"production"' },
  outdir: "out",
  write: false,
});

const js = result.outputFiles.find((f) => f.path.endsWith(".js")).text;
const css = result.outputFiles.find((f) => f.path.endsWith(".css")).text;

// The artifact host adds doctype/html/head/body itself, so the page starts at <title>.
const html = `<title>PDFWorkerz Build Board</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>${css}</style>
<div id="root"></div>
<script>${js.replace(/<\/script/gi, "<\\/script")}</script>
`;

await mkdir("dist-artifact", { recursive: true });
await writeFile("dist-artifact/tracker.html", html);
console.log(`dist-artifact/tracker.html written (${(html.length / 1024).toFixed(0)} KB)`);
