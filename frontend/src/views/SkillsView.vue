<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useSkillStore } from '@/stores/skill'
import { useAuthStore } from '@/stores/auth'
import { agentApi } from '@/api/agent'
import AgentIcon from '@/components/common/AgentIcon.vue'
import { deletionWarning } from '@/utils/agentDeletion'
import { PLACEHOLDERS, placeholderToken, unknownPlaceholders } from '@/utils/promptPlaceholders'
import PromptViewer from '@/components/common/PromptViewer.vue'
import type { RegistryAgent, NewAgentInput } from '@/types/skill'
import type { ApiAgentMode, ApiOutputKind, ApiSkill } from '@/types/api'
import { Check, PenLine, Plus, Search, Sparkles, Trash2, X } from '@lucide/vue'

const store = useSkillStore()
const auth = useAuthStore()
onMounted(async () => {
  await store.init()
  await loadSkills()      // 技能清单：高级模式的下拉要用
})

type Filter = 'all' | 'builtin' | 'custom'

/** 正在编辑的是**存量 skill 别名行**（历史数据）：只改名字/绑定，提示词不可编。 */
const isSkillAlias = computed(() => form.mode === 'skill')
const filter = ref<Filter>('all')
const keyword = ref('')

const filters: Array<{ key: Filter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'builtin', label: '内置' },
  { key: 'custom', label: '自定义' }
]

const countFor = (k: Filter) =>
  k === 'all' ? store.agents.length : k === 'builtin' ? store.builtin.length : store.custom.length

const filtered = computed(() => {
  let items = store.agents
  if (filter.value === 'builtin') items = store.builtin
  else if (filter.value === 'custom') items = store.custom
  const kw = keyword.value.trim().toLowerCase()
  if (kw) items = items.filter((a) => a.name.toLowerCase().includes(kw) || a.roleKey.toLowerCase().includes(kw))
  return items
})

const iconFor = (a: RegistryAgent) => {
  if (/pm|产品|需求/.test(a.name) || /pm/.test(a.roleKey)) return 'clipboard'
  if (/dev|程序|代码/.test(a.name) || /dev/.test(a.roleKey)) return 'code'
  if (/qa|测试/.test(a.name) || /qa/.test(a.roleKey)) return 'shield'
  if (/review|审查/.test(a.name)) return 'shield'
  if (/doc|文档/.test(a.name)) return 'file'
  return 'sparkles'
}

const modeLabel: Record<ApiAgentMode, string> = { skill: '高级', prompt: '提示词' }

const selected = ref<RegistryAgent | null>(null)

// ---- 注册表单 ----
// 长度约束与接口文档 §3.2 / §3.3 对齐：name 1–30 字；prompt 模式 system_prompt 10–8000 字（skill_id 必须为空）；
// 高级模式 skill_id 必填；agent_goal 2–500 字。
const NAME_MAX = 30
const PROMPT_MIN = 10
const PROMPT_MAX = 8000      // 与后端 routes/agents.py 的 PROMPT_MAX 一致（整合出来的提示词会更长）

/** 模板里直接写 `{{名字}}` 会被 Vue 当成插值（编译报错），所以这句话放脚本里 */
const PLACEHOLDER_HINT = '可用占位符（平台只会替换这些名字，写法 {{名字}} 或 {{input.名字}}）：'
/** 展示用的 `{{xxx}}` 字样一律在脚本里拼好再交给模板（模板里嵌花括号会被解析器截断） */
const placeholderOptions = computed(() => PLACEHOLDERS.map((p) => ({ ...p, token: placeholderToken(p.name) })))
/** 写了平台填不了的名字 → 只提示、不拦截（跑了才知道后果的那种坑更糟） */
const unknownTokens = computed(() => unknownPlaceholders(form.systemPrompt).map(placeholderToken))
const GOAL_MIN = 2
const GOAL_MAX = 500

const showForm = ref(false)
const submitting = ref(false)
const error = ref('')
/** 非空 = 正在编辑该自定义 Agent（走 PUT），为空 = 新建（走 POST） */
const editingId = ref<number | null>(null)

/* ---------------- 技能清单（高级模式选 skill_id 的数据源，§3.5）----------------
 * 以前是 input 手填，而后端白名单硬编码 —— 用户不知道有哪些、填错要等 422 才知道。
 * 标签用「内置角色中文名 · skill_id」：用户认得"产品经理"，也必须看到要填的那个字符串。 */
const skills = ref<ApiSkill[]>([])

async function loadSkills() {
  try {
    const res = await agentApi.listSkills()
    skills.value = res.items
  } catch {
    skills.value = []   // 拉不到就退化成"没有可选项"，并在 hint 里说明
  }
}

const bindableSkills = computed(() => skills.value.filter((s) => s.bindable))

const skillOptions = computed(() =>
  bindableSkills.value.map((s) => {
    const zh = store.builtin.find((a) => a.skillId === s.skill_id)?.name
    return {
      value: s.skill_id,
      label: zh ? `出厂技能：${zh}（${s.skill_id}）` : `出厂技能：${s.skill_id}`,
      description: s.description,
      contract: s.contract
    }
  })
)

