import { http } from './http'
import type {
  ApiAgentUsage,
  ApiAgent,
  ApiAuthorAgentInput,
  ApiAuthorAgentResp,
  ApiCreateAgentInput,
  ApiSkillListResp,
  ApiUpdateAgentInput
} from '@/types/api'

export const agentApi = {
  /** 获取角色菜单（内置 + 当前用户自定义） */
  list: (userId: number) => http.get<ApiAgent[]>('/api/agents', { user_id: userId }),

  /**
   * 一句话描述 → 规范化提示词（§3.2）。
   * ⚠️ 只生成、不落库；会真起一次 DSH 会话（10–60 秒，烧模型额度）。
   */
  author: (input: ApiAuthorAgentInput) =>
    // ⚠️ 必须带上身份：这个端点以前只从 query/token 认身份（不像 POST /api/agents 会读 body），
    //    前端漏传就必回 422「缺少身份信息」——实测报障就是这个原因。
    http.post<ApiAuthorAgentResp>('/api/agents/author', input, { user_id: input.user_id }),

  create: (input: ApiCreateAgentInput) => http.post<ApiAgent>('/api/agents', input),

  /**
   * 平台技能清单（§3.5）：新建 Agent「高级模式」下拉的数据源。
   * 以前没这个接口，只能让用户手敲 skill_id —— 而后端白名单是硬编码的，两边容易对不上。
   */
  listSkills: (bindableOnly = false) =>
    http.get<ApiSkillListResp>('/api/skills', bindableOnly ? { bindable_only: true } : undefined),

  /** PUT /api/agents/{id}（§3.4）：内置行 → 403；他人的自定义 → 404 */
  update: (id: number, body: ApiUpdateAgentInput) => http.put<ApiAgent>(`/api/agents/${id}`, body),

  /** 删除前的影响面：这个 Agent 被哪些工作流引用、被多少项目用过 */
  usage: (id: number) => http.get<ApiAgentUsage>(`/api/agents/${id}/usage`),

  remove: (id: number) => http.del<void>(`/api/agents/${id}`)
}
