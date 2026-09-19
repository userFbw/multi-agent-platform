"""project_steps 表 CRUD —— 一次执行的每个步骤（步骤卡片 / 会话号 / 产物 / 耗时）。

状态取值：PENDING / RUNNING / SUCCESS / FAILED / SKIPPED
（引擎建行时写 PENDING，跑完直接写终态，RUNNING 目前不会落库。）
每轮（round_no）生成一组 step_no=1..N 的行；引擎写，看板与路由只读。
"""
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.project_steps_model import ProjectStepDB

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"


def create_step(
    db: Session,
    project_id: int,
    agent_id: Optional[int],
    name: str,
    *,
    round_no: int = 1,
    step_no: int = 1,
) -> ProjectStepDB:
    """建一行 PENDING 步骤。"""
    row = ProjectStepDB(
        project_id=project_id, agent_id=agent_id, round_no=round_no,
        step_no=step_no, name=name, status=STATUS_PENDING,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def set_status(db: Session, step: ProjectStepDB, status: str) -> ProjectStepDB:
    step.status = status
    db.commit()
    db.refresh(step)
    return step


def start_step(db: Session, step: ProjectStepDB) -> ProjectStepDB:
    """步骤开跑（PENDING → RUNNING）。

    为什么必须有这一步：前端**5 秒轮询**「当前进行到哪一步、由哪个 Agent 负责」，
    判据就是"有一行 RUNNING"（`AgentStep.vue` 的 `isRunning` 样式早就写好了）。
    以前引擎只写 PENDING → SUCCESS/FAILED，中途没有任何状态可看，长节点（实测 95–115 秒）
    在界面上一直是"排队中"。
    """
    step.status = STATUS_RUNNING
    step.started_at_ms = int(time.time() * 1000)
    db.commit()
    db.refresh(step)
    return step


def finish_step(
    db: Session,
    step: ProjectStepDB,
    status: str,
    *,
    session_id: Optional[str] = None,
    elapsed_ms: int = 0,
    artifact_path: Optional[str] = None,
    error_code: Optional[str] = None,
    error: Optional[str] = None,
) -> ProjectStepDB:
    """步骤结束（SUCCESS/FAILED/SKIPPED）并落 会话号/耗时/产物路径/错误码。"""
    step.status = status
    if session_id is not None:
        step.session_id = session_id
    if elapsed_ms:
        step.elapsed_ms = elapsed_ms
    elif step.started_at_ms:
        # 调用方没给耗时（如"非预期异常"兜底路径）→ 用开跑时刻算，别让耗时显示成 0
        step.elapsed_ms = max(0, int(time.time() * 1000) - step.started_at_ms)
    if artifact_path is not None:
        step.artifact_path = artifact_path
    if error_code is not None:
        step.error_code = error_code
    if error is not None:
        step.error = error
    db.commit()
    db.refresh(step)
    return step


def next_step_no(db: Session, project_id: int, round_no: int = 1) -> int:
    """本轮已用的最大 step_no + 1（无行则 1）。"""
    last = (
        db.query(ProjectStepDB)
        .filter(ProjectStepDB.project_id == project_id, ProjectStepDB.round_no == round_no)
        .order_by(ProjectStepDB.step_no.desc())
        .first()
    )
    return (last.step_no + 1) if last else 1


def get_steps(db: Session, project_id: int, round_no: int = 1) -> list:
    """某项目某轮的全部步骤（按 step_no 升序）。"""
    return (
        db.query(ProjectStepDB)
        .filter(ProjectStepDB.project_id == project_id, ProjectStepDB.round_no == round_no)
        .order_by(ProjectStepDB.step_no)
        .all()
    )


def get_latest_round(db: Session, project_id: int) -> int:
    """该项目当前最大轮数（无记录返回 1）。"""
    last = (
        db.query(ProjectStepDB)
        .filter(ProjectStepDB.project_id == project_id)
        .order_by(ProjectStepDB.round_no.desc())
        .first()
    )
    return last.round_no if last else 1

def reconcile_interrupted_runs(db) -> dict:
    """平台启动时对账：把"上次进程死掉时留下的 RUNNING"收尾（S2-7 的缺口）。

    为什么要它：运行状态活在**平台进程的内存**里（会话注册表 + asyncio 任务）。
    平台一重启，这些全没了，但数据库里的 RUNNING 行还在 —— 客户看到项目永远"进行中"，
    点终止也没反应（注册表空的）。所以启动时统一把它们标成中断，让界面说实话。
    返回 {"steps": n, "projects": n}。
    """
    from app.db.models.projects_model import ProjectDB

    rows = db.query(ProjectStepDB).filter(ProjectStepDB.status == "RUNNING").all()
    for r in rows:
        finish_step(db, r, STATUS_FAILED,
                    error="运行被中断：平台后端重启导致这次会话丢失（这一步没跑完，已完成的产物保留）。"
                          "点「重新跑开发链」可从当前进度继续。")
    projects = db.query(ProjectDB).filter(ProjectDB.status == "RUNNING").all()
    for pr in projects:
        pr.status = "FAILED"
    db.commit()
    return {"steps": len(rows), "projects": len(projects)}