/** 当前选中的技能（用它的契约做只读展示、也用它的说明做提示） */
const pickedSkill = computed(() => bindableSkills.value.find((s) => s.skill_id === form.skillId) ?? null)

/** 输出契约的人话解释（讲清"这是给平台解析用的"，不是"要求模型输出什么"） */
const CONTRACT_LABELS: Record<ApiOutputKind, string> = {
  text: '纯文本',
  json_object: 'JSON 对象',
  json_array: 'JSON 数组',
  file_blocks: '代码文件块'
}

/**
 * 每个选项**平台会做什么**（实测报障：只写"输出契约"没人看得懂它的后果）。
 * 三个后果维度：① 会不会被结构解析 ② 产物落哪 ③ 能不能被条件分支按字段读。
 */
const CONTRACT_EFFECTS: Array<{ value: ApiOutputKind; label: string; effect: string }> = [
  {
    value: 'text',
    label: 'text · 纯文本（默认）',
    effect:
      '平台不解析，原文照收。产物落成「步骤名.md」；下游 Agent 直接读文本。最省事、也最安全，适合「写报告/写方案」这类只看内容的角色。'
  },
  {
    value: 'json_object',
    label: 'json_object · 单个 JSON 对象',
    effect:
      '平台会把产出解析成 JSON 对象 → 能被条件分支按字段读（如 level=simple/complex 决定走哪条链）；解析不出来只记 CONTRACT_SOFTFAIL，步骤仍算成功。'
  },
  {
    value: 'json_array',
    label: 'json_array · JSON 数组',
    effect:
      '期望 [{file_name, code_block}, …] → 平台会把每个文件写进项目 src/（simple-frontend 用的就是这个）。写不出来就当纯文本落成 .md。'
  },
  {
    value: 'file_blocks',
    label: 'file_blocks · 代码文件块',
    effect:
      '期望每个文件一行「# File: 路径」+ 代码块 → 同样写进项目 src/（前后端执行器用的就是这个）。'
  }
]

const outputKindOptions: Array<{ value: ApiOutputKind; label: string }> = [
  { value: 'text', label: 'text · 纯文本（默认）' },
  { value: 'json_object', label: 'json_object · 单个对象' },
  { value: 'json_array', label: 'json_array · 数组' },
  { value: 'file_blocks', label: 'file_blocks · 文件块' }
]

const form = reactive({
  name: '',
  mode: 'prompt' as ApiAgentMode,
  /** 新建时的起点：scratch=从零写；skill=选一个出厂技能，把要求整合进去。
   *  ⚠️ 两者最终都落成 mode='prompt'（整合产物就是一段提示词）；`form.mode` 只在
   *  编辑**存量 skill 别名行**时才为 'skill'。 */
  origin: 'scratch' as 'scratch' | 'skill',
  systemPrompt: '',
  skillId: '',
  outputKind: 'text' as ApiOutputKind,
  agentGoal: ''
})

const nameError = computed(() => {
  const n = form.name.trim()
  if (!n) return '请填写名称'
  if (n.length > NAME_MAX) return `名称最多 ${NAME_MAX} 字`
  return ''
})

const promptError = computed(() => {
  if (isSkillAlias.value) return ''
  const p = form.systemPrompt.trim()
  if (p.length < PROMPT_MIN) return `系统提示词至少 ${PROMPT_MIN} 字`
  if (p.length > PROMPT_MAX) return `系统提示词最多 ${PROMPT_MAX} 字`
  return ''
})

const skillError = computed(() => {
  if (!isSkillAlias.value) return ''
  return form.skillId.trim() ? '' : '请选择要绑定的出厂技能'
})

const canSubmit = computed(() => !nameError.value && !promptError.value && !skillError.value)

// ---- 一句话描述 → 规范化提示词（接口文档 §3.2：只生成不落库，会真起 DSH 会话）----
const authoring = ref(false)
const authorHint = ref('')
const authoredBy = ref('')      // 这次提示词是哪个技能生成的（接口返回的 skill_id）
const integratedFrom = ref('')  // 基于哪个出厂技能整合的（空 = 从零撰写）
const authorWarn = ref('')

const canAuthor = computed(() => {
  const g = form.agentGoal.trim()
  return g.length >= GOAL_MIN && g.length <= GOAL_MAX && !authoring.value
})

/* ---- 生成过程要"看得见"（实测报障：点了没反应，不知道有没有开始、也不知道结果在哪）----
 * ① 按钮上带秒表（真实耗时 10–60 秒，没计时会让人以为卡死）；
 * ② 按钮下方一条状态行（进行中/成功/失败），而不是把错误甩到表单最底部；
 * ③ 成功后把提示词框滚进视野并闪一下高亮 —— 结果就是填在那个框里的。 */
const authorElapsed = ref(0)
const authorError = ref('')
const promptBox = ref<HTMLElement | null>(null)
const promptFlash = ref(false)
let authorTimer: number | null = null

