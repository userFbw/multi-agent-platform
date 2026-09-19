from typing import Optional, List
from pydantic import BaseModel


# ==================== Project Step 相关 Schema ====================

class ProjectStepResponse(BaseModel):
    """项目步骤响应体"""
    id: int
    project_id: int
    agent_id: Optional[int] = None
    agent_name: Optional[str] = None  # 从 agents 表关联查询
    round_no: int
    step_no: int
    name: str
    status: str  # PENDING / RUNNING / SUCCESS / FAILED / SKIPPED
    artifact_path: Optional[str] = None
    session_id: Optional[str] = None
    elapsed_ms: Optional[int] = None
    started_at_ms: Optional[int] = None  # 开跑时刻（epoch ms）；RUNNING 时用它算实时耗时
    error_code: Optional[str] = None   # 稳定机器错误码，供前端分支判断
    error: Optional[str] = None        # 可读错误文案，供前端展示

    class Config:
        from_attributes = True


class ProjectStepsResponse(BaseModel):
    """项目步骤列表响应体"""
    project_id: int
    round_no: int
    total_steps: int
    steps: List[ProjectStepResponse]


class AgentAuthorRequest(BaseModel):
    """一句话描述 → 提示词 请求体（两种用法）

    · 从零撰写：只给 `agent_goal`；
    · **基于出厂技能整合**：给 `base_skill_id` —— 服务端把该技能的 SKILL.md 全文当草稿，
      要求写手把用户要求**合并进去**（不是追加在末尾）。原技能只读。
    """
    user_id: int
    agent_goal: str                     # 你要它干什么 / 在出厂技能基础上要改什么
    user_draft: Optional[str] = None    # 用户已有的提示词草稿（可选，有则在其基础上审校改写）
    base_skill_id: Optional[str] = None # 基于哪个出厂技能整合（见 §3.5 的 bindable 清单）


class AgentAuthorResponse(BaseModel):
    """生成的提示词（**不落库**，由用户过目后自行 POST /api/agents 落库）"""
    system_prompt: str
    char_count: int
    usable: bool                        # 是否满足 POST /api/agents 的下限（≥10 字）
    skill_id: str                       # 写手技能（agent-prompt-authoring）
    base_skill_id: Optional[str] = None # 基于哪个出厂技能整合的（从零撰写为空）
    # 新角色该用的输出契约：整合时**跟随原技能**（json_object / file_blocks / …），否则空
    suggested_output_kind: Optional[str] = None
    session_id: str = ""
    elapsed_seconds: float = 0
    message: str = ""


# ==================== Agent Registry 相关 Schema ====================

class AgentRegistryCreate(BaseModel):
    """创建自定义 Agent 请求体"""
    user_id: int
    name: str
    mode: str  # 'skill' | 'prompt'
    skill_id: Optional[str] = None
    system_prompt: Optional[str] = None
    output_kind: Optional[str] = None  # 输出契约覆盖，空 = 用所绑技能的声明


class AgentRegistryUpdate(BaseModel):
    """更新 Agent 请求体（禁止改 user_id / role_key）"""
    name: Optional[str] = None
    mode: Optional[str] = None
    skill_id: Optional[str] = None
    system_prompt: Optional[str] = None
    output_kind: Optional[str] = None


class AgentRegistryResponse(BaseModel):
    """Agent 响应体"""
    id: int
    user_id: Optional[int] = None
    role_key: str
    name: str
    mode: str
    skill_id: Optional[str] = None
    system_prompt: Optional[str] = None
    # 展示用（见 app/agents/skill_meta.py）：
    #   description = 一句话说明；prompt_text = 这个 Agent 真正会用的提示词正文
    #   内置 skill 角色取自 SKILL.md（agents 表里没有它们的提示词）；prompt 角色就是 system_prompt
    description: Optional[str] = None
    prompt_text: Optional[str] = None
    output_kind: Optional[str] = None
    builtin: bool = False  # 是否为平台内置（user_id 为空时为 True）

    class Config:
        from_attributes = True


class AgentUsageWorkflow(BaseModel):
    """引用了这个 Agent 的一张工作流"""
    id: int
    name: str
    nodes: List[str] = []          # 图里用到它的是哪些节点


class AgentUsageResponse(BaseModel):
    """删除自定义 Agent 前的**影响面**：谁还在用它"""
    agent_id: int
    name: str
    role_key: str
    deletable: bool
    reason: Optional[str] = None   # deletable=false 时的人话原因
    workflows: List[AgentUsageWorkflow] = []
    project_count: int = 0
    project_titles: List[str] = []
