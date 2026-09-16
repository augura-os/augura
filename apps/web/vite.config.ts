import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Contract §3/§9: dev proxy forwards ^(upload|assets|graph|analysis|settings|creatives)
// to the local FastAPI backend. Frontend code must use relative paths only.
export default defineConfig({
  plugins: [react()],
  // 静态产物输出到 /static/ 而非默认 /assets/——后者与 API 的 /assets 路由撞名
  // （nginx 会把 /assets 目录当静态目录处理，301 把 API 调用带死）
  build: { assetsDir: "static" },
  resolve: {
    alias: {
      "@shared": fileURLToPath(new URL("../../packages/shared/src", import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 3000,
    proxy: {
      "^/(upload|assets|graph|analysis|settings|creatives|review|derivations|dnas)": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
