"""planner 接口 —— 自然语言拆解 → 转 workflow → 执行

接口清单：
  - POST /api/workflows/planner  → 接收自然语言需求，拆解后转 workflow 并执行

本文件只做接口该做的事：接请求 → 调 `app.agents.planner_runner` → 返响应。
真正的编排逻辑（技能规格表、plan → 图、两阶段执行）都在 agents 层。
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, SessionLocal
from app.db.crud.projects_crud import get_project_by_id
from app.api.deps import assert_owner, current_user_id
from app.db.crud import project_steps_crud
from app.agents.orchestrator import read_project_prd
from app.agents.planner_runner import compose_plan, convert_plan_to_workflow, execute_plan

router = APIRouter(prefix="/api/workflows", tags=["Planner"])


class PlannerRequest(BaseModel):
    """Planner 请求体

    `user_requirement` 现在只是**兜底**：正常情况下编排官读的是项目里那份已审批的 PRD
    （Agent 模式 = 建项目跑 PM → 审批 → 编排官读 PRD 出图 → 执行那一张图）。
    保留它，是为了"编排页手动对一个已有项目出图"这种入口在还没有 PRD 时也能用。
    """
    project_id: int
    user_requirement: str = ""


@router.post("/planner", summary="自然语言拆解 → 转 workflow → 执行")
async def planner_endpoint(
    body: PlannerRequest,
    background_tasks: BackgroundTasks,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """项目已有 PRD 就读 PRD；没有才用请求里的需求 → 编排官出计划 → 转 workflow 并执行"""
    project = get_project_by_id(db, body.project_id)
    assert_owner(project, uid, "项目")

    round_no = project_steps_crud.get_latest_round(db, body.project_id)
    prd_text = read_project_prd(db, project)      # 没有 PM 产物时返回 ""

    # 1) 阶段一：编排决策（在册 Agent 的一次正常节点执行）
    try:
        plan, _ = await compose_plan(
            db, project=project, prd_content=prd_text,
            user_requirement=body.user_requirement, round_no=round_no,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Planner 执行失败: {e}")

    # 2) 把 plan 翻成图（PM 步骤会被剔掉；PRD 走 seed.prd_content）
    workflow = convert_plan_to_workflow(plan, db, project.user_id, project_title=project.title)

    # 3) 阶段二：执行生成的图 + 收尾（后台，避免阻塞请求）
    seeds = {"prd_content": prd_text, "user_requirement": body.user_requirement}

    async def _run():
        db = SessionLocal()
        try:
            await execute_plan(db, project=project, workflow=workflow, seeds=seeds, round_no=round_no)
        except Exception as e:
            print(f"[planner] workflow 执行失败: {e}")
        finally:
            db.close()

    background_tasks.add_task(_run)

    return {
        "goal": plan.get("goal", ""),
        "est_complexity": plan.get("est_complexity", ""),
        "steps_count": len(plan.get("steps", [])),
        "workflow": workflow,
        "round_no": round_no,
        "message": "任务编排官已出编排计划，workflow 已提交异步执行",
    }
