<script setup lang="ts">
import { computed, markRaw, onMounted, ref, watch } from 'vue'
import {
  VueFlow, type Connection, type EdgeChange, type EdgeMouseEvent, type Node, type NodeChange,
  type NodeMouseEvent
} from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import WorkflowNode from '@/components/workflow/WorkflowNode.vue'
import AgentIcon from '@/components/common/AgentIcon.vue'
import AgentHoverCard from '@/components/workspace/AgentHoverCard.vue'
import PromptViewer from '@/components/common/PromptViewer.vue'
import { agentBrief, splitPalette, toBriefInput } from '@/utils/agentBrief'
import PopoverMenu from '@/components/common/PopoverMenu.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useSkillStore } from '@/stores/skill'
import { useTaskStore } from '@/stores/task'
import { workflowApi } from '@/api/workflow'
import { plannerApi } from '@/api/planner'
import { lintWorkflow, issuesOfNode } from '@/utils/workflowLint'
import { canBeDecisionSource, decisionSourceReason, effectiveContract } from '@/utils/nodeContract'
import { impactWarning } from '@/utils/workflowImpact'
import type { ApiWorkflowUsage } from '@/types/api'
import type { RegistryAgent } from '@/types/skill'
import {
  AlertTriangle, Check, ChevronDown, GitBranch, GripVertical, Info, Play, RotateCcw, Save, ShieldCheck, Sparkles, Trash2, X
} from '@lucide/vue'

const store = useWorkflowStore()
const skillStore = useSkillStore()
const taskStore = useTaskStore()
const flowRef = ref<any>(null)
const saved = ref(false)
const error = ref('')

onMounted(async () => {
  await Promise.all([
    store.loadWorkflows(),
    skillStore.init(),
    skillStore.ensureSkillContracts(),   // 决策源判定要用技能声明的契约
    taskStore.loadProjects()
  ])
  const first = store.workflows[0]
  if (first) {
    store.openWorkflow(first.name)
    void loadGraphUsage()
    setTimeout(() => flowRef.value?.fitView({ padding: 0.25 }), 80)
  }
})

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const nodeTypes = { custom: markRaw(WorkflowNode) } as any

/* ---------------- 画布图检查（限制就地提示，不等提交后报错） ----------------
 * 规则都在 utils/workflowLint.ts；后端保存 / 执行 / 「校验」接口仍会再校验一遍，
 * 这里只是把"提交了才知道"的反馈提前到画的时候。 */
const knownRoles = computed(() => new Set(skillStore.agents.map((a) => a.roleKey)))

/**
 * 节点 → 有效输出契约（四层优先级，与后端一致：节点 > Agent > **技能声明** > text）。
 * 决策源必须是 json_object，否则运行时取不到字段、整步硬失败。
 * ⚠️ 技能那一层不能省：内置 Agent 行的 output_kind 都是 NULL，分类器的 json_object 来自技能声明。
 */
const contractSources = {
  agentKind: (role: string) => skillStore.agents.find((a) => a.roleKey === role)?.outputKind,
  agentSkill: (role: string) => skillStore.agents.find((a) => a.roleKey === role)?.skillId,
  skillKind: (skillId: string) => skillStore.skillContractOf(skillId)
}
const contractOf = (n: { role?: string; raw?: { output_kind?: string } }) =>
  effectiveContract(n, contractSources)

/** 画布上直接标出"哪些节点能当决策源"——不用让用户猜 */
const canBeSource = (n: { role?: string; raw?: { output_kind?: string } }) =>
  canBeDecisionSource(contractOf(n))

const lintIssues = computed(() =>
  lintWorkflow(store.nodes, store.edges, knownRoles.value, contractOf)
)
const lintErrors = computed(() => lintIssues.value.filter((i) => i.level === 'error'))
const lintOpen = ref(true)

const selected = computed(() => store.selectedNode)
const selectedEdge = computed(() => store.selectedEdge)

const lintFocus = (nodeId: string) => {
  if (!nodeId) return
  store.select(nodeId)
  try {
    flowRef.value?.fitView({ nodes: [{ id: nodeId }], padding: 1.5, duration: 200 })
  } catch {
    /* fitView 参数随 vue-flow 版本变化，定位失败不影响选中有反馈 */
  }
}

/** 会让后端/运行时必然失败的画布问题：拦住提交，把问题摆出来 */
const blockedByLint = (): boolean => {
  if (!lintErrors.value.length) return false
  lintOpen.value = true
  return true
}

const flowNodes = computed<Node[]>(() =>
  store.nodes.map((n) => {
    const own = issuesOfNode(lintIssues.value, n.id)
    return {
      id: n.id,
      type: 'custom',
      position: n.position,
      selected: store.selectedNodeId === n.id,
      data: {
        label: n.label,
        kind: n.kind,
        role: n.role,
        isStart: false,
        isEnd: false,
        // 画布上就地标出问题；「审批闸门」标在产出 PRD 的节点上（执行会停在这里等审批）
        isGate: n.role === 'pm',
        // 带执行条件的节点在画布上直接标出来（条件不满足时它会被跳过）
        condition: n.when ? `${String(n.when.ref).split('.').pop()} = ${n.when.eq}` : '',
        // 条件只进 tooltip（画布上不再挂徽标：用户反馈"画布标记太乱"）
        issue: own.some((i) => i.level === 'error') ? 'error' : own.length ? 'warn' : '',
        issueText: own.map((i) => i.message).join('\n')
      }
    }
  })
)

const onNodeClick = (e: NodeMouseEvent) => store.select(e.node.id)
const onDragStop = (e: { node: Node }) => store.moveNode(e.node.id, e.node.position)

const onConnect = (params: Connection) => {
  if (params.source && params.target) store.addEdge(params.source, params.target)
}

const onEdgeClick = (e: EdgeMouseEvent) => store.selectEdge(e.edge.id)

const onEdgesChange = (changes: EdgeChange[]) => {
  for (const c of changes) {
    if (c.type === 'remove') store.removeEdge(c.id)
  }
}

