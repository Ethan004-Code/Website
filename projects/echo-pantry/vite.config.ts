import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const root = fileURLToPath(new URL(".", import.meta.url));
const rawPort = process.env.PORT || "5173";
const port = Number(rawPort);
const basePath = process.env.BASE_PATH || "/";
const base = basePath.endsWith("/") ? basePath : `${basePath}/`;

if (!Number.isInteger(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

export default defineConfig({
  root,
  base,
  publicDir: path.resolve(root, "public"),
  build: {
    outDir: path.resolve(root, "dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(root, "index.html"),
    },
  },
  server: {
    host: "0.0.0.0",
    port,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET || "http://127.0.0.1:5050",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port,
    strictPort: true,
  },
});
