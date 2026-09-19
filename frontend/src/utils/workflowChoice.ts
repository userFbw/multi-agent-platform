import type { ApproveOptions } from '@/api/project'

/**
 * 「这个项目走哪张图」的选择（接口文档 §2.1 / §2.2）。
 *
 * 现在是**建项目时**就定下来（`POST /api/projects/create` 带 `workflow_id` / `workflow_name`），
 * 后端把它记在项目上（`projects.workflow_name`），之后**审批与迭代都沿用**：
 *  - 首页「指定工作流」选好后随创建请求发出去；
 *  - 这里存的只是本地记忆（按项目 + 「上次选择」），**仅供界面回显** —— 丢了也不影响，
 *    后端仍按项目上记的那张图跑。
 *
 * 用 key 的形式（`''` / `name:xxx` / `id:n`）在 UI 与存储间传递，转成请求参数时再拆开。
 */
const DEFAULT_KEY = 'agent-work-default-workflow'
const PROJECT_PREFIX = 'agent-work-project-workflow:'
const projectKey = (projectId: string | number) => `${PROJECT_PREFIX}${projectId}`

/**
 * 不指定图时后端用的模版。
 * ⚠️ 必须与 `backend/app/agents/builtin_workflows.py` 的 `DEFAULT_WORKFLOW_NAME` 一致
 * （内置模版现在用**中文名**，与首页下拉里的选项一一对应）。
 */
export const DEFAULT_WORKFLOW_NAME = '复杂项目'

/** 两张项目模版的人话说明：决定**要不要建后端**，所以文案必须写清适用范围 */
export const WORKFLOW_LABELS: Record<string, { label: string; desc: string }> = {
  简单项目: {
    label: '简单项目',
    desc: '纯前端页面（计算器/计时器等）：简单前端直出静态三件套 + 测试，不含登录与数据库'
  },
  复杂项目: {
    label: '复杂项目',
    desc: '含后端与数据库：架构拆解 → 后端 ∥ 前端 → 测试'
  }
}

/**
 * 已经作废的模版名 → 视为「没选」，避免拿旧名字去请求。
 * 后端也留了同样的映射（`LEGACY_WORKFLOW_NAMES`），所以历史项目不会因为改名而报错；
 * 前端这里只是把下拉里认不出的旧值干脆丢掉，让用户重选。
 */
const RETIRED_WORKFLOW_NAMES = new Set([
  'simple_chain', 'fullstack_chain',   // 改名前的英文名
  'dev_only_chain', 'full_dev_chain',  // 上一版下线的两张图
  'plan_only', 'demo_two_node'         // 已从用户可见列表移除
])

export type WorkflowChoice = ApproveOptions

/** 选择 → 下拉项的 key */
export function choiceToKey(choice: WorkflowChoice | null | undefined): string {
  if (!choice) return ''
  if (choice.workflowId != null) return `id:${choice.workflowId}`
  if (choice.workflowName) return `name:${choice.workflowName}`
  return ''
}

/** 下拉项的 key → 选择（已作废的模版名 → 空选择） */
export function keyToChoice(key: string): WorkflowChoice {
  const raw = (key ?? '').trim()
  if (!raw) return {}
  const idx = raw.indexOf(':')
  if (idx < 0) return {}
  const kind = raw.slice(0, idx)
  const value = raw.slice(idx + 1)
  if (!value) return {}
  if (kind === 'id') {
    const id = Number(value)
    return Number.isFinite(id) ? { workflowId: id } : {}
  }
  if (RETIRED_WORKFLOW_NAMES.has(value)) return {}
  return { workflowName: value }
}

function read(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function write(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* 隐私模式等场景下静默降级：不记忆，功能仍可用 */
  }
}

/** 记住「这个项目用哪张图」 */
export function saveProjectChoice(projectId: string | number, choice: WorkflowChoice): void {
  write(projectKey(projectId), choiceToKey(choice))
}

/** 取某个项目在首页选过的图；没有则返回 null */
export function loadProjectChoice(projectId: string | number): WorkflowChoice | null {
  const raw = read(projectKey(projectId))
  return raw ? keyToChoice(raw) : null
}

/** 记住「上次选的图」，作为下次进首页的默认值 */
export function saveDefaultChoice(choice: WorkflowChoice): void {
  write(DEFAULT_KEY, choiceToKey(choice))
}

export function loadDefaultChoice(): WorkflowChoice {
  const raw = read(DEFAULT_KEY)
  return raw ? keyToChoice(raw) : {}
}

/**
 * 这张图能不能在「审批」时机跑通？
 *
 * 含 `planner` 节点的图需要 `seed.user_requirement` + `seed.available_skills`，
 * 而审批链路不提供这两个种子，选了必然失败。所以要把它们过滤掉。
 * （含 `planner` 节点的图属于"编排官出图"那一步，不该被当成项目模版。）
 */
export function isRunnableAtApproval(wf: {
  name?: string | null
  nodes?: Array<{ agent?: { role_key?: string } | null }> | null
}): boolean {
  return !(wf.nodes ?? []).some((n) => n.agent?.role_key === 'planner')
}

/**
 * 这张图能不能**拿来建项目**？
 *
 * 后端要求「必须含生成 PRD 的节点（`role_key = pm`）」—— 没有它就没有审批对象，
 * 整个项目生命周期不成立，会直接 400。所以在首页下拉里就不该出现这种图。
 *
 * 顺带沿用审批时机的那条过滤（含 `planner` 节点的图需要额外种子）。
 */
export function isRunnableAsProject(wf: {
  name?: string | null
  nodes?: Array<{ agent?: { role_key?: string } | null }> | null
}): boolean {
  if (!isRunnableAtApproval(wf)) return false
  return (wf.nodes ?? []).some((n) => n.agent?.role_key === 'pm')
}

/**
 * 退出登录 / 注销账号时清空这些记忆。
 * 它们不属于某个账号的隐私数据，但「上次选的图」可能指向**上一个用户**的自定义工作流 id，
 * 换账号后留着会让审批拿到 400 —— 一并清掉最省事。
 */
export function clearWorkflowChoices(): void {
  try {
    const doomed: string[] = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i)
      if (k && (k === DEFAULT_KEY || k.startsWith(PROJECT_PREFIX))) doomed.push(k)
    }
    doomed.forEach((k) => localStorage.removeItem(k))
  } catch {
    /* 忽略 */
  }
}
