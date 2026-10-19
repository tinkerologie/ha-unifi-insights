import { defineConfig } from "vitest/config";

// One self-contained ES module, written straight into the integration so
// the Marketplace and HACS ship it. CI rebuilds and fails if the committed
// file differs.
export default defineConfig({
  build: {
    modulePreload: false,
    outDir: "../custom_components/unifi_insights/frontend",
    emptyOutDir: true,
    target: "es2022",
    minify: true,
    sourcemap: false,
    reportCompressedSize: false,
    rollupOptions: {
      input: {
        "topology-card": "src/index.ts",
        "site-health-card": "src/site-health-card.ts",
        "internet-activity-card": "src/internet-activity-card.ts",
        "performance-card": "src/performance-card.ts",
        "protect-status-card": "src/protect-status-card.ts",
        "timeline-card": "src/timeline-card.ts",
      },
      output: {
        format: "es",
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        minify: {
          compress: true,
          mangle: { toplevel: true },
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    include: ["test/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/index.ts"],
      thresholds: { lines: 90 },
      reporter: ["text-summary"],
    },
  },
});