/**
 * 节点被删掉时同步回 store —— **这条以前是缺的**，后果实测是这样：
 *   · 按 Backspace 删节点，画布上它消失了，但 store 里还在 → 保存时节点又被打包进工作流；
 *   · 只要之后有任何一次重渲染（点一下别处、选中别的节点），flowNodes 重算 → 节点**又冒出来**，
 *     看起来就是"节点删不掉"。
 * VueFlow 的 `:nodes` 是单向绑定（父组件是唯一数据源），它内部删完只会 emit `nodesChange`，
 * 等父组件把新数组写回去。所以必须在这里接着。
 */
const onNodesChange = (changes: NodeChange[]) => {
  for (const c of changes) {
    if (c.type === 'remove') store.removeNode(c.id)
  }
}

/** 图的轻量签名：节点数 + 连线数 + 各节点步骤名。变了就是"用户动过这张图" */
const graphSignature = computed(
  () =>
    `${store.nodes.length}#${store.edges.length}#` +
    store.nodes.map((n) => `${n.label}${n.when ? `?${n.when.ref}=${n.when.eq}` : ''}`).join('|')
)
watch(graphSignature, () => noteGraphEdited())


/* ---------------- 执行条件（when）编辑 ----------------
 * 条件本身后端早支持（校验/闭集推导/调度都在跑），缺的是画布入口。
 * 这里做三件事：选决策源、填字段与取值、把"决策源必须是 json_object 且必须是上游"讲清楚。 */
const CONDITION_OFF = ''
const conditionSource = ref('')
const conditionField = ref('verdict')
const conditionValue = ref('')

/** 可当决策源的上游节点（只列上游：非上游的话取值必然为空、节点会被永远跳过） */
const conditionSources = computed(() => {
  const sel = selected.value
  if (!sel) return []
  const ups = new Set<string>()
  const stack = store.edges.filter((e) => e.target === sel.id).map((e) => e.source)
  while (stack.length) {
    const cur = stack.pop() as string
    if (ups.has(cur)) continue
    ups.add(cur)
    stack.push(...store.edges.filter((e) => e.target === cur).map((e) => e.source))
  }
  return store.nodes
    .filter((n) => ups.has(n.id))
    .map((n) => {
      const kind = contractOf(n)
      const ok = canBeSource(n)
      return {
        id: n.id,
        label: n.label,
        ok,
        // 选项里直接写清楚"能不能用、为什么"，用户就不用靠猜
        text: ok
          ? `${n.label}　✅ 可作决策源（输出 JSON 对象）`
          : `${n.label}　⚠️ 暂不可用：${decisionSourceReason(kind)}`
      }
    })
    // 能用的排前面，省得在一堆不可用里挑
    .sort((a, b) => Number(b.ok) - Number(a.ok))
})

/** 当前选中的决策源能不能用（用不了时面板里给一条明确指引） */
const pickedSourceKind = computed(() => {
  const src = store.nodes.find((n) => n.id === conditionSource.value)
  return src ? contractOf(src) : ''
})

/** 同一个决策源 + 字段上已经用过的取值（闭集就是这些，避免写错大小写/拼写） */
const conditionValues = computed(() =>
  store.nodes
    .filter((n) => n.when && n.when.ref === `${conditionSource.value}.parsed.${conditionField.value}`)
    .map((n) => String(n.when?.eq ?? ''))
    .filter(Boolean)
)

/** 选中节点变化时把条件编辑区同步成它的现状 */
watch(
  () => selected.value?.id,
  () => {
    const when = selected.value?.when
    const m = when ? /^(.*)\.parsed\.([\w-]+)$/.exec(when.ref) : null
    conditionSource.value = m ? m[1] : CONDITION_OFF
    conditionField.value = m ? m[2] : 'verdict'
    conditionValue.value = when ? String(when.eq ?? '') : ''
  },
  { immediate: true }
)

const applyCondition = () => {
  const sel = selected.value
  if (!sel) return
  if (!conditionSource.value) {
    store.setNodeWhen(sel.id, null)
    return
  }
  store.setNodeWhen(sel.id, {
    ref: `${conditionSource.value}.parsed.${conditionField.value.trim() || 'verdict'}`,
    eq: conditionValue.value
  })
}

/** 点一条问题 → 选中对应节点并把它拉进视野（图级问题只展开列表） */


const edgeEndpoints = computed(() => {
  const e = store.selectedEdge
  if (!e) return { source: '', target: '' }
  const labelOf = (id: string) => store.nodes.find((n) => n.id === id)?.label ?? id
  return { source: labelOf(e.source), target: labelOf(e.target) }
})

const removeSelectedEdge = () => {
  if (store.selectedEdge) store.removeEdge(store.selectedEdge.id)
}

const workflowItems = computed(() =>
  store.workflows.map((w) => ({
    key: w.name,
    label: w.name,
    desc: w.description || (w.builtin ? '内置工作流' : '自定义工作流'),
    active: store.activeWorkflowName === w.name
  }))
)

const openWorkflow = (name: string) => {
  store.openWorkflow(name)
  void loadGraphUsage()
  setTimeout(() => flowRef.value?.fitView({ padding: 0.25 }), 60)
}

/* ---------------- 左栏：分组 + 悬停详情 ----------------
 * 分组只看 API 的 builtin 字段（不用名字猜）→ 用户新加的 Agent 一定进「我的 Agent」；
 * 悬停/聚焦时弹出放大的详情卡，讲清"这个节点是干嘛的、需要什么输入、产出什么"。 */
const paletteGroups = computed(() => {
  const g = splitPalette(skillStore.agents)
  return [
    { key: 'builtin', label: '内置角色', items: g.builtin },
    { key: 'custom', label: '我的 Agent', items: g.custom }
  ]
})

const briefAgent = ref<RegistryAgent | null>(null)
const briefStyle = ref<Record<string, string>>({})
const dragging = ref(false)

const briefOf = (a: RegistryAgent) =>
  agentBrief(toBriefInput(a), skillStore.skills.find((sk) => sk.skill_id === a.skillId) ?? null)

