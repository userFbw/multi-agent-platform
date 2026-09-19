import { assetUrl, http } from './http'
import type {
  ApiAbortRunResp,
  ApiAppActionResp,
  ApiAppLogsResp,
  ApiAppStatusResp,
  ApiArtifactContentResp,
  ApiArtifactsResp,
  ApiCreateProjectResp,
  ApiProjectMode,
  ApiPreviewResp,
  ApiPrdResp,
  ApiProject,
  ApiQaResp,
  ApiRunLogContentResp,
  ApiRunLogsResp,
  ApiSimpleResp
} from '@/types/api'

/**
 * 「这个项目用哪张图」（接口文档 §2.1 / §2.2）。
 *
 * 两张内置模版（节点定死、无分支；**名字就是首页下拉里的选项名**）：
 *   简单项目：生成 PRD → 简单前端直出静态三件套 → 自动化测试（纯前端页面）
 *   复杂项目：生成 PRD → 架构拆解 → 后端 ∥ 前端 → 自动化测试（含后端/数据库）
 *
 * 用法：
 *   · **建项目时**（`create`）传 → 图就定下来了，审批与迭代都沿用它；
 *   · 不传 → 后端用默认模版「复杂项目」；
 *   · `approve` / `revise` 也收这两个参数，但语义是「**中途换图**」（会被记到项目上）。
 */
export interface ApproveOptions {
  /**
   * 「同意」时一并提出的补充要求（可选）。
   * 后端会把它并进 PRD 正文（`## 审批时的补充要求`）一起送给后续节点；
   * 图里若在闸门后面还接了第二个 PM 节点，那个 PM 会据此改出一版新 PRD，下游读到的就是新版。
   */
  feedback?: string
  /** 自己的自定义工作流 id */
  workflowId?: number | null
  /** 内置模版名：`简单项目` / `复杂项目`（旧英文名后端仍兼容） */
  workflowName?: string | null
}

/** 创建项目时的选项：编排模式 + （workflow 模式下）选定哪张图 */
export interface CreateProjectOptions extends ApproveOptions {
  /**
   * 编排模式：
   *   `workflow`（默认）—— 图在建项目时就选好，审批通过后跑它的其余节点；
   *   `agent`           —— 审批通过后由编排官读已批准的 PRD 现场出图，再跑那张图。
   * 两种模式的审批闸门都在 PM 之后，PM 都只跑一次。
   */
  mode?: ApiProjectMode
}

