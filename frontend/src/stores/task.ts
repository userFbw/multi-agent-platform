import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { projectApi } from '@/api/project'
import type { ApproveOptions, CreateProjectOptions } from '@/api/project'
import { stepApi } from '@/api/step'
import { useAuthStore } from './auth'
import type { ApiArtifactItem, ApiProject, ApiStep, ApiStepStatus } from '@/types/api'
import { agentTypeFromName, mapProjectStatus, mapStepStatus } from '@/types/task'
import type { Task, TaskArtifact, TaskStep, TaskSummary } from '@/types/task'
import { resolvePreview } from '@/utils/previewUrl'
import type { PreviewKind } from '@/utils/previewUrl'
import { isOccupiedMessage, occupiedProjectId } from '@/utils/appAction'

function toSummary(p: ApiProject): TaskSummary {
  return {
    id: String(p.id),
    title: p.title,
    status: mapProjectStatus(p.status),
    rawStatus: p.status,
    zipPath: p.zip_path ?? null,
    workflowName: p.workflow_name ?? null,
    mode: p.mode ?? 'workflow'
  }
}

function toStep(s: ApiStep): TaskStep {
  return {
    id: String(s.id),
    stepNo: s.step_no,
    agentId: s.agent_id,
    agentName: s.agent_name,
    agentType: agentTypeFromName(s.agent_name),
    name: s.name,
    status: mapStepStatus(s.status),
    rawStatus: s.status,
    artifactPath: s.artifact_path ?? null,
    sessionId: s.session_id ?? null,
    elapsedMs: s.elapsed_ms ?? null,
    startedAtMs: s.started_at_ms ?? null,
    errorCode: s.error_code ?? null,
    error: s.error ?? null
  }
}

function toArtifact(a: ApiArtifactItem): TaskArtifact {
  return {
    stepNo: a.step_no,
    roundNo: a.round_no,
    name: a.name,
    status: mapStepStatus(a.status as ApiStepStatus),
    rawStatus: a.status,
    agentName: a.agent_name ?? null,
    skill: a.skill ?? null,
    path: a.artifact_path ?? null,
    kind: a.kind,
    exists: a.exists,
    isDir: a.is_dir,
    size: a.size,
    files: a.files,
    error: a.error ?? null
  }
}

