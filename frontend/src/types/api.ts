/** 后端接口 DTO（与《前端接口文档.md》一致） */

export interface ApiUser {
  id: number
  user_name: string
  password?: string
  avatar?: string | null
  /** 注册即视为已登录，响应里一并返回 token（接口文档 §1.1） */
  token?: string | null
}

export interface ApiLoginResp {
  status: string
  user_id: number
  user_name: string
  avatar?: string | null
  /** 身份凭证，见接口文档 §〇.1 */
  token?: string | null
}

export type ApiProjectStatus =
  | 'INITIAL'
  | 'RUNNING'
  | 'PENDING_APPROVAL'
  | 'COMPLETED'
  | 'FAILED'
  /** 客户在页面上点了「终止运行」——是**客户自己按停的**，不是系统故障 */
  | 'STOPPED'

export interface ApiProject {
  id: number
  user_id?: number
  title: string
  description?: string | null
  status: ApiProjectStatus
  zip_path?: string | null
  /** 本项目选定的工作流（§2.1）：建项目时定下、审批与迭代沿用 */
  workflow_name?: string | null
  /** 选的是自定义工作流时才有值（内置模版只有 name） */
  workflow_id?: number | null
  /** 编排模式（§2.1）：workflow = 图先画好；agent = 审批后由编排官读 PRD 出图 */
  mode?: ApiProjectMode
  /** 审批前的形态预检提示（§2.1）：PRD 判的运行形态与这张图对不上时给一句话，空串=没问题 */
  plan_warning?: string | null
}

/** 编排模式：两种模式的审批闸门都在 PM 之后，差别只在"通过之后图从哪来" */
export type ApiProjectMode = 'workflow' | 'agent'

export interface ApiCreateProjectResp {
  success: boolean
  message: string
  project_id: number
  status: ApiProjectStatus
  /** 这次用的是哪张图（§2.1），便于前端回显 */
  workflow?: string
  /** 本次的编排模式 */
  mode?: ApiProjectMode
}

export interface ApiSimpleResp {
  success: boolean
  message: string
}

export interface ApiPreviewResp {
  success: boolean
  is_web_project: boolean
  preview_url?: string | null
  /**
   * 产物形态：`app` = 前后端分离、要起生成的那个后端（独立端口，地址必须由前端按
   * 当前主机名重新拼）；`static` = 纯前端静态页（preview_url 是 /previews/... 相对路径，直接用）。
   */
  kind?: 'static' | 'app'
  /** app 模式的页面路径（一般就是 "/"） */
  path?: string | null
  /** app 模式生成后端监听的端口（如 8100） */
  app_port?: number | null
  /**
   * app 模式客户该用的主机名（部署方声明的公网地址，如 `8.134.74.75`）。
   * 空字符串 = 没声明 → 前端退回 `location.hostname`。
   */
  app_host?: string | null
  /** app 模式的进程状态：running / not_started / occupied …（§2.10） */
  app_status?: string | null
  /** 无 HTML 产物时的提示文案（§2.5） */
  message?: string | null
}

/**
 * POST /api/projects/{id}/run/abort 的返回（客户点「终止运行」）。
 * `aborted=false` 不代表失败：没有会话在跑时也返回 200，但**终止意图已记下**（后续节点不会再启动）。
 */
export interface ApiAbortRunResp {
  aborted: boolean
  killed: number[]
  step: string
  elapsed: number | null
  message: string
  project_id?: number
  app_status?: string | null
  note?: string
}

/** 生成项目后端的运行状态（GET /api/projects/{id}/app/status，§2.10） */
export interface ApiAppStatusResp {
  status: 'not_started' | 'running' | 'stopped' | 'occupied'
  port: number
  pid?: number | null
  /** 当前占着端口的项目号（可能是别人；occupied 时前端据此提示"要切换吗"） */
  project_id?: number | null
  running_this_project?: boolean
  started_at?: number | null
  ready_seconds?: number | null
  has_backend?: boolean
  needs_install?: boolean
  log_tail?: string
  note?: string
}

/** POST /app/start | /app/stop 的返回 */
export interface ApiAppActionResp {
  ok?: boolean
  status: string
  port?: number
  pid?: number | null
  project_id?: number | null
  ready_seconds?: number | null
  message?: string
  note?: string
  stopped?: boolean
  occupied_by?: number | null
  log_tail?: string
}