export const projectApi = {
  /**
   * 创建项目并启动 PRD 段（PM 出 PRD 后停下等人工审批 —— 两种模式都一样）。
   * `options.workflow*` 只在 `mode='workflow'` 时有意义；不传则用默认模版「复杂项目」。
   */
  create: (userId: number, title: string, description: string, options?: CreateProjectOptions) => {
    const query: Record<string, string | number | boolean | null | undefined> = { user_id: userId }
    if (options?.mode) query.mode = options.mode
    if (options?.workflowId != null) query.workflow_id = options.workflowId
    if (options?.workflowName) query.workflow_name = options.workflowName
    return http.post<ApiCreateProjectResp>('/api/projects/create', { title, description }, query)
  },

  list: (userId: number) => http.get<ApiProject[]>('/api/projects/', { user_id: userId }),

  get: (projectId: number) => http.get<ApiProject>(`/api/projects/${projectId}`),

  /**
   * 人机协作审批：approved=false 即驳回，可带 feedback。
   *
   * ⚠️ **默认不要传 options**：图在建项目时就定在项目上（`project.workflow_name`），
   * 不传即沿用，PM 也不会重跑。传了 = 中途换图（会覆盖项目上记的那张图）。
   * 图不合法 / 不是自己的 / 不含 PRD 节点 → 400（不会静默回退）。
   */
  approve: (projectId: number, approved: boolean, feedback?: string, options?: ApproveOptions) => {
    const query: Record<string, string | number | boolean | null | undefined> = { approved }
    if (feedback) query.feedback = feedback
    if (options?.workflowId != null) query.workflow_id = options.workflowId
    if (options?.workflowName) query.workflow_name = options.workflowName
    return http.post<ApiSimpleResp>(`/api/projects/${projectId}/approve`, undefined, query)
  },

  /**
   * 代码迭代修改：默认**沿用项目选定的图**（图存在项目上）。
   * 只有需求形态变了（例如纯前端后来要加登录）才需要带 options 换图。
   */
  /**
   * 提交修改意见并重跑开发链。
   * `iteration`（前端两个按钮）：
   *   `incremental` = 增量修改：**保留现有代码**，Agent 在其基础上局部改（省钱、不丢细节）
   *   `regenerate`  = 重新生成：**先删掉 src/ 再重写**（适合需求大改 / 想彻底重来）
   */
  revise: (projectId: number, feedback: string, options?: ApproveOptions,
           iteration: 'incremental' | 'regenerate' = 'regenerate') => {
    const query: Record<string, string | number | boolean | null | undefined> = { feedback, iteration }
    if (options?.workflowId != null) query.workflow_id = options.workflowId
    if (options?.workflowName) query.workflow_name = options.workflowName
    return http.post<ApiSimpleResp>(`/api/projects/${projectId}/revise`, undefined, query)
  },

  /**
   * 强制终止这个项目正在跑的 AI 会话（客户自己按停 → 项目状态变 STOPPED，**不是失败**）。
   * 幂等：没有在跑也会返回 200（`aborted=false`）。
   */
  abortRun: (projectId: number) =>
    http.post<ApiAbortRunResp>(`/api/projects/${projectId}/run/abort`),

  previewUrl: (projectId: number) =>
    http.get<ApiPreviewResp>(`/api/projects/${projectId}/preview-url`),

  /* ---------------- 生成项目的应用进程（复杂项目才有后端，§2.10） ---------------- */

  /** 启动生成项目的后端；`force=true` 才会停掉占着端口的**别的项目**（应用单实例） */
  appStart: (projectId: number, force = false) =>
    http.post<ApiAppActionResp>(`/api/projects/${projectId}/app/start`, undefined, { force }),

  appStop: (projectId: number) =>
    http.post<ApiAppActionResp>(`/api/projects/${projectId}/app/stop`),

  appStatus: (projectId: number) =>
    http.get<ApiAppStatusResp>(`/api/projects/${projectId}/app/status`),

  /** 应用日志（装依赖日志尾部 + 运行输出尾部），排查"为什么起不来"就看它 */
  appLogs: (projectId: number, tail = 200) =>
    http.get<ApiAppLogsResp>(`/api/projects/${projectId}/app/logs`, { tail }),

  prd: (projectId: number) => http.get<ApiPrdResp>(`/api/projects/${projectId}/prd`),

  qaReport: (projectId: number) => http.get<ApiQaResp>(`/api/projects/${projectId}/qa-report`),

  /**
   * 每一步 Agent 的产出清单：**自定义 / 新增节点的产出不再只能去运行日志里翻**。
   * 路径来自引擎落盘时登记的 `project_steps.artifact_path`。
   */
  artifacts: (projectId: number) =>
    http.get<ApiArtifactsResp>(`/api/projects/${projectId}/artifacts`),

  /** 单份产物正文（目录产物 → 合并后的代码）；路径含中文和 `/`，必须 encodeURIComponent */
  artifactContent: (projectId: number, path: string) =>
    http.get<ApiArtifactContentResp>(
      `/api/projects/${projectId}/artifacts/content?path=${encodeURIComponent(path)}`
    ),

  /** 运行日志 / 项目BUG 清单（§2.8）：每个 Agent 节点干了什么 */
  runLogs: (projectId: number) =>
    http.get<ApiRunLogsResp>(`/api/projects/${projectId}/run-logs`),

  /** 单份日志正文；文件名是中文，必须 encodeURIComponent */
  runLog: (projectId: number, logName: string) =>
    http.get<ApiRunLogContentResp>(
      `/api/projects/${projectId}/run-logs/${encodeURIComponent(logName)}`
    ),

  remove: (projectId: number) => http.del<ApiSimpleResp>(`/api/projects/${projectId}`),

  /**
   * ZIP 下载地址（给 <a href> / window.open 用）。
   * ⚠️ 这种跳转带不上 Authorization 头，所以显式带 user_id 走过渡期兼容路径。
   */
  downloadUrl: (projectId: number, userId?: number | null) =>
    assetUrl(`/api/projects/${projectId}/download${userId != null ? `?user_id=${userId}` : ''}`)
}
