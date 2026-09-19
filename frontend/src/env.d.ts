/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 平台自身请求基址（默认空串 = 同源，走 vite 代理） */
  readonly VITE_API_BASE?: string
  /** 平台 dev/preview 代理的后端目标 */
  readonly VITE_API_TARGET?: string
  /** 仅用于预览 iframe 注入 window.__API_BASE__，与平台自身基址解耦（F1-13） */
  readonly VITE_PREVIEW_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
