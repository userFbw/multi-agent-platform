"""后端入口：创建 FastAPI 应用、初始化数据库、挂载静态目录、注册全部路由。

启动：uvicorn main:app --host 0.0.0.0 --port 8000
"""
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes.agents import router as agents_router
from app.api.routes.auth import router as auth_router
from app.api.routes.planner import router as planner_router
from app.api.routes.project_app import router as project_app_router
from app.api.routes.projects import router as projects_router
from app.api.routes.skills import router as skills_router
from app.api.routes.steps import router as steps_router
from app.api.routes.workflow import router as workflow_router
from app.db.engine import Base, engine
from app.storage.paths import EXPORTS_DIR, IMAGES_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库；关闭时打一条日志。"""
    with engine.connect() as con:
        con.execute(text("PRAGMA foreign_keys = ON;"))
        con.commit()

    Base.metadata.create_all(bind=engine)   # 已存在的表是 no-op

    # 启动方式守卫：--reload 会让长任务被重载打断（真机踩到）→ 大声警告
    try:
        from app.core import reload_guard

        reload_guard.detect()
        reload_guard.banner()
    except Exception as e:  # noqa: BLE001
        print(f"[guard] 启动方式检查失败（忽略）：{e}")

    # 对账：平台重启后，上一轮遗留的"生成项目应用进程"要收掉（避免残留占着 8100）
    try:
        from app.runner import process as app_runner

        killed = app_runner.cleanup_orphans()
        if killed:
            print(f"[runner] 已清理上一次遗留的应用进程 pid={list(killed)}")
    except Exception as e:  # noqa: BLE001 —— 清理失败不能拖垮平台启动
        print(f"[runner] 清理遗留应用进程失败（忽略）：{e}")

    # 对账：平台重启后，上一轮遗留的"RUNNING"运行状态也要收尾 —— 否则界面永远显示"进行中"，
    # 而客户点「终止运行」也找不到会话（注册表是内存里的，重启就空了）。真机踩到过。
    try:
        from app.db.crud.project_steps_crud import reconcile_interrupted_runs
        from app.db.engine import SessionLocal

        with SessionLocal() as _db:
            fixed = reconcile_interrupted_runs(_db)
        if fixed["steps"] or fixed["projects"]:
            print(f"[runner] 已收尾上次遗留的运行状态：步骤 {fixed['steps']} 条、项目 {fixed['projects']} 个")
    except Exception as e:  # noqa: BLE001
        print(f"[runner] 收尾遗留运行状态失败（忽略）：{e}")

    yield

    print("AI原生全栈应用开发系统后端正在平稳关闭...")


app = FastAPI(title="AI原生全栈应用开发系统后端 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态目录：项目产物 / 用户头像（目录由 storage.paths 保证存在）
app.mount("/previews", StaticFiles(directory=EXPORTS_DIR), name="previews")
app.mount("/avatars", StaticFiles(directory=IMAGES_DIR), name="avatars")

# ---------------- 保险丝：慢请求看门狗 ----------------
# 为什么需要它：真机出现过"平台跑着跑着，客户刷新页面一直转圈没反应"，事后只能靠猜。
# 现在任何请求只要超过阈值（默认 1 秒）就把「方法 + 路径 + 耗时 + 当时有没有项目在跑」打进日志；
# 下一次再卡，日志直接指认是哪一段（例如收尾里建 venv/装依赖的那 ~150 秒）。
SLOW_REQUEST_SECONDS = float(os.getenv("DSH_SLOW_REQUEST_SECONDS", "1"))


def _running_hint() -> str:
    """当时有没有项目/步骤处于 RUNNING —— 用来判断"卡"是否与正在跑的任务有关。

    查询本身必须快、失败也不能影响响应，所以整段兜底。
    """
    try:
        from app.db.engine import SessionLocal
        from app.db.models.projects_model import ProjectDB
        from app.db.models.project_steps_model import ProjectStepDB

        with SessionLocal() as db:
            pids = [p.id for p in db.query(ProjectDB).filter(ProjectDB.status == "RUNNING").all()]
            steps = db.query(ProjectStepDB).filter(ProjectStepDB.status == "RUNNING").count()
        return f"运行中的项目={pids or '无'}、运行中的步骤={steps}"
    except Exception as e:  # noqa: BLE001
        return f"(运行状态查询失败：{e})"


@app.middleware("http")
async def slow_request_watchdog(request: Request, call_next):
    """给每个请求计时，超阈值就记一笔（含当时的运行状态）。"""
    t0 = time.perf_counter()
    response = await call_next(request)
    cost = time.perf_counter() - t0
    if cost >= SLOW_REQUEST_SECONDS:
        print(
            f"[慢请求] {request.method} {request.url.path} {cost:.2f}s "
            f"status={response.status_code} · {_running_hint()}",
            flush=True,
        )
    return response


# 全部路由
app.include_router(projects_router)
app.include_router(project_app_router)   # 复杂项目：生成后端的启停/状态/日志
app.include_router(auth_router)
app.include_router(agents_router)
app.include_router(skills_router)
app.include_router(steps_router)
app.include_router(workflow_router)
app.include_router(planner_router)


@app.get("/")
def read_root():
    return {
        "status": "success",
        "message": "AI原生全栈应用开发系统后端 API 运行正常！",
        "docs_url": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    # ⚠️ **默认不带 reload**（2026-09-19 真机事故的根源就在这里）：
    #    以前这里写死 `reload=True`，于是每次 `python main.py` 启动都是重载模式 ——
    #    reload 下 **监听 8000 的是父进程（重载器）**，子进程每次被重启的窗口里没人 accept：
    #    内核把连接排进 backlog，客户端一直转圈；子进程若启动失败，端口还听着、永远不服务
    #    （"假活"）。更致命的是平台每跑一个项目就往 `backend/exports/**` 写几百个 `.py`，
    #    而 uvicorn 默认监视 **cwd 下所有 *.py** → 写一个文件触发一轮重启 = 重载风暴，
    #    任务跑到一半被打断（p47 的 src/ 被清成 0 个文件就是这么来的）。
    #    要调试热重载请显式开：`DSH_DEV_RELOAD=1 python main.py`，并务必排除 exports。
    dev_reload = os.getenv("DSH_DEV_RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=dev_reload,
        # 只有显式开了热重载才需要排除生成物目录
        reload_excludes=["exports/*", "*.pyc", "__pycache__/*"] if dev_reload else None,
    )
