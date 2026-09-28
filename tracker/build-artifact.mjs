// Bundle the tracker into one HTML page for publishing as a claude.ai Artifact:
// `npm run artifact` -> dist-artifact/tracker.html. React loads from cdnjs (pinned UMD
// builds, allowed by the Artifact CSP); the page's own code, data and CSS are inlined.
import { build } from "esbuild";
import { mkdir, writeFile } from "node:fs/promises";

const REACT_VERSION = "18.3.1";

// Resolve react / react-dom imports to the UMD globals instead of bundling them.
const reactFromCdn = {
  name: "react-from-cdn",
  setup(b) {
    b.onResolve({ filter: /^react(-dom)?(\/client)?$/ }, (args) => ({ path: args.path, namespace: "cdn" }));
    b.onLoad({ filter: /.*/, namespace: "cdn" }, (args) => ({
      contents: args.path.startsWith("react-dom") ? "module.exports = window.ReactDOM;" : "module.exports = window.React;",
      loader: "js",
    }));
  },
};

const result = await build({
  entryPoints: ["src/main.jsx"],
  bundle: true,
  minify: false,
  format: "iife",
  jsx: "transform",
  jsxFactory: "React.createElement",
  jsxFragment: "React.Fragment",
  loader: { ".js": "jsx" },
  plugins: [reactFromCdn],
  outdir: "out",
  write: false,
});

const js = result.outputFiles.find((f) => f.path.endsWith(".js")).text;
const css = result.outputFiles.find((f) => f.path.endsWith(".css")).text;
const cdn = `https://cdnjs.cloudflare.com/ajax/libs`;

// The artifact host adds doctype/html/head/body itself, so the page starts at <title>.
const html = `<title>PDFWorkerz Build Board</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>${css}</style>
<div id="root"></div>
<script src="${cdn}/react/${REACT_VERSION}/umd/react.production.min.js"></script>
<script src="${cdn}/react-dom/${REACT_VERSION}/umd/react-dom.production.min.js"></script>
<script>${js.replace(/<\/script/gi, "<\\/script")}</script>
`;

await mkdir("dist-artifact", { recursive: true });
await writeFile("dist-artifact/tracker.html", html);
console.log(`dist-artifact/tracker.html written (${(html.length / 1024).toFixed(0)} KB)`);