function startAuthorTimer() {
  authorElapsed.value = 0
  authorTimer = window.setInterval(() => (authorElapsed.value += 1), 1000)
}
function stopAuthorTimer() {
  if (authorTimer !== null) {
    window.clearInterval(authorTimer)
    authorTimer = null
  }
}

async function revealPrompt() {
  await nextTick()
  promptBox.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  promptFlash.value = true
  window.setTimeout(() => (promptFlash.value = false), 1800)
}

const authorPrompt = async () => {
  if (!auth.user) {
    error.value = '未登录，无法生成'
    return
  }
  if (!canAuthor.value) {
    error.value = `请先填写「一句话描述」（${GOAL_MIN}–${GOAL_MAX} 字）`
    return
  }
  if (!window.confirm('这一步会真起一次 AI 会话（约 10–60 秒，会消耗模型额度），继续？')) return

  authoring.value = true
  error.value = ''
  authorHint.value = ''
  authorWarn.value = ''
  authorError.value = ''
  startAuthorTimer()
  try {
    const integrate = !isSkillAlias.value && form.origin === 'skill'
    const res = await agentApi.author({
      user_id: auth.user.id,
      agent_goal: form.agentGoal.trim(),
      user_draft: form.systemPrompt.trim() || null,
      // 传 base_skill_id = 让写手把「出厂技能 + 你的要求」**整合成一份**，而不是追加在末尾
      base_skill_id: integrate ? form.skillId.trim() : null
    })
    form.systemPrompt = res.system_prompt ?? ''
    // 整合时输出契约**跟随原技能**（否则整合后产出的 JSON/代码块会被当纯文本解析）
    if (res.suggested_output_kind) form.outputKind = res.suggested_output_kind
    if (res.base_skill_id) integratedFrom.value = res.base_skill_id
    authoredBy.value = res.skill_id || ''
    authorHint.value = res.base_skill_id
    ? `已基于出厂技能「${res.base_skill_id}」整合：${res.char_count} 字 · 用时 ${res.elapsed_seconds}s —— 确认无误后再点「注册」落库（原技能不变）`
    : `已生成 ${res.char_count} 字 · 用时 ${res.elapsed_seconds}s —— 确认无误后再点「注册」落库`
    if (!res.usable) {
      authorWarn.value = '生成结果不足 10 字，直接落库会被后端拒绝；请补充描述后重试，或手动编辑提示词。'
    }
    await revealPrompt()
  } catch (e) {
    // 就地展示（表单底部那条离按钮太远，实测用户看不到 → 以为"没反应"）
    authorError.value = (e as Error).message
    error.value = (e as Error).message
  } finally {
    authoring.value = false
    stopAuthorTimer()
  }
}

const openForm = () => {
  editingId.value = null
  showForm.value = true
  error.value = ''
}

/** 编辑自定义 Agent：把已有值填回表单，提交时走 PUT /api/agents/{id} */
const openEdit = (a: RegistryAgent) => {
  error.value = ''
  authorHint.value = ''
  authorWarn.value = ''
  editingId.value = a.id
  form.name = a.name
  form.mode = a.mode
  form.origin = a.mode === 'skill' ? 'skill' : 'scratch'
  integratedFrom.value = ''
  form.skillId = a.skillId ?? ''
  form.systemPrompt = a.systemPrompt ?? ''   // skill 模式下它 = 附加要求（可以为空）
  // 高级模式的输出契约由技能决定，不回填（避免把历史值当成用户意图再发回去）
  form.outputKind = a.mode === 'prompt' ? ((a.outputKind ?? 'text') as ApiOutputKind) : 'text'
  form.agentGoal = ''
  selected.value = null
  showForm.value = true
}

const closeForm = () => {
  showForm.value = false
  editingId.value = null
  error.value = ''
  authorHint.value = ''
  authorWarn.value = ''
  form.name = ''
  form.mode = 'prompt'
  form.origin = 'scratch'
  integratedFrom.value = ''
  form.systemPrompt = ''
  form.skillId = ''
  form.outputKind = 'text'
  form.agentGoal = ''
}

const submit = async () => {
  if (!canSubmit.value) {
    error.value = nameError.value || promptError.value || skillError.value || '请检查表单填写'
    return
  }
  submitting.value = true
  error.value = ''
  const input: NewAgentInput = {
    name: form.name.trim(),
    // 新建（从零 / 基于出厂技能整合）→ 都是 prompt 模式：落库的就是那段提示词；
    // 只有编辑**存量 skill 别名行**才提交 skill 模式（保持老数据行为不变）。
    mode: isSkillAlias.value ? 'skill' : 'prompt',
    skillId: isSkillAlias.value ? form.skillId.trim() : null,
    // system_prompt 两种模式都有意义：
    //   prompt 模式 = 这个角色**就是**这段提示词（必填 ≥10 字，后端会校验）；
    //   skill  模式 = 在出厂技能正文之后**附加**你自己的要求（可选，留空 = 与内置角色完全一致）。
    //   运行时的拼接顺序（ai_client.build_prompt）：技能正文 → 附加要求 → 任务输入 → 执行要求。
    systemPrompt: form.systemPrompt.trim() || null,
    // 别名行：输出契约由技能决定，不覆盖（agent 级优先级更高，乱选会把技能声明顶掉）；
    // 提示词行：用表单里的值（整合时已被自动设成原技能的契约）。
    outputKind: isSkillAlias.value ? null : (form.outputKind || 'text')
  }
  try {
    if (editingId.value != null) await store.updateAgent(editingId.value, input)
    else await store.register(input)
    closeForm()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    submitting.value = false
  }
}

