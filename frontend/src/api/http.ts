/** 真实 HTTP 客户端（对接 FastAPI 后端） */

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

/** 身份凭证的存放位置（接口文档 §〇.1 / §十） */
export const TOKEN_KEY = 'agent-work-token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* localStorage 不可用时静默降级为「不带 token」，后端仍可用 ?user_id= 兼容 */
  }
}

/**
 * 401 钩子：token 失效时由上层（main.ts）清登录态并回登录页。
 * 登录接口本身密码错也返回 401，但那时用户并未登录，上层会自行忽略。
 */
let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn
}

/** 给请求补上身份头；过渡期后端也接受 ?user_id=，两者可并存 */
function withAuth(init: RequestInit): RequestInit {
  const token = getToken()
  if (!token) return init
  const headers: Record<string, string> = { ...((init.headers as Record<string, string>) ?? {}) }
  headers.Authorization = `Bearer ${token}`
  return { ...init, headers }
}

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail ?? null
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const sp = new URLSearchParams()
  Object.entries(query).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.append(k, String(v))
  })
  const qs = sp.toString()
  return qs ? `${path}?${qs}` : path
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>
    const m = d.detail ?? d.message
    if (typeof m === 'string' && m) return m
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit, query?: Query): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${withQuery(path, query)}`, withAuth(init))
  } catch {
    throw new ApiError(0, '无法连接后端服务，请检查服务是否启动或网络是否可达')
  }

  if (!res.ok) {
    if (res.status === 401) {
      // token 失效（或后端开了强制身份）：先清掉这个失效凭证，再交给上层处理
      setToken(null)
      unauthorizedHandler?.()
    }
    let detail: unknown = null
    const text = await res.text().catch(() => '')
    try {
      detail = text ? JSON.parse(text) : null
    } catch {
      detail = text
    }
    throw new ApiError(res.status, messageFrom(detail, `请求失败 (${res.status})`), detail)
  }

  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return (await res.json()) as T
  return (await res.text()) as unknown as T
}

export const http = {
  get: <T>(path: string, query?: Query) => request<T>(path, { method: 'GET' }, query),

  post: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>(
      path,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      },
      query
    ),

  put: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>(
      path,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      },
      query
    ),

  del: <T>(path: string, query?: Query) => request<T>(path, { method: 'DELETE' }, query),

  upload: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form })
}

/** 拼接静态资源/文件下载地址（走同一代理前缀） */
export const assetUrl = (path: string) => `${BASE}${path}`
