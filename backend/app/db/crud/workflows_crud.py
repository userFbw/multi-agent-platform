"""workflows 表 CRUD —— 纯数据库操作。

职责边界：本模块【只写库】，不 import os/shutil、不建/删任何磁盘文件。
物理文件夹 / ZIP 一律归 utils/file_helper.py（调用方负责组合：crud 管库 + file_helper 管盘）。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.workflows_model import WorkflowDB


def create_workflow(db: Session, user_id: int, name: str, description: str = None, nodes: list = None, builtin: int = 0) -> WorkflowDB:
    """【增】创建自定义工作流"""
    db_workflow = WorkflowDB(
        user_id=user_id,
        name=name,
        description=description,
        nodes=nodes or [],
        builtin=builtin,
    )
    db.add(db_workflow)
    db.commit()
    db.refresh(db_workflow)
    return db_workflow


def get_workflow_by_id(db: Session, workflow_id: int) -> Optional[WorkflowDB]:
    """【查】按 ID 获取单个工作流"""
    return db.get(WorkflowDB, workflow_id)


def get_workflows_by_user(db: Session, user_id: int) -> list:
    """【查】某用户的工作流列表（内置 + 自己的）"""
    q = db.query(WorkflowDB).filter(
        (WorkflowDB.user_id == user_id) | (WorkflowDB.builtin == 1)
    )
    return q.order_by(WorkflowDB.id).all()


def update_workflow(db: Session, workflow_id: int, **fields) -> Optional[WorkflowDB]:
    """【改】更新工作流字段"""
    db_workflow = get_workflow_by_id(db, workflow_id)
    if not db_workflow:
        return None
    for k, v in fields.items():
        if hasattr(db_workflow, k):
            setattr(db_workflow, k, v)
    db.commit()
    db.refresh(db_workflow)
    return db_workflow


def delete_workflow(db: Session, workflow_id: int) -> bool:
    """【删】删除自定义工作流（内置工作流不允许删除）"""
    db_workflow = get_workflow_by_id(db, workflow_id)
    if not db_workflow or db_workflow.builtin == 1:
        return False
    db.delete(db_workflow)
    db.commit()
    return True