/* ---------------- 删除自定义 Agent（先查影响面，再说清楚后果） ---------------- */
const deleting = ref<number | null>(null)
/** 删完给一句后果说明：引用它的工作流节点现在解析不到角色了 */
const removedNotice = ref('')

/**
 * 删除入口在**两个地方**都能点到：卡片上的垃圾桶（不用先点开抽屉）、抽屉底部的「删除」。
 * 两者都走这里：先问后端"谁还在用它"，把影响写进确认框，用户确认后才真删。
 */
const askRemove = async (agent: RegistryAgent) => {
  if (agent.builtin) {
    error.value = '内置 Agent 不允许删除（它是全平台共享的角色）'
    return
  }
  error.value = ''
  deleting.value = agent.id
  try {
    const usage = await agentApi.usage(agent.id)
    if (!usage.deletable) {
      error.value = usage.reason || '这个 Agent 不允许删除'
      return
    }
    const warn = deletionWarning(usage)
    if (!window.confirm(warn.message)) return

    await store.remove(agent.id)
    if (selected.value?.id === agent.id) selected.value = null
    removedNotice.value = warn.workflowCount
      ? `已删除「${agent.name}」。它被 ${warn.workflowCount} 张工作流引用过（${warn.workflowNames.join('、')}），那些图里的对应节点现在解析不到角色 —— 打开画布会看到红色提示，请删掉该节点或换成别的 Agent。`
      : `已删除「${agent.name}」，当前没有工作流引用它。`
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    deleting.value = null
  }
}
</script>

