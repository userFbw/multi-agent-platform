/**
 * 左侧 Agent 列表的「悬停详情」数据 —— 纯函数，便于测试。
 *
 * 用户的原话：左栏应该让用户知道"每个节点该干嘛、能执行什么操作"。
 * 数据全部来自已有接口，不需要后端改动：
 *   GET /api/agents → name / role_key / mode / skill_id / output_kind / builtin / description / prompt_text
 *   GET /api/skills → inputs（这个技能需要什么输入）/ contract（输出契约）
 */
import type { RegistryAgent } from '@/types/skill'
import type { ApiSkill } from '@/types/api'

export interface AgentBriefInput {
  id: number
  name: string
  roleKey: string
  mode: string
  skillId: string | null
  outputKind: string | null
  builtin: boolean
  description: string | null
  promptText: string | null
}

export interface AgentBrief {
  /** 卡片标题行 */
  title: string
  /** 「内置 · 高级模式」这类副标题 */
  kindLabel: string
  roleKey: string
  skillId: string | null
  /** 能干什么（一句话，超长截断） */
  summary: string
  /** 有没有被截断（决定要不要给"查看完整内容"） */
  truncated: boolean
  /** 完整原文（给弹层用） */
  fullText: string
  /** 有效输出契约（agent 级 > 技能声明 > text） */
  contract: string
  /** 能不能当条件分支的决策源（= 输出 JSON 对象） */
  canBeDecisionSource: boolean
  /** 这个节点需要哪些输入（来自技能声明；自定义提示词 Agent 没有声明） */
  inputs: Array<{ name: string; required: boolean; description: string }>
}

const SUMMARY_LIMIT = 160

/** 把任意文本压成一行摘要 */
function summarize(text: string, limit = SUMMARY_LIMIT): { text: string; truncated: boolean } {
  const flat = (text || '').replace(/\s+/g, ' ').trim()
  if (!flat) return { text: '', truncated: false }
  if (flat.length <= limit) return { text: flat, truncated: false }
  return { text: flat.slice(0, limit) + '…', truncated: true }
}

export const MODE_LABEL: Record<string, string> = { skill: '高级模式（绑定出厂技能）', prompt: '提示词模式（自定义提示词）' }

/**
 * Agent + （可选）技能信息 → 悬停卡片要显示的东西。
 * @param skill 该 Agent 绑定的技能（`GET /api/skills` 里按 skill_id 找到的那个）；自定义提示词 Agent 为 null
 */
export function agentBrief(agent: AgentBriefInput, skill?: ApiSkill | null): AgentBrief {
  const kindLabel = `${agent.builtin ? '内置' : '自定义'} · ${MODE_LABEL[agent.mode] ?? agent.mode}`
  // 能干什么：内置用技能说明；自定义提示词 Agent 的 description 就是提示词开头，退而取 system_prompt 正文
  const fullText = (agent.description || '').trim() || (agent.promptText || '').trim()
  const { text: summary, truncated } = summarize(fullText)
  const contract = String(agent.outputKind || skill?.contract || 'text')
  return {
    title: agent.name,
    kindLabel,
    roleKey: agent.roleKey,
    skillId: agent.skillId,
    summary,
    truncated,
    fullText,
    contract,
    canBeDecisionSource: contract === 'json_object',
    inputs: (skill?.inputs ?? []).map((i) => ({
      name: i.name,
      required: !!i.required,
      description: i.description || ''
    }))
  }
}

/** 左栏分组：**按 API 的 builtin 字段分**，不用名字猜——新增的 Agent 一定进「我的 Agent」 */
export function splitPalette<T extends { builtin: boolean }>(agents: T[]): { builtin: T[]; custom: T[] } {
  return { builtin: agents.filter((a) => a.builtin), custom: agents.filter((a) => !a.builtin) }
}

/** 把 store 里的 RegistryAgent 转成 brief 需要的输入形状 */
export const toBriefInput = (a: RegistryAgent): AgentBriefInput => ({
  id: a.id,
  name: a.name,
  roleKey: a.roleKey,
  mode: a.mode,
  skillId: a.skillId ?? null,
  outputKind: a.outputKind ?? null,
  builtin: !!a.builtin,
  description: a.description ?? null,
  promptText: a.promptText ?? null
})
