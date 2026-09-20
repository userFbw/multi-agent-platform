"""projects 接口 —— 项目主线（创建 / 审批 / 开发 / 迭代 / 预览 / 下载）

接口清单：
  - POST   /api/projects/create                    → 创建项目并异步启动 PM
  - POST   /api/projects/{project_id}/approve      → 人工审批（同意→开发链；驳回→带意见重跑 PM）
  - POST   /api/projects/{project_id}/revise       → 按修改意见重新生成
  - GET    /api/projects/                          → 当前用户的项目列表
  - GET    /api/projects/{project_id}              → 项目详情（前端 5 秒轮询用）
  - GET    /api/projects/{project_id}/artifacts    → 每一步 Agent 的产出清单
  - GET    /api/projects/{project_id}/artifacts/content?path= → 读单份产物正文
  - GET    /api/projects/{project_id}/prd          → PRD 全文
  - GET    /api/projects/{project_id}/qa-report    → 测试报告全文
  - GET    /api/projects/{project_id}/preview-url  → 网页产物预览 URL
  - GET    /api/projects/{project_id}/download     → 下载 ZIP 交付包
  - DELETE /api/projects/{project_id}              → 删除项目（数据库 + 磁盘产物）

隔离：按 id 操作的端点统一校验 `project.user_id == 当前用户`（见 app/api/deps.py）。
产物路径：`exports/u<user_id>/p<project_id>/`，一律由 file_helper 定位。
"""
import asyncio
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.agents import node_inputs
from app.agents.orchestrator import step_orchestrator, read_project_prd
from app.runner import env as app_env
from app.runner import process as app_runner
from app.agents.workflow_engine import (
    WorkflowError, ensure_prd_node, resolve_workflow, shape_mismatch_warning, validate_workflow,
)
from app.api.deps import assert_owner, current_user_id
from app.db.crud import agents_crud, project_steps_crud
from app.api.schemas.projects_schemas import (
    ArtifactContentResponse,
    ArtifactListResponse,
    ProjectCreate,
    RunLogContentResponse,
    RunLogListResponse,
)
from app.db.crud.project_agents_crud import get_project_agents
from app.db.crud.projects_crud import (
    create_project,
    delete_project,
    get_project_by_id,
    get_projects_by_user,
    update_project_fields,
    update_project_status_or_zip,      # 「终止运行」对账时要把项目收尾为 STOPPED
)
from app.db.crud.users_crud import get_user_by_id
from app.db.engine import get_db
from app.storage.file_helper import file_helper

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("/create")
async def api_create_project(
    project_in: ProjectCreate,
    background_tasks: BackgroundTasks,
    workflow_id: int | None = None,
    workflow_name: str = "",
    mode: str = "workflow",
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """创建项目：选定**编排模式**（+ 可选工作流）→ 写库 → 异步跑第一段（PM 出 PRD）。

    两种模式**审批闸门都在 PM 之后**，PM 都只跑一次，差别只在"审批通过之后干什么"：

    | mode | 图从哪来 | 审批后 |
    |---|---|---|
    | `workflow`（默认） | 建项目时就选好（`workflow_id`/`workflow_name`） | 跑这张图的其余节点 |
    | `agent` | **审批之后**由编排官读 PRD 现场出图 | 编排官出图 → 跑它 |

    `workflow` 模式下，图必须含"产出 PRD 的节点"（没有它就没有审批对象）。
    """

    from app.core.reload_guard import assert_reload_safe

    assert_reload_safe()          # --reload 下长任务会被重载打断 → 直接拒绝（真机踩到）
    if uid is None:
        raise HTTPException(status_code=422, detail="缺少身份信息（user_id 或 token）")
    if not get_user_by_id(db, uid):
        raise HTTPException(status_code=404, detail="用户不存在")
    if mode not in ("workflow", "agent"):
        raise HTTPException(status_code=422, detail="mode 只能是 workflow 或 agent")

    try:
        workflow = resolve_workflow(db, user_id=uid,
                                    workflow_id=workflow_id, workflow_name=workflow_name)
        validate_workflow(workflow)
        ensure_prd_node(workflow)          # 没有 PRD 节点 → 400，别等跑起来才炸
    except WorkflowError as e:
        raise HTTPException(status_code=400, detail=f"工作流不可用: {e}")

    # 自定义图记 id（改名字也不影响解析），内置模版记 name（内置没有 id）
    project = create_project(db, uid, project_in.title, project_in.description,
                             workflow_id=workflow_id,
                             workflow_name="" if workflow_id is not None else workflow["name"],
                             mode=mode)
    file_helper.ensure_project_folder(uid, project.id)

    background_tasks.add_task(step_orchestrator.step_1_run_pm, project.id)

    return {
        "success": True,
        "message": (
            f"项目创建成功，已按「{workflow['name']}」开始规划需求..."
            if mode == "workflow"
            else "项目创建成功，产品经理正在规划需求（通过审批后由编排官自行决定开发节点）..."
        ),
        "project_id": project.id,
        "status": project.status,
        "mode": mode,
        "workflow": workflow["name"],
    }


@router.post("/{project_id}/approve")
async def api_approve_project(
    project_id: int,
    approved: bool,
    feedback: str = "",
    workflow_id: int | None = None,
    workflow_name: str = "",
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """人工审批：同意则继续（按项目的编排模式）；驳回则合并修改意见重跑 PRD 段。

    **闸门固定在 PM 之后**，两种模式一致；通过之后干什么由项目的 `mode` 决定：

        mode=workflow → 跑建项目时选定的那张图的其余节点
        mode=agent    → 编排官读 PRD 现场出图，再跑那张图

    图是**项目属性**（`projects.workflow_id / workflow_name`），所以这里默认沿用，不需要前端带。
    想中途换图才带参数（会被记到项目上）。**Agent 模式不接受换图** → 400。
    """

    from app.core.reload_guard import assert_reload_safe

    assert_reload_safe()          # --reload 下长任务会被重载打断 → 直接拒绝（真机踩到）
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    wants_switch = workflow_id is not None or bool(workflow_name)
    if wants_switch and (project.mode or "workflow") == "agent":
        raise HTTPException(
            status_code=400,
            detail="该项目的编排模式是 Agent 自行调度（审批后由编排官读 PRD 决定开发节点），"
                   "不能指定工作流",
        )

    workflow = None
    if approved and wants_switch:
        try:
            workflow = resolve_workflow(
                db, user_id=project.user_id,
                workflow_id=workflow_id, workflow_name=workflow_name,
            )
            validate_workflow(workflow)
            ensure_prd_node(workflow)
        except WorkflowError as e:
            raise HTTPException(status_code=400, detail=f"工作流不可用: {e}")
        # 换图 = 改这个项目的属性：记下来，后续迭代/重跑都按新图走
        # 显式换图 → 顺手清掉「编排官出的图」，之后一律按用户选的这张走
        update_project_fields(db, project_id, workflow_id=workflow_id,
                              workflow_name="" if workflow_id is not None else workflow["name"],
                              planned_workflow=None)

    await step_orchestrator.step_2_handle_approval(db, project_id, approved, feedback, workflow)

    return {"success": True, "message": "审批指令已下达，流程开始流转。"}


@router.get("/")
async def api_get_user_projects(
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """当前用户的项目列表（按创建时间倒序）。"""
    if uid is None:
        raise HTTPException(status_code=422, detail="缺少身份信息（user_id 或 token）")

    projects = get_projects_by_user(db, uid)
    return [
        {"id": p.id, "title": p.title, "status": p.status, "zip_path": p.zip_path,
         "workflow_name": p.workflow_name, "mode": p.mode or "workflow"}
        for p in reversed(projects)
    ]


@router.get("/{project_id}")
async def api_get_project_detail(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """项目详情（前端 5 秒轮询状态用）。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    # 审批前的形态预检：PRD 判的运行形态 vs 项目选定的图能产出什么。
    # 只在「待审批」时算（那一刻用户正好要做决定），算不出来也不影响详情接口。
    # agent 模式跳过：那张图要等审批通过后由编排官现场出，此刻还不存在，比了也是误报。
    plan_warning = ""
    if project.status == "PENDING_APPROVAL" and (project.mode or "workflow") != "agent":
        try:
            wf = step_orchestrator._project_workflow(db, project)
            plan_warning = shape_mismatch_warning(wf, read_project_prd(db, project))
        except Exception:  # noqa: BLE001 —— 预检是加分项，绝不能拖垮详情接口
            plan_warning = ""

    return {
        "id": project.id,
        "user_id": project.user_id,
        "title": project.title,
        "description": project.description,
        "status": project.status,
        "zip_path": project.zip_path,
        # 本项目选定的工作流（建项目时定下、审批与迭代沿用）。前端据此显示
        # "本项目使用：简单项目（simple_chain）"，不需要自己记住用户选了什么。
        "workflow_name": project.workflow_name,
        "workflow_id": project.workflow_id,
        # 编排模式：workflow（图先存在）/ agent（审批后编排官出图）。
        # 前端据此决定审批卡片上怎么措辞（"按这张图跑" vs "由编排官自行决定节点"）。
        "mode": project.mode or "workflow",
        # 形态预检提示（可能为空串）：PRD 说"前后端分离"、图里却没有后端节点时给一句话，
        # 让用户在点「通过审批」之前就知道这张图跑不出他要的东西。
        "plan_warning": plan_warning,
    }


@router.get("/{project_id}/preview-url")
async def api_get_preview_url(
    project_id: int,
    request: Request,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """网页产物预览 URL。源码目录取自 DEV 行登记的 path（数据库路径是唯一索引）。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    dev_agent = next((a for a in get_project_agents(db, project_id) if a.role == "DEV"), None)
    if not dev_agent or not dev_agent.path:
        raise HTTPException(status_code=404, detail="代码尚未生成，请等待程序员开发完成")

    project_dir = file_helper.get_project_dir(project.user_id, project.id)
    src_rel = dev_agent.path.rstrip("/")
    real_src_dir = os.path.join(project_dir, src_rel)
    url_prefix = f"{str(request.base_url)}previews/u{project.user_id}/p{project.id}"

    # —— 复杂项目（前后端分离）：页面由**生成的后端**自己托管（它把 ../frontend 挂成站点根），
    #    所以预览地址是应用端口，而不是平台的静态目录。纯前端项目走下面的老分支，行为不变。
    backend_entry = os.path.join(project_dir, "src", "backend", "main.py")
    if os.path.exists(backend_entry):
        base = str(request.base_url)                      # 形如 http://127.0.0.1:8000/
        host = base.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
        scheme = base.split("://", 1)[0]
        app_url = f"{scheme}://{host}:{app_env.APP_PORT}/"
        st = app_runner.status(project_dir, project_id=project.id)
        return {
            "success": True,
            "is_web_project": True,
            "kind": "app",                  # ★ 前端据此知道"这是要起后端的项目"
            "path": "/",
            "app_port": app_env.APP_PORT,
            "app_status": st.get("status"),
            # ★ 客户该用的主机名（部署方声明，空=前端按浏览器地址自己判断，见 runner/env.py）
            "app_host": app_env.public_host(),
            # 兼容字段：本机演示（浏览器就在服务器上）可直接用；上云后前端应按 kind=app
            # 用 app_host（没配就 location.hostname）+ app_port 重新拼一个"客户视角"的地址
            "preview_url": app_url,
        }

    if os.path.exists(os.path.join(real_src_dir, "index.html")):
        preview_url = f"{url_prefix}/{src_rel}/index.html"
    elif os.path.exists(os.path.join(project_dir, "index.html")):
        preview_url = f"{url_prefix}/index.html"
    else:
        return {
            "success": True,
            "is_web_project": False,
            "preview_url": None,
            "message": "此项目无 HTML 文件，仅支持查看源码。",
        }

    return {"success": True, "is_web_project": True, "kind": "static",
            "path": f"/previews/u{project.user_id}/p{project.id}/{src_rel}/index.html"
                    if os.path.exists(os.path.join(real_src_dir, "index.html"))
                    else f"/previews/u{project.user_id}/p{project.id}/index.html",
            "preview_url": preview_url}


@router.get("/{project_id}/artifacts", response_model=ArtifactListResponse,
            summary="项目产物清单（每一步 Agent 的产出）")
def api_project_artifacts(
    project_id: int,
    round_no: int | None = None,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """**每一步 Agent 产出了什么、放在哪** —— 不再只有 PRD / 测试报告 / 代码。

    路径的唯一来源是 `project_steps.artifact_path`（引擎落盘时登记的相对路径），
    所以这个接口既不会漏（画布拖出来的自定义 Agent 也登记）也不会被前端传任意路径读文件。

    轮次：默认把**所有轮次**合起来看，同一路径以更晚的轮次为准。
    为什么不是"只看最新轮次"：审批通过后的开发段是新一轮的步骤行，而 PM 的
    `PRD.md` 行留在上一轮 —— 只看最新轮会正好漏掉 PRD。
    """
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    latest = project_steps_crud.get_latest_round(db, project_id)
    rounds = [round_no] if round_no else list(range(1, latest + 1))
    picked: dict = {}
    for r in rounds:
        for s in project_steps_crud.get_steps(db, project_id, r):
            # 同一产物路径被后一轮覆盖 → 留最新那行；没有产物的行（失败/跳过）按步骤区分
            picked[s.artifact_path or f"{s.status}#{s.round_no}#{s.step_no}"] = s

    items = []
    for s in sorted(picked.values(), key=lambda x: (x.round_no, x.step_no)):
        info = file_helper.describe_artifact(project.user_id, project.id, s.artifact_path or "")
        agent = agents_crud.get_by_id(db, s.agent_id) if s.agent_id else None
        items.append({
            "round_no": s.round_no, "step_no": s.step_no, "name": s.name, "status": s.status,
            "agent_name": agent.name if agent else None,
            "skill": _effective_skill(agent),
            "artifact_path": s.artifact_path,
            "kind": _artifact_kind(s.artifact_path or "", info["is_dir"]),
            "exists": info["exists"], "is_dir": info["is_dir"],
            "size": info["size"], "files": info["files"],
            "error_code": s.error_code, "error": s.error,
        })
    return {"project_id": project_id, "round_no": latest, "total": len(items), "items": items}


@router.get("/{project_id}/artifacts/content", response_model=ArtifactContentResponse,
            summary="读取单个产物正文")
def api_project_artifact_content(
    project_id: int,
    path: str,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """读一份产物正文（目录产物 → 合并后的全部代码）。

    安全：`path` 必须**在本项目登记过的产物路径里**（白名单，来自 project_steps），
    再做一次"必须落在项目目录内"的校验 —— 前端无法借这个接口读项目外的文件。
    """
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    if not _is_registered_artifact(db, project_id, path):
        raise HTTPException(status_code=404, detail=f"未登记的产物路径: {path}")

    try:
        content = file_helper.read_artifact(project.user_id, project.id, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"物理文件已丢失: {path}")

    return {"project_id": project_id, "path": path,
            "name": os.path.basename(path.rstrip("/")) or path,
            "size": len(content.encode("utf-8")), "content": content}


_CODE_SUFFIXES = (".py", ".js", ".ts", ".vue", ".html", ".css", ".sh", ".sql", ".yml", ".yaml", ".txt")


def _effective_skill(agent) -> str:
    """这一步**实际**跑的技能。

    不能直接读 `agents.skill_id`：提示词模式的自定义 Agent 那一列是空的，
    引擎运行时会把它换算成 `generic-prompt-agent`（提示词作为输入值传进去）。
    这里用引擎同一个 resolve_run，保证"清单上写的"和"真跑的"是同一个东西。
    """
    if not agent:
        return ""
    try:
        return node_inputs.resolve_run(agent)[0] or ""
    except Exception:
        return agent.skill_id or ""


def _artifact_kind(path: str, is_dir: bool) -> str:
    """产物形态（前端据此选"markdown 渲染"还是"代码块"）"""
    if is_dir:
        return "dir"
    low = path.lower()
    if low.endswith(".md"):
        return "markdown"
    if low.endswith(".json"):
        return "json"
    return "code" if low.endswith(_CODE_SUFFIXES) else "text"


def _is_registered_artifact(db: Session, project_id: int, path: str) -> bool:
    """`path` 是否是本项目登记过的产物（或某个已登记目录下的文件）。

    登记目录（如 `src/`）允许取子文件，但仍然只在"已登记的目录"下 —— 不是任意路径。
    """
    path = (path or "").strip().replace("\\", "/")
    if not path or path.startswith("/") or ".." in path.split("/"):
        return False
    for r in range(1, project_steps_crud.get_latest_round(db, project_id) + 1):
        for s in project_steps_crud.get_steps(db, project_id, r):
            ap = (s.artifact_path or "").replace("\\", "/")
            if not ap:
                continue
            if path == ap or path == ap.rstrip("/"):
                return True
            if ap.endswith("/") and path.startswith(ap):
                return True
    return False


@router.get("/{project_id}/prd")
async def api_get_project_prd_text(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """PRD 全文（路径取自 PM 行登记值）。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    pm_agent = next((a for a in get_project_agents(db, project_id) if a.role == "PM"), None)
    if not pm_agent or not pm_agent.path:
        raise HTTPException(status_code=404, detail="产品需求文档（PRD）尚未生成")

    try:
        prd_content = file_helper.read_file_by_db_path(project.user_id, project.id, pm_agent.path)
    except Exception:
        raise HTTPException(status_code=404, detail="物理文件已丢失，请稍后再试")

    return {"success": True, "project_id": project_id, "prd_content": prd_content}


@router.get("/{project_id}/run-logs", response_model=RunLogListResponse)
def api_run_logs(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """**运行日志 / 项目BUG 清单** —— 让用户能查到"每个 Agent 节点干了什么"。

    这两类日志的正文是项目目录下的 md 文件（数据库只存索引与产物路径），
    文件名带步骤号，与 `project_steps.step_no` 一一对应，拿到清单后再按名字取正文。
    """
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    items = file_helper.list_logs(project.user_id, project.id)
    return {
        "project_id": project_id,
        "round_no": project_steps_crud.get_latest_round(db, project_id),
        "total": len(items),
        "items": items,
    }


@router.get("/{project_id}/run-logs/{log_name}", response_model=RunLogContentResponse)
def api_run_log_content(
    project_id: int,
    log_name: str,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """单份日志正文（按文件名取；文件名先在真实清单里做白名单匹配，杜绝路径穿越）。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    item = file_helper.find_log(project.user_id, project.id, log_name)
    if not item:
        raise HTTPException(status_code=404, detail=f"日志不存在: {log_name}")

    content = file_helper.read_log(project.user_id, project.id, item["kind"], log_name)
    return {"project_id": project_id, "kind": item["kind"], "name": log_name,
            "size": item["size"], "content": content}


@router.get("/{project_id}/qa-report")
async def api_get_project_qa_report_text(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """测试报告全文（路径取自 QA 行登记值）。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    qa_agent = next((a for a in get_project_agents(db, project_id) if a.role == "QA"), None)
    if not qa_agent or not qa_agent.path:
        raise HTTPException(status_code=404, detail="测试报告尚未生成")

    try:
        qa_content = file_helper.read_file_by_db_path(project.user_id, project.id, qa_agent.path)
    except Exception:
        raise HTTPException(status_code=404, detail="物理文件已丢失，请稍后再试")

    return {"success": True, "project_id": project_id, "qa_report": qa_content}


@router.delete("/{project_id}")
async def api_delete_project_by_id(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """彻底删除项目：**先停应用** → 数据库记录（成果行由外键级联清空）→ 磁盘产物。

    ⚠️ 顺序很重要：正在跑的应用占着固定端口（8100）。先删文件不停进程的话，进程会变成
    "没有代码却在跑"的孤儿，还把端口一直占着（真机踩到过）。
    """
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    owner_id = project.user_id

    # ① 先停应用（账本丢了也按进程指纹兜底回收），失败不阻塞删除
    #    ★ 走线程：停应用要发信号 + 轮询等进程退出（内部带 sleep 轮询）。同步跑会占住事件循环，
    #      这正是"收尾里建 venv/起应用"那一类问题 —— 量级小得多（最坏约 1 秒），但同一个道理。
    try:
        await asyncio.to_thread(
            app_runner.stop_project,
            file_helper.get_project_dir(owner_id, project_id),
            project_id=project_id,
        )
    except Exception as e:  # noqa: BLE001 —— 清理失败不该让用户删不掉项目
        print(f"[runner] 删除项目 p{project_id} 前停应用失败（忽略）：{e}")

    if not delete_project(db, project_id):
        raise HTTPException(status_code=500, detail="删除项目失败，数据库接口运行异常")

    file_helper.delete_project_files(owner_id, project_id)

    return {
        "success": True,
        "message": f"项目 ID: {project_id} 及其本地源码、打包 ZIP、关联 AI 记录已全部成功安全彻底清理！",
    }


@router.post("/{project_id}/run/abort", summary="强制终止这个项目正在跑的 AI 会话")
async def api_abort_run(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """客户在项目页点「终止运行」：把正在跑的会话**整组杀掉**，并且不让后续节点再启动。

    · 幂等：没有在跑也返回 200（`aborted=false` + 人话说明），并且照样记下"终止意图"——
      因为"刚好跑完"的瞬间点终止，也必须拦住下一个节点；
    · 项目状态由编排层收尾时置 `STOPPED`（**不是 FAILED**：这是客户自己按停的）；
    · **不影响已生成的应用进程**（那是「停止应用」按钮的事，见 §2.10）。
    """
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    from app.agents import run_registry

    # 杀进程组最长要等 5 秒宽限，**不能**在事件循环线程里同步做（会把全站请求都卡住）
    out = await asyncio.to_thread(run_registry.abort, project_id)

    # ★ 对账：注册表里没有在跑的会话（平台重启过 / 会话进程已消失），但 DB 里那次运行还挂着
    #   RUNNING —— 这种情况必须把状态收尾，否则客户点了终止、界面还是"进行中"，
    #   看起来就是"按钮没反应"（真机踩到）。
    reconciled = _reconcile_stale_run(db, project) if not out.get("aborted") else None
    if reconciled:
        out.update(reconciled)
    elif not out.get("aborted"):
        # 既没有在跑的会话、状态也是干净的 → 把话说清楚（别让客户以为按钮没反应）
        out["message"] = (f"这个项目当前没有正在跑的运行（项目状态：{project.status}），不需要终止。"
                          f"要重跑就点「重新跑开发链」。")

    st = app_runner.status(file_helper.get_project_dir(project.user_id, project_id),
                           project_id=project_id)
    out["project_id"] = project_id
    out["app_status"] = st.get("status")
    if st.get("status") == "running":
        out["note"] = "已生成的应用仍在运行（那是「停止应用」按钮的事），需要的话可以在应用控制条里停掉。"
    return out


def _reconcile_stale_run(db, project) -> dict | None:
    """把"看着还在跑、其实没人跑"的运行收尾。

    判据：注册表里没有它的会话（调用方已确认）**且** DB 里项目或步骤停在 RUNNING。
    返回 None 表示运行状态本来就是干净的。
    """
    round_no = project_steps_crud.get_latest_round(db, project.id)
    steps = project_steps_crud.get_steps(db, project.id, round_no) or []
    stuck = [s for s in steps if s.status == "RUNNING"]
    if not stuck and project.status != "RUNNING":
        return None

    for s in stuck:
        project_steps_crud.finish_step(
            db, s, project_steps_crud.STATUS_FAILED,
            error="运行已中断（平台后端重启或会话进程消失），这一步没跑完；"
                  "已完成的产物保留，点「重新跑开发链」可继续。",
        )
    update_project_status_or_zip(db, project.id, status="STOPPED")
    names = "、".join(s.name or f"步骤{s.step_no}" for s in stuck) or "（无 RUNNING 步骤）"
    return {
        "reconciled": True,
        "step": names,
        "message": f"这次运行其实已经中断了（平台重启或会话进程消失），已把状态收尾为「已终止」：{names}。"
                   f"已完成的产物都保留，点「重新跑开发链」可接着跑。",
    }


@router.post("/{project_id}/revise")
async def api_revise_project_code(
    project_id: int,
    feedback: str,
    background_tasks: BackgroundTasks,
    iteration: str = "regenerate",
    workflow_id: int | None = None,
    workflow_name: str = "",
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """按修改意见重构：异步重跑开发链，完成后自动重新测试并打包。

    默认**沿用项目选定的那张图**（图记在项目上，所以不会"迭代一次就悄悄换回默认图"）。
    只有需求形态变了（例如纯前端页面后来要加登录/数据库）才需要带 workflow_* 换图，
    换完会记到项目上，之后都按新图走。
    """

    from app.core.reload_guard import assert_reload_safe

    assert_reload_safe()          # --reload 下长任务会被重载打断 → 直接拒绝（真机踩到）
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    workflow = None
    if workflow_id is not None or workflow_name:
        try:
            workflow = resolve_workflow(db, user_id=project.user_id,
                                        workflow_id=workflow_id, workflow_name=workflow_name)
            validate_workflow(workflow)
            ensure_prd_node(workflow)
        except WorkflowError as e:
            raise HTTPException(status_code=400, detail=f"工作流不可用: {e}")
        # 显式换图 → 顺手清掉「编排官出的图」，之后一律按用户选的这张走
        update_project_fields(db, project_id, workflow_id=workflow_id,
                              workflow_name="" if workflow_id is not None else workflow["name"],
                              planned_workflow=None)

    # ★ 两种迭代模式（前端两个按钮）：
    #   incremental = 增量修改：保留 src/ 里的上一版代码，Agent 在其基础上局部改（默认给前端用）
    #   regenerate  = 重新生成：先清空 src/ 再按需求重写
    if iteration not in ("incremental", "regenerate"):
        raise HTTPException(status_code=400,
                            detail=f"iteration 只能是 incremental 或 regenerate，收到 {iteration!r}")
    background_tasks.add_task(step_orchestrator.step_3_revise_dev, project_id, feedback, workflow,
                              iteration == "incremental")

    return {
        "success": True,
        "message": "代码重构指令已下达，程序员正在根据您的建议修改代码，请耐心等待并轮询状态...",
    }


@router.get("/{project_id}/download")
async def api_download_project_zip(
    project_id: int,
    uid: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """下载项目 ZIP（含 PRD、源码、测试报告）。zip_path 是相对 backend/ 的路径。"""
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")

    if not project.zip_path:
        raise HTTPException(status_code=404, detail="该项目尚未打包，无法下载")

    backend_root = os.path.dirname(file_helper.exports_dir)
    absolute_zip_path = os.path.join(backend_root, project.zip_path)
    if not os.path.exists(absolute_zip_path):
        raise HTTPException(status_code=404, detail="物理压缩包文件在服务器上未找到，请重新生成项目")

    safe_title = "".join([c for c in project.title if c.isalnum() or c in ("_", "-")])
    return FileResponse(
        path=absolute_zip_path,
        media_type="application/octet-stream",
        filename=f"Project_{project_id}_{safe_title}.zip",
    )
