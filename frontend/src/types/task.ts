import type { AgentType } from './agent'
import type { ApiProjectMode, ApiProjectStatus, ApiStepStatus } from './api'
import type { PreviewKind } from '@/utils/previewUrl'

/** 前端统一任务/项目状态 */
export type TaskStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  /** 步骤级：被条件跳过（when 不满足） */
  | 'stopped'
  /** 项目级：客户手动终止（和"失败"区分开，界面不该显示成红色故障） */
  | 'aborted'

export const mapProjectStatus = (s: ApiProjectStatus): TaskStatus => {
  switch (s) {
    case 'INITIAL':
      return 'pending'
    case 'RUNNING':
      return 'running'
    case 'PENDING_APPROVAL':
      return 'awaiting_approval'
    case 'COMPLETED':
      return 'completed'
    case 'FAILED':
      return 'failed'
    case 'STOPPED':
      return 'aborted'
    default:
      return 'pending'
  }
}

export const mapStepStatus = (s: ApiStepStatus): TaskStatus => {
  switch (s) {
    case 'PENDING':
      return 'pending'
    case 'RUNNING':
      return 'running'
    case 'SUCCESS':
      return 'completed'
    case 'FAILED':
      return 'failed'
    case 'SKIPPED':
      return 'stopped'
    default:
      return 'pending'
  }
}

/** 列表项（对应后端项目列表） */
export interface TaskSummary {
  id: string
  title: string
  status: TaskStatus
  rawStatus: ApiProjectStatus
  zipPath: string | null
  /** 本项目选定的工作流（后端 `workflow_name`）；老项目为 null */
  workflowName: string | null
  /** 编排模式（后端 `mode`） */
  mode: ApiProjectMode
}

/** 单个步骤（对应 project_steps） */
export interface TaskStep {
  id: string
  stepNo: number
  agentId: number
  agentName: string
  agentType: AgentType
  name: string
  status: TaskStatus
  rawStatus: ApiStepStatus
  artifactPath: string | null
  sessionId: string | null
  elapsedMs: number | null
  /** 开跑时刻（epoch 毫秒）；进行中的步骤用它显示实时耗时 */
  startedAtMs: number | null
  errorCode: string | null
  error: string | null
}

/** 一步 Agent 的产出（对应后端 artifacts 清单） */
export interface TaskArtifact {
  stepNo: number
  roundNo: number
  name: string
  status: TaskStatus
  rawStatus: string
  agentName: string | null
  skill: string | null
  /** 项目内相对路径；失败 / 只跑没产物的步骤为空 */
  path: string | null
  kind: string
  exists: boolean
  isDir: boolean
  size: number
  files: number
  error: string | null
}

/** 任务详情视图模型（由 project + steps + PRD + QA + preview 组合） */
export interface Task {
  id: string
  title: string
  prompt: string
  status: TaskStatus
  rawStatus: ApiProjectStatus
  zipPath: string | null
  /** 本项目选定的工作流（后端 `workflow_name`）：建项目时定下，审批与迭代沿用 */
  workflowName: string | null
  /** 编排模式：workflow（图先存在）/ agent（审批后编排官出图） */
  mode: ApiProjectMode
  roundNo: number
  rounds: number[]
  steps: TaskStep[]
  prd: string | null
  qaReport: string | null
  previewUrl: string | null
  /**
   * 预览形态（见 utils/previewUrl.ts）：
   * `app` = 前后端分离、页面由生成的后端托管在独立端口 → 不能走 srcdoc 注入，得让 iframe 直接访问；
   * `static` = 纯前端静态页 → 继续走 srcdoc 注入 window.__API_BASE__ 的老路子。
   */
  previewKind: PreviewKind
  /** app 模式：生成后端是否在跑（running / not_started / occupied …），用于预览区提示与启动按钮 */
  appStatus: string | null
  /** app 模式：生成后端监听的端口（如 8100） */
  appPort: number | null
  /** app 模式：实际使用的主机名（部署方声明的公网地址优先） */
  appHost: string | null
  /** app 模式：客户可直接打开的完整地址（仅 kind=app 有值；静态页走 previewUrl） */
  appUrl: string | null
  /** 每一步 Agent 的产出（含自定义节点）；点开可看全文 */
  nodeArtifacts: TaskArtifact[]
}

/** 展示用产物（PRD / QA / ZIP / 步骤产物路径） */
export interface Artifact {
  id: string
  name: string
  kind: 'markdown' | 'code' | 'report' | 'data'
  content?: string
  hint?: string
}

/**
 * 角色名称规范化规则：按关键字把后端 agent_name 归一后归类。
 * 顺序即优先级——规划/规范化类必须排在 pm 之前，
 * 否则「需求规范化」会被 /需求/ 误判成 PM（F1-10）。
 */
const AGENT_TYPE_RULES: Array<{ type: AgentType; re: RegExp }> = [
  { type: 'planner', re: /planner|normaliz|规范化|标准化|需求规范|拆解|编排|规划|计划|架构|architect/ },
  { type: 'qa', re: /qa|测试|test|质检|验收/ },
  { type: 'pm', re: /pm|product|产品|需求|prd/ },
  { type: 'general', re: /review|审查|审核|reviewer|doc|文档|writer/ },
  { type: 'dev', re: /dev|engineer|程序|代码|开发|实现|前端|后端|frontend|backend|fullstack/ },
  { type: 'sandbox', re: /sandbox|沙箱|运行|验证|自检/ }
]

/** 将后端 agent_name 归类为前端 AgentType（用于图标/标签） */
export const agentTypeFromName = (name: string): AgentType => {
  const n = (name || '').toLowerCase()
  for (const rule of AGENT_TYPE_RULES) {
    if (rule.re.test(n)) return rule.type
  }
  return 'general'
}