<template>
  <main class="skills">
    <div class="skills__head">
      <div>
        <h1 class="skills__title">Agent</h1>
        <p class="skills__sub">
          平台内置角色 + 你自定义的 Agent：新建、编辑、删除，删除会先告诉你会影响哪些工作流
        </p>
      </div>
      <button type="button" class="aw-btn aw-btn--primary aw-btn--lg" @click="openForm">
        <Plus :size="16" :stroke-width="2.2" />
        新建 Agent
      </button>
    </div>

    <p v-if="removedNotice" class="skills__notice">
      <Check :size="13" :stroke-width="2.4" />
      <span>{{ removedNotice }}</span>
      <button type="button" class="skills__notice-close" title="知道了" @click="removedNotice = ''">
        <X :size="12" :stroke-width="2" />
      </button>
    </p>
    <p v-if="error" class="skills__error">{{ error }}</p>

    <div class="skills__toolbar">
      <div class="skills__search">
        <Search :size="14" :stroke-width="2" />
        <input v-model="keyword" class="skills__search-input" placeholder="搜索…" />
      </div>
      <div class="aw-segment">
        <button
          v-for="f in filters"
          :key="f.key"
          type="button"
          class="aw-segment__btn"
          :class="{ 'aw-segment__btn--active': filter === f.key }"
          @click="filter = f.key"
        >
          {{ f.label }}
          <span class="skills__count">{{ countFor(f.key) }}</span>
        </button>
      </div>
    </div>

    <p v-if="store.error" class="skills__error">{{ store.error }}</p>

    <div v-if="filtered.length" class="skills__grid">
      <!--
        卡片本身是 <button>，按钮不能嵌套按钮 → 外面包一层定位容器，
        把「删除」放在右上角（只对自定义 Agent 出现）。这样不用先点开抽屉就能删。
      -->
      <div v-for="a in filtered" :key="a.id" class="skill-item">
        <button
          type="button"
          class="skill"
          :class="{ 'skill--active': selected?.id === a.id }"
          @click="selected = a"
        >
        <span class="skill__icon"><AgentIcon :icon="iconFor(a)" :size="17" :stroke-width="2" /></span>
        <span class="skill__body">
          <span class="skill__top">
            <span class="skill__name">{{ a.name }}</span>
            <span class="skill__mode" :class="`skill__mode--${a.mode}`">{{ modeLabel[a.mode] }}</span>
            <span v-if="a.builtin" class="skill__builtin">内置</span>
          </span>
          <span class="skill__role">{{ a.roleKey }}</span>
          <span class="skill__desc">{{ a.description || a.systemPrompt || a.skillId || '平台内置技能' }}</span>
        </span>
        </button>
        <button
          v-if="!a.builtin"
          type="button"
          class="skill__remove"
          :disabled="deleting === a.id"
          :title="`删除自定义 Agent「${a.name}」`"
          @click.stop="askRemove(a)"
        >
          <Trash2 :size="13" :stroke-width="2" />
        </button>
      </div>
    </div>

    <div v-else-if="!store.loading" class="aw-empty">
      <span class="aw-empty__icon"><AgentIcon icon="sparkles" :size="20" /></span>
      <h3>暂无 Agent</h3>
      <p>注册一个自定义 Agent，或等待后端返回内置角色。</p>
    </div>

    <!-- 详情抽屉 -->
    <transition name="overlay-fade">
      <div v-if="selected" class="overlay" @click.self="selected = null">
        <div class="drawer">
          <div class="drawer__head">
            <span class="drawer__icon"><AgentIcon :icon="iconFor(selected)" :size="18" :stroke-width="2" /></span>
            <div class="drawer__titles">
              <span class="drawer__name">{{ selected.name }}</span>
              <span class="drawer__role">{{ selected.roleKey }}</span>
            </div>
            <button type="button" class="drawer__close" @click="selected = null"><X :size="16" :stroke-width="2" /></button>
          </div>

          <div class="drawer__section">
            <span class="drawer__label">类型</span>
            <span class="skill__mode" :class="`skill__mode--${selected.mode}`">{{ modeLabel[selected.mode] }}</span>
            <span v-if="selected.builtin" class="skill__builtin">平台内置</span>
          </div>
          <div class="drawer__section">
            <span class="drawer__label">skill_id</span>
            <p class="drawer__text drawer__text--mono">{{ selected.skillId || '—' }}</p>
          </div>
          <div class="drawer__section">
            <span class="drawer__label">System Prompt</span>
            <PromptViewer
              :text="selected.promptText || selected.systemPrompt"
              :meta="selected.mode === 'skill' && selected.skillId
                ? `技能 ${selected.skillId} · ${(selected.promptText || '').length} 字`
                : `自定义提示词 · ${(selected.systemPrompt || '').length} 字`"
              empty-text="（未提供提示词）"
            />
          </div>
          <div class="drawer__section">
            <span class="drawer__label">归属</span>
            <p class="drawer__text">{{ selected.builtin ? '平台共享' : `用户 #${selected.userId}` }}</p>
          </div>

          <div v-if="!selected.builtin" class="drawer__foot">
            <button type="button" class="aw-btn aw-btn--default" @click="openEdit(selected)">
              <PenLine :size="14" :stroke-width="2" />
              编辑
            </button>
            <button
              type="button"
              class="aw-btn aw-btn--default drawer__delete"
              :disabled="deleting === selected.id"
              @click="askRemove(selected)"
            >
              <Trash2 :size="14" :stroke-width="2" />
              {{ deleting === selected.id ? '检查影响中…' : '删除' }}
            </button>
          </div>
        </div>
      </div>
    </transition>

    <!-- 注册表单 -->
    <transition name="overlay-fade">
      <div v-if="showForm" class="overlay" @click.self="closeForm">
        <div class="form">
          <div class="form__head">
            <span class="form__title">{{ editingId != null ? '编辑 Agent' : '注册 Agent' }}</span>
            <button type="button" class="drawer__close" @click="closeForm"><X :size="16" :stroke-width="2" /></button>
          </div>

          <div class="form__body">
            <label class="form__field">
              <span class="form__label">
                名称
                <span class="form__count">{{ form.name.trim().length }}/{{ NAME_MAX }}</span>
              </span>
              <input
                v-model="form.name"
                class="form__input"
                placeholder="例如：我的审稿人"
                :maxlength="NAME_MAX"
              />
              <span v-if="nameError" class="form__hint form__hint--warn">{{ nameError }}</span>
            </label>

            <div class="form__field">
              <span class="form__label">注册模式</span>
              <div class="aw-segment form__segment">
                <button
                  type="button"
                  class="aw-segment__btn"
                  :class="{ 'aw-segment__btn--active': form.origin === 'scratch' }"
                  :disabled="isSkillAlias"
                  @click="form.origin = 'scratch'"
                >
                  从零开始
                </button>
                <button
                  type="button"
                  class="aw-segment__btn"
                  :class="{ 'aw-segment__btn--active': form.origin === 'skill' }"
                  :disabled="isSkillAlias"
                  @click="form.origin = 'skill'"
                >
                  基于出厂技能
                </button>
              </div>
              <p class="form__hint">
                <template v-if="isSkillAlias">
                  这是<strong>存量别名</strong>：它绑着出厂技能「{{ form.skillId }}」，行为与内置角色完全一致。
                  想做出自己的版本，请新建一个「基于出厂技能」的角色。
                </template>
                <template v-else-if="form.origin === 'scratch'">
                  从零开始：你写一句话说明它要干什么，平台用技能
                  <code>agent-prompt-authoring</code> 生成一份完整提示词。
                </template>
                <template v-else>
                  基于出厂技能：选一个平台技能，再把你的要求交给写手 —— 它会<strong>把两者整合成一份新提示词</strong>
                  （不是追加在末尾），存成你自己的新角色，<strong>原技能不变</strong>。
                </template>
              </p>
            </div>

            <label v-if="isSkillAlias || form.origin === 'skill'" class="form__field">
              <span class="form__label">{{ isSkillAlias ? '绑定技能' : '基础出厂技能' }}</span>
              <select v-model="form.skillId" class="form__input">
                <option value="">— 请选择平台技能 —</option>
                <option v-for="o in skillOptions" :key="o.value" :value="o.value">
                  {{ o.label }}
                </option>
              </select>
              <span v-if="pickedSkill?.description" class="form__hint">
                {{ pickedSkill.description }}
              </span>
              <span v-if="!skills.length" class="form__hint form__hint--warn">
                技能清单没拉到（后端 /api/skills 不可用），暂时无法选择。
              </span>
              <span v-if="skillError" class="form__hint form__hint--warn">{{ skillError }}</span>
            </label>

            <div v-if="!isSkillAlias" class="form__field">
              <span class="form__label">
                {{ form.origin === 'skill' ? '第一步：在它基础上你要改什么/加什么' : '第一步：一句话说清它要干什么' }}
                <span class="form__count">{{ form.agentGoal.trim().length }}/{{ GOAL_MAX }}</span>
              </span>
              <textarea
                v-model="form.agentGoal"
                class="form__textarea"
                rows="2"
                placeholder="例如：汇总需求并输出结构化摘要"
                :maxlength="GOAL_MAX"
              />
              <div class="form__actions">
                <button
                  type="button"
                  class="aw-btn aw-btn--primary aw-btn--sm"
                  :disabled="!canAuthor"
                  @click="authorPrompt"
                >
                  <Sparkles :size="13" :stroke-width="2" />
                  {{ authoring
                    ? `正在生成… 已 ${authorElapsed}s`
                    : form.origin === 'skill' ? '整合并生成提示词' : '用一句话生成提示词' }}
                </button>
                <span class="form__hint form__hint--inline">
                  <template v-if="form.origin === 'skill'">
                    写手会把「{{ form.skillId || '所选技能' }}」的全文与你的要求<strong>整合成一份新提示词</strong>
                    （约 10–60 秒，消耗额度）—— 原技能只读不变
                  </template>
                  <template v-else>
                    平台会让技能 <code>agent-prompt-authoring</code> 把这句人话写成完整提示词
                    （约 10–60 秒，消耗额度）
                  </template>
                </span>
              </div>

              <!-- 生成过程与结果就地反馈（以前只有按钮文字变一下，用户以为没反应） -->
              <p v-if="authoring" class="form__hint form__hint--busy">
                <span class="aw-spin" /> 已提交给写手技能，正在生成…（已 {{ authorElapsed }} 秒，通常 10–60 秒）
                —— 生成结果会填进下面的「系统提示词」，请稍候不要关闭
              </p>
              <p v-if="authorError" class="form__hint form__hint--warn">
                ❌ 生成失败：{{ authorError }}
              </p>
              <p v-if="authorHint" class="form__hint form__hint--ok">
                ✅ {{ authorHint }}
                <template v-if="authoredBy">
                  · 由技能 <code>{{ authoredBy }}</code> 生成
                </template>
              </p>
              <p v-if="authorWarn" class="form__hint form__hint--warn">{{ authorWarn }}</p>
            </div>

            <label v-if="!isSkillAlias" ref="promptBox" class="form__field"
                   :class="{ 'form__field--flash': promptFlash }">
              <span class="form__label">
                第二步：系统提示词 System Prompt
                <span v-if="integratedFrom" class="form__count">基于 {{ integratedFrom }} 整合</span>
                <span class="form__count">{{ form.systemPrompt.trim().length }}/{{ PROMPT_MAX }}</span>
              </span>
              <textarea
                v-model="form.systemPrompt"
                class="form__textarea"
                rows="6"
                placeholder="你是资深论文审稿人，输出 JSON {score, comments}"
              />
              <span v-if="promptError" class="form__hint form__hint--warn">{{ promptError }}</span>
              <span class="form__hint">
                {{ PLACEHOLDER_HINT }}
                <template v-for="(p, i) in placeholderOptions" :key="p.name">
                  <code v-if="i" class="form__code-sep">、</code>
                  <code :title="p.desc">{{ p.token }}</code>
                </template>
              </span>
              <span v-if="unknownPlaceholders.length" class="form__hint form__hint--warn">
                ⚠️ 这里写了平台填不了的名字：
                <code v-for="t in unknownTokens" :key="t" class="form__hint-code">{{ t }}</code>
                —— 它们不会被替换、也不会报错，会原样留在发给模型的提示词里（模型看到一串花括号）。
                请换成上面的名字，或删掉那一段。
              </span>
            </label>

            <!-- 输出契约：高级模式只读（跟随技能），提示词模式才让用户选 -->
            <div v-if="form.mode === 'skill'" class="form__field">
              <span class="form__label">输出契约</span>
              <p class="form__readonly">
                <template v-if="pickedSkill">
                  {{ CONTRACT_LABELS[pickedSkill.contract] }}（<code>{{ pickedSkill.contract }}</code>）
                  — 由技能 <code>{{ pickedSkill.skill_id }}</code> 决定
                </template>
                <template v-else>选择技能后自动确定</template>
              </p>
              <p class="form__hint">
                它决定<strong>平台按什么格式解析它的输出</strong>（JSON 能落进结构化字段、
                代码块会写进项目 src/），不是要求模型输出什么 —— 技能已经声明好了，
                所以这里不让改，免得把技能声明顶掉。
              </p>
            </div>

            <div v-else class="form__field">
              <span class="form__label">输出内容形式</span>
              <select v-model="form.outputKind" class="form__input">
                <option v-for="o in outputKindOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
              <p class="form__hint">
                这是<strong>平台怎么处理这个 Agent 的产出</strong>（不是要求模型输出什么）。
                默认 <code>text</code>：只有当产出要被<strong>条件分支按字段读</strong>，
                或要被<strong>写进项目 <code>src/</code></strong> 时，才需要改成下面三种之一：
              </p>
              <ul class="form__contract">
                <li
                  v-for="o in CONTRACT_EFFECTS"
                  :key="o.value"
                  :class="{ 'form__contract-item--active': form.outputKind === o.value }"
                  class="form__contract-item"
                >
                  <span class="form__contract-name">{{ o.label }}</span>
                  <span class="form__contract-effect">{{ o.effect }}</span>
                </li>
              </ul>
              <p class="form__hint">
                选错的后果不是失败，而是<strong>产物形态不对</strong>：本该写进 <code>src/</code> 的代码
                会变成一个 <code>.md</code> 文件；本该能被条件分支读到的字段读不到（那一步会记
                <code>CONTRACT_SOFTFAIL</code>，步骤仍算成功）。
              </p>
            </div>

            <p v-if="error" class="form__error">{{ error }}</p>
          </div>

          <div class="form__foot">
            <button type="button" class="aw-btn aw-btn--default" @click="closeForm">取消</button>
            <button type="button" class="aw-btn aw-btn--primary" :disabled="!canSubmit || submitting" @click="submit">
              <Check :size="14" :stroke-width="2.2" />
              {{ submitting ? '保存中…' : editingId != null ? '保存' : '注册' }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  </main>
</template>

<style scoped>
.skills {
  max-width: 960px;
  margin: 0 auto;
  padding: 36px 28px 64px;
}
.skills__notice {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 0 0 12px;
  padding: 9px 11px;
  font-size: var(--fs-12);
  line-height: 1.6;
  color: var(--color-warning);
  background: var(--color-warning-soft);
  border: 1px solid color-mix(in srgb, currentColor 30%, transparent);
  border-radius: var(--radius-md);
}
.skills__notice-close {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  color: inherit;
  opacity: 0.7;
}
.skills__notice-close:hover {
  opacity: 1;
}
.skills__error {
  margin: 0 0 12px;
  padding: 9px 11px;
  font-size: var(--fs-12);
  line-height: 1.6;
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border: 1px solid color-mix(in srgb, currentColor 30%, transparent);
  border-radius: var(--radius-md);
}
.skills__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}
.skills__title {
  font-size: var(--fs-24);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.skills__sub {
  margin-top: 4px;
  font-size: var(--fs-13);
  color: var(--color-text-tertiary);
}
.skills__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}
.skills__search {
  display: flex;
  align-items: center;
  gap: 8px;
  width: min(300px, 100%);
  height: 34px;
  padding: 0 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
}
.skills__search:focus-within {
  border-color: rgba(86, 88, 212, 0.5);
}
.skills__search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: var(--fs-13);
}
.skills__count {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: var(--color-surface-hover);
  border-radius: 999px;
  padding: 0 6px;
  line-height: 15px;
}
.skills__error {
  margin-bottom: 14px;
  font-size: var(--fs-12);
  color: var(--color-danger);
}