const showBrief = (a: RegistryAgent, e: Event) => {
  const el = (e.currentTarget ?? e.target) as HTMLElement | null
  const box = el?.getBoundingClientRect()
  // 贴在这一项的右侧；下方空间不够就往上挪，避免超出视口
  const top = Math.min(box?.top ?? 120, Math.max(60, window.innerHeight - 340))
  briefStyle.value = { top: `${Math.max(8, top)}px`, left: `${(box?.right ?? 220) + 10}px` }
  briefAgent.value = a
}

const hideBrief = () => {
  briefAgent.value = null
}

/** 「查看完整内容」→ 复用已有的 PromptViewer 弹层（与 Agent 页一致） */
const briefFull = ref<{ name: string; text: string } | null>(null)
const openBriefFull = (a: RegistryAgent) => {
  briefFull.value = { name: a.name, text: briefOf(a).fullText }
}

const onDragStart = (e: DragEvent, agent: RegistryAgent) => {
  hideBrief()                       // 拖拽时收起卡片，别挡操作
  dragging.value = true
  e.dataTransfer?.setData('application/agent-skill', agent.name)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
  // 拖拽结束的复位挂在元素自己的 @dragend 上（不在 setup 顶层加全局监听：
  // 那样 SSR 没有 document 会直接抛错，而且监听器不会清理 —— 渲染测试当场抓到过）
}

const onDrop = (e: DragEvent) => {
  const name = e.dataTransfer?.getData('application/agent-skill')
  if (!name) return
  const agent = skillStore.agents.find((a) => a.name === name)
  const pos = flowRef.value?.screenToFlowCoordinate?.({ x: e.clientX, y: e.clientY }) ?? { x: 240, y: 300 }
  const node = store.addNode(name, pos, agent?.roleKey ?? '')
  store.select(node.id)
}

/* 保存成功但"不能当项目模版"时的提示（后端回传 usable_as_project/hint）。
 * 以前这种情况完全没提示 —— 用户存完去首页找不到自己那张图，只会以为保存失败了。 */
const templateHint = ref('')

/* ---------------- 「改这张图会影响谁」 ----------------
 * 项目建好时就把图记在项目上（projects.workflow_id），审批与迭代都沿用它。
 * 所以在画布上动节点之前就该知道：保存后这些项目以后跑的就是新图；删掉这张图，
 * 它们再跑会直接失败。这里查一次影响面，改动时给一次提示，保存/删除时再确认一次。 */
const graphUsage = ref<ApiWorkflowUsage | null>(null)
/** 用户已经知道"这张图有项目在用"了（本次会话内只提示一次，别每拖一个节点都弹） */
const impactAcknowledged = ref(false)
const graphImpact = computed(() =>
  graphUsage.value && graphUsage.value.project_count > 0 ? graphUsage.value : null
)

const loadGraphUsage = async () => {
  const id = store.activeWorkflow?.id
  graphUsage.value = null
  impactAcknowledged.value = false
  if (id == null) return
  graphUsage.value = await workflowApi.usage(id).catch(() => null)
}

/** 图被改了一下（增删节点/连线/改名）——第一次改时提示"这会影响到谁" */
const noteGraphEdited = () => {
  if (!graphImpact.value || impactAcknowledged.value) return
  impactAcknowledged.value = true
}

/** 保存/删除前的二次确认；用户点"取消"就中止 */
const confirmImpact = (action: 'edit' | 'delete'): boolean => {
  const usage = graphImpact.value
  if (!usage) return true
  if (action === 'edit' && !impactAcknowledged.value) impactAcknowledged.value = true
  return window.confirm(impactWarning(action, usage).message)
}

const saveAsWorkflow = async () => {
  error.value = ''
  // 空图后端必拒（workflow 必须是含 nodes 列表的对象）——这里先说，不用白跑一次请求。
  // 其余的图级问题（缺 PRD、孤立节点）只是提示，不拦保存：草稿可以先存。
  if (!store.nodes.length) {
    lintOpen.value = true
    error.value = '画布是空的：先从左边拖一个技能 / Agent 进来再保存。'
    return
  }
  const name = window.prompt('工作流名称', `我的编排 ${store.workflows.length + 1}`)
  if (!name) return
  try {
    const created = await store.saveAsWorkflow(name.trim(), '前端编排保存')
    templateHint.value = created.usable_as_project === false ? created.hint || '' : ''
    saved.value = true
    setTimeout(() => (saved.value = false), 1600)
  } catch (e) {
    error.value = (e as Error).message
  }
}

const removeSelected = () => {
  if (selected.value) store.removeNode(selected.value.id)
}

/* ---- 把改动存回「已打开的自定义工作流」（PUT /api/workflows/{id}，§5.3）----
 * 内置模板的 id 是 null，天然改不了，只能「另存为新工作流」。 */
const savingUpdate = ref(false)
const savedUpdate = ref(false)
const canUpdate = computed(() => store.activeWorkflow?.id != null)

const saveCurrent = async () => {
  error.value = ''
  if (!store.nodes.length) {
    lintOpen.value = true
    error.value = '画布是空的：先从左边拖一个技能 / Agent 进来再保存。'
    return
  }
  if (!confirmImpact('edit')) return      // 有项目在用这张图 → 让用户确认后果
  savingUpdate.value = true
  try {
    const updated = await store.saveWorkflow()
    templateHint.value = updated.usable_as_project === false ? updated.hint || '' : ''
    savedUpdate.value = true
    setTimeout(() => (savedUpdate.value = false), 1600)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    savingUpdate.value = false
  }
}

/* ---------------- 运行 / 校验 / 自然语言拆解 ---------------- */
const projects = computed(() => taskStore.list)
const runOpen = ref(false)
const runProjectId = ref<number | null>(null)
const runRequirement = ref('')
const runBusy = ref(false)
const runError = ref('')
const runMessage = ref('')
const validating = ref(false)
const validateResult = ref('')