/** GET /app/logs：安装日志尾部 + 运行日志 */
export interface ApiAppLogsResp {
  project_id: number
  install_log_tail: string
  app_log: string[]
}

/** 运行日志 / 项目BUG（§2.8） */
export type ApiRunLogKind = '运行日志' | '项目BUG'

/** 正在用某张工作流的项目（GET /api/workflows/{id}/usage） */
export interface ApiWorkflowUsageProject {
  id: number
  title: string
  status: string
}

/** 改/删一张工作流之前的影响面 */
export interface ApiWorkflowUsage {
  workflow_id: number
  name: string
  projects: ApiWorkflowUsageProject[]
  project_count: number
  /** 有 RUNNING / PENDING_APPROVAL 的项目时最危险：它们下一次执行就用新图（或直接失败） */
  has_active_project: boolean
}

/** 引用了某 Agent 的一张工作流（GET /api/agents/{id}/usage） */
export interface ApiAgentUsageWorkflow {
  id: number
  name: string
  nodes: string[]
}

/** 删除自定义 Agent 前的影响面 */
export interface ApiAgentUsage {
  agent_id: number
  name: string
  role_key: string
  /** 能不能删；内置角色为 false */
  deletable: boolean
  reason?: string | null
  workflows: ApiAgentUsageWorkflow[]
  /** 用过它的历史项目（只影响留痕，不影响已有产物） */
  project_count: number
  project_titles: string[]
}

/** 一步 Agent 的产出（GET /api/projects/{id}/artifacts） */
export interface ApiArtifactItem {
  round_no: number
  step_no: number
  name: string
  status: string
  agent_name?: string | null
  /** 实际会跑的技能（提示词 Agent 会被换算成 generic-prompt-agent） */
  skill?: string | null
  /** 项目内相对路径；失败/跳过的步骤为空 */
  artifact_path?: string | null
  /** markdown / json / code / dir / text */
  kind: string
  exists: boolean
  is_dir: boolean
  size: number
  files: number
  error_code?: string | null
  error?: string | null
}

export interface ApiArtifactsResp {
  project_id: number
  round_no: number
  total: number
  items: ApiArtifactItem[]
}

export interface ApiArtifactContentResp {
  project_id: number
  path: string
  name: string
  size: number
  content: string
}

export interface ApiRunLogItem {
  kind: ApiRunLogKind | string
  /** 文件名；取正文时原样回传，拼 URL 前记得 encodeURIComponent */
  name: string
  /** 字节数 */
  size: number
  /** 文件 mtime，**秒级**时间戳（本接口特有，其余耗时字段是毫秒） */
  modified: number
}

export interface ApiRunLogsResp {
  project_id: number
  round_no: number
  total: number
  items: ApiRunLogItem[]
}

export interface ApiRunLogContentResp {
  project_id: number
  kind: ApiRunLogKind | string
  name: string
  size: number
  content: string
}

export interface ApiPrdResp {
  success: boolean
  project_id: number
  prd_content: string
}

export interface ApiQaResp {
  success: boolean
  project_id: number
  qa_report: string
}

export type ApiStepStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED' | 'SKIPPED'

export interface ApiStep {
  id: number
  project_id: number
  agent_id: number
  agent_name: string
  round_no: number
  step_no: number
  name: string
  status: ApiStepStatus
  artifact_path?: string | null
  session_id?: string | null
  elapsed_ms?: number | null
  /** 开跑时刻（epoch 毫秒）：RUNNING 时 elapsed_ms 还是 0，用它算实时耗时 */
  started_at_ms?: number | null
  error_code?: string | null
  error?: string | null
}

export interface ApiStepsResp {
  project_id: number
  round_no: number
  total_steps: number
  steps: ApiStep[]
}

export interface ApiRoundsResp {
  project_id: number
  rounds: number[]
  latest_round: number
}

export type ApiAgentMode = 'skill' | 'prompt'

/** 输出契约声明（agents.output_kind / nodes[].output_kind） */
export type ApiOutputKind = 'json_object' | 'json_array' | 'file_blocks' | 'text'

