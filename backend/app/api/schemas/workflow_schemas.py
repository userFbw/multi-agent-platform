"""workflow 相关 Schema —— 工作流定义与执行"""
from typing import Optional, List, Any, Dict
from pydantic import BaseModel


class WorkflowNodeAgent(BaseModel):
    """节点 agent 配置"""
    id: Optional[int] = None
    role_key: Optional[str] = None
    user_id: Optional[int] = None


class WorkflowNodeWhen(BaseModel):
    """节点条件配置"""
    ref: str
    eq: Any


class WorkflowNode(BaseModel):
    """工作流节点定义"""
    id: str
    name: str
    agent: WorkflowNodeAgent
    deps: List[str] = []
    when: Optional[WorkflowNodeWhen] = None
    inputs: Dict[str, Any] = {}
    output_kind: Optional[str] = None
    task_note: Optional[str] = None
    artifact_file: Optional[str] = None
    code_dir: Optional[str] = None
    system_prompt: Optional[str] = None


# 入参用**普通对象**而不是 `WorkflowNode` 模型：下游 `validate_workflow()` 按 dict 写、
# `workflows.nodes` 是 JSON 列，两边都只认 dict。以前这里写成 `List[WorkflowNode]` 的后果是
# 保存工作流**必 500**，而且有两个失败点：
#   ① validate_workflow → AttributeError: 'WorkflowNode' object has no attribute 'get'
#   ② 就算①修好，落库 → StatementError: Object of type WorkflowNode is not JSON serializable
# 这也与同文件的 `/validate`、`/execute` 口径一致（它们本来就是 List[Dict[str, Any]]）。
# 响应侧仍然用 `WorkflowNode` 做校验（见 WorkflowResponse）。
class WorkflowCreate(BaseModel):
    """创建自定义工作流请求体"""
    name: str
    description: Optional[str] = None
    nodes: List[Dict[str, Any]]


class WorkflowUpdate(BaseModel):
    """更新自定义工作流请求体"""
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None


class WorkflowResponse(BaseModel):
    """工作流响应体"""
    id: Optional[int] = None   # 内置工作流为 None；自定义工作流用于 PUT/DELETE
    name: str
    description: Optional[str] = None
    nodes: List[WorkflowNode]
    builtin: bool = False  # 是否为平台内置
    # 这张图能不能当"项目模版"（= 是否含「生成 PRD」的节点）。为 false 时 hint 给出原因，
    # 前端在画布保存后 / 工作流列表里提示用户（否则用户只会发现"首页选不到它"）。
    usable_as_project: bool = True
    hint: Optional[str] = None


class WorkflowListResponse(BaseModel):
    """工作流列表响应体"""
    items: List[WorkflowResponse]
    total: int


class WorkflowExecuteRequest(BaseModel):
    """执行工作流请求体"""
    project_id: int
    round_no: Optional[int] = None
    seeds: Dict[str, Any] = {}
    workflow_name: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None  # 自定义节点（优先于 workflow_name）


class WorkflowExecuteResponse(BaseModel):
    """工作流执行响应体"""
    workflow: str
    round_no: int
    steps: List[Dict[str, Any]]
    node_results: Dict[str, Any]


class WorkflowUsageProject(BaseModel):
    """正在用这张图的项目"""
    id: int
    title: str
    status: str          # INITIAL / RUNNING / PENDING_APPROVAL / COMPLETED / FAILED


class WorkflowUsageResponse(BaseModel):
    """改动/删除这张图之前的影响面：哪些项目还在用它"""
    workflow_id: int
    name: str
    projects: List[WorkflowUsageProject] = []
    project_count: int = 0
    # 有待审批/运行中的项目时最危险：它们下一次执行就用新图（或直接失败）
    has_active_project: bool = False
