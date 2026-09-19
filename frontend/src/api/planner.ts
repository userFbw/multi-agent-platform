import { http } from './http'
import type { ApiPlannerResp } from '@/types/api'

export const plannerApi = {
  /** 自然语言拆解 → 转 workflow → 异步执行 */
  plan: (projectId: number, userRequirement: string) =>
    http.post<ApiPlannerResp>('/api/workflows/planner', {
      project_id: projectId,
      user_requirement: userRequirement
    })
}
