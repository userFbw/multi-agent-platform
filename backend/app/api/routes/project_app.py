"""project_app 路由 —— 生成项目**后端进程**的启停与观测（复杂项目专用）

为什么单独一个文件：这是"运行环境"领域，与项目 CRUD 不是一回事；也便于整体停用回退。

硬约束（见 app/runner/env.py）：
    · 生成项目只用自己的 `.venv`，绝不碰平台运行环境；
    · 应用单实例、固定端口 8100（已定"只分一个端口给客户"）；
    · 失败返回**真实原因**（依赖装不上/端口被占/超时未就绪），不静默、不换端口。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import assert_owner, current_user_id
from app.db.crud.projects_crud import get_project_by_id
from app.db.engine import get_db
from app.runner import process as rproc
from app.runner import venv as rvenv
from app.storage.file_helper import file_helper

router = APIRouter(prefix="/api/projects/{project_id}/app", tags=["项目应用（复杂项目）"])


def _require_project(project_id: int, uid: Optional[int], db: Session):
    project = get_project_by_id(db, project_id)
    assert_owner(project, uid, "项目")
    return project


def _project_dir(project) -> str:
    return file_helper.get_project_dir(project.user_id, project.id)


def _has_backend(project_dir: str) -> bool:
    import os

    return os.path.exists(os.path.join(project_dir, "src", "backend", "main.py"))


@router.post("/start", summary="启动生成项目的后端（装依赖 → 起服务 → 等就绪）")
def api_app_start(
    project_id: int,
    force: bool = False,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """复杂项目才有后端；纯前端项目调用会被明确拒绝（不静默成功）。

    `force=true` = 端口被**本平台别的项目**占着时，停掉它再启动本项目（单实例语义）。
    """
    project = _require_project(project_id, uid, db)
    pdir = _project_dir(project)
    if not _has_backend(pdir):
        raise HTTPException(status_code=400,
                            detail="这个项目没有后端（纯前端项目无需启动应用）—— src/backend/main.py 不存在")
    res = rproc.start(pdir, project_id=project.id, force=force)
    if not res.get("ok"):
        # 409 = 端口被别的项目占用（可 force 切换）；其余按 400（生成侧/环境问题）
        code = 409 if res.get("status") == "occupied" else 400
        raise HTTPException(status_code=code, detail=res.get("message") or "启动失败")
    return res


@router.post("/stop", summary="停止生成项目的后端（幂等）")
def api_app_stop(
    project_id: int,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """只停**本项目**的应用（账本按项目记账，停 A 不会影响 B）。

    账本丢了但进程还在（历史遗留）也照样能停掉 —— `stop_project` 会按进程指纹兜底回收。
    """
    project = _require_project(project_id, uid, db)
    pdir = _project_dir(project)
    out = rproc.stop_project(pdir, project_id=project.id)
    st = rproc.app_status(pdir, project_id=project.id)
    return {"project_id": project.id, **out, "status": st["status"],
            "port": st["port"],
            "occupied_by": st.get("project_id") if st["status"] == "occupied" else None}


@router.get("/status", summary="生成项目后端的运行状态（含日志尾部）")
def api_app_status(
    project_id: int,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """状态：not_started / running / stopped（`project_id` 能看出当前跑的是哪个项目）。"""
    project = _require_project(project_id, uid, db)
    pdir = _project_dir(project)
    st = rproc.app_status(pdir, project_id=project.id)
    st["running_this_project"] = st["status"] == "running" and st.get("project_id") == project.id
    return st


@router.get("/logs", summary="生成项目的应用日志（装依赖 + 运行输出）")
def api_app_logs(
    project_id: int,
    tail: int = 200,
    uid: Optional[int] = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    project = _require_project(project_id, uid, db)
    pdir = _project_dir(project)
    return {
        "project_id": project.id,
        "install_log_tail": rvenv._last_lines(
            "\n".join(_read_lines(str(rvenv.log_path(pdir)), 60)), 20),
        "app_log": rproc.logs(pdir, tail=tail),
    }


def _read_lines(path: str, n: int) -> list:
    try:
        return open(path, encoding="utf-8", errors="replace").read().splitlines()[-n:]
    except OSError:
        return []
