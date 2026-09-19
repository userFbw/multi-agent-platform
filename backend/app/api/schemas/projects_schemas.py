from typing import List, Optional
from pydantic import BaseModel, Field

class ProjectCreate(BaseModel):
    """前端创建项目入参（仅支持增操作需要的核心字段）"""
    title: str = Field(..., min_length=1, description="项目名称")
    description: str = Field(..., description="项目描述")

class RunLogItem(BaseModel):
    """一份运行日志 / 项目BUG 的清单项（不含正文）"""
    kind: str        # "运行日志" 或 "项目BUG"
    name: str        # 文件名，如 "05-后端开发_r1.md"
    size: int        # 字节
    modified: int    # 修改时间戳（秒）


class RunLogListResponse(BaseModel):
    """运行日志清单"""
    project_id: int
    round_no: int
    total: int
    items: List[RunLogItem]


class RunLogContentResponse(BaseModel):
    """单份日志正文"""
    project_id: int
    kind: str
    name: str
    size: int
    content: str


class ProjectResponse(BaseModel):
    """后端返回给前端的项目基础数据"""
    id: int
    user_id: int
    title: str
    description: str
    status: str
    zip_path: Optional[str] = None

    class Config:
        from_attributes = True


class ArtifactItem(BaseModel):
    """一步 Agent 的产出（清单项，不含正文）"""
    round_no: int
    step_no: int
    name: str                      # 步骤名，如 "审查计算器代码"
    status: str                    # SUCCESS / FAILED / SKIPPED / RUNNING
    agent_name: Optional[str] = None
    skill: Optional[str] = None    # 实际会跑的技能（提示词 Agent 会换算成 generic-prompt-agent）
    artifact_path: Optional[str] = None   # 项目内相对路径；失败/跳过的步骤可能为空
    kind: str = "text"             # markdown / json / code / text / dir
    exists: bool = False           # 物理文件还在不在（被清理过就是 False）
    is_dir: bool = False
    size: int = 0
    files: int = 0                 # 目录产物的文件数
    error_code: Optional[str] = None
    error: Optional[str] = None


class ArtifactListResponse(BaseModel):
    """项目产物清单：每一步 Agent 产出了什么、放在哪"""
    project_id: int
    round_no: int
    total: int
    items: List[ArtifactItem]


class ArtifactContentResponse(BaseModel):
    """单份产物正文（目录产物返回合并后的代码）"""
    project_id: int
    path: str
    name: str
    size: int
    content: str
