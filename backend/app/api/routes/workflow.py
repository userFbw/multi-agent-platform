"""workflow 接口 —— 工作流定义管理与执行

接口清单：
  - GET    /api/workflows                → 获取工作流列表（内置 + 自定义）
  - POST   /api/workflows                → 创建自定义工作流
  - PUT    /api/workflows/{workflow_id}  → 更新自定义工作流
  - DELETE /api/workflows/{workflow_id}  → 删除自定义工作流
  - GET    /api/workflows/{id}/usage     → 这张图被哪些项目引用（影响面）
  - POST   /api/workflows/validate       → 校验工作流定义
  - POST   /api/workflows/execute        → 执行工作流（异步，支持自定义 nodes）
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.engine import get_db, SessionLocal
from app.db.crud import projects_crud
from app.db.crud.projects_crud import get_project_by_id
from app.api.deps import assert_owner, current_user_id
from app.db.crud import workflows_crud
from app.agents.builtin_workflows import BUILTIN_WORKFLOWS, DEFAULT_WORKFLOW_NAME
from app.agents.workflow_engine import (
    WorkflowError, project_template_hint, run_workflow, validate_workflow,
)
from app.agents.orchestrator import step_orchestrator
from app.api.schemas.workflow_schemas import (
    WorkflowUsageResponse,
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowResponse,
    WorkflowListResponse,
    WorkflowExecuteRequest,
    WorkflowExecuteResponse,
)

router = APIRouter(prefix="/api/workflows", tags=["工作流"])


def _builtin_to_response(name: str, wf: dict) -> dict:
    """内置 workflow → 响应字典"""
    hint = project_template_hint(wf)
    return {
        "id": None,
        "name": wf.get("name", name),
        "description": wf.get("description", ""),
        "nodes": wf.get("nodes", []),
        "builtin": True,
        "usable_as_project": not hint,
        "hint": hint or None,
    }


def _db_to_response(wf) -> dict:
    """数据库行 → 响应字典"""
    wf_dict = {"name": wf.name, "nodes": wf.nodes or []}
    hint = project_template_hint(wf_dict)
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "nodes": wf.nodes or [],
        "builtin": wf.builtin == 1,
        "usable_as_project": not hint,
        "hint": hint or None,
    }


# ==================== CRUD ====================

@router.get("", response_model=WorkflowListResponse, summary="获取工作流列表（内置 + 自定义）")
def list_workflows(
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """获取所有工作流：内置 + 自己的自定义。"""
    if uid is None:
        raise HTTPException(status_code=422, detail="缺少身份信息（user_id 或 token）")
    items = [_builtin_to_response(name, wf) for name, wf in BUILTIN_WORKFLOWS.items()]
    db_workflows = workflows_crud.get_workflows_by_user(db, uid)
    items.extend([_db_to_response(w) for w in db_workflows if w.builtin != 1])
    return {"items": items, "total": len(items)}


@router.post("", response_model=WorkflowResponse, status_code=201, summary="创建自定义工作流")
def create_workflow(
    body: WorkflowCreate,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """创建自定义工作流。"""
    if uid is None:
        raise HTTPException(status_code=422, detail="缺少身份信息（user_id 或 token）")
    try:
        validated = validate_workflow({"nodes": body.nodes})
    except WorkflowError as e:
        raise HTTPException(status_code=400, detail=f"工作流定义校验失败: {e}")

    db_wf = workflows_crud.create_workflow(
        db, user_id=uid,
        name=body.name,
        description=body.description,
        nodes=body.nodes,
    )
    return _db_to_response(db_wf)


@router.get("/{workflow_id}/usage", response_model=WorkflowUsageResponse,
            summary="这张图被哪些项目引用（改动/删除前的影响面）")
def workflow_usage(
    workflow_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """**改图之前先问一句"谁在用它"**。

    项目在建的时候就记下了 `projects.workflow_id`，审批与迭代都沿用它：
      · 保存修改 → 那些项目**以后再跑就是新图**（正在等审批的项目，开发段直接用新图）；
      · 删除这张图 → 它们再执行会**直接失败**（"项目选定的工作流已不可用"），
        平台不会悄悄换回默认模版（宁可明确报错，也别让人以为跑的还是原来那张）。
    所以画布上改节点 / 删节点之前，要把这两件事说清楚。
    """
    if uid is None:
        raise HTTPException(status_code=422, detail="缺少身份信息（user_id 或 token）")
    wf = workflows_crud.get_workflow_by_id(db, workflow_id)
    assert_owner(wf, uid, "工作流")

    used = [p for p in projects_crud.get_projects_by_user(db, uid)
            if p.workflow_id == workflow_id]
    projects = [{"id": p.id, "title": p.title, "status": p.status} for p in used]
    return {
        "workflow_id": workflow_id, "name": wf.name, "projects": projects,
        "project_count": len(projects),
        "has_active_project": any(p["status"] in ("RUNNING", "PENDING_APPROVAL") for p in projects),
    }


@router.put("/{workflow_id}", response_model=WorkflowResponse, summary="更新自定义工作流")
def update_workflow(
    workflow_id: int,
    body: WorkflowUpdate,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """更新自定义工作流（内置不允许修改；只能改自己的）"""
    db_wf = workflows_crud.get_workflow_by_id(db, workflow_id)
    if db_wf and db_wf.builtin == 1:
        raise HTTPException(status_code=403, detail="内置工作流不允许修改")
    assert_owner(db_wf, uid, "工作流")

    if body.nodes is not None:
        try:
            validate_workflow({"nodes": body.nodes})
        except WorkflowError as e:
            raise HTTPException(status_code=400, detail=f"工作流定义校验失败: {e}")

    update_fields = {}
    if body.name is not None:
        update_fields["name"] = body.name
    if body.description is not None:
        update_fields["description"] = body.description
    if body.nodes is not None:
        update_fields["nodes"] = body.nodes

    updated = workflows_crud.update_workflow(db, workflow_id, **update_fields)
    return _db_to_response(updated)


@router.delete("/{workflow_id}", status_code=204, summary="删除自定义工作流")
def delete_workflow(
    workflow_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """删除自定义工作流（内置不允许删除；只能删自己的）"""
    db_wf = workflows_crud.get_workflow_by_id(db, workflow_id)
    if db_wf and db_wf.builtin == 1:
        raise HTTPException(status_code=403, detail="内置工作流不允许删除")
    assert_owner(db_wf, uid, "工作流")
    workflows_crud.delete_workflow(db, workflow_id)
    return None


# ==================== Validate / Execute ====================

@router.post("/validate", summary="校验工作流定义")
def validate_workflow_endpoint(workflow: dict):
    """校验工作流 JSON 定义是否合法（不执行）"""
    try:
        validated = validate_workflow(workflow)
        return {"valid": True, "workflow": validated}
    except WorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/execute", response_model=WorkflowExecuteResponse, summary="执行工作流")
async def execute_workflow(
    body: WorkflowExecuteRequest,
    background_tasks: BackgroundTasks,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """执行工作流：支持内置 workflow_name 或自定义 nodes"""
    project = get_project_by_id(db, body.project_id)
    assert_owner(project, uid, "项目")


    # 优先使用自定义 nodes，否则按 workflow_name 查找
    workflow = None
    wf_name = body.workflow_name or DEFAULT_WORKFLOW_NAME
    if body.nodes:
        workflow = {"name": wf_name, "nodes": body.nodes}
    else:
        workflow = BUILTIN_WORKFLOWS.get(wf_name)

    if not workflow:
        raise HTTPException(status_code=404, detail=f"工作流 '{wf_name}' 不存在")

    from app.db.crud import project_steps_crud
    round_no = body.round_no or project_steps_crud.get_latest_round(db, body.project_id)

    async def _run():
        db = SessionLocal()
        try:
            result = await run_workflow(
                db,
                project=project,
                user_id=project.user_id,
                workflow=workflow,
                seeds=body.seeds,
                round_no=round_no,
            )
            # 收尾与主链路一致：登记 project_agents + 写运行日志总览 + 打包 ZIP + 置 COMPLETED。
            # 不做的话前端点「执行」后项目会一直停在 RUNNING，四个取产物的接口全 404。
            # ★ 必须用异步入口：收尾里建 venv/装依赖/起应用最坏 ~150 秒，同步调会把事件循环
            #   占死 —— 客户此时刷新页面/点按钮就是"一直转圈没反应"（真机踩到）。
            await step_orchestrator.finish_project_async(db, project, result)
        except Exception as e:
            print(f"[workflow] 执行失败: {e}")
        finally:
            db.close()

    background_tasks.add_task(_run)

    return {
        "workflow": wf_name,
        "round_no": round_no,
        "steps": [],
        "node_results": {},
        "message": "工作流已提交异步执行",
    }
