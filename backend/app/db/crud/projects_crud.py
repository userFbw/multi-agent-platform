"""projects 表 CRUD —— 只写库，不碰磁盘。

物理文件夹 / ZIP 一律归 storage/file_helper（调用方负责组合）。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.projects_model import ProjectDB

# update 白名单：与 ProjectDB 实际列严格一致，防止误改 user_id 等关键归属列 / 写入不存在字段
_EDITABLE_FIELDS = {
    "title", "description", "status", "zip_path", "pass_first_try",
    "workflow_id", "workflow_name",      # 换图（审批/迭代时显式覆盖并记下来）
    "planned_workflow",                  # agent 模式：编排官出的图（重跑复用；显式换图时清空）
}


def create_project(db: Session, user_id: int, title: str, description: str,
                   workflow_id: int = None, workflow_name: str = None,
                   mode: str = "workflow") -> ProjectDB:
    """【增】只写 projects 行，初始状态 INITIAL；磁盘文件夹由 file_helper 负责

    入参是普通值而不是 API 的 pydantic DTO —— db 层不该知道上层 DTO 长什么样。
    workflow_id / workflow_name = 该项目选定的工作流：**建项目时就定下**，
    之后审批与迭代都沿用同一张图（图是项目的属性，不靠前端每次带回来）。
    mode = 编排模式："workflow"（图先存在）/ "agent"（审批后由 Planner 出图）。
    """
    db_project = ProjectDB(
        user_id=user_id,
        title=title,
        description=description,
        status="INITIAL",
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        mode=mode or "workflow",
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


def get_project_by_id(db: Session, project_id: int) -> Optional[ProjectDB]:
    """【查】按 ID 获取单个项目"""
    return db.get(ProjectDB, project_id)


def get_projects_by_user(
    db: Session, user_id: int, *, status: Optional[str] = None
) -> list:
    """【查】某用户的项目列表（可选按状态过滤；按 id 升序返回，与老行为一致）"""
    q = db.query(ProjectDB).filter(ProjectDB.user_id == user_id)
    if status:
        q = q.filter(ProjectDB.status == status)
    return q.order_by(ProjectDB.id).all()


def update_project_status_or_zip(
    db: Session,
    project_id: int,
    status: Optional[str] = None,
    zip_path: Optional[str] = None,
) -> Optional[ProjectDB]:
    """【改】更新状态 / 打包路径（老 orchestrator 兼容入口）"""
    db_project = get_project_by_id(db, project_id)
    if not db_project:
        return None
    if status is not None:
        db_project.status = status
    if zip_path is not None:
        db_project.zip_path = zip_path
    db.commit()
    db.refresh(db_project)
    return db_project


def update_project_fields(db: Session, project_id: int, **fields) -> Optional[ProjectDB]:
    """【改】白名单通用更新（title/description/status/zip_path/pass_first_try）"""
    unknown = set(fields) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"不允许更新的字段: {sorted(unknown)}")
    db_project = get_project_by_id(db, project_id)
    if not db_project:
        return None
    for k, v in fields.items():
        setattr(db_project, k, v)
    db.commit()
    db.refresh(db_project)
    return db_project


def delete_project(db: Session, project_id: int) -> bool:
    """【删】只删 projects 行；project_agents 由外键 ON DELETE CASCADE 级联清空；
    磁盘文件夹/ZIP 由 file_helper.delete_project_files 负责"""
    db_project = get_project_by_id(db, project_id)
    if not db_project:
        return False
    db.delete(db_project)
    db.commit()
    return True
