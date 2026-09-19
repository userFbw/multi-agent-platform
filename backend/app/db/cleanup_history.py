#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cleanup_history.py —— 清理开发过程中留下的两类历史残留数据

只删**两类明确无用的历史残留**，删之前会逐条校验特征，不满足就中止：

    ① 空壳成果行（project_agents）
       特征：path / session_id / elapsed_time **三者全空**。
       来历：早期 `_map_steps_to_agents` 只要节点出现在 node_results 里就登记，
             被 SKIPPED 的节点也登了 —— 于是"后端开发"明明跳过了，成果表里却留一行空壳。
       现已修复（`ran()` 守卫），新数据不会再产生，所以这些行纯属历史痕迹。

    ② 卡死项目（projects）
       特征：status='RUNNING' **且** title 以 'verify_' 开头。
       来历：9-15 的链路验证项目。当时三条执行路径里只有主链路有收尾逻辑，
             planner / execute 两条没有 → 项目永远停在 RUNNING，磁盘上也没 ZIP。
       连带删除：库行（project_steps 由外键级联）+ 磁盘项目目录/ZIP + 会话日志。

默认 **dry-run**：只打印将要做什么，不动任何东西。加 `--apply` 才真删，且**动库前自动备份**。

用法（仓库根目录）:
    PYTHONPATH=backend .venv/bin/python backend/app/db/cleanup_history.py
    PYTHONPATH=backend .venv/bin/python backend/app/db/cleanup_history.py --apply
"""
import os
import shutil
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 显式导入全部模型：删 projects 行时 SQLAlchemy 要按外键排序 mapper，
# 若 users 等表未注册进 metadata 会报 NoReferencedTableError（seed.py 里也是这么做的）
from app.db.models import agents_model, auth_model, projects_model  # noqa: E402,F401
from app.db.models.agent_registry_model import AgentRegistryDB  # noqa: E402,F401
from app.db.models.project_steps_model import ProjectStepDB  # noqa: E402,F401
from app.db.models.workflows_model import WorkflowDB  # noqa: E402,F401

from app.db.crud import projects_crud  # noqa: E402
from app.db.crud import project_agents_crud as pa_crud  # noqa: E402
from app.db.engine import SessionLocal  # noqa: E402
from app.storage.file_helper import file_helper  # noqa: E402

LOG_ROOT = _BACKEND_DIR / "data" / "logs"


def find_empty_agent_rows(db) -> list:
    """① 三重空壳的成果行（path / session_id / elapsed_time 全空）。"""
    from app.db.models.agents_model import ProjectAgentDB
    return (
        db.query(ProjectAgentDB)
        .filter(ProjectAgentDB.path.is_(None))
        .filter(ProjectAgentDB.session_id.is_(None))
        .filter((ProjectAgentDB.elapsed_time.is_(None)) | (ProjectAgentDB.elapsed_time == 0))
        .order_by(ProjectAgentDB.project_id, ProjectAgentDB.id)
        .all()
    )


def find_stuck_projects(db) -> list:
    """② 卡死的验证项目（RUNNING 且标题以 verify_ 开头）。"""
    from app.db.models.projects_model import ProjectDB
    return (
        db.query(ProjectDB)
        .filter(ProjectDB.status == "RUNNING")
        .filter(ProjectDB.title.like("verify_%"))
        .order_by(ProjectDB.id)
        .all()
    )


def _drop_if_empty(path: Path, recursive: bool = False) -> None:
    """目录空了就删掉（不空绝不动）—— 避免留下 exports/u7 这种空壳账号目录。"""
    if not path.is_dir():
        return
    if recursive:
        shutil.rmtree(path, ignore_errors=True)
    elif not any(path.iterdir()):
        path.rmdir()


def main() -> None:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        shells = find_empty_agent_rows(db)
        stuck = find_stuck_projects(db)

        print("=" * 92)
        print("① 空壳成果行（project_agents：path/session_id/elapsed_time 全空）")
        print("=" * 92)
        if not shells:
            print("  （没有）")
        for r in shells:
            print(f"  - id={r.id}  project={r.project_id}  role={r.role:<11} {r.agent_name}"
                  f"   final_output={r.final_output!r}")

        print()
        print("=" * 92)
        print("② 卡死项目（status=RUNNING 且 title 以 verify_ 开头）")
        print("=" * 92)
        if not stuck:
            print("  （没有）")
        for p in stuck:
            proj_dir = Path(file_helper.get_project_dir(p.user_id, p.id))
            zip_path = proj_dir.parent / f"p{p.id}.zip"
            log_dir = LOG_ROOT / f"u{p.user_id}" / f"p{p.id}"
            n_logs = len(list(log_dir.glob("*.json"))) if log_dir.is_dir() else 0
            print(f"  - p{p.id}  user={p.user_id}  {p.title!r}  status={p.status}")
            print(f"      磁盘目录 {'有' if proj_dir.is_dir() else '无'}　"
                  f"ZIP {'有' if zip_path.exists() else '无'}　"
                  f"会话日志 {n_logs} 份")

        # —— 安全校验：特征不符就拒绝执行（宁可不动，也不误删）——
        for p in stuck:
            if not p.title.startswith("verify_") or p.status != "RUNNING":
                sys.exit(f"❌ 中止：p{p.id} 不符合「verify_ 开头的 RUNNING 项目」特征，请人工确认")

        if not shells and not stuck:
            print("\n没有需要清理的数据，已是最新状态。")
            return

        print()
        print(f"合计：{len(shells)} 行空壳 + {len(stuck)} 个卡死项目"
              f"（连带 {sum(1 for _ in stuck)} 个项目的 project_steps 级联行）")
        if not apply:
            print("\n这是 **dry-run**，未做任何改动。确认无误后加 --apply 执行。")
            return

        from app.db.migrate import backup_db
        backup_db()

        for r in shells:
            pa_crud.delete_agent_result(db, r.id)
        print(f"✅ 已删除 {len(shells)} 行空壳成果")

        for p in stuck:
            uid, pid = p.user_id, p.id
            projects_crud.delete_project(db, pid)      # 库行 + 外键级联 project_steps
            file_helper.delete_project_files(uid, pid)  # 磁盘项目目录 + ZIP
            _drop_if_empty(LOG_ROOT / f"u{uid}" / f"p{pid}", recursive=True)   # 会话原始日志
            _drop_if_empty(LOG_ROOT / f"u{uid}")
            _drop_if_empty(Path(file_helper.exports_dir) / f"u{uid}")           # 账号产物目录
            print(f"✅ 已删除项目 p{pid}（{p.title}）及其磁盘产物与会话日志")
        print("\n完成。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
