import type { ApiPreviewResp } from '@/types/api'

/**
 * 预览地址拼装（**必须按用户浏览器所在的位置拼，不能用后端给的主机名**）
 *
 * 为什么不能用后端的 `preview_url`：
 *   复杂项目（前后端分离）的页面由**生成的那个后端**托管，跑在独立端口（如 8100）。
 *   后端只能用「请求来源」拼主机名，而请求是**服务器内部**发出来的（前端经 vite 代理，
 *   `changeOrigin` 让来源变成 127.0.0.1:8000），于是拼出来是 `http://127.0.0.1:8100/` ——
 *   用户远程访问时，这个地址指向**他自己的电脑**，点开必然白屏。
 *   所以主机名只能由浏览器自己提供：`location.hostname`。
 *
 * 真机踩到过：平台部署在云服务器（8.134.74.75），用户点「打开应用」拿到 127.0.0.1:8100 打不开。
 */

/** 后端返回的预览类型：app=要起后端的复杂项目（独立端口），static=纯前端静态页 */
export type PreviewKind = 'static' | 'app'

export interface ResolvedPreview {
  /** 可直接给 iframe / 新窗口打开的地址；没有产物时为 null */
  url: string | null
  kind: PreviewKind
  /** app 模式下生成后端是否在跑（running / not_started / occupied…） */
  appStatus: string | null
  /** app 模式生成后端监听的端口 */
  appPort: number | null
  /** app 模式实际用的主机名（部署方声明的优先） */
  appHost: string | null
}

/** 当前浏览器的主机名（拿不到时退化为 localhost） */
export function currentHostname(): string {
  const h = typeof window !== 'undefined' ? window.location?.hostname : ''
  return h || 'localhost'
}

/**
 * 把 `GET /preview-url` 的响应解析成"用户视角"的地址。
 *
 * 主机名优先级：`app_host`（部署方声明的公网地址）> `location.hostname`（当前浏览器）。
 * 为什么要留 `app_host` 这一手：用户可能用**隧道**访问平台（浏览器地址是 `localhost:3000`），
 * 这时按 hostname 会拼出 `http://localhost:8100/` = 用户自己电脑的 8100 → "拒绝连接"
 * （真机踩到）。部署方最清楚这台机器的对外地址，所以允许它声明。
 *
 * · kind=app  → `http://<主机名>:<app_port><path>`（http：生成项目的后端是明文 HTTP）
 * · kind=static → 沿用后端给的地址（是 `/previews/...` 相对路径，天然跟着当前来源走）
 */
export function resolvePreview(resp: ApiPreviewResp | null | undefined): ResolvedPreview {
  if (!resp || !resp.is_web_project) {
    return { url: null, kind: 'static', appStatus: null, appPort: null, appHost: null }
  }

  if (resp.kind === 'app' && resp.app_port) {
    const path = resp.path && resp.path.startsWith('/') ? resp.path : '/'
    const host = resp.app_host || currentHostname()
    return {
      url: `http://${host}:${resp.app_port}${path}`,
      kind: 'app',
      appStatus: resp.app_status ?? null,
      appPort: resp.app_port,
      appHost: host,
    }
  }

  return { url: resp.preview_url ?? null, kind: 'static', appStatus: null, appPort: null, appHost: null }
}
