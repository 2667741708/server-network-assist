import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const repositoryRoot = resolve(fileURLToPath(new URL('.', import.meta.url)), '..');

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: resolve(repositoryRoot, 'src/server_network_assist/desktop_ui'),
    emptyOutDir: false,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: 'desktop.js',
        chunkFileNames: 'desktop-chunk-[hash].js',
        assetFileNames: (asset) => asset.name?.endsWith('.css') ? 'desktop.css' : 'assets/[name]-[hash][extname]'
      }
    }
  }
});
