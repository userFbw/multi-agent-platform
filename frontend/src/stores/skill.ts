import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { agentApi } from '@/api/agent'
import { useAuthStore } from './auth'
import type { ApiAgent, ApiSkill } from '@/types/api'
import type { NewAgentInput, RegistryAgent } from '@/types/skill'

function toRegistry(a: ApiAgent): RegistryAgent {
  return {
    id: a.id,
    userId: a.user_id,
    roleKey: a.role_key,
    name: a.name,
    mode: a.mode,
    skillId: a.skill_id ?? null,
    systemPrompt: a.system_prompt ?? null,
    description: a.description ?? null,
    promptText: a.prompt_text ?? null,
    outputKind: a.output_kind ?? null,
    builtin: a.builtin
  }
}

export const useSkillStore = defineStore('skill', () => {
  const agents = ref<RegistryAgent[]>([])
  const loading = ref(false)
  const error = ref('')

  /** skill_id → 技能声明的 contract（画布判"能不能当决策源"要用它） */
  const skillContracts = ref<Record<string, string>>({})
  /** 完整技能清单（左栏悬停卡要显示"需要哪些输入"，来自技能声明的 input[]） */
  const skills = ref<ApiSkill[]>([])

  const builtin = computed(() => agents.value.filter((a) => a.builtin))
  const custom = computed(() => agents.value.filter((a) => !a.builtin))

  /**
   * 拉一次技能清单，缓存 skill_id → contract。
   *
   * 为什么画布需要它：内置 Agent 行的 `output_kind` 都是 NULL，它们的契约来自技能声明
   * （审查员 code-reviewer = json_object）。少了这一层，画布会把审查员误判成"不能当决策源"。
   * 只拉一次（契约是技能文件里的静态声明，不随用户操作变）；失败就静默降级为"算不出"。
   */
  async function ensureSkillContracts(): Promise<void> {
    if (Object.keys(skillContracts.value).length) return
    try {
      const res = await agentApi.listSkills(false)
      const map: Record<string, string> = {}
      for (const item of res.items ?? []) map[item.skill_id] = String(item.contract)
      skillContracts.value = map
      skills.value = res.items ?? []
    } catch {
      /* 拉不到就按"算不出契约"处理（等价于 text），不影响其它功能 */
    }
  }

  const skillContractOf = (skillId: string | null | undefined): string | null =>
    skillId ? (skillContracts.value[skillId] ?? null) : null

  async function init(force = false): Promise<void> {
    const auth = useAuthStore()
    if (!auth.user) return
    if (agents.value.length && !force) return
    loading.value = true
    error.value = ''
    try {
      agents.value = (await agentApi.list(auth.user.id)).map(toRegistry)
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function register(input: NewAgentInput): Promise<RegistryAgent> {
    const auth = useAuthStore()
    if (!auth.user) throw new Error('未登录')
    const created = await agentApi.create({
      user_id: auth.user.id,
      name: input.name,
      mode: input.mode,
      skill_id: input.skillId,
      system_prompt: input.systemPrompt,
      output_kind: input.outputKind ?? null
    })
    const item = toRegistry(created)
    agents.value.unshift(item)
    return item
  }

  /** 更新自定义 Agent（PUT /api/agents/{id}，§3.4）。内置行后端会 403，前端不提供入口。 */
  async function updateAgent(id: number, input: NewAgentInput): Promise<RegistryAgent> {
    const updated = await agentApi.update(id, {
      name: input.name,
      mode: input.mode,
      // prompt 模式 skill_id 必须为空；skill 模式 system_prompt 不参与
      skill_id: input.mode === 'skill' ? input.skillId : null,
      system_prompt: input.mode === 'prompt' ? input.systemPrompt : null,
      output_kind: input.outputKind ?? null
    })
    const item = toRegistry(updated)
    const idx = agents.value.findIndex((a) => a.id === id)
    if (idx >= 0) agents.value[idx] = item
    return item
  }

  async function remove(id: number): Promise<void> {
    await agentApi.remove(id)
    agents.value = agents.value.filter((a) => a.id !== id)
  }

  return {
    agents, loading, error, builtin, custom,
    skillContracts, skills, skillContractOf, ensureSkillContracts,
    init, register, updateAgent, remove
  }
})
