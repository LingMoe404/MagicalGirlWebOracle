import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: '/admin/assets/',
  build: {
    outDir: fileURLToPath(new URL('../src/magicalgirl_web_oracle/admin/static', import.meta.url)),
    emptyOutDir: false,
    rollupOptions: {
      onwarn(warning, warn) {
        // Fluent's client-component markers are redundant in this browser-only
        // bundle; keep all other Rollup diagnostics visible.
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE' && warning.message.includes('"use client"')) return;
        warn(warning);
      },
      output: {
        entryFileNames: 'app.js', chunkFileNames: '[name].js', assetFileNames: '[name][extname]',
        manualChunks(id) {
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'react-vendor';
          if (id.includes('node_modules')) return 'fluent-vendor';
        },
      },
    },
  },
});
