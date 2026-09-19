import { http } from './http'
import type { ApiLoginResp, ApiUser } from '@/types/api'

export const userApi = {
  create: (user_name: string, password: string) =>
    http.post<ApiUser>('/api/users', { user_name, password }),

  /** GET /api/users/{id}（§1.3）：不属于自己 → 404；响应会回显 password，展示时忽略 */
  get: (id: number) => http.get<ApiUser>(`/api/users/${id}`),

  update: (id: number, body: Partial<{ user_name: string; password: string; avatar: string }>) =>
    http.put<ApiUser>(`/api/users/${id}`, body),

  /** DELETE /api/users/{id}（§1.5）→ 204；库行级联删除，但**磁盘产物 exports/u<id>/ 不清理** */
  remove: (id: number) => http.del<void>(`/api/users/${id}`),

  login: (user_name: string, password: string) =>
    http.post<ApiLoginResp>('/api/users/login', { user_name, password }),

  uploadAvatar: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http.upload<{ status: string; message: string; avatar_url: string }>(
      `/api/users/${id}/upload-avatar`,
      form
    )
  }
}