.skills__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.skill-item {
  position: relative;
}
.skill__remove {
  position: absolute;
  top: 8px;
  right: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  color: var(--color-text-tertiary);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  opacity: 0;
  transition: opacity 0.13s var(--ease), color 0.13s var(--ease);
}
.skill-item:hover .skill__remove,
.skill__remove:focus-visible {
  opacity: 1;
}
.skill__remove:hover {
  color: var(--color-danger);
  border-color: color-mix(in srgb, var(--color-danger) 40%, transparent);
}
.skill__remove:disabled {
  opacity: 0.5;
}
.skill {
  display: flex;
  gap: 12px;
  padding: 14px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  text-align: left;
  transition: all 0.15s var(--ease);
  box-shadow: var(--shadow-xs);
}
.skill:hover {
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-sm);
}
.skill--active {
  border-color: rgba(86, 88, 212, 0.5);
  box-shadow: 0 0 0 3px var(--color-accent-ring);
}
.skill__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}
.skill__body {
  flex: 1;
  min-width: 0;
}
.skill__top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.skill__name {
  font-size: var(--fs-14);
  font-weight: 650;
}
.skill__mode {
  font-size: 10px;
  font-weight: 600;
  border-radius: 999px;
  padding: 1px 7px;
}
.skill__mode--prompt {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.skill__mode--skill {
  background: var(--color-warning-soft);
  color: var(--color-warning);
}
.skill__builtin {
  font-size: 10px;
  font-weight: 600;
  border-radius: 999px;
  padding: 1px 7px;
  background: var(--color-surface-hover);
  color: var(--color-text-tertiary);
}
.skill__role {
  display: block;
  font-size: var(--fs-12);
  color: var(--color-text-secondary);
  margin-top: 1px;
  font-family: ui-monospace, Consolas, monospace;
}
.skill__desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
  line-height: 1.5;
  margin-top: 6px;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(15, 16, 19, 0.4);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
}
.drawer,
.form {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  max-height: 88vh;
  overflow: hidden;
}
.drawer {
  width: min(520px, 100%);
  padding: 22px 24px;
  overflow-y: auto;
}
.form {
  width: min(600px, 100%);
}
.drawer__head,
.form__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.form__head {
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 0;
}
.drawer__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}
.drawer__titles {
  flex: 1;
  min-width: 0;
}
.drawer__name {
  display: block;
  font-size: var(--fs-17);
  font-weight: 700;
}
.drawer__role {
  display: block;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
  font-family: ui-monospace, Consolas, monospace;
}
.form__title {
  flex: 1;
  font-size: var(--fs-16);
  font-weight: 700;
}
.drawer__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
}
.drawer__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.drawer__section {
  margin-top: 18px;
}
.drawer__label,
.form__label {
  display: block;
  font-size: var(--fs-11);
  font-weight: 650;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
  margin-bottom: 7px;
}
.drawer__text {
  font-size: var(--fs-13);
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.drawer__text--mono {
  font-family: ui-monospace, Consolas, monospace;
}
.form__hint--busy {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-accent);
}
.aw-spin {
  width: 11px;
  height: 11px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: aw-spin 0.9s linear infinite;
}
@keyframes aw-spin {
  to {
    transform: rotate(360deg);
  }
}
.form__field--flash {
  animation: form-flash 1.6s ease-out;
}
@keyframes form-flash {
  0%,
  40% {
    background: rgba(86, 88, 212, 0.1);
  }
  100% {
    background: transparent;
  }
}
.form__contract {
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.form__contract-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 8px;
  border-left: 2px solid var(--color-border);
  background: var(--color-sidebar);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.form__contract-item--active {
  border-left-color: var(--color-accent);
  background: rgba(86, 88, 212, 0.08);
}
.form__contract-name {
  font-size: 11px;
  font-weight: 620;
  color: var(--color-text-secondary);
}
.form__contract-effect {
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
.form__readonly {
  margin: 0;
  padding: 8px 10px;
  font-size: var(--fs-12);
  color: var(--color-text-secondary);
  background: var(--color-sidebar);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
}
.form__readonly code {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
  color: var(--color-text);
}
.drawer__pre {
  font-family: ui-monospace, Consolas, monospace;
  font-size: var(--fs-12);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-sidebar);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  color: var(--color-text);
}
.drawer__foot {
  display: flex;
  gap: 8px;
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
}
.drawer__foot .aw-btn {
  flex: 1;
  justify-content: center;
}
.drawer__delete {
  color: var(--color-danger);
  border-color: var(--color-border);
}

.form__body {
  padding: 18px 20px;
  overflow-y: auto;
}
.form__field {
  display: block;
  margin-bottom: 16px;
}
.form__input,
.form__textarea {
  width: 100%;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  font-size: var(--fs-13);
  outline: none;
}
.form__input {
  height: 38px;
  padding: 0 12px;
}
.form__textarea {
  padding: 9px 12px;
  line-height: 1.6;
  resize: vertical;
  min-height: 96px;
  font-family: inherit;
}
.form__input:focus,
.form__textarea:focus {
  border-color: rgba(86, 88, 212, 0.5);
}
.form__segment {
  width: 100%;
}
.form__segment .aw-segment__btn {
  flex: 1;
}
.form__hint {
  margin-top: 7px;
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
  line-height: 1.5;
}
.form__hint--inline {
  margin-top: 0;
  flex: 1;
}
.form__hint--ok {
  color: var(--color-success);
}
.form__hint--warn {
  color: var(--color-warning);
}
.form__count {
  float: right;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  color: var(--color-text-faint);
}
.form__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}
.form__code-sep {
  background: none;
  padding: 0;
}
.form__error {
  font-size: var(--fs-12);
  color: var(--color-danger);
}
.form__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--color-border);
}

.overlay-fade-enter-active,
.overlay-fade-leave-active {
  transition: opacity 0.16s var(--ease);
}
.overlay-fade-enter-from,
.overlay-fade-leave-to {
  opacity: 0;
}

@media (max-width: 720px) {
  .skills {
    padding: 24px 14px 40px;
  }
}
</style>
