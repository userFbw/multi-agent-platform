import type { AgentType } from './agent'
import type { ApiNodeWhen, ApiWorkflowNode } from './api'

export type WorkflowNodeKind = 'start' | 'end' | 'skill' | AgentType

export interface WorkflowNode {
  id: string
  type: string
  kind: WorkflowNodeKind
  label: string
  position: { x: number; y: number }
  icon?: string
  role?: string
  skillId?: string
  /**
   * 执行条件：满足才跑（不相等就 SKIPPED）。不设 = 无条件执行（依赖就绪就跑）。
   * ref 形如 `决策源节点id.parsed.字段`，取值闭集由平台从所有 when 反推。
   */
  when?: ApiNodeWhen | null
  /**
   * 后端返回的**原始节点定义**（画布不编辑的那些字段：inputs / output_kind /
   * task_note / artifact_file / code_dir / system_prompt …）。
   *
   * 保存时以它为底再覆盖画布管得着的字段 —— 以前画布只发 id/name/agent/deps，
   * 打开一张带这些字段的图（内置模版、别人给的图）改一下再保存就**把它们悄悄丢了**
   * （实测：另存内置模版后，`artifact_file: PRD.md` 没了，产物名变成「生成 PRD.md」）。
   */
  raw?: ApiWorkflowNode
}

export interface WorkflowEdge {
  id: string
  source: string
  target: string
}
