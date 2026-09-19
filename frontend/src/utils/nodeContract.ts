/**
 * 节点这次会按哪种契约解析输出 —— 与后端 `ai_client.resolve_contract` 的**四层优先级一致**：
 *
 *     ① 节点级 `output_kind`（run_kind） > ② Agent 的 `output_kind`（agent_kind）
 *     > ③ **技能声明的 contract**（skill 的 frontmatter） > ④ text
 *
 * 为什么必须四层都算（血泪）：内置 Agent 行（pm / qa / **reviewer** / docgen）的
 * `output_kind` 全是 NULL，它们的契约来自**技能**——审查员 `code-reviewer` 声明的是
 * `json_object`，所以它能当条件分支的决策源。只算前两层的后果是：画布会把分类器标成
 * "不能当决策源"，而它其实完全可以（先把用户教错了，再让他去改一个根本不用改的东西）。
 */
import type { ApiOutputKind } from '@/types/api'

export type ContractKind = ApiOutputKind | string

export interface ContractSources {
  /** agents.output_kind（按 role_key 查） */
  agentKind: (roleKey: string) => ContractKind | null | undefined
  /** agents.skill_id（按 role_key 查） */
  agentSkill: (roleKey: string) => string | null | undefined
  /** 技能 frontmatter 的 contract（按 skill_id 查） */
  skillKind: (skillId: string) => ContractKind | null | undefined
}

export interface ContractNode {
  role?: string
  /** 后端返回的原始节点定义（节点级 output_kind 在 raw 里） */
  raw?: { output_kind?: ContractKind | null } | null
}

/** 节点 → 有效契约（字符串；算不出来就是 text，和后端一样） */
export function effectiveContract(node: ContractNode, src: ContractSources): string {
  const nodeKind = node.raw?.output_kind
  if (nodeKind) return String(nodeKind)
  const role = node.role ?? ''
  const agentKind = src.agentKind(role)
  if (agentKind) return String(agentKind)
  const skill = src.agentSkill(role)
  const skillKind = skill ? src.skillKind(skill) : null
  if (skillKind) return String(skillKind)
  return 'text'
}

/**
 * 这个节点能不能当条件分支的**决策源**？
 *
 * 判定就是"它的输出会被解析成 JSON 对象"（后端 `check_decision` 要求 `parsed` 是 dict）；
 * 与"是不是分类器"无关——任何声明了 json_object 的节点都行。
 */
export const canBeDecisionSource = (contract: string): boolean => contract === 'json_object'

/** 给 UI 用的一句话解释：为什么能用 / 为什么不能用 */
export function decisionSourceReason(contract: string): string {
  if (canBeDecisionSource(contract)) return `输出按 JSON 对象解析（${contract}），可作为决策源`
  if (contract === 'json_array') return '输出是 JSON 数组，取不到「字段」——不能当决策源'
  if (contract === 'file_blocks') return '输出是代码文件块，没有字段——不能当决策源'
  return `输出按纯文本解析（${contract}），没有结构化字段——不能当决策源`
}
