import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发期把 /api 转发到后端,前端用相对路径 fetch,免跨域
export default defineConfig({
  plugins: [react()],
  resolve: {
    // @/ 指向 src/(shadcn 约定)。用 fileURLToPath 免装 @types/node 的 path 类型。
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
