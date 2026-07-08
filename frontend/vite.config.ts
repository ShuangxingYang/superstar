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
      // 显式配置:SSE 流式对话必须逐条透传,不能被代理缓冲。
      // 去掉 Accept-Encoding 防止中间层 gzip(gzip 要攒够一块才能解压,会把逐 token 的流憋成整批)。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('Accept-Encoding', 'identity')
          })
        },
      },
    },
  },
})
