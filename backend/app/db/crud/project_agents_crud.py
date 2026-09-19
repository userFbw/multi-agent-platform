"""project_agents 表 CRUD —— 按角色登记 AI 成果（老表，方法名保持兼容）。

登记每个角色在某项目上的产物路径、文本快照、会话号与耗时。
其中的 path 是 /prd、/qa-report、/preview-url 三个接口取文件的唯一索引。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.agents_model import ProjectAgentDB


def add_project_agent_result(
    db: Session,
    project_id: int,
    role: str,
    agent_name: str,
    elapsed_time: int = 0,
    final_output: Optional[str] = None,
    path: Optional[str] = None,
    session_id: Optional[str] = None,
) -> ProjectAgentDB:
    """【增】录入一个 AI 角色的成果记录"""
    db_agent = ProjectAgentDB(
        project_id=project_id,
        role=role,
        agent_name=agent_name,
        elapsed_time=elapsed_time,
        final_output=final_output,
        path=path,
        session_id=session_id,
    )
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent


def get_project_agents(db: Session, project_id: int) -> list:
    """【查】某项目下所有角色的成果列表"""
    return db.query(ProjectAgentDB).filter(ProjectAgentDB.project_id == project_id).all()


def get_agent_by_id(db: Session, agent_id: int) -> Optional[ProjectAgentDB]:
    """【查】按记录 ID 获取单条成果"""
    return db.get(ProjectAgentDB, agent_id)


def get_by_project_and_role(db: Session, project_id: int, role: str) -> Optional[ProjectAgentDB]:
    """【查】某项目下指定角色的一条成果（upsert 前置查询）"""
    return (
        db.query(ProjectAgentDB)
        .filter(ProjectAgentDB.project_id == project_id, ProjectAgentDB.role == role)
        .first()
    )


def update_agent_output(
    db: Session,
    agent_id: int,
    final_output: Optional[str] = None,
    path: Optional[str] = None,
    session_id: Optional[str] = None,
    additional_time: int = 0,
) -> Optional[ProjectAgentDB]:
    """【改】更新成果的文本/路径/会话号，并累加耗时（毫秒）"""
    db_agent = get_agent_by_id(db, agent_id)
    if not db_agent:
        return None
    if final_output is not None:
        db_agent.final_output = final_output
    if path is not None:
        db_agent.path = path
    if session_id is not None:
        db_agent.session_id = session_id
    if additional_time > 0:
        db_agent.elapsed_time += additional_time
    db.commit()
    db.refresh(db_agent)
    return db_agent


def upsert_by_role(
    db: Session,
    project_id: int,
    role: str,
    agent_name: str,
    final_output: Optional[str] = None,
    path: Optional[str] = None,
    session_id: Optional[str] = None,
    additional_time: int = 0,
) -> ProjectAgentDB:
    """【改/增】按 (project_id, role) 原子性"查→改或插"。

    老 orchestrator 的"先查 agents 再 add/update"可收敛到这一个方法。
    """
    agent = get_by_project_and_role(db, project_id, role)
    if agent:
        return update_agent_output(
            db, agent.id,
            final_output=final_output, path=path, session_id=session_id,
            additional_time=additional_time,
        )
    return add_project_agent_result(
        db, project_id=project_id, role=role, agent_name=agent_name,
        elapsed_time=additional_time, final_output=final_output,
        path=path, session_id=session_id,
    )


def delete_agent_result(db: Session, agent_id: int) -> bool:
    """【删】删除单条成果记录"""
    db_agent = get_agent_by_id(db, agent_id)
    if not db_agent:
        return False
    db.delete(db_agent)
    db.commit()
    return True