const openRun = async () => {
  runError.value = ''
  runMessage.value = ''
  validateResult.value = ''
  // 会必然失败的问题先在画布上指出（后端 /execute 也会拒，只是不想让你白等一次往返）
  if (blockedByLint()) {
    runError.value = '画布上还有会导致运行失败的问题，先按上方「图检查」里的提示修掉。'
  }
  await taskStore.loadProjects()
  if (runProjectId.value == null && projects.value.length) {
    runProjectId.value = Number(projects.value[0].id)
  }
  runOpen.value = true
}

const doValidate = async () => {
  runError.value = ''
  validateResult.value = ''
  const nodes = store.currentApiNodes()
  if (!nodes.length) {
    runError.value = '画布上没有节点，先拖入技能卡片。'
    return
  }
  validating.value = true
  try {
    const res = await workflowApi.validate({ name: store.activeWorkflowName || 'draft', nodes })
    validateResult.value = res.valid ? `校验通过：${nodes.length} 个节点` : '校验未通过'
  } catch (e) {
    runError.value = (e as Error).message
  } finally {
    validating.value = false
  }
}

/**
 * 同一项目再提一条链的风险提示（与详情页的审批抑制同一个问题）：
 * 后端不会阻止并行执行，而开发链第一步就是清空代码目录 src/ ——
 * 两条链同时跑会互相覆盖。这里只在提交前确认一次，不硬拦。
 */
const confirmIfProjectBusy = (projectId: number | null): boolean => {
  if (projectId == null) return true
  const p = taskStore.list.find((t) => Number(t.id) === projectId)
  if (!p || (p.status !== 'running' && p.status !== 'awaiting_approval')) return true
  return window.confirm(
    '该项目当前正在执行中。\n\n再提交一条链会清空它的代码目录（src/），并与正在跑的链互相覆盖。\n确定继续？'
  )
}

const doExecute = async () => {
  runError.value = ''
  runMessage.value = ''
  const nodes = store.currentApiNodes()
  if (!nodes.length) {
    runError.value = '画布上没有节点，先拖入技能卡片。'
    return
  }
  if (blockedByLint()) {
    runError.value = '画布上还有会导致运行失败的问题，先按上方「图检查」里的提示修掉。'
    return
  }
  if (runProjectId.value == null) {
    runError.value = '请选择要执行的项目。'
    return
  }
  if (!confirmIfProjectBusy(runProjectId.value)) return
  runBusy.value = true
  try {
    const res = (await workflowApi.execute({
      project_id: runProjectId.value,
      seeds: { user_requirement: runRequirement.value },
      nodes
    })) as { message?: string }
    runMessage.value = res?.message || '工作流已提交异步执行'
  } catch (e) {
    runError.value = (e as Error).message
  } finally {
    runBusy.value = false
  }
}

const doPlanner = async () => {
  runError.value = ''
  runMessage.value = ''
  if (runProjectId.value == null) {
    runError.value = '请选择要执行的项目。'
    return
  }
  if (!runRequirement.value.trim()) {
    runError.value = '自然语言拆解需要填写需求描述。'
    return
  }
  if (!confirmIfProjectBusy(runProjectId.value)) return
  runBusy.value = true
  try {
    const res = await plannerApi.plan(runProjectId.value, runRequirement.value.trim())
    runMessage.value = `拆解完成：${res.steps_count} 步（${res.est_complexity || '复杂度未知'}），已提交执行`
  } catch (e) {
    runError.value = (e as Error).message
  } finally {
    runBusy.value = false
  }
}

const removeActive = async () => {
  const wf = store.activeWorkflow
  if (!wf) return
  if (wf.builtin) {
    error.value = '内置工作流不允许删除。'
    return
  }
  if (wf.id == null) {
    error.value = '后端未返回工作流 id，暂不可删除（待后端修复：WorkflowResponse 缺 id）。'
    return
  }
  // 删除前也要说清后果：用它的项目以后再跑会直接失败（平台不会悄悄换回默认模版）
  if (!window.confirm(`确定删除工作流「${wf.name}」？`)) return
  if (!confirmImpact('delete')) return
  try {
    await store.removeWorkflow(wf.id)
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
  }
}
</script>

