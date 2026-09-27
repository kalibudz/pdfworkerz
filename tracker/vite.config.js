import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the tracker with hot reload: edits to features.json or
// ../state/checkpoint.json show up immediately, which is what makes it "live".
export default defineConfig({
  plugins: [react()],
  server: { fs: { allow: [".."] } },
});