export const useTaskStore = defineStore('task', () => {
  const list = ref<TaskSummary[]>([])
  const tasks = ref<Record<string, Task>>({})
  const currentId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref('')
  const polling = ref(false)
  const lastSyncAt = ref<string | null>(null)
  let pollTimer: number | null = null

  const currentTask = computed<Task | null>(() =>
    currentId.value ? tasks.value[currentId.value] ?? null : null
  )

  async function loadProjects(): Promise<void> {
    const auth = useAuthStore()
    if (!auth.user) return
    loading.value = true
    error.value = ''
    try {
      const projects = await projectApi.list(auth.user.id)
      list.value = projects.map(toSummary)
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  /** 创建项目：`opts.mode` = 编排模式（默认 workflow），`opts.workflow*` = 选定哪张图。
   *  workflow 模式下图**建项目时就定在项目上**，之后审批/迭代都沿用，不用前端再带；
   *  agent 模式不指定图，等审批通过后由编排官读 PRD 出图。 */
  async function createProject(
    title: string,
    description: string,
    opts?: CreateProjectOptions
  ): Promise<TaskSummary> {
    const auth = useAuthStore()
    if (!auth.user) throw new Error('未登录')
    const res = await projectApi.create(auth.user.id, title, description, opts)
    const summary: TaskSummary = {
      id: String(res.project_id),
      title,
      status: mapProjectStatus(res.status),
      rawStatus: res.status,
      zipPath: null,
      workflowName: res.workflow ?? opts?.workflowName ?? null,
      mode: res.mode ?? opts?.mode ?? 'workflow'
    }
    list.value.unshift(summary)
    return summary
  }

  /** 拉取项目详情 + 步骤 + PRD/QA/预览，组装成视图模型 */
  async function loadTask(id: string, opts: { silent?: boolean } = {}): Promise<void> {
    const projectId = Number(id)
    if (!opts.silent) loading.value = true
    error.value = ''
    try {
      const [project, stepsResp, rounds] = await Promise.all([
        projectApi.get(projectId),
        stepApi.latest(projectId).catch(() => null),
        stepApi.rounds(projectId).catch(() => null)
      ])
      const steps = (stepsResp?.steps ?? []).map(toStep)
      const status = mapProjectStatus(project.status)

      let prd: string | null = null
      let qaReport: string | null = null
      let previewUrl: string | null = null
      let previewKind: PreviewKind = 'static'
      let appStatus: string | null = null
      let appPort: number | null = null
      let appHost: string | null = null
      let appUrl: string | null = null
      let nodeArtifacts: TaskArtifact[] = []

      if (project.status !== 'INITIAL') {
        prd = await projectApi.prd(projectId).then((r) => r.prd_content).catch(() => null)
        // 每一步的产出清单（正文按需再拉）：自定义节点的结果也在这里露出
        nodeArtifacts = await projectApi
          .artifacts(projectId)
          .then((r) => (r.items ?? []).map(toArtifact))
          .catch(() => [])
      }
      // STOPPED（客户手动终止）也拉预览：已生成的应用可能还在跑，客户想看看半成品
      if (project.status === 'COMPLETED' || project.status === 'STOPPED') {
        qaReport = await projectApi.qaReport(projectId).then((r) => r.qa_report).catch(() => null)
        // ★ 预览地址由浏览器自己拼主机名（后端只能看到"服务器内部"的来源，拼出来是 127.0.0.1）
        const preview = await projectApi
          .previewUrl(projectId)
          .then((r) => resolvePreview(r))
          .catch(() => resolvePreview(null))
        previewUrl = preview.url
        previewKind = preview.kind
        appStatus = preview.appStatus
        appPort = preview.appPort
        appHost = preview.appHost
        appUrl = preview.kind === 'app' ? preview.url : null
      }

      const task: Task = {
        id,
        title: project.title,
        prompt: project.description ?? '',
        status,
        rawStatus: project.status,
        zipPath: project.zip_path ?? null,
        workflowName: project.workflow_name ?? null,
        mode: project.mode ?? 'workflow',
        roundNo: stepsResp?.round_no ?? rounds?.latest_round ?? 1,
        rounds: rounds?.rounds ?? [],
        steps,
        prd,
        qaReport,
        previewUrl,
        previewKind,
        appStatus,
        appPort,
        appHost,
        appUrl,
        nodeArtifacts
      }
      tasks.value[id] = task
      syncSummary(task)
      lastSyncAt.value = new Date().toISOString()

      if (status === 'completed' || status === 'failed' || status === 'aborted') stopPolling()
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      if (!opts.silent) loading.value = false
    }
  }

  function syncSummary(task: Task) {
    const idx = list.value.findIndex((t) => t.id === task.id)
    const item: TaskSummary = {
      id: task.id,
      title: task.title,
      status: task.status,
      rawStatus: task.rawStatus,
      zipPath: task.zipPath,
      workflowName: task.workflowName,
      mode: task.mode
    }
    if (idx >= 0) list.value[idx] = item
    else list.value.unshift(item)
  }

  /** 审批通过。`opts` 只在**要中途换图**时才传 —— 不传就沿用项目选定的那张（PM 也不会重跑）。
   *  失败（如图不存在/不属于自己 → 400）写进 error，交由页面展示，不抛给调用方。 */
  async function approve(id: string, opts?: ApproveOptions): Promise<void> {
    error.value = ''
    try {
      await projectApi.approve(Number(id), true, opts?.feedback, opts)
      await loadTask(id, { silent: true })
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  async function reject(id: string, feedback: string): Promise<void> {
    error.value = ''
    try {
      await projectApi.approve(Number(id), false, feedback)
      await loadTask(id, { silent: true })
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  /** 迭代修改：默认沿用项目选定的图（图在项目上，所以"迭代一次就悄悄换回默认图"不存在了）。
   *  只有需求形态变了才传 `opts` 换图。 */
  /**
   * 提交修改意见并重跑。
   * `iteration` 由界面上的两个按钮决定：增量修改（保代码）/ 重新生成（清代码重写）。
   */
  async function revise(id: string, feedback: string, opts?: ApproveOptions,
                        iteration: 'incremental' | 'regenerate' = 'regenerate'): Promise<void> {
    error.value = ''
    try {
      await projectApi.revise(Number(id), feedback, opts, iteration)
      await loadTask(id, { silent: true })
      if (iteration === 'incremental') {
        appFeedback.value = { kind: 'ok', text: '已按「增量修改」重跑：会保留现有代码，只改你要求的部分' }
      }
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  /* ---------------- 生成项目的应用进程（S2-3 / S2-4 / S2-6 共用） ----------------
   * 状态与地址都存在 Task 上，界面（AppRunner / 预览区）只读 store，不各自拉接口。
   */

  /** 最近一次应用操作的反馈（成功文案 / 被占用的提示），界面直接展示 */
  const appFeedback = ref<{ kind: 'ok' | 'warn' | 'error'; text: string; occupiedBy?: number | null } | null>(null)

  function patchApp(id: string, patch: Partial<Task>) {
    const task = tasks.value[id]
    if (task) tasks.value[id] = { ...task, ...patch }
  }

  /** 启动 / 停止 / 重启后把最新的运行状态（+预览地址）同步回来 */
  async function syncAppState(id: string): Promise<void> {
    const num = Number(id)
    const [st, pv] = await Promise.all([
      projectApi.appStatus(num).catch(() => null),
      projectApi.previewUrl(num).catch(() => null)
    ])
    if (st) patchApp(id, { appStatus: st.status, appPort: st.port ?? null })
    if (pv) {
      const p = resolvePreview(pv)
      patchApp(id, { previewUrl: p.url, previewKind: p.kind, appPort: p.appPort,
                     appHost: p.appHost, appUrl: p.kind === 'app' ? p.url : null })
    }
  }

  /**
   * 启动应用。`force=true` 才会停掉占着固定端口的**别的项目**（应用单实例）。
   * 409 = 端口被别的项目占用 → 把占用者的项目号放进 feedback，界面据此问"要切换吗"。
   */
  async function startApp(id: string, force = false): Promise<boolean> {
    appFeedback.value = null
    try {
      const res = await projectApi.appStart(Number(id), force)
      await syncAppState(id)
      appFeedback.value = { kind: 'ok', text: res.message ?? `应用已启动${res.ready_seconds ? `（${res.ready_seconds}s）` : ''}` }
      return true
    } catch (e) {
      const msg = (e as Error).message
      const occupied = isOccupiedMessage(msg)
      appFeedback.value = { kind: occupied ? 'warn' : 'error', text: msg,
                            occupiedBy: occupiedProjectId(msg) }
      await syncAppState(id)
      return false
    }
  }

  async function stopApp(id: string): Promise<boolean> {
    appFeedback.value = null
    try {
      const res = await projectApi.appStop(Number(id))
      await syncAppState(id)
      appFeedback.value = {
        kind: 'ok',
        text: res.stopped ? '应用已停止' : res.note ?? '没有在跑的应用'
      }
      return true
    } catch (e) {
      appFeedback.value = { kind: 'error', text: (e as Error).message }
      return false
    }
  }

  /** 重启 = 停 + 起（改完代码/依赖后常用） */
  async function restartApp(id: string): Promise<boolean> {
    await projectApi.appStop(Number(id)).catch(() => null)
    return startApp(id, true)
  }

  /** 最近一次「终止运行」的反馈（界面顶部提示用） */
  const abortFeedback = ref<{ kind: 'ok' | 'error'; text: string } | null>(null)

  /**
   * 强制终止这个项目正在跑的 AI 会话。
   * 客户自己按停 → 后端把项目置为 STOPPED（**不是失败**），页面顶部给一句说明。
   */
  async function abortRun(id: string): Promise<boolean> {
    abortFeedback.value = null
    try {
      const res = await projectApi.abortRun(Number(id))
      abortFeedback.value = { kind: 'ok', text: res.message || '已终止' }
      await loadTask(id, { silent: true })
      return res.aborted
    } catch (e) {
      abortFeedback.value = { kind: 'error', text: (e as Error).message }
      return false
    }
  }

  /** 终止后接着跑：复用 revise（PRD **不重跑**，从架构拆解那一步往后继续） */
  async function resumeAfterAbort(id: string): Promise<void> {
    abortFeedback.value = null
    // 续跑默认走**增量**：已完成的产物保留，只补没做完的部分（比例行重写便宜且不丢细节）
    await revise(id, '上一次运行没跑完，请沿用已完成的产物继续把项目做完。', undefined, 'incremental')
  }

  async function removeProject(id: string): Promise<void> {
    await projectApi.remove(Number(id))
    list.value = list.value.filter((t) => t.id !== id)
    delete tasks.value[id]
    if (currentId.value === id) currentId.value = null
  }

  function select(id: string | null) {
    currentId.value = id
  }

  function startPolling(id: string) {
    stopPolling()
    polling.value = true
    pollTimer = window.setInterval(() => loadTask(id, { silent: true }), 5000)
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
    polling.value = false
  }

  function reset() {
    currentId.value = null
    stopPolling()
  }

  /** 退出登录用：连内存里的项目列表/详情一起清掉，避免下一个账号看到上一个账号的缓存 */
  function clearAll() {
    reset()
    list.value = []
    tasks.value = {}
    error.value = ''
    lastSyncAt.value = null
  }

  return {
    list,
    tasks,
    currentId,
    currentTask,
    loading,
    error,
    polling,
    lastSyncAt,
    loadProjects,
    createProject,
    loadTask,
    approve,
    reject,
    revise,
    appFeedback,
    abortFeedback,
    abortRun,
    resumeAfterAbort,
    startApp,
    stopApp,
    restartApp,
    syncAppState,
    removeProject,
    select,
    startPolling,
    stopPolling,
    reset,
    clearAll
  }
})
