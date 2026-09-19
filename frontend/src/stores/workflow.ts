import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { workflowApi, type WorkflowBody } from '@/api/workflow'
import { useAuthStore } from './auth'
import type { ApiWorkflow } from '@/types/api'
import type { WorkflowEdge, WorkflowNode } from '@/types/workflow'
import type { ApiNodeWhen } from '@/types/api'

/** 画布元素 id 生成器：时间戳 + 自增序号，避免同一毫秒内批量创建时 id 撞车 */
let idSeq = 0
const nextId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(++idSeq).toString(36)}`

/** 后端 workflow node → 画布节点（纵向排布）。
 *  ⚠️ `raw` 必须保留后端原始节点：画布不认识/不编辑的字段靠它透传回去（见 toApiNodes）。 */
function toCanvasNodes(wf: ApiWorkflow): WorkflowNode[] {
  return (wf.nodes ?? []).map((n, i) => ({
    id: n.id || `n-${i}`,
    type: 'custom',
    kind: 'skill',
    label: n.name || n.agent?.role_key || n.id,
    role: n.agent?.role_key ?? '',
    position: { x: 240, y: 20 + i * 120 },
    when: n.when ?? null,
    raw: n
  }))
}

/**
 * 画布节点 + 连线 → 后端 workflow node。
 *
 * 以**后端原始定义**（`n.raw`）为底，只覆盖画布管得着的字段（id / 步骤名 / 角色 / 依赖 / 条件）：
 *   · 画布不编辑的字段（inputs / output_kind / task_note / artifact_file / code_dir / system_prompt）
 *     原样带回去 —— 否则"打开→保存"就是一次静默的数据破坏（实测踩过）；
 *   · 从零拖出来的节点没有 raw，产出的对象与以前逐字段一致（不含 when 时不写 when）。
 */
function toApiNodes(nodes: WorkflowNode[], edges: WorkflowEdge[]) {
  return nodes
    .filter((n) => n.kind !== 'start' && n.kind !== 'end')
    .map((n) => {
      const base: Record<string, unknown> = { ...(n.raw ?? {}) }
      const merged: Record<string, unknown> = {
        ...base,
        id: n.id,
        name: n.label,
        agent: { ...((base.agent as Record<string, unknown>) ?? {}), role_key: n.role || n.label },
        deps: edges.filter((e) => e.target === n.id).map((e) => e.source)
      }
      if (n.when) merged.when = n.when
      else delete merged.when          // 画布上取消了条件 → 定义里也不能留着
      return merged as WorkflowNodeWire
    })
}

/** 保存出去的后端节点形状（字段透传，所以是宽松的字典） */
export type WorkflowNodeWire = Record<string, unknown> & { id: string; deps: string[] }

export const useWorkflowStore = defineStore('workflow', () => {
  const workflows = ref<ApiWorkflow[]>([])
  const nodes = ref<WorkflowNode[]>([])
  const edges = ref<WorkflowEdge[]>([])
  const selectedNodeId = ref<string | null>(null)
  const selectedEdgeId = ref<string | null>(null)
  const activeWorkflowName = ref<string | null>(null)
  const loading = ref(false)
  const error = ref('')

  const selectedNode = computed<WorkflowNode | null>(
    () => nodes.value.find((n) => n.id === selectedNodeId.value) ?? null
  )

  const selectedEdge = computed<WorkflowEdge | null>(
    () => edges.value.find((e) => e.id === selectedEdgeId.value) ?? null
  )

  /** 当前打开的工作流定义（内置或自定义） */
  const activeWorkflow = computed<ApiWorkflow | null>(
    () => workflows.value.find((w) => w.name === activeWorkflowName.value) ?? null
  )

  /** 当前画布（含连线）转成后端可执行 / 可保存的节点数组 */
  function currentApiNodes() {
    return toApiNodes(nodes.value, edges.value)
  }

  async function loadWorkflows(): Promise<void> {
    const auth = useAuthStore()
    if (!auth.user) return
    loading.value = true
    error.value = ''
    try {
      const res = await workflowApi.list(auth.user.id)
      workflows.value = res.items ?? []
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  function openWorkflow(name: string) {
    const wf = workflows.value.find((w) => w.name === name)
    if (!wf) return
    activeWorkflowName.value = wf.name
    nodes.value = toCanvasNodes(wf)
    edges.value = []
    for (const n of wf.nodes ?? []) {
      for (const dep of n.deps ?? []) addEdge(dep, n.id)
    }
    selectedNodeId.value = null
    selectedEdgeId.value = null
  }

  function select(id: string | null) {
    selectedNodeId.value = id
    selectedEdgeId.value = null
  }

  function selectEdge(id: string | null) {
    selectedEdgeId.value = id
    selectedNodeId.value = null
  }

  function clearSelection() {
    selectedNodeId.value = null
    selectedEdgeId.value = null
  }

  /** 设置/取消执行条件（when）。传 null 就是"无条件执行"。 */
  function setNodeWhen(id: string, when: ApiNodeWhen | null) {
    const node = nodes.value.find((n) => n.id === id)
    if (!node) return
    const ref = (when?.ref || '').trim()
    if (!when || !ref) {
      node.when = null
      delete node.when
      return
    }
    node.when = { ref, eq: when.eq }
  }

  /** 改步骤名（画布节点 label → 后端 node.name）。
   *  为什么需要：同一个 Agent 可以在图上出现多次（"审批后再让 PM 出一版"就是这样画的），
   *  都叫「产品经理」的话，运行日志和产物清单里根本分不清哪一步是哪一步。 */
  function renameNode(id: string, label: string) {
    const node = nodes.value.find((n) => n.id === id)
    const next = label.trim()
    if (node && next) node.label = next
  }

  function moveNode(id: string, position: { x: number; y: number }) {
    const node = nodes.value.find((n) => n.id === id)
    if (node) node.position = position
  }

  function addNode(label: string, position: { x: number; y: number }, role = ''): WorkflowNode {
    const node: WorkflowNode = { id: nextId('n'), type: 'custom', kind: 'skill', label, role, position }
    nodes.value.push(node)
    return node
  }

  function addEdge(source: string, target: string) {
    if (!source || !target || source === target) return
    if (edges.value.some((e) => e.source === source && e.target === target)) return
    edges.value.push({ id: nextId('e'), source, target })
  }

  function removeNode(id: string) {
    nodes.value = nodes.value.filter((n) => n.id !== id)
    const kept = edges.value.filter((e) => e.source !== id && e.target !== id)
    if (kept.length !== edges.value.length && !kept.some((e) => e.id === selectedEdgeId.value)) {
      selectedEdgeId.value = null
    }
    edges.value = kept
    if (selectedNodeId.value === id) selectedNodeId.value = null
  }

  function removeEdge(id: string) {
    edges.value = edges.value.filter((e) => e.id !== id)
    if (selectedEdgeId.value === id) selectedEdgeId.value = null
  }

  async function saveAsWorkflow(name: string, description = ''): Promise<ApiWorkflow> {
    const auth = useAuthStore()
    if (!auth.user) throw new Error('未登录')
    const body: WorkflowBody = { name, description, nodes: toApiNodes(nodes.value, edges.value) }
    const created = await workflowApi.create(auth.user.id, body)
    await loadWorkflows()
    activeWorkflowName.value = created.name
    return created
  }

  /**
   * 把当前画布存回**已打开的自定义工作流**（PUT /api/workflows/{id}，§5.3）。
   * 内置模板 id 为 null，天然改不了 —— 只能走「另存为新工作流」。
   */
  async function saveWorkflow(): Promise<ApiWorkflow> {
    const wf = activeWorkflow.value
    if (!wf) throw new Error('请先选择一张工作流')
    if (wf.id == null) throw new Error('内置工作流不能直接修改，请用「另存为新工作流」新建一份')
    const body: WorkflowBody = {
      name: wf.name,
      description: wf.description ?? '',
      nodes: toApiNodes(nodes.value, edges.value)
    }
    const updated = await workflowApi.update(wf.id, body)
    await loadWorkflows()
    activeWorkflowName.value = updated.name ?? wf.name
    return updated
  }

  async function removeWorkflow(id: number): Promise<void> {
    await workflowApi.remove(id)
    workflows.value = workflows.value.filter((w) => w.id !== id)
  }

  return {
    workflows,
    nodes,
    edges,
    selectedNodeId,
    selectedEdgeId,
    selectedNode,
    selectedEdge,
    activeWorkflow,
    activeWorkflowName,
    currentApiNodes,
    loading,
    error,
    loadWorkflows,
    openWorkflow,
    select,
    selectEdge,
    clearSelection,
    moveNode,
    renameNode,
    setNodeWhen,
    addNode,
    addEdge,
    removeNode,
    removeEdge,
    saveAsWorkflow,
    saveWorkflow,
    removeWorkflow
  }
})
