import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 后端服务地址，可用 .env 的 VITE_API_TARGET 覆盖
  const target = env.VITE_API_TARGET || 'http://8.134.74.75:8000'

  const proxy = {
    '/api': { target, changeOrigin: true },
    '/previews': { target, changeOrigin: true },
    '/avatars': { target, changeOrigin: true }
  }

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      port: 3000,
      open: true,
      proxy
    },
    preview: {
      port: 3000,
      proxy
    }
  }
})