export interface ApiAgent {
  id: number
  user_id: number | null
  role_key: string
  name: string
  mode: ApiAgentMode
  skill_id?: string | null
  system_prompt?: string | null
  /**
   * 一句话说明（§3.1）：skill 角色取自 SKILL.md frontmatter；prompt 角色取提示词首句。
   * 内置角色的 system_prompt 是空的，所以**列表/卡片要展示说明时用这个字段**。
   */
  description?: string | null
  /**
   * 这个 Agent 真正会用的提示词正文：
   * skill 角色 = SKILL.md 正文（去 frontmatter）；prompt 角色 = system_prompt。
   * 详情抽屉展示"System Prompt"时用它（`system_prompt` 对内置角色恒为 null）。
   */
  prompt_text?: string | null
  /** 空 = 用所绑技能的声明（§3.1 / §3.3） */
  output_kind?: ApiOutputKind | null
  builtin: boolean
}

export interface ApiCreateAgentInput {
  user_id: number
  name: string
  mode: ApiAgentMode
  skill_id?: string | null
  system_prompt?: string | null
  output_kind?: ApiOutputKind | null
}

/** PUT /api/agents/{id}（§3.4）：字段全可选；**禁止**改 user_id / role_key */
export interface ApiUpdateAgentInput {
  name?: string
  mode?: ApiAgentMode
  skill_id?: string | null
  system_prompt?: string | null
  output_kind?: ApiOutputKind | null
}

/** 一句话描述 → 规范化提示词（§3.2，只生成不落库） */
export interface ApiAuthorAgentInput {
  user_id: number
  /** 2–500 字：你要它干什么；基于出厂技能整合时 = 在它基础上要改/加什么 */
  agent_goal: string
  /** 可选的提示词草稿，≤12000 字 */
  user_draft?: string | null
  /**
   * 基于哪个**出厂技能**整合（§3.5 的 `bindable=true` 清单）。
   * 传了它就变成"整合"而不是"从零撰写"：服务端把该技能的 SKILL.md 全文当草稿，
   * 要求写手把用户要求**合并进去**（不是追加在末尾），原技能只读不变。
   */
  base_skill_id?: string | null
}

export interface ApiAuthorAgentResp {
  system_prompt: string
  char_count: number
  /** false = 结果不足 10 字，落库会被 §3.3 拒绝 */
  usable: boolean
  /** 写手技能（agent-prompt-authoring） */
  skill_id: string
  /** 基于哪个出厂技能整合的；从零撰写时为空 */
  base_skill_id?: string | null
  /**
   * 新角色该用的**输出契约**：整合时跟随原技能（json_object / file_blocks / …），
   * 从零撰写时为空。前端据此自动设置「输出内容形式」，否则整合后的 JSON/代码块会被当纯文本。
   */
  suggested_output_kind?: ApiOutputKind | null
  session_id: string
  elapsed_seconds: number
  message: string
}

/** 条件节点：ref 只接受 `<节点id>.parsed.<字段>` 这一种形式（§5.2） */
export interface ApiNodeWhen {
  ref: string
  eq: string | number | boolean
}

export interface ApiWorkflowNode {
  id: string
  name?: string
  agent?: { role_key?: string }
  deps?: string[]
  when?: ApiNodeWhen | null
  inputs?: Record<string, unknown>
  output_kind?: ApiOutputKind | string
  artifact_file?: string
  code_dir?: string
  task_note?: string | null
}

export interface ApiWorkflow {
  id: number | null
  name: string
  description?: string | null
  nodes: ApiWorkflowNode[]
  builtin?: boolean
  /**
   * 这张图能不能当"项目模版"（= 是否含「生成 PRD」的节点）。
   * 为 false 时画布"执行"照样能用，但**首页「指定工作流」里选不到** —— `hint` 给出人话原因。
   */
  usable_as_project?: boolean
  hint?: string | null
}

/** 平台技能（`GET /api/skills`）：新建 Agent「高级模式」选 skill_id 的数据源 */
export interface ApiSkillInput {
  name: string
  required: boolean
  description: string
}

export interface ApiSkill {
  skill_id: string
  name: string
  description: string
  /** 技能自己声明的输出契约（= 平台按什么格式解析它的输出，不是"要求模型输出什么"） */
  contract: ApiOutputKind
  inputs: ApiSkillInput[]
  /** false = 平台内部技能，不能填进 agents.skill_id（选了会被 422 拒） */
  bindable: boolean
  not_bindable_reason: string
}

export interface ApiSkillListResp {
  items: ApiSkill[]
  total: number
}

export interface ApiWorkflowListResp {
  items: ApiWorkflow[]
  total: number
}

export interface ApiPlannerResp {
  goal: string
  est_complexity: string
  steps_count: number
  workflow: ApiWorkflow
  round_no: number
  message: string
}
