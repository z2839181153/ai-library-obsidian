import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发服务器 5173；/api 与 /ws 代理到后端 8800
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8800', changeOrigin: true },
      '/ws': { target: 'http://127.0.0.1:8800', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
