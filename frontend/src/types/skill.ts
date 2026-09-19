import type { ApiAgentMode, ApiOutputKind } from './api'

/** 角色注册表项（对应后端 agents 表 / /api/agents） */
export interface RegistryAgent {
  id: number
  userId: number | null
  roleKey: string
  name: string
  mode: ApiAgentMode
  skillId: string | null
  systemPrompt: string | null
  /** 一句话说明（内置角色也有；来自 SKILL.md） */
  description: string | null
  /** 实际生效的提示词正文（内置角色 = SKILL.md 正文） */
  promptText: string | null
  /** 输出契约声明；空 = 用所绑技能的声明（接口文档 §3.1） */
  outputKind: ApiOutputKind | null
  builtin: boolean
}

export interface NewAgentInput {
  name: string
  mode: ApiAgentMode
  skillId: string | null
  systemPrompt: string | null
  outputKind?: ApiOutputKind | null
}
