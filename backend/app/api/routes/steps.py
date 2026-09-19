"""project_steps 接口 —— 项目步骤执行明细（状态卡片/会话日志/耗时）

接口清单：
  - GET /api/projects/{project_id}/steps             → 获取项目步骤列表（默认最新轮次）
  - GET /api/projects/{project_id}/steps/latest      → 获取最新轮次的步骤
  - GET /api/projects/{project_id}/steps/rounds      → 获取所有轮次列表
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.crud import project_steps_crud, agents_crud
from app.db.crud.projects_crud import get_project_by_id
from app.api.deps import assert_owner, current_user_id
from app.api.schemas.agents_schemas import ProjectStepsResponse

router = APIRouter(prefix="/api/projects/{project_id}/steps", tags=["项目步骤"])


def _enrich_step(step: project_steps_crud.ProjectStepDB, db: Session) -> dict:
    """步骤行 → 响应字典（关联查询 agent_name）"""
    agent_name = None
    if step.agent_id:
        agent = agents_crud.get_by_id(db, step.agent_id)
        agent_name = agent.name if agent else None
    return {
        "id": step.id,
        "project_id": step.project_id,
        "agent_id": step.agent_id,
        "agent_name": agent_name,
        "round_no": step.round_no,
        "step_no": step.step_no,
        "name": step.name,
        "status": step.status,
        "artifact_path": step.artifact_path,
        "session_id": step.session_id,
        "elapsed_ms": step.elapsed_ms,
        # 开跑时刻（epoch ms）：RUNNING 期间 elapsed_ms 还是 0，前端用它算实时耗时
        "started_at_ms": step.started_at_ms,
        "error_code": step.error_code,
        "error": step.error,
    }


def _require_project(project_id: int, uid: Optional[int], db: Session):
    """取项目并校验归属：不存在或不属于当前用户 → 404。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")
    return project


@router.get("", response_model=ProjectStepsResponse, summary="获取项目步骤列表（默认最新轮次）")
def get_project_steps(
    project_id: int,
    round_no: Optional[int] = Query(None, description="轮次编号，不传则返回最新轮次"),
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """获取项目步骤列表，用于前端步骤卡片渲染"""
    _require_project(project_id, uid, db)

    # 如果没指定轮次，获取最新轮次
    if round_no is None:
        round_no = project_steps_crud.get_latest_round(db, project_id)

    steps = project_steps_crud.get_steps(db, project_id, round_no=round_no)
    return {
        "project_id": project_id,
        "round_no": round_no,
        "total_steps": len(steps),
        "steps": [_enrich_step(s, db) for s in steps],
    }


@router.get("/latest", response_model=ProjectStepsResponse, summary="获取最新轮次的步骤")
def get_latest_round_steps(
    project_id: int,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """获取项目最新轮次的全部步骤"""
    _require_project(project_id, uid, db)

    round_no = project_steps_crud.get_latest_round(db, project_id)
    steps = project_steps_crud.get_steps(db, project_id, round_no=round_no)
    return {
        "project_id": project_id,
        "round_no": round_no,
        "total_steps": len(steps),
        "steps": [_enrich_step(s, db) for s in steps],
    }


@router.get("/rounds", summary="获取所有轮次列表")
def get_rounds_list(
    project_id: int,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """获取项目所有执行轮次列表"""
    _require_project(project_id, uid, db)

    # 查询所有不同的 round_no
    from sqlalchemy import distinct
    from app.db.models.project_steps_model import ProjectStepDB

    rounds = (
        db.query(distinct(ProjectStepDB.round_no))
        .filter(ProjectStepDB.project_id == project_id)
        .order_by(ProjectStepDB.round_no)
        .all()
    )
    round_list = [r[0] for r in rounds]

    return {
        "project_id": project_id,
        "rounds": round_list,
        "latest_round": round_list[-1] if round_list else 1,
    }
