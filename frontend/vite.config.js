import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: { input: { main: 'index.html', prototype: 'prototype.html', legacy: 'legacy.html' } },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8010',
      '/workspace': 'http://127.0.0.1:8010',
    },
  },
})
