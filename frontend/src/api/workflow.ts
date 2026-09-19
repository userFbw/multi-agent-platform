import { http } from './http'
import type {
  ApiWorkflow,
  ApiWorkflowListResp,
  ApiWorkflowNode,
  ApiWorkflowUsage
} from '@/types/api'

export interface WorkflowBody {
  name: string
  description?: string
  nodes: ApiWorkflowNode[]
}

export const workflowApi = {
  list: (userId: number) => http.get<ApiWorkflowListResp>('/api/workflows', { user_id: userId }),

  create: (userId: number, body: WorkflowBody) =>
    http.post<ApiWorkflow>('/api/workflows', body, { user_id: userId }),

  /** PUT /api/workflows/{id}（§5.3）：只能改自己的；内置模板 id 为 null，天然动不了 */
  update: (id: number, body: Partial<WorkflowBody>) =>
    http.put<ApiWorkflow>(`/api/workflows/${id}`, body),

  remove: (id: number) => http.del<void>(`/api/workflows/${id}`),

  /** 这张图被哪些项目引用（改动/删除前的影响面，§5.x） */
  usage: (workflowId: number) => http.get<ApiWorkflowUsage>(`/api/workflows/${workflowId}/usage`),

  validate: (body: WorkflowBody) =>
    http.post<{ valid: boolean; workflow: ApiWorkflow }>('/api/workflows/validate', body),

  execute: (body: {
    project_id: number
    round_no?: number
    seeds?: Record<string, unknown>
    workflow_name?: string
    nodes?: ApiWorkflowNode[] | null
  }) => http.post<unknown>('/api/workflows/execute', body)
}