<template>
  <div class="wf">
    <div class="wf__top">
      <div class="wf__title-block">
        <GitBranch :size="16" :stroke-width="2" class="wf__branch" />
        <div>
          <h1 class="wf__title">工作流编排</h1>
          <p class="wf__sub">拖入技能卡片添加节点，拖拽节点端口连线定义依赖</p>
        </div>
      </div>

      <PopoverMenu :items="workflowItems" align="left" :width="240" :icon="ChevronDown" :label="store.activeWorkflowName || '选择工作流'" @select="openWorkflow" />

      <div class="wf__hint"><Info :size="13" :stroke-width="2" />拖拽技能入画布添加节点，拖拽节点端口连线</div>

      <button type="button" class="aw-btn aw-btn--default aw-btn--sm" @click="store.loadWorkflows()">
        <RotateCcw :size="13" :stroke-width="2" />
        刷新
      </button>
      <button type="button" class="aw-btn aw-btn--primary aw-btn--sm" @click="saveAsWorkflow">
        <Check v-if="saved" :size="13" :stroke-width="2.4" />
        <Save v-else :size="13" :stroke-width="2" />
        {{ saved ? '已保存' : canUpdate ? '另存为新工作流' : '保存为工作流' }}
      </button>
      <button
        v-if="canUpdate"
        type="button"
        class="aw-btn aw-btn--default aw-btn--sm"
        :disabled="savingUpdate"
        title="把当前画布存回已打开的自定义工作流"
        @click="saveCurrent"
      >
        <Check v-if="savedUpdate" :size="13" :stroke-width="2.4" />
        <Save v-else :size="13" :stroke-width="2" />
        {{ savedUpdate ? '已更新' : savingUpdate ? '保存中…' : '保存修改' }}
      </button>
      <button type="button" class="aw-btn aw-btn--default aw-btn--sm" @click="openRun">
        <Play :size="13" :stroke-width="2" />
        运行
      </button>
      <button
        v-if="store.activeWorkflow && !store.activeWorkflow.builtin"
        type="button"
        class="aw-btn aw-btn--default aw-btn--sm"
        @click="removeActive"
      >
        <Trash2 :size="13" :stroke-width="2" />
        删除工作流
      </button>
    </div>

    <p v-if="error || store.error" class="wf__error">{{ error || store.error }}</p>
    <p v-if="templateHint" class="wf__notice">
      <Info :size="13" :stroke-width="2" />
      <span>{{ templateHint }}</span>
      <button type="button" class="wf__notice-close" title="知道了" @click="templateHint = ''">
        <X :size="12" :stroke-width="2" />
      </button>
    </p>

    <!-- 有项目在用这张图时，常驻一条提示：动节点之前就该知道会影响谁 -->
    <p v-if="graphImpact" class="wf__notice wf__notice--impact">
      <AlertTriangle :size="13" :stroke-width="2" />
      <span>
        这张图「{{ graphImpact.name }}」已被
        <strong>{{ graphImpact.project_count }} 个项目</strong>引用（{{ graphImpact.projects.map((p) => p.title).join('、') }}）：
        在这里增删节点、保存后，这些项目<strong>以后再执行就是按新图跑</strong>；
        删掉这张图，它们再跑会直接失败。
      </span>
      <button
        type="button"
        class="wf__notice-close"
        title="知道了"
        @click="graphUsage = null"
      >
        <X :size="12" :stroke-width="2" />
      </button>
    </p>

    <!-- 图检查：把"提交后才知道"的限制提前摆到画布上（后端保存/执行时仍会再校验一遍） -->
    <div
      class="wf__lint"
      :class="lintErrors.length ? 'wf__lint--error' : lintIssues.length ? 'wf__lint--warn' : 'wf__lint--ok'"
    >
      <button type="button" class="wf__lint-head" @click="lintOpen = !lintOpen">
        <AlertTriangle v-if="lintIssues.length" :size="14" :stroke-width="2" />
        <ShieldCheck v-else :size="14" :stroke-width="2" />
        <span v-if="lintErrors.length">图检查：{{ lintErrors.length }} 处会导致运行失败</span>
        <span v-else-if="lintIssues.length">图检查：能跑，但有 {{ lintIssues.length }} 条提示</span>
        <span v-else>图检查通过：可以保存 / 运行</span>
        <ChevronDown
          v-if="lintIssues.length"
          :size="13"
          :stroke-width="2"
          class="wf__lint-caret"
          :class="{ 'wf__lint-caret--up': !lintOpen }"
        />
      </button>
      <ul v-if="lintIssues.length && lintOpen" class="wf__lint-list">
        <li v-for="(i, idx) in lintIssues" :key="idx" class="wf__lint-item" :class="`wf__lint-item--${i.level}`">
          <span class="wf__lint-dot" />
          <span class="wf__lint-text">{{ i.message }}</span>
          <button v-if="i.nodeId" type="button" class="wf__lint-jump" @click="lintFocus(i.nodeId)">定位</button>
        </li>
      </ul>
    </div>

    <div class="wf__body" :class="{ 'wf__body--panel': selected || selectedEdge }">
      <aside class="wf__palette">
        <div class="wf__palette-head">
          <span>Agent 节点</span>
          <span class="wf__palette-count">{{ skillStore.agents.length }}</span>
        </div>
        <p class="wf__palette-hint">拖入画布添加节点；<strong>鼠标停住可看它干什么</strong></p>

        <div class="wf__palette-scroll">
          <!-- 按 API 的 builtin 字段分组：内置角色 / 我的 Agent（新加的 Agent 自动进下面这组） -->
          <template v-for="g in paletteGroups" :key="g.key">
            <div class="wf__palette-group">
              <span>{{ g.label }}</span>
              <span class="wf__palette-count">{{ g.items.length }}</span>
            </div>
            <div class="wf__palette-list">
              <div
                v-for="a in g.items"
                :key="a.id"
                class="wf-skill"
                draggable="true"
                tabindex="0"
                @dragstart="onDragStart($event, a)"
                @dragend="dragging = false"
                @mouseenter="showBrief(a, $event)"
                @mouseleave="hideBrief()"
                @focus="showBrief(a, $event)"
                @blur="hideBrief()"
              >
                <GripVertical :size="13" :stroke-width="2" class="wf-skill__grip" />
                <span class="wf-skill__icon"><AgentIcon icon="sparkles" :size="14" :stroke-width="2" /></span>
                <span class="wf-skill__body">
                  <span class="wf-skill__name">{{ a.name }}</span>
                  <span class="wf-skill__role">{{ a.roleKey }}</span>
                </span>
              </div>
            </div>
          </template>
          <p v-if="!paletteGroups[1].items.length" class="wf__palette-empty">
            还没有自定义 Agent —— 去 <router-link to="/agents">Agent 页</router-link> 新建，建完这里就会出现。
          </p>
        </div>

        <!-- 悬停放大的详情卡（fixed 定位，不占列表空间；pointer-events:none 不挡拖拽） -->
        <div v-if="briefAgent && !dragging" class="wf-brief" :style="briefStyle">
          <AgentHoverCard :brief="briefOf(briefAgent)" @open-full="openBriefFull(briefAgent)" />
        </div>
      </aside>

      <div class="wf__canvas" @dragover.prevent @drop="onDrop">
        <VueFlow
          ref="flowRef"
          :nodes="flowNodes"
          :edges="store.edges"
          :node-types="nodeTypes"
          :fit-view-on-init="true"
          :min-zoom="0.3"
          :max-zoom="1.8"
          :nodes-draggable="true"
          :nodes-connectable="true"
          :edges-selectable="true"
          :delete-key-code="['Backspace', 'Delete']"
          @connect="onConnect"
          @edges-change="onEdgesChange"
          @nodes-change="onNodesChange"
          @node-click="onNodeClick"
          @node-drag-stop="onDragStop"
          @edge-click="onEdgeClick"
          @pane-click="store.clearSelection()"
        />
      </div>

      <aside class="wf__panel">
        <transition name="panel">
          <div v-if="selected" key="panel" class="wf__panel-inner">
            <div class="cfg">
              <div class="cfg__head">
                <h3 class="cfg__title">节点</h3>
              </div>
              <label class="cfg__field">
                <span class="cfg__field-label">步骤名</span>
                <input
                  class="cfg__input"
                  type="text"
                  :value="selected.label"
                  maxlength="40"
                  placeholder="例如：按审批意见重出 PRD"
                  @input="store.renameNode(selected.id, ($event.target as HTMLInputElement).value)"
                />
              </label>
              <p v-if="selected.role" class="cfg__role">{{ selected.role }}</p>
              <p v-if="selected.when" class="cfg__cond">
                执行条件：<code>{{ selected.when.ref }}</code> = <code>{{ selected.when.eq }}</code>
                —— 取值不相等时这一步会被跳过
              </p>
              <p class="cfg__note">
                节点将作为工作流步骤提交给后端（agent.role_key = 角色标识）。
                步骤名会出现在运行日志、产物清单和步骤卡片里 —— 同一个 Agent 在图上出现多次时，
                改成能区分的名字（如「生成 PRD」/「按审批意见重出 PRD」）。
              </p>
              <div class="cfg__section">
                <p class="cfg__section-title">执行条件（可选）</p>
                <label class="cfg__field">
                  <span class="cfg__field-label">决策源节点</span>
                  <select
                    class="cfg__input"
                    :value="conditionSource"
                    @change="conditionSource = ($event.target as HTMLSelectElement).value; applyCondition()"
                  >
                    <option :value="CONDITION_OFF">无条件：依赖就绪就执行</option>
                    <option v-for="s in conditionSources" :key="s.id" :value="s.id">
                      {{ s.text }}
                    </option>
                  </select>
                </label>
                <p
                  v-if="conditionSource && !canBeDecisionSource(pickedSourceKind)"
                  class="cfg__warn"
                >
                  这个决策源的输出不是 JSON 对象（当前 {{ pickedSourceKind }}），取不到字段 ——
                  运行到这一步会硬失败。去 <strong>Agent 页面</strong>把它（或它绑定的技能）的
                  「输出内容形式」改成 <code>json_object</code>，再回来选它。
                </p>

                <template v-if="conditionSource">
                  <label class="cfg__field">
                    <span class="cfg__field-label">字段名（决策源 JSON 输出里的字段）</span>
                    <input
                      class="cfg__input"
                      type="text"
                      :value="conditionField"
                      placeholder="verdict"
                      @change="conditionField = ($event.target as HTMLInputElement).value; applyCondition()"
                    />
                  </label>
                  <label class="cfg__field">
                    <span class="cfg__field-label">取值（相等才执行）</span>
                    <input
                      class="cfg__input"
                      type="text"
                      :value="conditionValue"
                      list="wf-condition-values"
                      placeholder="needs_revision"
                      @change="conditionValue = ($event.target as HTMLInputElement).value; applyCondition()"
                    />
                    <datalist id="wf-condition-values">
                      <option v-for="v in conditionValues" :key="v" :value="v" />
                    </datalist>
                  </label>
                  <p class="cfg__tip">
                    取值不等于这里写的值时，这个节点会被<strong>跳过</strong>（不建步骤行、不实例化 Agent）。
                    决策源必须能产出 <code>json_object</code>，否则运行时取不到字段会直接失败 ——
                    图检查会替你盯着。字段名/取值大小写敏感。
                  </p>
                </template>
                <p v-else class="cfg__tip">
                  不设条件 = 现在的顺序执行（上游成功就跑）。需要"满足条件才跑"时才在下面选一个决策源。
                </p>
                <details class="cfg__help">
                  <summary>条件分支怎么用？（点开看规则）</summary>
                  <ul class="cfg__help-list">
                    <li>
                      <strong>连线 ≠ 分支。</strong>一个节点拉两条线、下游都不带条件 →
                      两个下游<strong>都会跑</strong>（并行扇出）；要"只跑一条"，就得给每个下游各写一条
                      <strong>互斥的条件</strong>。
                    </li>
                    <li>
                      <strong>决策源不限于分类器。</strong>任何<strong>输出 JSON 对象</strong>、且在它上游的节点都能当
                      （图上带 <code>{ }</code> 徽标的都可以）。内置分类器只是现成的例子。
                    </li>
                    <li>
                      <strong>取值大小写敏感</strong>，必须与决策源实际输出的字面量一字不差；
                      条件不成立的节点会被<strong>跳过</strong>（不建步骤行、不实例化 Agent）。
                    </li>
                    <li>
                      <strong>分支跑完会汇合</strong>：下游节点等两条分支都结束（成功或跳过）再执行。
                    </li>
                  </ul>
                </details>
              </div>

              <div class="cfg__foot">
                <button type="button" class="cfg__delete" @click="removeSelected">
                  <Trash2 :size="14" :stroke-width="2" />
                  删除节点
                </button>
                <span class="cfg__tip">也可以选中后按 Backspace / Delete，或点节点卡片右上角的 ×</span>
              </div>
            </div>
          </div>

          <div v-else-if="selectedEdge" key="edge" class="wf__panel-inner">
            <div class="cfg">
              <div class="cfg__head">
                <h3 class="cfg__title">连线</h3>
              </div>
              <p class="cfg__name">依赖关系</p>
              <p class="cfg__role">{{ edgeEndpoints.source }} → {{ edgeEndpoints.target }}</p>
              <p class="cfg__note">
                连线表示执行依赖：上游节点成功后，下游节点才会执行。点击线选中，按 Backspace / Delete 删除。
              </p>
              <div class="cfg__foot">
                <button type="button" class="cfg__delete" @click="removeSelectedEdge">
                  <Trash2 :size="14" :stroke-width="2" />
                  删除连线
                </button>
              </div>
            </div>
          </div>
        </transition>
      </aside>
    </div>

    <!-- 运行 / 拆解 -->
    <!-- 悬停卡里「查看完整内容」→ 复用 Agent 页那套长文本查看器 -->
    <div v-if="briefFull" class="run-overlay" @click.self="briefFull = null">
      <div class="run-dialog" @click.stop>
        <div class="run-head">
          <h3 class="run-title">{{ briefFull.name }}</h3>
          <button type="button" class="run-close" @click="briefFull = null">
            <X :size="16" :stroke-width="2" />
          </button>
        </div>
        <div class="run-body">
          <PromptViewer :text="briefFull.text" :max-height="420" />
        </div>
      </div>
    </div>

    <div v-if="runOpen" class="run-overlay" @click.self="runOpen = false">
      <div class="run-panel">
        <div class="run-head">
          <span class="run-title">运行工作流</span>
          <button type="button" class="run-close" @click="runOpen = false">
            <X :size="16" :stroke-width="2" />
          </button>
        </div>

        <div class="run-body">
          <label class="run-field">
            <span class="run-label">目标项目</span>
            <select v-model.number="runProjectId" class="run-select">
              <option :value="null" disabled>请选择项目</option>
              <option v-for="p in projects" :key="p.id" :value="Number(p.id)">{{ p.title }}</option>
            </select>
          </label>
          <p v-if="!projects.length" class="run-hint">还没有项目，请先在首页新建一个任务。</p>

          <label class="run-field">
            <span class="run-label">需求描述（作为 user_requirement 种子）</span>
            <textarea
              v-model="runRequirement"
              class="run-textarea"
              rows="4"
              placeholder="例如：帮我开发一个用户管理系统…"
            />
          </label>

          <p v-if="runError" class="run-error">{{ runError }}</p>
          <p v-else-if="lintErrors.length" class="run-error">
            画布上还有 {{ lintErrors.length }} 处会导致运行失败的问题，先按画布上方「图检查」里的提示修掉
            —— 后端的执行接口同样会拒绝。
          </p>
          <p v-else-if="runMessage" class="run-success">{{ runMessage }}</p>
          <p v-if="validateResult" class="run-hint">{{ validateResult }}</p>

          <div class="run-actions">
            <button type="button" class="aw-btn aw-btn--default aw-btn--sm" :disabled="validating" @click="doValidate">
              <ShieldCheck :size="13" :stroke-width="2" />
              {{ validating ? '校验中…' : '校验画布' }}
            </button>
            <button
              type="button"
              class="aw-btn aw-btn--default aw-btn--sm"
              :disabled="runBusy || !runRequirement.trim()"
              @click="doPlanner"
            >
              <Sparkles :size="13" :stroke-width="2" />
              AI 拆解并执行
            </button>
            <button
              type="button"
              class="aw-btn aw-btn--primary aw-btn--sm"
              :disabled="runBusy || lintErrors.length > 0"
              :title="lintErrors.length ? '画布上还有会导致运行失败的问题（后端执行接口同样会拒绝）' : ''"
              @click="doExecute"
            >
              <Play :size="13" :stroke-width="2" />
              {{ runBusy ? '提交中…' : '执行当前工作流' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wf {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.wf__top {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.wf__title-block {
  display: flex;
  align-items: center;
  gap: 12px;
}
.wf__branch {
  color: var(--color-accent);
}
.wf__title {
  font-size: var(--fs-17);
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
}
.wf__sub {
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.wf__hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
}
.wf__notice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 8px 16px 0;
  padding: 8px 10px;
  font-size: var(--fs-12);
  line-height: 1.6;
  color: #8a5a00;
  background: rgba(176, 125, 29, 0.1);
  border: 1px solid rgba(176, 125, 29, 0.35);
  border-radius: var(--radius-md);
}
.wf__notice--impact {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-color: color-mix(in srgb, currentColor 30%, transparent);
}
.wf__notice-close {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  color: inherit;
  opacity: 0.7;
}
.wf__notice-close:hover {
  opacity: 1;
}
.wf__error {
  padding: 8px 24px;
  font-size: var(--fs-12);
  color: var(--color-danger);
}

/* ---- 图检查条：限制在画布上就地提示，不等提交 ---- */
.wf__lint {
  margin: 8px 16px 0;
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  font-size: var(--fs-12);
}
.wf__lint--ok {
  color: var(--color-success);
  background: var(--color-success-soft);
  border-color: var(--color-success-ring);
}
.wf__lint--warn {
  color: var(--color-warning);
  background: var(--color-warning-soft);
  border-color: color-mix(in srgb, currentColor 30%, transparent);
}
.wf__lint--error {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-color: color-mix(in srgb, currentColor 30%, transparent);
}
.wf__lint-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 8px 11px;
  font-size: var(--fs-12);
  font-weight: 600;
  color: inherit;
  text-align: left;
}
.wf__lint-caret {
  margin-left: auto;
  transition: transform 0.16s var(--ease);
}
.wf__lint-caret--up {
  transform: rotate(180deg);
}
.wf__lint-list {
  margin: 0;
  padding: 0 11px 9px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.wf__lint-item {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.wf__lint-dot {
  width: 5px;
  height: 5px;
  margin-top: 7px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.wf__lint-item--error .wf__lint-dot {
  color: var(--color-danger);
}
.wf__lint-item--warn .wf__lint-dot {
  color: var(--color-warning);
}
.wf__lint-text {
  min-width: 0;
}
.wf__lint-jump {
  margin-left: auto;
  flex-shrink: 0;
  font-size: var(--fs-12);
  font-weight: 600;
  color: var(--color-accent);
}

.wf__body {
  flex: 1;
  min-height: 0;
  display: flex;
}
.wf__palette {
  width: 208px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  background: var(--color-sidebar);
  padding: 14px 12px;
  overflow-y: auto;
}
.wf__palette-head {
  display: flex;
  align-items: center;
  font-size: var(--fs-12);
  font-weight: 650;
  color: var(--color-text-secondary);
}
.wf__palette-count {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
  background: var(--color-surface-hover);
  border-radius: 999px;
  padding: 0 6px;
  line-height: 15px;
}
.wf__palette-hint {
  margin: 4px 0 10px;
  font-size: 10.5px;
  color: var(--color-text-faint);
}
.wf__palette-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.wf__palette-group {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 10px 2px 4px;
  font-size: 10.5px;
  font-weight: 650;
  color: var(--color-text-tertiary);
}
.wf__palette-empty {
  margin: 8px 2px 0;
  font-size: 10.5px;
  line-height: 1.65;
  color: var(--color-text-tertiary);
}
.wf__palette-empty a {
  color: var(--color-accent);
}
/* 悬停详情：fixed 定位、不吃鼠标事件（否则会挡住拖拽） */
.wf-brief {
  position: fixed;
  z-index: 40;
  pointer-events: none;
}
.wf-brief :deep(.brief__more) {
  pointer-events: auto;
}
.wf__palette-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.wf-skill {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 9px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: grab;
}
.wf-skill:hover {
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-xs);
}
.wf-skill__grip {
  color: var(--color-text-faint);
  flex-shrink: 0;
}
.wf-skill__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}
.wf-skill__name {
  display: block;
  font-size: var(--fs-12);
  font-weight: 600;
}
.wf-skill__role {
  display: block;
  font-size: 10px;
  color: var(--color-text-tertiary);
  font-family: ui-monospace, Consolas, monospace;
}

.wf__canvas {
  flex: 1;
  min-width: 0;
  height: 100%;
  background-color: var(--color-sidebar);
  background-image: radial-gradient(var(--color-canvas-dot) 1px, transparent 1px);
  background-size: 20px 20px;
}
:deep(.vue-flow__edge-path) {
  stroke: var(--color-border-strong);
  stroke-width: 1.6;
  transition: stroke 0.13s var(--ease), stroke-width 0.13s var(--ease);
}
:deep(.vue-flow__edge:hover .vue-flow__edge-path) {
  stroke: var(--color-accent);
  stroke-width: 2.4;
}
:deep(.vue-flow__edge.selected .vue-flow__edge-path) {
  stroke: var(--color-accent);
  stroke-width: 2.8;
}
:deep(.vue-flow__edge),
:deep(.vue-flow__edge-interaction) {
  cursor: pointer;
}
.wf__panel {
  width: 0;
  overflow: hidden;
  border-left: 1px solid var(--color-border);
  background: var(--color-surface);
  transition: width 0.2s var(--ease);
}
.wf__body--panel .wf__panel {
  width: 300px;
}
.wf__panel-inner {
  width: 300px;
  height: 100%;
  overflow-y: auto;
}
.cfg {
  padding: 18px 20px 28px;
}
.cfg__title {
  font-size: var(--fs-11);
  font-weight: 650;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}
.cfg__name {
  font-size: var(--fs-16);
  font-weight: 700;
  margin: 10px 0 2px;
}
.cfg__role {
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
  font-family: ui-monospace, Consolas, monospace;
}
.cfg__note {
  margin-top: 16px;
  font-size: var(--fs-12);
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.cfg__foot {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
}
.cfg__field {
  display: block;
  margin: 10px 0 4px;
}
.cfg__field-label {
  display: block;
  margin-bottom: 4px;
  font-size: var(--fs-12);
  color: var(--color-text-secondary);
}
.cfg__input {
  width: 100%;
  padding: 7px 9px;
  font-size: var(--fs-12);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.cfg__warn {
  margin-top: 6px;
  padding: 7px 9px;
  font-size: 10.5px;
  line-height: 1.65;
  color: var(--color-warning);
  background: var(--color-warning-soft);
  border: 1px solid color-mix(in srgb, currentColor 30%, transparent);
  border-radius: var(--radius-md);
}
.cfg__help {
  margin-top: 10px;
  font-size: 10.5px;
  color: var(--color-text-tertiary);
}
.cfg__help summary {
  cursor: pointer;
  color: var(--color-accent);
}
.cfg__help-list {
  margin: 6px 0 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  line-height: 1.65;
}
.cfg__cond {
  margin-top: 6px;
  padding: 6px 8px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--color-warning);
  background: var(--color-warning-soft);
  border-radius: var(--radius-md);
}
.cfg__cond code {
  font-family: ui-monospace, 'SF Mono', Consolas, monospace;
}
.cfg__section {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.cfg__section-title {
  margin-bottom: 8px;
  font-size: var(--fs-12);
  font-weight: 650;
}
.cfg__tip {
  display: block;
  margin-top: 6px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
.cfg__input:focus {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-ring);
}
.cfg__delete {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  font-size: var(--fs-12);
  font-weight: 550;
  color: var(--color-danger);
  background: var(--color-danger-soft);
}
.cfg__delete:hover {
  background: rgba(207, 71, 71, 0.16);
}

.panel-enter-active,
.panel-leave-active {
  transition: opacity 0.16s var(--ease);
}
.panel-enter-from,
.panel-leave-to {
  opacity: 0;
}

.run-overlay {
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
.run-panel {
  width: min(560px, 100%);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}
.run-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
}
.run-title {
  font-size: var(--fs-14);
  font-weight: 650;
}
.run-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
}
.run-close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.run-body {
  padding: 16px 18px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.run-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.run-label {
  font-size: var(--fs-12);
  font-weight: 600;
  color: var(--color-text-secondary);
}
.run-select,
.run-textarea {
  width: 100%;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--fs-13);
  padding: 8px 10px;
  outline: none;
}
.run-textarea {
  resize: vertical;
  line-height: 1.6;
}
.run-select:focus,
.run-textarea:focus {
  border-color: rgba(86, 88, 212, 0.5);
}
.run-hint {
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.run-error {
  font-size: var(--fs-12);
  color: var(--color-danger);
}
.run-success {
  font-size: var(--fs-12);
  color: var(--color-accent);
}
.run-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
}

@media (max-width: 900px) {
  .wf__palette {
    display: none;
  }
  .wf__body {
    position: relative;
  }
  .wf__body--panel .wf__panel {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    width: 300px;
    z-index: 20;
    box-shadow: var(--shadow-lg);
  }
}
</style>
